"""
app/models/notification_matrix.py
----------------------------------
Admin-controlled notification matrix.

One row per (event_type, role_key) pair.

role_key values
---------------
admin            — all users with role='admin'
director         — all users with role='director'
inspector        — all users with role='inspector'
project_manager  — all users with role='project_manager'
customer         — all customer-portal users assigned to the relevant facility
assignee         — the specific user the issue/inspection is assigned to
                   (implicit; always notified regardless of matrix)
custom           — free-form extra email addresses stored in custom_emails JSON

Default matrix (mirrors current hardcoded behaviour)
-----------------------------------------------------
inspection_completed     : admin ✓  director  ✓  inspector ✗  pm ✗  customer ✓
issue_assigned           : admin ✗  director  ✗  inspector ✗  pm ✗  customer ✗  (assignee implicit)
issue_status             : admin ✗  director  ✗  inspector ✗  pm ✗  customer ✗  (assignee implicit)
issue_comment            : admin ✗  director  ✗  inspector ✗  pm ✗  customer ✗  (assignee implicit)
issue_follow_update      : admin ✗  director  ✗  inspector ✗  pm ✗  customer ✗  (followers implicit)
issue_flagged            : admin ✗  director  ✗  inspector ✗  pm ✗  customer ✓  (assignee implicit)
issue_created            : admin ✗  director  ✗  inspector ✗  pm ✗  customer ✓  (assignee implicit)
issue_updated_customer   : admin ✗  director  ✗  inspector ✗  pm ✗  customer ✓
verification_requested   : admin ✓  director  ✓  inspector ✗  pm ✗  customer ✗
sla_alert                : admin ✓  director  ✗  inspector ✗  pm ✗  customer ✗  (assignee + followers implicit)
"""

import json
from app import db

# Role keys available in the matrix UI
MATRIX_ROLES = [
    ('admin',           'Admin'),
    ('director',        'Director'),
    ('inspector',       'Inspector'),
    ('project_manager', 'Project Manager'),
    ('customer',        'Customer'),
    ('custom',          'Custom Recipients'),
]

# Events shown in the matrix — maps event_key → display label
# event_key is used as the DB event_type value
MATRIX_EVENTS = {
    'inspection_completed':   'Inspection completed',
    'issue_assigned':         'Issue assigned (new)',
    'issue_reassigned':       'Issue reassigned',
    'issue_unassigned':       'Issue unassigned',
    'issue_status':           'Issue status changed',
    'issue_comment':          'Issue comment added',
    'issue_follow_update':    'Issue follow update',
    'issue_flagged':          'Issue flagged (from inspection)',
    'issue_created':          'Issue created (standalone)',
    'issue_updated_customer': 'Issue updated (customer)',
    'verification_requested': 'Verification requested',
    'sla_alert':              'SLA at-risk / breached',
}

# Default enabled state: (event_key, role_key) → True/False
# Mirrors the current hardcoded behaviour exactly.
MATRIX_DEFAULTS = {
    # inspection_completed
    ('inspection_completed',   'admin'):           True,
    ('inspection_completed',   'director'):      True,
    ('inspection_completed',   'inspector'):       False,
    ('inspection_completed',   'project_manager'): False,
    ('inspection_completed',   'customer'):        True,
    ('inspection_completed',   'custom'):          False,
    # issue_assigned (assignee is always notified implicitly)
    ('issue_assigned',         'admin'):           False,
    ('issue_assigned',         'director'):      False,
    ('issue_assigned',         'inspector'):       False,
    ('issue_assigned',         'project_manager'): False,
    ('issue_assigned',         'customer'):        False,
    ('issue_assigned',         'custom'):          False,
    # issue_reassigned
    ('issue_reassigned',       'admin'):           False,
    ('issue_reassigned',       'director'):      False,
    ('issue_reassigned',       'inspector'):       False,
    ('issue_reassigned',       'project_manager'): False,
    ('issue_reassigned',       'customer'):        False,
    ('issue_reassigned',       'custom'):          False,
    # issue_unassigned
    ('issue_unassigned',       'admin'):           False,
    ('issue_unassigned',       'director'):      False,
    ('issue_unassigned',       'inspector'):       False,
    ('issue_unassigned',       'project_manager'): False,
    ('issue_unassigned',       'customer'):        False,
    ('issue_unassigned',       'custom'):          False,
    # issue_status
    ('issue_status',           'admin'):           False,
    ('issue_status',           'director'):      False,
    ('issue_status',           'inspector'):       False,
    ('issue_status',           'project_manager'): False,
    ('issue_status',           'customer'):        False,
    ('issue_status',           'custom'):          False,
    # issue_comment
    ('issue_comment',          'admin'):           False,
    ('issue_comment',          'director'):      False,
    ('issue_comment',          'inspector'):       False,
    ('issue_comment',          'project_manager'): False,
    ('issue_comment',          'customer'):        False,
    ('issue_comment',          'custom'):          False,
    # issue_follow_update (followers always notified implicitly)
    ('issue_follow_update',    'admin'):           False,
    ('issue_follow_update',    'director'):      False,
    ('issue_follow_update',    'inspector'):       False,
    ('issue_follow_update',    'project_manager'): False,
    ('issue_follow_update',    'customer'):        False,
    ('issue_follow_update',    'custom'):          False,
    # issue_flagged (from inspection)
    ('issue_flagged',          'admin'):           False,
    ('issue_flagged',          'director'):      False,
    ('issue_flagged',          'inspector'):       False,
    ('issue_flagged',          'project_manager'): False,
    ('issue_flagged',          'customer'):        True,
    ('issue_flagged',          'custom'):          False,
    # issue_created (standalone)
    ('issue_created',          'admin'):           False,
    ('issue_created',          'director'):      False,
    ('issue_created',          'inspector'):       False,
    ('issue_created',          'project_manager'): False,
    ('issue_created',          'customer'):        True,
    ('issue_created',          'custom'):          False,
    # issue_updated_customer
    ('issue_updated_customer', 'admin'):           False,
    ('issue_updated_customer', 'director'):      False,
    ('issue_updated_customer', 'inspector'):       False,
    ('issue_updated_customer', 'project_manager'): False,
    ('issue_updated_customer', 'customer'):        True,
    ('issue_updated_customer', 'custom'):          False,
    # verification_requested
    ('verification_requested', 'admin'):           True,
    ('verification_requested', 'director'):      True,
    ('verification_requested', 'inspector'):       False,
    ('verification_requested', 'project_manager'): False,
    ('verification_requested', 'customer'):        False,
    ('verification_requested', 'custom'):          False,
    # sla_alert (assignee + followers always notified implicitly)
    ('sla_alert',              'admin'):           True,
    ('sla_alert',              'director'):      False,
    ('sla_alert',              'inspector'):       False,
    ('sla_alert',              'project_manager'): False,
    ('sla_alert',              'customer'):        False,
    ('sla_alert',              'custom'):          False,
}


class NotificationMatrix(db.Model):
    """Admin-controlled per-event notification routing."""

    __tablename__ = 'notification_matrix'

    id           = db.Column(db.Integer, primary_key=True)
    event_type   = db.Column(db.String(50), nullable=False)
    role_key     = db.Column(db.String(30), nullable=False)
    enabled      = db.Column(db.Boolean,    nullable=False, default=True)
    custom_emails = db.Column(db.Text,      nullable=True)   # JSON list, only used when role_key='custom'

    __table_args__ = (
        db.UniqueConstraint('event_type', 'role_key', name='uq_notif_matrix_event_role'),
    )

    def get_custom_emails(self):
        """Return custom_emails as a Python list."""
        if not self.custom_emails:
            return []
        try:
            result = json.loads(self.custom_emails)
            return [e.strip() for e in result if isinstance(e, str) and e.strip()]
        except (json.JSONDecodeError, TypeError):
            return []

    def __repr__(self):
        return f'<NotificationMatrix {self.event_type} / {self.role_key} enabled={self.enabled}>'


def get_matrix_row(event_type: str, role_key: str) -> NotificationMatrix:
    """
    Return the matrix row for (event_type, role_key), creating it from
    defaults if it doesn't exist yet.  Safe to call without seeding.
    """
    row = NotificationMatrix.query.filter_by(
        event_type=event_type, role_key=role_key
    ).first()
    if row is None:
        default = MATRIX_DEFAULTS.get((event_type, role_key), False)
        row = NotificationMatrix(
            event_type=event_type,
            role_key=role_key,
            enabled=default,
        )
        db.session.add(row)
        db.session.flush()
    return row


def is_enabled(event_type: str, role_key: str) -> bool:
    """Return True if the matrix enables notifications for this event/role pair."""
    row = NotificationMatrix.query.filter_by(
        event_type=event_type, role_key=role_key
    ).first()
    if row is None:
        return MATRIX_DEFAULTS.get((event_type, role_key), False)
    return row.enabled


def get_custom_emails_for(event_type: str) -> list:
    """Return the custom email list for this event type."""
    row = NotificationMatrix.query.filter_by(
        event_type=event_type, role_key='custom'
    ).first()
    if row is None:
        return []
    return row.get_custom_emails()
