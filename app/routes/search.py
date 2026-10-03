"""
Global search — unified search across employees, schedules, and PTO.
"""
import logging
from datetime import date
from flask import Blueprint, request, jsonify
from sqlalchemy import or_
from app.auth import login_required, get_current_user
from app.models import db, Employee, Schedule, PTOEntry, PlanningUnit

log = logging.getLogger(__name__)
search_bp = Blueprint("search", __name__, url_prefix="/search")


@search_bp.route("/api/query", methods=["POST"])
@login_required
def query():
    """Search employees, schedules, PTO entries."""
    user = get_current_user()
    if not user:
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    payload = request.get_json(silent=True) or {}
    q = (payload.get("q") or "").strip()
    if len(q) < 2:
        return jsonify({"success": True, "results": []})

    results = []
    search_term = f"%{q}%"

    try:
        # Search employees
        employees = Employee.query.filter(
            or_(
                Employee.first_name.ilike(search_term),
                Employee.last_name.ilike(search_term),
                Employee.employee_id.ilike(search_term),
                Employee.email.ilike(search_term),
            )
        ).limit(8).all()

        for emp in employees:
            pu_name = emp.planning_unit.name if emp.planning_unit else ""
            results.append({
                "type": "employee",
                "icon": "👤",
                "title": emp.full_name,
                "subtitle": f"{emp.employee_id} · {pu_name}" if pu_name else emp.employee_id,
                "url": f"/people/{emp.id}/profile",
                "status": emp.status,
            })

        # Search planning units
        if user.get("role") in ("admin", "supervisor"):
            units = PlanningUnit.query.filter(
                PlanningUnit.name.ilike(search_term)
            ).limit(4).all()
            for pu in units:
                emp_count = Employee.query.filter_by(
                    planning_unit_id=pu.id, status="Active"
                ).count()
                results.append({
                    "type": "planning_unit",
                    "icon": "🏢",
                    "title": pu.name,
                    "subtitle": f"{emp_count} active employees",
                    "url": f"/scheduling/?lob={pu.name}",
                })

        # Quick navigation links
        nav_links = [
            ("Dashboard", "/", "📊"),
            ("People", "/people/", "👥"),
            ("Scheduling", "/scheduling/", "📅"),
            ("Forecasting", "/forecasting/", "📈"),
            ("Capacity Planning", "/capacity/", "🔢"),
            ("Real-Time", "/realtime/", "⚡"),
            ("Quality", "/quality/", "⭐"),
            ("Approvals", "/approvals/", "✅"),
            ("Reports", "/reports/", "📋"),
            ("Settings", "/settings/", "⚙️"),
            ("Data Import", "/data/", "📂"),
        ]
        q_lower = q.lower()
        for label, url, icon in nav_links:
            if q_lower in label.lower():
                results.append({
                    "type": "page",
                    "icon": icon,
                    "title": label,
                    "subtitle": "Navigate to page",
                    "url": url,
                })

    except Exception as e:
        log.exception("Search error")
        return jsonify({"success": False, "error": str(e)})

    return jsonify({"success": True, "results": results[:15]})
