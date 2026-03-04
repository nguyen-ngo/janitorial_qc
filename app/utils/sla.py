"""
sla.py
------
Issue SLA (Service Level Agreement) helpers.

SLA thresholds define the maximum number of hours an issue of a given
severity may remain unresolved before it is considered breached.

Statuses
--------
ok        — within the allowed window
at_risk   — past 75% of the allowed window but not yet breached
breached  — past the deadline
None      — issue is already resolved; SLA no longer applies
"""

from datetime import timedelta
from app.utils.time_utils import now_eastern

# ── Configurable thresholds (hours) ──────────────────────────────────────────
SLA_HOURS = {
    'critical': 4,
    'high':     24,
    'medium':   72,
    'low':      168,   # 7 days
}

# Fraction of the window at which an issue becomes "at risk"
AT_RISK_THRESHOLD = 0.75


def sla_deadline(issue):
    """
    Return the datetime by which the issue must be resolved,
    or None if the severity is not recognised.
    """
    hours = SLA_HOURS.get(issue.severity)
    if hours is None:
        return None
    return issue.reported_at + timedelta(hours=hours)


def sla_status(issue):
    """
    Return one of: 'ok', 'at_risk', 'breached', or None.

    None is returned when the issue is already resolved — SLA no longer
    applies.  None is also returned for unrecognised severity values.
    """
    if issue.status == 'resolved':
        return None

    hours = SLA_HOURS.get(issue.severity)
    if hours is None:
        return None

    deadline   = issue.reported_at + timedelta(hours=hours)
    at_risk_at = issue.reported_at + timedelta(hours=hours * AT_RISK_THRESHOLD)
    now        = now_eastern()

    if now >= deadline:
        return 'breached'
    if now >= at_risk_at:
        return 'at_risk'
    return 'ok'


def sla_hours_remaining(issue):
    """
    Return the number of hours remaining before the SLA deadline.
    Negative values indicate the deadline has already passed.
    Returns None for resolved issues or unrecognised severities.
    """
    if issue.status == 'resolved':
        return None
    deadline = sla_deadline(issue)
    if deadline is None:
        return None
    delta = deadline - now_eastern()
    return round(delta.total_seconds() / 3600, 1)


# ── SLA alert dispatcher ──────────────────────────────────────────────────────

def send_sla_alerts():
    """
    Check all open/in-progress issues for SLA breaches or at-risk status and
    dispatch in-app + email notifications to the appropriate recipients.

    Recipient rules:
      - Admins always receive alerts
      - If the issue is assigned, the assignee also receives an alert
      - All followers of the issue also receive an alert
      - Deduplication ensures each user gets at most one notification per call

    Deduplication across cron runs:
      - Issue.sla_notified tracks the highest alert level already sent
        ('at_risk' or 'breached'). A notification is only sent once per level.
      - 'breached' supersedes 'at_risk': if a user was already notified
        at-risk, they will receive a second notification when it breaches.

    Returns the number of notifications created.
    """
    from flask import current_app, url_for
    from app import db
    from app.models.issue import Issue
    from app.models.user import User
    from app.utils.notifications import notify
    from app.models.notification import EVENT_SLA_ALERT
    import logging

    logger = logging.getLogger(__name__)

    open_issues = Issue.query.filter(
        Issue.status.in_(['open', 'in_progress'])
    ).all()

    admins = User.query.filter_by(role='admin').all()

    total_sent = 0

    for issue in open_issues:
        status = sla_status(issue)

        # Only act on at_risk or breached
        if status not in ('at_risk', 'breached'):
            continue

        # Skip if this level (or higher) was already notified
        already = issue.sla_notified
        if already == 'breached':
            continue   # highest level already sent
        if already == 'at_risk' and status == 'at_risk':
            continue   # at_risk already sent, not yet breached

        # Build recipient set — deduplicated by user.id
        recipients = {}

        for admin in admins:
            recipients[admin.id] = admin

        if issue.assigned_to and issue.assigned_user:
            recipients[issue.assigned_user.id] = issue.assigned_user

        for follower_link in issue.followers.all():
            user = follower_link.user
            recipients[user.id] = user

        if not recipients:
            continue

        # Compose message
        hrs   = sla_hours_remaining(issue)
        deadline = sla_deadline(issue)

        if status == 'breached':
            title = f'🚨 SLA Breached — Issue #{issue.id} ({issue.severity.title()})'
            body  = (
                f'Issue #{issue.id} at {issue.area.facility.name} '
                f'has breached its SLA deadline. '
                f'Severity: {issue.severity.title()}. '
                f'Deadline was {deadline.strftime("%Y-%m-%d %H:%M") if deadline else "N/A"}. '
                f'Current status: {issue.status.replace("_", " ").title()}.'
            )
        else:  # at_risk
            title = f'⚠️ SLA At Risk — Issue #{issue.id} ({issue.severity.title()})'
            body  = (
                f'Issue #{issue.id} at {issue.area.facility.name} '
                f'is approaching its SLA deadline with approximately '
                f'{abs(hrs):.1f}h remaining. '
                f'Severity: {issue.severity.title()}. '
                f'Deadline: {deadline.strftime("%Y-%m-%d %H:%M") if deadline else "N/A"}. '
                f'Current status: {issue.status.replace("_", " ").title()}.'
            )

        try:
            link = url_for('issues.view', issue_id=issue.id)
        except RuntimeError:
            link = f'/issues/{issue.id}'

        for user in recipients.values():
            notify(
                recipient  = user,
                title      = title,
                body       = body,
                link       = link,
                issue_id   = issue.id,
                event_type = EVENT_SLA_ALERT,
                send_email = True,
            )
            total_sent += 1

        # Mark this issue as notified at the current level
        issue.sla_notified = status
        logger.info(
            'SLA ALERT SENT | issue_id=%s | status=%s | recipients=%s',
            issue.id, status, list(recipients.keys()),
        )

    if total_sent:
        db.session.commit()

    return total_sent
