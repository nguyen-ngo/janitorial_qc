"""
app/utils/notifications.py
~~~~~~~~~~~~~~~~~~~~~~~~~~
Central helper for creating in-app notifications and dispatching email alerts.

Usage
-----
    from app.utils.notifications import notify

    notify(
        recipient   = some_user,
        title       = 'Issue #12 Updated',
        body        = 'Status changed to In Progress by admin.',
        link        = url_for('issues.view', issue_id=12),
        issue_id    = 12,
        event_type  = EVENT_ISSUE_STATUS,   # controls preference lookup
        send_email  = True,
    )

Email delivery is best-effort: a failure to send will be logged but will NOT
raise an exception or roll back the DB transaction.

Digest emails are sent by calling send_pending_digests(frequency) from the
/notifications/send-digest route, which is triggered by a server cron job.
"""

import logging, threading
from flask import current_app, render_template_string
from flask_mail import Message
from app import db, mail
from app.models.notification import (
    Notification, NotificationPreference,
    ALL_EVENT_TYPES,
)

logger = logging.getLogger(__name__)


# ── Email templates ────────────────────────────────────────────────────────────

_EMAIL_HTML_SINGLE = """\
<!DOCTYPE html>
<html>
<body style="font-family:Arial,sans-serif;color:#333;max-width:600px;margin:auto;">
  <h2 style="color:#0d6efd;">{{ title }}</h2>
  <p>{{ body }}</p>
  {% if link %}
  <p>
    <a href="{{ base_url }}{{ link }}"
       style="background:#0d6efd;color:#fff;padding:10px 20px;
              text-decoration:none;border-radius:4px;display:inline-block;">
      View Details
    </a>
  </p>
  {% endif %}
  <hr style="border:none;border-top:1px solid #eee;margin-top:32px;">
  <p style="font-size:12px;color:#888;">
    Janitorial QC System — automated notification. Do not reply to this email.<br>
    <a href="{{ base_url }}/notifications/preferences" style="color:#888;">
      Manage notification preferences
    </a>
  </p>
</body>
</html>
"""

_EMAIL_TEXT_SINGLE = """\
{{ title }}

{{ body }}
{% if link %}
View: {{ base_url }}{{ link }}
{% endif %}

--
Janitorial QC System — automated notification.
Manage preferences: {{ base_url }}/notifications/preferences
"""

_EMAIL_HTML_DIGEST = """\
<!DOCTYPE html>
<html>
<body style="font-family:Arial,sans-serif;color:#333;max-width:600px;margin:auto;">
  <h2 style="color:#0d6efd;">Your {{ frequency|title }} JQC Notification Digest</h2>
  <p>You have <strong>{{ notifications|length }}</strong> new notification(s):</p>
  <hr style="border:none;border-top:1px solid #eee;">
  {% for n in notifications %}
  <div style="margin-bottom:20px;padding:12px;background:#f8f9fa;border-radius:6px;
              border-left:4px solid #0d6efd;">
    <p style="margin:0 0 4px;font-weight:bold;">{{ n.title }}</p>
    <p style="margin:0 0 8px;font-size:.9em;color:#555;">{{ n.body }}</p>
    {% if n.link %}
    <a href="{{ base_url }}{{ n.link }}"
       style="font-size:.85em;color:#0d6efd;text-decoration:none;">
      View Details →
    </a>
    {% endif %}
    <p style="margin:6px 0 0;font-size:.75em;color:#999;">
      {{ n.created_at.strftime('%b %d, %Y %I:%M %p') }}
    </p>
  </div>
  {% endfor %}
  <hr style="border:none;border-top:1px solid #eee;margin-top:32px;">
  <p style="font-size:12px;color:#888;">
    Janitorial QC System — automated digest. Do not reply to this email.<br>
    <a href="{{ base_url }}/notifications/preferences" style="color:#888;">
      Manage notification preferences
    </a>
  </p>
</body>
</html>
"""

_EMAIL_TEXT_DIGEST = """\
Your {{ frequency|title }} JQC Notification Digest
{{ notifications|length }} new notification(s):

{% for n in notifications %}
---
{{ n.title }}
{{ n.body }}
{% if n.link %}View: {{ base_url }}{{ n.link }}{% endif %}
{{ n.created_at.strftime('%b %d, %Y %I:%M %p') }}
{% endfor %}

--
Janitorial QC System — automated digest.
Manage preferences: {{ base_url }}/notifications/preferences
"""


# ── Preference helpers ─────────────────────────────────────────────────────────

def _get_preference(user_id, event_type):
    """Return the NotificationPreference for a user+event, or None if not set."""
    if not event_type:
        return None
    return NotificationPreference.query.filter_by(
        user_id=user_id, event_type=event_type
    ).first()


def _email_enabled_for(user, event_type):
    """Return True if the user wants an immediate email for this event type."""
    pref = _get_preference(user.id, event_type)
    if pref is None:
        return True          # Default: email on, immediate
    if not pref.email_enabled:
        return False         # User opted out of email entirely for this event
    if pref.digest_mode:
        return False         # User prefers digest — suppress immediate email
    return True


def _digest_mode_for(user, event_type):
    """Return True if this notification should be held for digest delivery."""
    pref = _get_preference(user.id, event_type)
    if pref is None:
        return False
    return pref.email_enabled and pref.digest_mode


# ── Core notify function ───────────────────────────────────────────────────────

def notify(
    recipient,
    title: str,
    body: str,
    link: str = None,
    issue_id: int = None,
    inspection_id: int = None,
    event_type: str = None,
    send_email: bool = True,
):
    """Create an in-app Notification record and optionally send an email.

    Parameters
    ----------
    recipient    : User ORM instance
    title        : Short notification headline
    body         : Full notification message
    link         : Relative URL for the 'View Details' button/link
    issue_id     : FK to issues.id (optional)
    inspection_id: FK to inspections.id (optional)
    event_type   : One of the EVENT_* constants from models.notification
                   Used to look up the user's preference for this event.
    send_email   : Master switch — set False to suppress all email (overrides prefs)
    """
    # Determine digest flag before creating the record
    hold_for_digest = send_email and bool(event_type) and _digest_mode_for(recipient, event_type)

    # ── 1. Persist in-app notification ──────────────────────────────────────
    notif = Notification(
        user_id        = recipient.id,
        title          = title,
        body           = body,
        link           = link,
        issue_id       = issue_id,
        inspection_id  = inspection_id,
        is_read        = False,
        digest_pending = hold_for_digest,
    )
    db.session.add(notif)
    # NOTE: Caller is responsible for db.session.commit()

    logger.info(
        'NOTIFICATION CREATED | user=%s | event=%s | title=%s | digest=%s',
        recipient.username, event_type, title, hold_for_digest,
    )

    # ── 2. Send immediate email if applicable ────────────────────────────────
    if send_email and not hold_for_digest:
        should_send = (
            event_type is None or _email_enabled_for(recipient, event_type)
        )
        if should_send and recipient.email and current_app.config.get('MAIL_SERVER'):
            _send_single_email(recipient, title, body, link)


def _send_single_email(recipient, title, body, link):
    """Dispatch a single immediate notification email in a background thread.

    Sending is offloaded to a daemon thread so SMTP latency never blocks the
    HTTP response. The Flask application context is pushed explicitly so that
    Flask-Mail and config lookups work outside the request context.
    """
    # Render templates while still inside the request context
    try:
        base_url = current_app.config.get('APP_BASE_URL', '').rstrip('/')
        sender   = current_app.config.get(
            'MAIL_DEFAULT_SENDER',
            current_app.config.get('MAIL_USERNAME', 'noreply@janitorialqc.local'),
        )
        html_body = render_template_string(
            _EMAIL_HTML_SINGLE, title=title, body=body, link=link, base_url=base_url,
        )
        text_body = render_template_string(
            _EMAIL_TEXT_SINGLE, title=title, body=body, link=link, base_url=base_url,
        )
        msg = Message(
            subject    = f'[JQC] {title}',
            sender     = sender,
            recipients = [recipient.email],
            body       = text_body,
            html       = html_body,
        )
    except Exception as exc:
        logger.error('NOTIFICATION EMAIL BUILD FAILED | to=%s | error=%s', recipient.email, exc)
        return

    # Capture app instance before leaving the request context
    app              = current_app._get_current_object()
    recipient_email  = recipient.email
    subject          = msg.subject

    def _send():
        with app.app_context():
            try:
                mail.send(msg)
                logger.info(
                    'NOTIFICATION EMAIL SENT | to=%s | subject=%s',
                    recipient_email, subject,
                )
            except Exception as exc:
                logger.error(
                    'NOTIFICATION EMAIL FAILED | to=%s | error=%s',
                    recipient_email, exc,
                )

    t = threading.Thread(target=_send, daemon=True)
    t.start()


# ── Digest delivery ────────────────────────────────────────────────────────────


# ── Customer portal notifications ─────────────────────────────────────────────

def notify_customers_for_facility(
    facility_id: int,
    event_type: str,
    title: str,
    body: str,
    link: str = None,
    issue_id: int = None,
    inspection_id: int = None,
):
    """Dispatch in-app + email notifications to all customer users assigned
    to the given facility.

    Resolves assignments via CustomerAssignment rows:
      - facility-scoped assignment (facility_id matches exactly)
      - project-scoped assignment (facility belongs to the project, no facility_id set)

    Respects each customer's NotificationPreference for the supplied event_type.
    Best-effort: a failure on one recipient does not block others.

    Parameters
    ----------
    facility_id   : The facility where the event occurred.
    event_type    : EVENT_CUSTOMER_INSPECTION_DONE or EVENT_CUSTOMER_ISSUE_UPDATED.
    title         : Short notification headline.
    body          : Full notification message.
    link          : Relative URL for 'View Details'.
    issue_id      : FK to issues.id (optional).
    inspection_id : FK to inspections.id (optional).
    """
    try:
        from app.models.project import CustomerAssignment
        from app.models.facility import Facility
        from app.models.user import User

        facility = Facility.query.get(facility_id)
        if not facility:
            logger.warning(
                'notify_customers_for_facility | facility_id=%s not found', facility_id
            )
            return

        # Collect distinct customer user IDs that have access to this facility
        notified_user_ids = set()

        # 1. Direct facility-scoped assignments
        direct = CustomerAssignment.query.filter_by(facility_id=facility_id).all()
        for a in direct:
            notified_user_ids.add(a.user_id)

        # 2. Project-scoped assignments (no facility_id) — if facility belongs to a project
        if facility.project_id:
            project_wide = CustomerAssignment.query.filter_by(
                project_id=facility.project_id,
                facility_id=None,
            ).all()
            for a in project_wide:
                notified_user_ids.add(a.user_id)

        if not notified_user_ids:
            logger.debug(
                'notify_customers_for_facility | facility_id=%s | no customer assignments found',
                facility_id,
            )
            return

        for user_id in notified_user_ids:
            user = User.query.get(user_id)
            if not user or not user.active or user.role != 'customer':
                continue
            try:
                notify(
                    recipient     = user,
                    title         = title,
                    body          = body,
                    link          = link,
                    issue_id      = issue_id,
                    inspection_id = inspection_id,
                    event_type    = event_type,
                    send_email    = True,
                )
                logger.info(
                    'CUSTOMER NOTIFY | user=%s | facility_id=%s | event=%s',
                    user.username, facility_id, event_type,
                )
            except Exception as exc:
                logger.error(
                    'CUSTOMER NOTIFY FAILED | user=%s | facility_id=%s | event=%s | error=%s',
                    user_id, facility_id, event_type, exc,
                )

    except Exception as exc:
        logger.error(
            'notify_customers_for_facility | unexpected error | facility_id=%s | error=%s',
            facility_id, exc,
        )

def send_pending_digests(frequency: str = 'daily'):
    """Send digest emails for all users who have pending digest notifications.

    Called from the /notifications/send-digest route, which is hit by cron.

    Parameters
    ----------
    frequency : 'hourly' or 'daily' — matches digest_frequency in preferences
    """
    if not current_app.config.get('MAIL_SERVER'):
        logger.warning('DIGEST SKIPPED | MAIL_SERVER not configured')
        return 0

    # Find all users with pending digest notifications
    from app.models.user import User
    pending_user_ids = (
        db.session.query(Notification.user_id)
        .filter_by(digest_pending=True)
        .distinct()
        .all()
    )
    pending_user_ids = [row[0] for row in pending_user_ids]

    sent_count = 0
    for user_id in pending_user_ids:
        user = User.query.get(user_id)
        if not user or not user.email:
            continue

        # Collect only the notifications that match this frequency for this user
        # A notification is included in a frequency's digest if at least one of
        # the user's digest preferences matches that frequency.
        # Simple approach: include all pending if user has any pref with this frequency.
        has_freq_pref = NotificationPreference.query.filter_by(
            user_id=user_id,
            digest_mode=True,
            digest_frequency=frequency,
            email_enabled=True,
        ).first()

        if not has_freq_pref:
            continue

        notifications = Notification.query.filter_by(
            user_id=user_id,
            digest_pending=True,
        ).order_by(Notification.created_at.asc()).all()

        if not notifications:
            continue

        try:
            base_url  = current_app.config.get('APP_BASE_URL', '').rstrip('/')
            sender    = current_app.config.get(
                'MAIL_DEFAULT_SENDER',
                current_app.config.get('MAIL_USERNAME', 'noreply@janitorialqc.local'),
            )
            html_body = render_template_string(
                _EMAIL_HTML_DIGEST,
                notifications=notifications,
                frequency=frequency,
                base_url=base_url,
            )
            text_body = render_template_string(
                _EMAIL_TEXT_DIGEST,
                notifications=notifications,
                frequency=frequency,
                base_url=base_url,
            )
            msg = Message(
                subject    = f'[JQC] Your {frequency.title()} Notification Digest '
                             f'({len(notifications)} update{"s" if len(notifications) != 1 else ""})',
                sender     = sender,
                recipients = [user.email],
                body       = text_body,
                html       = html_body,
            )
            mail.send(msg)

            # Clear the pending flag on all notifications just sent
            for n in notifications:
                n.digest_pending = False
            db.session.commit()

            sent_count += 1
            logger.info(
                'DIGEST EMAIL SENT | to=%s | frequency=%s | count=%s',
                user.email, frequency, len(notifications),
            )
        except Exception as exc:
            logger.error(
                'DIGEST EMAIL FAILED | to=%s | frequency=%s | error=%s',
                user.email, frequency, exc,
            )

    return sent_count

# ── Matrix-driven broadcast helpers ───────────────────────────────────────────

def notify_by_matrix(
    event_type: str,
    title: str,
    body: str,
    link: str = None,
    issue_id: int = None,
    inspection_id: int = None,
    facility_id: int = None,
    exclude_user_ids: set = None,
):
    """
    Dispatch in-app + email notifications for a broadcast event according
    to the admin-configured notification matrix.

    For each enabled role in the matrix, all active users with that role
    are notified (optionally scoped to facility via CustomerAssignment for
    the 'customer' role).  Custom email addresses are sent a plain email
    without creating an in-app Notification record.

    Parameters
    ----------
    event_type        : One of the MATRIX_EVENTS keys from notification_matrix.
    title             : Short notification headline.
    body              : Full notification body.
    link              : Relative URL for 'View Details'.
    issue_id          : FK to issues.id (optional).
    inspection_id     : FK to inspections.id (optional).
    facility_id       : Used to scope 'customer' role to assigned facility.
    exclude_user_ids  : Set of user IDs to skip (e.g. the actor themselves).
    """
    from app.models.notification_matrix import (
        is_enabled, get_custom_emails_for, MATRIX_ROLES,
    )
    from app.models.user import User

    exclude = set(exclude_user_ids or [])
    notified = set()   # deduplicate across roles

    role_to_db = {
        'admin':           'admin',
        'supervisor':      'supervisor',
        'inspector':       'inspector',
        'project_manager': 'project_manager',
        'customer':        'customer',
    }

    for role_key, _ in MATRIX_ROLES:
        if role_key == 'custom':
            continue   # handled separately below
        if not is_enabled(event_type, role_key):
            continue

        db_role = role_to_db.get(role_key)
        if not db_role:
            continue

        users = User.query.filter_by(role=db_role, active=True).all()

        # Scope customer role to facility if provided
        if role_key == 'customer' and facility_id:
            from app.utils.notifications import notify_customers_for_facility
            notify_customers_for_facility(
                facility_id   = facility_id,
                event_type    = event_type,
                title         = title,
                body          = body,
                link          = link,
                issue_id      = issue_id,
                inspection_id = inspection_id,
            )
            continue   # notify_customers_for_facility handles dedup internally

        for user in users:
            if user.id in exclude or user.id in notified:
                continue
            notify(
                recipient     = user,
                title         = title,
                body          = body,
                link          = link,
                issue_id      = issue_id,
                inspection_id = inspection_id,
                event_type    = event_type,
                send_email    = True,
            )
            notified.add(user.id)

    # ── Custom email recipients ───────────────────────────────────────────
    custom_emails = get_custom_emails_for(event_type)
    for email in custom_emails:
        _send_custom_email(email, title, body, link)

    logger.info(
        'MATRIX NOTIFY | event=%s | notified=%s | custom_emails=%s',
        event_type, len(notified), len(custom_emails),
    )


def _send_custom_email(to_email: str, title: str, body: str, link: str = None):
    """Send a plain email to a custom (non-user) address. Best-effort."""
    try:
        base_url = current_app.config.get('APP_BASE_URL', '').rstrip('/')
        sender   = current_app.config.get(
            'MAIL_DEFAULT_SENDER',
            current_app.config.get('MAIL_USERNAME', 'noreply@janitorialqc.local'),
        )
        html_body = render_template_string(
            _EMAIL_HTML_SINGLE, title=title, body=body,
            link=link, base_url=base_url,
        )
        text_body = render_template_string(
            _EMAIL_TEXT_SINGLE, title=title, body=body,
            link=link, base_url=base_url,
        )
        msg = Message(
            subject    = f'[JQC] {title}',
            sender     = sender,
            recipients = [to_email],
            body       = text_body,
            html       = html_body,
        )
    except Exception as exc:
        logger.error('CUSTOM EMAIL BUILD FAILED | to=%s | error=%s', to_email, exc)
        return

    app = current_app._get_current_object()

    def _send():
        with app.app_context():
            try:
                mail.send(msg)
                logger.info('CUSTOM EMAIL SENT | to=%s', to_email)
            except Exception as exc:
                logger.error('CUSTOM EMAIL FAILED | to=%s | error=%s', to_email, exc)

    import threading
    threading.Thread(target=_send, daemon=True).start()
