"""
Announcements — supervisors/admins create, agents view on portal.
"""
import logging
from datetime import datetime, timezone

from flask import Blueprint, render_template, request, jsonify
from app.auth import login_required, get_current_user
from app.models import db, Announcement, User

log = logging.getLogger(__name__)
announce_bp = Blueprint("announcements", __name__, url_prefix="/announcements")


def _utcnow():
    return datetime.now(timezone.utc)


# ── Management page (admin/supervisor) ─────────────────────

@announce_bp.route("/")
@login_required
def index():
    user = get_current_user()
    if not user or user.get("role") not in ("admin", "supervisor"):
        return "Forbidden", 403
    return render_template("announcements/index.html", user=user)


# ── Agent-facing: list active announcements ──────────────────

@announce_bp.route("/api/active", methods=["POST"])
@login_required
def active_announcements():
    """Return active (non-expired) announcements for the portal."""
    user = get_current_user()
    if user and user.get("is_demo"):
        from app.demo_data import get_demo_announcements
        return jsonify(success=True, announcements=get_demo_announcements())
    now = _utcnow()
    query = Announcement.query.filter(
        db.or_(Announcement.expires_at.is_(None), Announcement.expires_at > now)
    ).order_by(Announcement.is_pinned.desc(), Announcement.created_at.desc())

    entries = query.limit(20).all()
    rows = []
    for a in entries:
        author = User.query.get(a.created_by)
        rows.append({
            "id": a.id,
            "title": a.title,
            "body": a.body or "",
            "category": a.category,
            "is_pinned": a.is_pinned,
            "author": author.display_name if author else "Unknown",
            "created_at": a.created_at.isoformat() if a.created_at else "",
        })
    return jsonify(success=True, announcements=rows)


# ── Supervisor/Admin: manage announcements ───────────────────

@announce_bp.route("/api/list", methods=["POST"])
@login_required
def list_announcements():
    """All announcements for management view."""
    user = get_current_user()
    if user and user.get("is_demo"):
        from app.demo_data import get_demo_announcements
        return jsonify(success=True, announcements=get_demo_announcements())
    user = get_current_user()
    if not user or user.get("role") not in ("admin", "supervisor"):
        return jsonify(error="Forbidden"), 403

    entries = Announcement.query.order_by(
        Announcement.created_at.desc()
    ).limit(50).all()

    rows = []
    for a in entries:
        author = User.query.get(a.created_by)
        rows.append({
            "id": a.id,
            "title": a.title,
            "body": a.body or "",
            "category": a.category,
            "is_pinned": a.is_pinned,
            "author": author.display_name if author else "Unknown",
            "created_at": a.created_at.isoformat() if a.created_at else "",
            "expires_at": a.expires_at.isoformat() if a.expires_at else None,
        })
    return jsonify(success=True, announcements=rows)


@announce_bp.route("/api/save", methods=["POST"])
@login_required
def save_announcement():
    """Create or update an announcement."""
    user = get_current_user()
    if not user or user.get("role") not in ("admin", "supervisor"):
        return jsonify(error="Forbidden"), 403

    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    if not title:
        return jsonify(success=False, error="Title is required")

    ann_id = data.get("id")
    if ann_id:
        ann = Announcement.query.get(ann_id)
        if not ann:
            return jsonify(success=False, error="Not found")
    else:
        ann = Announcement(created_by=user["id"])
        db.session.add(ann)

    ann.title = title
    ann.body = (data.get("body") or "").strip()
    ann.category = data.get("category", "general")
    ann.is_pinned = bool(data.get("is_pinned", False))

    expires = data.get("expires_at")
    if expires:
        try:
            ann.expires_at = datetime.fromisoformat(expires.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            ann.expires_at = None
    else:
        ann.expires_at = None

    is_new = ann_id is None
    db.session.commit()

    # Notify all users when a new announcement is created
    if is_new:
        try:
            from app.routes.notifications import notify
            all_users = User.query.filter_by(is_active=True).all()
            for u in all_users:
                notify(
                    u.id,
                    f"New Announcement: {ann.title}",
                    (ann.body or "")[:200],
                    category="info",
                    link="/portal/",
                )
        except Exception:
            log.warning("Failed to notify users of new announcement", exc_info=True)

    return jsonify(success=True, id=ann.id)


@announce_bp.route("/api/delete", methods=["POST"])
@login_required
def delete_announcement():
    """Delete an announcement."""
    user = get_current_user()
    if not user or user.get("role") not in ("admin", "supervisor"):
        return jsonify(error="Forbidden"), 403

    data = request.get_json(silent=True) or {}
    ann = Announcement.query.get(data.get("id"))
    if not ann:
        return jsonify(success=False, error="Not found")

    db.session.delete(ann)
    db.session.commit()
    return jsonify(success=True)
