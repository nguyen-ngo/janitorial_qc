import os
import json
import uuid
from datetime import datetime
from app.utils.time_utils import now_eastern
from flask import (Blueprint, render_template, redirect, url_for,
                   flash, request, current_app, jsonify, Response)
from flask_login import login_required, current_user
from app import db
from app.models.inspection import (Inspection, InspectionTemplate,
                                   ChecklistItem, InspectionResult)
from app.models.facility import Facility, Area
from app.models.issue import Issue
from app.models.user import User
from app.utils.forms import StartInspectionForm, IssueForm
from app.utils.decorators import supervisor_required
from app.utils.pdf_export import generate_inspection_pdf
from app.utils.notifications import notify, notify_customers_for_facility
from app.models.notification import (
    EVENT_INSPECTION_DONE, EVENT_ISSUE_ASSIGNED,
    EVENT_CUSTOMER_INSPECTION_DONE, EVENT_CUSTOMER_ISSUE_UPDATED,
)
from app.utils.audit import log_action, ACTION_CREATE, ACTION_UPDATE, ACTION_DELETE, ACTION_EXPORT
from app.utils.scope import get_customer_scope

bp = Blueprint('inspections', __name__, url_prefix='/inspections')

ALLOWED_EXTENSIONS = {'jpg', 'jpeg', 'png', 'gif'}

INPUT_FIELD_TYPES = {
    'text', 'textarea', 'number', 'date', 'email',
    'checkbox', 'checkbox_group', 'radio', 'select',
    'rating', 'signature', 'image', 'table'
}


def _save_photo(file_obj, subfolder='inspection_photos'):
    """Save an uploaded photo; return the relative path or None."""
    if not file_obj or not file_obj.filename:
        return None
    ext = file_obj.filename.rsplit('.', 1)[-1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return None
    filename = f"{uuid.uuid4().hex}.{ext}"
    dest_dir = os.path.join(current_app.config['UPLOAD_FOLDER'], subfolder)
    os.makedirs(dest_dir, exist_ok=True)
    file_obj.save(os.path.join(dest_dir, filename))
    return f"uploads/{subfolder}/{filename}"


def _collect_form_responses(form_fields, existing_responses=None):
    """
    Walk the submitted form data and collect responses keyed by field ID.
    Returns a dict: { field_id: value_or_list_or_path }
    Photo uploads are saved to disk; their path is stored as the value.

    existing_responses: previously saved form data (from inspection.notes).
    Used to preserve photo paths when no new file is uploaded on resubmit.
    """
    if existing_responses is None:
        existing_responses = {}

    responses = {}
    for field in form_fields:
        fid   = field['id']
        ftype = field['type']

        if ftype in ('label', 'section', 'button_submit', 'button_print', 'button_email'):
            continue  # display-only, nothing to capture

        key = f"field_{fid}"

        if ftype == 'checkbox':
            responses[fid] = 'true' if request.form.get(key) else 'false'

        elif ftype == 'checkbox_group':
            responses[fid] = request.form.getlist(key)

        elif ftype == 'image':
            photo_file = request.files.get(key)
            path = _save_photo(photo_file, subfolder='inspection_photos')
            if path:
                # New file uploaded — use the new path
                responses[fid] = path
            else:
                # No new file — preserve the previously saved photo path.
                # JSON keys are always strings; try both str and original type.
                existing_path = (
                    existing_responses.get(str(fid))
                    or existing_responses.get(fid)
                    or ''
                )
                responses[fid] = existing_path

        elif ftype == 'table':
            cols = field.get('col_headers') or ['Column 1']
            rows = int(field.get('table_rows') or 3)
            table_data = []
            for r in range(rows):
                row_data = {}
                for c_idx, col in enumerate(cols):
                    cell_key = f"{key}_r{r}_c{c_idx}"
                    row_data[col] = request.form.get(cell_key, '')
                table_data.append(row_data)
            responses[fid] = table_data

        elif ftype == 'rating':
            responses[fid] = request.form.get(key, '0')

        else:
            # text, textarea, number, date, email, radio, select, signature
            responses[fid] = request.form.get(key, '')

    return responses


def _compute_score_from_form(form_fields, responses):
    """
    Derive an overall score from rating fields and checkbox pass/fail fields.
    Returns a float 0–100 or None if the form has no scoreable fields.
    """
    scoreable = [f for f in form_fields if f['type'] in ('rating', 'checkbox', 'radio')]
    if not scoreable:
        return None

    total, earned = 0, 0
    for field in scoreable:
        fid = field['id']
        val = responses.get(fid, '')

        if field['type'] == 'rating':
            try:
                v = int(val)
                if v == 0:
                    continue   # 0 = not answered, skip entirely
                earned += v
                total  += 5
            except (ValueError, TypeError):
                pass  # unparseable = not answered, skip

        elif field['type'] == 'checkbox':
            total  += 1
            if val == 'true':
                earned += 1

        elif field['type'] == 'radio':
            # Options that look like pass/yes/ok score 1; fail/no/na score 0
            total += 1
            if val.lower() in ('pass', 'yes', 'ok', 'good', 'acceptable', 'compliant'):
                earned += 1

    return round((earned / total) * 100, 2) if total else None


def _validate_required(form_fields, responses):
    """Return a list of labels for required fields that have empty responses."""
    missing = []
    for field in form_fields:
        if not field.get('required'):
            continue
        ftype = field['type']
        if ftype in ('label', 'section', 'button_submit', 'button_print', 'button_email'):
            continue
        val = responses.get(field['id'])
        empty = (
            val is None
            or val == ''
            or val == 'false'
            or val == '0'
            or val == []
        )
        if empty:
            missing.append(field.get('label', 'Untitled field'))
    return missing


# ── List ──────────────────────────────────────────────────────────────────────

@bp.route('/')
@login_required
def index():
    page = request.args.get('page', 1, type=int)
    q = Inspection.query.order_by(Inspection.inspection_date.desc())

    if current_user.role == 'inspector':
        q = q.filter(Inspection.inspector_id == current_user.id)
    elif current_user.role == 'customer':
        customer_facility_ids = get_customer_scope(current_user)
        if not customer_facility_ids:
            q = q.filter(False)
        else:
            q = q.filter(Inspection.facility_id.in_(customer_facility_ids))

    status_filter    = request.args.get('status', '')
    facility_filter  = request.args.get('facility_id', '')
    follow_up_filter = request.args.get('follow_up', '')
    if status_filter:
        q = q.filter(Inspection.status == status_filter)
    if facility_filter.isdigit():
        q = q.filter(Inspection.facility_id == int(facility_filter))
    if follow_up_filter == '1':
        q = q.filter(
            Inspection.follow_up_required == True,
            Inspection.status == 'completed',
        ).filter(~Inspection.follow_ups.any())

    inspections = q.paginate(page=page, per_page=20, error_out=False)
    if current_user.role == 'customer':
        cids = get_customer_scope(current_user) or []
        facilities = Facility.query.filter(Facility.id.in_(cids), Facility.active == True).order_by(Facility.name).all()
    else:
        facilities  = Facility.query.filter_by(active=True).order_by(Facility.name).all()

    return render_template('inspections/list.html',
                           inspections=inspections,
                           facilities=facilities,
                           status_filter=status_filter,
                           facility_filter=facility_filter,
                           follow_up_filter=follow_up_filter)


# ── Start ─────────────────────────────────────────────────────────────────────

@bp.route('/start', methods=['GET', 'POST'])
@login_required
def start():
    form = StartInspectionForm()

    templates  = InspectionTemplate.query.order_by(InspectionTemplate.name).all()
    facilities = Facility.query.filter_by(active=True).order_by(Facility.name).all()

    form.template_id.choices = [(t.id, t.name) for t in templates]
    form.facility_id.choices = [(f.id, f.name) for f in facilities]

    # Pre-select template/facility when arriving from reinspect()
    from flask import session as _session
    if not form.is_submitted():
        if _session.get('reinspect_template_id'):
            form.template_id.data = _session['reinspect_template_id']
        if _session.get('reinspect_facility_id'):
            form.facility_id.data = _session['reinspect_facility_id']

    selected_fid = form.facility_id.data or (facilities[0].id if facilities else None)
    areas = Area.query.filter_by(facility_id=selected_fid).order_by(Area.name).all() if selected_fid else []
    form.area_id.choices = [(0, '— No specific area —')] + [(a.id, a.name) for a in areas]

    if form.validate_on_submit():
        template = InspectionTemplate.query.get_or_404(form.template_id.data)

        # Guard: template must have a form built in the form editor
        if not template.get_form_schema():
            flash('This template has no form fields yet. Please build the form in the template editor first.', 'warning')
            return redirect(url_for('inspections.start'))

        from flask import session as _session
        parent_id = _session.pop('reinspect_parent_id', None)
        inspection = Inspection(
            template_id          = template.id,
            facility_id          = form.facility_id.data,
            area_id              = form.area_id.data or None,
            inspector_id         = current_user.id,
            inspection_date      = now_eastern(),
            status               = 'in_progress',
            notes                = form.notes.data or None,
            parent_inspection_id = parent_id,
        )
        db.session.add(inspection)
        db.session.commit()
        log_action(ACTION_CREATE, 'Inspection', inspection.id,
                   f'{inspection.template.name} @ {inspection.facility.name}',
                   f'template_id={inspection.template_id}; facility_id={inspection.facility_id}')

        flash('Inspection started. Fill in the form below and submit when complete.', 'info')
        return redirect(url_for('inspections.execute', inspection_id=inspection.id))

    return render_template('inspections/start.html', form=form, facilities=facilities)


# ── AJAX: areas for a given facility ─────────────────────────────────────────

@bp.route('/areas/<int:facility_id>')
@login_required
def areas_for_facility(facility_id):
    areas = Area.query.filter_by(facility_id=facility_id).order_by(Area.name).all()
    return jsonify([{'id': a.id, 'name': a.name} for a in areas])


# ── Execute — render and submit the template form ─────────────────────────────

@bp.route('/<int:inspection_id>/execute', methods=['GET', 'POST'])
@login_required
def execute(inspection_id):
    inspection = Inspection.query.get_or_404(inspection_id)

    if current_user.role == 'inspector' and inspection.inspector_id != current_user.id:
        flash('Access denied.', 'danger')
        return redirect(url_for('inspections.index'))

    if inspection.status == 'completed':
        return redirect(url_for('inspections.view', inspection_id=inspection_id))

    template    = inspection.template
    form_fields = template.get_form_schema()

    # Sort fields by grid position (row then col) for logical reading order
    form_fields = sorted(form_fields, key=lambda f: (f.get('row', 0), f.get('col', 0)))

    # Load any previously saved draft responses
    saved_responses = {}
    if inspection.notes:
        try:
            parsed = json.loads(inspection.notes)
            if isinstance(parsed, dict) and '_form_data' in parsed:
                saved_responses = parsed['_form_data']
        except (json.JSONDecodeError, TypeError):
            pass

    if request.method == 'POST':
        action = request.form.get('action', 'submit')

        # Collect all field responses from the submitted form.
        # Pass saved_responses so existing photo paths are preserved
        # when no new file is selected on this submission.
        responses = _collect_form_responses(form_fields, saved_responses)

        if action == 'submit':
            # Validate required fields
            missing = _validate_required(form_fields, responses)
            if missing:
                # Save draft so the inspector doesn't lose their work
                _save_draft(inspection, responses)
                flash(
                    f'Please complete all required fields before submitting: '
                    f'{", ".join(missing[:5])}{"…" if len(missing) > 5 else ""}',
                    'warning'
                )
                return redirect(url_for('inspections.execute', inspection_id=inspection_id))

            # Compute score and mark complete
            score = _compute_score_from_form(form_fields, responses)
            inspection.overall_score = score
            inspection.status        = 'completed'
            inspection.completed_at  = now_eastern()

            # Persist the final form data alongside any inspector notes
            _save_responses(inspection, responses)
            db.session.commit()

            # ── Notify supervisors/admins that an inspection was completed ──
            supervisors = User.query.filter(
                User.role.in_(['admin', 'supervisor'])
            ).all()
            inspection_link = url_for('inspections.view', inspection_id=inspection.id)
            score_display = f'{score:.1f}%' if score is not None else 'N/A'
            for supervisor in supervisors:
                if supervisor.id != current_user.id:
                    notify(
                        recipient     = supervisor,
                        title         = f'Inspection #{inspection.id} Completed',
                        body          = (
                            f'{current_user.username} completed an inspection at '
                            f'{inspection.facility.name} using the '
                            f'"{inspection.template.name}" template. '
                            f'Overall score: {score_display}.'
                        ),
                        link          = inspection_link,
                        inspection_id = inspection.id,
                        event_type    = EVENT_INSPECTION_DONE,
                        send_email    = True,
                    )
            # ── Notify customer portal users for this facility ──────────
            notify_customers_for_facility(
                facility_id   = inspection.facility_id,
                event_type    = EVENT_CUSTOMER_INSPECTION_DONE,
                title         = f'Inspection Completed at {inspection.facility.name}',
                body          = (
                    f'An inspection using the "{inspection.template.name}" template '
                    f'was completed at {inspection.facility.name}. '
                    f'Overall score: {score_display}.'
                ),
                link          = url_for('inspections.view', inspection_id=inspection.id),
                inspection_id = inspection.id,
            )
            db.session.commit()  # Commit notifications
            log_action(ACTION_UPDATE, 'Inspection', inspection.id,
                       f'{inspection.template.name} @ {inspection.facility.name}',
                       f'status=completed; score={score}')

            flash('Inspection submitted successfully!', 'success')
            return redirect(url_for('inspections.view', inspection_id=inspection_id))

        else:  # save draft
            _save_draft(inspection, responses)
            db.session.commit()
            flash('Draft saved. You can continue filling in the form later.', 'success')
            return redirect(url_for('inspections.execute', inspection_id=inspection_id))

    return render_template('inspections/execute.html',
                           inspection=inspection,
                           form_fields=form_fields,
                           saved_responses=saved_responses)


def _save_responses(inspection, responses):
    """Persist final form responses into inspection.notes as JSON."""
    existing = {}
    if inspection.notes:
        try:
            existing = json.loads(inspection.notes)
        except (json.JSONDecodeError, TypeError):
            existing = {'_inspector_notes': inspection.notes}
    existing['_form_data'] = responses
    inspection.notes = json.dumps(existing)


def _save_draft(inspection, responses):
    """Save a draft of form responses — same storage as final, just status stays in_progress."""
    _save_responses(inspection, responses)
    db.session.commit()


# ── AJAX: save draft (used by Flag Issue button before navigating away) ───────

@bp.route('/<int:inspection_id>/save-draft', methods=['POST'])
@login_required
def save_draft_ajax(inspection_id):
    """
    Accepts a JSON body of { field_id: value } and persists it as a draft.
    Returns JSON { ok: true } on success so the caller can navigate to flag_issue.
    """
    inspection = Inspection.query.get_or_404(inspection_id)

    if current_user.role == 'inspector' and inspection.inspector_id != current_user.id:
        return jsonify({'ok': False, 'error': 'Access denied'}), 403

    if inspection.status == 'completed':
        return jsonify({'ok': False, 'error': 'Inspection already completed'}), 400

    data = request.get_json(silent=True) or {}
    responses = data.get('responses', {})

    # Merge with any previously saved responses so photo paths are preserved
    existing_responses = {}
    if inspection.notes:
        try:
            parsed = json.loads(inspection.notes)
            if isinstance(parsed, dict) and '_form_data' in parsed:
                existing_responses = parsed['_form_data']
        except (json.JSONDecodeError, TypeError):
            pass

    # Only overwrite keys that are present in the submitted data;
    # preserve photo paths for image fields not included in the JSON payload.
    merged = {**existing_responses, **responses}

    _save_responses(inspection, merged)
    db.session.commit()

    current_app.logger.info(
        'INSPECTION DRAFT SAVED (flag) | inspection_id=%s | by=%s',
        inspection_id, current_user.username
    )
    return jsonify({'ok': True})


# ── View ──────────────────────────────────────────────────────────────────────

@bp.route('/<int:inspection_id>')
@login_required
def view(inspection_id):
    inspection = Inspection.query.get_or_404(inspection_id)

    if current_user.role == 'inspector' and inspection.inspector_id != current_user.id:
        flash('Access denied.', 'danger')
        return redirect(url_for('inspections.index'))
    if current_user.role == 'customer':
        cids = get_customer_scope(current_user) or []
        if inspection.facility_id not in cids:
            flash('Access denied.', 'danger')
            return redirect(url_for('inspections.index'))

    template    = inspection.template
    form_fields = sorted(template.get_form_schema(),
                         key=lambda f: (f.get('row', 0), f.get('col', 0)))

    # Decode saved responses
    form_data = {}
    if inspection.notes:
        try:
            parsed = json.loads(inspection.notes)
            if isinstance(parsed, dict):
                form_data = parsed.get('_form_data', {})
        except (json.JSONDecodeError, TypeError):
            pass

    issues = inspection.issues.order_by(Issue.reported_at.desc()).all()

    # ── Score comparison against parent inspection ────────────────────────
    # Produces a list of per-item dicts so the template can render a
    # side-by-side delta table without any additional queries at render time.
    comparison = None
    if inspection.parent and inspection.parent.status == 'completed':
        parent = inspection.parent

        # Index parent results by checklist_item_id
        parent_results = {
            r.checklist_item_id: r
            for r in parent.results.all()
        }

        rows = []
        for result in inspection.results.order_by(
            InspectionResult.checklist_item_id
        ).all():
            item   = result.checklist_item
            p_res  = parent_results.get(result.checklist_item_id)

            # Normalise scores to a 0–100 percentage for fair comparison
            def _pct(res, ci):
                if res is None:
                    return None
                if ci.scoring_type == 'pass_fail':
                    if res.passed is None:
                        return None
                    return 100.0 if res.passed else 0.0
                if ci.scoring_type == 'rating_5':
                    return (float(res.score) / 5 * 100) if res.score is not None else None
                if ci.scoring_type == 'rating_10':
                    return (float(res.score) / 10 * 100) if res.score is not None else None
                return None

            cur_pct = _pct(result, item)
            par_pct = _pct(p_res, item) if p_res else None

            if cur_pct is None and par_pct is None:
                continue   # skip unanswered items on both sides

            delta = None
            if cur_pct is not None and par_pct is not None:
                delta = round(cur_pct - par_pct, 1)

            rows.append({
                'category':    item.category or 'Uncategorised',
                'description': item.item_description,
                'parent_pct':  round(par_pct, 1) if par_pct is not None else None,
                'current_pct': round(cur_pct, 1) if cur_pct is not None else None,
                'delta':       delta,
            })

        comparison = {
            'parent_id':    parent.id,
            'parent_date':  parent.inspection_date,
            'parent_score': float(parent.overall_score) if parent.overall_score else None,
            'current_score': float(inspection.overall_score) if inspection.overall_score else None,
            'score_delta':  (
                round(float(inspection.overall_score) - float(parent.overall_score), 2)
                if inspection.overall_score and parent.overall_score else None
            ),
            'rows': rows,
            'improved':  sum(1 for r in rows if r['delta'] is not None and r['delta'] > 0),
            'regressed': sum(1 for r in rows if r['delta'] is not None and r['delta'] < 0),
            'unchanged': sum(1 for r in rows if r['delta'] == 0),
        }

    return render_template('inspections/view.html',
                           inspection=inspection,
                           form_fields=form_fields,
                           form_data=form_data,
                           issues=issues,
                           comparison=comparison)


# ── Flag issue during inspection ──────────────────────────────────────────────

@bp.route('/<int:inspection_id>/flag-issue', methods=['GET', 'POST'])
@login_required
def flag_issue(inspection_id):
    inspection = Inspection.query.get_or_404(inspection_id)

    if current_user.role == 'inspector' and inspection.inspector_id != current_user.id:
        flash('Access denied.', 'danger')
        return redirect(url_for('inspections.index'))

    form  = IssueForm()
    areas = Area.query.filter_by(facility_id=inspection.facility_id).order_by(Area.name).all()
    staff = User.query.filter(User.role.in_(['supervisor', 'inspector'])).order_by(User.username).all()

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
            reported_at   = now_eastern(),
        )
        db.session.add(issue)

        if form.severity.data in ('high', 'critical') and inspection.status != 'completed':
            inspection.status = 'flagged'

        db.session.commit()
        current_app.logger.info(
            'ISSUE FLAGGED | issue_id=%s | inspection_id=%s | severity=%s | assigned_to=%s | by=%s',
            issue.id, inspection_id, issue.severity, issue.assigned_to, current_user.username
        )

        # ── Notify the assignee of the flagged issue ─────────────────────
        if issue.assigned_to:
            assignee = User.query.get(issue.assigned_to)
            if assignee and assignee.id != current_user.id:
                notify(
                    recipient     = assignee,
                    title         = f'New Issue #{issue.id} Assigned to You',
                    body          = (
                        f'A {issue.severity.title()}-severity issue was flagged during '
                        f'inspection #{inspection_id} at {inspection.facility.name} '
                        f'and assigned to you. '
                        f'Description: {issue.description[:120]}'
                        f'{"…" if len(issue.description) > 120 else ""}'
                    ),
                    link          = url_for('issues.view', issue_id=issue.id),
                    issue_id      = issue.id,
                    event_type    = EVENT_ISSUE_ASSIGNED,
                    send_email    = True,
                )
                db.session.commit()  # Commit notification

        # ── Notify customer portal users for this facility ──────────
        notify_customers_for_facility(
            facility_id = inspection.facility_id,
            event_type  = EVENT_CUSTOMER_ISSUE_UPDATED,
            title       = f'New Issue #{issue.id} at {inspection.facility.name}',
            body        = (
                f'A new {issue.severity.title()}-severity issue has been logged '
                f'in {issue.area.name} at {inspection.facility.name} '
                f'during inspection #{inspection_id}. '
                f'Description: {issue.description[:120]}'
                f'{"…" if len(issue.description) > 120 else ""}'
            ),
            link     = url_for('issues.view', issue_id=issue.id),
            issue_id = issue.id,
        )
        db.session.commit()

        flash('Issue logged successfully.', 'success')
        return redirect(url_for('inspections.execute', inspection_id=inspection_id))

    return render_template('inspections/flag_issue.html',
                           form=form, inspection=inspection)


# ── Export to PDF ────────────────────────────────────────────────────────────

@bp.route('/<int:inspection_id>/export-pdf')
@login_required
def export_pdf(inspection_id):
    """Generate and stream a PDF report for the given inspection."""
    inspection = Inspection.query.get_or_404(inspection_id)

    # Inspectors may only export their own inspections
    if current_user.role == 'inspector' and inspection.inspector_id != current_user.id:
        flash('Access denied.', 'danger')
        return redirect(url_for('inspections.index'))
    if current_user.role == 'customer':
        cids = get_customer_scope(current_user) or []
        if inspection.facility_id not in cids:
            flash('Access denied.', 'danger')
            return redirect(url_for('inspections.index'))

    template    = inspection.template
    form_fields = sorted(template.get_form_schema(),
                         key=lambda f: (f.get('row', 0), f.get('col', 0)))

    form_data = {}
    if inspection.notes:
        try:
            parsed = json.loads(inspection.notes)
            if isinstance(parsed, dict):
                form_data = parsed.get('_form_data', {})
        except (json.JSONDecodeError, TypeError):
            pass

    issues = inspection.issues.order_by(Issue.reported_at.desc()).all()

    static_folder = os.path.join(current_app.root_path, 'static')

    # Log image field values to help diagnose missing-photo issues in PDF export
    for field in form_fields:
        if field.get('type') == 'image':
            fid = str(field.get('id', ''))
            val = form_data.get(fid, '')
            resolved = os.path.join(static_folder, val) if val else ''
            current_app.logger.info(
                'PDF export image field | fid=%s | val=%r | exists=%s | resolved=%r',
                fid, val, os.path.exists(resolved) if resolved else False, resolved
            )

    pdf_bytes = generate_inspection_pdf(
        inspection   = inspection,
        form_fields  = form_fields,
        form_data    = form_data,
        issues       = issues,
        static_folder= static_folder,
    )

    filename = (f'inspection_{inspection.id}_'
                f'{inspection.inspection_date.strftime("%Y%m%d")}.pdf')

    current_app.logger.info(
        'PDF export | inspection_id=%s | inspector=%s | by=%s',
        inspection.id, inspection.inspector.username, current_user.username
    )
    log_action(ACTION_EXPORT, 'Inspection', inspection.id,
               f'{inspection.template.name} @ {inspection.facility.name}',
               f'format=pdf')

    return Response(
        pdf_bytes,
        mimetype='application/pdf',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'},
    )


# ── Flag / clear follow-up required ──────────────────────────────────────────

@bp.route('/<int:inspection_id>/flag-followup', methods=['POST'])
@login_required
@supervisor_required
def flag_followup(inspection_id):
    """Mark an inspection as requiring a follow-up re-inspection."""
    inspection = Inspection.query.get_or_404(inspection_id)
    note = request.form.get('follow_up_note', '').strip() or None

    inspection.follow_up_required = True
    inspection.follow_up_note     = note
    db.session.commit()

    current_app.logger.info(
        'INSPECTION FOLLOW-UP FLAGGED | id=%s | by=%s | note=%r',
        inspection_id, current_user.username, note,
    )
    log_action(ACTION_UPDATE, 'Inspection', inspection_id,
               f'{inspection.template.name} @ {inspection.facility.name}',
               f'follow_up_required=True; note={note!r}')
    flash('Follow-up inspection required flag set.', 'warning')
    return redirect(url_for('inspections.view', inspection_id=inspection_id))


@bp.route('/<int:inspection_id>/clear-followup', methods=['POST'])
@login_required
@supervisor_required
def clear_followup(inspection_id):
    """Clear the follow-up required flag once actioned."""
    inspection = Inspection.query.get_or_404(inspection_id)
    inspection.follow_up_required = False
    inspection.follow_up_note     = None
    db.session.commit()
    log_action(ACTION_UPDATE, 'Inspection', inspection_id,
               f'{inspection.template.name} @ {inspection.facility.name}',
               'follow_up_required=False (cleared)')
    flash('Follow-up flag cleared.', 'success')
    return redirect(url_for('inspections.view', inspection_id=inspection_id))


# ── Start a re-inspection (linked to parent) ──────────────────────────────────

@bp.route('/<int:inspection_id>/reinspect')
@login_required
def reinspect(inspection_id):
    """Pre-fill the Start Inspection form with the same template/facility,
    linking the new inspection to the parent via parent_inspection_id."""
    from flask import session
    parent = Inspection.query.get_or_404(inspection_id)

    if current_user.role == 'customer':
        flash('Access denied.', 'danger')
        return redirect(url_for('inspections.index'))

    # Store parent context in session so start() can pick it up
    session['reinspect_parent_id']  = parent.id
    session['reinspect_template_id'] = parent.template_id
    session['reinspect_facility_id'] = parent.facility_id
    flash(
        f'Starting re-inspection of #{parent.id} — '
        f'{parent.template.name} @ {parent.facility.name}.',
        'info',
    )
    return redirect(url_for('inspections.start'))


# ── Delete ────────────────────────────────────────────────────────────────────

@bp.route('/<int:inspection_id>/delete', methods=['POST'])
@login_required
@supervisor_required
def delete(inspection_id):
    inspection = Inspection.query.get_or_404(inspection_id)

    # Capture identifiers for the log before deletion
    insp_id       = inspection.id
    insp_date     = inspection.inspection_date.strftime('%Y-%m-%d %H:%M')
    facility_name = inspection.facility.name
    template_name = inspection.template.name
    inspector_name = inspection.inspector.username

    # ── Clean up uploaded photos from disk ────────────────────────────────
    # Collect photo paths from form data (inspection_photos) and issues
    photo_paths = []
    if inspection.notes:
        try:
            notes_data = json.loads(inspection.notes)
            form_data  = notes_data.get('_form_data', {}) if isinstance(notes_data, dict) else {}
            for val in form_data.values():
                if isinstance(val, str) and val.startswith('uploads/'):
                    photo_paths.append(val)
        except (json.JSONDecodeError, TypeError):
            pass

    for issue in inspection.issues.all():
        if issue.photo_path:
            photo_paths.append(issue.photo_path)

    # Delete the inspection record (cascade removes results and issues)
    db.session.delete(inspection)
    db.session.commit()

    # Remove photo files after successful DB commit
    for rel_path in photo_paths:
        abs_path = os.path.join(current_app.config['UPLOAD_FOLDER'], '..', 'static', rel_path)
        abs_path = os.path.normpath(abs_path)
        try:
            if os.path.isfile(abs_path):
                os.remove(abs_path)
        except OSError:
            pass  # Non-fatal: log but don't block the response

    current_app.logger.info(
        'INSPECTION DELETED | id=%s | facility="%s" | template="%s" | '
        'date=%s | inspector=%s | deleted_by=%s',
        insp_id, facility_name, template_name,
        insp_date, inspector_name, current_user.username
    )
    log_action(ACTION_DELETE, 'Inspection', insp_id,
               f'{template_name} @ {facility_name}',
               f'date={insp_date}; inspector={inspector_name}')

    flash(
        f'Inspection #{insp_id} ({template_name} — {facility_name}, {insp_date}) '
        f'has been permanently deleted.',
        'success'
    )
    return redirect(url_for('inspections.index'))