"""
app/api/jwt_utils.py
--------------------
Thin wrappers around PyJWT for signing and verifying access tokens.

Access tokens are short-lived JWTs (default 60 minutes) signed with
HMAC-SHA256 using the app's SECRET_KEY.  They carry only the minimum
claims needed to identify the caller:

    {
        "sub":  "42",          # user.id as string
        "role": "inspector",   # user.role
        "iat":  1710000000,    # issued-at  (UTC epoch)
        "exp":  1710003600,    # expiry     (UTC epoch, 60 min later)
    }

Refresh tokens are opaque random strings stored in the DB
(see app/models/api_token.py).  This module only handles JWTs.
"""

import logging
from datetime import datetime, timezone, timedelta

import jwt
from flask import current_app

logger = logging.getLogger(__name__)

ACCESS_TOKEN_LIFETIME_MINUTES = 60


def _secret():
    return current_app.config['SECRET_KEY']


def generate_access_token(user, lifetime_minutes: int = ACCESS_TOKEN_LIFETIME_MINUTES) -> str:
    """
    Create and sign a new access token for the given user.

    Parameters
    ----------
    user             : User ORM instance
    lifetime_minutes : Token validity window (default 60 min)

    Returns
    -------
    str
        Signed JWT string ready to include in Authorization header.
    """
    now = datetime.now(timezone.utc)
    payload = {
        'sub':  str(user.id),
        'role': user.role,
        'iat':  now,
        'exp':  now + timedelta(minutes=lifetime_minutes),
    }
    return jwt.encode(payload, _secret(), algorithm='HS256')


def decode_access_token(token: str) -> dict | None:
    """
    Decode and verify a JWT access token.

    Returns the payload dict on success, or None if the token is invalid,
    expired, or tampered with.  Logs the failure reason at DEBUG level.
    """
    try:
        return jwt.decode(token, _secret(), algorithms=['HS256'])
    except jwt.ExpiredSignatureError:
        logger.debug('JWT decode failed: token expired')
        return None
    except jwt.InvalidTokenError as exc:
        logger.debug('JWT decode failed: %s', exc)
        return None
