"""
app/api/decorators.py
---------------------
Request-level guards for all /api/v1/ endpoints.

@jwt_required
    Validates the Bearer token in the Authorization header.
    On success, sets flask.g.api_user to the authenticated User instance
    so any route can access it without a second DB query.

@api_role_required(*roles)
    Must be applied AFTER @jwt_required.
    Rejects callers whose role is not in the allowed set.

Usage
-----
    @bp.route('/inspections')
    @jwt_required
    def list_inspections():
        user = g.api_user
        ...

    @bp.route('/admin/users')
    @jwt_required
    @api_role_required('admin')
    def admin_only():
        ...
"""

import logging
from functools import wraps

from flask import request, g, abort

from app.api.jwt_utils import decode_access_token
from app.api.errors import api_error
from app.models.user import User

logger = logging.getLogger(__name__)


def jwt_required(f):
    """
    Validate the JWT Bearer token and load the user into flask.g.api_user.

    Returns 401 if:
      - Authorization header is missing or malformed
      - Token is expired or invalid
      - User referenced by the token no longer exists
      - User account has been disabled (active=False)
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get('Authorization', '')
        if not auth_header.startswith('Bearer '):
            return api_error('Missing or malformed Authorization header', 401)

        raw_token = auth_header[len('Bearer '):]
        payload   = decode_access_token(raw_token)

        if payload is None:
            return api_error('Access token is invalid or expired', 401)

        user_id = int(payload.get('sub', 0))
        user    = User.query.get(user_id)

        if user is None:
            return api_error('User not found', 401)

        if not user.active:
            return api_error('Account is disabled', 401)

        # Make the user available to the route without re-querying
        g.api_user = user
        return f(*args, **kwargs)

    return decorated


def api_role_required(*roles):
    """
    Restrict an endpoint to users whose role is in the provided list.

    Must be stacked BELOW @jwt_required so that g.api_user is already set.

    Example
    -------
        @jwt_required
        @api_role_required('admin', 'supervisor')
        def supervisor_only_route():
            ...
    """
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            user = getattr(g, 'api_user', None)
            if user is None:
                # Defensive: jwt_required should always run first
                return api_error('Authentication required', 401)
            if user.role not in roles:
                logger.warning(
                    'API role denied | user=%s role=%s required=%s endpoint=%s',
                    user.username, user.role, roles, request.endpoint,
                )
                return api_error('Insufficient permissions', 403)
            return f(*args, **kwargs)
        return decorated
    return decorator
