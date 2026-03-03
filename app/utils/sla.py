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
