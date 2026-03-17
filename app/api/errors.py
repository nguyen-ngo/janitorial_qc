"""
app/api/errors.py
-----------------
Consistent JSON error responses for every API endpoint.

Every response — success or failure — uses the same envelope:

    {
        "ok":    true | false,
        "data":  { ... } | null,
        "error": null | "Human-readable message"
    }

Usage
-----
    from app.api.errors import api_error, api_ok

    return api_ok({'inspection': {...}})
    return api_error('Inspection not found', 404)
"""

from flask import jsonify


def api_ok(data=None, status=200):
    """Return a successful JSON response."""
    return jsonify({
        'ok':    True,
        'data':  data,
        'error': None,
    }), status


def api_error(message: str, status: int = 400):
    """Return an error JSON response."""
    return jsonify({
        'ok':    False,
        'data':  None,
        'error': message,
    }), status


# ── Registered error handlers (attached to the api blueprint) ─────────────────

def register_error_handlers(bp):
    """
    Attach JSON error handlers to the given blueprint so that Flask
    exceptions (404, 405, 500, etc.) return JSON instead of HTML within
    the /api/v1/ prefix.
    """
    from werkzeug.exceptions import HTTPException

    @bp.errorhandler(400)
    def bad_request(e):
        return api_error(str(e.description) if hasattr(e, 'description') else 'Bad request', 400)

    @bp.errorhandler(401)
    def unauthorized(e):
        return api_error('Authentication required', 401)

    @bp.errorhandler(403)
    def forbidden(e):
        return api_error('Access denied', 403)

    @bp.errorhandler(404)
    def not_found(e):
        return api_error('Resource not found', 404)

    @bp.errorhandler(405)
    def method_not_allowed(e):
        return api_error('Method not allowed', 405)

    @bp.errorhandler(413)
    def payload_too_large(e):
        return api_error('Uploaded file is too large', 413)

    @bp.errorhandler(422)
    def unprocessable(e):
        return api_error('Unprocessable request', 422)

    @bp.errorhandler(500)
    def internal_error(e):
        return api_error('Internal server error', 500)

    @bp.errorhandler(HTTPException)
    def generic_http(e):
        return api_error(e.description or e.name, e.code)
