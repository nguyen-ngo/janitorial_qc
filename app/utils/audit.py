"""
audit.py
--------
Centralised helper for writing AuditLog entries.

Usage (inside any route after db.session.commit()):

    from app.utils.audit import log_action

    log_action(
        action       = 'CREATE',
        entity_type  = 'Facility',
        entity_id    = facility.id,
        entity_label = facility.name,
        details      = f'address={facility.address}',
    )

``action`` should be one of the ACTION_* constants defined below.
``entity_type`` should match the model class name for consistency.
"""

import logging
from flask import request
from flask_login import current_user
from app import db
from app.models.audit import AuditLog

logger = logging.getLogger(__name__)

# ── Canonical action constants ────────────────────────────────────────────────
ACTION_CREATE  = 'CREATE'
ACTION_UPDATE  = 'UPDATE'
ACTION_DELETE  = 'DELETE'
ACTION_LOGIN   = 'LOGIN'
ACTION_LOGOUT  = 'LOGOUT'
ACTION_EXPORT  = 'EXPORT'


def log_action(action: str,
               entity_type: str,
               entity_id: int | None = None,
               entity_label: str | None = None,
               details: str | None = None) -> None:
    """
    Write a single AuditLog row.  Safe to call from any request context.

    Parameters
    ----------
    action       : One of the ACTION_* constants (or a custom string ≤ 50 chars).
    entity_type  : Model name — 'User', 'Facility', 'Area', 'Template',
                   'Inspection', 'Issue', etc.
    entity_id    : Primary key of the affected record (optional).
    entity_label : Human-readable name / description snapshot (optional).
    details      : Extra context string, e.g. 'status=open→resolved' (optional).
    """
    try:
        # Resolve actor — fall back gracefully if called outside request context
        if current_user and current_user.is_authenticated:
            uid      = current_user.id
            uname    = current_user.username
            urole    = current_user.role
        else:
            uid, uname, urole = None, 'system', 'system'

        # Best-effort IP extraction; respects X-Forwarded-For from Nginx
        ip = None
        try:
            ip = (request.headers.get('X-Forwarded-For', '').split(',')[0].strip()
                  or request.remote_addr)
        except RuntimeError:
            pass  # outside request context

        entry = AuditLog(
            user_id      = uid,
            username     = uname,
            user_role    = urole,
            action       = action[:50],
            entity_type  = entity_type[:50],
            entity_id    = entity_id,
            entity_label = (entity_label or '')[:255],
            details      = details,
            ip_address   = (ip or '')[:45],
        )
        db.session.add(entry)
        db.session.commit()

    except Exception as exc:
        # Audit logging must never break the primary request flow
        logger.error('AuditLog write failed: %s', exc, exc_info=True)
        try:
            db.session.rollback()
        except Exception:
            pass
