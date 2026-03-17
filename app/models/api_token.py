"""
app/models/api_token.py
-----------------------
Persistent storage for JWT refresh tokens and APNs device tokens.

RefreshToken
    One row per active mobile session.  When the access token expires the
    app presents its refresh token here; a new access token is issued and
    the refresh token is rotated (old one deleted, new one inserted).
    Revocation is instant: delete the row.

DeviceToken
    One row per (user, device) pair.  Stores the APNs token so the server
    can push notifications to the device.  Updated on every app launch
    because APNs tokens can rotate.
"""

import secrets
from app import db
from app.utils.time_utils import now_eastern
from datetime import timedelta


class RefreshToken(db.Model):
    """
    Opaque refresh token stored server-side.

    The token value itself is a 64-character hex string generated with
    secrets.token_hex(32).  Only the SHA-256 hash is stored so that a DB
    breach does not expose live tokens.
    """
    __tablename__ = 'api_refresh_tokens'

    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(
        db.Integer,
        db.ForeignKey('users.id', ondelete='CASCADE'),
        nullable=False,
        index=True,
    )
    # SHA-256 hex digest of the raw token — never store the raw value
    token_hash = db.Column(db.String(64), nullable=False, unique=True, index=True)
    # Device identifier supplied by the app (UIDevice.identifierForVendor)
    device_id  = db.Column(db.String(64),  nullable=True)
    device_name = db.Column(db.String(100), nullable=True)   # e.g. "John's iPhone"
    created_at = db.Column(db.DateTime, nullable=False, default=now_eastern)
    expires_at = db.Column(db.DateTime, nullable=False)
    revoked    = db.Column(db.Boolean,  nullable=False, default=False)

    user = db.relationship('User', foreign_keys=[user_id],
                           backref=db.backref('refresh_tokens', lazy='dynamic',
                                              cascade='all, delete-orphan'))

    @classmethod
    def create_for(cls, user, device_id=None, device_name=None,
                   lifetime_days=30):
        """
        Generate a new refresh token, persist it, and return the raw token
        string (only time it is ever available in plaintext).
        """
        import hashlib
        raw   = secrets.token_hex(32)           # 64-char hex, 256 bits entropy
        hashed = hashlib.sha256(raw.encode()).hexdigest()
        token  = cls(
            user_id     = user.id,
            token_hash  = hashed,
            device_id   = device_id,
            device_name = device_name,
            expires_at  = now_eastern() + timedelta(days=lifetime_days),
        )
        db.session.add(token)
        return raw, token   # caller must db.session.commit()

    @classmethod
    def verify(cls, raw_token):
        """
        Look up a refresh token by its raw value.
        Returns the RefreshToken row if valid and unexpired, else None.
        """
        import hashlib
        hashed = hashlib.sha256(raw_token.encode()).hexdigest()
        row = cls.query.filter_by(token_hash=hashed, revoked=False).first()
        if row is None:
            return None
        if row.expires_at < now_eastern():
            return None
        return row

    def revoke(self):
        self.revoked = True

    def __repr__(self):
        return f'<RefreshToken user={self.user_id} device={self.device_id}>'


class DeviceToken(db.Model):
    """
    APNs device token for push notification delivery.

    One row per (user, device_id) pair — upserted on every app launch.
    The apns_token is the hex string returned by the iOS SDK.
    """
    __tablename__ = 'api_device_tokens'

    id          = db.Column(db.Integer, primary_key=True)
    user_id     = db.Column(
        db.Integer,
        db.ForeignKey('users.id', ondelete='CASCADE'),
        nullable=False,
        index=True,
    )
    device_id   = db.Column(db.String(64),  nullable=False)   # UIDevice.identifierForVendor
    apns_token  = db.Column(db.String(200), nullable=False)
    device_name = db.Column(db.String(100), nullable=True)
    app_version = db.Column(db.String(20),  nullable=True)
    registered_at = db.Column(db.DateTime, nullable=False, default=now_eastern)

    __table_args__ = (
        db.UniqueConstraint('user_id', 'device_id', name='uq_device_token_user_device'),
    )

    user = db.relationship('User', foreign_keys=[user_id],
                           backref=db.backref('device_tokens', lazy='dynamic',
                                              cascade='all, delete-orphan'))

    def __repr__(self):
        return f'<DeviceToken user={self.user_id} device={self.device_id}>'
