from app import db
from app.utils.time_utils import now_eastern


class IssueComment(db.Model):
    __tablename__ = 'issue_comments'

    id            = db.Column(db.Integer, primary_key=True)
    issue_id      = db.Column(db.Integer, db.ForeignKey('issues.id'), nullable=False)
    user_id       = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    status_at_time = db.Column(db.String(20))   # snapshot of issue status when comment was made
    body          = db.Column(db.Text, nullable=False)
    created_at    = db.Column(db.DateTime, default=now_eastern, nullable=False)

    # Relationships
    author = db.relationship('User', foreign_keys=[user_id])

    def __repr__(self):
        return f'<IssueComment {self.id} issue={self.issue_id}>'


# ── Issue Follower ─────────────────────────────────────────────────────────────
# Association table linking users who opt in to receive notifications
# for any updates on a specific issue.

class IssueFollower(db.Model):
    __tablename__ = 'issue_followers'

    id         = db.Column(db.Integer, primary_key=True)
    issue_id   = db.Column(db.Integer, db.ForeignKey('issues.id', ondelete='CASCADE'), nullable=False)
    user_id    = db.Column(db.Integer, db.ForeignKey('users.id',  ondelete='CASCADE'), nullable=False)
    created_at = db.Column(db.DateTime, default=now_eastern, nullable=False)

    __table_args__ = (
        db.UniqueConstraint('issue_id', 'user_id', name='uq_issue_follower'),
    )

    user  = db.relationship('User',  foreign_keys=[user_id])
    issue = db.relationship('Issue', foreign_keys=[issue_id], back_populates='followers')

    def __repr__(self):
        return f'<IssueFollower issue={self.issue_id} user={self.user_id}>'


class Issue(db.Model):
    __tablename__ = 'issues'

    id            = db.Column(db.Integer, primary_key=True)
    inspection_id = db.Column(db.Integer, db.ForeignKey('inspections.id'))
    area_id       = db.Column(db.Integer, db.ForeignKey('areas.id'), nullable=False)
    severity      = db.Column(db.Enum('low', 'medium', 'high', 'critical'), nullable=False)
    description   = db.Column(db.Text, nullable=False)
    photo_path    = db.Column(db.String(255))
    status        = db.Column(db.Enum('open', 'in_progress', 'resolved', 'pending_verification'), default='open')
    assigned_to   = db.Column(db.Integer, db.ForeignKey('users.id'))
    reported_at   = db.Column(db.DateTime, default=now_eastern)
    resolved_at   = db.Column(db.DateTime)
    result_notes  = db.Column(db.Text)
    result_photos = db.Column(db.JSON)   # list of relative paths e.g. ["uploads/issue_photos/abc.jpg"]

    # ── Resolution verification ──────────────────────────────────────────
    verified_by        = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    verified_at        = db.Column(db.DateTime, nullable=True)
    verification_note  = db.Column(db.Text, nullable=True)

    # Tracks which SLA alert level has already been notified so cron runs
    # don't fire duplicate notifications. Values: None / 'at_risk' / 'breached'
    sla_notified  = db.Column(db.String(10), nullable=True, default=None)

    # Relationships
    assigned_user  = db.relationship('User', foreign_keys=[assigned_to], backref='assigned_issues')
    verifier       = db.relationship('User', foreign_keys=[verified_by], backref='verified_issues')
    comments      = db.relationship('IssueComment', backref='issue', lazy='dynamic',
                                    order_by='IssueComment.created_at',
                                    cascade='all, delete-orphan')
    followers     = db.relationship('IssueFollower', back_populates='issue',
                                    cascade='all, delete-orphan', lazy='dynamic')

    def is_followed_by(self, user):
        """Return True if the given user is currently following this issue."""
        return self.followers.filter_by(user_id=user.id).first() is not None

    def __repr__(self):
        return f'<Issue {self.id} - {self.severity}>'