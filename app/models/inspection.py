from app import db
from datetime import datetime
import json


class InspectionTemplate(db.Model):
    __tablename__ = 'inspection_templates'

    id          = db.Column(db.Integer, primary_key=True)
    name        = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text)
    frequency   = db.Column(db.Enum('daily', 'weekly', 'monthly', 'quarterly'))
    created_by  = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)
    form_schema = db.Column(db.JSON, nullable=True)

    checklist_items = db.relationship('ChecklistItem', backref='template', lazy='dynamic', cascade='all, delete-orphan')
    inspections     = db.relationship('Inspection', backref='template', lazy='dynamic')

    def get_form_schema(self):
        if self.form_schema is None:
            return []
        if isinstance(self.form_schema, str):
            try:
                return json.loads(self.form_schema)
            except (json.JSONDecodeError, TypeError):
                return []
        return self.form_schema

    def __repr__(self):
        return f'<InspectionTemplate {self.name}>'


class ChecklistItem(db.Model):
    __tablename__ = 'checklist_items'

    id               = db.Column(db.Integer, primary_key=True)
    template_id      = db.Column(db.Integer, db.ForeignKey('inspection_templates.id'), nullable=False)
    category         = db.Column(db.String(100))
    item_description = db.Column(db.Text, nullable=False)
    scoring_type     = db.Column(db.Enum('pass_fail', 'rating_5', 'rating_10'))
    weight           = db.Column(db.Numeric(3, 2), default=1.00)
    requires_photo   = db.Column(db.Boolean, default=False)
    display_order    = db.Column(db.Integer)

    results = db.relationship('InspectionResult', backref='checklist_item', lazy='dynamic')

    def __repr__(self):
        return f'<ChecklistItem {self.item_description[:30]}>'


class Inspection(db.Model):
    __tablename__ = 'inspections'

    id              = db.Column(db.Integer, primary_key=True)
    template_id     = db.Column(db.Integer, db.ForeignKey('inspection_templates.id'), nullable=False)
    facility_id     = db.Column(db.Integer, db.ForeignKey('facilities.id'), nullable=False)
    area_id         = db.Column(db.Integer, db.ForeignKey('areas.id'))
    inspector_id    = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    inspection_date = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    overall_score   = db.Column(db.Numeric(5, 2))
    status          = db.Column(db.Enum('in_progress', 'completed', 'flagged'), default='in_progress')
    notes           = db.Column(db.Text)          # inspector free-text notes
    form_data       = db.Column(db.JSON)          # filled form field responses {field_id: value}
    completed_at    = db.Column(db.DateTime)

    results = db.relationship('InspectionResult', backref='inspection', lazy='dynamic', cascade='all, delete-orphan')
    issues  = db.relationship('Issue', backref='inspection', lazy='dynamic')

    def __repr__(self):
        return f'<Inspection {self.id} - {self.inspection_date}>'


class InspectionResult(db.Model):
    __tablename__ = 'inspection_results'

    id                = db.Column(db.Integer, primary_key=True)
    inspection_id     = db.Column(db.Integer, db.ForeignKey('inspections.id'), nullable=False)
    checklist_item_id = db.Column(db.Integer, db.ForeignKey('checklist_items.id'), nullable=False)
    score             = db.Column(db.Numeric(5, 2))
    passed            = db.Column(db.Boolean)
    comments          = db.Column(db.Text)
    photo_path        = db.Column(db.String(255))

    def __repr__(self):
        return f'<InspectionResult {self.id}>'
