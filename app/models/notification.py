from app import db
from app.utils.time_utils import now_eastern


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
    link          = db.Column(db.String(512))          # URL the bell-click should navigate to
    is_read       = db.Column(db.Boolean, default=False, nullable=False)
    created_at    = db.Column(db.DateTime, default=now_eastern, nullable=False)

    # Optional FK references — only one will be populated at a time
    issue_id      = db.Column(db.Integer, db.ForeignKey('issues.id',       ondelete='CASCADE'), nullable=True)
    inspection_id = db.Column(db.Integer, db.ForeignKey('inspections.id',  ondelete='CASCADE'), nullable=True)

    recipient = db.relationship('User', foreign_keys=[user_id], backref='notifications')

    def __repr__(self):
        return f'<Notification {self.id} user={self.user_id} read={self.is_read}>'