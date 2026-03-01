# app/routes/notifications.py
import logging
from flask import Blueprint, jsonify, request, abort
from flask_login import login_required, current_user
from app import db
from app.models.notification import Notification

logger = logging.getLogger(__name__)

bp = Blueprint('notifications', __name__, url_prefix='/notifications')


@bp.route('/feed')
@login_required
def feed():
    """Return the 20 most recent notifications for the current user as JSON.
    Used by the navbar bell icon to populate the dropdown.
    """
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


@bp.route('/<int:notif_id>/mark-read', methods=['POST'])
@login_required
def mark_read(notif_id):
    """Mark a single notification as read."""
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


@bp.route('/mark-all-read', methods=['POST'])
@login_required
def mark_all_read():
    """Mark all unread notifications for the current user as read."""
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
    return jsonify({'ok': True, 'marked': updated})