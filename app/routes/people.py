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
                        EmployeeShiftSequence, SkillGroup)

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


# ── Rotations View ─────────────────────────────────────────
@people_bp.route("/rotations")
@login_required
def rotations():
    user = get_current_user()

    if user and user.get("is_demo"):
        from app.demo_data import get_demo_settings_data, get_demo_employees
        demo = get_demo_settings_data()
        emps, _ = get_demo_employees()
        employees = [{"id": i+1, "employee_id": e.get("Employee ID",""),
                      "name": f"{e.get('First Name','')} {e.get('Last Name','')}".strip(),
                      "lob": e.get("Latest Skill Name","")}
                     for i, e in enumerate(emps)
                     if str(e.get("Status","")).strip().lower() in ("active","")]
        return render_template("people/rotations.html",
            user=user,
            rotations=demo.get("rotations", []),
            shifts=demo.get("shifts", []),
            employees=employees,
            all_lobs=demo.get("all_lobs", []),
            fill_in_rules=demo.get("fill_in_rules", []),
        )

    from app.models import (RotationPattern, ShiftTemplate, Employee,
                            PlanningUnit, FillInRule)

    rotations_list = [r.to_dict() for r in RotationPattern.query.order_by(RotationPattern.name).all()]
    shifts = [s.to_dict() for s in ShiftTemplate.query.order_by(ShiftTemplate.sort_order, ShiftTemplate.name).all()]
    all_lobs = [{"id": pu.id, "name": pu.name} for pu in PlanningUnit.query.order_by(PlanningUnit.name).all()]
    employees = [{"id": e.id, "employee_id": e.employee_id, "name": e.full_name,
                  "lob": e.planning_unit.name if e.planning_unit else ""}
                 for e in Employee.query.filter_by(status="Active").order_by(Employee.last_name).all()]
    fill_in_rules = [r.to_dict() for r in FillInRule.query.order_by(
        FillInRule.shift_category, FillInRule.priority).all()]

    return render_template("people/rotations.html",
        user=user,
        rotations=rotations_list,
        shifts=shifts,
        employees=employees,
        all_lobs=all_lobs,
        fill_in_rules=fill_in_rules,
    )


def _demo_guard():
    """Return a mock-success JSON response if the current user is a demo user, else None."""
    user = get_current_user()
    if user and user.get("is_demo"):
        return jsonify(ok=True, demo=True, message="Changes are not saved in demo mode.")


# ── Rotation Patterns CRUD ───────────────────────────────────

@people_bp.route("/rotations/save", methods=["POST"])
@admin_required
def save_rotation():
    dg = _demo_guard()
    if dg:
        return dg
    from app.models import db, RotationPattern
    data = request.get_json(silent=True) or {}
    rot_id = data.get("id")
    name = (data.get("name") or "").strip()
    weeks = data.get("weeks", [])
    if not name:
        return jsonify({"success": False, "error": "Rotation name is required"})
    if not weeks or len(weeks) < 1:
        return jsonify({"success": False, "error": "At least one week is required"})

    if rot_id:
        rot = RotationPattern.query.get(rot_id)
        if not rot:
            return jsonify({"success": False, "error": "Rotation pattern not found"})
        rot.name = name
        rot.weeks_json = json.dumps(weeks)
        rot.cycle_weeks = len(weeks)
    else:
        rot = RotationPattern(
            name=name,
            weeks_json=json.dumps(weeks),
            cycle_weeks=len(weeks),
        )
        db.session.add(rot)
    db.session.commit()
    return jsonify({"success": True, "rotation": rot.to_dict()})


@people_bp.route("/rotations/delete", methods=["POST"])
@admin_required
def delete_rotation():
    dg = _demo_guard()
    if dg:
        return dg
    from app.models import db, RotationPattern
    data = request.get_json(silent=True) or {}
    rot = RotationPattern.query.get(data.get("id"))
    if not rot:
        return jsonify({"success": False, "error": "Rotation pattern not found"})
    db.session.delete(rot)
    db.session.commit()
    return jsonify({"success": True})


# ── Rotation Assignments CRUD ───────────────────────────────

@people_bp.route("/rotations/assign", methods=["POST"])
@admin_required
def assign_rotation():
    dg = _demo_guard()
    if dg:
        return dg
    from app.models import db, RotationPattern, RotationAssignment, Employee
    data = request.get_json(silent=True) or {}
    rot = RotationPattern.query.get(data.get("rotation_id"))
    if not rot:
        return jsonify({"success": False, "error": "Rotation pattern not found"})
    emp = Employee.query.get(data.get("employee_id"))
    if not emp:
        return jsonify({"success": False, "error": "Employee not found"})
    existing = RotationAssignment.query.filter_by(
        rotation_id=rot.id, employee_id=emp.id).first()
    if existing:
        return jsonify({"success": False, "error": f"{emp.full_name} is already assigned"})
    assign = RotationAssignment(
        rotation_id=rot.id,
        employee_id=emp.id,
        current_week=int(data.get("current_week", 0)),
        start_date=_dt.datetime.strptime(data["start_date"], "%Y-%m-%d").date()
            if data.get("start_date") else None,
    )
    db.session.add(assign)
    db.session.commit()
    return jsonify({"success": True, "rotation": rot.to_dict()})


@people_bp.route("/rotations/unassign", methods=["POST"])
@admin_required
def unassign_rotation():
    dg = _demo_guard()
    if dg:
        return dg
    from app.models import db, RotationAssignment, RotationPattern
    data = request.get_json(silent=True) or {}
    assign = RotationAssignment.query.get(data.get("id"))
    if not assign:
        return jsonify({"success": False, "error": "Assignment not found"})
    rot_id = assign.rotation_id
    db.session.delete(assign)
    db.session.commit()
    rot = RotationPattern.query.get(rot_id)
    return jsonify({"success": True, "rotation": rot.to_dict() if rot else {}})


# ── Fill-In Rules CRUD ─────────────────────────────────────

@people_bp.route("/rotations/fill-in-rules/list", methods=["GET", "POST"])
@admin_required
def list_fill_in_rules():
    user = get_current_user()
    if user and user.get("is_demo"):
        from app.demo_data import get_demo_settings_data
        demo = get_demo_settings_data()
        return jsonify({"success": True, "rules": demo["fill_in_rules"]})
    from app.models import FillInRule
    rules = FillInRule.query.order_by(FillInRule.shift_category, FillInRule.priority).all()
    return jsonify({"success": True, "rules": [r.to_dict() for r in rules]})


@people_bp.route("/rotations/fill-in-rules/save", methods=["POST"])
@admin_required
def save_fill_in_rule():
    dg = _demo_guard()
    if dg:
        return dg
    from app.models import db, FillInRule
    data = request.get_json(silent=True) or {}
    rule_id = data.get("id")
    if rule_id:
        rule = FillInRule.query.get(rule_id)
        if not rule:
            return jsonify({"success": False, "error": "Rule not found"})
    else:
        rule = FillInRule()
        db.session.add(rule)

    rule.shift_category = data.get("shift_category", rule.shift_category or "closing")
    rule.employee_id = int(data["employee_id"]) if data.get("employee_id") else rule.employee_id
    rule.priority = int(data.get("priority", rule.priority or 0))
    rule.planning_unit_id = int(data["planning_unit_id"]) if data.get("planning_unit_id") else None
    rule.fallback_template_id = int(data["fallback_template_id"]) if data.get("fallback_template_id") else None
    rule.is_active = data.get("is_active", True)

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        err = str(e)
        if "uq_fillin_cat_employee" in err:
            return jsonify({"success": False, "error": "This employee already has a rule for that shift category."})
        return jsonify({"success": False, "error": err})

    return jsonify({"success": True, "rule": rule.to_dict()})


@people_bp.route("/rotations/fill-in-rules/delete", methods=["POST"])
@admin_required
def delete_fill_in_rule():
    dg = _demo_guard()
    if dg:
        return dg
    from app.models import db, FillInRule
    data = request.get_json(silent=True) or {}
    rule = FillInRule.query.get(data.get("id"))
    if not rule:
        return jsonify({"success": False, "error": "Rule not found"})
    db.session.delete(rule)
    db.session.commit()
    return jsonify({"success": True})


# ── Rotation Schedule Generation ────────────────────────────

@people_bp.route("/rotations/generate", methods=["POST"])
@admin_required
def generate_rotation_schedules():
    dg = _demo_guard()
    if dg:
        return dg
    """
    Generate schedule rows from rotation patterns for a date range.
    POST JSON: {rotation_id?, start_date, end_date}
    If rotation_id is omitted, generates for ALL active rotations.
    """
    from app.models import (db, RotationPattern, RotationAssignment, Employee,
                            Schedule, ShiftSegment, ShiftTemplate, PlanningUnit)
    import json as _json

    data = request.get_json(silent=True) or {}
    start_str = data.get("start_date", "")
    end_str = data.get("end_date", "")
    if not start_str or not end_str:
        return jsonify({"success": False, "error": "Start and end dates are required"})

    start_date = _dt.datetime.strptime(start_str, "%Y-%m-%d").date()
    end_date = _dt.datetime.strptime(end_str, "%Y-%m-%d").date()
    if end_date < start_date:
        return jsonify({"success": False, "error": "End date must be after start date"})
    if (end_date - start_date).days > 90:
        return jsonify({"success": False, "error": "Max 90 days per generation"})

    rot_id = data.get("rotation_id")
    if rot_id:
        rotation_list = [RotationPattern.query.get(rot_id)]
        rotation_list = [r for r in rotation_list if r]
    else:
        rotation_list = RotationPattern.query.filter_by(is_active=True).all()

    if not rotation_list:
        return jsonify({"success": False, "error": "No rotation patterns found"})

    # Cache shift templates by id
    templates = {t.id: t for t in ShiftTemplate.query.all()}

    DAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    created = 0
    skipped = 0
    warnings = []
    skip_notes = {}

    def _note(reason):
        skip_notes[reason] = skip_notes.get(reason, 0) + 1

    def _parse_hhmm(s):
        parts = str(s).strip().split(":")
        return _dt.time(int(parts[0]), int(parts[1]))

    def _to_min(s):
        t = _parse_hhmm(s)
        return t.hour * 60 + t.minute

    def _to_hhmm(m):
        return f"{m // 60:02d}:{m % 60:02d}"

    from app.scheduling.engine import _get_availability, _explain_unavailability, _generate_segments
    try:
        from app.people.manager import get_availability_map
        avail_map = get_availability_map()
    except Exception as e:
        avail_map = {}
        warnings.append(f"Could not load availability/accommodations: {e}")

    per_day_index = {}

    def _build_schedule(emp, d, tmpl, status):
        avail = _get_availability(emp.full_name, d, avail_map,
                                  employee_ext_id=emp.employee_id)
        if not avail["available"]:
            return None, _explain_unavailability(emp.full_name, d, avail_map, emp.employee_id)

        start_min = _to_min(tmpl.start_time)
        end_min = _to_min(tmpl.end_time)
        shift_type = tmpl.shift_type or "full"

        adjusted = False
        if avail.get("shift_start"):
            start_min = _to_min(avail["shift_start"]); adjusted = True
        if avail.get("shift_end"):
            end_min = _to_min(avail["shift_end"]); adjusted = True
        if avail.get("earliest_start") and start_min < _to_min(avail["earliest_start"]):
            start_min = _to_min(avail["earliest_start"]); adjusted = True
        if avail.get("latest_end") and end_min > _to_min(avail["latest_end"]):
            end_min = _to_min(avail["latest_end"]); adjusted = True
        if avail["day_type"] == "half":
            shift_type = "half"
            half = (end_min - start_min) // 2
            end_min = start_min + half
            adjusted = True
        if end_min <= start_min:
            return None, "accommodation window leaves no shift time"

        hours = round((end_min - start_min) / 60, 2)
        pu_id = tmpl.planning_unit_id if getattr(tmpl, "planning_unit_id", None) else emp.planning_unit_id

        sched = Schedule(
            employee_id=emp.id,
            planning_unit_id=pu_id,
            schedule_date=d,
            shift_start=_dt.time(start_min // 60, start_min % 60),
            shift_end=_dt.time(end_min // 60, end_min % 60),
            shift_type=shift_type,
            hours=hours,
            status=status,
        )
        db.session.add(sched)
        db.session.flush()

        segs = []
        if not adjusted and tmpl.segments_json:
            try:
                segs = _json.loads(tmpl.segments_json) or []
            except Exception:
                segs = []
        if not segs:
            idx = per_day_index.get(d, 0)
            per_day_index[d] = idx + 1
            segs = _generate_segments(_to_hhmm(start_min), _to_hhmm(end_min), shift_type,
                                      stagger_index=idx, total_employees=max(1, idx + 1))
        for i, seg in enumerate(segs):
            if not seg.get("start") or not seg.get("end"):
                continue
            db.session.add(ShiftSegment(
                schedule_id=sched.id,
                activity_type=seg.get("type", "on-call"),
                start_time=_parse_hhmm(seg["start"]),
                end_time=_parse_hhmm(seg["end"]),
                duration_mins=int(seg.get("duration_mins", 0)),
                sort_order=i,
                notes=seg.get("notes", ""),
            ))
        return sched, None

    covered = set()
    total_assignments = 0

    for rot in rotation_list:
        weeks = _json.loads(rot.weeks_json) if rot.weeks_json else []
        if not weeks:
            warnings.append(f"Rotation '{rot.name}' has no weeks defined — skipped.")
            continue
        cycle_len = len(weeks)
        if not rot.assignments:
            warnings.append(f"Rotation '{rot.name}' has no employees assigned.")
            continue

        for assign in rot.assignments:
            emp = assign.employee
            if not emp:
                _note("assignment has no employee")
                continue
            if str(emp.status or "Active").strip().lower() not in ("active", ""):
                _note(f"{emp.full_name} is {emp.status}")
                continue
            total_assignments += 1

            d = start_date
            while d <= end_date:
                anchor = assign.start_date or start_date
                days_since = (d - anchor).days
                if days_since < 0:
                    _note(f"{emp.full_name}: date before rotation start ({anchor})")
                    d += _dt.timedelta(days=1)
                    continue
                week_num = ((days_since // 7) + (assign.current_week or 0)) % cycle_len
                shifts_map = weeks[week_num].get("shifts", {})
                template_id = shifts_map.get(DAY_KEYS[d.weekday()])

                if not template_id or str(template_id).lower() in ("off", ""):
                    d += _dt.timedelta(days=1)
                    continue
                try:
                    tid = int(template_id)
                except (ValueError, TypeError):
                    _note(f"invalid template id '{template_id}'")
                    d += _dt.timedelta(days=1)
                    continue
                tmpl = templates.get(tid)
                if not tmpl:
                    warnings.append(f"Shift template {tid} not found for {emp.full_name} on {d}")
                    d += _dt.timedelta(days=1)
                    continue

                existing = Schedule.query.filter_by(employee_id=emp.id, schedule_date=d).first()
                if existing:
                    skipped += 1
                    covered.add((d, tmpl.shift_category or "any"))
                    d += _dt.timedelta(days=1)
                    continue

                sched, reason = _build_schedule(emp, d, tmpl, "scheduled")
                if sched:
                    created += 1
                    covered.add((d, tmpl.shift_category or "any"))
                else:
                    _note(f"{emp.full_name}: {reason}")
                d += _dt.timedelta(days=1)

    if total_assignments == 0:
        warnings.append("No active employees are assigned to any rotation — nothing to generate.")

    db.session.commit()

    # ── FILL-IN PASS ──────────────────────────────────────────
    from app.models import FillInRule, PTOEntry

    fill_in_created = 0
    fill_rules = FillInRule.query.filter_by(is_active=True).order_by(FillInRule.priority).all()

    if fill_rules:
        rules_by_cat = {}
        for rule in fill_rules:
            rules_by_cat.setdefault(rule.shift_category, []).append(rule)

        pto_entries = PTOEntry.query.filter(
            PTOEntry.start_date <= end_date,
            PTOEntry.end_date >= start_date,
        ).all()
        pto_dates = {}
        for pto in pto_entries:
            s = set()
            d = max(pto.start_date, start_date)
            while d <= min(pto.end_date, end_date):
                s.add(d)
                d += _dt.timedelta(days=1)
            pto_dates.setdefault(pto.employee_id, set()).update(s)

        for cat, rules in rules_by_cat.items():
            cat_templates = [t for t in templates.values()
                            if (t.shift_category or "any") == cat and t.is_active]
            if not cat_templates:
                continue

            DAY_TYPES = ["weekday"] * 5 + ["saturday", "sunday"]
            d = start_date
            while d <= end_date:
                day_name = DAY_TYPES[d.weekday()]
                day_templates = [ct for ct in cat_templates
                                 if (ct.day_type or "any") in ("any", day_name)]
                if not day_templates:
                    d += _dt.timedelta(days=1)
                    continue

                has_coverage = (d, cat) in covered
                if not has_coverage:
                    for es in Schedule.query.filter_by(schedule_date=d).all():
                        for ct in cat_templates:
                            if (es.shift_start and es.shift_end and
                                    es.shift_start.strftime("%H:%M") == ct.start_time and
                                    es.shift_end.strftime("%H:%M") == ct.end_time):
                                has_coverage = True
                                break
                        if has_coverage:
                            break

                if not has_coverage:
                    for rule in rules:
                        emp = rule.employee
                        if not emp or str(emp.status or "Active").strip().lower() not in ("active", ""):
                            continue
                        if emp.id in pto_dates and d in pto_dates[emp.id]:
                            continue
                        if Schedule.query.filter_by(employee_id=emp.id, schedule_date=d).first():
                            continue

                        tmpl = templates.get(rule.fallback_template_id) if rule.fallback_template_id else None
                        if not tmpl:
                            tmpl = day_templates[0]

                        sched, reason = _build_schedule(emp, d, tmpl, "fill-in")
                        if not sched:
                            _note(f"fill-in {emp.full_name}: {reason}")
                            continue
                        fill_in_created += 1
                        covered.add((d, cat))
                        warnings.append(f"Fill-in: {emp.full_name} covers {cat} on {d}")
                        break

                d += _dt.timedelta(days=1)

        db.session.commit()

    for reason, count in sorted(skip_notes.items(), key=lambda x: -x[1]):
        warnings.insert(0, f"Skipped {count} day(s) — {reason}")

    msg = f"Created {created} schedule(s), skipped {skipped} (already existed)."
    if fill_in_created:
        msg += f" Auto-filled {fill_in_created} gap(s)."

    return jsonify({
        "success": True,
        "created": created + fill_in_created,
        "skipped": skipped,
        "fill_ins": fill_in_created,
        "warnings": warnings,
        "message": msg,
    })


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
        else:
            return jsonify({"success": False, "error": f"Unknown assignment type: {assign_type}"})

        db.session.commit()
        return jsonify({"success": True})
    except Exception as e:
        db.session.rollback()
        err = str(e)
        if "uq_emp_pu" in err or "uq_emp_wtpm" in err or "uq_sel_emp" in err:
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
