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
DEFAULT_SHIFT_LENGTH_HRS = 8.0
DEFAULT_INTERVAL_MINS = 30
DAYS_OF_WEEK = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


# ═════════════════════════════════════════════════════════════
# DATA LOADING
# ═════════════════════════════════════════════════════════════

def _get_requirements_for_date(lob, date_obj, sheet=None):
    """
    Pull interval-level requirements for a LOB on a specific date.
    Returns list of {time: "HH:MM", agents_required: float}.
    Reads from the REQUIREMENTS RAW tab via data_source.
    """
    try:
        from app.data_source import SheetSource
        src = SheetSource()
        intervals, err = src.get_requirements(lob, date_obj)
        if err:
            log.warning(f"Requirements fetch error for {lob} {date_obj}: {err}")
            return []
        if not intervals:
            return []
        # Normalize to list of dicts
        results = []
        for row in intervals:
            ts = row.get("time", "")
            agents = float(row.get("agents_required", 0) or 0)
            # Extract HH:MM from timestamp
            if len(ts) > 10:
                time_str = ts[11:16]
            else:
                time_str = ts
            results.append({
                "time": time_str,
                "agents_required": agents,
            })
        return results
    except Exception as e:
        log.warning(f"Requirements load error: {e}")
        return []


def _get_employees_for_lob(lob, sheet=None):
    """
    Return active employees assigned to a given LOB/skill.
    Each employee dict has: name, employee_id, lob.
    """
    try:
        from app.people.manager import get_employees
        employees, err = get_employees(sheet)
        if err:
            return []
        results = []
        for emp in employees:
            status = str(emp.get("Status", "")).strip().lower()
            if status in ("inactive", "terminated", "deleted"):
                continue
            skill = (emp.get("Latest Skill Name") or "").strip()
            if skill.lower() != lob.strip().lower():
                continue
            first = str(emp.get("First Name", "")).strip()
            last = str(emp.get("Last Name", "")).strip()
            name = f"{first} {last}".strip()
            results.append({
                "name": name,
                "employee_id": emp.get("Employee ID", ""),
                "lob": skill,
            })
        return results
    except Exception as e:
        log.warning(f"Employee load error: {e}")
        return []


def _get_availability(employee_name, date_obj, availability_map=None):
    """
    Check if an employee is available on a given date and what
    restrictions they have. Returns dict:
      {available: bool, day_type: "fill"|"off"|"half",
       shift_start: str|None, shift_end: str|None}
    """
    if availability_map is None:
        try:
            from app.people.manager import get_availability_map
            availability_map = get_availability_map()
        except Exception:
            return {"available": True, "day_type": "fill",
                    "shift_start": None, "shift_end": None}

    info = availability_map.get(employee_name, {})

    # Check PTO first
    pto_dates = info.get("pto_dates", set())
    if date_obj in pto_dates:
        return {"available": False, "day_type": "off",
                "shift_start": None, "shift_end": None}

    # Check accommodations
    accom = info.get("accommodations", {})
    if accom:
        day_name = DAYS_OF_WEEK[date_obj.weekday()]
        day_val = str(accom.get(day_name, "fill")).strip().lower()
        if day_val == "off":
            return {"available": False, "day_type": "off",
                    "shift_start": None, "shift_end": None}
        return {
            "available": True,
            "day_type": day_val if day_val in ("fill", "half") else "fill",
            "shift_start": accom.get("Shift Start") or None,
            "shift_end": accom.get("Shift End") or None,
        }

    return {"available": True, "day_type": "fill",
            "shift_start": None, "shift_end": None}


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


def generate_shifts(lob, date_obj, shift_length_hrs=None, sheet=None):
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
    employees = _get_employees_for_lob(lob, sheet)
    warnings = []

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
        for emp in employees:
            avail = _get_availability(emp["name"], date_obj, avail_map)
            if not avail["available"]:
                unassigned.append(emp["name"])
                continue
            start = avail.get("shift_start") or "08:00"
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
            shifts.append({
                "employee": emp["name"],
                "employee_id": emp["employee_id"],
                "start": start,
                "end": end,
                "hours": hours,
                "type": stype,
            })
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
    for emp in employees:
        avail = _get_availability(emp["name"], date_obj, avail_map)
        if not avail["available"]:
            unassigned.append(emp["name"])
            continue
        emp_avails.append((emp, avail))

    # Restricted employees first (have shift_start or half day)
    emp_avails.sort(key=lambda x: (
        0 if x[1].get("shift_start") or x[1]["day_type"] == "half" else 1
    ))

    shifts = []
    for emp, avail in emp_avails:
        if avail["day_type"] == "half":
            length = shift_length_mins // 2
        else:
            length = shift_length_mins

        # If employee has a fixed shift start, use it
        if avail.get("shift_start"):
            start_min = _time_to_minutes(avail["shift_start"])
            end_min = start_min + length
            if avail.get("shift_end"):
                end_min = min(end_min, _time_to_minutes(avail["shift_end"]))
                length = end_min - start_min
        else:
            # Find the best start time: maximize coverage of unmet demand
            best_start = possible_starts[0] if possible_starts else ops_start_mins
            best_score = -1
            for s in possible_starts:
                # Score = sum of (requirement - already_scheduled) where positive
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

        # Record the shift
        for m in range(start_min, end_min, DEFAULT_INTERVAL_MINS):
            scheduled_per_interval[m] += 1

        shifts.append({
            "employee": emp["name"],
            "employee_id": emp["employee_id"],
            "start": _minutes_to_time(start_min),
            "end": _minutes_to_time(end_min),
            "hours": round(length / 60, 1),
            "type": "half" if avail["day_type"] == "half" else "full",
        })

    # Sort shifts by start time, then employee name
    shifts.sort(key=lambda s: (s["start"], s["employee"]))

    return shifts, unassigned, warnings


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
    if not requirements:
        return []

    # Build scheduled count per interval
    scheduled_map = defaultdict(int)
    for shift in shifts:
        s_min = _time_to_minutes(shift["start"])
        e_min = _time_to_minutes(shift["end"])
        for m in range(s_min, e_min, DEFAULT_INTERVAL_MINS):
            t = _minutes_to_time(m)
            scheduled_map[t] += 1

    coverage = []
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

def generate_schedule_range(lob, start_date, end_date, shift_length_hrs=None, sheet=None):
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

    while d <= end_date:
        shifts, unassigned, warnings = generate_shifts(
            lob, d, shift_length_hrs, sheet)
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
    }


def get_available_lobs(sheet=None):
    """Return sorted list of distinct LOB names from the employee roster."""
    try:
        from app.people.manager import get_employees
        employees, err = get_employees(sheet)
        if err:
            return []
        lobs = set()
        for emp in employees:
            status = str(emp.get("Status", "")).strip().lower()
            if status in ("inactive", "terminated", "deleted"):
                continue
            lob = (emp.get("Latest Skill Name") or "").strip()
            if lob:
                lobs.add(lob)
        return sorted(lobs)
    except Exception:
        return []
