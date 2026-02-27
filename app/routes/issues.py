from datetime import datetime
from flask import (Blueprint, render_template, redirect, url_for,
                   flash, request, current_app)
from flask_login import login_required, current_user
from app import db
from app.models.issue import Issue
from app.models.facility import Facility, Area
from app.models.user import User
from app.utils.forms import IssueForm, IssueUpdateForm
from app.utils.decorators import supervisor_required

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
        issue.status = form.status.data

        # Only admin/supervisor can reassign; inspectors can only update status
        if current_user.role in ['admin', 'supervisor']:
            issue.assigned_to = form.assigned_to.data or None

        if form.status.data == 'resolved' and not issue.resolved_at:
            issue.resolved_at = datetime.utcnow()
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

        db.session.commit()
        current_app.logger.info(
            'ISSUE UPDATED | id=%s | status=%s | result_photos_added=%s | updated_by=%s',
            issue.id, issue.status, len(new_photos), current_user.username
        )
        flash('Issue updated.', 'success')
        return redirect(url_for('issues.view', issue_id=issue_id))

    return render_template('issues/view.html', issue=issue, form=form)


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
            reported_at = datetime.utcnow(),
        )
        db.session.add(issue)
        db.session.commit()
        flash('Issue created.', 'success')
        return redirect(url_for('issues.index'))

    return render_template('issues/form.html', form=form, title='Log New Issue')