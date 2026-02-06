from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify
from flask_login import login_required, current_user
from app import db
from app.models.inspection import InspectionTemplate, ChecklistItem
from app.utils.forms import InspectionTemplateForm, ChecklistItemForm
from app.utils.decorators import supervisor_required

bp = Blueprint('templates', __name__, url_prefix='/templates')

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
        
        flash(f'Template "{template.name}" created successfully.', 'success')
        return redirect(url_for('templates.edit_template', template_id=template.id))
    
    return render_template('templates/form.html', form=form, title='Create Inspection Template')

@bp.route('/<int:template_id>')
@login_required
def view_template(template_id):
    template = InspectionTemplate.query.get_or_404(template_id)
    checklist_items = template.checklist_items.order_by(ChecklistItem.display_order, ChecklistItem.category).all()
    
    # Group items by category
    items_by_category = {}
    for item in checklist_items:
        category = item.category or 'General'
        if category not in items_by_category:
            items_by_category[category] = []
        items_by_category[category].append(item)
    
    return render_template('templates/view.html', template=template, items_by_category=items_by_category)

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
        flash(f'Template "{template.name}" updated successfully.', 'success')
        return redirect(url_for('templates.view_template', template_id=template.id))
    
    checklist_items = template.checklist_items.order_by(ChecklistItem.display_order, ChecklistItem.category).all()
    
    return render_template('templates/edit.html', form=form, template=template, checklist_items=checklist_items)

@bp.route('/<int:template_id>/delete', methods=['POST'])
@login_required
@supervisor_required
def delete_template(template_id):
    template = InspectionTemplate.query.get_or_404(template_id)
    
    # Check if template has inspections
    if template.inspections.count() > 0:
        flash('Cannot delete template with existing inspections.', 'danger')
        return redirect(url_for('templates.view_template', template_id=template.id))
    
    template_name = template.name
    db.session.delete(template)
    db.session.commit()
    
    flash(f'Template "{template_name}" deleted successfully.', 'success')
    return redirect(url_for('templates.index'))

# Checklist Item Management

@bp.route('/<int:template_id>/items/new', methods=['GET', 'POST'])
@login_required
@supervisor_required
def create_checklist_item(template_id):
    template = InspectionTemplate.query.get_or_404(template_id)
    form = ChecklistItemForm()
    
    if form.validate_on_submit():
        # Get the highest display order
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
    
    return render_template('templates/item_form.html', form=form, template=template, title='Add Checklist Item')

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
    
    return render_template('templates/item_form.html', form=form, item=item, template=item.template, title='Edit Checklist Item')

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