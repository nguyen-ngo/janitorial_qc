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
                responses[fid] = path
            else:
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
                    continue
                earned += v
                total  += 5
            except (ValueError, TypeError):
                pass

        elif field['type'] == 'checkbox':
            total  += 1
            if val == 'true':
                earned += 1

        elif field['type'] == 'radio':
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
        facilities = Facility.query.filter_by(active=True).order_by(Facility.name).all()

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


# ── Execute ───────────────────────────────────────────────────────────────────

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
    form_fields = sorted(form_fields, key=lambda f: (f.get('row', 0), f.get('col', 0)))

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
        responses = _collect_form_responses(form_fields, saved_responses)

        if action == 'submit':
            missing = _validate_required(form_fields, responses)
            if missing:
                _save_draft(inspection, responses)
                flash(
                    f'Please complete all required fields before submitting: '
                    f'{", ".join(missing[:5])}{"…" if len(missing) > 5 else ""}',
                    'warning'
                )
                return redirect(url_for('inspections.execute', inspection_id=inspection_id))

            score = _compute_score_from_form(form_fields, responses)
            inspection.overall_score = score
            inspection.status        = 'completed'
            inspection.completed_at  = now_eastern()

            _save_responses(inspection, responses)
            db.session.commit()

            supervisors = User.query.filter(User.role.in_(['admin', 'supervisor'])).all()
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
            db.session.commit()
            log_action(ACTION_UPDATE, 'Inspection', inspection.id,
                       f'{inspection.template.name} @ {inspection.facility.name}',
                       f'status=completed; score={score}')

            flash('Inspection submitted successfully!', 'success')
            return redirect(url_for('inspections.view', inspection_id=inspection_id))

        else:
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
    """Save a draft — same storage as final, status stays in_progress."""
    _save_responses(inspection, responses)
    db.session.commit()


# ── AJAX: save draft ──────────────────────────────────────────────────────────

@bp.route('/<int:inspection_id>/save-draft', methods=['POST'])
@login_required
def save_draft_ajax(inspection_id):
    inspection = Inspection.query.get_or_404(inspection_id)

    if current_user.role == 'inspector' and inspection.inspector_id != current_user.id:
        return jsonify({'ok': False, 'error': 'Access denied'}), 403

    if inspection.status == 'completed':
        return jsonify({'ok': False, 'error': 'Inspection already completed'}), 400

    data = request.get_json(silent=True) or {}
    responses = data.get('responses', {})

    existing_responses = {}
    if inspection.notes:
        try:
            parsed = json.loads(inspection.notes)
            if isinstance(parsed, dict) and '_form_data' in parsed:
                existing_responses = parsed['_form_data']
        except (json.JSONDecodeError, TypeError):
            pass

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
    comparison = None
    if inspection.parent and inspection.parent.status == 'completed':
        parent = inspection.parent

        parent_form_data = {}
        if parent.notes:
            try:
                parsed_p = json.loads(parent.notes)
                if isinstance(parsed_p, dict):
                    parent_form_data = parsed_p.get('_form_data', {})
            except (json.JSONDecodeError, TypeError):
                pass

        # ── DEBUG: log raw schema + form_data so we can see what's actually stored ──
        current_app.logger.warning(
            'COMPARISON DEBUG | inspection_id=%s | current_form_data=%s',
            inspection_id, json.dumps(form_data)
        )
        current_app.logger.warning(
            'COMPARISON DEBUG | parent_id=%s | parent_form_data=%s',
            parent.id, json.dumps(parent_form_data)
        )
        current_app.logger.warning(
            'COMPARISON DEBUG | form_fields=%s',
            json.dumps([
                {'id': f.get('id'), 'type': f.get('type'), 'row': f.get('row'),
                 'col': f.get('col'), 'label': f.get('label'), 'text_content': f.get('text_content')}
                for f in form_fields
            ])
        )
        # ── END DEBUG ──

        scoreable_types = ('rating', 'checkbox', 'radio')

        # Collect all text/textarea fields per row, keyed by (col, fid)
        # so we can pick the leftmost non-empty value as the item name.
        _row_text_candidates = {}   # row → [(col, fid), ...]
        _preceding_label     = {}   # field_id → nearest section/label text
        _last_label_text     = ''

        for _f in form_fields:
            _ftype = _f.get('type')
            _frow  = _f.get('row', 0)
            _fcol  = _f.get('col', 0)
            _fid   = str(_f.get('id', ''))

            if _ftype == 'label':
                _last_label_text = (
                    _f.get('text_content')
                    or _f.get('label')
                    or _f.get('text')
                    or _last_label_text
                )
            elif _ftype in ('text', 'textarea'):
                if _frow not in _row_text_candidates:
                    _row_text_candidates[_frow] = []
                _row_text_candidates[_frow].append((_fcol, _fid))
            elif _ftype == 'section':
                _last_label_text = (
                    _f.get('label') or _f.get('text_content') or _last_label_text
                )
            elif _ftype in scoreable_types:
                _preceding_label[_fid] = _last_label_text

        # Resolve row → item name: leftmost text field with a non-empty value
        _row_text_val = {}
        for _frow, _candidates in _row_text_candidates.items():
            for _fcol, _fid in sorted(_candidates):
                _val = (
                    str(form_data.get(_fid, '')).strip()
                    or str(parent_form_data.get(_fid, '')).strip()
                )
                if _val:
                    _row_text_val[_frow] = _val
                    break

        def _resolve_label(field, fid):
            frow = field.get('row', 0)
            if frow in _row_text_val:
                return _row_text_val[frow]
            own = field.get('label') or field.get('placeholder')
            if own:
                return own
            return _preceding_label.get(fid) or fid

        rows = []

        for field in form_fields:
            ftype = field.get('type')
            if ftype not in scoreable_types:
                continue

            fid   = str(field.get('id', ''))
            label = _resolve_label(field, fid)

            cur_val = form_data.get(fid, '')
            par_val = parent_form_data.get(fid, '')

            def _field_pct(val, ft):
                if ft == 'rating':
                    try:
                        v = int(val)
                        return None if v == 0 else round(v / 5 * 100, 1)
                    except (ValueError, TypeError):
                        return None
                if ft == 'checkbox':
                    if val == '' or val is None:
                        return None
                    return 100.0 if val == 'true' else 0.0
                if ft == 'radio':
                    return None if not val else 100.0
                return None

            cur_pct = _field_pct(cur_val, ftype)
            par_pct = _field_pct(par_val, ftype)

            if cur_pct is None and par_pct is None:
                continue

            delta = None
            if cur_pct is not None and par_pct is not None:
                delta = round(cur_pct - par_pct, 1)

            rows.append({
                'category':    _preceding_label.get(fid) or field.get('category') or 'General',
                'description': label,
                'parent_pct':  par_pct,
                'current_pct': cur_pct,
                'delta':       delta,
            })

        comparison = {
            'parent_id':     parent.id,
            'parent_date':   parent.inspection_date,
            'parent_score':  float(parent.overall_score) if parent.overall_score else None,
            'current_score': float(inspection.overall_score) if inspection.overall_score else None,
            'score_delta':   (
                round(float(inspection.overall_score) - float(parent.overall_score), 2)
                if inspection.overall_score and parent.overall_score else None
            ),
            'rows':      rows,
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
                db.session.commit()

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


# ── Export to PDF ─────────────────────────────────────────────────────────────

@bp.route('/<int:inspection_id>/export-pdf')
@login_required
def export_pdf(inspection_id):
    """Generate and stream a PDF report for the given inspection."""
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
        inspection    = inspection,
        form_fields   = form_fields,
        form_data     = form_data,
        issues        = issues,
        static_folder = static_folder,
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

    session['reinspect_parent_id']   = parent.id
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

    insp_id        = inspection.id
    insp_date      = inspection.inspection_date.strftime('%Y-%m-%d %H:%M')
    facility_name  = inspection.facility.name
    template_name  = inspection.template.name
    inspector_name = inspection.inspector.username

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

    db.session.delete(inspection)
    db.session.commit()

    for rel_path in photo_paths:
        abs_path = os.path.normpath(
            os.path.join(current_app.config['UPLOAD_FOLDER'], '..', 'static', rel_path)
        )
        try:
            if os.path.isfile(abs_path):
                os.remove(abs_path)
        except OSError:
            pass

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