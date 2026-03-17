"""
app/api/auth.py
---------------
Authentication endpoints for the JQC mobile app.

POST /api/v1/auth/login
    Accepts username + password.
    Returns a short-lived access token (JWT) and a long-lived refresh token
    (opaque, stored in DB).  The app stores both in the iOS Keychain.

POST /api/v1/auth/refresh
    Accepts a refresh token.
    Returns a new access token.  The refresh token is rotated — the old one
    is revoked and a new one is issued, preventing replay attacks.

POST /api/v1/auth/logout
    Accepts a refresh token.
    Revokes it so it can no longer be used to issue new access tokens.
    The app should discard both tokens from the Keychain after this call.

POST /api/v1/devices/register
    Registers or updates the APNs device token for push notifications.
    Called on every app launch after the user has already authenticated.
    Requires a valid access token (JWT).

GET /api/v1/auth/me
    Returns the current user's profile from the access token.
    Useful for the app to verify the token is still valid on launch.
"""

import logging

from flask import Blueprint, request, g
from app import db
from app.models.user import User
from app.models.api_token import RefreshToken, DeviceToken
from app.api.errors import api_ok, api_error
from app.api.jwt_utils import generate_access_token
from app.api.decorators import jwt_required
from app.utils.audit import log_action, ACTION_LOGIN, ACTION_LOGOUT
from app.utils.time_utils import now_eastern

logger = logging.getLogger(__name__)

bp = Blueprint('api_auth', __name__)


def _user_payload(user: User) -> dict:
    """Serialize a User to the dict returned in auth responses."""
    return {
        'id':         user.id,
        'username':   user.username,
        'email':      user.email,
        'role':       user.role,
        'created_at': user.created_at.isoformat() if user.created_at else None,
    }


# ── Login ─────────────────────────────────────────────────────────────────────

@bp.route('/auth/login', methods=['POST'])
def login():
    """
    Authenticate with username + password.

    Request JSON
    ------------
    {
        "username":    "john",
        "password":    "secret",
        "device_id":   "A1B2C3D4...",   // UIDevice.identifierForVendor (optional)
        "device_name": "John's iPhone"  // (optional)
    }

    Response 200
    ------------
    {
        "ok": true,
        "data": {
            "access_token":  "<jwt>",
            "refresh_token": "<opaque_hex>",
            "token_type":    "Bearer",
            "expires_in":    3600,
            "user": { id, username, email, role, created_at }
        }
    }
    """
    data = request.get_json(silent=True) or {}

    username = (data.get('username') or '').strip()
    password = data.get('password') or ''

    if not username or not password:
        return api_error('username and password are required', 400)

    user = User.query.filter_by(username=username).first()

    # Generic message — never reveal whether the username exists
    if user is None or not user.check_password(password):
        logger.warning('API login failed | username=%s | ip=%s',
                       username, request.remote_addr)
        return api_error('Invalid credentials', 401)

    if not user.active:
        return api_error('Account is disabled. Please contact an administrator.', 401)

    device_id   = (data.get('device_id')   or '')[:64]  or None
    device_name = (data.get('device_name') or '')[:100] or None

    # Issue tokens
    access_token          = generate_access_token(user)
    raw_refresh, rt_row   = RefreshToken.create_for(
        user,
        device_id=device_id,
        device_name=device_name,
    )
    db.session.commit()

    log_action(ACTION_LOGIN, 'User', user.id, user.username,
               f'source=mobile_api; device_id={device_id}')

    logger.info('API LOGIN | user=%s | role=%s | device_id=%s',
                user.username, user.role, device_id)

    return api_ok({
        'access_token':  access_token,
        'refresh_token': raw_refresh,
        'token_type':    'Bearer',
        'expires_in':    3600,   # seconds — matches ACCESS_TOKEN_LIFETIME_MINUTES * 60
        'user':          _user_payload(user),
    })


# ── Refresh ───────────────────────────────────────────────────────────────────

@bp.route('/auth/refresh', methods=['POST'])
def refresh():
    """
    Exchange a valid refresh token for a new access token.

    The refresh token is rotated on every call — the submitted token is
    revoked and a brand new one is issued.  This limits the damage window
    if a token is ever stolen.

    Request JSON
    ------------
    { "refresh_token": "<opaque_hex>" }

    Response 200
    ------------
    {
        "ok": true,
        "data": {
            "access_token":  "<new_jwt>",
            "refresh_token": "<new_opaque_hex>",
            "token_type":    "Bearer",
            "expires_in":    3600
        }
    }
    """
    data          = request.get_json(silent=True) or {}
    raw_token     = (data.get('refresh_token') or '').strip()

    if not raw_token:
        return api_error('refresh_token is required', 400)

    rt_row = RefreshToken.verify(raw_token)
    if rt_row is None:
        logger.warning('API refresh rejected | invalid/expired token | ip=%s',
                       request.remote_addr)
        return api_error('Refresh token is invalid or expired', 401)

    user = User.query.get(rt_row.user_id)
    if user is None or not user.active:
        rt_row.revoke()
        db.session.commit()
        return api_error('Account not available', 401)

    # Rotate: revoke old token, issue new pair
    device_id   = rt_row.device_id
    device_name = rt_row.device_name
    rt_row.revoke()

    new_access              = generate_access_token(user)
    new_raw_refresh, new_rt = RefreshToken.create_for(
        user,
        device_id=device_id,
        device_name=device_name,
    )
    db.session.commit()

    logger.info('API TOKEN REFRESH | user=%s | device_id=%s',
                user.username, device_id)

    return api_ok({
        'access_token':  new_access,
        'refresh_token': new_raw_refresh,
        'token_type':    'Bearer',
        'expires_in':    3600,
    })


# ── Logout ────────────────────────────────────────────────────────────────────

@bp.route('/auth/logout', methods=['POST'])
@jwt_required
def logout():
    """
    Revoke the current session's refresh token.

    The app should call this when the user taps "Log out" and then discard
    both the access token and refresh token from the Keychain.

    Request JSON
    ------------
    { "refresh_token": "<opaque_hex>" }

    Response 200
    ------------
    { "ok": true, "data": { "message": "Logged out" } }
    """
    data      = request.get_json(silent=True) or {}
    raw_token = (data.get('refresh_token') or '').strip()

    if raw_token:
        rt_row = RefreshToken.verify(raw_token)
        if rt_row and rt_row.user_id == g.api_user.id:
            rt_row.revoke()
            db.session.commit()

    log_action(ACTION_LOGOUT, 'User', g.api_user.id, g.api_user.username,
               'source=mobile_api')
    logger.info('API LOGOUT | user=%s', g.api_user.username)

    return api_ok({'message': 'Logged out successfully'})


# ── Current user ──────────────────────────────────────────────────────────────

@bp.route('/auth/me', methods=['GET'])
@jwt_required
def me():
    """
    Return the authenticated user's profile.

    Called by the app on launch to verify the stored access token is still
    valid and to refresh the local user record.

    Response 200
    ------------
    { "ok": true, "data": { "user": { id, username, email, role, ... } } }
    """
    return api_ok({'user': _user_payload(g.api_user)})


# ── Device token registration ─────────────────────────────────────────────────

@bp.route('/devices/register', methods=['POST'])
@jwt_required
def register_device():
    """
    Register or update the APNs device token for the authenticated user.

    Called on every app launch after authentication so the server always
    has the current token (APNs rotates tokens periodically).

    Request JSON
    ------------
    {
        "device_id":   "<UIDevice.identifierForVendor>",
        "apns_token":  "<hex_string_from_didRegisterForRemoteNotifications>",
        "device_name": "John's iPhone",   // optional
        "app_version": "1.0.3"            // optional
    }

    Response 200
    ------------
    { "ok": true, "data": { "registered": true } }
    """
    data        = request.get_json(silent=True) or {}
    device_id   = (data.get('device_id')   or '').strip()[:64]
    apns_token  = (data.get('apns_token')  or '').strip()[:200]
    device_name = (data.get('device_name') or '').strip()[:100] or None
    app_version = (data.get('app_version') or '').strip()[:20]  or None

    if not device_id or not apns_token:
        return api_error('device_id and apns_token are required', 400)

    # Upsert: update existing row or insert new one
    existing = DeviceToken.query.filter_by(
        user_id=g.api_user.id,
        device_id=device_id,
    ).first()

    if existing:
        existing.apns_token   = apns_token
        existing.device_name  = device_name
        existing.app_version  = app_version
        existing.registered_at = now_eastern()
    else:
        db.session.add(DeviceToken(
            user_id     = g.api_user.id,
            device_id   = device_id,
            apns_token  = apns_token,
            device_name = device_name,
            app_version = app_version,
        ))

    db.session.commit()
    logger.info('API DEVICE REGISTERED | user=%s | device_id=%s | apns_token=...%s',
                g.api_user.username, device_id, apns_token[-6:])

    return api_ok({'registered': True})