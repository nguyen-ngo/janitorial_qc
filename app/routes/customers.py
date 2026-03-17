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

    customer_ids = [c.id for c in customers]

    # ── Single bulk query for all assignments ─────────────────────────────
    # Replaces per-customer CustomerAssignment.query.filter_by(user_id=...) loop
    all_assignments = (
        CustomerAssignment.query
        .filter(CustomerAssignment.user_id.in_(customer_ids))
        .all()
    ) if customer_ids else []

    assignment_map = {c.id: [] for c in customers}
    for a in all_assignments:
        assignment_map[a.user_id].append(a)

    # ── Single bulk query for all active facilities in assigned projects ──
    # Resolves facility scope for every customer without repeated DB round-trips.
    from collections import defaultdict
    assigned_project_ids = {a.project_id for a in all_assignments}

    project_facilities_map = defaultdict(list)  # project_id → [facility_id, ...]
    if assigned_project_ids:
        proj_facs = (
            Facility.query
            .filter(
                Facility.project_id.in_(assigned_project_ids),
                Facility.active == True,
            )
            .all()
        )
        for f in proj_facs:
            project_facilities_map[f.project_id].append(f.id)

    scope_map = {}   # user_id → sorted list[int] facility IDs
    for customer in customers:
        ids = set()
        for a in assignment_map[customer.id]:
            if a.facility_id:
                ids.add(a.facility_id)
            else:
                ids.update(project_facilities_map.get(a.project_id, []))
        scope_map[customer.id] = sorted(ids)

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


# ── CSV template download ─────────────────────────────────────────────────────

@bp.route('/import/template')
@login_required
@admin_required
def import_template():
    """Download a blank CSV template showing the expected import format."""
    import csv, io
    from flask import Response

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        'username', 'email', 'password',
        'project_name', 'facility_name',
    ])
    writer.writerow([
        'jane.smith', 'jane@acme.com', 'SecurePass1!',
        'Acme Contract', 'Downtown Office',
    ])
    writer.writerow([
        'bob.jones', 'bob@acme.com', 'SecurePass2!',
        'Acme Contract', '',
    ])
    buf.seek(0)
    return Response(
        buf.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename="customer_import_template.csv"'},
    )


# ── Bulk import (upload → preview → confirm) ──────────────────────────────────

@bp.route('/import', methods=['GET', 'POST'])
@login_required
@admin_required
def bulk_import():
    """Two-phase CSV import for customer accounts.

    Phase 1 (GET / POST with file):
        Parse and validate the CSV, return a preview of what will be created.
        No database writes occur here.

    Phase 2 (POST with confirmed=1):
        Write all validated rows to the database.

    CSV columns
    -----------
    username      : required — must be unique across users
    email         : required — must be unique across users
    password      : required — min 8 characters
    project_name  : optional — must match an existing active Project name exactly
    facility_name : optional — if given, must match an active Facility within the project

    One row = one user.  A user may have at most one assignment per import row;
    import the same username on multiple rows to assign them to multiple projects.
    Duplicate username rows after the first are treated as additional assignments.
    """
    import csv, io
    from flask import session as _session

    projects   = Project.query.filter_by(active=True).order_by(Project.name).all()
    proj_by_name = {p.name.strip().lower(): p for p in projects}

    # ── Phase 2: commit confirmed rows ────────────────────────────────────
    if request.method == 'POST' and request.form.get('confirmed') == '1':
        import json
        rows_json = request.form.get('rows_json', '[]')
        try:
            rows = json.loads(rows_json)
        except Exception:
            flash('Import session expired. Please re-upload the file.', 'danger')
            return redirect(url_for('customers.bulk_import'))

        created_users  = 0
        created_assign = 0
        skipped        = 0

        # Track users created in this batch (username → User) so duplicate
        # rows for the same username add assignments rather than re-creating.
        batch_users = {}

        for row in rows:
            uname   = row['username']
            email   = row['email']
            pw      = row['password']
            proj_id = row.get('project_id')
            fac_id  = row.get('facility_id')

            # Get or create user
            user = (
                batch_users.get(uname)
                or User.query.filter_by(username=uname).first()
            )

            if user is None:
                user = User(
                    username = uname,
                    email    = email,
                    role     = 'customer',
                    active   = True,
                )
                user.set_password(pw)
                db.session.add(user)
                db.session.flush()   # populate user.id before assignment
                batch_users[uname] = user
                created_users += 1
                log_action(ACTION_CREATE, 'User', user.id, user.username,
                           f'role=customer; email={email}; source=bulk_import')
                logger.info('BULK IMPORT | user_created | username=%s email=%s by=%s',
                            uname, email, current_user.username)

            # Create assignment if a project was specified
            if proj_id:
                existing = CustomerAssignment.query.filter_by(
                    user_id     = user.id,
                    project_id  = proj_id,
                    facility_id = fac_id or None,
                ).first()
                if not existing:
                    assign = CustomerAssignment(
                        user_id     = user.id,
                        project_id  = proj_id,
                        facility_id = fac_id or None,
                    )
                    db.session.add(assign)
                    db.session.flush()   # populate assign.id before audit log
                    created_assign += 1
                    log_action(ACTION_CREATE, 'CustomerAssignment', assign.id,
                               f'{uname} → project_id={proj_id}',
                               f'facility_id={fac_id}; source=bulk_import')
                else:
                    skipped += 1

        db.session.commit()
        logger.info(
            'BULK IMPORT COMMITTED | by=%s | users=%s | assignments=%s | skipped=%s',
            current_user.username, created_users, created_assign, skipped,
        )
        flash(
            f'Import complete: {created_users} user(s) created, '
            f'{created_assign} assignment(s) added'
            + (f', {skipped} duplicate assignment(s) skipped.' if skipped else '.'),
            'success',
        )
        return redirect(url_for('customers.index'))

    # ── Phase 1: parse and validate ───────────────────────────────────────
    preview_rows   = []
    errors         = []
    raw_valid_rows = []   # serialisable dicts passed to phase 2 via hidden field

    if request.method == 'POST':
        file = request.files.get('csv_file')

        if not file or not file.filename:
            flash('Please select a CSV file to upload.', 'warning')
            return render_template('customers/import.html', projects=projects)

        if not file.filename.lower().endswith('.csv'):
            flash('Only .csv files are accepted.', 'danger')
            return render_template('customers/import.html', projects=projects)

        try:
            stream  = io.StringIO(file.stream.read().decode('utf-8-sig'))
            reader  = csv.DictReader(stream)
            raw_rows = list(reader)
        except Exception as exc:
            flash(f'Could not parse file: {exc}', 'danger')
            return render_template('customers/import.html', projects=projects)

        required_cols = {'username', 'email', 'password'}
        if not required_cols.issubset(set(reader.fieldnames or [])):
            flash(
                f'CSV is missing required columns: {required_cols - set(reader.fieldnames or [])}. '
                'Download the template to see the expected format.',
                'danger',
            )
            return render_template('customers/import.html', projects=projects)

        # Track usernames seen in this file to catch intra-file duplicates
        seen_usernames = {}   # username → first row index (1-based)
        seen_emails    = {}

        for i, raw in enumerate(raw_rows, start=2):   # row 1 = header
            row_errors = []

            uname = (raw.get('username') or '').strip()
            email = (raw.get('email') or '').strip()
            pw    = (raw.get('password') or '').strip()
            pname = (raw.get('project_name') or '').strip()
            fname = (raw.get('facility_name') or '').strip()

            if not uname:
                row_errors.append('username is required')
            if not email:
                row_errors.append('email is required')
            if not pw:
                row_errors.append('password is required')
            elif len(pw) < 8:
                row_errors.append('password must be at least 8 characters')

            # Duplicate username within file (first occurrence creates the user;
            # subsequent occurrences add assignments — that's intentional)
            if uname:
                if uname in seen_usernames:
                    # Allowed only if it's an additional assignment row
                    pass
                else:
                    seen_usernames[uname] = i
                    # Check DB uniqueness only for new usernames
                    if User.query.filter_by(username=uname).first():
                        row_errors.append(f'username "{uname}" already exists in the system')

            if email:
                if email in seen_emails:
                    row_errors.append(f'email "{email}" appears more than once in this file')
                else:
                    seen_emails[email] = i
                    if User.query.filter_by(email=email).first():
                        row_errors.append(f'email "{email}" already exists in the system')

            # Resolve project
            project  = None
            facility = None
            proj_id  = None
            fac_id   = None

            if pname:
                project = proj_by_name.get(pname.lower())
                if project is None:
                    row_errors.append(f'project "{pname}" not found or inactive')
                else:
                    proj_id = project.id
                    if fname:
                        from app.models.facility import Facility
                        facility = Facility.query.filter(
                            Facility.project_id == project.id,
                            Facility.active     == True,
                            db.func.lower(Facility.name) == fname.lower(),
                        ).first()
                        if facility is None:
                            row_errors.append(
                                f'facility "{fname}" not found in project "{pname}"'
                            )
                        else:
                            fac_id = facility.id
            elif fname:
                row_errors.append('facility_name requires project_name to also be set')

            status = 'error' if row_errors else 'ok'
            preview_rows.append({
                'row':      i,
                'username': uname,
                'email':    email,
                'project':  project.name if project else '—',
                'facility': facility.name if facility else ('All' if project else '—'),
                'status':   status,
                'errors':   row_errors,
            })

            if not row_errors:
                raw_valid_rows.append({
                    'username':    uname,
                    'email':       email,
                    'password':    pw,
                    'project_id':  proj_id,
                    'facility_id': fac_id,
                })
            else:
                errors.extend(row_errors)

    import json
    return render_template(
        'customers/import.html',
        projects       = projects,
        preview_rows   = preview_rows,
        has_errors     = bool(errors),
        valid_count    = len(raw_valid_rows),
        rows_json      = json.dumps(raw_valid_rows),
    )

@bp.route('/facilities-for-project/<int:project_id>')
@login_required
@admin_required
def facilities_for_project(project_id):
    from flask import jsonify
    project    = Project.query.get_or_404(project_id)
    facilities = project.facilities.filter_by(active=True).order_by(Facility.name).all()
    return jsonify([{'id': f.id, 'name': f.name} for f in facilities])