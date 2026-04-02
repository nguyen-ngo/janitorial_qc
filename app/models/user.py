from app import db, login_manager
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from app.utils.time_utils import now_eastern

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

class User(UserMixin, db.Model):
    __tablename__ = 'users'

    id            = db.Column(db.Integer, primary_key=True)
    username      = db.Column(db.String(100), unique=True, nullable=False, index=True)
    full_name     = db.Column(db.String(150), nullable=True)
    email         = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role          = db.Column(
        db.Enum('admin', 'supervisor', 'inspector', 'project_manager', 'customer'),
        nullable=False
    )
    created_at    = db.Column(db.DateTime, default=now_eastern)
    active        = db.Column(db.Boolean, default=True, nullable=False)

    # ── Customer password-setup workflow ──────────────────────────────────
    # password_set: False for newly created customer accounts until they
    #               complete the set-password flow via emailed link.
    #               Always True for internal users created via UserForm.
    password_set                = db.Column(db.Boolean, nullable=False, default=True)
    set_password_token          = db.Column(db.String(64), nullable=True, index=True)
    set_password_token_expires  = db.Column(db.DateTime, nullable=True)

    # Relationships
    inspections = db.relationship('Inspection', backref='inspector', lazy='dynamic')

    # ── Flask-Login integration ────────────────────────────────────────────
    # Override UserMixin.is_active so that disabled accounts are rejected
    # automatically by login_required and login_user() without any extra code.
    @property
    def is_active(self):
        return self.active

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def display_name(self):
        """Return full name if set, otherwise fall back to username."""
        return self.full_name.strip() if self.full_name and self.full_name.strip() else self.username

    def generate_set_password_token(self, expires_hours=72):
        """Create a one-time set-password token valid for `expires_hours` hours."""
        import secrets
        from datetime import timedelta
        self.set_password_token         = secrets.token_hex(32)   # 64 hex chars
        self.set_password_token_expires = now_eastern() + timedelta(hours=expires_hours)
        return self.set_password_token

    def clear_set_password_token(self):
        """Invalidate the token after use."""
        self.set_password_token         = None
        self.set_password_token_expires = None

    @staticmethod
    def verify_set_password_token(token):
        """Return the User whose token matches, or None if invalid/expired."""
        if not token:
            return None
        user = User.query.filter_by(set_password_token=token).first()
        if user is None:
            return None
        if user.set_password_token_expires is None:
            return None
        if now_eastern() > user.set_password_token_expires:
            return None
        return user

    def __repr__(self):
        return f'<User {self.username}>'
