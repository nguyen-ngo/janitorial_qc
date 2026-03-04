"""
app/models/scheduled_report.py
-------------------------------
Stores the configuration for automated scheduled report emails.
"""

from app import db
from app.utils.time_utils import now_eastern


class ScheduledReport(db.Model):
    """Configuration record for a recurring emailed report.

    report_type options:
      summary   — overall KPI digest (inspections + issues)
      facility  — single-facility scorecard
      issues    — open/in-progress issues list

    frequency options: daily | weekly | monthly

    recipients: JSON list of email address strings, e.g.
        ["manager@acme.com", "client@acme.com"]

    include_pdf / include_csv: attach respective exports to the email.
    """
    __tablename__ = 'scheduled_reports'

    id           = db.Column(db.Integer, primary_key=True)
    name         = db.Column(db.String(255), nullable=False)
    report_type  = db.Column(
        db.Enum('summary', 'facility', 'issues'),
        nullable=False, default='summary'
    )
    frequency    = db.Column(
        db.Enum('daily', 'weekly', 'monthly'),
        nullable=False
    )
    facility_id  = db.Column(
        db.Integer, db.ForeignKey('facilities.id', ondelete='SET NULL'),
        nullable=True
    )
    recipients   = db.Column(db.JSON, nullable=False, default=list)
    include_pdf  = db.Column(db.Boolean, nullable=False, default=False)
    include_csv  = db.Column(db.Boolean, nullable=False, default=False)
    active       = db.Column(db.Boolean, nullable=False, default=True)
    created_by   = db.Column(
        db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'),
        nullable=True
    )
    created_at   = db.Column(db.DateTime, nullable=False, default=now_eastern)
    last_sent_at = db.Column(db.DateTime, nullable=True)
    next_send_at = db.Column(db.DateTime, nullable=True)

    # Relationships
    facility = db.relationship('Facility', foreign_keys=[facility_id])
    creator  = db.relationship('User',     foreign_keys=[created_by])

    def recipient_list(self):
        """Return recipients as a Python list (safe even if stored as string)."""
        if isinstance(self.recipients, list):
            return self.recipients
        import json
        try:
            return json.loads(self.recipients)
        except Exception:
            return []

    def __repr__(self):
        return f'<ScheduledReport {self.id} {self.name!r} {self.frequency}>'
