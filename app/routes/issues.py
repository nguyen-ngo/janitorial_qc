from datetime import datetime
from flask import (Blueprint, render_template, redirect, url_for,
                   flash, request)
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

    # Inspectors only see issues they reported (linked to their inspections)
    if current_user.role == 'inspector':
        from app.models.inspection import Inspection
        q = q.join(Inspection, Issue.inspection_id == Inspection.id)\
             .filter(Inspection.inspector_id == current_user.id)

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
    form  = IssueUpdateForm(obj=issue)

    staff = User.query.filter(User.role.in_(['supervisor','inspector'])).order_by(User.username).all()
    form.assigned_to.choices = [(0, '— Unassigned —')] + [(u.id, u.username) for u in staff]
    form.status.data = form.status.data or issue.status

    if form.validate_on_submit():
        issue.status      = form.status.data
        issue.assigned_to = form.assigned_to.data or None

        if form.status.data == 'resolved' and not issue.resolved_at:
            issue.resolved_at = datetime.utcnow()
        elif form.status.data != 'resolved':
            issue.resolved_at = None

        db.session.commit()
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
