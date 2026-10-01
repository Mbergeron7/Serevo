"""
scheduling/engine.py — Schedule generation & coverage analysis
==============================================================
Generates shift assignments for employees based on interval-level
requirements from the Capacity module, cross-referenced with employee
availability from the People module (accommodations + PTO).

Also produces coverage gap analysis: required vs. scheduled headcount
per interval, per day, per week, per month, and per year.
"""

import logging
import datetime
import math
from collections import defaultdict

log = logging.getLogger("serevo.scheduling")

# ── Shift defaults ──────────────────────────────────────────
DEFAULT_SHIFT_LENGTH_HRS = 8.5
DEFAULT_INTERVAL_MINS = 30
DAYS_OF_WEEK = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# ── Segment / activity defaults ────────────────────────────────
# Hardcoded fallbacks — used only when no DB segment codes have placement rules
SEGMENT_RULES_FULL = [
    {"type": "break",  "offset_mins": 120, "duration_mins": 15,
     "is_flexible": True, "window_start_mins": 90, "window_end_mins": 150},
    {"type": "lunch",  "offset_mins": 240, "duration_mins": 30,
     "is_flexible": True, "window_start_mins": 210, "window_end_mins": 300},
    {"type": "break",  "offset_mins": 360, "duration_mins": 15,
     "is_flexible": True, "window_start_mins": 330, "window_end_mins": 390},
]
SEGMENT_RULES_HALF = [
    {"type": "break",  "offset_mins": 120, "duration_mins": 15,
     "is_flexible": True, "window_start_mins": 90, "window_end_mins": 150},
]

ACTIVITY_TYPES = ["on-call", "break", "lunch", "meeting", "training", "other"]


def _get_segment_rules(shift_type="full"):
    """
    Load segment placement rules from the SegmentCode DB table.
    Falls back to hardcoded rules if no DB codes have placement data.
    Returns list of rule dicts sorted by offset_mins.
    """
    try:
        from app.models import SegmentCode
        codes = SegmentCode.query.filter(
            SegmentCode.is_active == True,
            SegmentCode.offset_mins.isnot(None),
            SegmentCode.duration_mins.isnot(None),
        ).order_by(SegmentCode.sort_order, SegmentCode.offset_mins).all()

        if not codes:
            return list(SEGMENT_RULES_FULL if shift_type == "full" else SEGMENT_RULES_HALF)

        rules = []
        for c in codes:
            rules.append({
                "type": c.code,
                "offset_mins": c.offset_mins,
                "duration_mins": c.duration_mins,
                "is_flexible": bool(c.is_flexible),
                "window_start_mins": c.window_start_mins or c.offset_mins,
                "window_end_mins": c.window_end_mins or c.offset_mins,
            })

        # For half shifts, only include segments that fit in ~half a shift
        if shift_type == "half":
            half_cutoff = int(DEFAULT_SHIFT_LENGTH_HRS * 60 / 2)
            rules = [r for r in rules if r["offset_mins"] < half_cutoff]

        rules.sort(key=lambda r: r["offset_mins"])
        return rules
    except Exception as e:
        log.warning(f"Segment rules load error: {e}")
        return list(SEGMENT_RULES_FULL if shift_type == "full" else SEGMENT_RULES_HALF)


# ═════════════════════════════════════════════════════════════
# DATA LOADING
# ═════════════════════════════════════════════════════════════

def _get_requirements_for_date(lob, date_obj, sheet=None):
    """
    Pull interval-level requirements for a LOB on a specific date.
    Returns list of {time: "HH:MM", agents_required: float}.

    Tries three sources in order:
      1. Stored requirements (DB or Sheet, via get_source())
      2. Stored forecast data → Erlang C to compute requirements on the fly
      3. Empty list (caller falls back to default shifts)
    """
    # --- 1. Try stored requirements ---
    try:
        from app.data_source import get_source
        src = get_source()
        intervals, err = src.get_requirements(lob, date_obj)
        if not err and intervals:
            results = []
            for row in intervals:
                ts = row.get("time", "")
                agents = float(row.get("agents_required", 0) or 0)
                if len(ts) > 10:
                    time_str = ts[11:16]
                else:
                    time_str = ts
                results.append({
                    "time": time_str,
                    "agents_required": agents,
                })
            if results:
                return results
        if err:
            log.info(f"Requirements not available for {lob} {date_obj}: {err}")
    except Exception as e:
        log.info(f"Requirements lookup error for {lob}: {e}")

    # --- 2. Fall back to forecast data → Erlang C ---
    try:
        from app.data_source import get_source
        src = get_source()
        forecast, f_err = src.get_forecast(lob, date_obj)
        if not f_err and forecast:
            from app.forecasting.engine import erlang_c_staffing
            results = []
            for row in forecast:
                ts = row.get("time", "")
                offered = float(row.get("offered", 0) or 0)
                aht = float(row.get("aht", 0) or 0)
                if offered > 0 and aht > 0:
                    ec = erlang_c_staffing(offered, aht)
                    agents = ec["agents_with_shrinkage"]
                else:
                    agents = 0
                if len(ts) > 10:
                    time_str = ts[11:16]
                else:
                    time_str = ts
                results.append({
                    "time": time_str,
                    "agents_required": agents,
                })
            if results:
                log.info(f"Generated requirements from forecast for {lob} {date_obj} "
                         f"({len(results)} intervals)")
                return results
        if f_err:
            log.info(f"Forecast also not available for {lob} {date_obj}: {f_err}")
    except Exception as e:
        log.info(f"Forecast fallback error for {lob}: {e}")

    return []


def _get_employees_for_lob(lob, sheet=None):
    """
    Return active employees assigned to a given LOB/skill.
    Each employee dict has: name, employee_id, lob.

    Strategy (stop at first that returns results):
    1. get_employees() → to_legacy_dict() → match "Latest Skill Name"
    2. DB join: PlanningUnit by name → Employee by planning_unit_id
    3. Google Sheet direct read → match "Latest Skill Name" column
    4. DB all employees + sheet LOB mapping (handles NULL planning_unit_id)
    """
    from app.data_source import normalize_lob
    lob_normalized = normalize_lob(lob.strip()).lower()

    def _is_active(status_val):
        return str(status_val).strip().lower() not in ("inactive", "terminated", "deleted")

    try:
        # ── Path 1: get_employees (legacy dict format) ──
        from app.people.manager import get_employees
        employees, err = get_employees(sheet)
        if err:
            employees = []
        results = []
        for emp in employees:
            if not _is_active(emp.get("Status", "")):
                continue
            skill = (emp.get("Latest Skill Name") or "").strip()
            if normalize_lob(skill).lower() != lob_normalized:
                continue
            first = str(emp.get("First Name", "")).strip()
            last = str(emp.get("Last Name", "")).strip()
            results.append({
                "name": f"{first} {last}".strip(),
                "employee_id": emp.get("Employee ID", ""),
                "lob": skill,
                "team_lead": emp.get("Team Lead", ""),
            })
        if results:
            return results

        # ── Path 2: DB join via PlanningUnit ──
        try:
            from app.models import db, Employee, PlanningUnit
            pu = PlanningUnit.query.filter(
                db.func.lower(PlanningUnit.name) == lob_normalized
            ).first()
            if pu:
                db_emps = Employee.query.filter_by(
                    planning_unit_id=pu.id
                ).filter(
                    db.func.lower(Employee.status).notin_(
                        ["inactive", "terminated", "deleted"]
                    )
                ).all()
                for e in db_emps:
                    results.append({
                        "name": e.full_name,
                        "employee_id": e.employee_id,
                        "lob": pu.name,
                        "team_lead": e.team_lead or "",
                    })
                if results:
                    log.info(f"LOB '{lob}': found {len(results)} via DB join")
                    return results
        except Exception as fb_err:
            log.warning(f"DB join fallback error: {fb_err}")

        # ── Path 3: Google Sheet direct read ──
        sheet_records = None
        try:
            from app.people.manager import _open_sheet
            sh, sh_err = _open_sheet()
            if sh and not sh_err:
                ws = sh.worksheet("EMPLOYEES")
                sheet_records = ws.get_all_records()
                for rec in sheet_records:
                    if not _is_active(rec.get("Status", "")):
                        continue
                    skill = (rec.get("Latest Skill Name") or "").strip()
                    if normalize_lob(skill).lower() != lob_normalized:
                        continue
                    first = str(rec.get("First Name", "")).strip()
                    last = str(rec.get("Last Name", "")).strip()
                    results.append({
                        "name": f"{first} {last}".strip(),
                        "employee_id": rec.get("Employee ID", ""),
                        "lob": skill,
                        "team_lead": rec.get("Team Lead", ""),
                    })
                if results:
                    log.info(f"LOB '{lob}': found {len(results)} via sheet direct read")
                    # Also repair planning_unit_id while we have the data
                    _try_repair_pu(sheet_records)
                    return results
        except Exception as sh_err:
            log.warning(f"Sheet fallback error: {sh_err}")

        # ── Path 4: DB employees + sheet LOB mapping ──
        # Handles the case where DB employees have NULL planning_unit_id
        # but the sheet knows which LOB they belong to
        if sheet_records is None:
            try:
                from app.people.manager import _open_sheet
                sh, sh_err = _open_sheet()
                if sh and not sh_err:
                    ws = sh.worksheet("EMPLOYEES")
                    sheet_records = ws.get_all_records()
            except Exception:
                pass

        if sheet_records:
            # Build employee_id → LOB from sheet
            sheet_lob_map = {}
            for rec in sheet_records:
                eid = str(rec.get("Employee ID", "")).strip()
                skill = (rec.get("Latest Skill Name") or "").strip()
                if eid and skill:
                    sheet_lob_map[eid] = skill

            if sheet_lob_map:
                try:
                    from app.models import Employee
                    all_emps = Employee.query.filter(
                        db.func.lower(Employee.status).notin_(
                            ["inactive", "terminated", "deleted"]
                        )
                    ).all()
                    for e in all_emps:
                        mapped_lob = sheet_lob_map.get(str(e.employee_id).strip(), "")
                        if normalize_lob(mapped_lob).lower() == lob_normalized:
                            results.append({
                                "name": e.full_name,
                                "employee_id": e.employee_id,
                                "lob": mapped_lob,
                                "team_lead": e.team_lead or "",
                            })
                    if results:
                        log.info(f"LOB '{lob}': found {len(results)} via DB+sheet mapping")
                        _try_repair_pu(sheet_records)
                        return results
                except Exception as e4:
                    log.warning(f"DB+sheet mapping error: {e4}")

        log.warning(f"LOB '{lob}': no employees found via any path")
        return results
    except Exception as e:
        log.warning(f"Employee load error: {e}")
        return []


def _try_repair_pu(sheet_records):
    """Best-effort: set planning_unit_id on employees missing it."""
    try:
        from app.models import db, Employee, PlanningUnit
        missing = Employee.query.filter(Employee.planning_unit_id.is_(None)).all()
        if not missing:
            return
        lob_map = {}
        for rec in sheet_records:
            eid = str(rec.get("Employee ID", "")).strip()
            skill = (rec.get("Latest Skill Name") or "").strip()
            if eid and skill:
                lob_map[eid] = skill
        pu_cache = {}
        updated = 0
        for emp in missing:
            lob_name = lob_map.get(str(emp.employee_id).strip())
            if not lob_name:
                continue
            if lob_name not in pu_cache:
                pu = PlanningUnit.query.filter(
                    db.func.lower(PlanningUnit.name) == lob_name.lower()
                ).first()
                if not pu:
                    pu = PlanningUnit(name=lob_name)
                    db.session.add(pu)
                    db.session.flush()
                pu_cache[lob_name] = pu
            emp.planning_unit_id = pu_cache[lob_name].id
            updated += 1
        if updated:
            db.session.commit()
            log.info(f"Repaired planning_unit_id for {updated} employees")
    except Exception as e:
        log.warning(f"PU repair error: {e}")
        try:
            db.session.rollback()
        except Exception:
            pass


def _get_db_availability(employee_db_id, day_of_week):
    """
    Check the EmployeeAvailability DB table for per-day constraints.
    Returns dict or None if no record exists.
    """
    try:
        from app.models import EmployeeAvailability
        row = EmployeeAvailability.query.filter_by(
            employee_id=employee_db_id, day_of_week=day_of_week
        ).first()
        if not row:
            return None
        return {
            "is_available": row.is_available,
            "earliest_start": row.earliest_start.strftime("%H:%M") if row.earliest_start else None,
            "latest_start": row.latest_start.strftime("%H:%M") if row.latest_start else None,
            "latest_end": row.latest_end.strftime("%H:%M") if row.latest_end else None,
        }
    except Exception:
        return None


def _get_rotation_shift(employee_db_id, date_obj):
    """
    Check if an employee has a rotation assignment and return the
    shift template for the given date based on their cycle position.
    Returns ShiftTemplate dict or None.
    """
    try:
        import json
        from app.models import RotationAssignment, ShiftTemplate
        assignment = RotationAssignment.query.filter_by(employee_id=employee_db_id).first()
        if not assignment or not assignment.pattern or not assignment.pattern.is_active:
            return None

        pattern = assignment.pattern
        weeks = json.loads(pattern.weeks_json) if pattern.weeks_json else []
        if not weeks:
            return None

        # Determine which week in the cycle this date falls on
        cycle_weeks = pattern.cycle_weeks or len(weeks)
        if cycle_weeks < 1:
            cycle_weeks = 1

        # Calculate weeks elapsed since the assignment's start_date (or pattern creation)
        ref_date = assignment.start_date or (pattern.created_at.date() if pattern.created_at else date_obj)
        days_elapsed = (date_obj - ref_date).days
        if days_elapsed < 0:
            days_elapsed = 0
        weeks_elapsed = days_elapsed // 7
        current_week_idx = (assignment.current_week + weeks_elapsed) % cycle_weeks

        if current_week_idx >= len(weeks):
            return None

        week_def = weeks[current_week_idx]
        day_name = DAYS_OF_WEEK[date_obj.weekday()].lower()[:3]

        # week_def.shifts is {mon: template_id|null, ...}
        shifts_map = week_def.get("shifts", {})
        template_id = shifts_map.get(day_name)
        if template_id in (None, "", "off", 0, "0"):
            # Employee IS on a rotation and the rotation says this is a day off
            return {"off": True, "name": pattern.name}

        try:
            template = ShiftTemplate.query.get(int(template_id))
        except (TypeError, ValueError):
            template = None
        if not template:
            return None

        return {
            "start": template.start_time,
            "end": template.end_time,
            "hours": template.hours or 8.0,
            "type": template.shift_type or "full",
            "name": template.name,
        }
    except Exception:
        return None


def _resolve_employee_db_id(employee_ext_id):
    """Look up the DB primary key for an employee by their external employee_id."""
    try:
        from app.models import Employee
        emp = Employee.query.filter_by(employee_id=employee_ext_id).first()
        return emp.id if emp else None
    except Exception:
        return None


def _get_availability(employee_name, date_obj, availability_map=None, employee_ext_id=None):
    """
    Check if an employee is available on a given date and what
    restrictions they have. Merges:
      1. PTO (highest priority — employee is off)
      2. Accommodations from people manager (day-level off/half/fill, shift times)
      3. EmployeeAvailability DB table (per-day windows: earliest/latest start, latest end)

    Returns dict:
      {available: bool, day_type: "fill"|"off"|"half",
       shift_start: str|None, shift_end: str|None,
       earliest_start: str|None, latest_start: str|None, latest_end: str|None}
    """
    if availability_map is None:
        try:
            from app.people.manager import get_availability_map
            availability_map = get_availability_map()
        except Exception:
            return {"available": True, "day_type": "fill",
                    "shift_start": None, "shift_end": None,
                    "earliest_start": None, "latest_start": None, "latest_end": None}

    info = availability_map.get(employee_name, {})

    # Check PTO first
    pto_dates = info.get("pto_dates", set())
    if date_obj in pto_dates:
        return {"available": False, "day_type": "off",
                "shift_start": None, "shift_end": None,
                "earliest_start": None, "latest_start": None, "latest_end": None}

    # Start with defaults
    result = {
        "available": True, "day_type": "fill",
        "shift_start": None, "shift_end": None,
        "earliest_start": None, "latest_start": None, "latest_end": None,
    }

    # Check accommodations
    accom = info.get("accommodations", {})
    if accom:
        day_name = DAYS_OF_WEEK[date_obj.weekday()]
        day_val = str(accom.get(day_name, "fill")).strip().lower()
        if day_val == "off":
            log.info(f"  → {employee_name} marked off by accommodation ({day_name}={day_val})")
            return {"available": False, "day_type": "off",
                    "shift_start": None, "shift_end": None,
                    "earliest_start": None, "latest_start": None, "latest_end": None}
        result["day_type"] = day_val if day_val in ("fill", "half") else "fill"
        result["shift_start"] = accom.get("Shift Start") or None
        result["shift_end"] = accom.get("Shift End") or None

    # Check EmployeeAvailability DB table (overrides/supplements accommodations)
    if employee_ext_id:
        db_id = _resolve_employee_db_id(employee_ext_id)
        if db_id:
            day_of_week = date_obj.weekday()  # 0=Mon ... 6=Sun
            db_avail = _get_db_availability(db_id, day_of_week)
            if db_avail:
                if not db_avail["is_available"]:
                    log.info(f"  → {employee_name} marked unavailable by EmployeeAvailability "
                             f"(day_of_week={day_of_week})")
                    return {"available": False, "day_type": "off",
                            "shift_start": None, "shift_end": None,
                            "earliest_start": None, "latest_start": None, "latest_end": None}
                result["earliest_start"] = db_avail.get("earliest_start")
                result["latest_start"] = db_avail.get("latest_start")
                result["latest_end"] = db_avail.get("latest_end")

    return result


def _explain_unavailability(employee_name, date_obj, availability_map, employee_ext_id=None):
    """Return a human-readable reason why an employee is unavailable on a date."""
    info = availability_map.get(employee_name, {})

    # Check PTO
    pto_dates = info.get("pto_dates", set())
    if date_obj in pto_dates:
        return "PTO"

    # Check accommodations
    accom = info.get("accommodations", {})
    if accom:
        day_name = DAYS_OF_WEEK[date_obj.weekday()]
        day_val = str(accom.get(day_name, "fill")).strip().lower()
        if day_val == "off":
            return f"accommodation ({day_name}=off)"

    # Check DB availability
    if employee_ext_id:
        db_id = _resolve_employee_db_id(employee_ext_id)
        if db_id:
            day_of_week = date_obj.weekday()
            db_avail = _get_db_availability(db_id, day_of_week)
            if db_avail and not db_avail["is_available"]:
                return f"EmployeeAvailability (day {day_of_week}=unavailable)"

    return "unknown reason"


# ═════════════════════════════════════════════════════════════
# SHIFT GENERATION
# ═════════════════════════════════════════════════════════════

def _time_to_minutes(time_str):
    """Convert 'HH:MM' to minutes from midnight."""
    try:
        parts = time_str.split(":")
        return int(parts[0]) * 60 + int(parts[1])
    except Exception:
        return 0


def _minutes_to_time(mins):
    """Convert minutes from midnight to 'HH:MM'."""
    h = mins // 60
    m = mins % 60
    return f"{h:02d}:{m:02d}"


def _generate_segments(start_time, end_time, shift_type, stagger_index=0, total_employees=1):
    """
    Build the segments list for a shift, inserting break and lunch
    windows into on-call blocks.

    For flexible segments (breaks/lunch with windows), the stagger_index
    distributes placement across the window so not everyone takes break
    at the same time. Employee 0 gets the earliest slot, employee N gets
    a later slot, evenly spread across the window.

    Args:
        start_time: "HH:MM" shift start
        end_time: "HH:MM" shift end
        shift_type: "full" or "half"
        stagger_index: this employee's index (0-based) for staggering
        total_employees: total number of employees being scheduled (for stagger spread)

    Returns list of:
      {type: "on-call"|"break"|"lunch", start: "HH:MM", end: "HH:MM",
       duration_mins: int}
    """
    s_min = _time_to_minutes(start_time)
    e_min = _time_to_minutes(end_time)

    rules = _get_segment_rules(shift_type)

    # Collect break/lunch windows that fit within the shift
    pauses = []
    for rule in rules:
        is_flex = rule.get("is_flexible", False)
        duration = rule["duration_mins"]

        if is_flex and total_employees > 1:
            # Stagger within the window
            win_start = rule.get("window_start_mins", rule["offset_mins"])
            win_end = rule.get("window_end_mins", rule["offset_mins"])
            # Available window for start times (must leave room for duration)
            window_range = max(0, win_end - win_start)
            if total_employees > 1 and window_range > 0:
                step = window_range / total_employees
                offset = win_start + int(step * stagger_index)
            else:
                offset = rule["offset_mins"]
        else:
            offset = rule["offset_mins"]

        p_start = s_min + offset
        p_end = p_start + duration
        if p_end <= e_min:
            pauses.append({
                "type": rule["type"],
                "start_min": p_start,
                "end_min": p_end,
                "duration_mins": duration,
            })

    # Build segments: fill gaps between pauses with on-call
    segments = []
    cursor = s_min
    for p in sorted(pauses, key=lambda x: x["start_min"]):
        # Resolve overlaps — if this pause starts before cursor, push it forward
        if p["start_min"] < cursor:
            p["start_min"] = cursor
            p["end_min"] = cursor + p["duration_mins"]
            if p["end_min"] > e_min:
                continue  # doesn't fit, skip

        if cursor < p["start_min"]:
            segments.append({
                "type": "on-call",
                "start": _minutes_to_time(cursor),
                "end": _minutes_to_time(p["start_min"]),
                "duration_mins": p["start_min"] - cursor,
            })
        segments.append({
            "type": p["type"],
            "start": _minutes_to_time(p["start_min"]),
            "end": _minutes_to_time(p["end_min"]),
            "duration_mins": p["duration_mins"],
        })
        cursor = p["end_min"]

    # Trailing on-call block
    if cursor < e_min:
        segments.append({
            "type": "on-call",
            "start": _minutes_to_time(cursor),
            "end": _minutes_to_time(e_min),
            "duration_mins": e_min - cursor,
        })

    return segments


def generate_shifts(lob, date_obj, shift_length_hrs=None, sheet=None, employee_ids=None):
    """
    Generate shift assignments for a LOB on a given date.

    Algorithm:
    1. Load interval requirements for the date
    2. Load available employees for the LOB
    3. Find the peak requirement to determine how many shifts needed
    4. Assign employees to shifts that best cover the requirement curve
    5. Respect accommodations (restricted hours, half days)
    6. Respect PTO (skip unavailable employees)

    Returns:
        shifts: list of {employee, start, end, hours, type}
        unassigned: list of employee names with no shift
        warnings: list of strings
    """
    if shift_length_hrs is None:
        shift_length_hrs = DEFAULT_SHIFT_LENGTH_HRS

    shift_length_mins = int(shift_length_hrs * 60)
    requirements = _get_requirements_for_date(lob, date_obj, sheet)
    all_employees = _get_employees_for_lob(lob, sheet)
    employees = list(all_employees)
    warnings = []
    log.info(f"generate_shifts: {lob} on {date_obj}: {len(employees)} employees found, "
             f"{len(requirements)} requirement intervals")

    # Filter by selected employee IDs if provided
    if employee_ids:
        id_set = set(str(eid) for eid in employee_ids)
        employees = [e for e in employees if str(e.get("employee_id", "")) in id_set]
        log.info(f"  Filtered to {len(employees)} by employee_ids selection")

    if not employees:
        warnings.append(f"No active employees found for {lob}")
        return [], [], warnings

    # Build availability map once
    try:
        from app.people.manager import get_availability_map
        avail_map = get_availability_map(sheet)
    except Exception:
        avail_map = {}

    # If no requirements data, create a basic schedule anyway
    if not requirements:
        warnings.append(f"No requirements data for {lob} on {date_obj}. "
                        "Generating default 08:00–16:00 shifts for available staff.")
        shifts = []
        unassigned = []
        skip_reasons = []
        for emp in employees:
            avail = _get_availability(emp["name"], date_obj, avail_map,
                                      employee_ext_id=emp.get("employee_id"))
            if not avail["available"]:
                reason = _explain_unavailability(emp["name"], date_obj, avail_map,
                                                 emp.get("employee_id"))
                skip_reasons.append(f"{emp['name']}: {reason}")
                unassigned.append(emp["name"])
                continue

            # Check for rotation-assigned shift template
            db_id = _resolve_employee_db_id(emp.get("employee_id"))
            rot_shift = _get_rotation_shift(db_id, date_obj) if db_id else None
            if rot_shift and rot_shift.get("off"):
                skip_reasons.append(f"{emp['name']}: rotation '{rot_shift.get('name')}' day off")
                unassigned.append(emp["name"])
                continue

            if rot_shift:
                start = rot_shift["start"].strftime("%H:%M") if hasattr(rot_shift["start"], "strftime") else str(rot_shift["start"])[:5]
                end = rot_shift["end"].strftime("%H:%M") if hasattr(rot_shift["end"], "strftime") else str(rot_shift["end"])[:5]
                hours = rot_shift.get("hours", shift_length_hrs)
                stype = rot_shift.get("type", "full")
            else:
                start = avail.get("shift_start") or "08:00"
                # Apply earliest_start constraint
                if avail.get("earliest_start"):
                    es_min = _time_to_minutes(avail["earliest_start"])
                    if _time_to_minutes(start) < es_min:
                        start = avail["earliest_start"]
                if avail["day_type"] == "half":
                    end_mins = _time_to_minutes(start) + (shift_length_mins // 2)
                    end = _minutes_to_time(end_mins)
                    hours = shift_length_hrs / 2
                    stype = "half"
                else:
                    end_mins = _time_to_minutes(start) + shift_length_mins
                    end = avail.get("shift_end") or _minutes_to_time(end_mins)
                    hours = shift_length_hrs
                    stype = "full"
                # Cap end time at latest_end
                if avail.get("latest_end"):
                    le_min = _time_to_minutes(avail["latest_end"])
                    if _time_to_minutes(end) > le_min:
                        end = avail["latest_end"]
                        hours = round((_time_to_minutes(end) - _time_to_minutes(start)) / 60, 1)
            shifts.append({
                "employee": emp["name"],
                "employee_id": emp["employee_id"],
                "start": start,
                "end": end,
                "hours": hours,
                "type": stype,
                "status": "scheduled",
                "team_lead": emp.get("team_lead", ""),
                "segments": [],  # filled below with stagger
            })

        # Apply staggered segments across all scheduled employees
        total_scheduled = len(shifts)
        for idx, shift in enumerate(shifts):
            shift["segments"] = _generate_segments(
                shift["start"], shift["end"], shift["type"],
                stagger_index=idx, total_employees=total_scheduled)

        warnings.extend(_apply_fill_in_rules(lob, date_obj, shifts, employees, avail_map))
        if skip_reasons:
            warnings.append(f"Skipped {len(skip_reasons)} employee(s): " +
                            "; ".join(skip_reasons))
        return shifts, unassigned, warnings

    # Sort requirements by time
    requirements.sort(key=lambda r: r["time"])

    # Determine the operational window from requirements
    req_times = [r["time"] for r in requirements if r["agents_required"] > 0]
    if not req_times:
        warnings.append("All requirements are zero for this date.")
        return [], [e["name"] for e in employees], warnings

    ops_start = req_times[0]
    ops_end_mins = _time_to_minutes(req_times[-1]) + DEFAULT_INTERVAL_MINS
    ops_end = _minutes_to_time(ops_end_mins)

    # Build a minute-level requirement map
    req_by_minute = {}
    for r in requirements:
        t = _time_to_minutes(r["time"])
        for m in range(t, t + DEFAULT_INTERVAL_MINS):
            req_by_minute[m] = r["agents_required"]

    # Determine possible shift start times (every 30 min within ops window)
    ops_start_mins = _time_to_minutes(ops_start)
    possible_starts = list(range(ops_start_mins,
                                  ops_end_mins - shift_length_mins + 1,
                                  DEFAULT_INTERVAL_MINS))
    if not possible_starts:
        possible_starts = [ops_start_mins]

    # Score each possible start time by how much requirement it covers
    def score_start(start_min, length_mins):
        """Sum of requirements covered by a shift starting at start_min."""
        total = 0
        for m in range(start_min, start_min + length_mins, DEFAULT_INTERVAL_MINS):
            total += req_by_minute.get(m, 0)
        return total

    # Track how many agents are already scheduled per interval
    scheduled_per_interval = defaultdict(int)

    # Sort employees: those with accommodation restrictions first (they have
    # fewer placement options), then unrestricted employees
    emp_avails = []
    unassigned = []
    log.info(f"Scheduling {len(employees)} employees for {lob} on {date_obj}")
    skip_reasons = []
    for emp in employees:
        avail = _get_availability(emp["name"], date_obj, avail_map,
                                  employee_ext_id=emp.get("employee_id"))
        if not avail["available"]:
            reason = _explain_unavailability(emp["name"], date_obj, avail_map,
                                             emp.get("employee_id"))
            log.info(f"  SKIP {emp['name']} ({reason})")
            skip_reasons.append(f"{emp['name']}: {reason}")
            unassigned.append(emp["name"])
            continue
        # Resolve DB id and check for rotation shift
        db_id = _resolve_employee_db_id(emp.get("employee_id"))
        rot_shift = _get_rotation_shift(db_id, date_obj) if db_id else None
        if rot_shift and rot_shift.get("off"):
            log.info(f"  SKIP {emp['name']} (rotation day off)")
            skip_reasons.append(f"{emp['name']}: rotation '{rot_shift.get('name')}' day off")
            unassigned.append(emp["name"])
            continue
        emp_avails.append((emp, avail, rot_shift))

    # Restricted employees first (have shift_start, half day, or rotation)
    emp_avails.sort(key=lambda x: (
        0 if x[2] or x[1].get("shift_start") or x[1]["day_type"] == "half" else 1
    ))

    shifts = []
    for emp, avail, rot_shift in emp_avails:
        # ── Rotation-assigned shift takes priority ──
        if rot_shift:
            rs = rot_shift["start"]
            re = rot_shift["end"]
            start_min = _time_to_minutes(rs.strftime("%H:%M") if hasattr(rs, "strftime") else str(rs)[:5])
            end_min = _time_to_minutes(re.strftime("%H:%M") if hasattr(re, "strftime") else str(re)[:5])
            length = end_min - start_min
            if length <= 0:
                length = shift_length_mins
                end_min = start_min + length
        else:
            if avail["day_type"] == "half":
                length = shift_length_mins // 2
            else:
                length = shift_length_mins

            # Build constrained possible_starts based on availability windows
            emp_possible_starts = list(possible_starts)  # copy
            if avail.get("earliest_start"):
                es_min = _time_to_minutes(avail["earliest_start"])
                emp_possible_starts = [s for s in emp_possible_starts if s >= es_min]
            if avail.get("latest_start"):
                ls_min = _time_to_minutes(avail["latest_start"])
                emp_possible_starts = [s for s in emp_possible_starts if s <= ls_min]
            if not emp_possible_starts:
                # Fall back to original list if constraints eliminated everything
                emp_possible_starts = list(possible_starts) if possible_starts else [ops_start_mins]

            # If employee has a fixed shift start from accommodations, use it
            if avail.get("shift_start"):
                start_min = _time_to_minutes(avail["shift_start"])
                # Still respect earliest_start
                if avail.get("earliest_start"):
                    es_min = _time_to_minutes(avail["earliest_start"])
                    start_min = max(start_min, es_min)
                end_min = start_min + length
                if avail.get("shift_end"):
                    end_min = min(end_min, _time_to_minutes(avail["shift_end"]))
                    length = end_min - start_min
            else:
                # Find the best start time: maximize coverage of unmet demand
                best_start = emp_possible_starts[0] if emp_possible_starts else ops_start_mins
                best_score = -1
                for s in emp_possible_starts:
                    score = 0
                    for m in range(s, s + length, DEFAULT_INTERVAL_MINS):
                        req = req_by_minute.get(m, 0)
                        already = scheduled_per_interval.get(m, 0)
                        gap = req - already
                        if gap > 0:
                            score += gap
                    if score > best_score:
                        best_score = score
                        best_start = s

                start_min = best_start
                end_min = start_min + length

            # Cap end time at latest_end from availability
            if avail.get("latest_end"):
                le_min = _time_to_minutes(avail["latest_end"])
                if end_min > le_min:
                    end_min = le_min
                    length = end_min - start_min

        # Record the shift
        for m in range(start_min, end_min, DEFAULT_INTERVAL_MINS):
            scheduled_per_interval[m] += 1

        stype = "half" if avail["day_type"] == "half" else "full"
        s_time = _minutes_to_time(start_min)
        e_time = _minutes_to_time(end_min)
        shifts.append({
            "employee": emp["name"],
            "employee_id": emp["employee_id"],
            "start": s_time,
            "end": e_time,
            "hours": round(length / 60, 1),
            "type": stype,
            "status": "scheduled",
            "team_lead": emp.get("team_lead", ""),
            "segments": [],  # filled below with stagger
        })

    # Apply staggered segments across all scheduled employees
    total_scheduled = len(shifts)
    for idx, shift in enumerate(shifts):
        shift["segments"] = _generate_segments(
            shift["start"], shift["end"], shift["type"],
            stagger_index=idx, total_employees=total_scheduled)

    # Fill-in / backup rules: cover shift categories nobody landed on
    fill_warnings = _apply_fill_in_rules(lob, date_obj, shifts, employees, avail_map)
    warnings.extend(fill_warnings)

    # Sort shifts by start time, then employee name
    shifts.sort(key=lambda s: (s["start"], s["employee"]))

    if skip_reasons:
        warnings.append(f"Skipped {len(skip_reasons)} employee(s): " +
                        "; ".join(skip_reasons))

    return shifts, unassigned, warnings


def _apply_fill_in_rules(lob, date_obj, shifts, employees, avail_map):
    """
    After the main assignment pass, check each shift category that has
    FillInRule entries (e.g. "closing"). If no scheduled shift matches a
    template in that category for this day, schedule the highest-priority
    available fill-in employee on that template. Mutates `shifts`.
    Returns a list of warning strings describing fill-ins made.
    """
    notes = []
    try:
        from app.models import FillInRule, ShiftTemplate, PlanningUnit, db
    except Exception:
        return notes

    try:
        rules = FillInRule.query.filter_by(is_active=True).order_by(FillInRule.priority).all()
        if not rules:
            return notes

        pu = PlanningUnit.query.filter(db.func.lower(PlanningUnit.name) == lob.lower()).first()
        pu_id = pu.id if pu else None
        templates = ShiftTemplate.query.filter_by(is_active=True).all()
        day_name = ["weekday"] * 5 + ["saturday", "sunday"]
        day_name = day_name[date_obj.weekday()]

        rules_by_cat = {}
        for r in rules:
            # LOB-specific rules only apply to their LOB
            if r.planning_unit_id and pu_id and r.planning_unit_id != pu_id:
                continue
            rules_by_cat.setdefault(r.shift_category, []).append(r)

        scheduled_ids = {str(s["employee_id"]) for s in shifts}
        lob_emp_ids = {str(e.get("employee_id", "")) for e in employees}

        for cat, cat_rules in rules_by_cat.items():
            cat_templates = [t for t in templates
                             if (t.shift_category or "any") == cat
                             and (t.day_type or "any") in ("any", day_name)
                             and (not t.planning_unit_id or t.planning_unit_id == pu_id)]
            if not cat_templates:
                continue  # category doesn't operate today

            covered = any(
                s["start"] == t.start_time and s["end"] == t.end_time
                for s in shifts for t in cat_templates
            )
            if covered:
                continue

            for rule in cat_rules:
                emp = rule.employee
                if not emp or str(emp.status or "Active").lower() not in ("active", ""):
                    continue
                ext_id = str(emp.employee_id)
                if ext_id in scheduled_ids or ext_id not in lob_emp_ids:
                    continue
                avail = _get_availability(emp.full_name, date_obj, avail_map, employee_ext_id=ext_id)
                if not avail["available"]:
                    continue

                tmpl = rule.fallback_template if rule.fallback_template else cat_templates[0]
                s_min = _time_to_minutes(tmpl.start_time)
                e_min = _time_to_minutes(tmpl.end_time)
                if avail.get("shift_start"):
                    s_min = _time_to_minutes(avail["shift_start"])
                if avail.get("shift_end"):
                    e_min = _time_to_minutes(avail["shift_end"])
                if avail.get("latest_end"):
                    e_min = min(e_min, _time_to_minutes(avail["latest_end"]))
                if e_min <= s_min:
                    continue
                stype = "half" if avail["day_type"] == "half" else (tmpl.shift_type or "full")
                shifts.append({
                    "employee": emp.full_name,
                    "employee_id": ext_id,
                    "start": _minutes_to_time(s_min),
                    "end": _minutes_to_time(e_min),
                    "hours": round((e_min - s_min) / 60, 1),
                    "type": stype,
                    "status": "scheduled",
                    "team_lead": emp.team_lead or "",
                    "segments": _generate_segments(_minutes_to_time(s_min), _minutes_to_time(e_min),
                                                   stype, stagger_index=len(shifts),
                                                   total_employees=len(shifts) + 1),
                    "fill_in": True,
                })
                scheduled_ids.add(ext_id)
                notes.append(f"Fill-in: {emp.full_name} covers {cat} ({tmpl.name})")
                break
    except Exception as e:
        log.warning(f"Fill-in rules error: {e}")
    return notes


# ═════════════════════════════════════════════════════════════
# COVERAGE ANALYSIS
# ═════════════════════════════════════════════════════════════

def analyze_coverage(lob, date_obj, shifts, sheet=None):
    """
    Compare scheduled headcount vs. requirements per interval.

    Returns list of:
      {time, required, scheduled, gap, coverage_pct}
    where gap = scheduled - required (negative = understaffed).
    """
    requirements = _get_requirements_for_date(lob, date_obj, sheet)

    # Build scheduled count per interval
    scheduled_map = defaultdict(int)
    for shift in shifts:
        s_min = _time_to_minutes(shift["start"])
        e_min = _time_to_minutes(shift["end"])
        for m in range(s_min, e_min, DEFAULT_INTERVAL_MINS):
            t = _minutes_to_time(m)
            scheduled_map[t] += 1

    coverage = []
    if requirements:
        # Compare scheduled vs required
        for r in requirements:
            t = r["time"]
            req = r["agents_required"]
            sched = scheduled_map.get(t, 0)
            gap = sched - req
            pct = round((sched / req) * 100, 1) if req > 0 else (100.0 if sched > 0 else 0)
            coverage.append({
                "time": t,
                "required": round(req, 1),
                "scheduled": sched,
                "gap": round(gap, 1),
                "coverage_pct": pct,
            })
    elif scheduled_map:
        # No requirements data — show scheduled headcount only
        for t in sorted(scheduled_map.keys()):
            sched = scheduled_map[t]
            coverage.append({
                "time": t,
                "required": 0,
                "scheduled": sched,
                "gap": sched,
                "coverage_pct": 100.0,
            })

    return coverage


def coverage_summary(coverage_intervals):
    """
    Summarize a day's coverage into headline stats.
    Returns dict with: avg_coverage_pct, peak_required, peak_gap,
                        understaffed_intervals, total_intervals
    """
    if not coverage_intervals:
        return {
            "avg_coverage_pct": 0,
            "peak_required": 0,
            "peak_gap": 0,
            "understaffed_intervals": 0,
            "total_intervals": 0,
        }

    total = len(coverage_intervals)
    understaffed = sum(1 for c in coverage_intervals if c["gap"] < 0)
    peak_req = max(c["required"] for c in coverage_intervals)
    peak_gap = min(c["gap"] for c in coverage_intervals)  # most negative
    avg_pct = sum(c["coverage_pct"] for c in coverage_intervals) / total

    return {
        "avg_coverage_pct": round(avg_pct, 1),
        "peak_required": round(peak_req, 1),
        "peak_gap": round(peak_gap, 1),
        "understaffed_intervals": understaffed,
        "total_intervals": total,
    }


# ═════════════════════════════════════════════════════════════
# MULTI-DAY AGGREGATION
# ═════════════════════════════════════════════════════════════

def generate_schedule_range(lob, start_date, end_date, shift_length_hrs=None, sheet=None, employee_ids=None):
    """
    Generate shifts and coverage for a date range.
    Returns {
        days: [{date, shifts, unassigned, coverage, summary, warnings}],
        totals: {total_shifts, total_hours, avg_coverage, ...}
    }
    """
    if shift_length_hrs is None:
        shift_length_hrs = DEFAULT_SHIFT_LENGTH_HRS

    days = []
    d = start_date
    total_shifts = 0
    total_hours = 0.0
    total_coverage_sum = 0.0
    total_days_with_coverage = 0
    all_employees_seen = set()
    all_scheduled = set()

    _temp_id_counter = 0
    while d <= end_date:
        shifts, unassigned, warnings = generate_shifts(
            lob, d, shift_length_hrs, sheet, employee_ids)
        # Assign temporary IDs so the UI can reference unsaved shifts
        for s in shifts:
            if "id" not in s:
                _temp_id_counter += 1
                s["id"] = f"tmp_{_temp_id_counter}"
        for s in shifts:
            all_scheduled.add(s["employee"])
        for u in unassigned:
            all_employees_seen.add(u)
        for s in shifts:
            all_employees_seen.add(s["employee"])
        coverage = analyze_coverage(lob, d, shifts, sheet)
        summary = coverage_summary(coverage)

        total_shifts += len(shifts)
        total_hours += sum(s["hours"] for s in shifts)
        if summary["total_intervals"] > 0:
            total_coverage_sum += summary["avg_coverage_pct"]
            total_days_with_coverage += 1

        days.append({
            "date": d.strftime("%Y-%m-%d"),
            "date_label": d.strftime("%a %b %d"),
            "day_of_week": DAYS_OF_WEEK[d.weekday()],
            "shifts": shifts,
            "unassigned": unassigned,
            "coverage": coverage,
            "summary": summary,
            "warnings": warnings,
        })
        d += datetime.timedelta(days=1)

    avg_coverage = (round(total_coverage_sum / total_days_with_coverage, 1)
                    if total_days_with_coverage > 0 else 0)

    never_scheduled = all_employees_seen - all_scheduled
    global_warnings = []
    if never_scheduled:
        global_warnings.append(
            f"{len(never_scheduled)} employee(s) were never scheduled across "
            f"the entire range: {', '.join(sorted(never_scheduled))}. "
            "Check their PTO, accommodations, and availability settings."
        )

    return {
        "lob": lob,
        "start_date": start_date.strftime("%Y-%m-%d"),
        "end_date": end_date.strftime("%Y-%m-%d"),
        "days": days,
        "totals": {
            "total_shifts": total_shifts,
            "total_hours": round(total_hours, 1),
            "total_days": len(days),
            "avg_coverage_pct": avg_coverage,
        },
        "warnings": global_warnings,
    }


def get_available_lobs(sheet=None):
    """Return sorted list of distinct LOB names from the employee roster."""
    try:
        from app.people.manager import get_employees
        employees, err = get_employees(sheet)
        if err:
            employees = []
        lobs = set()
        for emp in employees:
            status = str(emp.get("Status", "")).strip().lower()
            if status in ("inactive", "terminated", "deleted"):
                continue
            lob = (emp.get("Latest Skill Name") or "").strip()
            if lob:
                lobs.add(lob)
        # Fallback 1: pull directly from PlanningUnit table
        if not lobs:
            try:
                from app.models import PlanningUnit
                units = PlanningUnit.query.all()
                for u in units:
                    name = (u.name or "").strip()
                    if name:
                        lobs.add(name)
            except Exception:
                pass
        # Fallback 2: try the Google Sheet directly if still no LOBs
        if not lobs and sheet is None:
            try:
                from app.people.manager import _open_sheet
                sh, sh_err = _open_sheet()
                if sh and not sh_err:
                    ws = sh.worksheet("EMPLOYEES")
                    records = ws.get_all_records()
                    for emp in records:
                        status = str(emp.get("Status", "")).strip().lower()
                        if status in ("inactive", "terminated", "deleted"):
                            continue
                        lob = (emp.get("Latest Skill Name") or "").strip()
                        if lob:
                            lobs.add(lob)
            except Exception:
                pass
        return sorted(lobs)
    except Exception:
        return []
