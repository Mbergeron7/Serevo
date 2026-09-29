"""
routes/people.py — People Management blueprint
================================================
Employee roster, accommodations, and PTO management.
"""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from flask import (Blueprint, render_template, request, redirect,
                   url_for, jsonify)
from app.auth import login_required, get_current_user
from app.models import db, EmployeeAvailability, RotationAssignment

log = logging.getLogger("serevo.people")

people_bp = Blueprint("people", __name__, url_prefix="/people")

TIMEZONE = "America/Toronto"


def _get_sheet():
    """Return the capacity Google Sheet object, or None."""
    try:
        from app.data_source import _open_capacity_sheet
        sheet, err = _open_capacity_sheet()
        if err:
            log.warning(f"Sheet open error: {err}")
            return None
        return sheet
    except Exception as e:
        log.warning(f"Sheet import error: {e}")
        return None


# ── Roster View ──────────────────────────────────────────────
@people_bp.route("/")
@login_required
def roster():
    user = get_current_user()

    # Demo mode: serve fake data, no API/sheet calls
    if user and user.get("is_demo"):
        from app.demo_data import (get_demo_employees, get_demo_accommodations,
                                   get_demo_pto, _DEMO_ROTATION, DEMO_EMPLOYEES)
        employees, emp_err = get_demo_employees()
        accoms, _ = get_demo_accommodations()
        pto_list, _ = get_demo_pto()

        # Build demo avail_map — all demo employees have availability set
        avail_map = {}
        for e in employees:
            full = f"{e.get('First Name', '')} {e.get('Last Name', '')}".strip()
            if full:
                avail_map[full] = True

        # Build demo rotation_map — employees in _DEMO_ROTATION
        _rot_ids = set(_DEMO_ROTATION.keys())
        rotation_map = {}
        for e in employees:
            if e.get("Employee ID") in _rot_ids:
                full = f"{e.get('First Name', '')} {e.get('Last Name', '')}".strip()
                rotation_map[full] = True
    else:
        from app.people.manager import get_employees, get_accommodations, get_pto
        sheet = _get_sheet()
        employees, emp_err = get_employees()  # always read from DB (edits go there)
        accoms, _ = get_accommodations(sheet)
        pto_list, _ = get_pto(sheet)

    # Build lookup maps for the template
    accom_map = {}
    for a in accoms:
        name = a.get("Employee", "").strip()
        if name:
            accom_map[name] = a

    pto_map = {}
    for p in pto_list:
        name = p.get("Employee", "").strip()
        if name:
            pto_map.setdefault(name, []).append(p)

    # Build availability & rotation maps from DB (non-demo only)
    if not (user and user.get("is_demo")):
        avail_emp_ids = set(
            r[0] for r in db.session.query(EmployeeAvailability.employee_id).distinct().all()
        )
        avail_map = {}
        for e in employees:
            db_id = e.get("_db_id")
            if db_id and db_id in avail_emp_ids:
                full = f"{e.get('First Name', '')} {e.get('Last Name', '')}".strip()
                avail_map[full] = True

        rot_emp_ids = set(
            r[0] for r in db.session.query(RotationAssignment.employee_id).distinct().all()
        )
        rotation_map = {}
        for e in employees:
            db_id = e.get("_db_id")
            if db_id and db_id in rot_emp_ids:
                full = f"{e.get('First Name', '')} {e.get('Last Name', '')}".strip()
                rotation_map[full] = True

    # Count stats
    active = [e for e in employees
              if str(e.get("Status", "")).strip().lower() in ("active", "")]
    lobs = set()
    for e in employees:
        lob = (e.get("Latest Skill Name") or "").strip()
        if lob:
            lobs.add(lob)

    return render_template("people/roster.html",
        user=user,
        employees=employees,
        accom_map=accom_map,
        pto_map=pto_map,
        avail_map=avail_map,
        rotation_map=rotation_map,
        emp_error=emp_err,
        active_count=len(active),
        total_count=len(employees),
        lob_count=len(lobs),
        accom_count=len(accoms),
        pto_count=len(pto_list),
    )


# ── Accommodations ───────────────────────────────────────────
@people_bp.route("/accommodations")
@login_required
def accommodations():
    user = get_current_user()

    if user and user.get("is_demo"):
        from app.demo_data import get_demo_accommodations, get_demo_employees
        accoms, err = get_demo_accommodations()
        emps, _ = get_demo_employees()
        emp_names = [f"{e['First Name']} {e['Last Name']}" for e in emps]
    else:
        from app.people.manager import get_accommodations, get_employee_names
        sheet = _get_sheet()
        accoms, err = get_accommodations(sheet)
        emp_names, _ = get_employee_names(sheet)

    return render_template("people/accommodations.html",
        user=user,
        accommodations=accoms,
        employee_names=emp_names,
        error=err,
    )


@people_bp.route("/accommodations/save", methods=["POST"])
@login_required
def save_accommodation():
    from app.people.manager import save_accommodation as _save
    try:
        payload = request.get_json(silent=True) or {}
        err = _save(payload)
        if err:
            return jsonify({"success": False, "error": err})
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@people_bp.route("/accommodations/delete", methods=["POST"])
@login_required
def delete_accommodation():
    from app.people.manager import delete_accommodation as _delete
    try:
        payload = request.get_json(silent=True) or {}
        name = payload.get("employee", "")
        err = _delete(name)
        if err:
            return jsonify({"success": False, "error": err})
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


# ── PTO ──────────────────────────────────────────────────────
@people_bp.route("/pto")
@login_required
def pto():
    user = get_current_user()

    if user and user.get("is_demo"):
        from app.demo_data import get_demo_pto, get_demo_employees
        pto_list, err = get_demo_pto()
        emps, _ = get_demo_employees()
        emp_names = [f"{e['First Name']} {e['Last Name']}" for e in emps]
    else:
        from app.people.manager import get_pto, get_employee_names
        sheet = _get_sheet()
        pto_list, err = get_pto(sheet)
        emp_names, _ = get_employee_names(sheet)

    return render_template("people/pto.html",
        user=user,
        pto_list=pto_list,
        employee_names=emp_names,
        error=err,
    )


@people_bp.route("/pto/save", methods=["POST"])
@login_required
def save_pto():
    from app.people.manager import save_pto as _save
    try:
        payload = request.get_json(silent=True) or {}
        err = _save(payload)
        if err:
            return jsonify({"success": False, "error": err})
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@people_bp.route("/pto/delete", methods=["POST"])
@login_required
def delete_pto():
    from app.people.manager import delete_pto as _delete
    try:
        payload = request.get_json(silent=True) or {}
        row_idx = int(payload.get("row_index", 0))
        if row_idx < 2:
            return jsonify({"success": False, "error": "Invalid row"})
        err = _delete(row_idx)
        if err:
            return jsonify({"success": False, "error": err})
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


# ── Availability ────────────────────────────────────────────
@people_bp.route("/availability")
@login_required
def availability():
    user = get_current_user()

    if user and user.get("is_demo"):
        from app.demo_data import get_demo_employees
        employees, emp_err = get_demo_employees()
    else:
        from app.people.manager import get_employees
        employees, emp_err = get_employees()

    return render_template("people/availability.html",
        user=user,
        employees=employees,
        emp_error=emp_err,
    )


# ── API: employee names (for other modules to use) ───────────
@people_bp.route("/api/names")
@login_required
def api_names():
    from app.people.manager import get_employee_names
    names, err = get_employee_names()
    if err:
        return jsonify({"names": [], "error": err})
    return jsonify({"names": names})
