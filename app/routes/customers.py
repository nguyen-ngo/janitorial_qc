"""
app/routes/customers.py
-----------------------
Customer Management — admin-only consolidated view.

Provides a single screen to:
  - List all customer-role users with their assignment summary
  - Create a new customer account
  - Edit an existing customer (username / email / password / active)
  - Manage assignments for a customer (add / remove)
  - Quick-disable / enable a customer account
  - View a customer's scoped facility access at a glance
"""

import logging
from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from app import db
from app.models.user import User
from app.models.project import Project, CustomerAssignment
from app.models.facility import Facility
from app.utils.forms import CustomerUserForm, CustomerAssignmentForm
from app.utils.decorators import admin_required
from app.utils.audit import log_action, ACTION_CREATE, ACTION_UPDATE, ACTION_DELETE
from app.utils.scope import get_customer_scope

logger = logging.getLogger(__name__)

bp = Blueprint('customers', __name__, url_prefix='/customers')


# ── List ──────────────────────────────────────────────────────────────────────

@bp.route('/')
@login_required
@admin_required
def index():
    """Consolidated customer management dashboard."""
    customers = (
        User.query
        .filter_by(role='customer')
        .order_by(User.username)
        .all()
    )

    # Pre-compute assignment summary per customer to avoid N+1 in template
    assignment_map = {}   # user_id → list[CustomerAssignment]
    scope_map      = {}   # user_id → list[int] facility IDs

    for customer in customers:
        assignments = CustomerAssignment.query.filter_by(user_id=customer.id).all()
        assignment_map[customer.id] = assignments
        scope_map[customer.id]      = get_customer_scope(customer) or []

    # All active projects for the assignment modal
    projects = Project.query.filter_by(active=True).order_by(Project.name).all()

    return render_template(
        'customers/index.html',
        customers      = customers,
        assignment_map = assignment_map,
        scope_map      = scope_map,
        projects       = projects,
    )


# ── Create customer ───────────────────────────────────────────────────────────

@bp.route('/new', methods=['GET', 'POST'])
@login_required
@admin_required
def create():
    form = CustomerUserForm()

    if form.validate_on_submit():
        user = User(
            username = form.username.data,
            email    = form.email.data,
            role     = 'customer',
            active   = True,
        )
        user.set_password(form.password.data)
        db.session.add(user)
        db.session.commit()
        logger.info('CUSTOMERS | create | admin=%s new_customer=%s email=%s',
                    current_user.username, user.username, user.email)
        log_action(ACTION_CREATE, 'User', user.id, user.username,
                   f'role=customer; email={user.email}; created_via=customer_mgmt')
        flash(f'Customer account "{user.username}" created successfully.', 'success')
        return redirect(url_for('customers.manage', customer_id=user.id))

    return render_template('customers/form.html', form=form, title='Create Customer Account')


# ── Edit customer ─────────────────────────────────────────────────────────────

@bp.route('/<int:customer_id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def edit(customer_id):
    customer = User.query.get_or_404(customer_id)
    if customer.role != 'customer':
        flash('This page is only for customer accounts.', 'warning')
        return redirect(url_for('customers.index'))

    form = CustomerUserForm(user=customer, obj=customer)

    if form.validate_on_submit():
        customer.username = form.username.data
        customer.email    = form.email.data
        if form.password.data:
            customer.set_password(form.password.data)
        db.session.commit()
        logger.info('CUSTOMERS | edit | admin=%s customer_id=%s username=%s',
                    current_user.username, customer.id, customer.username)
        log_action(ACTION_UPDATE, 'User', customer.id, customer.username,
                   f'email={customer.email}; updated_via=customer_mgmt')
        flash(f'Customer "{customer.username}" updated successfully.', 'success')
        return redirect(url_for('customers.manage', customer_id=customer.id))

    return render_template('customers/form.html', form=form, customer=customer,
                           title='Edit Customer Account')


# ── Customer detail / assignment management ───────────────────────────────────

@bp.route('/<int:customer_id>')
@login_required
@admin_required
def manage(customer_id):
    """Single-customer detail page: profile + all assignments."""
    customer = User.query.get_or_404(customer_id)
    if customer.role != 'customer':
        flash('This page is only for customer accounts.', 'warning')
        return redirect(url_for('customers.index'))

    assignments  = CustomerAssignment.query.filter_by(user_id=customer_id).all()
    facility_ids = get_customer_scope(customer) or []
    facilities   = (
        Facility.query
        .filter(Facility.id.in_(facility_ids), Facility.active == True)
        .order_by(Facility.name)
        .all()
    ) if facility_ids else []

    # Assignment form (populated here so it can be rendered inline)
    aform    = CustomerAssignmentForm()
    projects = Project.query.filter_by(active=True).order_by(Project.name).all()
    aform.user_id.choices     = [(customer.id, customer.username)]
    aform.facility_id.choices = [(0, '— All facilities in project —')]

    return render_template(
        'customers/manage.html',
        customer    = customer,
        assignments = assignments,
        facilities  = facilities,
        aform       = aform,
        projects    = projects,
    )


# ── Add assignment (from customer detail page) ────────────────────────────────

@bp.route('/<int:customer_id>/assignments/add', methods=['POST'])
@login_required
@admin_required
def add_assignment(customer_id):
    customer = User.query.get_or_404(customer_id)
    if customer.role != 'customer':
        flash('Assignments are only for customer accounts.', 'warning')
        return redirect(url_for('customers.index'))

    project_id  = request.form.get('project_id', type=int)
    facility_id = request.form.get('facility_id', type=int) or None

    if not project_id:
        flash('Please select a project.', 'warning')
        return redirect(url_for('customers.manage', customer_id=customer_id))

    project = Project.query.get_or_404(project_id)

    # Guard: duplicate assignment
    existing = CustomerAssignment.query.filter_by(
        user_id     = customer_id,
        project_id  = project_id,
        facility_id = facility_id,
    ).first()

    if existing:
        flash('That assignment already exists.', 'warning')
        return redirect(url_for('customers.manage', customer_id=customer_id))

    assignment = CustomerAssignment(
        user_id     = customer_id,
        project_id  = project_id,
        facility_id = facility_id,
    )
    db.session.add(assignment)
    db.session.commit()

    scope_label = f'facility_id={facility_id}' if facility_id else 'all facilities'
    logger.info('CUSTOMERS | assignment_add | admin=%s customer=%s project_id=%s scope=%s',
                current_user.username, customer.username, project_id, scope_label)
    log_action(ACTION_CREATE, 'CustomerAssignment', assignment.id,
               f'{customer.username} → {project.name}',
               f'scope={scope_label}')
    flash(f'Assignment added: "{customer.username}" → "{project.name}".', 'success')
    return redirect(url_for('customers.manage', customer_id=customer_id))


# ── Remove assignment ─────────────────────────────────────────────────────────

@bp.route('/assignments/<int:assignment_id>/remove', methods=['POST'])
@login_required
@admin_required
def remove_assignment(assignment_id):
    assignment  = CustomerAssignment.query.get_or_404(assignment_id)
    customer_id = assignment.user_id
    customer    = User.query.get(customer_id)
    project     = Project.query.get(assignment.project_id)

    username     = customer.username if customer else f'user_id={customer_id}'
    project_name = project.name if project else f'project_id={assignment.project_id}'
    snap_id      = assignment.id

    db.session.delete(assignment)
    db.session.commit()
    logger.info('CUSTOMERS | assignment_remove | admin=%s customer=%s project=%s',
                current_user.username, username, project_name)
    log_action(ACTION_DELETE, 'CustomerAssignment', snap_id,
               f'{username} → {project_name}')
    flash(f'Assignment removed for "{username}".', 'success')
    return redirect(url_for('customers.manage', customer_id=customer_id))


# ── Toggle active ─────────────────────────────────────────────────────────────

@bp.route('/<int:customer_id>/toggle-active', methods=['POST'])
@login_required
@admin_required
def toggle_active(customer_id):
    customer = User.query.get_or_404(customer_id)
    if customer.role != 'customer':
        flash('This action is only for customer accounts.', 'warning')
        return redirect(url_for('customers.index'))

    customer.active = not customer.active
    db.session.commit()

    label = 'enabled' if customer.active else 'disabled'
    logger.info('CUSTOMERS | toggle_active | admin=%s customer=%s action=%s',
                current_user.username, customer.username, label)
    log_action(ACTION_UPDATE, 'User', customer.id, customer.username,
               f'account {label} via customer_mgmt by {current_user.username}')
    flash(f'Customer "{customer.username}" has been {label}.', 'success')
    return redirect(request.referrer or url_for('customers.index'))


# ── AJAX: facilities for a project (used by add-assignment form) ──────────────

@bp.route('/facilities-for-project/<int:project_id>')
@login_required
@admin_required
def facilities_for_project(project_id):
    from flask import jsonify
    project    = Project.query.get_or_404(project_id)
    facilities = project.facilities.filter_by(active=True).order_by(Facility.name).all()
    return jsonify([{'id': f.id, 'name': f.name} for f in facilities])
