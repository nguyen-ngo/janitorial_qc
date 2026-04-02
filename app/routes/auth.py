from flask import Blueprint, render_template, redirect, url_for, flash, request, abort
from flask_login import login_user, logout_user, login_required, current_user
from urllib.parse import urlparse
from app import db
from app.models.user import User
from app.utils.forms import LoginForm, UserForm, ProfileForm
from app.utils.decorators import admin_required
import logging
from app.utils.audit import log_action, ACTION_CREATE, ACTION_UPDATE, ACTION_DELETE, ACTION_LOGIN, ACTION_LOGOUT

logger = logging.getLogger(__name__)

bp = Blueprint('auth', __name__, url_prefix='/auth')


def _safe_next(next_url: str | None) -> str:
    """
    Validate that the redirect target is a relative URL on this host.
    Returns the safe URL, or the dashboard index if the URL is external/invalid.
    This prevents open-redirect attacks where an attacker crafts a login link
    containing next=https://evil.com to hijack post-login redirects.
    """
    if not next_url:
        return url_for('dashboard.index')
    parsed = urlparse(next_url)
    # Reject any URL that specifies a network location (external host) or scheme
    if parsed.netloc or parsed.scheme:
        return url_for('dashboard.index')
    return next_url


@bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard.index'))

    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data).first()

        if user and user.check_password(form.password.data):
            if not user.active:
                flash('Your account has been disabled. Please contact an administrator.', 'danger')
                return render_template('auth/login.html', form=form)
            login_user(user, remember=form.remember_me.data)
            # Use validated next URL — never redirect blindly to request.args['next']
            next_page = _safe_next(request.args.get('next'))
            log_action(ACTION_LOGIN, 'User', user.id, user.username)
            flash(f'Welcome back, {user.username}!', 'success')
            return redirect(next_page)
        else:
            # Generic message — don't reveal whether the username exists
            flash('Invalid credentials. Please try again.', 'danger')

    return render_template('auth/login.html', form=form)


@bp.route('/logout')
@login_required
def logout():
    log_action(ACTION_LOGOUT, 'User', current_user.id, current_user.username)
    logout_user()
    flash('Successfully logged out.', 'success')
    return redirect(url_for('auth.login'))


@bp.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    """User profile page — view stats and update email/password."""
    from app.models.inspection import Inspection
    from app.models.issue import Issue

    form = ProfileForm(user=current_user, obj=current_user)

    if form.validate_on_submit():
        current_user.email = form.email.data

        if form.new_password.data:
            current_user.set_password(form.new_password.data)
            logger.info('AUTH | profile_password_change | user_id=%s username=%s',
                        current_user.id, current_user.username)

        db.session.commit()
        logger.info('AUTH | profile_update | user_id=%s username=%s email=%s',
                    current_user.id, current_user.username, current_user.email)
        log_action(ACTION_UPDATE, 'User', current_user.id, current_user.username,
                   'self-service profile update')
        flash('Profile updated successfully.', 'success')
        return redirect(url_for('auth.profile'))

    # ── Activity stats ────────────────────────────────────────────────────
    total_inspections = Inspection.query.filter_by(inspector_id=current_user.id).count()
    completed_inspections = Inspection.query.filter_by(
        inspector_id=current_user.id, status='completed'
    ).count()

    recent_inspections = (
        Inspection.query
        .filter_by(inspector_id=current_user.id)
        .order_by(Inspection.inspection_date.desc())
        .limit(5)
        .all()
    )

    open_issues = Issue.query.filter_by(
        assigned_to=current_user.id, status='open'
    ).count() if hasattr(Issue, 'assigned_to') else 0

    return render_template(
        'auth/profile.html',
        form=form,
        total_inspections=total_inspections,
        completed_inspections=completed_inspections,
        recent_inspections=recent_inspections,
        open_issues=open_issues,
    )


@bp.route('/users')
@login_required
@admin_required
def list_users():
    users = User.query.order_by(User.created_at.desc()).all()
    return render_template('auth/users.html', users=users)


@bp.route('/users/new', methods=['GET', 'POST'])
@login_required
@admin_required
def create_user():
    form = UserForm()

    if form.validate_on_submit():
        user = User(
            username=form.username.data,
            email=form.email.data,
            role=form.role.data
        )
        user.set_password(form.password.data)
        db.session.add(user)
        db.session.commit()
        logger.info('AUTH | user_create | admin_id=%s admin=%s new_user=%s role=%s',
                    current_user.id, current_user.username, user.username, user.role)
        log_action(ACTION_CREATE, 'User', user.id, user.username,
                   f'role={user.role}; email={user.email}')
        flash(f'User {user.username} created successfully.', 'success')
        return redirect(url_for('auth.list_users'))

    return render_template('auth/user_form.html', form=form, title='Create User')


@bp.route('/users/<int:user_id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_user(user_id):
    user = User.query.get_or_404(user_id)
    form = UserForm(user=user, obj=user)

    if form.validate_on_submit():
        user.username = form.username.data
        user.email    = form.email.data
        user.role     = form.role.data

        if form.password.data:
            user.set_password(form.password.data)

        db.session.commit()
        logger.info('AUTH | user_edit | admin_id=%s admin=%s target_user_id=%s target_user=%s',
                    current_user.id, current_user.username, user.id, user.username)
        log_action(ACTION_UPDATE, 'User', user.id, user.username,
                   f'role={user.role}; email={user.email}')
        flash(f'User {user.username} updated successfully.', 'success')
        return redirect(url_for('auth.list_users'))

    return render_template('auth/user_form.html', form=form, user=user, title='Edit User')


@bp.route('/users/<int:user_id>/delete', methods=['POST'])
@login_required
@admin_required
def delete_user(user_id):
    user = User.query.get_or_404(user_id)

    if user.id == current_user.id:
        flash('Cannot delete your own account.', 'danger')
        return redirect(url_for('auth.list_users'))

    # Guard: block deletion if user has related records that would orphan data
    # or violate FK constraints (inspections they conducted, issues assigned to them,
    # or templates they created).
    if user.inspections.count() > 0:
        flash(
            f'Cannot delete "{user.username}" — they have existing inspection records. '
            'Deactivate the account instead.',
            'danger'
        )
        return redirect(url_for('auth.list_users'))

    username = user.username
    user_id   = user.id
    db.session.delete(user)
    db.session.commit()
    logger.info('AUTH | user_delete | admin_id=%s admin=%s deleted_user=%s',
                current_user.id, current_user.username, username)
    log_action(ACTION_DELETE, 'User', user_id, username)
    flash(f'User {username} deleted successfully.', 'success')
    return redirect(url_for('auth.list_users'))

@bp.route('/users/<int:user_id>/toggle-active', methods=['POST'])
@login_required
@admin_required
def toggle_active(user_id):
    user = User.query.get_or_404(user_id)

    if user.id == current_user.id:
        flash('You cannot disable your own account.', 'danger')
        return redirect(url_for('auth.list_users'))

    user.active = not user.active
    db.session.commit()

    action_label = 'enabled' if user.active else 'disabled'
    logger.info(
        'AUTH | user_%s | admin_id=%s admin=%s target_user=%s',
        action_label, current_user.id, current_user.username, user.username,
    )
    log_action(
        ACTION_UPDATE, 'User', user.id, user.username,
        f'account {action_label} by {current_user.username}',
    )
    flash(f'User {user.username} has been {action_label}.', 'success')
    return redirect(request.referrer or url_for('auth.list_users'))

# ── Notification Matrix ───────────────────────────────────────────────────────

@bp.route('/notification-matrix', methods=['GET', 'POST'])
@login_required
@admin_required
def notification_matrix():
    """Admin-only notification matrix — controls who receives each event type."""
    import json as _json
    from app.models.notification_matrix import (
        NotificationMatrix, MATRIX_EVENTS, MATRIX_ROLES, MATRIX_DEFAULTS,
    )

    if request.method == 'POST':
        for event_key in MATRIX_EVENTS:
            for role_key, _ in MATRIX_ROLES:
                row = NotificationMatrix.query.filter_by(
                    event_type=event_key, role_key=role_key
                ).first()
                if row is None:
                    row = NotificationMatrix(event_type=event_key, role_key=role_key)
                    db.session.add(row)

                if role_key == 'custom':
                    raw = request.form.get(f'custom_{event_key}', '').strip()
                    # Parse comma-separated emails into a JSON list
                    emails = [e.strip() for e in raw.split(',') if e.strip()]
                    row.custom_emails = _json.dumps(emails)
                    row.enabled = bool(emails)
                else:
                    row.enabled = bool(request.form.get(f'matrix_{event_key}_{role_key}'))

        db.session.commit()
        log_action(ACTION_UPDATE, 'NotificationMatrix', None,
                   'Notification Matrix', 'admin updated notification matrix')
        logger.info('NOTIFICATION MATRIX UPDATED | by=%s', current_user.username)
        flash('Notification matrix saved successfully.', 'success')
        return redirect(url_for('auth.notification_matrix'))

    # Build current state dict: {event_key: {role_key: enabled/emails}}
    all_rows = NotificationMatrix.query.all()
    state = {}   # event_key -> role_key -> row
    for row in all_rows:
        state.setdefault(row.event_type, {})[row.role_key] = row

    return render_template(
        'auth/notification_matrix.html',
        matrix_events = MATRIX_EVENTS,
        matrix_roles  = MATRIX_ROLES,
        defaults      = MATRIX_DEFAULTS,
        state         = state,
    )
