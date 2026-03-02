"""
app/utils/notifications.py
~~~~~~~~~~~~~~~~~~~~~~~~~~
Central helper for creating in-app notifications and dispatching email alerts.
"""

import logging
from flask import current_app, render_template_string
from flask_mail import Message
from app import db, mail
from app.models.notification import Notification

logger = logging.getLogger(__name__)

_EMAIL_HTML = """\
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
    Janitorial QC System — automated notification. Do not reply to this email.
  </p>
</body>
</html>
"""

_EMAIL_TEXT = """\
{{ title }}

{{ body }}
{% if link %}
View: {{ base_url }}{{ link }}
{% endif %}

--
Janitorial QC System — automated notification.
"""


def notify(
    recipient,
    title: str,
    body: str,
    link: str = None,
    issue_id: int = None,
    inspection_id: int = None,
    send_email: bool = True,
):
    """Create an in-app Notification record and optionally send an email."""
    # ── 1. Persist in-app notification ─────────────────────────────────────
    notif = Notification(
        user_id       = recipient.id,
        title         = title,
        body          = body,
        link          = link,
        issue_id      = issue_id,
        inspection_id = inspection_id,
        is_read       = False,
    )
    db.session.add(notif)
    # NOTE: The caller is responsible for calling db.session.commit().

    logger.info(
        'NOTIFICATION CREATED | user=%s | title=%s | issue_id=%s | inspection_id=%s',
        recipient.username, title, issue_id, inspection_id,
    )

    # ── 2. Send email (best-effort) ─────────────────────────────────────────
    if send_email and recipient.email and current_app.config.get('MAIL_SERVER'):
        try:
            base_url = current_app.config.get('APP_BASE_URL', '').rstrip('/')

            html_body = render_template_string(
                _EMAIL_HTML,
                title=title,
                body=body,
                link=link,
                base_url=base_url,
            )
            text_body = render_template_string(
                _EMAIL_TEXT,
                title=title,
                body=body,
                link=link,
                base_url=base_url,
            )

            sender = current_app.config.get(
                'MAIL_DEFAULT_SENDER',
                current_app.config.get('MAIL_USERNAME', 'noreply@janitorialqc.local'),
            )

            msg = Message(
                subject    = f'[JQC] {title}',
                sender     = sender,
                recipients = [recipient.email],
                body       = text_body,
                html       = html_body,
            )
            mail.send(msg)
            logger.info(
                'NOTIFICATION EMAIL SENT | to=%s | subject=%s',
                recipient.email, msg.subject,
            )
        except Exception as exc:
            logger.error(
                'NOTIFICATION EMAIL FAILED | to=%s | error=%s',
                recipient.email, exc,
            )