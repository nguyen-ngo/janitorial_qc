from app import db
from datetime import datetime


class IssueComment(db.Model):
    __tablename__ = 'issue_comments'

    id            = db.Column(db.Integer, primary_key=True)
    issue_id      = db.Column(db.Integer, db.ForeignKey('issues.id'), nullable=False)
    user_id       = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    status_at_time = db.Column(db.String(20))   # snapshot of issue status when comment was made
    body          = db.Column(db.Text, nullable=False)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    author = db.relationship('User', foreign_keys=[user_id])

    def __repr__(self):
        return f'<IssueComment {self.id} issue={self.issue_id}>'


class Issue(db.Model):
    __tablename__ = 'issues'

    id            = db.Column(db.Integer, primary_key=True)
    inspection_id = db.Column(db.Integer, db.ForeignKey('inspections.id'))
    area_id       = db.Column(db.Integer, db.ForeignKey('areas.id'), nullable=False)
    severity      = db.Column(db.Enum('low', 'medium', 'high', 'critical'), nullable=False)
    description   = db.Column(db.Text, nullable=False)
    photo_path    = db.Column(db.String(255))
    status        = db.Column(db.Enum('open', 'in_progress', 'resolved'), default='open')
    assigned_to   = db.Column(db.Integer, db.ForeignKey('users.id'))
    reported_at   = db.Column(db.DateTime, default=datetime.utcnow)
    resolved_at   = db.Column(db.DateTime)
    result_notes  = db.Column(db.Text)
    result_photos = db.Column(db.JSON)   # list of relative paths e.g. ["uploads/issue_photos/abc.jpg"]

    # Relationships
    assigned_user = db.relationship('User', foreign_keys=[assigned_to], backref='assigned_issues')
    comments      = db.relationship('IssueComment', backref='issue', lazy='dynamic',
                                    order_by='IssueComment.created_at',
                                    cascade='all, delete-orphan')

    def __repr__(self):
        return f'<Issue {self.id} - {self.severity}>'