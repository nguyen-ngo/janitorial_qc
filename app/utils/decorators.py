from functools import wraps
from flask import flash, redirect, url_for
from flask_login import current_user

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != 'admin':
            flash('Administrator access required.', 'danger')
            return redirect(url_for('dashboard.index'))
        return f(*args, **kwargs)
    return decorated_function

def supervisor_required(f):
    """Grants access to admin and director roles.

    The decorator is intentionally kept as 'supervisor_required' so that all
    existing route decorators (@supervisor_required) continue to work without
    any changes to the route files.  The access list now reflects the renamed
    Director role instead of the retired Supervisor role.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role not in ['admin', 'director']:
            flash('Director access required.', 'danger')
            return redirect(url_for('dashboard.index'))
        return f(*args, **kwargs)
    return decorated_function

def project_manager_required(f):
    """Grants access to admin, director, and project_manager roles."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role not in [
            'admin', 'director', 'project_manager'
        ]:
            flash('Project Manager access required.', 'danger')
            return redirect(url_for('dashboard.index'))
        return f(*args, **kwargs)
    return decorated_function

def customer_required(f):
    """Restricts access to customer-role users only.

    Internal staff (admin, director, inspector, project_manager) should
    never be routed through customer-scoped views — use their own routes.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != 'customer':
            flash('Customer portal access required.', 'danger')
            return redirect(url_for('dashboard.index'))
        return f(*args, **kwargs)
    return decorated_function
