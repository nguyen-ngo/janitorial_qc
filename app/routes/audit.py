import logging
from datetime import datetime, timedelta
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required
from app import db
from app.models.audit import AuditLog
from app.models.user import User
from app.utils.decorators import admin_required
from app.utils.audit import log_action, ACTION_DELETE
from app.utils.time_utils import now_eastern

bp = Blueprint('audit', __name__, url_prefix='/audit')

logger = logging.getLogger(__name__)


# ── List (paginated, filterable) ──────────────────────────────────────────────

@bp.route('/')
@login_required
@admin_required
def index():
    page = request.args.get('page', 1, type=int)

    # ── Filter params ─────────────────────────────────────────────────────
    filter_user        = request.args.get('user_id',     '', type=str)
    filter_action      = request.args.get('action',      '', type=str)
    filter_entity_type = request.args.get('entity_type', '', type=str)
    filter_date_from   = request.args.get('date_from',   '', type=str)
    filter_date_to     = request.args.get('date_to',     '', type=str)

    q = AuditLog.query.order_by(AuditLog.created_at.desc())

    if filter_user.isdigit():
        q = q.filter(AuditLog.user_id == int(filter_user))
    if filter_action:
        q = q.filter(AuditLog.action == filter_action)
    if filter_entity_type:
        q = q.filter(AuditLog.entity_type == filter_entity_type)
    if filter_date_from:
        try:
            from datetime import datetime
            q = q.filter(AuditLog.created_at >= datetime.strptime(filter_date_from, '%Y-%m-%d'))
        except ValueError:
            pass
    if filter_date_to:
        try:
            from datetime import datetime, timedelta
            # Include the full day_to by shifting to midnight of next day
            q = q.filter(AuditLog.created_at < datetime.strptime(filter_date_to, '%Y-%m-%d') + timedelta(days=1))
        except ValueError:
            pass

    logs  = q.paginate(page=page, per_page=50, error_out=False)
    users = User.query.order_by(User.username).all()

    # Distinct action and entity_type values for the filter dropdowns
    distinct_actions = (
        AuditLog.query.with_entities(AuditLog.action)
        .distinct()
        .order_by(AuditLog.action)
        .all()
    )
    distinct_entity_types = (
        AuditLog.query.with_entities(AuditLog.entity_type)
        .distinct()
        .order_by(AuditLog.entity_type)
        .all()
    )

    return render_template(
        'audit/index.html',
        logs=logs,
        users=users,
        distinct_actions=[r[0] for r in distinct_actions],
        distinct_entity_types=[r[0] for r in distinct_entity_types],
        filter_user=filter_user,
        filter_action=filter_action,
        filter_entity_type=filter_entity_type,
        filter_date_from=filter_date_from,
        filter_date_to=filter_date_to,
    )


# ── Detail ────────────────────────────────────────────────────────────────────

@bp.route('/<int:log_id>')
@login_required
@admin_required
def view(log_id):
    entry = AuditLog.query.get_or_404(log_id)
    return render_template('audit/view.html', entry=entry)


# ── Purge old logs ────────────────────────────────────────────────────────────

PURGE_OPTIONS = {
    7:   '7 days',
    30:  '30 days',
    60:  '60 days',
    90:  '90 days',
    180: '180 days',
    365: '1 year',
}

@bp.route('/purge', methods=['POST'])
@login_required
@admin_required
def purge():
    """Delete audit log entries older than the selected threshold.

    Accepts a POST form field `older_than` (integer days).
    The purge itself is recorded as a new audit log entry so there is
    always a traceable record of who purged what and when.
    """
    try:
        older_than = int(request.form.get('older_than', 0))
    except (ValueError, TypeError):
        older_than = 0

    if older_than not in PURGE_OPTIONS:
        flash('Invalid purge threshold selected.', 'danger')
        return redirect(url_for('audit.index'))

    cutoff = now_eastern() - timedelta(days=older_than)
    deleted = AuditLog.query.filter(AuditLog.created_at < cutoff).delete()
    db.session.flush()

    label = PURGE_OPTIONS[older_than]
    log_action(
        ACTION_DELETE, 'AuditLog', None,
        f'Purged {deleted} log entries older than {label}',
        f'cutoff={cutoff.strftime("%Y-%m-%d %H:%M:%S")} UTC; deleted={deleted}',
    )

    db.session.commit()

    logger.info(
        'AUDIT PURGE | deleted=%s | older_than=%s days | by=%s',
        deleted, older_than, request.remote_addr,
    )

    flash(
        f'{deleted} audit log entr{"ies" if deleted != 1 else "y"} '
        f'older than {label} have been permanently deleted.',
        'success' if deleted else 'info',
    )
    return redirect(url_for('audit.index'))