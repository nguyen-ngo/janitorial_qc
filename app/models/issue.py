from app import db
from datetime import datetime

class Issue(db.Model):
    __tablename__ = 'issues'

    id = db.Column(db.Integer, primary_key=True)
    inspection_id = db.Column(db.Integer, db.ForeignKey('inspections.id'))
    area_id = db.Column(db.Integer, db.ForeignKey('areas.id'), nullable=False)
    severity = db.Column(db.Enum('low', 'medium', 'high', 'critical'), nullable=False)
    description = db.Column(db.Text, nullable=False)
    photo_path = db.Column(db.String(255))
    status = db.Column(db.Enum('open', 'in_progress', 'resolved'), default='open')
    assigned_to = db.Column(db.Integer, db.ForeignKey('users.id'))
    reported_at = db.Column(db.DateTime, default=datetime.utcnow)
    resolved_at = db.Column(db.DateTime)

    def __repr__(self):
        return f'<Issue {self.id} - {self.severity}>'
