"""
Audit log helper — call audit_log() from any route to record an action.
"""
import logging
from flask import request
from app.models import db, AuditLog
from app.auth import get_current_user

log = logging.getLogger(__name__)


def audit_log(action, entity_type="", entity_id=None, detail=""):
    """Record an audit event for the current user."""
    try:
        user = get_current_user()
        entry = AuditLog(
            user_id=user["id"] if user else None,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            detail=detail,
        )
        db.session.add(entry)
        db.session.commit()
    except Exception as e:
        log.warning(f"Audit log write failed: {e}")
        try:
            db.session.rollback()
        except Exception:
            pass
