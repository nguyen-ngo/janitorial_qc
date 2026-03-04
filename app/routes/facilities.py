from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from app import db
from app.models.facility import Facility, Area
from app.models.project import Project
from app.utils.forms import FacilityForm, AreaForm
from app.utils.decorators import supervisor_required, admin_required
from app.utils.audit import log_action, ACTION_CREATE, ACTION_UPDATE, ACTION_DELETE

bp = Blueprint('facilities', __name__, url_prefix='/facilities')

@bp.route('/')
@login_required
def list_facilities():
    facilities = Facility.query.order_by(Facility.name).all()
    return render_template('facilities/list.html', facilities=facilities)

@bp.route('/new', methods=['GET', 'POST'])
@login_required
@supervisor_required
def create_facility():
    form = FacilityForm()
    projects = Project.query.filter_by(active=True).order_by(Project.name).all()
    form.project_id.choices = [(0, '— None —')] + [(p.id, p.name) for p in projects]

    if form.validate_on_submit():
        project_id = form.project_id.data if form.project_id.data else None
        facility = Facility(
            name=form.name.data,
            address=form.address.data,
            contact_person=form.contact_person.data,
            contact_phone=form.contact_phone.data,
            project_id=project_id if project_id else None,
            active=form.active.data
        )

        db.session.add(facility)
        db.session.commit()
        log_action(ACTION_CREATE, 'Facility', facility.id, facility.name,
                   f'contact={facility.contact_person or ""}; project_id={facility.project_id}; active={facility.active}')
        flash(f'Facility "{facility.name}" created successfully.', 'success')
        return redirect(url_for('facilities.view_facility', facility_id=facility.id))

    return render_template('facilities/form.html', form=form, title='Create Facility')

@bp.route('/<int:facility_id>')
@login_required
def view_facility(facility_id):
    facility = Facility.query.get_or_404(facility_id)
    areas = facility.areas.order_by(Area.name).all()
    return render_template('facilities/view.html', facility=facility, areas=areas)

@bp.route('/<int:facility_id>/edit', methods=['GET', 'POST'])
@login_required
@supervisor_required
def edit_facility(facility_id):
    facility = Facility.query.get_or_404(facility_id)
    form = FacilityForm(obj=facility)
    projects = Project.query.filter_by(active=True).order_by(Project.name).all()
    form.project_id.choices = [(0, '— None —')] + [(p.id, p.name) for p in projects]

    if form.validate_on_submit():
        project_id = form.project_id.data if form.project_id.data else None
        facility.name = form.name.data
        facility.address = form.address.data
        facility.contact_person = form.contact_person.data
        facility.contact_phone = form.contact_phone.data
        facility.project_id = project_id if project_id else None
        facility.active = form.active.data

        db.session.commit()
        log_action(ACTION_UPDATE, 'Facility', facility.id, facility.name,
                   f'project_id={facility.project_id}; active={facility.active}')
        flash(f'Facility "{facility.name}" updated successfully.', 'success')
        return redirect(url_for('facilities.view_facility', facility_id=facility.id))

    return render_template('facilities/form.html', form=form, facility=facility, title='Edit Facility')

@bp.route('/<int:facility_id>/delete', methods=['POST'])
@login_required
@admin_required
def delete_facility(facility_id):
    facility = Facility.query.get_or_404(facility_id)
    
    if facility.inspections.count() > 0:
        flash(f'Cannot delete "{facility.name}" — it has existing inspection records.', 'danger')
        return redirect(url_for('facilities.view_facility', facility_id=facility.id))
    
    facility_name = facility.name
    facility_id_snap = facility.id
    db.session.delete(facility)
    db.session.commit()
    log_action(ACTION_DELETE, 'Facility', facility_id_snap, facility_name)
    flash(f'Facility "{facility_name}" has been permanently deleted.', 'success')
    return redirect(url_for('facilities.list_facilities'))

# Area Management Routes

@bp.route('/<int:facility_id>/areas/new', methods=['GET', 'POST'])
@login_required
@supervisor_required
def create_area(facility_id):
    facility = Facility.query.get_or_404(facility_id)
    form = AreaForm()
    form.facility_id.choices = [(facility.id, facility.name)]
    form.facility_id.data = facility.id
    
    if form.validate_on_submit():
        area = Area(
            name=form.name.data,
            area_type=form.area_type.data,
            facility_id=facility.id
        )
        
        db.session.add(area)
        db.session.commit()
        log_action(ACTION_CREATE, 'Area', area.id, area.name,
                   f'facility={facility.name}; type={area.area_type or ""}')
        flash(f'Area "{area.name}" created successfully.', 'success')
        return redirect(url_for('facilities.view_facility', facility_id=facility.id))
    
    return render_template('facilities/area_form.html', form=form, facility=facility, title='Create Area')

@bp.route('/areas/<int:area_id>/edit', methods=['GET', 'POST'])
@login_required
@supervisor_required
def edit_area(area_id):
    area = Area.query.get_or_404(area_id)
    form = AreaForm(obj=area)
    
    facilities = Facility.query.filter_by(active=True).order_by(Facility.name).all()
    form.facility_id.choices = [(f.id, f.name) for f in facilities]
    
    if form.validate_on_submit():
        area.name = form.name.data
        area.area_type = form.area_type.data
        area.facility_id = form.facility_id.data
        
        db.session.commit()
        log_action(ACTION_UPDATE, 'Area', area.id, area.name,
                   f'facility_id={area.facility_id}; type={area.area_type or ""}')
        flash(f'Area "{area.name}" updated successfully.', 'success')
        return redirect(url_for('facilities.view_facility', facility_id=area.facility_id))
    
    return render_template('facilities/area_form.html', form=form, area=area, facility=area.facility, title='Edit Area')

@bp.route('/areas/<int:area_id>/delete', methods=['POST'])
@login_required
@supervisor_required
def delete_area(area_id):
    area = Area.query.get_or_404(area_id)
    facility_id = area.facility_id
    
    if area.inspections.count() > 0:
        flash('Cannot delete area with existing inspections.', 'danger')
        return redirect(url_for('facilities.view_facility', facility_id=facility_id))
    
    area_name = area.name
    area_id_snap = area.id
    db.session.delete(area)
    db.session.commit()
    log_action(ACTION_DELETE, 'Area', area_id_snap, area_name)
    flash(f'Area "{area_name}" deleted successfully.', 'success')
    return redirect(url_for('facilities.view_facility', facility_id=facility_id))