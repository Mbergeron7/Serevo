"""
routes/people.py — People Management blueprint
================================================
Employee roster, accommodations, and PTO management.
"""

import json
import logging
import datetime as _dt
from datetime import datetime
from zoneinfo import ZoneInfo

from flask import (Blueprint, render_template, request, redirect,
                   url_for, jsonify, abort)
from app.auth import login_required, admin_required, get_current_user
from app.models import (db, EmployeeAvailability, Schedule, Contract,
                        Employee, EmployeePlanningUnit,
                        EmployeeContract, Selection, SelectionMember,
                        SkillMapping, PlanningUnit, ShiftSequence,
                        EmployeeShiftSequence, SkillGroup, CoachingSession)

log = logging.getLogger("serevo.people")

people_bp = Blueprint("people", __name__, url_prefix="/people")

TIMEZONE = "America/Toronto"


from app.routes._utils import get_sheet as _get_sheet


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

        # Build demo schedule_map — employees with schedules created
        # In demo, the 4 rotation employees have generated schedules
        _sched_ids = set(_DEMO_ROTATION.keys())
        schedule_map = {}
        for e in employees:
            if e.get("Employee ID") in _sched_ids:
                full = f"{e.get('First Name', '')} {e.get('Last Name', '')}".strip()
                schedule_map[full] = True
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
        try:
            avail_emp_ids = set(
                r[0] for r in db.session.query(EmployeeAvailability.employee_id).distinct().all()
            )
        except Exception:
            db.session.rollback()
            avail_emp_ids = set()
        avail_map = {}
        for e in employees:
            db_id = e.get("_db_id")
            if db_id and db_id in avail_emp_ids:
                full = f"{e.get('First Name', '')} {e.get('Last Name', '')}".strip()
                avail_map[full] = True

        sched_emp_ids = set(
            r[0] for r in db.session.query(Schedule.employee_id).distinct().all()
        )
        schedule_map = {}
        for e in employees:
            db_id = e.get("_db_id")
            if db_id and db_id in sched_emp_ids:
                full = f"{e.get('First Name', '')} {e.get('Last Name', '')}".strip()
                schedule_map[full] = True

    # Count stats
    active = [e for e in employees
              if str(e.get("Status", "")).strip().lower() in ("active", "")]
    lobs = set()
    for e in employees:
        lob = (e.get("Latest Skill Name") or "").strip()
        if lob:
            lobs.add(lob)

    rotation_map = {}  # placeholder — rotation feature not yet wired

    if user and user.get("is_demo"):
        contracts = []
    else:
        try:
            contracts = [c.to_dict() for c in Contract.query.order_by(Contract.name).all()]
        except Exception:
            db.session.rollback()
            contracts = []

    return render_template("people/roster.html",
        user=user,
        employees=employees,
        accom_map=accom_map,
        pto_map=pto_map,
        avail_map=avail_map,
        schedule_map=schedule_map,
        emp_error=emp_err,
        active_count=len(active),
        total_count=len(employees),
        lob_count=len(lobs),
        accom_count=len(accoms),
        pto_count=len(pto_list),
        contracts=contracts,
        rotation_map=rotation_map,
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
# ── API: employee names (for other modules to use) ───────────
@people_bp.route("/api/names")
@login_required
def api_names():
    from app.people.manager import get_employee_names
    names, err = get_employee_names()
    if err:
        return jsonify({"names": [], "error": err})
    return jsonify({"names": names})


def _demo_guard():
    """Return a mock-success JSON response if the current user is a demo user, else None."""
    user = get_current_user()
    if user and user.get("is_demo"):
        return jsonify(ok=True, demo=True, message="Changes are not saved in demo mode.")


# ═══════════════════════════════════════════════════════════════
# EMPLOYEE PROFILE PAGE
# ═══════════════════════════════════════════════════════════════

@people_bp.route("/<emp_id>/profile")
@login_required
def employee_profile(emp_id):
    user = get_current_user()

    # Demo mode: build a mock employee object from DEMO_EMPLOYEES
    if user and user.get("is_demo"):
        from app.demo_data import DEMO_EMPLOYEES
        demo_emp = None
        for e in DEMO_EMPLOYEES:
            if str(e.get("Employee ID", "")) == str(emp_id):
                demo_emp = e
                break
        if not demo_emp:
            abort(404)

        class _MockPU:
            def __init__(self, name): self.name = name
        class _MockEmp:
            def __init__(self, d):
                self.id = d.get("Employee ID")
                self.employee_id = d.get("Employee ID", "")
                self.first_name = d.get("First Name", "")
                self.last_name = d.get("Last Name", "")
                self.full_name = f"{self.first_name} {self.last_name}".strip()
                self.status = d.get("Status", "Active")
                self.team_lead = d.get("Team Lead", "")
                self.languages = d.get("Languages", "English")
                self.timezone = d.get("Timezone", "America/Toronto")
                self.skill_start = d.get("Latest Skill Start", "")
                self.skill_end = d.get("Latest Skill End", "")
                self.end_date = d.get("End Date", "")
                self.schedule_excluded = d.get("Schedule Excluded", "") == "Yes"
                self.email = d.get("Email", "")
                self.external_id_1 = ""
                self.external_id_2 = ""
                lob = d.get("Latest Skill Name", "")
                self.planning_unit = _MockPU(lob) if lob else None
                self.contract = None

        emp = _MockEmp(demo_emp)
        return render_template("people/profile.html",
            user=user,
            emp=emp,
            skill_mappings=[],
            contract_assignments=[],
            selection_memberships=[],
            pu_assignments=[],
            ss_assignments=[],
            avail_map={},
        )

    emp_id = int(emp_id)
    emp = Employee.query.get_or_404(emp_id)

    skill_mappings = SkillMapping.query.filter_by(employee_id=emp.id).all()
    contract_assignments = EmployeeContract.query.filter_by(employee_id=emp.id).all()
    selection_memberships = SelectionMember.query.filter_by(employee_id=emp.id).all()
    pu_assignments = EmployeePlanningUnit.query.filter_by(employee_id=emp.id).order_by(
        EmployeePlanningUnit.priority).all()
    try:
        ss_assignments = EmployeeShiftSequence.query.filter_by(employee_id=emp.id).all()
    except Exception:
        db.session.rollback()
        ss_assignments = []
    # Build availability map keyed by day_of_week (0-6)
    avail_entries = EmployeeAvailability.query.filter_by(employee_id=emp.id).all()
    avail_map = {a.day_of_week: a for a in avail_entries}

    return render_template("people/profile.html",
        user=user,
        emp=emp,
        skill_mappings=skill_mappings,
        contract_assignments=contract_assignments,
        selection_memberships=selection_memberships,
        pu_assignments=pu_assignments,
        ss_assignments=ss_assignments,
        avail_map=avail_map,
    )


# ═══════════════════════════════════════════════════════════════
# PROFILE API ENDPOINTS
# ═══════════════════════════════════════════════════════════════

@people_bp.route("/api/profile/contracts-list")
@login_required
def profile_contracts_list():
    items = Contract.query.filter_by(is_active=True).order_by(Contract.name).all()
    return jsonify({"items": [{"id": c.id, "name": c.name} for c in items]})


@people_bp.route("/api/profile/selections-list")
@login_required
def profile_selections_list():
    items = Selection.query.filter_by(is_active=True).order_by(Selection.name).all()
    return jsonify({"items": [{"id": s.id, "name": s.name} for s in items]})


@people_bp.route("/api/profile/shift-sequences-list")
@login_required
def profile_shift_sequences_list():
    items = ShiftSequence.query.filter_by(is_active=True).order_by(ShiftSequence.name).all()
    return jsonify({"items": [{"id": s.id, "name": s.name} for s in items]})


@people_bp.route("/api/profile/assign", methods=["POST"])
@login_required
def profile_assign():
    dg = _demo_guard()
    if dg:
        return dg
    data = request.get_json(silent=True) or {}
    assign_type = data.get("type")
    emp_id = data.get("employee_id")
    item_id = data.get("item_id")

    if not emp_id or not item_id:
        return jsonify({"success": False, "error": "Missing employee or item ID."})

    valid_from = _dt.datetime.strptime(data["valid_from"], "%Y-%m-%d").date() if data.get("valid_from") else None
    valid_to = _dt.datetime.strptime(data["valid_to"], "%Y-%m-%d").date() if data.get("valid_to") else None

    try:
        if assign_type == "planning_unit":
            obj = EmployeePlanningUnit(
                employee_id=emp_id, planning_unit_id=item_id,
                priority=int(data.get("priority", 1)),
                valid_from=valid_from, valid_to=valid_to,
            )
            db.session.add(obj)
        elif assign_type == "contract":
            obj = EmployeeContract(
                employee_id=emp_id, contract_id=item_id,
                valid_from=valid_from, valid_to=valid_to,
            )
            db.session.add(obj)
        elif assign_type == "selection":
            obj = SelectionMember(selection_id=item_id, employee_id=emp_id)
            db.session.add(obj)
        elif assign_type == "shift_sequence":
            ref_date = _dt.datetime.strptime(data["reference_date"], "%Y-%m-%d").date() if data.get("reference_date") else None
            obj = EmployeeShiftSequence(
                employee_id=emp_id, shift_sequence_id=item_id,
                reference_date=ref_date,
                valid_from=valid_from, valid_to=valid_to,
            )
            db.session.add(obj)
        elif assign_type == "skill":
            obj = SkillMapping(
                employee_id=emp_id, skill_group_id=item_id,
                proficiency=int(data.get("proficiency", 3)),
                priority=int(data.get("priority", 1)),
                is_active=True,
            )
            db.session.add(obj)
        else:
            return jsonify({"success": False, "error": f"Unknown assignment type: {assign_type}"})

        db.session.commit()
        return jsonify({"success": True})
    except Exception as e:
        db.session.rollback()
        err = str(e)
        if "uq_emp_pu" in err or "uq_emp_wtpm" in err or "uq_sel_emp" in err or "uq_skill_employee" in err:
            return jsonify({"success": False, "error": "This assignment already exists."})
        return jsonify({"success": False, "error": err})


@people_bp.route("/api/profile/unassign", methods=["POST"])
@login_required
def profile_unassign():
    dg = _demo_guard()
    if dg:
        return dg
    data = request.get_json(silent=True) or {}
    assign_type = data.get("type")
    assign_id = data.get("assignment_id")

    if not assign_id:
        return jsonify({"success": False, "error": "Missing assignment ID."})

    model_map = {
        "planning_unit": EmployeePlanningUnit,
        "contract": EmployeeContract,
        "selection": SelectionMember,
        "shift_sequence": EmployeeShiftSequence,
        "skill": SkillMapping,
    }
    model = model_map.get(assign_type)
    if not model:
        return jsonify({"success": False, "error": f"Unknown type: {assign_type}"})

    obj = model.query.get(assign_id)
    if not obj:
        return jsonify({"success": False, "error": "Assignment not found."})

    db.session.delete(obj)
    db.session.commit()
    return jsonify({"success": True})


@people_bp.route("/api/profile/availability", methods=["POST"])
@login_required
def profile_availability():
    dg = _demo_guard()
    if dg:
        return dg
    data = request.get_json(silent=True) or {}
    emp_id = data.get("employee_id")
    days = data.get("days", [])

    if not emp_id:
        return jsonify({"success": False, "error": "Missing employee ID."})

    try:
        for day in days:
            dow = day["day_of_week"]
            existing = EmployeeAvailability.query.filter_by(
                employee_id=emp_id, day_of_week=dow).first()

            earliest = _dt.datetime.strptime(day["earliest_start"], "%H:%M").time() if day.get("earliest_start") else None
            latest = _dt.datetime.strptime(day["latest_end"], "%H:%M").time() if day.get("latest_end") else None

            if existing:
                existing.is_available = day.get("is_available", True)
                existing.earliest_start = earliest
                existing.latest_end = latest
            else:
                obj = EmployeeAvailability(
                    employee_id=emp_id,
                    day_of_week=dow,
                    is_available=day.get("is_available", True),
                    earliest_start=earliest,
                    latest_end=latest,
                )
                db.session.add(obj)

        db.session.commit()
        return jsonify({"success": True})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "error": str(e)})


# ── Coaching Sessions ─────────────────────────────────────────

@people_bp.route("/api/coaching/list", methods=["POST"])
@login_required
def coaching_list():
    data = request.get_json(silent=True) or {}
    emp_id = data.get("employee_id")
    user = get_current_user()

    if user and user.get("is_demo"):
        from app.demo_data import get_demo_coaching_sessions
        return jsonify({"sessions": get_demo_coaching_sessions(emp_id)})

    if not emp_id:
        return jsonify({"sessions": []})

    try:
        sessions = (CoachingSession.query
                    .filter_by(employee_id=emp_id)
                    .order_by(CoachingSession.session_date.desc())
                    .all())
        return jsonify({"sessions": [s.to_dict() for s in sessions]})
    except Exception as e:
        db.session.rollback()
        return jsonify({"sessions": [], "error": str(e)})


@people_bp.route("/api/coaching/save", methods=["POST"])
@login_required
@admin_required
def coaching_save():
    dg = _demo_guard()
    if dg:
        return dg
    data = request.get_json(silent=True) or {}
    session_id = data.get("id")

    try:
        if session_id:
            cs = CoachingSession.query.get(session_id)
            if not cs:
                return jsonify({"success": False, "error": "Session not found."})
        else:
            cs = CoachingSession(employee_id=data["employee_id"])
            db.session.add(cs)

        cs.coach_name = data.get("coach_name", "")
        cs.session_date = _dt.date.fromisoformat(data["session_date"])
        cs.session_time = data.get("session_time") or None
        cs.duration_mins = int(data.get("duration_mins") or 30)
        cs.topic = data.get("topic", "")
        cs.category = data.get("category", "general")
        cs.quality_score = float(data["quality_score"]) if data.get("quality_score") else None
        cs.notes = data.get("notes", "")
        cs.outcome = data.get("outcome", "")
        cs.follow_up = data.get("follow_up", "")

        db.session.commit()
        return jsonify({"success": True, "session": cs.to_dict()})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "error": str(e)})


@people_bp.route("/api/coaching/delete", methods=["POST"])
@login_required
@admin_required
def coaching_delete():
    dg = _demo_guard()
    if dg:
        return dg
    data = request.get_json(silent=True) or {}
    session_id = data.get("id")

    cs = CoachingSession.query.get(session_id)
    if not cs:
        return jsonify({"success": False, "error": "Session not found."})

    db.session.delete(cs)
    db.session.commit()
    return jsonify({"success": True})
