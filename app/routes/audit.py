import logging
from flask import Blueprint, render_template, request
from flask_login import login_required
from app.models.audit import AuditLog
from app.models.user import User
from app.utils.decorators import admin_required

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
        db.session.query(AuditLog.action)
        .distinct()
        .order_by(AuditLog.action)
        .all()
    )
    distinct_entity_types = (
        db.session.query(AuditLog.entity_type)
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


# Avoid circular import — imported after function definitions
from app import db  # noqa: E402
