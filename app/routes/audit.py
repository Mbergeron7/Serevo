"""
Audit Log — admin-only view of all system activity.
"""
import logging
from datetime import date, timedelta
from flask import Blueprint, render_template, request, jsonify
from app.auth import login_required, get_current_user
from app.models import AuditLog

log = logging.getLogger(__name__)
audit_bp = Blueprint("audit", __name__, url_prefix="/audit-log")


@audit_bp.route("/")
@login_required
def index():
    user = get_current_user()
    if not user or user.get("role") != "admin":
        return "Forbidden", 403
    return render_template("audit/index.html")


@audit_bp.route("/api/entries", methods=["POST"])
@login_required
def list_entries():
    user = get_current_user()
    if not user or user.get("role") != "admin":
        return jsonify(error="Forbidden"), 403

    if user and user.get("is_demo"):
        from app.demo_data import get_demo_audit_log
        entries = get_demo_audit_log()
        return jsonify(success=True, entries=entries, total=len(entries), page=1, per_page=50)

    data = request.get_json(silent=True) or {}
    days = int(data.get("days", 7))
    action_filter = data.get("action", "")
    target_filter = data.get("entity_type", "")
    page = max(int(data.get("page", 1)), 1)
    per_page = 50

    cutoff = date.today() - timedelta(days=days)
    q = AuditLog.query.filter(AuditLog.created_at >= cutoff.isoformat())

    if action_filter:
        q = q.filter(AuditLog.action == action_filter)
    if target_filter:
        q = q.filter(AuditLog.entity_type == target_filter)

    total = q.count()
    entries = q.order_by(AuditLog.created_at.desc()).offset((page - 1) * per_page).limit(per_page).all()

    rows = [e.to_dict() for e in entries]

    return jsonify(success=True, entries=rows, total=total, page=page, per_page=per_page)
