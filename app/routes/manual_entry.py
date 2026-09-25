"""
routes/manual_entry.py — Manual data entry endpoints (Phase 1.4)
================================================================
JSON API endpoints for creating/editing employees, forecast intervals,
and requirement intervals via modal forms.
"""

import logging
from datetime import datetime

from flask import Blueprint, request, jsonify
from app.auth import login_required
from app.models import db, Employee, PlanningUnit, ForecastInterval, RequirementInterval

log = logging.getLogger("serevo.manual_entry")

manual_entry_bp = Blueprint("manual_entry", __name__, url_prefix="/api")


# ── helpers ─────────────────────────────────────────────────────
def _get_or_create_unit(name):
    """Find or create a PlanningUnit by name. Returns the unit."""
    name = (name or "").strip()
    if not name:
        return None
    unit = PlanningUnit.query.filter_by(name=name).first()
    if not unit:
        unit = PlanningUnit(name=name)
        db.session.add(unit)
        db.session.flush()
    return unit


def _parse_date(val):
    """Parse a date string (YYYY-MM-DD). Returns date or None."""
    if not val:
        return None
    try:
        return datetime.strptime(str(val).strip()[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _parse_datetime(date_str, time_str):
    """Combine date + time strings into a datetime."""
    if not date_str:
        return None
    try:
        d = str(date_str).strip()[:10]
        t = str(time_str or "00:00").strip()[:5]
        return datetime.strptime(f"{d} {t}", "%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return None


# ═══════════════════════════════════════════════════════════════
# EMPLOYEES — CRUD
# ═══════════════════════════════════════════════════════════════

@manual_entry_bp.route("/employees", methods=["GET"])
@login_required
def list_employees():
    """Return all employees as JSON (for modal population)."""
    emps = Employee.query.order_by(Employee.last_name, Employee.first_name).all()
    result = []
    for e in emps:
        pu = e.planning_unit
        result.append({
            "id": e.id,
            "employee_id": e.employee_id,
            "first_name": e.first_name,
            "last_name": e.last_name,
            "status": e.status,
            "lob": pu.name if pu else "",
            "all_skills": e.all_skills or "",
            "skill_start": e.skill_start.isoformat() if e.skill_start else "",
            "skill_end": e.skill_end.isoformat() if e.skill_end else "",
            "end_date": e.end_date.isoformat() if e.end_date else "",
        })
    return jsonify({"employees": result})


@manual_entry_bp.route("/employees/save", methods=["POST"])
@login_required
def save_employee():
    """Create or update an employee."""
    try:
        data = request.get_json(silent=True) or {}

        # Validate required fields
        emp_id_str = (data.get("employee_id") or "").strip()
        first = (data.get("first_name") or "").strip()
        last = (data.get("last_name") or "").strip()
        if not emp_id_str or not first or not last:
            return jsonify({"success": False, "error": "Employee ID, First Name, and Last Name are required."})

        # Planning unit
        lob = (data.get("lob") or "").strip()
        unit = _get_or_create_unit(lob) if lob else None

        # Find existing or create new
        pk = data.get("id")
        if pk:
            emp = Employee.query.get(int(pk))
            if not emp:
                return jsonify({"success": False, "error": "Employee not found."})
        else:
            # Check for duplicate employee_id
            existing = Employee.query.filter_by(employee_id=emp_id_str).first()
            if existing:
                return jsonify({"success": False, "error": f"Employee ID '{emp_id_str}' already exists."})
            emp = Employee(employee_id=emp_id_str)
            db.session.add(emp)

        emp.employee_id = emp_id_str
        emp.first_name = first
        emp.last_name = last
        emp.status = (data.get("status") or "Active").strip()
        emp.planning_unit_id = unit.id if unit else None
        emp.all_skills = (data.get("all_skills") or "").strip()
        emp.skill_start = _parse_date(data.get("skill_start"))
        emp.skill_end = _parse_date(data.get("skill_end"))
        emp.end_date = _parse_date(data.get("end_date"))

        db.session.commit()
        return jsonify({"success": True, "id": emp.id})

    except Exception as e:
        db.session.rollback()
        log.exception("save_employee error")
        return jsonify({"success": False, "error": str(e)})


@manual_entry_bp.route("/employees/delete", methods=["POST"])
@login_required
def delete_employee():
    """Deactivate (soft-delete) an employee."""
    try:
        data = request.get_json(silent=True) or {}
        pk = data.get("id")
        if not pk:
            return jsonify({"success": False, "error": "Missing employee id."})
        emp = Employee.query.get(int(pk))
        if not emp:
            return jsonify({"success": False, "error": "Employee not found."})
        emp.status = "Inactive"
        db.session.commit()
        return jsonify({"success": True})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "error": str(e)})


# ═══════════════════════════════════════════════════════════════
# PLANNING UNITS — list for dropdowns
# ═══════════════════════════════════════════════════════════════

@manual_entry_bp.route("/planning-units", methods=["GET"])
@login_required
def list_planning_units():
    units = PlanningUnit.query.filter_by(is_active=True).order_by(PlanningUnit.name).all()
    return jsonify({"units": [{"id": u.id, "name": u.name} for u in units]})


# ═══════════════════════════════════════════════════════════════
# FORECAST INTERVALS — manual entry
# ═══════════════════════════════════════════════════════════════

@manual_entry_bp.route("/forecast/save", methods=["POST"])
@login_required
def save_forecast_interval():
    """Create a single forecast interval or a batch."""
    try:
        data = request.get_json(silent=True) or {}

        unit_name = (data.get("planning_unit") or "").strip()
        if not unit_name:
            return jsonify({"success": False, "error": "Planning unit is required."})
        unit = _get_or_create_unit(unit_name)

        # Support single or batch
        intervals = data.get("intervals")
        if intervals is None:
            # Single entry
            ts = _parse_datetime(data.get("date"), data.get("time"))
            if not ts:
                return jsonify({"success": False, "error": "Valid date and time are required."})
            offered = float(data.get("offered", 0) or 0)
            aht = float(data.get("aht", 0) or 0)
            fi = ForecastInterval(
                planning_unit_id=unit.id,
                timestamp=ts,
                offered=offered,
                aht=aht,
                source="manual",
            )
            db.session.add(fi)
            db.session.commit()
            return jsonify({"success": True, "count": 1})

        # Batch
        count = 0
        for row in intervals:
            ts = _parse_datetime(row.get("date"), row.get("time"))
            if not ts:
                continue
            fi = ForecastInterval(
                planning_unit_id=unit.id,
                timestamp=ts,
                offered=float(row.get("offered", 0) or 0),
                aht=float(row.get("aht", 0) or 0),
                source="manual",
            )
            db.session.add(fi)
            count += 1
        db.session.commit()
        return jsonify({"success": True, "count": count})

    except Exception as e:
        db.session.rollback()
        log.exception("save_forecast error")
        return jsonify({"success": False, "error": str(e)})


# ═══════════════════════════════════════════════════════════════
# REQUIREMENT INTERVALS — manual entry
# ═══════════════════════════════════════════════════════════════

@manual_entry_bp.route("/requirements/save", methods=["POST"])
@login_required
def save_requirement_interval():
    """Create a single requirement interval or a batch."""
    try:
        data = request.get_json(silent=True) or {}

        unit_name = (data.get("planning_unit") or "").strip()
        if not unit_name:
            return jsonify({"success": False, "error": "Planning unit is required."})
        unit = _get_or_create_unit(unit_name)

        intervals = data.get("intervals")
        if intervals is None:
            ts = _parse_datetime(data.get("date"), data.get("time"))
            if not ts:
                return jsonify({"success": False, "error": "Valid date and time are required."})
            agents = float(data.get("agents_required", 0) or 0)
            ri = RequirementInterval(
                planning_unit_id=unit.id,
                timestamp=ts,
                agents_required=agents,
                source="manual",
            )
            db.session.add(ri)
            db.session.commit()
            return jsonify({"success": True, "count": 1})

        count = 0
        for row in intervals:
            ts = _parse_datetime(row.get("date"), row.get("time"))
            if not ts:
                continue
            ri = RequirementInterval(
                planning_unit_id=unit.id,
                timestamp=ts,
                agents_required=float(row.get("agents_required", 0) or 0),
                source="manual",
            )
            db.session.add(ri)
            count += 1
        db.session.commit()
        return jsonify({"success": True, "count": count})

    except Exception as e:
        db.session.rollback()
        log.exception("save_requirement error")
        return jsonify({"success": False, "error": str(e)})
