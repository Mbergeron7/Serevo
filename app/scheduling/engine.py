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
        log.info(f"[REQ-DEBUG] Looking up requirements for lob={lob!r} date={date_obj}")
        intervals, err = src.get_requirements(lob, date_obj)
        log.info(f"[REQ-DEBUG] Got {len(intervals) if intervals else 0} intervals, err={err}")
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


def get_multi_skill_requirements(lob, date_obj, sheet=None):
    """
    Calculate per-skill-group staffing requirements for a LOB.

    If the LOB has multiple skill groups mapped, splits the total
    requirement proportionally based on how many employees hold each
    skill group (weighted by proficiency effectiveness).

    Returns:
      {
        skill_group_name: [{time: "HH:MM", agents_required: float}, ...],
        ...
      }
    If no multi-skill split applies, returns {lob: requirements}.
    """
    base_reqs = _get_requirements_for_date(lob, date_obj, sheet)
    if not base_reqs:
        return {lob: []}

    # Check if there are multiple skill groups with employees mapped to this LOB
    try:
        from app.models import SkillGroup, SkillMapping, Employee, PlanningUnit, db
        from app.data_source import normalize_lob

        lob_norm = normalize_lob(lob.strip()).lower()

        # Find the planning unit for this LOB
        pu = PlanningUnit.query.filter(
            db.func.lower(PlanningUnit.name) == lob_norm
        ).first()

        if not pu:
            return {lob: base_reqs}

        # Get all employees in this LOB and their skill mappings
        emps = Employee.query.filter_by(planning_unit_id=pu.id).filter(
            db.func.lower(Employee.status).notin_(["inactive", "terminated", "deleted"])
        ).all()

        if not emps:
            return {lob: base_reqs}

        # Calculate effective capacity per skill group
        skill_capacity = defaultdict(float)  # skill_group_name → total effectiveness
        total_capacity = 0.0

        for emp in emps:
            mappings = SkillMapping.query.filter_by(
                employee_id=emp.id, is_active=True
            ).join(SkillGroup).all()

            if mappings:
                for m in mappings:
                    eff = proficiency_to_effectiveness(m.proficiency)
                    skill_capacity[m.skill_group.name] += eff
                    total_capacity += eff
            else:
                # No explicit mapping — count under primary LOB
                skill_capacity[lob] += 1.0
                total_capacity += 1.0

        if len(skill_capacity) <= 1 or total_capacity == 0:
            return {lob: base_reqs}

        # Split requirements proportionally by skill capacity
        result = {}
        for sg_name, cap in skill_capacity.items():
            ratio = cap / total_capacity
            result[sg_name] = [
                {
                    "time": r["time"],
                    "agents_required": round(r["agents_required"] * ratio, 2),
                }
                for r in base_reqs
            ]

        log.info(f"Multi-skill split for {lob}: {len(skill_capacity)} groups, "
                 f"ratios: {', '.join(f'{k}={v/total_capacity:.1%}' for k, v in skill_capacity.items())}")
        return result

    except Exception as e:
        log.warning(f"Multi-skill requirements error: {e}")
        return {lob: base_reqs}


def get_skill_proficiency_map(lob=None):
    """
    Load skill proficiency mappings from the DB.
    Returns dict: employee_db_id → {skill_group_name: proficiency (1-5)}.
    If lob is given, also returns multi-skilled employees who have that
    skill group in their mappings (even if their primary LOB differs).
    """
    try:
        from app.models import SkillGroup, SkillMapping, Employee
        query = SkillMapping.query.filter_by(is_active=True).join(SkillGroup)
        if lob:
            from app.data_source import normalize_lob
            lob_norm = normalize_lob(lob.strip()).lower()
            # Get all skill groups, then filter mappings for employees
            # who have any mapping to a group matching this LOB
            all_groups = SkillGroup.query.filter_by(is_active=True).all()
            matching_group_ids = [
                g.id for g in all_groups
                if normalize_lob(g.name).lower() == lob_norm
            ]
            if not matching_group_ids:
                return {}
            # Get all employees mapped to these groups
            mappings = SkillMapping.query.filter(
                SkillMapping.skill_group_id.in_(matching_group_ids),
                SkillMapping.is_active == True
            ).all()
        else:
            mappings = query.all()

        result = {}
        for m in mappings:
            emp_id = m.employee_id
            if emp_id not in result:
                result[emp_id] = {}
            sg = m.skill_group
            if sg:
                result[emp_id][sg.name] = m.proficiency
        return result
    except Exception as e:
        log.debug(f"get_skill_proficiency_map: {e}")
        return {}


def proficiency_to_effectiveness(proficiency):
    """
    Convert a 1-5 proficiency rating to an effectiveness multiplier.
    5 = 1.0 (full effectiveness), 1 = 0.4 (minimal effectiveness).
    """
    # Linear scale: 1→0.4, 2→0.55, 3→0.7, 4→0.85, 5→1.0
    return round(0.25 + (proficiency * 0.15), 2)


def score_schedule(days_data, lob=None):
    """
    Score a generated schedule across multiple dimensions.

    Input: list of day dicts from generate_schedule_range output.
    Returns: {
        overall_score: 0-100,
        coverage_score: 0-100,
        cost_score: 0-100,
        balance_score: 0-100,
        details: {...}
    }
    """
    if not days_data:
        return {"overall_score": 0, "coverage_score": 0, "cost_score": 0,
                "balance_score": 0, "details": {}}

    # ── Coverage score ──
    total_intervals = 0
    understaffed_intervals = 0
    overstaffed_intervals = 0
    total_coverage_pct_sum = 0
    worst_gap = 0

    for day in days_data:
        for c in day.get("coverage", []):
            total_intervals += 1
            if c["required"] > 0:
                total_coverage_pct_sum += min(c["coverage_pct"], 100)
                if c["gap"] < 0:
                    understaffed_intervals += 1
                    worst_gap = min(worst_gap, c["gap"])
                elif c["gap"] > 2:  # overstaffed by more than 2
                    overstaffed_intervals += 1

    avg_coverage = (total_coverage_pct_sum / total_intervals
                    if total_intervals > 0 else 0)
    understaffed_pct = (understaffed_intervals / total_intervals * 100
                        if total_intervals > 0 else 0)
    # Coverage score: penalize understaffing heavily
    coverage_score = min(100, max(0, avg_coverage - understaffed_pct * 0.5))

    # ── Cost score (overtime & efficiency) ──
    total_hours = 0
    total_required_hours = 0
    overtime_hours = 0
    employee_hours = defaultdict(float)

    for day in days_data:
        for s in day.get("shifts", []):
            h = s.get("hours", 0)
            total_hours += h
            employee_hours[s.get("employee", "")] += h
        for c in day.get("coverage", []):
            total_required_hours += c.get("required", 0) * (DEFAULT_INTERVAL_MINS / 60)

    # Overtime: employees over 40 hrs across the range
    for emp, hrs in employee_hours.items():
        if hrs > 40:
            overtime_hours += hrs - 40

    # Efficiency: ratio of required to scheduled (closer to 1 is better)
    efficiency = (total_required_hours / total_hours
                  if total_hours > 0 else 0)
    efficiency = min(efficiency, 1.0)  # cap at 1

    overtime_penalty = min(30, overtime_hours * 2)
    cost_score = max(0, min(100, efficiency * 100 - overtime_penalty))

    # ── Balance score (workload distribution) ──
    if employee_hours:
        hrs_values = list(employee_hours.values())
        avg_hrs = sum(hrs_values) / len(hrs_values)
        if avg_hrs > 0:
            variance = sum((h - avg_hrs) ** 2 for h in hrs_values) / len(hrs_values)
            std_dev = variance ** 0.5
            cv = std_dev / avg_hrs  # coefficient of variation
            # Lower CV = more balanced; CV of 0 = perfect, CV > 0.5 = poor
            balance_score = max(0, min(100, 100 - cv * 200))
        else:
            balance_score = 50
    else:
        balance_score = 0

    # ── Overall score ──
    overall_score = round(
        coverage_score * 0.50 +  # coverage is most important
        cost_score * 0.25 +
        balance_score * 0.25, 1
    )

    return {
        "overall_score": round(overall_score, 1),
        "coverage_score": round(coverage_score, 1),
        "cost_score": round(cost_score, 1),
        "balance_score": round(balance_score, 1),
        "details": {
            "avg_coverage_pct": round(avg_coverage, 1),
            "understaffed_intervals": understaffed_intervals,
            "overstaffed_intervals": overstaffed_intervals,
            "total_intervals": total_intervals,
            "worst_gap": round(worst_gap, 1),
            "total_hours": round(total_hours, 1),
            "total_required_hours": round(total_required_hours, 1),
            "overtime_hours": round(overtime_hours, 1),
            "num_employees": len(employee_hours),
            "efficiency_pct": round(efficiency * 100, 1),
        },
    }


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


def _get_wtpm_eligible_shifts(employee_db_id, date_obj):
    """
    Resolve the Work Time Pattern Model chain for an employee on a given date.

    Chain: Employee → EmployeeWorkTimePattern → WorkTimePatternModel
           → (cycle to current week's) WeekTimePattern → eligible DayModels

    The WTPM may contain multiple WeekTimePatterns that cycle. The reference_date
    on the EmployeeWorkTimePattern determines which week in the cycle we're on.

    Returns:
        list of DayModel dicts [{name, start_time, end_time, paid_hours, total_hours,
                                  model_type, color, activities_json, day_model_id}]
        or empty list if no WTPM assigned or no eligible day models for this date.
    """
    try:
        import json as _json
        from app.models import (EmployeeWorkTimePattern, WorkTimePatternModel,
                                WorkTimePatternModelPattern, WeekTimePattern,
                                WeekTimePatternDayModel, DayModel)

        # Find the employee's active WTPM assignment
        assignment = EmployeeWorkTimePattern.query.filter_by(
            employee_id=employee_db_id
        ).first()
        if not assignment:
            return []

        # Check validity period
        if assignment.valid_from and date_obj < assignment.valid_from:
            return []
        if assignment.valid_to and date_obj > assignment.valid_to:
            return []

        wtpm = assignment.work_time_pattern_model
        if not wtpm or not wtpm.is_active:
            return []

        # Get the ordered week time patterns for this WTPM
        pattern_links = WorkTimePatternModelPattern.query.filter_by(
            work_time_pattern_model_id=wtpm.id
        ).order_by(WorkTimePatternModelPattern.position).all()

        if not pattern_links:
            return []

        # Determine which week pattern to use based on cycle position
        ref_date = assignment.reference_date or date_obj
        days_elapsed = (date_obj - ref_date).days
        if days_elapsed < 0:
            days_elapsed = 0
        weeks_elapsed = days_elapsed // 7
        cycle_length = len(pattern_links)
        current_week_idx = weeks_elapsed % cycle_length

        wtp_link = pattern_links[current_week_idx]
        wtp = wtp_link.week_time_pattern
        if not wtp or not wtp.is_active:
            return []

        # Get eligible day models from this week time pattern
        dm_links = WeekTimePatternDayModel.query.filter_by(
            week_time_pattern_id=wtp.id
        ).order_by(WeekTimePatternDayModel.position).all()

        if not dm_links:
            return []

        # Filter day models by day_type matching today
        day_of_week = date_obj.weekday()  # 0=Mon ... 6=Sun
        if day_of_week < 5:
            today_type = "weekday"
        elif day_of_week == 5:
            today_type = "saturday"
        else:
            today_type = "sunday"

        eligible = []
        for link in dm_links:
            dm = link.day_model
            if not dm or not dm.is_active:
                continue
            # day_type filter: "any" matches everything, otherwise must match
            if dm.day_type and dm.day_type != "any" and dm.day_type != today_type:
                continue
            eligible.append({
                "day_model_id": dm.id,
                "name": dm.name,
                "abbreviation": dm.abbreviation or "",
                "start_time": dm.start_time,
                "end_time": dm.end_time,
                "paid_hours": dm.paid_hours or 8.0,
                "total_hours": dm.total_hours or 8.5,
                "model_type": dm.model_type or "Fixed",
                "color": dm.color or "#4472C4",
                "activities_json": dm.activities_json or "[]",
            })

        return eligible

    except Exception as e:
        log.warning(f"WTPM resolution error for employee {employee_db_id}: {e}")
        return []


def _get_employee_quartile(employee_db_id, planning_unit_id=None):
    """
    Get an employee's quartile assignment (1-4).
    Returns the quartile number or None if not assigned.
    """
    try:
        from app.models import EmployeeQuartile
        q = EmployeeQuartile.query.filter_by(employee_id=employee_db_id)
        if planning_unit_id:
            q = q.filter(
                (EmployeeQuartile.planning_unit_id == planning_unit_id) |
                (EmployeeQuartile.planning_unit_id.is_(None))
            )
        row = q.first()
        return row.quartile if row else None
    except Exception:
        return None


def _has_quartile_assignments(planning_unit_id):
    """Check if any employees in this planning unit have quartile assignments."""
    try:
        from app.models import EmployeeQuartile
        count = EmployeeQuartile.query.filter(
            (EmployeeQuartile.planning_unit_id == planning_unit_id) |
            (EmployeeQuartile.planning_unit_id.is_(None))
        ).count()
        return count > 0
    except Exception:
        return False


def _get_availability(employee_name, date_obj, availability_map=None, employee_ext_id=None, _db_id_cache=None):
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
        db_id = (_db_id_cache or {}).get(employee_ext_id) if _db_id_cache else None
        if db_id is None:
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


def generate_shifts(lob, date_obj, shift_length_hrs=None, sheet=None, employee_ids=None,
                    use_proficiency=True):
    """
    Generate shift assignments for a LOB on a given date.

    Algorithm:
    1. Load interval requirements for the date
    2. Load available employees for the LOB
    3. Find the peak requirement to determine how many shifts needed
    4. Assign employees to shifts that best cover the requirement curve
    5. Respect accommodations (restricted hours, half days)
    6. Respect PTO (skip unavailable employees)
    7. Weight effective headcount by skill proficiency (if enabled)

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

    # Load proficiency map for effectiveness weighting
    prof_map = {}
    if use_proficiency:
        prof_map = get_skill_proficiency_map(lob)

    def _emp_effectiveness(emp_dict):
        """Get effectiveness multiplier for an employee based on proficiency."""
        if not prof_map:
            return 1.0
        db_id = _resolve_employee_db_id(emp_dict.get("employee_id"))
        if db_id and db_id in prof_map:
            # Use the max proficiency across matching skill groups
            profs = list(prof_map[db_id].values())
            if profs:
                return proficiency_to_effectiveness(max(profs))
        return 1.0

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
                # Check WTPM eligible shifts
                wtpm_shifts = _get_wtpm_eligible_shifts(db_id, date_obj) if db_id else []
                if wtpm_shifts:
                    # Filter by availability, pick first eligible
                    chosen_dm = None
                    for dm in wtpm_shifts:
                        dm_start_min = _time_to_minutes(dm["start_time"])
                        dm_end_min = _time_to_minutes(dm["end_time"])
                        if avail.get("earliest_start") and dm_start_min < _time_to_minutes(avail["earliest_start"]):
                            continue
                        if avail.get("latest_end") and dm_end_min > _time_to_minutes(avail["latest_end"]):
                            continue
                        chosen_dm = dm
                        break
                    if chosen_dm:
                        start = chosen_dm["start_time"]
                        end = chosen_dm["end_time"]
                        hours = chosen_dm.get("total_hours", shift_length_hrs)
                        stype = "full"
                    else:
                        wtpm_shifts = []  # fall through to standard

                if not wtpm_shifts:
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
            eff = _emp_effectiveness(emp)
            shifts.append({
                "employee": emp["name"],
                "employee_id": emp["employee_id"],
                "start": start,
                "end": end,
                "hours": hours,
                "type": stype,
                "status": "scheduled",
                "team_lead": emp.get("team_lead", ""),
                "effectiveness": eff,
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

    # Track effective headcount per interval (weighted by proficiency)
    scheduled_per_interval = defaultdict(float)

    # Sort employees: those with accommodation restrictions first (they have
    # fewer placement options), then unrestricted employees

    # Pre-load all employee DB IDs in one query to avoid N+1
    _ext_ids = [emp.get("employee_id") for emp in employees if emp.get("employee_id")]
    _db_id_cache = {}
    if _ext_ids:
        try:
            from app.models import Employee as _Emp
            _rows = _Emp.query.filter(_Emp.employee_id.in_(_ext_ids)).all()
            _db_id_cache = {r.employee_id: r.id for r in _rows}
        except Exception:
            pass

    emp_avails = []
    unassigned = []
    log.info(f"Scheduling {len(employees)} employees for {lob} on {date_obj}")
    skip_reasons = []
    for emp in employees:
        avail = _get_availability(emp["name"], date_obj, avail_map,
                                  employee_ext_id=emp.get("employee_id"),
                                  _db_id_cache=_db_id_cache)
        if not avail["available"]:
            reason = _explain_unavailability(emp["name"], date_obj, avail_map,
                                             emp.get("employee_id"))
            log.info(f"  SKIP {emp['name']} ({reason})")
            skip_reasons.append(f"{emp['name']}: {reason}")
            unassigned.append(emp["name"])
            continue
        # Resolve DB id from cache (or fallback to single query)
        ext_id = emp.get("employee_id")
        db_id = _db_id_cache.get(ext_id) if ext_id else None
        if db_id is None and ext_id:
            db_id = _resolve_employee_db_id(ext_id)
        rot_shift = _get_rotation_shift(db_id, date_obj) if db_id else None
        if rot_shift and rot_shift.get("off"):
            log.info(f"  SKIP {emp['name']} (rotation day off)")
            skip_reasons.append(f"{emp['name']}: rotation '{rot_shift.get('name')}' day off")
            unassigned.append(emp["name"])
            continue
        emp_avails.append((emp, avail, rot_shift))

    # ── Resolve WTPM eligible shifts and quartile for each employee ──
    # Enrich emp_avails with WTPM day models and quartile
    pu_id = None
    try:
        from app.models import PlanningUnit, db as _db
        from app.data_source import normalize_lob
        _lob_norm = normalize_lob(lob.strip()).lower()
        _pu = PlanningUnit.query.filter(
            _db.func.lower(PlanningUnit.name) == _lob_norm
        ).first()
        pu_id = _pu.id if _pu else None
    except Exception:
        pass

    use_quartiles = _has_quartile_assignments(pu_id) if pu_id else False

    enriched = []  # (emp, avail, rot_shift, wtpm_shifts, quartile)
    for emp, avail, rot_shift in emp_avails:
        ext_id = emp.get("employee_id")
        db_id = _db_id_cache.get(ext_id) if ext_id else None
        if db_id is None and ext_id:
            db_id = _resolve_employee_db_id(ext_id)

        # WTPM eligible day models (only used if no rotation/shift sequence)
        wtpm_shifts = []
        if not rot_shift and db_id:
            wtpm_shifts = _get_wtpm_eligible_shifts(db_id, date_obj)

        quartile = None
        if use_quartiles and db_id:
            quartile = _get_employee_quartile(db_id, pu_id)

        enriched.append((emp, avail, rot_shift, wtpm_shifts, quartile))

    # Sort employees for optimal placement:
    # 1. Rotation/shift sequence employees first (static, fewest options)
    # 2. WTPM-assigned employees next (constrained to eligible day models)
    # 3. Unrestricted employees last
    # Within each group: if quartiles enabled, Q1 first (lower = better)
    # Then by highest proficiency
    enriched.sort(key=lambda x: (
        0 if x[2] else (1 if x[3] else 2),              # rot > wtpm > unrestricted
        0 if x[1].get("shift_start") or x[1]["day_type"] == "half" else 1,
        (x[4] or 5) if use_quartiles else 0,             # Q1=1 first, unassigned=5 last
        -_emp_effectiveness(x[0]),                        # highest effectiveness first
    ))

    shifts = []
    for emp, avail, rot_shift, wtpm_shifts, quartile in enriched:
        # ── Priority 1: Rotation/shift sequence — static override ──
        if rot_shift:
            rs = rot_shift["start"]
            re = rot_shift["end"]
            start_min = _time_to_minutes(rs.strftime("%H:%M") if hasattr(rs, "strftime") else str(rs)[:5])
            end_min = _time_to_minutes(re.strftime("%H:%M") if hasattr(re, "strftime") else str(re)[:5])
            length = end_min - start_min
            if length <= 0:
                length = shift_length_mins
                end_min = start_min + length

        # ── Priority 2: WTPM day models — pick best-fitting eligible shift ──
        elif wtpm_shifts:
            # Filter eligible day models by availability constraints
            candidates = []
            for dm in wtpm_shifts:
                dm_start = _time_to_minutes(dm["start_time"])
                dm_end = _time_to_minutes(dm["end_time"])
                dm_length = dm_end - dm_start
                if dm_length <= 0:
                    continue

                # Apply availability constraints as filters
                if avail.get("earliest_start"):
                    es_min = _time_to_minutes(avail["earliest_start"])
                    if dm_start < es_min:
                        continue  # shift starts too early for this employee
                if avail.get("latest_start"):
                    ls_min = _time_to_minutes(avail["latest_start"])
                    if dm_start > ls_min:
                        continue  # shift starts too late
                if avail.get("latest_end"):
                    le_min = _time_to_minutes(avail["latest_end"])
                    if dm_end > le_min:
                        continue  # shift ends too late

                # Score by how much unmet demand this day model covers
                score = 0
                for m in range(dm_start, dm_end, DEFAULT_INTERVAL_MINS):
                    req = req_by_minute.get(m, 0)
                    already = scheduled_per_interval.get(m, 0)
                    gap = req - already
                    if gap > 0:
                        score += gap
                candidates.append((dm, dm_start, dm_end, dm_length, score))

            if candidates:
                # Pick the day model that covers the most unmet demand
                candidates.sort(key=lambda c: -c[4])
                best_dm, start_min, end_min, length, _ = candidates[0]
                log.info(f"  WTPM: {emp['name']} → {best_dm['name']} "
                         f"({best_dm['start_time']}-{best_dm['end_time']})")
            else:
                # No WTPM day model fits availability — fall back to standard logic
                log.info(f"  WTPM: {emp['name']} — no eligible day model fits availability, "
                         "falling back to standard placement")
                wtpm_shifts = []  # clear so we fall into standard logic below

        # ── Priority 3: Standard placement (no rotation, no WTPM) ──
        if not rot_shift and not wtpm_shifts:
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

        # Record the shift — track effective headcount per interval
        eff = _emp_effectiveness(emp)
        for m in range(start_min, end_min, DEFAULT_INTERVAL_MINS):
            scheduled_per_interval[m] += eff

        stype = "half" if avail["day_type"] == "half" else "full"
        s_time = _minutes_to_time(start_min)
        e_time = _minutes_to_time(end_min)
        shift_dict = {
            "employee": emp["name"],
            "employee_id": emp["employee_id"],
            "start": s_time,
            "end": e_time,
            "hours": round(length / 60, 1),
            "type": stype,
            "status": "scheduled",
            "team_lead": emp.get("team_lead", ""),
            "effectiveness": eff,
            "segments": [],  # filled below with stagger
        }
        if quartile:
            shift_dict["quartile"] = quartile
        if wtpm_shifts and not rot_shift:
            # Record which day model was selected
            best_dm_name = candidates[0][0]["name"] if candidates else None
            if best_dm_name:
                shift_dict["day_model"] = best_dm_name
        shifts.append(shift_dict)

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

    # Build scheduled count per interval (with optional proficiency weighting)
    scheduled_map = defaultdict(float)
    for shift in shifts:
        s_min = _time_to_minutes(shift["start"])
        e_min = _time_to_minutes(shift["end"])
        weight = shift.get("effectiveness", 1.0)
        for m in range(s_min, e_min, DEFAULT_INTERVAL_MINS):
            t = _minutes_to_time(m)
            scheduled_map[t] += weight

    coverage = []
    if requirements:
        # Compare scheduled vs required
        for r in requirements:
            t_raw = r["time"]
            # Normalize to HH:MM — requirements may return "YYYY-MM-DD HH:MM"
            t = t_raw[11:16] if len(t_raw) > 5 else t_raw
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

    result = {
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

    # Add schedule scoring
    result["score"] = score_schedule(days, lob)

    # Add multi-skill demand breakdown if applicable
    try:
        skill_reqs = get_multi_skill_requirements(lob, start_date, sheet)
        if len(skill_reqs) > 1:  # Only include if there's an actual multi-skill split
            result["skill_demand"] = {
                sg: [{"time": r["time"], "required": r["agents_required"]} for r in intervals]
                for sg, intervals in skill_reqs.items()
            }
    except Exception as e:
        log.warning(f"Multi-skill demand summary error: {e}")

    return result


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
