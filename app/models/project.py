from app import db
from app.utils.time_utils import now_eastern


class Project(db.Model):
    """Top-level grouping that contains one or more Facilities.

    Each project may have an assigned project_manager (User) and one or more
    customer users linked via CustomerAssignment.
    """
    __tablename__ = 'projects'

    id             = db.Column(db.Integer, primary_key=True)
    name           = db.Column(db.String(255), nullable=False)
    description    = db.Column(db.Text)
    project_manager_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    active         = db.Column(db.Boolean, default=True, nullable=False)
    created_at     = db.Column(db.DateTime, default=now_eastern, nullable=False)

    # Relationships
    project_manager   = db.relationship('User', foreign_keys=[project_manager_id],
                                        backref='managed_projects')
    facilities        = db.relationship('Facility', backref='project', lazy='dynamic')
    customer_assignments = db.relationship('CustomerAssignment', back_populates='project',
                                           cascade='all, delete-orphan', lazy='dynamic')

    def __repr__(self):
        return f'<Project {self.name}>'


class CustomerAssignment(db.Model):
    """Links a customer-role User to a Project and/or a specific Facility.

    - project_id only  → customer can view all facilities within that project
    - project_id + facility_id → customer is scoped to that specific facility
    """
    __tablename__ = 'customer_assignments'

    id          = db.Column(db.Integer, primary_key=True)
    user_id     = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'),
                            nullable=False, index=True)
    project_id  = db.Column(db.Integer, db.ForeignKey('projects.id', ondelete='CASCADE'),
                            nullable=False, index=True)
    facility_id = db.Column(db.Integer, db.ForeignKey('facilities.id', ondelete='CASCADE'),
                            nullable=True, index=True)
    created_at  = db.Column(db.DateTime, default=now_eastern, nullable=False)

    __table_args__ = (
        db.UniqueConstraint('user_id', 'project_id', 'facility_id',
                            name='uq_customer_assignment'),
    )

    # Relationships
    user     = db.relationship('User', foreign_keys=[user_id],
                               backref=db.backref('customer_assignments', lazy='dynamic'))
    project  = db.relationship('Project', foreign_keys=[project_id],
                               back_populates='customer_assignments')
    facility = db.relationship('Facility', foreign_keys=[facility_id],
                               backref=db.backref('customer_assignments', lazy='dynamic'))

    def __repr__(self):
        return (f'<CustomerAssignment user={self.user_id} '
                f'project={self.project_id} facility={self.facility_id}>')
