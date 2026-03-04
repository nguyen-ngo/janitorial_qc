# app/routes/notifications.py
import logging
from flask import (Blueprint, jsonify, request, abort,
                   render_template, redirect, url_for, flash, current_app)
from flask_login import login_required, current_user
from app import db
from app.models.notification import (
    Notification, NotificationPreference, ALL_EVENT_TYPES
)

logger = logging.getLogger(__name__)

bp = Blueprint('notifications', __name__, url_prefix='/notifications')


# ── Bell feed (navbar dropdown) ───────────────────────────────────────────────

@bp.route('/feed')
@login_required
def feed():
    """Return the 20 most recent notifications for the current user as JSON."""
    notifs = (
        Notification.query
        .filter_by(user_id=current_user.id)
        .order_by(Notification.created_at.desc())
        .limit(20)
        .all()
    )
    unread_count = Notification.query.filter_by(
        user_id=current_user.id, is_read=False
    ).count()

    items = []
    for n in notifs:
        items.append({
            'id':         n.id,
            'title':      n.title,
            'body':       n.body,
            'link':       n.link,
            'is_read':    n.is_read,
            'created_at': n.created_at.strftime('%b %d, %Y %I:%M %p'),
        })

    return jsonify({'notifications': items, 'unread_count': unread_count})


# ── Full notification history page ────────────────────────────────────────────

@bp.route('/')
@login_required
def index():
    """Full paginated notification history with read/unread filter."""
    page        = request.args.get('page', 1, type=int)
    filter_read = request.args.get('filter', 'all')  # 'all' | 'unread' | 'read'

    q = Notification.query.filter_by(user_id=current_user.id)

    if filter_read == 'unread':
        q = q.filter_by(is_read=False)
    elif filter_read == 'read':
        q = q.filter_by(is_read=True)

    notifications = q.order_by(Notification.created_at.desc()).paginate(
        page=page, per_page=25, error_out=False
    )
    unread_count = Notification.query.filter_by(
        user_id=current_user.id, is_read=False
    ).count()

    return render_template(
        'notifications/index.html',
        notifications=notifications,
        filter_read=filter_read,
        unread_count=unread_count,
    )


# ── Mark single notification read ─────────────────────────────────────────────

@bp.route('/<int:notif_id>/mark-read', methods=['POST'])
@login_required
def mark_read(notif_id):
    notif = Notification.query.get_or_404(notif_id)
    if notif.user_id != current_user.id:
        abort(403)
    notif.is_read = True
    db.session.commit()
    logger.info(
        'NOTIFICATION READ | id=%s | user=%s',
        notif_id, current_user.username,
    )
    return jsonify({'ok': True})


# ── Mark all read ─────────────────────────────────────────────────────────────

@bp.route('/mark-all-read', methods=['POST'])
@login_required
def mark_all_read():
    updated = (
        Notification.query
        .filter_by(user_id=current_user.id, is_read=False)
        .update({'is_read': True})
    )
    db.session.commit()
    logger.info(
        'NOTIFICATIONS ALL READ | user=%s | count=%s',
        current_user.username, updated,
    )

    # Support both AJAX (returns JSON) and form POST (redirects to index)
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or \
       request.content_type == 'application/json':
        return jsonify({'ok': True, 'marked': updated})
    return redirect(url_for('notifications.index'))


# ── Notification preferences ──────────────────────────────────────────────────

@bp.route('/preferences', methods=['GET', 'POST'])
@login_required
def preferences():
    """Display and save per-event notification preferences."""
    if request.method == 'POST':
        for event_type in ALL_EVENT_TYPES:
            pref = NotificationPreference.query.filter_by(
                user_id=current_user.id,
                event_type=event_type,
            ).first()

            if pref is None:
                pref = NotificationPreference(
                    user_id=current_user.id,
                    event_type=event_type,
                )
                db.session.add(pref)

            pref.email_enabled     = bool(request.form.get(f'email_{event_type}'))
            pref.digest_mode       = bool(request.form.get(f'digest_{event_type}'))
            pref.digest_frequency  = request.form.get(f'freq_{event_type}', 'daily')

            # Guard: digest_mode only meaningful when email is enabled
            if not pref.email_enabled:
                pref.digest_mode = False

        db.session.commit()
        logger.info(
            'NOTIFICATION PREFERENCES SAVED | user=%s',
            current_user.username,
        )
        flash('Notification preferences saved.', 'success')
        return redirect(url_for('notifications.preferences'))

    # Build a dict keyed by event_type for easy template access
    prefs_map = {}
    for pref in NotificationPreference.query.filter_by(user_id=current_user.id).all():
        prefs_map[pref.event_type] = pref

    return render_template(
        'notifications/preferences.html',
        event_types=ALL_EVENT_TYPES,
        prefs_map=prefs_map,
    )


# ── Digest trigger (called by cron) ───────────────────────────────────────────

@bp.route('/send-digest', methods=['POST'])
def send_digest():
    """Trigger digest email delivery. Protected by a shared secret token.

    Called by a cron job, e.g.:
        # Hourly digest
        0 * * * * curl -s -X POST https://yourdomain.com/notifications/send-digest \
            -d "token=YOUR_DIGEST_SECRET&frequency=hourly"

        # Daily digest at 07:00
        0 7 * * * curl -s -X POST https://yourdomain.com/notifications/send-digest \
            -d "token=YOUR_DIGEST_SECRET&frequency=daily"
    """
    token     = request.form.get('token') or request.args.get('token')
    frequency = request.form.get('frequency', 'daily')

    expected  = current_app.config.get('DIGEST_SECRET')
    if not expected or token != expected:
        logger.warning('DIGEST TRIGGER REJECTED | bad or missing token')
        abort(403)

    if frequency not in ('hourly', 'daily'):
        abort(400)

    from app.utils.notifications import send_pending_digests
    sent = send_pending_digests(frequency=frequency)

    logger.info('DIGEST TRIGGERED | frequency=%s | sent=%s', frequency, sent)
    return jsonify({'ok': True, 'sent': sent, 'frequency': frequency})

# ── SLA alert trigger (called by cron) ────────────────────────────────────────

@bp.route('/check-sla', methods=['POST'])
def check_sla():
    """Scan all open issues for SLA breaches and dispatch alerts.

    Protected by the same DIGEST_SECRET token used for digest delivery.
    Recommended cron schedule — every 30 minutes is sufficient for most
    deployments; adjust based on your shortest SLA threshold (critical = 4h):

        */30 * * * * curl -s -X POST https://yourdomain.com/notifications/check-sla \\
            -d "token=YOUR_DIGEST_SECRET"
    """
    token    = request.form.get('token') or request.args.get('token')
    expected = current_app.config.get('DIGEST_SECRET')

    if not expected or token != expected:
        logger.warning('SLA CHECK REJECTED | bad or missing token')
        abort(403)

    from app.utils.sla import send_sla_alerts
    sent = send_sla_alerts()

    logger.info('SLA CHECK TRIGGERED | notifications_sent=%s', sent)
    return jsonify({'ok': True, 'notifications_sent': sent})
