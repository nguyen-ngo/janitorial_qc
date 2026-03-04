from app import db
from app.utils.time_utils import now_eastern


# ── Event type constants ───────────────────────────────────────────────────────
# These are the canonical keys used across the preference system.
# Every call to notify() should pass one of these as event_type.

EVENT_ISSUE_ASSIGNED    = 'issue_assigned'
EVENT_ISSUE_STATUS      = 'issue_status'
EVENT_ISSUE_COMMENT     = 'issue_comment'
EVENT_ISSUE_FOLLOW      = 'issue_follow_update'
EVENT_INSPECTION_DONE   = 'inspection_completed'
EVENT_SLA_ALERT         = 'sla_alert'

ALL_EVENT_TYPES = {
    EVENT_ISSUE_ASSIGNED:  'Issue assigned to me',
    EVENT_ISSUE_STATUS:    'Issue status changed',
    EVENT_ISSUE_COMMENT:   'New comment on issue',
    EVENT_ISSUE_FOLLOW:    'Updates on followed issues',
    EVENT_INSPECTION_DONE: 'Inspection completed',
    EVENT_SLA_ALERT:       'SLA at-risk / breached alerts',
}


class Notification(db.Model):
    """Stores in-app notifications for users.

    Each notification is tied to a single recipient and optionally linked to
    either an Issue or an Inspection so the UI can build a direct link.
    """
    __tablename__ = 'notifications'

    id            = db.Column(db.Integer, primary_key=True)
    user_id       = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    title         = db.Column(db.String(255), nullable=False)
    body          = db.Column(db.Text, nullable=False)
    link          = db.Column(db.String(512))
    is_read       = db.Column(db.Boolean, default=False, nullable=False)
    created_at    = db.Column(db.DateTime, default=now_eastern, nullable=False)

    # Optional FK references — only one will be populated at a time
    issue_id      = db.Column(db.Integer, db.ForeignKey('issues.id',      ondelete='CASCADE'), nullable=True)
    inspection_id = db.Column(db.Integer, db.ForeignKey('inspections.id', ondelete='CASCADE'), nullable=True)

    # Digest tracking: set to True when created, cleared after digest email sent
    digest_pending = db.Column(db.Boolean, default=False, nullable=False, index=True)

    recipient = db.relationship('User', foreign_keys=[user_id], backref='notifications')

    def __repr__(self):
        return f'<Notification {self.id} user={self.user_id} read={self.is_read}>'


class NotificationPreference(db.Model):
    """Per-user, per-event notification preferences.

    One row per (user_id, event_type) combination.
    If no row exists for a user+event, defaults apply (email on, no digest).
    """
    __tablename__ = 'notification_preferences'

    id            = db.Column(db.Integer, primary_key=True)
    user_id       = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'),
                              nullable=False, index=True)
    event_type    = db.Column(db.String(50), nullable=False)
    email_enabled = db.Column(db.Boolean, default=True,  nullable=False)
    digest_mode   = db.Column(db.Boolean, default=False, nullable=False)
    # digest_frequency: 'hourly' or 'daily' — only relevant when digest_mode is True
    digest_frequency = db.Column(db.String(10), default='daily', nullable=False)

    __table_args__ = (
        db.UniqueConstraint('user_id', 'event_type', name='uq_notif_pref_user_event'),
    )

    user = db.relationship('User', foreign_keys=[user_id],
                           backref=db.backref('notification_preferences', lazy='dynamic'))

    def __repr__(self):
        return (f'<NotificationPreference user={self.user_id} '
                f'event={self.event_type} email={self.email_enabled} digest={self.digest_mode}>')