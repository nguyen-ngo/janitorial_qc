"""
app/api/__init__.py
-------------------
Registers the /api/v1 blueprint group.

All mobile API routes live under the prefix /api/v1/.
This module is imported once from app/__init__.py — see the integration
instructions at the bottom of this file.

Blueprint layout
----------------
/api/v1/auth/login          → api_auth.login
/api/v1/auth/refresh        → api_auth.refresh
/api/v1/auth/logout         → api_auth.logout
/api/v1/auth/me             → api_auth.me
/api/v1/devices/register    → api_auth.register_device

Future phases will add:
/api/v1/facilities          → api_facilities.*
/api/v1/inspections         → api_inspections.*
/api/v1/issues              → api_issues.*
/api/v1/notifications       → api_notifications.*
/api/v1/photos/upload       → api_photos.*
"""

from flask import Blueprint
from app.api.errors import register_error_handlers

# Parent blueprint — all sub-blueprints registered under this prefix
api_bp = Blueprint('api', __name__, url_prefix='/api/v1')

# Register JSON error handlers so Flask exceptions within /api/v1/
# return JSON instead of HTML error pages.
register_error_handlers(api_bp)


def register_api(app):
    """
    Import and register all API sub-blueprints onto api_bp, then
    register api_bp on the Flask app.

    Called once from create_app() in app/__init__.py.
    """
    # ── Phase 1: Auth ────────────────────────────────────────────────────
    from app.api.auth import bp as auth_bp
    api_bp.register_blueprint(auth_bp)

    # ── Phase 2+: Additional blueprints registered here as phases complete
    # from app.api.facilities    import bp as facilities_bp
    # from app.api.inspections   import bp as inspections_bp
    # from app.api.issues        import bp as issues_bp
    # from app.api.notifications import bp as notifications_bp
    # from app.api.photos        import bp as photos_bp
    # api_bp.register_blueprint(facilities_bp)
    # api_bp.register_blueprint(inspections_bp)
    # api_bp.register_blueprint(issues_bp)
    # api_bp.register_blueprint(notifications_bp)
    # api_bp.register_blueprint(photos_bp)

    app.register_blueprint(api_bp)