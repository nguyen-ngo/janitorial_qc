from app.utils.time_utils import now_eastern
from flask import (Blueprint, render_template, redirect, url_for,
                   flash, request, current_app)
from flask_login import login_required, current_user
from app import db
from app.models.issue import Issue, IssueComment
from app.models.facility import Facility, Area
from app.models.user import User
from app.utils.forms import IssueForm, IssueUpdateForm
from app.utils.decorators import supervisor_required
from app.utils.notifications import notify

bp = Blueprint('issues', __name__, url_prefix='/issues')


# ── List ──────────────────────────────────────────────────────────────────────

@bp.route('/')
@login_required
def index():
    page = request.args.get('page', 1, type=int)

    q = Issue.query.order_by(Issue.reported_at.desc())

    # Inspectors only see issues assigned to them
    if current_user.role == 'inspector':
        q = q.filter(Issue.assigned_to == current_user.id)

    severity_filter = request.args.get('severity', '')
    status_filter   = request.args.get('status', '')
    if severity_filter:
        q = q.filter(Issue.severity == severity_filter)
    if status_filter:
        q = q.filter(Issue.status == status_filter)

    issues = q.paginate(page=page, per_page=25, error_out=False)

    return render_template('issues/list.html',
                           issues=issues,
                           severity_filter=severity_filter,
                           status_filter=status_filter)


# ── View / Update ─────────────────────────────────────────────────────────────

@bp.route('/<int:issue_id>', methods=['GET', 'POST'])
@login_required
def view(issue_id):
    issue = Issue.query.get_or_404(issue_id)

    # Access control: inspectors may only view/edit issues assigned to them
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

        # Only admin/supervisor can reassign; inspectors can only update status
        if current_user.role in ['admin', 'supervisor']:
            issue.assigned_to = form.assigned_to.data or None

        if form.status.data == 'resolved' and not issue.resolved_at:
            issue.resolved_at = now_eastern()
        elif form.status.data != 'resolved':
            issue.resolved_at = None

        # Save result notes (overwrite with latest value)
        issue.result_notes = form.result_notes.data or None

        # Append any newly uploaded result photos
        from app.routes.inspections import _save_photo
        new_photos = []
        for file_obj in request.files.getlist('result_photos'):
            path = _save_photo(file_obj, subfolder='issue_result_photos')
            if path:
                new_photos.append(path)
        if new_photos:
            existing = issue.result_photos or []
            issue.result_photos = existing + new_photos

        # Persist a comment entry if the user wrote update notes
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
        issue_link = url_for('issues.view', issue_id=issue.id)
        new_assigned_to = issue.assigned_to

        # 1. Notify the assignee when status changes
        if old_status != issue.status and new_assigned_to:
            assignee = User.query.get(new_assigned_to)
            if assignee and assignee.id != current_user.id:
                notify(
                    recipient     = assignee,
                    title         = f'Issue #{issue.id} Status Updated',
                    body          = (
                        f'Issue in {issue.area.name} was updated from '
                        f'"{old_status.replace("_", " ").title()}" to '
                        f'"{issue.status.replace("_", " ").title()}" '
                        f'by {current_user.username}.'
                    ),
                    link          = issue_link,
                    issue_id      = issue.id,
                    send_email    = True,
                )

        # 2. Notify newly assigned user when the assignee changes
        if (old_assigned_to != new_assigned_to) and new_assigned_to:
            new_assignee = User.query.get(new_assigned_to)
            if new_assignee and new_assignee.id != current_user.id:
                notify(
                    recipient     = new_assignee,
                    title         = f'Issue #{issue.id} Assigned to You',
                    body          = (
                        f'You have been assigned Issue #{issue.id} '
                        f'({issue.severity.title()} severity) in {issue.area.name}. '
                        f'Current status: {issue.status.replace("_", " ").title()}.'
                    ),
                    link          = issue_link,
                    issue_id      = issue.id,
                    send_email    = True,
                )

        # 3. Notify the previously assigned user when unassigned
        if old_assigned_to and old_assigned_to != new_assigned_to:
            old_assignee = User.query.get(old_assigned_to)
            if old_assignee and old_assignee.id != current_user.id:
                notify(
                    recipient     = old_assignee,
                    title         = f'Issue #{issue.id} Unassigned',
                    body          = (
                        f'You have been removed from Issue #{issue.id} '
                        f'in {issue.area.name} by {current_user.username}.'
                    ),
                    link          = issue_link,
                    issue_id      = issue.id,
                    send_email    = True,
                )

        # 4. Notify the assignee when a comment is added (if not the commenter)
        if comment_body and new_assigned_to:
            commentee = User.query.get(new_assigned_to)
            if commentee and commentee.id != current_user.id:
                notify(
                    recipient     = commentee,
                    title         = f'New Comment on Issue #{issue.id}',
                    body          = (
                        f'{current_user.username} added a comment on Issue #{issue.id}: '
                        f'"{comment_body[:120]}{"…" if len(comment_body) > 120 else ""}"'
                    ),
                    link          = issue_link,
                    issue_id      = issue.id,
                    send_email    = True,
                )

        db.session.commit()  # Commit notifications
        flash('Issue updated.', 'success')
        return redirect(url_for('issues.view', issue_id=issue_id))

    comments = issue.comments.order_by(IssueComment.created_at.asc()).all()
    return render_template('issues/view.html', issue=issue, form=form, comments=comments)


# ── Standalone create (not from an inspection) ────────────────────────────────

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

        # ── Notify the assignee of the new issue ────────────────────────
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
                    send_email = True,
                )
                db.session.commit()  # Commit notification

        flash('Issue created.', 'success')
        return redirect(url_for('issues.index'))

    return render_template('issues/form.html', form=form, title='Log New Issue')