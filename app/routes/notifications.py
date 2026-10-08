"""
Notifications API — bell-icon dropdown for in-app alerts.
"""
import logging
from flask import Blueprint, jsonify, request
from app.auth import login_required, get_current_user
from app.models import db, Notification

log = logging.getLogger(__name__)
notif_bp = Blueprint("notifications", __name__, url_prefix="/notifications")


@notif_bp.route("/api/list", methods=["POST"])
@login_required
def list_notifications():
    """Return most recent notifications for the current user."""
    try:
        user = get_current_user()
        if user and user.get("is_demo"):
            from app.demo_data import get_demo_notifications
            notifs = get_demo_notifications()
            unread = sum(1 for n in notifs if not n["is_read"])
            return jsonify(success=True, notifications=notifs, unread_count=unread)
        limit = int(request.json.get("limit", 20)) if request.is_json else 20
        notifs = (
            Notification.query
            .filter_by(user_id=user["id"])
            .order_by(Notification.created_at.desc())
            .limit(min(limit, 50))
            .all()
        )
        unread = Notification.query.filter_by(
            user_id=user["id"], is_read=False
        ).count()
        return jsonify({
            "success": True,
            "notifications": [n.to_dict() for n in notifs],
            "unread_count": unread,
        })
    except Exception as e:
        log.exception("Notification list error")
        return jsonify({"success": False, "error": str(e)})


@notif_bp.route("/api/unread-count", methods=["POST"])
@login_required
def unread_count():
    """Quick count of unread notifications (for badge)."""
    try:
        user = get_current_user()
        if user and user.get("is_demo"):
            return jsonify({"success": True, "count": 2})
        count = Notification.query.filter_by(
            user_id=user["id"], is_read=False
        ).count()
        return jsonify({"success": True, "count": count})
    except Exception:
        return jsonify({"success": True, "count": 0})


@notif_bp.route("/api/mark-read", methods=["POST"])
@login_required
def mark_read():
    """Mark one or all notifications as read."""
    try:
        payload = request.get_json(silent=True) or {}
        notif_id = payload.get("id")
        user = get_current_user()
        if user and user.get("is_demo"):
            return jsonify(success=True, demo=True)
        if notif_id == "all":
            Notification.query.filter_by(
                user_id=user["id"], is_read=False
            ).update({"is_read": True})
        elif notif_id:
            n = Notification.query.filter_by(
                id=notif_id, user_id=user["id"]
            ).first()
            if n:
                n.is_read = True
        db.session.commit()
        return jsonify({"success": True})
    except Exception as e:
        db.session.rollback()
        log.exception("Mark read error")
        return jsonify({"success": False, "error": str(e)})


@notif_bp.route("/api/clear", methods=["POST"])
@login_required
def clear_notifications():
    """Delete all read notifications for current user."""
    try:
        user = get_current_user()
        if user and user.get("is_demo"):
            return jsonify(success=True, demo=True)
        Notification.query.filter_by(
            user_id=user["id"], is_read=True
        ).delete()
        db.session.commit()
        return jsonify({"success": True})
    except Exception as e:
        db.session.rollback()
        log.exception("Clear notifications error")
        return jsonify({"success": False, "error": str(e)})


# ── Helper to create notifications from anywhere ──────────────

def notify(user_id, title, message=None, category="info", link=None):
    """Create a notification for a user. Call from any route."""
    try:
        n = Notification(
            user_id=user_id,
            category=category,
            title=title,
            message=message,
            link=link,
        )
        db.session.add(n)
        db.session.commit()
        return n
    except Exception:
        db.session.rollback()
        log.exception("Failed to create notification")
        return None


def audit(action, detail=None, entity_type=None, entity_id=None, user_id=None):
    """Write an audit log entry."""
    from app.models import AuditLog
    try:
        if user_id is None:
            try:
                cu = get_current_user()
                user_id = cu["id"] if cu else None
            except Exception:
                user_id = None
        entry = AuditLog(
            user_id=user_id,
            action=action,
            detail=detail,
            entity_type=entity_type,
            entity_id=entity_id,
        )
        db.session.add(entry)
        db.session.commit()
    except Exception:
        db.session.rollback()
        log.exception("Audit log error")


def notify_all_admins(title, message=None, category="system", link=None):
    """Notify all admin users."""
    from app.models import User
    try:
        admins = User.query.filter_by(role="admin", is_active=True).all()
        for u in admins:
            n = Notification(
                user_id=u.id, category=category,
                title=title, message=message, link=link,
            )
            db.session.add(n)
        db.session.commit()
    except Exception:
        db.session.rollback()
        log.exception("Failed to notify admins")
