from app import db
from app.utils.time_utils import now_eastern


class AuditLog(db.Model):
    """
    Persistent record of every create / edit / delete action performed by
    a user.  Entries are immutable once written — never updated or deleted
    through the application.
    """
    __tablename__ = 'audit_logs'

    id           = db.Column(db.Integer, primary_key=True)
    # Who performed the action (NULL-safe: user may be deleted later)
    user_id      = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    username     = db.Column(db.String(100), nullable=False)          # snapshot at time of action
    user_role    = db.Column(db.String(20),  nullable=False)          # snapshot at time of action
    # What happened
    action       = db.Column(db.String(50),  nullable=False)          # CREATE / UPDATE / DELETE / LOGIN / LOGOUT / EXPORT
    entity_type  = db.Column(db.String(50),  nullable=False)          # User / Facility / Area / Template / Inspection / Issue / …
    entity_id    = db.Column(db.Integer,     nullable=True)           # PK of the affected record (NULL for bulk ops)
    entity_label = db.Column(db.String(255), nullable=True)           # Human-readable identifier snapshot
    # Extra context stored as free-text (key=value pairs, comma-separated)
    details      = db.Column(db.Text,        nullable=True)
    # When
    created_at   = db.Column(db.DateTime, default=now_eastern, nullable=False, index=True)
    # Request context
    ip_address   = db.Column(db.String(45),  nullable=True)           # supports IPv6

    # Relationship — may be None if user was deleted
    user = db.relationship('User', foreign_keys=[user_id])

    def __repr__(self):
        return (f'<AuditLog {self.id} {self.action} {self.entity_type}:{self.entity_id}'
                f' by {self.username}>')
