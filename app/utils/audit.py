"""Centralized audit logging helper.

Usage:
    from app.utils.audit import log_audit
    log_audit("schedule_published", detail="Week of 2026-01-05",
              entity_type="schedule", entity_id=42)
"""
from flask import session


def log_audit(action, detail=None, entity_type=None, entity_id=None, user_id=None):
    """Write one row to audit_log. Automatically picks up current_user if available."""
    from app.models import AuditLog, db

    uid = user_id
    if uid is None:
        uid = session.get("user_id")

    entry = AuditLog(
        user_id=uid,
        action=action,
        detail=detail,
        entity_type=entity_type,
        entity_id=entity_id,
    )
    db.session.add(entry)
    # Caller is responsible for db.session.commit() (or the request teardown will).
    return entry
