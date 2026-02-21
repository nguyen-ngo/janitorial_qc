import os
import uuid
from datetime import datetime
from flask import (Blueprint, render_template, redirect, url_for,
                   flash, request, current_app, jsonify)
from flask_login import login_required, current_user
from app import db
from app.models.inspection import (Inspection, InspectionTemplate,
                                   ChecklistItem, InspectionResult)
from app.models.facility import Facility, Area
from app.models.issue import Issue
from app.models.user import User
from app.utils.forms import StartInspectionForm, IssueForm
from app.utils.decorators import supervisor_required

bp = Blueprint('inspections', __name__, url_prefix='/inspections')

ALLOWED_EXTENSIONS = {'jpg', 'jpeg', 'png', 'gif'}


def _save_photo(file_obj, subfolder='inspection_photos'):
    """Save an uploaded photo; return the relative path or None."""
    if not file_obj or not file_obj.filename:
        return None
    ext = file_obj.filename.rsplit('.', 1)[-1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return None
    filename  = f"{uuid.uuid4().hex}.{ext}"
    dest_dir  = os.path.join(current_app.config['UPLOAD_FOLDER'], subfolder)
    os.makedirs(dest_dir, exist_ok=True)
    file_obj.save(os.path.join(dest_dir, filename))
    return f"uploads/{subfolder}/{filename}"


def _compute_score(inspection):
    """
    Weighted average of all scored checklist results.
    pass_fail  → 100 if passed else 0
    rating_5   → (score / 5)  * 100
    rating_10  → (score / 10) * 100
    Returns a Decimal-compatible float or None if no results.
    """
    results = inspection.results.join(ChecklistItem).all()
    if not results:
        return None

    total_weight = 0.0
    weighted_sum = 0.0

    for r in results:
        item   = r.checklist_item
        weight = float(item.weight or 1.0)

        if item.scoring_type == 'pass_fail':
            pts = 100.0 if r.passed else 0.0
        elif item.scoring_type == 'rating_5':
            pts = (float(r.score) / 5.0 * 100.0) if r.score is not None else 0.0
        elif item.scoring_type == 'rating_10':
            pts = (float(r.score) / 10.0 * 100.0) if r.score is not None else 0.0
        else:
            pts = 100.0 if r.passed else 0.0

        weighted_sum  += pts * weight
        total_weight  += weight

    return round(weighted_sum / total_weight, 2) if total_weight else None


# ── List ─────────────────────────────────────────────────────────────────────

@bp.route('/')
@login_required
def index():
    page = request.args.get('page', 1, type=int)

    q = Inspection.query.order_by(Inspection.inspection_date.desc())

    # Inspectors only see their own
    if current_user.role == 'inspector':
        q = q.filter(Inspection.inspector_id == current_user.id)

    # Optional filters
    status_filter   = request.args.get('status', '')
    facility_filter = request.args.get('facility_id', '', type=str)
    if status_filter:
        q = q.filter(Inspection.status == status_filter)
    if facility_filter.isdigit():
        q = q.filter(Inspection.facility_id == int(facility_filter))

    inspections = q.paginate(page=page, per_page=20, error_out=False)
    facilities  = Facility.query.filter_by(active=True).order_by(Facility.name).all()

    return render_template('inspections/list.html',
                           inspections=inspections,
                           facilities=facilities,
                           status_filter=status_filter,
                           facility_filter=facility_filter)


# ── Start ─────────────────────────────────────────────────────────────────────

@bp.route('/start', methods=['GET', 'POST'])
@login_required
def start():
    form = StartInspectionForm()

    templates  = InspectionTemplate.query.order_by(InspectionTemplate.name).all()
    facilities = Facility.query.filter_by(active=True).order_by(Facility.name).all()

    form.template_id.choices = [(t.id, t.name) for t in templates]
    form.facility_id.choices = [(f.id, f.name) for f in facilities]

    # Area choices populated via AJAX based on selected facility
    selected_fid = form.facility_id.data or (facilities[0].id if facilities else None)
    areas = Area.query.filter_by(facility_id=selected_fid).order_by(Area.name).all() if selected_fid else []
    form.area_id.choices = [(0, '— No specific area —')] + [(a.id, a.name) for a in areas]

    if form.validate_on_submit():
        inspection = Inspection(
            template_id  = form.template_id.data,
            facility_id  = form.facility_id.data,
            area_id      = form.area_id.data or None,
            inspector_id = current_user.id,
            inspection_date = datetime.utcnow(),
            status       = 'in_progress',
            notes        = form.notes.data or None,
        )
        db.session.add(inspection)
        db.session.flush()  # get inspection.id

        # Pre-create blank InspectionResult rows for every checklist item
        template = InspectionTemplate.query.get(form.template_id.data)
        for item in template.checklist_items.order_by(ChecklistItem.display_order).all():
            db.session.add(InspectionResult(
                inspection_id    = inspection.id,
                checklist_item_id = item.id,
            ))

        db.session.commit()
        flash(f'Inspection started. Complete each item below.', 'success')
        return redirect(url_for('inspections.execute', inspection_id=inspection.id))

    return render_template('inspections/start.html', form=form, facilities=facilities)


# ── AJAX: areas for a given facility ─────────────────────────────────────────

@bp.route('/areas/<int:facility_id>')
@login_required
def areas_for_facility(facility_id):
    areas = Area.query.filter_by(facility_id=facility_id).order_by(Area.name).all()
    return jsonify([{'id': a.id, 'name': a.name} for a in areas])


# ── Execute ───────────────────────────────────────────────────────────────────

@bp.route('/<int:inspection_id>/execute', methods=['GET', 'POST'])
@login_required
def execute(inspection_id):
    inspection = Inspection.query.get_or_404(inspection_id)

    # Inspectors can only work on their own inspections
    if current_user.role == 'inspector' and inspection.inspector_id != current_user.id:
        flash('Access denied.', 'danger')
        return redirect(url_for('inspections.index'))

    if inspection.status == 'completed':
        return redirect(url_for('inspections.view', inspection_id=inspection_id))

    # Ordered checklist items with their result rows
    results = (
        InspectionResult.query
        .join(ChecklistItem)
        .filter(InspectionResult.inspection_id == inspection_id)
        .order_by(ChecklistItem.display_order)
        .all()
    )

    if request.method == 'POST':
        action = request.form.get('action', 'save')

        for result in results:
            item    = result.checklist_item
            prefix  = f"item_{result.id}_"

            if item.scoring_type == 'pass_fail':
                passed_val = request.form.get(f"{prefix}passed", '')
                result.passed = True  if passed_val == 'pass' else \
                                False if passed_val == 'fail' else None
                result.score  = None
            elif item.scoring_type in ('rating_5', 'rating_10'):
                raw = request.form.get(f"{prefix}score", '')
                try:
                    result.score  = float(raw)
                    result.passed = result.score > 0
                except (ValueError, TypeError):
                    result.score  = None
                    result.passed = None
            else:
                result.passed = None
                result.score  = None

            result.comments = request.form.get(f"{prefix}comments", '').strip() or None

            # Photo upload
            photo_file = request.files.get(f"{prefix}photo")
            if photo_file and photo_file.filename:
                path = _save_photo(photo_file)
                if path:
                    result.photo_path = path

        if action == 'complete':
            # Validate all required-photo items have a photo
            missing_photos = [
                r for r in results
                if r.checklist_item.requires_photo and not r.photo_path
            ]
            if missing_photos:
                db.session.commit()
                flash(f'{len(missing_photos)} item(s) require a photo before completing.', 'warning')
                return redirect(url_for('inspections.execute', inspection_id=inspection_id))

            inspection.overall_score = _compute_score(inspection)
            inspection.status        = 'completed'
            inspection.completed_at  = datetime.utcnow()
            db.session.commit()
            flash('Inspection completed successfully!', 'success')
            return redirect(url_for('inspections.view', inspection_id=inspection_id))

        db.session.commit()
        flash('Progress saved.', 'success')
        return redirect(url_for('inspections.execute', inspection_id=inspection_id))

    # Count answered vs total
    answered = sum(1 for r in results if r.passed is not None or r.score is not None)
    staff    = User.query.filter(User.role.in_(['supervisor', 'inspector'])).order_by(User.username).all()

    return render_template('inspections/execute.html',
                           inspection=inspection,
                           results=results,
                           answered=answered,
                           staff=staff)


# ── View (completed) ──────────────────────────────────────────────────────────

@bp.route('/<int:inspection_id>')
@login_required
def view(inspection_id):
    inspection = Inspection.query.get_or_404(inspection_id)

    if current_user.role == 'inspector' and inspection.inspector_id != current_user.id:
        flash('Access denied.', 'danger')
        return redirect(url_for('inspections.index'))

    results = (
        InspectionResult.query
        .join(ChecklistItem)
        .filter(InspectionResult.inspection_id == inspection_id)
        .order_by(ChecklistItem.display_order)
        .all()
    )

    # Group by category
    categories = {}
    for r in results:
        cat = r.checklist_item.category or 'General'
        categories.setdefault(cat, []).append(r)

    issues = inspection.issues.order_by(Issue.reported_at.desc()).all()

    return render_template('inspections/view.html',
                           inspection=inspection,
                           categories=categories,
                           issues=issues)


# ── Flag issue during inspection ──────────────────────────────────────────────

@bp.route('/<int:inspection_id>/flag-issue', methods=['GET', 'POST'])
@login_required
def flag_issue(inspection_id):
    inspection = Inspection.query.get_or_404(inspection_id)

    if current_user.role == 'inspector' and inspection.inspector_id != current_user.id:
        flash('Access denied.', 'danger')
        return redirect(url_for('inspections.index'))

    form = IssueForm()
    areas = Area.query.filter_by(facility_id=inspection.facility_id).order_by(Area.name).all()
    staff = User.query.filter(User.role.in_(['supervisor','inspector'])).order_by(User.username).all()

    form.area_id.choices     = [(a.id, a.name) for a in areas]
    form.assigned_to.choices = [(0, '— Unassigned —')] + [(u.id, u.username) for u in staff]

    if form.validate_on_submit():
        photo_path = _save_photo(form.photo.data, subfolder='issue_photos')
        issue = Issue(
            inspection_id = inspection_id,
            area_id       = form.area_id.data,
            severity      = form.severity.data,
            description   = form.description.data,
            photo_path    = photo_path,
            status        = 'open',
            assigned_to   = form.assigned_to.data or None,
            reported_at   = datetime.utcnow(),
        )
        db.session.add(issue)

        # Auto-flag the inspection if a high/critical issue is logged
        if form.severity.data in ('high', 'critical') and inspection.status != 'completed':
            inspection.status = 'flagged'

        db.session.commit()
        flash('Issue logged successfully.', 'success')
        return redirect(url_for('inspections.execute', inspection_id=inspection_id))

    return render_template('inspections/flag_issue.html',
                           form=form, inspection=inspection)


# ── Delete ────────────────────────────────────────────────────────────────────

@bp.route('/<int:inspection_id>/delete', methods=['POST'])
@login_required
@supervisor_required
def delete(inspection_id):
    inspection = Inspection.query.get_or_404(inspection_id)
    db.session.delete(inspection)
    db.session.commit()
    flash('Inspection deleted.', 'success')
    return redirect(url_for('inspections.index'))
