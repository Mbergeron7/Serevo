"""
demo_data.py — Realistic fake data for demo mode
==================================================
When DEMO_MODE=true and no Google Sheet is connected, this module
provides a complete set of employees, requirements, forecast, and
scheduling data for a fictional generic contact center.

LOBs: Sales Support, Tech Help Desk, Billing
~20 employees across those LOBs with realistic shifts.
"""

import datetime
import math
import random

# ── LOBs ──────────────────────────────────────────────────────
DEMO_LOBS = ["Sales Support", "Tech Help Desk", "Billing"]

DEMO_WORKLOADS = {
    "Sales Support":   "demo-sales-support",
    "Tech Help Desk":  "demo-tech-help-desk",
    "Billing":         "demo-billing",
}

# ── Employees ─────────────────────────────────────────────────
DEMO_EMPLOYEES = [
    # Sales Support (7 agents)
    {"Status": "Active", "First Name": "Alex",    "Last Name": "Morgan",    "Employee ID": "E1001", "Latest Skill Name": "Sales Support",   "Latest Skill Start": "2025-01-15", "Latest Skill End": "", "All Skills": "Sales Support", "End Date": ""},
    {"Status": "Active", "First Name": "Jordan",  "Last Name": "Rivera",    "Employee ID": "E1002", "Latest Skill Name": "Sales Support",   "Latest Skill Start": "2025-03-01", "Latest Skill End": "", "All Skills": "Sales Support", "End Date": ""},
    {"Status": "Active", "First Name": "Casey",   "Last Name": "Chen",      "Employee ID": "E1003", "Latest Skill Name": "Sales Support",   "Latest Skill Start": "2025-02-10", "Latest Skill End": "", "All Skills": "Sales Support", "End Date": ""},
    {"Status": "Active", "First Name": "Taylor",  "Last Name": "Brooks",    "Employee ID": "E1004", "Latest Skill Name": "Sales Support",   "Latest Skill Start": "2024-11-01", "Latest Skill End": "", "All Skills": "Sales Support", "End Date": ""},
    {"Status": "Active", "First Name": "Sam",     "Last Name": "Patel",     "Employee ID": "E1005", "Latest Skill Name": "Sales Support",   "Latest Skill Start": "2025-06-15", "Latest Skill End": "", "All Skills": "Sales Support", "End Date": ""},
    {"Status": "Active", "First Name": "Riley",   "Last Name": "Kim",       "Employee ID": "E1006", "Latest Skill Name": "Sales Support",   "Latest Skill Start": "2025-04-01", "Latest Skill End": "", "All Skills": "Sales Support", "End Date": ""},
    {"Status": "Active", "First Name": "Morgan",  "Last Name": "Lee",       "Employee ID": "E1007", "Latest Skill Name": "Sales Support",   "Latest Skill Start": "2025-05-20", "Latest Skill End": "", "All Skills": "Sales Support", "End Date": ""},
    # Tech Help Desk (7 agents)
    {"Status": "Active", "First Name": "Jamie",   "Last Name": "Torres",    "Employee ID": "E2001", "Latest Skill Name": "Tech Help Desk",  "Latest Skill Start": "2025-01-10", "Latest Skill End": "", "All Skills": "Tech Help Desk", "End Date": ""},
    {"Status": "Active", "First Name": "Avery",   "Last Name": "Nguyen",    "Employee ID": "E2002", "Latest Skill Name": "Tech Help Desk",  "Latest Skill Start": "2025-02-15", "Latest Skill End": "", "All Skills": "Tech Help Desk", "End Date": ""},
    {"Status": "Active", "First Name": "Drew",    "Last Name": "Campbell",  "Employee ID": "E2003", "Latest Skill Name": "Tech Help Desk",  "Latest Skill Start": "2024-09-01", "Latest Skill End": "", "All Skills": "Tech Help Desk", "End Date": ""},
    {"Status": "Active", "First Name": "Quinn",   "Last Name": "Dubois",    "Employee ID": "E2004", "Latest Skill Name": "Tech Help Desk",  "Latest Skill Start": "2025-03-20", "Latest Skill End": "", "All Skills": "Tech Help Desk", "End Date": ""},
    {"Status": "Active", "First Name": "Reese",   "Last Name": "Martin",    "Employee ID": "E2005", "Latest Skill Name": "Tech Help Desk",  "Latest Skill Start": "2025-07-01", "Latest Skill End": "", "All Skills": "Tech Help Desk", "End Date": ""},
    {"Status": "Active", "First Name": "Dakota",  "Last Name": "Singh",     "Employee ID": "E2006", "Latest Skill Name": "Tech Help Desk",  "Latest Skill Start": "2025-04-10", "Latest Skill End": "", "All Skills": "Tech Help Desk", "End Date": ""},
    {"Status": "Active", "First Name": "Skyler",  "Last Name": "O'Brien",   "Employee ID": "E2007", "Latest Skill Name": "Tech Help Desk",  "Latest Skill Start": "2025-06-01", "Latest Skill End": "", "All Skills": "Tech Help Desk", "End Date": ""},
    # Billing (6 agents)
    {"Status": "Active", "First Name": "Harper",  "Last Name": "Wilson",    "Employee ID": "E3001", "Latest Skill Name": "Billing",         "Latest Skill Start": "2025-01-05", "Latest Skill End": "", "All Skills": "Billing", "End Date": ""},
    {"Status": "Active", "First Name": "Rowan",   "Last Name": "Garcia",    "Employee ID": "E3002", "Latest Skill Name": "Billing",         "Latest Skill Start": "2025-02-20", "Latest Skill End": "", "All Skills": "Billing", "End Date": ""},
    {"Status": "Active", "First Name": "Emery",   "Last Name": "Davis",     "Employee ID": "E3003", "Latest Skill Name": "Billing",         "Latest Skill Start": "2024-12-01", "Latest Skill End": "", "All Skills": "Billing", "End Date": ""},
    {"Status": "Active", "First Name": "Finley",  "Last Name": "Johnson",   "Employee ID": "E3004", "Latest Skill Name": "Billing",         "Latest Skill Start": "2025-05-01", "Latest Skill End": "", "All Skills": "Billing", "End Date": ""},
    {"Status": "Active", "First Name": "Blair",   "Last Name": "Thompson",  "Employee ID": "E3005", "Latest Skill Name": "Billing",         "Latest Skill Start": "2025-03-15", "Latest Skill End": "", "All Skills": "Billing", "End Date": ""},
    {"Status": "Active", "First Name": "Sage",    "Last Name": "Anderson",  "Employee ID": "E3006", "Latest Skill Name": "Billing",         "Latest Skill Start": "2025-08-01", "Latest Skill End": "", "All Skills": "Billing", "End Date": ""},
]

# ── Accommodations (a few sample entries) ─────────────────────
DEMO_ACCOMMODATIONS = [
    {"Employee": "Casey Chen",   "Group": "Sales", "LOB": "Sales Support",  "Mon": "fill", "Tue": "fill", "Wed": "off", "Thu": "fill", "Fri": "fill", "Sat": "off", "Sun": "off", "Shift Start": "09:00", "Shift End": "17:00", "Notes": "Reduced schedule — Wednesdays off", "Updated": "2026-08-15 10:00"},
    {"Employee": "Drew Campbell","Group": "Tech",  "LOB": "Tech Help Desk", "Mon": "fill", "Tue": "fill", "Wed": "fill", "Thu": "fill", "Fri": "fill", "Sat": "off", "Sun": "off", "Shift Start": "10:00", "Shift End": "18:00", "Notes": "Late start accommodation",          "Updated": "2026-07-20 14:30"},
]

# ── PTO (a few sample entries relative to today) ──────────────
def _demo_pto():
    today = datetime.date.today()
    return [
        {"Employee": "Sam Patel",     "Group": "Sales",   "LOB": "Sales Support",  "Start Date": (today + datetime.timedelta(days=2)).strftime("%Y-%m-%d"), "End Date": (today + datetime.timedelta(days=4)).strftime("%Y-%m-%d"), "Type": "full", "Note": "Vacation", "Updated": "2026-09-01 09:00"},
        {"Employee": "Reese Martin",  "Group": "Tech",    "LOB": "Tech Help Desk", "Start Date": today.strftime("%Y-%m-%d"), "End Date": today.strftime("%Y-%m-%d"), "Type": "full", "Note": "Personal day", "Updated": "2026-09-20 08:00"},
        {"Employee": "Sage Anderson", "Group": "Billing", "LOB": "Billing",        "Start Date": (today - datetime.timedelta(days=1)).strftime("%Y-%m-%d"), "End Date": (today + datetime.timedelta(days=1)).strftime("%Y-%m-%d"), "Type": "full", "Note": "Medical",  "Updated": "2026-09-18 11:00"},
    ]


# ── Interval-level data generators ───────────────────────────

# Volume profiles per LOB (relative multiplier per half-hour from 08:00–22:00)
_VOLUME_PROFILES = {
    "Sales Support": [
        0.3, 0.5, 0.7, 0.9, 1.0, 1.0, 0.95, 0.85, 0.9, 1.0,
        1.0, 0.95, 0.8, 0.7, 0.6, 0.5, 0.4, 0.35, 0.3, 0.25,
        0.2, 0.15, 0.1, 0.1, 0.05, 0.05, 0.02, 0.01,
    ],
    "Tech Help Desk": [
        0.2, 0.4, 0.6, 0.8, 0.95, 1.0, 1.0, 0.9, 0.85, 0.95,
        1.0, 0.9, 0.85, 0.8, 0.7, 0.6, 0.5, 0.45, 0.4, 0.35,
        0.3, 0.25, 0.2, 0.15, 0.1, 0.08, 0.05, 0.02,
    ],
    "Billing": [
        0.25, 0.45, 0.65, 0.85, 0.95, 1.0, 0.9, 0.85, 0.8, 0.9,
        0.95, 0.85, 0.75, 0.65, 0.55, 0.45, 0.35, 0.3, 0.25, 0.2,
        0.15, 0.1, 0.08, 0.05, 0.03, 0.02, 0.01, 0.01,
    ],
}

# Peak offered calls per interval and average AHT by LOB
_LOB_PARAMS = {
    "Sales Support":  {"peak_offered": 45, "aht": 360},
    "Tech Help Desk": {"peak_offered": 35, "aht": 480},
    "Billing":        {"peak_offered": 30, "aht": 300},
}


def _generate_intervals(lob, date_obj):
    """Generate 28 half-hour intervals (08:00–22:00) of forecast + requirements."""
    profile = _VOLUME_PROFILES.get(lob, _VOLUME_PROFILES["Sales Support"])
    params = _LOB_PARAMS.get(lob, _LOB_PARAMS["Sales Support"])

    # Use date as seed for consistency (same date = same data)
    seed = date_obj.toordinal() + hash(lob) % 10000
    rng = random.Random(seed)

    # Weekend = lower volume
    is_weekend = date_obj.weekday() >= 5
    weekend_factor = 0.6 if is_weekend else 1.0

    intervals = []
    for i, mult in enumerate(profile):
        hour = 8 + i // 2
        minute = (i % 2) * 30
        time_str = f"{hour:02d}:{minute:02d}"
        ts = f"{date_obj.strftime('%Y-%m-%d')} {time_str}"

        # Add some random jitter (±15%)
        jitter = rng.uniform(0.85, 1.15)
        offered = round(params["peak_offered"] * mult * weekend_factor * jitter, 1)
        aht = round(params["aht"] * rng.uniform(0.9, 1.1), 1)

        # Erlang-based requirement estimate
        traffic = (offered * aht) / 1800  # 30-min interval
        agents_required = max(1, math.ceil(traffic * 1.2))  # 20% buffer

        intervals.append({
            "time": ts,
            "offered": offered,
            "aht": aht,
            "agents_required": agents_required,
        })

    return intervals


def get_demo_employees():
    """Return (employees_list, None)."""
    return list(DEMO_EMPLOYEES), None


def get_demo_planning_units():
    """Return (units_list, None)."""
    return [{"id": lob, "name": lob} for lob in DEMO_LOBS], None


def get_demo_requirements(lob, day):
    """Return (list_of_{time, agents_required}, None) for a LOB + date."""
    if isinstance(day, str):
        day = datetime.datetime.strptime(day[:10], "%Y-%m-%d").date()
    intervals = _generate_intervals(lob, day)
    return [{"time": iv["time"], "agents_required": iv["agents_required"]}
            for iv in intervals], None


def get_demo_forecast(lob, day):
    """Return (list_of_{time, offered, aht}, None) for a LOB + date."""
    if isinstance(day, str):
        day = datetime.datetime.strptime(day[:10], "%Y-%m-%d").date()
    intervals = _generate_intervals(lob, day)
    return [{"time": iv["time"], "offered": iv["offered"], "aht": iv["aht"]}
            for iv in intervals], None


def get_demo_accommodations():
    """Return (accommodations_list, None)."""
    return list(DEMO_ACCOMMODATIONS), None


def get_demo_pto():
    """Return (pto_list, None)."""
    return _demo_pto(), None


# ── Schedules ────────────────────────────────────────────────

_SHIFT_PATTERNS = [
    ("08:00", "16:30", 8.0),
    ("09:00", "17:30", 8.0),
    ("10:00", "18:30", 8.0),
    ("07:00", "15:30", 8.0),
    ("11:00", "19:30", 8.0),
    ("12:00", "20:30", 8.0),
]


def get_demo_schedules(schedule_date=None):
    """Return a list of schedule dicts for the given date."""
    if schedule_date is None:
        schedule_date = datetime.date.today()
    if isinstance(schedule_date, str):
        schedule_date = datetime.datetime.strptime(schedule_date[:10], "%Y-%m-%d").date()

    weekday = schedule_date.weekday()
    rng = random.Random(schedule_date.toordinal())

    schedules = []
    for i, emp in enumerate(DEMO_EMPLOYEES):
        name = f"{emp['First Name']} {emp['Last Name']}"
        shift = _SHIFT_PATTERNS[i % len(_SHIFT_PATTERNS)]
        start_str, end_str, hours = shift

        # Weekend: only ~20% of staff works
        if weekday >= 5 and rng.random() > 0.2:
            schedules.append({
                "id": 8000 + i,
                "employee": name,
                "employee_id": emp["Employee ID"],
                "date": schedule_date.isoformat(),
                "start": "", "end": "",
                "type": "full", "hours": 0,
                "status": "off", "segments": [],
            })
            continue

        # Build segments
        sh, sm = int(start_str[:2]), int(start_str[3:])
        segments = [
            {"id": 0, "type": "on-call",  "start": start_str, "end": f"{sh+2:02d}:{sm:02d}", "duration_mins": 120, "sort_order": 0, "notes": ""},
            {"id": 1, "type": "break",    "start": f"{sh+2:02d}:{sm:02d}", "end": f"{sh+2:02d}:15", "duration_mins": 15, "sort_order": 1, "notes": ""},
            {"id": 2, "type": "on-call",  "start": f"{sh+2:02d}:15", "end": f"{sh+4:02d}:{sm:02d}", "duration_mins": 105, "sort_order": 2, "notes": ""},
            {"id": 3, "type": "lunch",    "start": f"{sh+4:02d}:{sm:02d}", "end": f"{sh+4:02d}:30", "duration_mins": 30, "sort_order": 3, "notes": ""},
            {"id": 4, "type": "on-call",  "start": f"{sh+4:02d}:30", "end": f"{sh+6:02d}:{sm:02d}", "duration_mins": 90, "sort_order": 4, "notes": ""},
            {"id": 5, "type": "break",    "start": f"{sh+6:02d}:{sm:02d}", "end": f"{sh+6:02d}:15", "duration_mins": 15, "sort_order": 5, "notes": ""},
            {"id": 6, "type": "on-call",  "start": f"{sh+6:02d}:15", "end": end_str, "duration_mins": int((hours - 6.5) * 60), "sort_order": 6, "notes": ""},
        ]

        schedules.append({
            "id": 8000 + i,
            "employee": name,
            "employee_id": emp["Employee ID"],
            "date": schedule_date.isoformat(),
            "start": start_str, "end": end_str,
            "type": "full", "hours": hours,
            "status": "scheduled", "segments": segments,
        })
    return schedules


# ── Dashboard summary ────────────────────────────────────────

def get_demo_dashboard_stats():
    """Summary numbers for the dashboard."""
    today = datetime.date.today()
    schedules = get_demo_schedules(today)
    on_today = sum(1 for s in schedules if s["status"] == "scheduled")
    off_today = sum(1 for s in schedules if s["status"] == "off")
    pto_entries = _demo_pto()
    pto_today = sum(1 for p in pto_entries
                    if p["Start Date"] <= today.isoformat() <= p["End Date"])
    return {
        "total_employees": len(DEMO_EMPLOYEES),
        "on_today": on_today,
        "off_today": off_today,
        "pto_today": pto_today,
        "planning_units": len(DEMO_LOBS),
        "lobs": list(DEMO_LOBS),
    }


# ── Real-Time monitoring demo data ──────────────────────────

def get_demo_realtime_snapshot(lob, date_obj):
    """Return a realistic intraday snapshot matching the real engine shape.

    Shape: {lob, date, current_interval, current_time,
            intervals: [{time, required, scheduled, gap, coverage_pct,
                         forecast_offered, forecast_aht, status}],
            current: {time, required, scheduled, gap, coverage_pct, status,
                      forecast_offered, forecast_aht},
            shifts: [{employee, employee_id, start, end, hours, type}],
            unassigned: [names],
            summary: {total_intervals, understaffed_count, overstaffed_count,
                      avg_coverage_pct, peak_required, peak_gap,
                      total_scheduled, total_unassigned},
            alerts: [{severity, message, time, details}]}
    """
    raw_ivs = _generate_intervals(lob, date_obj)
    # _generate_intervals returns time as full timestamp "YYYY-MM-DD HH:MM";
    # realtime data uses just "HH:MM"
    for iv in raw_ivs:
        iv["time"] = iv["time"][11:]  # "2026-09-28 08:00" → "08:00"
    now = datetime.datetime.now()
    cur_interval = f"{now.hour:02d}:{(now.minute // 30) * 30:02d}"
    rng = random.Random(date_obj.toordinal() + hash(lob) + 7)

    # Build demo shifts for this LOB's employees
    lob_emps = [e for e in DEMO_EMPLOYEES
                if e["Latest Skill Name"] == lob and e["Status"] == "Active"]
    shifts = []
    unassigned = []
    for i, emp in enumerate(lob_emps):
        name = f"{emp['First Name']} {emp['Last Name']}"
        # Weekend: ~30% off
        if date_obj.weekday() >= 5 and rng.random() > 0.3:
            unassigned.append(name)
            continue
        pattern = _SHIFT_PATTERNS[i % len(_SHIFT_PATTERNS)]
        shifts.append({
            "employee": name,
            "employee_id": emp["Employee ID"],
            "start": pattern[0],
            "end": pattern[1],
            "hours": pattern[2],
            "type": "full",
        })

    # Count scheduled agents per interval from shifts
    sched_map = {}
    for iv in raw_ivs:
        sched_map[iv["time"]] = 0
    for s in shifts:
        sh, sm = int(s["start"][:2]), int(s["start"][3:])
        eh, em = int(s["end"][:2]), int(s["end"][3:])
        s_min = sh * 60 + sm
        e_min = eh * 60 + em
        for m in range(s_min, e_min, 30):
            t = f"{m // 60:02d}:{m % 60:02d}"
            if t in sched_map:
                sched_map[t] += 1

    intervals = []
    for iv in raw_ivs:
        t = iv["time"]
        req = iv["agents_required"]
        sched = sched_map.get(t, 0)
        gap = sched - req
        pct = round((sched / req) * 100, 1) if req > 0 else (100.0 if sched > 0 else 0)
        if req > 0 and pct < 70:
            status = "critical"
        elif req > 0 and pct < 90:
            status = "warning"
        elif sched > req and req > 0:
            status = "over"
        else:
            status = "ok"
        intervals.append({
            "time": t,
            "required": req,
            "scheduled": sched,
            "gap": round(gap, 1),
            "coverage_pct": pct,
            "forecast_offered": round(iv["offered"], 1),
            "forecast_aht": round(iv["aht"], 1),
            "status": status,
        })

    # Current interval
    current = None
    for iv in intervals:
        if iv["time"] == cur_interval:
            current = iv
            break
    if not current:
        for iv in reversed(intervals):
            if iv["time"] <= cur_interval:
                current = iv
                break
    if not current:
        current = {"time": cur_interval, "required": 0, "scheduled": 0,
                   "gap": 0, "coverage_pct": 0, "status": "ok",
                   "forecast_offered": 0, "forecast_aht": 0}

    # Alerts
    cur_mins = int(cur_interval[:2]) * 60 + int(cur_interval[3:])
    alerts = []
    for iv in intervals:
        iv_mins = int(iv["time"][:2]) * 60 + int(iv["time"][3:])
        if iv_mins < cur_mins or iv_mins > cur_mins + 120:
            continue
        when = "NOW" if iv["time"] == cur_interval else f"at {iv['time']}"
        if iv["status"] == "critical":
            alerts.append({"severity": "critical", "message": f"Critical understaffing {when}",
                           "time": iv["time"],
                           "details": f"Need {iv['required']} agents, only {iv['scheduled']} scheduled ({iv['coverage_pct']}% coverage)"})
        elif iv["status"] == "warning":
            alerts.append({"severity": "warning", "message": f"Understaffed {when}",
                           "time": iv["time"],
                           "details": f"Need {iv['required']} agents, {iv['scheduled']} scheduled ({iv['coverage_pct']}% coverage)"})
    if unassigned:
        alerts.append({"severity": "info", "message": f"{len(unassigned)} employee(s) unavailable today",
                       "time": "", "details": ", ".join(unassigned[:5]) + ("…" if len(unassigned) > 5 else "")})
    sev_order = {"critical": 0, "warning": 1, "info": 2}
    alerts.sort(key=lambda a: (sev_order.get(a["severity"], 9), a["time"]))

    # Summary
    understaffed = sum(1 for iv in intervals if iv["gap"] < 0)
    overstaffed = sum(1 for iv in intervals if iv["gap"] > 0 and iv["required"] > 0)
    avg_cov = sum(iv["coverage_pct"] for iv in intervals) / max(1, len(intervals))
    peak_req = max((iv["required"] for iv in intervals), default=0)
    peak_gap = min((iv["gap"] for iv in intervals), default=0)

    return {
        "lob": lob,
        "date": date_obj.isoformat(),
        "current_interval": cur_interval,
        "current_time": now.strftime("%H:%M:%S"),
        "intervals": intervals,
        "current": current,
        "shifts": shifts,
        "unassigned": unassigned,
        "summary": {
            "total_intervals": len(intervals),
            "understaffed_count": understaffed,
            "overstaffed_count": overstaffed,
            "avg_coverage_pct": round(avg_cov, 1),
            "peak_required": peak_req,
            "peak_gap": round(peak_gap, 1),
            "total_scheduled": len(shifts),
            "total_unassigned": len(unassigned),
        },
        "alerts": alerts,
    }


def get_demo_adherence(lob, date_obj):
    """Return per-agent adherence matching the real engine shape.

    Shape: {adherence: [{employee, employee_id, shift_start, shift_end,
                         shift_type, hours, expected_state, current_interval,
                         is_on_shift}],
            unassigned: [names], current_interval, on_shift_count, total_scheduled}
    """
    now = datetime.datetime.now()
    cur_interval = f"{now.hour:02d}:{(now.minute // 30) * 30:02d}"
    cur_mins = now.hour * 60 + now.minute
    rng = random.Random(date_obj.toordinal() + hash(lob))

    lob_emps = [e for e in DEMO_EMPLOYEES
                if e["Latest Skill Name"] == lob and e["Status"] == "Active"]

    adherence = []
    unassigned = []
    for i, emp in enumerate(lob_emps):
        name = f"{emp['First Name']} {emp['Last Name']}"
        if date_obj.weekday() >= 5 and rng.random() > 0.3:
            unassigned.append(name)
            continue
        pattern = _SHIFT_PATTERNS[i % len(_SHIFT_PATTERNS)]
        s_min = int(pattern[0][:2]) * 60 + int(pattern[0][3:])
        e_min = int(pattern[1][:2]) * 60 + int(pattern[1][3:])
        is_on = s_min <= cur_mins < e_min
        if is_on:
            expected = "On Queue"
        elif cur_mins < s_min:
            expected = "Not Started"
        else:
            expected = "Shift Ended"

        adherence.append({
            "employee": name,
            "employee_id": emp["Employee ID"],
            "shift_start": pattern[0],
            "shift_end": pattern[1],
            "shift_type": "full",
            "hours": pattern[2],
            "expected_state": expected,
            "current_interval": cur_interval,
            "is_on_shift": is_on,
        })

    adherence.sort(key=lambda a: (0 if a["is_on_shift"] else 1, a["employee"]))

    return {
        "adherence": adherence,
        "unassigned": unassigned,
        "current_interval": cur_interval,
        "on_shift_count": sum(1 for a in adherence if a["is_on_shift"]),
        "total_scheduled": len(adherence),
    }


def get_demo_service_level(lob, date_obj):
    """Return interval-level service level matching the real engine shape.

    Shape: [{time, estimated_sl, agents, traffic_intensity, offered, aht}]
    """
    # Reuse the snapshot to get consistent intervals + scheduled counts
    snap = get_demo_realtime_snapshot(lob, date_obj)
    results = []
    for iv in snap["intervals"]:
        offered = iv.get("forecast_offered", 0)
        aht = iv.get("forecast_aht", 0)
        agents = iv.get("scheduled", 0)

        if offered > 0 and aht > 0 and agents > 0:
            traffic = (offered * aht) / 1800
            if agents > traffic:
                # Simple Erlang C approximation
                rho = traffic / agents
                pw = (traffic ** agents / math.factorial(int(agents))) / (
                    sum(traffic ** k / math.factorial(k) for k in range(int(agents))) +
                    (traffic ** agents / math.factorial(int(agents))) * (1 / (1 - rho))
                ) if rho < 1 else 1.0
                sl = 1 - pw * math.exp(-(agents - traffic) * (30 / aht)) if rho < 1 else 0.0
                sl = max(0.0, min(1.0, sl))
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


# ── Seed the demo user ──────────────────────────────────────

def seed_demo_user(app):
    """Create the demo@serevo.app user if it doesn't exist."""
    with app.app_context():
        from app.models import db, User
        from flask_bcrypt import generate_password_hash

        demo = User.query.filter_by(email="demo@serevo.app").first()
        if not demo:
            demo = User(
                email="demo@serevo.app",
                password_hash=generate_password_hash("demo1234").decode("utf-8"),
                display_name="Demo User",
                role="admin",
                is_demo=True,
                is_active=True,
            )
            db.session.add(demo)
        else:
            # Ensure existing demo user has admin role and is active
            demo.role = "admin"
            demo.is_demo = True
            demo.is_active = True
        db.session.commit()


# ═══════════════════════════════════════════════════════════════
# DEMO SETTINGS / CUSTOMIZATION DATA
# ═══════════════════════════════════════════════════════════════

def get_demo_settings_data():
    """Return all mock data for the Settings > Customization page.
    Each key maps to a template variable in customization.html."""
    import datetime as _dt

    cur_year = _dt.date.today().year

    segments = [
        {"id":1,"code":"on-call","label":"On Call","color":"#22c55e","is_productive":True,"is_paid":True,"is_default":True,"sort_order":0,"is_active":True,
         "offset_mins":None,"duration_mins":None,"is_flexible":False,"window_start_mins":None,"window_end_mins":None},
        {"id":2,"code":"break","label":"Break","color":"#f59e0b","is_productive":False,"is_paid":True,"is_default":True,"sort_order":1,"is_active":True,
         "offset_mins":120,"duration_mins":15,"is_flexible":True,"window_start_mins":90,"window_end_mins":150},
        {"id":3,"code":"lunch","label":"Lunch","color":"#ef4444","is_productive":False,"is_paid":False,"is_default":True,"sort_order":2,"is_active":True,
         "offset_mins":240,"duration_mins":30,"is_flexible":True,"window_start_mins":210,"window_end_mins":300},
        {"id":4,"code":"training","label":"Training","color":"#8b5cf6","is_productive":False,"is_paid":True,"is_default":False,"sort_order":3,"is_active":True,
         "offset_mins":60,"duration_mins":60,"is_flexible":False,"window_start_mins":None,"window_end_mins":None},
        {"id":5,"code":"meeting","label":"Team Meeting","color":"#3b82f6","is_productive":False,"is_paid":True,"is_default":False,"sort_order":4,"is_active":True,
         "offset_mins":0,"duration_mins":30,"is_flexible":False,"window_start_mins":None,"window_end_mins":None},
        {"id":6,"code":"coaching","label":"Coaching / 1-on-1","color":"#06b6d4","is_productive":False,"is_paid":True,"is_default":False,"sort_order":5,"is_active":True,
         "offset_mins":180,"duration_mins":30,"is_flexible":False,"window_start_mins":None,"window_end_mins":None},
        {"id":7,"code":"project","label":"Project Work","color":"#10b981","is_productive":True,"is_paid":True,"is_default":False,"sort_order":6,"is_active":True,
         "offset_mins":None,"duration_mins":None,"is_flexible":False,"window_start_mins":None,"window_end_mins":None},
    ]

    # Segments JSON for shift templates
    _day_segs = [
        {"type":"on-call","start":"08:00","end":"10:00","duration_mins":120,"notes":""},
        {"type":"break","start":"10:00","end":"10:15","duration_mins":15,"notes":""},
        {"type":"on-call","start":"10:15","end":"12:00","duration_mins":105,"notes":""},
        {"type":"lunch","start":"12:00","end":"12:30","duration_mins":30,"notes":""},
        {"type":"on-call","start":"12:30","end":"14:30","duration_mins":120,"notes":""},
        {"type":"break","start":"14:30","end":"14:45","duration_mins":15,"notes":""},
        {"type":"on-call","start":"14:45","end":"16:30","duration_mins":105,"notes":""},
    ]
    _close_segs = [
        {"type":"on-call","start":"13:30","end":"15:30","duration_mins":120,"notes":""},
        {"type":"break","start":"15:30","end":"15:45","duration_mins":15,"notes":""},
        {"type":"on-call","start":"15:45","end":"18:00","duration_mins":135,"notes":""},
        {"type":"lunch","start":"18:00","end":"18:30","duration_mins":30,"notes":""},
        {"type":"on-call","start":"18:30","end":"20:30","duration_mins":120,"notes":""},
        {"type":"break","start":"20:30","end":"20:45","duration_mins":15,"notes":""},
        {"type":"on-call","start":"20:45","end":"22:00","duration_mins":75,"notes":""},
    ]
    _mid_segs = [
        {"type":"on-call","start":"10:00","end":"12:00","duration_mins":120,"notes":""},
        {"type":"break","start":"12:00","end":"12:15","duration_mins":15,"notes":""},
        {"type":"on-call","start":"12:15","end":"14:30","duration_mins":135,"notes":""},
        {"type":"lunch","start":"14:30","end":"15:00","duration_mins":30,"notes":""},
        {"type":"on-call","start":"15:00","end":"17:00","duration_mins":120,"notes":""},
        {"type":"break","start":"17:00","end":"17:15","duration_mins":15,"notes":""},
        {"type":"on-call","start":"17:15","end":"18:30","duration_mins":75,"notes":""},
    ]

    shifts = [
        {"id":1,"name":"Day Shift — Weekday","start_time":"08:00","end_time":"16:30","hours":8.5,"shift_type":"full","segments":_day_segs,"planning_unit_id":"","lob_name":"All","day_type":"weekday","shift_category":"opening","is_active":True,"sort_order":0},
        {"id":2,"name":"Closing Shift — Weekday","start_time":"13:30","end_time":"22:00","hours":8.5,"shift_type":"full","segments":_close_segs,"planning_unit_id":"","lob_name":"All","day_type":"weekday","shift_category":"closing","is_active":True,"sort_order":1},
        {"id":3,"name":"Mid Shift — Weekday","start_time":"10:00","end_time":"18:30","hours":8.5,"shift_type":"full","segments":_mid_segs,"planning_unit_id":"","lob_name":"All","day_type":"weekday","shift_category":"mid","is_active":True,"sort_order":2},
        {"id":4,"name":"Day Shift — Saturday","start_time":"09:00","end_time":"17:30","hours":8.5,"shift_type":"full","segments":_day_segs,"planning_unit_id":"","lob_name":"All","day_type":"saturday","shift_category":"opening","is_active":True,"sort_order":3},
        {"id":5,"name":"Closing Shift — Saturday","start_time":"13:30","end_time":"22:00","hours":8.5,"shift_type":"full","segments":_close_segs,"planning_unit_id":"","lob_name":"All","day_type":"saturday","shift_category":"closing","is_active":True,"sort_order":4},
        {"id":6,"name":"Sunday Shift","start_time":"10:00","end_time":"18:30","hours":8.5,"shift_type":"full","segments":_mid_segs,"planning_unit_id":"","lob_name":"All","day_type":"sunday","shift_category":"any","is_active":True,"sort_order":5},
    ]

    rotations = [
        {
            "id":1,"name":"TL 4-Week Rotation","cycle_weeks":4,"is_active":True,
            "weeks":[
                {"label":"Week 1","shifts":{"mon":1,"tue":1,"wed":1,"thu":1,"fri":1,"sat":None,"sun":None}},
                {"label":"Week 2","shifts":{"mon":2,"tue":2,"wed":2,"thu":2,"fri":2,"sat":None,"sun":None}},
                {"label":"Week 3","shifts":{"mon":1,"tue":1,"wed":1,"thu":1,"fri":1,"sat":4,"sun":None}},
                {"label":"Week 4","shifts":{"mon":3,"tue":3,"wed":3,"thu":3,"fri":3,"sat":None,"sun":6}},
            ],
            "assignments":[
                {"id":1,"rotation_id":1,"employee_id":"E2001","employee_name":"Jamie Torres","current_week":0,"start_date":f"{cur_year}-01-06"},
                {"id":2,"rotation_id":1,"employee_id":"E1001","employee_name":"Alex Morgan","current_week":1,"start_date":f"{cur_year}-01-06"},
                {"id":3,"rotation_id":1,"employee_id":"E3001","employee_name":"Harper Wilson","current_week":2,"start_date":f"{cur_year}-01-06"},
                {"id":4,"rotation_id":1,"employee_id":"E2003","employee_name":"Drew Campbell","current_week":3,"start_date":f"{cur_year}-01-06"},
            ],
        },
    ]

    lob_settings = [
        {"id":1,"planning_unit_id":1,"lob_name":"Sales Support","service_level_target":0.80,"target_asa":30,"interval_minutes":30,"shrinkage_pct":0.30,"occupancy_target":0.85,"max_occupancy":0.92,"default_shift_hrs":8.5,"operating_start":"08:00","operating_end":"22:00","sat_operating_start":"09:00","sat_operating_end":"22:00","sun_operating_start":"10:00","sun_operating_end":"18:30"},
        {"id":2,"planning_unit_id":2,"lob_name":"Tech Help Desk","service_level_target":0.85,"target_asa":20,"interval_minutes":30,"shrinkage_pct":0.28,"occupancy_target":0.82,"max_occupancy":0.90,"default_shift_hrs":8.5,"operating_start":"08:00","operating_end":"22:00","sat_operating_start":"09:00","sat_operating_end":"20:00","sun_operating_start":"","sun_operating_end":""},
        {"id":3,"planning_unit_id":3,"lob_name":"Billing","service_level_target":0.75,"target_asa":45,"interval_minutes":30,"shrinkage_pct":0.32,"occupancy_target":0.88,"max_occupancy":0.94,"default_shift_hrs":8.0,"operating_start":"08:00","operating_end":"20:00","sat_operating_start":"09:00","sat_operating_end":"17:00","sun_operating_start":"","sun_operating_end":""},
    ]

    all_lobs = [
        {"id":1,"name":"Sales Support"},
        {"id":2,"name":"Tech Help Desk"},
        {"id":3,"name":"Billing"},
    ]

    time_off_types = [
        {"id":1,"code":"vacation","label":"Vacation","color":"#3b82f6","is_paid":True,"requires_approval":True,"max_days_per_year":15,"min_notice_days":14,"is_default":True,"is_active":True,"sort_order":0},
        {"id":2,"code":"sick","label":"Sick Leave","color":"#ef4444","is_paid":True,"requires_approval":False,"max_days_per_year":10,"min_notice_days":0,"is_default":True,"is_active":True,"sort_order":1},
        {"id":3,"code":"personal","label":"Personal Day","color":"#8b5cf6","is_paid":True,"requires_approval":True,"max_days_per_year":3,"min_notice_days":7,"is_default":False,"is_active":True,"sort_order":2},
        {"id":4,"code":"bereavement","label":"Bereavement","color":"#6b7280","is_paid":True,"requires_approval":False,"max_days_per_year":5,"min_notice_days":0,"is_default":False,"is_active":True,"sort_order":3},
        {"id":5,"code":"unpaid","label":"Unpaid Leave","color":"#f97316","is_paid":False,"requires_approval":True,"max_days_per_year":None,"min_notice_days":7,"is_default":False,"is_active":True,"sort_order":4},
        {"id":6,"code":"fmla","label":"FMLA","color":"#14b8a6","is_paid":False,"requires_approval":True,"max_days_per_year":None,"min_notice_days":30,"is_default":False,"is_active":True,"sort_order":5},
    ]

    ot_rules = [
        {"id":1,"name":"Standard Voluntary OT","rule_type":"voluntary","max_ot_hours_week":10.0,"max_ot_hours_day":4.0,"requires_approval":True,"min_notice_hours":24,"blackout_dates":[],"eligible_after_days":90,"pay_multiplier":1.5,"is_active":True},
        {"id":2,"name":"Peak Season Mandatory","rule_type":"mandatory","max_ot_hours_week":15.0,"max_ot_hours_day":4.0,"requires_approval":False,"min_notice_hours":48,"blackout_dates":[],"eligible_after_days":30,"pay_multiplier":1.5,"is_active":True},
        {"id":3,"name":"Holiday Double-Time","rule_type":"voluntary","max_ot_hours_week":8.0,"max_ot_hours_day":8.0,"requires_approval":True,"min_notice_hours":72,"blackout_dates":[],"eligible_after_days":0,"pay_multiplier":2.0,"is_active":True},
    ]

    sched_rules = [
        {"id":1,"name":"Full-Time Standard","min_hours_week":37.5,"max_hours_week":40.0,"max_hours_day":10.0,"max_consecutive_days":5,"min_rest_between_shifts_hrs":11.0,"min_days_off_per_week":2,"max_split_shifts_week":0,"allow_back_to_back":False,"is_default":True,"is_active":True},
        {"id":2,"name":"Part-Time Flex","min_hours_week":16.0,"max_hours_week":28.0,"max_hours_day":8.0,"max_consecutive_days":5,"min_rest_between_shifts_hrs":10.0,"min_days_off_per_week":2,"max_split_shifts_week":1,"allow_back_to_back":False,"is_default":False,"is_active":True},
        {"id":3,"name":"Weekend Warrior","min_hours_week":12.0,"max_hours_week":20.0,"max_hours_day":10.0,"max_consecutive_days":3,"min_rest_between_shifts_hrs":10.0,"min_days_off_per_week":4,"max_split_shifts_week":0,"allow_back_to_back":True,"is_default":False,"is_active":True},
    ]

    holidays = [
        {"id":1,"name":"New Year's Day","date":f"{cur_year}-01-01","is_full_day":True,"start_time":None,"end_time":None,"is_paid":True,"affects_forecast":True,"volume_factor":0.0,"year":cur_year,"is_recurring":True},
        {"id":2,"name":"Family Day","date":f"{cur_year}-02-17","is_full_day":True,"start_time":None,"end_time":None,"is_paid":True,"affects_forecast":True,"volume_factor":0.0,"year":cur_year,"is_recurring":True},
        {"id":3,"name":"Good Friday","date":f"{cur_year}-04-18","is_full_day":True,"start_time":None,"end_time":None,"is_paid":True,"affects_forecast":True,"volume_factor":0.0,"year":cur_year,"is_recurring":False},
        {"id":4,"name":"Victoria Day","date":f"{cur_year}-05-19","is_full_day":True,"start_time":None,"end_time":None,"is_paid":True,"affects_forecast":True,"volume_factor":0.0,"year":cur_year,"is_recurring":True},
        {"id":5,"name":"Canada Day","date":f"{cur_year}-07-01","is_full_day":True,"start_time":None,"end_time":None,"is_paid":True,"affects_forecast":True,"volume_factor":0.0,"year":cur_year,"is_recurring":True},
        {"id":6,"name":"Civic Holiday","date":f"{cur_year}-08-04","is_full_day":True,"start_time":None,"end_time":None,"is_paid":True,"affects_forecast":True,"volume_factor":0.3,"year":cur_year,"is_recurring":True},
        {"id":7,"name":"Labour Day","date":f"{cur_year}-09-01","is_full_day":True,"start_time":None,"end_time":None,"is_paid":True,"affects_forecast":True,"volume_factor":0.0,"year":cur_year,"is_recurring":True},
        {"id":8,"name":"Thanksgiving","date":f"{cur_year}-10-13","is_full_day":True,"start_time":None,"end_time":None,"is_paid":True,"affects_forecast":True,"volume_factor":0.0,"year":cur_year,"is_recurring":True},
        {"id":9,"name":"Christmas Day","date":f"{cur_year}-12-25","is_full_day":True,"start_time":None,"end_time":None,"is_paid":True,"affects_forecast":True,"volume_factor":0.0,"year":cur_year,"is_recurring":True},
        {"id":10,"name":"Boxing Day","date":f"{cur_year}-12-26","is_full_day":True,"start_time":None,"end_time":None,"is_paid":True,"affects_forecast":True,"volume_factor":0.0,"year":cur_year,"is_recurring":True},
    ]

    skill_groups = [
        {"id":1,"name":"Sales Support","description":"Inbound sales inquiries and upselling","is_active":True,"mappings":[
            {"id":1,"skill_group_id":1,"employee_id":"E1001","employee_name":"Alex Morgan","proficiency":5,"priority":1,"is_active":True},
            {"id":2,"skill_group_id":1,"employee_id":"E1002","employee_name":"Jordan Rivera","proficiency":4,"priority":1,"is_active":True},
            {"id":3,"skill_group_id":1,"employee_id":"E1003","employee_name":"Casey Chen","proficiency":4,"priority":2,"is_active":True},
        ]},
        {"id":2,"name":"Tech Help Desk","description":"Technical support and troubleshooting","is_active":True,"mappings":[
            {"id":4,"skill_group_id":2,"employee_id":"E2001","employee_name":"Jamie Torres","proficiency":5,"priority":1,"is_active":True},
            {"id":5,"skill_group_id":2,"employee_id":"E2002","employee_name":"Avery Nguyen","proficiency":4,"priority":1,"is_active":True},
            {"id":6,"skill_group_id":2,"employee_id":"E2003","employee_name":"Drew Campbell","proficiency":5,"priority":1,"is_active":True},
        ]},
        {"id":3,"name":"Billing","description":"Billing inquiries, disputes, and account changes","is_active":True,"mappings":[
            {"id":7,"skill_group_id":3,"employee_id":"E3001","employee_name":"Harper Wilson","proficiency":5,"priority":1,"is_active":True},
            {"id":8,"skill_group_id":3,"employee_id":"E3002","employee_name":"Rowan Garcia","proficiency":3,"priority":2,"is_active":True},
        ]},
        {"id":4,"name":"Escalations","description":"Cross-trained agents handling tier-2 escalations","is_active":True,"mappings":[
            {"id":9,"skill_group_id":4,"employee_id":"E1001","employee_name":"Alex Morgan","proficiency":4,"priority":1,"is_active":True},
            {"id":10,"skill_group_id":4,"employee_id":"E2001","employee_name":"Jamie Torres","proficiency":5,"priority":1,"is_active":True},
        ]},
    ]

    adherence_codes = [
        {"id":1,"code":"late","label":"Late to Shift","color":"#ef4444","is_excused":False,"category":"late","is_default":True,"is_active":True,"sort_order":0},
        {"id":2,"code":"early_out","label":"Left Early","color":"#f97316","is_excused":False,"category":"early_out","is_default":True,"is_active":True,"sort_order":1},
        {"id":3,"code":"ncns","label":"No Call / No Show","color":"#dc2626","is_excused":False,"category":"absence","is_default":True,"is_active":True,"sort_order":2},
        {"id":4,"code":"approved_late","label":"Approved Late","color":"#22c55e","is_excused":True,"category":"late","is_default":False,"is_active":True,"sort_order":3},
        {"id":5,"code":"break_over","label":"Break Overrun","color":"#f59e0b","is_excused":False,"category":"break_overrun","is_default":False,"is_active":True,"sort_order":4},
        {"id":6,"code":"system_issue","label":"System Issue","color":"#6b7280","is_excused":True,"category":"other","is_default":False,"is_active":True,"sort_order":5},
    ]

    alerts = [
        {"id":1,"name":"Service Level Below Target","alert_type":"sl_breach","threshold_value":0.80,"threshold_operator":"lt","lob_name":"All","planning_unit_id":None,"notify_email":True,"notify_in_app":True,"email_recipients":"ops-team@example.com","cooldown_minutes":30,"is_active":True},
        {"id":2,"name":"Understaffed Alert","alert_type":"understaffed","threshold_value":2.0,"threshold_operator":"gt","lob_name":"Sales Support","planning_unit_id":1,"notify_email":False,"notify_in_app":True,"email_recipients":"","cooldown_minutes":15,"is_active":True},
        {"id":3,"name":"Adherence Below 90%","alert_type":"adherence","threshold_value":0.90,"threshold_operator":"lt","lob_name":"All","planning_unit_id":None,"notify_email":True,"notify_in_app":True,"email_recipients":"wfm-lead@example.com","cooldown_minutes":60,"is_active":True},
        {"id":4,"name":"Forecast Variance > 15%","alert_type":"forecast_variance","threshold_value":15.0,"threshold_operator":"gt","lob_name":"All","planning_unit_id":None,"notify_email":False,"notify_in_app":True,"email_recipients":"","cooldown_minutes":120,"is_active":True},
    ]

    brand_data = {
        "id":1,"company_name":"Acme Customer Solutions","tagline":"Powering exceptional customer experiences",
        "accent_color":"#2563eb","contact_email":"admin@acme-cs.example.com",
        "logo_url":"","favicon_url":"","footer_text":"© Acme Customer Solutions · Workforce Management Platform",
    }

    employees = [
        {"id": i+1, "employee_id": e["Employee ID"], "name": f'{e["First Name"]} {e["Last Name"]}'}
        for i, e in enumerate(DEMO_EMPLOYEES)
    ]

    # Availability — mock per-employee, per-day entries
    DAY_NAMES = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
    availability = []
    avail_id = 0
    for i, e in enumerate(DEMO_EMPLOYEES):
        for d in range(7):
            avail_id += 1
            if d >= 5:  # weekend
                availability.append({
                    "id": avail_id, "employee_id": i+1, "day_of_week": d,
                    "day_name": DAY_NAMES[d], "is_available": False,
                    "earliest_start": None, "latest_start": None,
                    "latest_end": None, "notes": "Weekend — unavailable",
                })
            else:
                # Vary start windows slightly per employee
                es_h = 7 + (i % 3)          # 07:00, 08:00, or 09:00
                ls_h = es_h + 1
                le_h = es_h + 9             # 9-hour max span
                availability.append({
                    "id": avail_id, "employee_id": i+1, "day_of_week": d,
                    "day_name": DAY_NAMES[d], "is_available": True,
                    "earliest_start": f"{es_h:02d}:00",
                    "latest_start": f"{ls_h:02d}:00",
                    "latest_end": f"{le_h:02d}:00",
                    "notes": "",
                })

    fill_in_rules = [
        {"id":1,"shift_category":"closing","employee_id":2,"employee_name":"Jordan Rivera","employee_ext_id":"E1002","priority":0,"planning_unit_id":None,"fallback_template_id":2,"fallback_template_name":"Closing Shift — Weekday","is_active":True},
        {"id":2,"shift_category":"closing","employee_id":4,"employee_name":"Taylor Brooks","employee_ext_id":"E1004","priority":1,"planning_unit_id":None,"fallback_template_id":None,"fallback_template_name":"","is_active":True},
        {"id":3,"shift_category":"opening","employee_id":6,"employee_name":"Riley Kim","employee_ext_id":"E1006","priority":0,"planning_unit_id":None,"fallback_template_id":1,"fallback_template_name":"Day Shift — Weekday","is_active":True},
    ]

    return {
        "segments": segments,
        "shifts": shifts,
        "rotations": rotations,
        "lob_settings": lob_settings,
        "all_lobs": all_lobs,
        "time_off_types": time_off_types,
        "ot_rules": ot_rules,
        "sched_rules": sched_rules,
        "holidays": holidays,
        "skill_groups": skill_groups,
        "adherence_codes": adherence_codes,
        "alerts": alerts,
        "brand_data": brand_data,
        "employees": employees,
        "fill_in_rules": fill_in_rules,
        "availability": availability,
        "current_year": cur_year,
    }
