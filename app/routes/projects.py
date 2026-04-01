"""
app/routes/projects.py
----------------------
Project management routes.

Access matrix:
  - List / view : admin, supervisor, project_manager
  - Create / edit / delete : admin, supervisor
  - Customer assignment management : admin
"""

import logging
from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from app import db
from app.models.project import Project, CustomerAssignment
from app.models.facility import Facility
from app.models.user import User
from app.utils.forms import ProjectForm, CustomerAssignmentForm
from app.utils.decorators import admin_required, supervisor_required, project_manager_required
from app.utils.audit import log_action, ACTION_CREATE, ACTION_UPDATE, ACTION_DELETE

logger = logging.getLogger(__name__)

bp = Blueprint('projects', __name__, url_prefix='/projects')


# ── List ──────────────────────────────────────────────────────────────────────

@bp.route('/')
@login_required
@project_manager_required
def index():
    projects = Project.query.order_by(Project.name).all()
    return render_template('projects/list.html', projects=projects)


# ── Create ────────────────────────────────────────────────────────────────────

@bp.route('/new', methods=['GET', 'POST'])
@login_required
@supervisor_required
def create():
    form = ProjectForm()
    # Populate project_manager choices: users with role project_manager
    pm_users = User.query.filter_by(role='project_manager', active=True).order_by(User.username).all()
    form.project_manager_id.choices = [(0, '— None —')] + [(u.id, u.username) for u in pm_users]

    if form.validate_on_submit():
        pm_id = form.project_manager_id.data or None
        project = Project(
            name=form.name.data,
            description=form.description.data,
            project_manager_id=pm_id if pm_id else None,
            active=form.active.data,
        )
        db.session.add(project)
        db.session.commit()
        logger.info('PROJECTS | create | user=%s project_id=%s name=%s',
                    current_user.username, project.id, project.name)
        log_action(ACTION_CREATE, 'Project', project.id, project.name,
                   f'pm_id={pm_id}; active={project.active}')
        flash(f'Project "{project.name}" created successfully.', 'success')
        return redirect(url_for('projects.view', project_id=project.id))

    return render_template('projects/form.html', form=form, title='Create Project')


# ── View ──────────────────────────────────────────────────────────────────────

@bp.route('/<int:project_id>')
@login_required
@project_manager_required
def view(project_id):
    project = Project.query.get_or_404(project_id)
    facilities = project.facilities.order_by(Facility.name).all()
    assignments = (
        CustomerAssignment.query
        .filter_by(project_id=project_id)
        .join(User, CustomerAssignment.user_id == User.id)
        .order_by(User.username)
        .all()
    )
    return render_template(
        'projects/view.html',
        project=project,
        facilities=facilities,
        assignments=assignments,
    )


# ── Edit ──────────────────────────────────────────────────────────────────────

@bp.route('/<int:project_id>/edit', methods=['GET', 'POST'])
@login_required
@supervisor_required
def edit(project_id):
    project = Project.query.get_or_404(project_id)
    form = ProjectForm(obj=project)
    pm_users = User.query.filter_by(role='project_manager', active=True).order_by(User.username).all()
    form.project_manager_id.choices = [(0, '— None —')] + [(u.id, u.username) for u in pm_users]

    if form.validate_on_submit():
        pm_id = form.project_manager_id.data or None
        project.name = form.name.data
        project.description = form.description.data
        project.project_manager_id = pm_id if pm_id else None
        project.active = form.active.data
        db.session.commit()
        logger.info('PROJECTS | edit | user=%s project_id=%s name=%s',
                    current_user.username, project.id, project.name)
        log_action(ACTION_UPDATE, 'Project', project.id, project.name,
                   f'pm_id={project.project_manager_id}; active={project.active}')
        flash(f'Project "{project.name}" updated successfully.', 'success')
        return redirect(url_for('projects.view', project_id=project.id))

    return render_template('projects/form.html', form=form, project=project, title='Edit Project')


# ── Delete ────────────────────────────────────────────────────────────────────

@bp.route('/<int:project_id>/delete', methods=['POST'])
@login_required
@admin_required
def delete(project_id):
    project = Project.query.get_or_404(project_id)

    if project.facilities.count() > 0:
        flash(f'Cannot delete "{project.name}" — it has linked facilities. '
              'Reassign or remove those facilities first.', 'danger')
        return redirect(url_for('projects.view', project_id=project_id))

    project_name = project.name
    project_id_snap = project.id
    db.session.delete(project)
    db.session.commit()
    logger.info('PROJECTS | delete | user=%s project_id=%s name=%s',
                current_user.username, project_id_snap, project_name)
    log_action(ACTION_DELETE, 'Project', project_id_snap, project_name)
    flash(f'Project "{project_name}" deleted successfully.', 'success')
    return redirect(url_for('projects.index'))


# ── Customer Assignment — Add ─────────────────────────────────────────────────

@bp.route('/<int:project_id>/assignments/add', methods=['GET', 'POST'])
@login_required
@admin_required
def add_assignment(project_id):
    project = Project.query.get_or_404(project_id)
    form = CustomerAssignmentForm()

    # Customer users only
    customers = User.query.filter_by(role='customer', active=True).order_by(User.username).all()
    form.user_id.choices = [(u.id, f'{u.username} ({u.email})') for u in customers]

    # Facilities belonging to this project
    project_facilities = project.facilities.order_by(Facility.name).all()
    form.facility_id.choices = [(0, '— All facilities in project —')] + \
                               [(f.id, f.name) for f in project_facilities]

    if form.validate_on_submit():
        facility_id = form.facility_id.data if form.facility_id.data else None

        # Guard against duplicate assignments
        existing = CustomerAssignment.query.filter_by(
            user_id=form.user_id.data,
            project_id=project_id,
            facility_id=facility_id,
        ).first()

        if existing:
            flash('This customer assignment already exists.', 'warning')
            return redirect(url_for('projects.view', project_id=project_id))

        assignment = CustomerAssignment(
            user_id=form.user_id.data,
            project_id=project_id,
            facility_id=facility_id,
        )
        db.session.add(assignment)
        db.session.commit()

        user = db.session.get(User, form.user_id.data)
        scope_label = f'facility_id={facility_id}' if facility_id else 'all facilities'
        logger.info('PROJECTS | assignment_add | admin=%s customer=%s project_id=%s scope=%s',
                    current_user.username, user.username, project_id, scope_label)
        log_action(ACTION_CREATE, 'CustomerAssignment', assignment.id,
                   f'{user.username} → {project.name}',
                   f'scope={scope_label}')
        flash(f'Customer "{user.username}" assigned to project "{project.name}".', 'success')
        return redirect(url_for('projects.view', project_id=project_id))

    return render_template(
        'projects/assignment_form.html',
        form=form,
        project=project,
        title='Add Customer Assignment',
    )


# ── Customer Assignment — Remove ──────────────────────────────────────────────

@bp.route('/assignments/<int:assignment_id>/remove', methods=['POST'])
@login_required
@admin_required
def remove_assignment(assignment_id):
    assignment = CustomerAssignment.query.get_or_404(assignment_id)
    project_id = assignment.project_id
    project = Project.query.get_or_404(project_id)
    user = db.session.get(User, assignment.user_id)

    username = user.username if user else f'user_id={assignment.user_id}'
    assignment_id_snap = assignment.id

    db.session.delete(assignment)
    db.session.commit()

    logger.info('PROJECTS | assignment_remove | admin=%s customer=%s project_id=%s',
                current_user.username, username, project_id)
    log_action(ACTION_DELETE, 'CustomerAssignment', assignment_id_snap,
               f'{username} → {project.name}')
    flash(f'Assignment for "{username}" removed.', 'success')
    return redirect(url_for('projects.view', project_id=project_id))
