"""
realtime/engine.py — Real-time monitoring & adherence
=====================================================
Provides:
  - Current-interval queue snapshot (required vs scheduled vs available)
  - Intraday staffing tracker with gap/surplus per interval
  - Agent adherence (scheduled state vs actual — uses People + Scheduling data)
  - Staffing alerts (understaffed, overstaffed, SL-at-risk)
"""

import logging
import datetime
import math
from collections import defaultdict

log = logging.getLogger("serevo.realtime")

DEFAULT_INTERVAL_MINS = 30
ALERT_CRITICAL_PCT = 0.70   # coverage below 70% → critical
ALERT_WARNING_PCT  = 0.90   # coverage below 90% → warning


# ═══════════════════════════════════════════════════════════════
# DATA HELPERS
# ═══════════════════════════════════════════════════════════════

def _get_sheet():
    try:
        from app.data_source import _open_capacity_sheet
        sheet, err = _open_capacity_sheet()
        return None if err else sheet
    except Exception:
        return None


def get_available_lobs(sheet=None):
    """Return sorted list of distinct LOB names."""
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


def _time_to_minutes(time_str):
    try:
        parts = time_str.split(":")
        return int(parts[0]) * 60 + int(parts[1])
    except Exception:
        return 0


def _minutes_to_time(mins):
    h = mins // 60
    m = mins % 60
    return f"{h:02d}:{m:02d}"


def _current_interval():
    """Return the start of the current 30-minute interval as HH:MM."""
    now = datetime.datetime.now()
    mins = (now.hour * 60 + now.minute)
    interval_start = (mins // DEFAULT_INTERVAL_MINS) * DEFAULT_INTERVAL_MINS
    return _minutes_to_time(interval_start)


# ═══════════════════════════════════════════════════════════════
# INTRADAY SNAPSHOT
# ═══════════════════════════════════════════════════════════════

def get_intraday_snapshot(lob, date_obj=None, sheet=None):
    """
    Build intraday staffing data for a LOB on a given date (default today).
    Combines:
      - Requirements from REQUIREMENTS RAW (what's needed)
      - Scheduled shifts from the scheduling engine (who's assigned)
      - Forecast from FORECAST RAW (predicted volume)

    Returns {
        lob, date, current_interval, current_time,
        intervals: [{time, required, scheduled, gap, coverage_pct,
                     forecast_offered, forecast_aht, status}],
        current: {time, required, scheduled, gap, coverage_pct, status},
        summary: {total_intervals, understaffed_count, overstaffed_count,
                  avg_coverage_pct, peak_required, peak_gap},
        alerts: [{severity, message, time, details}],
    }
    """
    if date_obj is None:
        date_obj = datetime.date.today()

    # Load requirements
    try:
        from app.data_source import SheetSource
        src = SheetSource()
        req_intervals, _ = src.get_requirements(lob, date_obj)
    except Exception:
        req_intervals = []

    # Load forecast
    try:
        from app.data_source import SheetSource
        src = SheetSource()
        fc_intervals, _ = src.get_forecast(lob, date_obj)
    except Exception:
        fc_intervals = []

    # Load scheduled shifts
    try:
        from app.scheduling.engine import generate_shifts
        shifts, unassigned, warnings = generate_shifts(lob, date_obj, sheet=sheet)
    except Exception:
        shifts = []
        unassigned = []
        warnings = []

    # Build requirement map {HH:MM -> agents_required}
    req_map = {}
    for r in (req_intervals or []):
        ts = str(r.get("time", ""))
        time_str = ts[11:16] if len(ts) > 10 else ts
        req_map[time_str] = float(r.get("agents_required", 0) or 0)

    # Build forecast map {HH:MM -> {offered, aht}}
    fc_map = {}
    for f in (fc_intervals or []):
        ts = str(f.get("time", ""))
        time_str = ts[11:16] if len(ts) > 10 else ts
        fc_map[time_str] = {
            "offered": float(f.get("offered", 0) or 0),
            "aht": float(f.get("aht", 0) or 0),
        }

    # Build scheduled count per interval from shifts
    sched_map = defaultdict(int)
    for s in shifts:
        s_min = _time_to_minutes(s["start"])
        e_min = _time_to_minutes(s["end"])
        for m in range(s_min, e_min, DEFAULT_INTERVAL_MINS):
            sched_map[_minutes_to_time(m)] += 1

    # Merge into interval list
    all_times = sorted(set(list(req_map.keys()) + list(sched_map.keys()) + list(fc_map.keys())))

    intervals = []
    for t in all_times:
        req = req_map.get(t, 0)
        sched = sched_map.get(t, 0)
        gap = sched - req
        pct = round((sched / req) * 100, 1) if req > 0 else (100.0 if sched > 0 else 0)
        fc = fc_map.get(t, {})

        if req > 0 and pct < ALERT_CRITICAL_PCT * 100:
            status = "critical"
        elif req > 0 and pct < ALERT_WARNING_PCT * 100:
            status = "warning"
        elif sched > req and req > 0:
            status = "over"
        else:
            status = "ok"

        intervals.append({
            "time": t,
            "required": round(req, 1),
            "scheduled": sched,
            "gap": round(gap, 1),
            "coverage_pct": pct,
            "forecast_offered": round(fc.get("offered", 0), 1),
            "forecast_aht": round(fc.get("aht", 0), 1),
            "status": status,
        })

    # Current interval
    cur_time = _current_interval()
    current = None
    for iv in intervals:
        if iv["time"] == cur_time:
            current = iv
            break
    if not current and intervals:
        # Find closest past interval
        for iv in reversed(intervals):
            if iv["time"] <= cur_time:
                current = iv
                break
    if not current:
        current = {
            "time": cur_time, "required": 0, "scheduled": 0,
            "gap": 0, "coverage_pct": 0, "status": "ok",
            "forecast_offered": 0, "forecast_aht": 0,
        }

    # Summary
    understaffed = sum(1 for iv in intervals if iv["gap"] < 0)
    overstaffed = sum(1 for iv in intervals if iv["gap"] > 0 and iv["required"] > 0)
    avg_cov = (sum(iv["coverage_pct"] for iv in intervals) / len(intervals)
               if intervals else 0)
    peak_req = max((iv["required"] for iv in intervals), default=0)
    peak_gap = min((iv["gap"] for iv in intervals), default=0)

    # Alerts
    alerts = _generate_alerts(intervals, cur_time, shifts, unassigned)

    return {
        "lob": lob,
        "date": date_obj.strftime("%Y-%m-%d"),
        "current_interval": cur_time,
        "current_time": datetime.datetime.now().strftime("%H:%M:%S"),
        "intervals": intervals,
        "current": current,
        "shifts": shifts,
        "unassigned": unassigned,
        "summary": {
            "total_intervals": len(intervals),
            "understaffed_count": understaffed,
            "overstaffed_count": overstaffed,
            "avg_coverage_pct": round(avg_cov, 1),
            "peak_required": round(peak_req, 1),
            "peak_gap": round(peak_gap, 1),
            "total_scheduled": len(shifts),
            "total_unassigned": len(unassigned),
        },
        "alerts": alerts,
    }


# ═══════════════════════════════════════════════════════════════
# ADHERENCE
# ═══════════════════════════════════════════════════════════════

def get_adherence_snapshot(lob, date_obj=None, sheet=None):
    """
    Build adherence data: for each scheduled agent, show their
    scheduled shift vs expected state at the current time.

    Returns list of {
        employee, employee_id, shift_start, shift_end, shift_type,
        expected_state, current_interval, is_on_shift
    }
    """
    if date_obj is None:
        date_obj = datetime.date.today()

    try:
        from app.scheduling.engine import generate_shifts
        shifts, unassigned, _ = generate_shifts(lob, date_obj, sheet=sheet)
    except Exception:
        shifts = []
        unassigned = []

    cur_time = _current_interval()
    cur_mins = _time_to_minutes(cur_time)

    adherence = []
    for s in shifts:
        s_min = _time_to_minutes(s["start"])
        e_min = _time_to_minutes(s["end"])
        is_on = s_min <= cur_mins < e_min

        if is_on:
            expected = "On Queue"
        elif cur_mins < s_min:
            expected = "Not Started"
        else:
            expected = "Shift Ended"

        adherence.append({
            "employee": s["employee"],
            "employee_id": s.get("employee_id", ""),
            "shift_start": s["start"],
            "shift_end": s["end"],
            "shift_type": s.get("type", "full"),
            "hours": s.get("hours", 0),
            "expected_state": expected,
            "current_interval": cur_time,
            "is_on_shift": is_on,
        })

    # Sort: on-shift first, then by name
    adherence.sort(key=lambda a: (0 if a["is_on_shift"] else 1, a["employee"]))

    return {
        "adherence": adherence,
        "unassigned": unassigned,
        "current_interval": cur_time,
        "on_shift_count": sum(1 for a in adherence if a["is_on_shift"]),
        "total_scheduled": len(adherence),
    }


# ═══════════════════════════════════════════════════════════════
# ALERTS
# ═══════════════════════════════════════════════════════════════

def _generate_alerts(intervals, cur_time, shifts, unassigned):
    """Generate staffing alerts based on current and upcoming intervals."""
    alerts = []
    cur_mins = _time_to_minutes(cur_time)

    # Look at current + next 2 hours of intervals
    window_end = cur_mins + 120

    for iv in intervals:
        iv_mins = _time_to_minutes(iv["time"])
        if iv_mins < cur_mins or iv_mins > window_end:
            continue

        if iv["status"] == "critical":
            when = "NOW" if iv["time"] == cur_time else f"at {iv['time']}"
            alerts.append({
                "severity": "critical",
                "message": f"Critical understaffing {when}",
                "time": iv["time"],
                "details": f"Need {iv['required']} agents, only {iv['scheduled']} scheduled "
                           f"({iv['coverage_pct']}% coverage)",
            })
        elif iv["status"] == "warning":
            when = "NOW" if iv["time"] == cur_time else f"at {iv['time']}"
            alerts.append({
                "severity": "warning",
                "message": f"Understaffed {when}",
                "time": iv["time"],
                "details": f"Need {iv['required']} agents, {iv['scheduled']} scheduled "
                           f"({iv['coverage_pct']}% coverage)",
            })

    # Alert for unassigned employees
    if unassigned:
        alerts.append({
            "severity": "info",
            "message": f"{len(unassigned)} employee(s) unavailable today",
            "time": "",
            "details": ", ".join(unassigned[:5]) + ("…" if len(unassigned) > 5 else ""),
        })

    # Sort: critical first, then warning, then info
    severity_order = {"critical": 0, "warning": 1, "info": 2}
    alerts.sort(key=lambda a: (severity_order.get(a["severity"], 9), a["time"]))

    return alerts


# ═══════════════════════════════════════════════════════════════
# SERVICE LEVEL TRACKER
# ═══════════════════════════════════════════════════════════════

def get_service_level_intraday(lob, date_obj=None, sheet=None):
    """
    Estimate intraday service level per interval using Erlang C
    based on forecast volume/AHT and scheduled agents.

    Returns list of {time, estimated_sl, agents, traffic_intensity, offered, aht}
    """
    if date_obj is None:
        date_obj = datetime.date.today()

    snapshot = get_intraday_snapshot(lob, date_obj, sheet)
    intervals = snapshot.get("intervals", [])

    from app.forecasting.engine import _erlang_c, _service_level

    results = []
    for iv in intervals:
        offered = iv.get("forecast_offered", 0)
        aht = iv.get("forecast_aht", 0)
        agents = iv.get("scheduled", 0)

        if offered > 0 and aht > 0 and agents > 0:
            traffic = (offered * aht) / 1800  # 30-min interval
            if agents > traffic:
                sl = _service_level(agents, traffic, 30, aht)
            else:
                sl = 0.0
        else:
            traffic = 0
            sl = 1.0 if agents > 0 else 0.0

        results.append({
            "time": iv["time"],
            "estimated_sl": round(sl, 4),
            "agents": agents,
            "traffic_intensity": round(traffic, 2),
            "offered": offered,
            "aht": aht,
        })

    return results
