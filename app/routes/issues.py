from app.utils.time_utils import now_eastern
from flask import (Blueprint, render_template, redirect, url_for,
                   flash, request, current_app, jsonify)
from flask_login import login_required, current_user
from app import db
from app.models.issue import Issue, IssueComment, IssueFollower
from app.models.facility import Facility, Area
from app.models.user import User
from app.models.notification import (
    EVENT_ISSUE_ASSIGNED, EVENT_ISSUE_STATUS,
    EVENT_ISSUE_COMMENT, EVENT_ISSUE_FOLLOW,
)
from app.utils.forms import IssueForm, IssueUpdateForm
from app.utils.decorators import supervisor_required
from app.utils.notifications import notify
from app.utils.audit import log_action, ACTION_CREATE, ACTION_UPDATE, ACTION_DELETE
from app.utils.sla import sla_status

bp = Blueprint('issues', __name__, url_prefix='/issues')


# ── Shared helper ─────────────────────────────────────────────────────────────

def _notify_followers(issue, title, body, exclude_user_ids=None):
    """Dispatch a notification to every follower of the given issue."""
    exclude    = set(exclude_user_ids or [])
    issue_link = url_for('issues.view', issue_id=issue.id)
    for follower in issue.followers.all():
        if follower.user_id in exclude:
            continue
        notify(
            recipient  = follower.user,
            title      = title,
            body       = body,
            link       = issue_link,
            issue_id   = issue.id,
            event_type = EVENT_ISSUE_FOLLOW,
            send_email = True,
        )


# ── List ──────────────────────────────────────────────────────────────────────

@bp.route('/')
@login_required
def index():
    page = request.args.get('page', 1, type=int)
    q    = Issue.query.order_by(Issue.reported_at.desc())

    if current_user.role == 'inspector':
        q = q.filter(Issue.assigned_to == current_user.id)

    severity_filter = request.args.get('severity', '')
    status_filter   = request.args.get('status', '')
    sla_filter      = request.args.get('sla', '')
    if severity_filter:
        q = q.filter(Issue.severity == severity_filter)
    if status_filter:
        q = q.filter(Issue.status == status_filter)

    # SLA filter — applied in Python after DB query since SLA is computed
    issues_paged = q.paginate(page=page, per_page=25, error_out=False)

    if sla_filter:
        filtered_items = [i for i in issues_paged.items if sla_status(i) == sla_filter]
    else:
        filtered_items = issues_paged.items

    # Build a set of issue IDs the current user is following so the template
    # can render the following badge and inline unfollow button without an
    # additional query per row.
    followed_ids = {
        f.issue_id
        for f in IssueFollower.query.filter_by(user_id=current_user.id).all()
    }

    return render_template('issues/list.html',
                           issues=issues_paged,
                           issue_items=filtered_items,
                           severity_filter=severity_filter,
                           status_filter=status_filter,
                           sla_filter=sla_filter,
                           followed_ids=followed_ids)


# ── View / Update ─────────────────────────────────────────────────────────────

@bp.route('/<int:issue_id>', methods=['GET', 'POST'])
@login_required
def view(issue_id):
    issue = Issue.query.get_or_404(issue_id)

    if current_user.role == 'inspector' and issue.assigned_to != current_user.id:
        flash('Access denied. You can only view issues assigned to you.', 'danger')
        return redirect(url_for('issues.index'))

    form  = IssueUpdateForm(obj=issue)
    staff = User.query.filter(User.role.in_(['supervisor','inspector'])).order_by(User.username).all()
    form.assigned_to.choices = [(0, '— Unassigned —')] + [(u.id, u.username) for u in staff]
    form.status.data = form.status.data or issue.status

    if form.validate_on_submit():
        old_status      = issue.status
        old_assigned_to = issue.assigned_to

        issue.status = form.status.data

        if current_user.role in ['admin', 'supervisor']:
            issue.assigned_to = form.assigned_to.data or None

        if form.status.data == 'resolved' and not issue.resolved_at:
            issue.resolved_at  = now_eastern()
            issue.sla_notified = None   # clear so alerts fire again if re-opened
        elif form.status.data != 'resolved':
            issue.resolved_at  = None

        issue.result_notes = form.result_notes.data or None

        from app.routes.inspections import _save_photo
        new_photos = []
        for file_obj in request.files.getlist('result_photos'):
            path = _save_photo(file_obj, subfolder='issue_result_photos')
            if path:
                new_photos.append(path)
        if new_photos:
            existing = issue.result_photos or []
            issue.result_photos = existing + new_photos

        comment_body = form.update_notes.data.strip() if form.update_notes.data else ''
        if comment_body:
            comment = IssueComment(
                issue_id       = issue.id,
                user_id        = current_user.id,
                status_at_time = issue.status,
                body           = comment_body,
            )
            db.session.add(comment)

        db.session.commit()
        current_app.logger.info(
            'ISSUE UPDATED | id=%s | status=%s | result_photos_added=%s | comment=%s | updated_by=%s',
            issue.id, issue.status, len(new_photos), bool(comment_body), current_user.username
        )

        # ── Notifications ────────────────────────────────────────────────
        issue_link      = url_for('issues.view', issue_id=issue.id)
        new_assigned_to = issue.assigned_to
        actor_id        = current_user.id

        # 1. Status changed — notify assignee
        if old_status != issue.status and new_assigned_to:
            assignee = User.query.get(new_assigned_to)
            if assignee and assignee.id != actor_id:
                notify(
                    recipient  = assignee,
                    title      = f'Issue #{issue.id} Status Updated',
                    body       = (
                        f'Issue in {issue.area.name} was updated from '
                        f'"{old_status.replace("_", " ").title()}" to '
                        f'"{issue.status.replace("_", " ").title()}" '
                        f'by {current_user.username}.'
                    ),
                    link       = issue_link,
                    issue_id   = issue.id,
                    event_type = EVENT_ISSUE_STATUS,
                    send_email = True,
                )

        # 2. Reassigned — notify new assignee
        if (old_assigned_to != new_assigned_to) and new_assigned_to:
            new_assignee = User.query.get(new_assigned_to)
            if new_assignee and new_assignee.id != actor_id:
                notify(
                    recipient  = new_assignee,
                    title      = f'Issue #{issue.id} Assigned to You',
                    body       = (
                        f'You have been assigned Issue #{issue.id} '
                        f'({issue.severity.title()} severity) in {issue.area.name}. '
                        f'Current status: {issue.status.replace("_", " ").title()}.'
                    ),
                    link       = issue_link,
                    issue_id   = issue.id,
                    event_type = EVENT_ISSUE_ASSIGNED,
                    send_email = True,
                )

        # 3. Unassigned — notify previous assignee
        if old_assigned_to and old_assigned_to != new_assigned_to:
            old_assignee = User.query.get(old_assigned_to)
            if old_assignee and old_assignee.id != actor_id:
                notify(
                    recipient  = old_assignee,
                    title      = f'Issue #{issue.id} Unassigned',
                    body       = (
                        f'You have been removed from Issue #{issue.id} '
                        f'in {issue.area.name} by {current_user.username}.'
                    ),
                    link       = issue_link,
                    issue_id   = issue.id,
                    event_type = EVENT_ISSUE_ASSIGNED,
                    send_email = True,
                )

        # 4. Comment added — notify assignee
        if comment_body and new_assigned_to:
            commentee = User.query.get(new_assigned_to)
            if commentee and commentee.id != actor_id:
                notify(
                    recipient  = commentee,
                    title      = f'New Comment on Issue #{issue.id}',
                    body       = (
                        f'{current_user.username} added a comment on Issue #{issue.id}: '
                        f'"{comment_body[:120]}{"…" if len(comment_body) > 120 else ""}"'
                    ),
                    link       = issue_link,
                    issue_id   = issue.id,
                    event_type = EVENT_ISSUE_COMMENT,
                    send_email = True,
                )

        # 5. Notify followers — consolidated message, exclude actor + assignees
        exclude_ids = {actor_id}
        if new_assigned_to:
            exclude_ids.add(new_assigned_to)
        if old_assigned_to:
            exclude_ids.add(old_assigned_to)

        changes = []
        if old_status != issue.status:
            changes.append(
                f'status changed from "{old_status.replace("_"," ").title()}" '
                f'to "{issue.status.replace("_"," ").title()}"'
            )
        if old_assigned_to != new_assigned_to:
            new_name = User.query.get(new_assigned_to).username if new_assigned_to else 'Unassigned'
            changes.append(f'reassigned to {new_name}')
        if comment_body:
            changes.append(f'new comment added by {current_user.username}')

        if changes:
            _notify_followers(
                issue            = issue,
                title            = f'Issue #{issue.id} Updated',
                body             = (
                    f'Issue #{issue.id} in {issue.area.name} was updated by '
                    f'{current_user.username}: {"; ".join(changes)}.'
                ),
                exclude_user_ids = exclude_ids,
            )

        db.session.commit()  # Commit all notifications
        log_action(ACTION_UPDATE, 'Issue', issue.id,
                   f'#{issue.id} in {issue.area.name}',
                   f'status={issue.status}; assigned_to={issue.assigned_to}')
        flash('Issue updated.', 'success')
        return redirect(url_for('issues.view', issue_id=issue_id))

    is_following = issue.is_followed_by(current_user)
    comments     = issue.comments.order_by(IssueComment.created_at.asc()).all()
    return render_template('issues/view.html',
                           issue=issue,
                           form=form,
                           comments=comments,
                           is_following=is_following)


# ── Follow ────────────────────────────────────────────────────────────────────

@bp.route('/<int:issue_id>/follow', methods=['POST'])
@login_required
def follow(issue_id):
    issue = Issue.query.get_or_404(issue_id)
    if not issue.is_followed_by(current_user):
        follower = IssueFollower(issue_id=issue.id, user_id=current_user.id)
        db.session.add(follower)
        db.session.commit()
        current_app.logger.info(
            'ISSUE FOLLOW | issue_id=%s | user=%s',
            issue.id, current_user.username,
        )
        flash('You are now following this issue and will receive notifications for any updates.', 'success')
    else:
        flash('You are already following this issue.', 'info')
    return redirect(url_for('issues.view', issue_id=issue_id))


# ── Unfollow ──────────────────────────────────────────────────────────────────

@bp.route('/<int:issue_id>/unfollow', methods=['POST'])
@login_required
def unfollow(issue_id):
    issue    = Issue.query.get_or_404(issue_id)
    follower = issue.followers.filter_by(user_id=current_user.id).first()
    if follower:
        db.session.delete(follower)
        db.session.commit()
        current_app.logger.info(
            'ISSUE UNFOLLOW | issue_id=%s | user=%s',
            issue.id, current_user.username,
        )
        flash('You have unfollowed this issue.', 'info')
    else:
        flash('You are not following this issue.', 'info')

    # Respect an explicit next URL (e.g. return to the list page).
    # Fall back to the issue view if none is provided.
    next_url = request.form.get('next') or url_for('issues.view', issue_id=issue_id)
    return redirect(next_url)


# ── Standalone create ─────────────────────────────────────────────────────────

@bp.route('/new', methods=['GET', 'POST'])
@login_required
@supervisor_required
def create():
    form  = IssueForm()
    areas = Area.query.join(Facility).filter(Facility.active == True).order_by(Facility.name, Area.name).all()
    staff = User.query.filter(User.role.in_(['supervisor','inspector'])).order_by(User.username).all()

    form.area_id.choices     = [(a.id, f"{a.facility.name} — {a.name}") for a in areas]
    form.assigned_to.choices = [(0, '— Unassigned —')] + [(u.id, u.username) for u in staff]

    if form.validate_on_submit():
        from app.routes.inspections import _save_photo
        photo_path = _save_photo(form.photo.data, subfolder='issue_photos')
        issue = Issue(
            area_id     = form.area_id.data,
            severity    = form.severity.data,
            description = form.description.data,
            photo_path  = photo_path,
            status      = 'open',
            assigned_to = form.assigned_to.data or None,
            reported_at = now_eastern(),
        )
        db.session.add(issue)
        db.session.commit()
        current_app.logger.info(
            'ISSUE CREATED | id=%s | severity=%s | area_id=%s | assigned_to=%s | created_by=%s',
            issue.id, issue.severity, issue.area_id, issue.assigned_to, current_user.username
        )
        log_action(ACTION_CREATE, 'Issue', issue.id,
                   f'#{issue.id} {issue.severity} in {issue.area.name}',
                   f'severity={issue.severity}; assigned_to={issue.assigned_to}')

        if issue.assigned_to:
            assignee = User.query.get(issue.assigned_to)
            if assignee and assignee.id != current_user.id:
                notify(
                    recipient  = assignee,
                    title      = f'New Issue #{issue.id} Assigned to You',
                    body       = (
                        f'A new {issue.severity.title()}-severity issue has been logged '
                        f'in {issue.area.name} and assigned to you. '
                        f'Description: {issue.description[:120]}'
                        f'{"…" if len(issue.description) > 120 else ""}'
                    ),
                    link       = url_for('issues.view', issue_id=issue.id),
                    issue_id   = issue.id,
                    event_type = EVENT_ISSUE_ASSIGNED,
                    send_email = True,
                )
                db.session.commit()

        flash('Issue created.', 'success')
        return redirect(url_for('issues.index'))

    return render_template('issues/form.html', form=form, title='Log New Issue')