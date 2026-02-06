from app import db
from datetime import datetime

class Facility(db.Model):
    __tablename__ = 'facilities'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    address = db.Column(db.Text)
    contact_person = db.Column(db.String(100))
    contact_phone = db.Column(db.String(20))
    active = db.Column(db.Boolean, default=True)

    # Relationships
    areas = db.relationship('Area', backref='facility', lazy='dynamic')
    inspections = db.relationship('Inspection', backref='facility', lazy='dynamic')

    def __repr__(self):
        return f'<Facility {self.name}>'

class Area(db.Model):
    __tablename__ = 'areas'

    id = db.Column(db.Integer, primary_key=True)
    facility_id = db.Column(db.Integer, db.ForeignKey('facilities.id'), nullable=False)
    name = db.Column(db.String(255), nullable=False)
    area_type = db.Column(db.String(50))

    # Relationships
    inspections = db.relationship('Inspection', backref='area', lazy='dynamic')
    issues = db.relationship('Issue', backref='area', lazy='dynamic')

    def __repr__(self):
        return f'<Area {self.name}>'
