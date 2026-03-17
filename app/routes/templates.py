from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify
from flask_login import login_required, current_user
from app import db
from app.models.inspection import InspectionTemplate, ChecklistItem
from app.utils.forms import InspectionTemplateForm, ChecklistItemForm
from app.utils.decorators import supervisor_required
import json
from app.utils.audit import log_action, ACTION_CREATE, ACTION_UPDATE, ACTION_DELETE

bp = Blueprint('templates', __name__, url_prefix='/templates')


# ---------------------------------------------------------------------------
# Template CRUD
# ---------------------------------------------------------------------------

@bp.route('/')
@login_required
def index():
    templates = InspectionTemplate.query.order_by(InspectionTemplate.name).all()
    return render_template('templates/list.html', templates=templates)


@bp.route('/new', methods=['GET', 'POST'])
@login_required
@supervisor_required
def create_template():
    form = InspectionTemplateForm()

    if form.validate_on_submit():
        template = InspectionTemplate(
            name=form.name.data,
            description=form.description.data,
            frequency=form.frequency.data,
            created_by=current_user.id
        )
        db.session.add(template)
        db.session.commit()
        log_action(ACTION_CREATE, 'Template', template.id, template.name,
                   f'frequency={template.frequency}')

        flash(f'Template "{template.name}" created successfully.', 'success')
        return redirect(url_for('templates.form_editor', template_id=template.id))

    return render_template('templates/form.html', form=form, title='Create Inspection Template')


@bp.route('/<int:template_id>')
@login_required
def view_template(template_id):
    template = InspectionTemplate.query.get_or_404(template_id)
    form_fields = template.get_form_schema()
    return render_template(
        'templates/view.html',
        template=template,
        form_fields=form_fields
    )


@bp.route('/<int:template_id>/edit', methods=['GET', 'POST'])
@login_required
@supervisor_required
def edit_template(template_id):
    template = InspectionTemplate.query.get_or_404(template_id)
    form = InspectionTemplateForm(obj=template)

    if form.validate_on_submit():
        template.name = form.name.data
        template.description = form.description.data
        template.frequency = form.frequency.data
        db.session.commit()
        log_action(ACTION_UPDATE, 'Template', template.id, template.name,
                   f'frequency={template.frequency}')
        flash(f'Template "{template.name}" updated successfully.', 'success')
        return redirect(url_for('templates.view_template', template_id=template.id))

    form_fields = template.get_form_schema()
    return render_template(
        'templates/edit.html',
        form=form,
        template=template,
        form_fields=form_fields
    )


@bp.route('/<int:template_id>/rename', methods=['POST'])
@login_required
@supervisor_required
def rename_template(template_id):
    template = InspectionTemplate.query.get_or_404(template_id)

    new_name = request.form.get('name', '').strip()
    if not new_name:
        flash('Template name cannot be empty.', 'danger')
        return redirect(url_for('templates.index'))
    if len(new_name) > 255:
        flash('Template name is too long (max 255 characters).', 'danger')
        return redirect(url_for('templates.index'))

    valid_frequencies = {'daily', 'weekly', 'monthly', 'quarterly'}
    new_frequency = request.form.get('frequency', '').strip()
    if new_frequency not in valid_frequencies:
        flash('Invalid frequency value.', 'danger')
        return redirect(url_for('templates.index'))

    template.name        = new_name
    template.description = request.form.get('description', '').strip() or None
    template.frequency   = new_frequency

    db.session.commit()
    log_action(ACTION_UPDATE, 'Template', template.id, template.name,
               f'frequency={template.frequency}; via=rename')
    flash(f'Template "{template.name}" updated successfully.', 'success')
    return redirect(url_for('templates.index'))


@bp.route('/<int:template_id>/delete', methods=['POST'])
@login_required
@supervisor_required
def delete_template(template_id):
    template = InspectionTemplate.query.get_or_404(template_id)

    if template.inspections.count() > 0:
        flash('Cannot delete template with existing inspections.', 'danger')
        return redirect(url_for('templates.index'))

    template_name = template.name
    template_id_snap = template.id
    db.session.delete(template)
    db.session.commit()
    log_action(ACTION_DELETE, 'Template', template_id_snap, template_name)

    flash(f'Template "{template_name}" deleted successfully.', 'success')
    return redirect(url_for('templates.index'))


@bp.route('/<int:template_id>/duplicate', methods=['POST'])
@login_required
@supervisor_required
def duplicate_template(template_id):
    src = InspectionTemplate.query.get_or_404(template_id)

    # Duplicate the template header
    new_tpl = InspectionTemplate(
        name=f'{src.name} (Copy)',
        description=src.description,
        frequency=src.frequency,
        created_by=current_user.id
    )
    db.session.add(new_tpl)
    db.session.flush()  # get new_tpl.id before committing

    # Duplicate all checklist items
    for item in src.checklist_items.order_by(ChecklistItem.display_order).all():
        new_item = ChecklistItem(
            template_id=new_tpl.id,
            category=item.category,
            item_description=item.item_description,
            scoring_type=item.scoring_type,
            weight=item.weight,
            requires_photo=item.requires_photo,
            display_order=item.display_order
        )
        db.session.add(new_item)

    # Duplicate form schema if present
    if src.form_schema:
        new_tpl.form_schema = src.form_schema

    db.session.commit()
    log_action(ACTION_CREATE, 'Template', new_tpl.id, new_tpl.name,
               f'duplicated_from={src.id}; frequency={new_tpl.frequency}')

    flash(f'Template "{src.name}" duplicated successfully.', 'success')
    return redirect(url_for('templates.index'))


# ---------------------------------------------------------------------------
# Form Editor
# ---------------------------------------------------------------------------

@bp.route('/<int:template_id>/form-editor')
@login_required
@supervisor_required
def form_editor(template_id):
    template = InspectionTemplate.query.get_or_404(template_id)
    form_schema = template.get_form_schema()
    return render_template(
        'templates/form_editor.html',
        template=template,
        form_schema=form_schema          # pass the list — tojson handles encoding in the template
    )


@bp.route('/<int:template_id>/form-editor/save', methods=['POST'])
@login_required
@supervisor_required
def save_form_schema(template_id):
    """AJAX endpoint — receives the full form schema as JSON and persists it."""
    template = InspectionTemplate.query.get_or_404(template_id)

    data = request.get_json(silent=True)
    if data is None:
        return jsonify({'success': False, 'error': 'Invalid JSON payload'}), 400

    fields = data.get('fields', [])

    # Hard cap on total field count to prevent oversized JSON payloads
    MAX_FIELDS = 150
    if len(fields) > MAX_FIELDS:
        return jsonify({'success': False, 'error': f'Form may not exceed {MAX_FIELDS} fields.'}), 400

    # Basic sanitisation — ensure each field has the minimum required keys
    # Track seen IDs to enforce uniqueness
    seen_ids = set()
    sanitised = []
    for field in fields:
        if not isinstance(field, dict):
            continue
        if not field.get('id') or not field.get('type'):
            continue
        # Reject duplicate field IDs
        field_id = str(field.get('id', ''))
        if field_id in seen_ids:
            continue
        seen_ids.add(field_id)
        ftype = str(field.get('type', 'text'))
        entry = {
            'id':           field_id,
            'type':         ftype,
            'label':        str(field.get('label', 'Untitled'))[:255],
            'placeholder':  str(field.get('placeholder', ''))[:255],
            'required':     bool(field.get('required', False)),
            'options':      field.get('options', []) if ftype in ('radio', 'checkbox_group', 'select', 'pass_fail') else [],
            'help_text':    str(field.get('help_text', ''))[:500],
            'order':        int(field.get('order', 0)),
            # Grid position & size
            'col':          max(1, min(12,  int(field.get('col',     1)))),
            'row':          max(1, min(9999, int(field.get('row',    1)))),
            'colSpan':      max(1, min(12,  int(field.get('colSpan', 6)))),
            'rowSpan':      max(1, min(20,  int(field.get('rowSpan', 2)))),
        }
        # Table-specific fields
        if ftype == 'table':
            raw_hdrs = field.get('col_headers', ['Column 1', 'Column 2', 'Column 3'])
            col_headers = [str(h)[:100] for h in raw_hdrs if isinstance(h, str)][:20] or ['Column 1']
            entry['col_headers'] = col_headers
            entry['table_cols']  = len(col_headers)
            entry['table_rows']  = max(1, min(30, int(field.get('table_rows', 3))))
        # Label-specific fields
        if ftype == 'label':
            entry['text_content'] = str(field.get('text_content', 'Label text'))[:2000]
            entry['font_size']    = field.get('font_size', 'normal') if field.get('font_size') in ('small','normal','large','x-large') else 'normal'
            entry['font_weight']  = 'bold' if field.get('font_weight') == 'bold' else 'normal'
        # Button-specific fields
        if ftype in ('button_submit', 'button_print', 'button_email'):
            defaults = {'button_submit':'Submit Form','button_print':'Print Form','button_email':'Email Form'}
            entry['btn_label'] = str(field.get('btn_label', defaults[ftype]))[:100]
        sanitised.append(entry)

    template.form_schema = sanitised
    db.session.commit()
    log_action(ACTION_UPDATE, 'Template', template.id, template.name,
               f'form_schema saved; field_count={len(sanitised)}')

    return jsonify({'success': True, 'field_count': len(sanitised)})


@bp.route('/<int:template_id>/form-editor/preview')
@login_required
def form_preview(template_id):
    """Renders a read-only preview of the dynamic form."""
    template = InspectionTemplate.query.get_or_404(template_id)
    form_fields = template.get_form_schema()
    return render_template(
        'templates/form_preview.html',
        template=template,
        form_fields=form_fields
    )


# ---------------------------------------------------------------------------
# Checklist Item Management (legacy, kept for backwards compatibility)
# ---------------------------------------------------------------------------

@bp.route('/<int:template_id>/items/new', methods=['GET', 'POST'])
@login_required
@supervisor_required
def create_checklist_item(template_id):
    template = InspectionTemplate.query.get_or_404(template_id)
    form = ChecklistItemForm()

    if form.validate_on_submit():
        max_order = db.session.query(db.func.max(ChecklistItem.display_order))\
            .filter_by(template_id=template.id).scalar() or 0

        item = ChecklistItem(
            template_id=template.id,
            category=form.category.data,
            item_description=form.item_description.data,
            scoring_type=form.scoring_type.data,
            weight=form.weight.data,
            requires_photo=form.requires_photo.data,
            display_order=max_order + 1
        )
        db.session.add(item)
        db.session.commit()

        flash('Checklist item added successfully.', 'success')
        return redirect(url_for('templates.edit_template', template_id=template.id))

    return render_template(
        'templates/item_form.html',
        form=form,
        template=template,
        title='Add Checklist Item'
    )


@bp.route('/items/<int:item_id>/edit', methods=['GET', 'POST'])
@login_required
@supervisor_required
def edit_checklist_item(item_id):
    item = ChecklistItem.query.get_or_404(item_id)
    form = ChecklistItemForm(obj=item)

    if form.validate_on_submit():
        item.category = form.category.data
        item.item_description = form.item_description.data
        item.scoring_type = form.scoring_type.data
        item.weight = form.weight.data
        item.requires_photo = form.requires_photo.data
        db.session.commit()
        flash('Checklist item updated successfully.', 'success')
        return redirect(url_for('templates.edit_template', template_id=item.template_id))

    return render_template(
        'templates/item_form.html',
        form=form,
        item=item,
        template=item.template,
        title='Edit Checklist Item'
    )


@bp.route('/items/<int:item_id>/delete', methods=['POST'])
@login_required
@supervisor_required
def delete_checklist_item(item_id):
    item = ChecklistItem.query.get_or_404(item_id)
    template_id = item.template_id
    db.session.delete(item)
    db.session.commit()
    flash('Checklist item deleted successfully.', 'success')
    return redirect(url_for('templates.edit_template', template_id=template_id))


@bp.route('/<int:template_id>/items/reorder', methods=['POST'])
@login_required
@supervisor_required
def reorder_items(template_id):
    template = InspectionTemplate.query.get_or_404(template_id)
    item_order = request.json.get('item_order', [])

    for index, item_id in enumerate(item_order):
        item = ChecklistItem.query.get(item_id)
        if item and item.template_id == template.id:
            item.display_order = index

    db.session.commit()
    return jsonify({'success': True})