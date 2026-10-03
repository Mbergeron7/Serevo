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
    # Sales Support (7 agents) — Team Lead: Lisa Tran
    {"Status": "Active", "First Name": "Alex",    "Last Name": "Morgan",    "Employee ID": "E1001", "Latest Skill Name": "Sales Support",   "Latest Skill Start": "2025-01-15", "Latest Skill End": "", "All Skills": "Sales Support, Escalations", "End Date": "", "Contract Type": "Full-Time", "Weekly Hours": 42.5, "Days Per Week": 5, "Hours Per Day": 8.5, "Timezone": "America/Toronto", "Schedule Excluded": "", "Languages": "English, French", "Team Lead": "Lisa Tran"},
    {"Status": "Active", "First Name": "Jordan",  "Last Name": "Rivera",    "Employee ID": "E1002", "Latest Skill Name": "Sales Support",   "Latest Skill Start": "2025-03-01", "Latest Skill End": "", "All Skills": "Sales Support", "End Date": "", "Contract Type": "Full-Time", "Weekly Hours": 42.5, "Days Per Week": 5, "Hours Per Day": 8.5, "Timezone": "America/Toronto", "Schedule Excluded": "", "Languages": "English, Spanish", "Team Lead": "Lisa Tran"},
    {"Status": "Active", "First Name": "Casey",   "Last Name": "Chen",      "Employee ID": "E1003", "Latest Skill Name": "Sales Support",   "Latest Skill Start": "2025-02-10", "Latest Skill End": "", "All Skills": "Sales Support, Admin QA", "End Date": "", "Contract Type": "Full-Time", "Weekly Hours": 32.0, "Days Per Week": 4, "Hours Per Day": 8.0, "Timezone": "America/Toronto", "Schedule Excluded": "", "Languages": "English, Mandarin", "Team Lead": "Lisa Tran"},
    {"Status": "Active", "First Name": "Taylor",  "Last Name": "Brooks",    "Employee ID": "E1004", "Latest Skill Name": "Sales Support",   "Latest Skill Start": "2024-11-01", "Latest Skill End": "", "All Skills": "Sales Support", "End Date": "", "Contract Type": "Full-Time", "Weekly Hours": 42.5, "Days Per Week": 5, "Hours Per Day": 8.5, "Timezone": "America/Toronto", "Schedule Excluded": "", "Languages": "English", "Team Lead": "Lisa Tran"},
    {"Status": "Active", "First Name": "Sam",     "Last Name": "Patel",     "Employee ID": "E1005", "Latest Skill Name": "Sales Support",   "Latest Skill Start": "2025-06-15", "Latest Skill End": "", "All Skills": "Sales Support", "End Date": "", "Contract Type": "Part-Time", "Weekly Hours": 24.0, "Days Per Week": 3, "Hours Per Day": 8.0, "Timezone": "America/Toronto", "Schedule Excluded": "", "Languages": "English, Hindi", "Team Lead": "Lisa Tran"},
    {"Status": "Active", "First Name": "Riley",   "Last Name": "Kim",       "Employee ID": "E1006", "Latest Skill Name": "Sales Support",   "Latest Skill Start": "2025-04-01", "Latest Skill End": "", "All Skills": "Sales Support, Admin Trainer", "End Date": "", "Contract Type": "Full-Time", "Weekly Hours": 42.5, "Days Per Week": 5, "Hours Per Day": 8.5, "Timezone": "America/Vancouver", "Schedule Excluded": "", "Languages": "English, Korean", "Team Lead": "Lisa Tran"},
    {"Status": "Active", "First Name": "Morgan",  "Last Name": "Lee",       "Employee ID": "E1007", "Latest Skill Name": "Sales Support",   "Latest Skill Start": "2025-05-20", "Latest Skill End": "", "All Skills": "Sales Support", "End Date": "", "Contract Type": "Full-Time", "Weekly Hours": 42.5, "Days Per Week": 5, "Hours Per Day": 8.5, "Timezone": "America/Toronto", "Schedule Excluded": "Yes", "Languages": "English", "Team Lead": "Lisa Tran"},
    # Tech Help Desk (7 agents) — Team Lead: Marco Ruiz
    {"Status": "Active", "First Name": "Jamie",   "Last Name": "Torres",    "Employee ID": "E2001", "Latest Skill Name": "Tech Help Desk",  "Latest Skill Start": "2025-01-10", "Latest Skill End": "", "All Skills": "Tech Help Desk, Escalations", "End Date": "", "Contract Type": "Full-Time", "Weekly Hours": 42.5, "Days Per Week": 5, "Hours Per Day": 8.5, "Timezone": "America/Toronto", "Schedule Excluded": "", "Languages": "English, Spanish", "Team Lead": "Marco Ruiz"},
    {"Status": "Active", "First Name": "Avery",   "Last Name": "Nguyen",    "Employee ID": "E2002", "Latest Skill Name": "Tech Help Desk",  "Latest Skill Start": "2025-02-15", "Latest Skill End": "", "All Skills": "Tech Help Desk, Admin QA", "End Date": "", "Contract Type": "Full-Time", "Weekly Hours": 42.5, "Days Per Week": 5, "Hours Per Day": 8.5, "Timezone": "America/Toronto", "Schedule Excluded": "", "Languages": "English, Vietnamese", "Team Lead": "Marco Ruiz"},
    {"Status": "Active", "First Name": "Drew",    "Last Name": "Campbell",  "Employee ID": "E2003", "Latest Skill Name": "Tech Help Desk",  "Latest Skill Start": "2024-09-01", "Latest Skill End": "", "All Skills": "Tech Help Desk, Escalations", "End Date": "", "Contract Type": "Full-Time", "Weekly Hours": 42.5, "Days Per Week": 5, "Hours Per Day": 8.5, "Timezone": "America/Toronto", "Schedule Excluded": "", "Languages": "English", "Team Lead": "Marco Ruiz"},
    {"Status": "Active", "First Name": "Quinn",   "Last Name": "Dubois",    "Employee ID": "E2004", "Latest Skill Name": "Tech Help Desk",  "Latest Skill Start": "2025-03-20", "Latest Skill End": "", "All Skills": "Tech Help Desk", "End Date": "", "Contract Type": "Part-Time", "Weekly Hours": 20.0, "Days Per Week": 5, "Hours Per Day": 4.0, "Timezone": "America/Toronto", "Schedule Excluded": "", "Languages": "English, French", "Team Lead": "Marco Ruiz"},
    {"Status": "Active", "First Name": "Reese",   "Last Name": "Martin",    "Employee ID": "E2005", "Latest Skill Name": "Tech Help Desk",  "Latest Skill Start": "2025-07-01", "Latest Skill End": "", "All Skills": "Tech Help Desk", "End Date": "", "Contract Type": "Full-Time", "Weekly Hours": 42.5, "Days Per Week": 5, "Hours Per Day": 8.5, "Timezone": "America/Toronto", "Schedule Excluded": "", "Languages": "English", "Team Lead": "Marco Ruiz"},
    {"Status": "Active", "First Name": "Dakota",  "Last Name": "Singh",     "Employee ID": "E2006", "Latest Skill Name": "Tech Help Desk",  "Latest Skill Start": "2025-04-10", "Latest Skill End": "", "All Skills": "Tech Help Desk, Admin Recruiting", "End Date": "", "Contract Type": "Full-Time", "Weekly Hours": 42.5, "Days Per Week": 5, "Hours Per Day": 8.5, "Timezone": "America/Toronto", "Schedule Excluded": "", "Languages": "English, Punjabi", "Team Lead": "Marco Ruiz"},
    {"Status": "Active", "First Name": "Skyler",  "Last Name": "O'Brien",   "Employee ID": "E2007", "Latest Skill Name": "Tech Help Desk",  "Latest Skill Start": "2025-06-01", "Latest Skill End": "", "All Skills": "Tech Help Desk", "End Date": "", "Contract Type": "Full-Time", "Weekly Hours": 42.5, "Days Per Week": 5, "Hours Per Day": 8.5, "Timezone": "America/Toronto", "Schedule Excluded": "Yes", "Languages": "English", "Team Lead": "Marco Ruiz"},
    # Billing (6 agents) — Team Lead: Priya Sharma
    {"Status": "Active", "First Name": "Harper",  "Last Name": "Wilson",    "Employee ID": "E3001", "Latest Skill Name": "Billing",         "Latest Skill Start": "2025-01-05", "Latest Skill End": "", "All Skills": "Billing, Escalations", "End Date": "", "Contract Type": "Full-Time", "Weekly Hours": 42.5, "Days Per Week": 5, "Hours Per Day": 8.5, "Timezone": "America/Toronto", "Schedule Excluded": "", "Languages": "English", "Team Lead": "Priya Sharma"},
    {"Status": "Active", "First Name": "Rowan",   "Last Name": "Garcia",    "Employee ID": "E3002", "Latest Skill Name": "Billing",         "Latest Skill Start": "2025-02-20", "Latest Skill End": "", "All Skills": "Billing, Admin Store Liaison", "End Date": "", "Contract Type": "Full-Time", "Weekly Hours": 42.5, "Days Per Week": 5, "Hours Per Day": 8.5, "Timezone": "America/Toronto", "Schedule Excluded": "", "Languages": "English, Spanish", "Team Lead": "Priya Sharma"},
    {"Status": "Active", "First Name": "Emery",   "Last Name": "Davis",     "Employee ID": "E3003", "Latest Skill Name": "Billing",         "Latest Skill Start": "2024-12-01", "Latest Skill End": "", "All Skills": "Billing", "End Date": "", "Contract Type": "Part-Time", "Weekly Hours": 24.0, "Days Per Week": 4, "Hours Per Day": 6.0, "Timezone": "America/Toronto", "Schedule Excluded": "", "Languages": "English", "Team Lead": "Priya Sharma"},
    {"Status": "Active", "First Name": "Finley",  "Last Name": "Johnson",   "Employee ID": "E3004", "Latest Skill Name": "Billing",         "Latest Skill Start": "2025-05-01", "Latest Skill End": "", "All Skills": "Billing, Admin QA", "End Date": "", "Contract Type": "Full-Time", "Weekly Hours": 42.5, "Days Per Week": 5, "Hours Per Day": 8.5, "Timezone": "America/Halifax", "Schedule Excluded": "", "Languages": "English, French", "Team Lead": "Priya Sharma"},
    {"Status": "Active", "First Name": "Blair",   "Last Name": "Thompson",  "Employee ID": "E3005", "Latest Skill Name": "Billing",         "Latest Skill Start": "2025-03-15", "Latest Skill End": "", "All Skills": "Billing", "End Date": "", "Contract Type": "Full-Time", "Weekly Hours": 42.5, "Days Per Week": 5, "Hours Per Day": 8.5, "Timezone": "America/Toronto", "Schedule Excluded": "", "Languages": "English", "Team Lead": "Priya Sharma"},
    {"Status": "Active", "First Name": "Sage",    "Last Name": "Anderson",  "Employee ID": "E3006", "Latest Skill Name": "Billing",         "Latest Skill Start": "2025-08-01", "Latest Skill End": "", "All Skills": "Billing, Admin Trainer", "End Date": "", "Contract Type": "Full-Time", "Weekly Hours": 42.5, "Days Per Week": 5, "Hours Per Day": 8.5, "Timezone": "America/Toronto", "Schedule Excluded": "", "Languages": "English", "Team Lead": "Priya Sharma"},
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


DEMO_SKILL_GROUPS = [
    {"id": 1, "name": "Sales Support"},
    {"id": 2, "name": "Tech Help Desk"},
    {"id": 3, "name": "Billing"},
    {"id": 4, "name": "Escalations"},
    {"id": 5, "name": "Admin QA"},
    {"id": 6, "name": "Admin Recruiting"},
    {"id": 7, "name": "Admin Store Liaison"},
    {"id": 8, "name": "Admin Trainer"},
]


def get_demo_employees():
    """Return (employees_list, None)."""
    return list(DEMO_EMPLOYEES), None


def get_demo_skill_groups():
    """Return list of demo skill groups for the skills dropdown."""
    return list(DEMO_SKILL_GROUPS)


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


# Demo scheduling rules — mirror what the real engine reads from Settings/People
# Rotation "TL 4-Week Rotation" (see get_demo_settings_data): week1 Day, week2 Closing,
# week3 Day + Sat, week4 Mid + Sun. Assigned: Jamie Torres, Alex Morgan, Harper Wilson,
# Drew Campbell (current_week 0..3), anchored to Jan 6 of the current year.
_DEMO_ROTATION = {
    "E2001": 0, "E1001": 1, "E3001": 2, "E2003": 3,
}
_DEMO_ROT_WEEKS = [
    {"mon": 1, "tue": 1, "wed": 1, "thu": 1, "fri": 1, "sat": None, "sun": None},
    {"mon": 2, "tue": 2, "wed": 2, "thu": 2, "fri": 2, "sat": None, "sun": None},
    {"mon": 1, "tue": 1, "wed": 1, "thu": 1, "fri": 1, "sat": 4, "sun": None},
    {"mon": 3, "tue": 3, "wed": 3, "thu": 3, "fri": 3, "sat": None, "sun": 6},
]
_DEMO_TEMPLATES = {  # id -> (name, start, end, category)
    1: ("Day Shift — Weekday", "08:00", "16:30", "opening"),
    2: ("Closing Shift — Weekday", "13:30", "22:00", "closing"),
    3: ("Mid Shift — Weekday", "10:00", "18:30", "mid"),
    4: ("Day Shift — Saturday", "09:00", "17:30", "opening"),
    5: ("Closing Shift — Saturday", "13:30", "22:00", "closing"),
    6: ("Sunday Shift", "10:00", "18:30", "any"),
}
# Weekend coverage: who works weekends per LOB (everyone else is off unless their rotation says so)
_DEMO_WEEKEND_STAFF = {
    "Sales Support":  {5: ["E1002"], 6: ["E1004"]},   # Sat: Jordan Rivera, Sun: Taylor Brooks
    "Tech Help Desk": {5: ["E2004"], 6: ["E2006"]},   # Sat: Quinn Dubois, Sun: Dakota Singh
    "Billing":        {5: ["E3002"], 6: ["E3005"]},   # Sat: Rowan Garcia, Sun: Blair Thompson
}
# Fill-in rules (see get_demo_settings_data): closing → Jordan Rivera, then Taylor Brooks
_DEMO_FILL_IN = {"closing": ["E1002", "E1004"], "opening": ["E1006"]}
_DEMO_ACCOM = {  # ext id -> {day_off: weekday int or None, start, end}
    "E1003": {"day_off": 2, "start": "09:00", "end": "17:00"},  # Casey Chen — Wednesdays off
    "E2003": {"day_off": None, "start": "10:00", "end": "18:00"},  # Drew Campbell — late start
}


def _demo_rotation_shift(ext_id, day):
    """Return template tuple, {"off": True}, or None if not on rotation."""
    if ext_id not in _DEMO_ROTATION:
        return None
    anchor = datetime.date(day.year, 1, 6)
    anchor -= datetime.timedelta(days=anchor.weekday())   # Monday of that week
    weeks = max(0, (day - anchor).days) // 7
    week = (weeks + _DEMO_ROTATION[ext_id]) % len(_DEMO_ROT_WEEKS)
    tid = _DEMO_ROT_WEEKS[week][["mon", "tue", "wed", "thu", "fri", "sat", "sun"][day.weekday()]]
    if not tid:
        return {"off": True, "name": "TL 4-Week Rotation"}
    return _DEMO_TEMPLATES[tid]


def _demo_pto_set(day):
    out = set()
    for p in _demo_pto():
        if p["Start Date"] <= day.isoformat() <= p["End Date"]:
            out.add(p["Employee"])
    return out


def _build_demo_segments(start_str, end_str, stagger_idx, total):
    """Break/lunch placement from the demo segment rules (break1 @2h, lunch @4h, break2 @6h)."""
    sh, sm = map(int, start_str.split(":"))
    eh, em = map(int, end_str.split(":"))
    s_min, e_min = sh * 60 + sm, eh * 60 + em
    stagger = int((stagger_idx % max(1, total)) * (60 / max(1, total)))  # spread across a 1h window
    pauses = []
    for off, dur, typ in ((120, 15, "break"), (240, 30, "lunch"), (360, 15, "break")):
        ps = s_min + off + stagger
        if ps + dur <= e_min:
            pauses.append((ps, ps + dur, dur, typ))
    segs, cursor, sid = [], s_min, 0
    for ps, pe, dur, typ in pauses:
        if cursor < ps:
            segs.append({"id": sid, "type": "on-call", "start": _mm(cursor), "end": _mm(ps),
                         "duration_mins": ps - cursor, "sort_order": sid, "notes": ""}); sid += 1
        segs.append({"id": sid, "type": typ, "start": _mm(ps), "end": _mm(pe),
                     "duration_mins": dur, "sort_order": sid, "notes": ""}); sid += 1
        cursor = pe
    if cursor < e_min:
        segs.append({"id": sid, "type": "on-call", "start": _mm(cursor), "end": _mm(e_min),
                     "duration_mins": e_min - cursor, "sort_order": sid, "notes": ""})
    return segs


def _mm(m):
    return f"{m // 60:02d}:{m % 60:02d}"


def _demo_holiday_for_date(day):
    """Return the holiday dict if *day* falls on a demo holiday, else None."""
    settings = get_demo_settings_data()
    for h in settings.get("holidays", []):
        if h["date"] == day.isoformat():
            return h
    return None


def plan_demo_day(schedule_date, lob=None):
    """Apply the demo rules for one date. Returns (schedules, warnings).
    schedules: full list (status scheduled | off | pto), each with a 'reason' for non-working days."""
    if isinstance(schedule_date, str):
        schedule_date = datetime.datetime.strptime(schedule_date[:10], "%Y-%m-%d").date()
    day = schedule_date
    wd = day.weekday()

    # ── Holiday check ────────────────────────────────────────
    holiday = _demo_holiday_for_date(day)
    if holiday and holiday.get("volume_factor", 1.0) == 0:
        # Fully closed — everyone is off
        schedules = []
        for i, emp in enumerate(DEMO_EMPLOYEES):
            emp_lob = emp["Latest Skill Name"]
            if lob and emp_lob != lob:
                continue
            name = f"{emp['First Name']} {emp['Last Name']}"
            schedules.append({
                "id": 8000 + i + day.toordinal() % 1000,
                "employee": name,
                "employee_id": emp["Employee ID"],
                "date": day.isoformat(),
                "start": "", "end": "", "type": "full", "hours": 0,
                "segments": [],
                "team_lead": emp.get("Team Lead", ""),
                "status": "off",
                "reason": holiday["name"],
            })
        return schedules, [f"Holiday: {holiday['name']} — centre closed"]

    pto_names = _demo_pto_set(day)
    schedules, warnings, skipped = [], [], []
    scheduled_by_lob = {}

    for i, emp in enumerate(DEMO_EMPLOYEES):
        emp_lob = emp["Latest Skill Name"]
        if lob and emp_lob != lob:
            continue
        ext = emp["Employee ID"]
        name = f"{emp['First Name']} {emp['Last Name']}"
        base = {"id": 8000 + i + day.toordinal() % 1000, "employee": name, "employee_id": ext,
                "date": day.isoformat(), "start": "", "end": "", "type": "full", "hours": 0,
                "segments": [], "lob": emp_lob, "team_lead": emp.get("Team Lead", "")}

        def off(status, reason):
            schedules.append({**base, "status": status, "reason": reason})
            skipped.append(f"{name}: {reason}")

        if name in pto_names:
            off("pto", "PTO"); continue
        accom = _DEMO_ACCOM.get(ext)
        if accom and accom["day_off"] == wd:
            off("off", "accommodation — day off"); continue

        rot = _demo_rotation_shift(ext, day)
        if isinstance(rot, dict) and rot.get("off"):
            off("off", f"rotation '{rot['name']}' day off"); continue

        if rot:
            _, start_str, end_str, _cat = rot
        elif wd >= 5:
            if ext not in _DEMO_WEEKEND_STAFF.get(emp_lob, {}).get(wd, []):
                off("off", "not on weekend coverage"); continue
            start_str, end_str = ("09:00", "17:30") if wd == 5 else ("10:00", "18:30")
        else:
            start_str, end_str, _ = _SHIFT_PATTERNS[i % len(_SHIFT_PATTERNS)]

        if accom:
            start_str, end_str = accom["start"], accom["end"]

        sh, sm = map(int, start_str.split(":")); eh, em = map(int, end_str.split(":"))
        hours = round(((eh * 60 + em) - (sh * 60 + sm)) / 60, 2)
        idx = scheduled_by_lob.get(emp_lob, 0); scheduled_by_lob[emp_lob] = idx + 1
        schedules.append({**base, "start": start_str, "end": end_str, "hours": hours,
                          "status": "scheduled", "reason": "",
                          "segments": _build_demo_segments(start_str, end_str, idx, 7)})

    # ── Partial-holiday reduction ────────────────────────────────
    if holiday and 0 < holiday.get("volume_factor", 1.0) < 1.0:
        vf = holiday["volume_factor"]
        # Group scheduled employees by LOB, keep only ceil(count * vf) per LOB
        from collections import defaultdict
        by_lob = defaultdict(list)
        for s in schedules:
            if s["status"] == "scheduled":
                by_lob[s.get("lob", s.get("_lob", ""))].append(s)
        for lob_name, sched_list in by_lob.items():
            keep = max(1, math.ceil(len(sched_list) * vf))
            for s in sched_list[keep:]:
                s["status"] = "off"
                s["reason"] = holiday["name"]
                s["start"] = ""
                s["end"] = ""
                s["hours"] = 0
                s["segments"] = []
        warnings.append(f"Holiday: {holiday['name']} — staffing reduced to {vf:.0%}")

    # Fill-in pass: closing shift must be covered on weekdays for each LOB with a rule
    if wd < 5:
        for cat, backups in _DEMO_FILL_IN.items():
            tmpl = next((t for t in _DEMO_TEMPLATES.values() if t[3] == cat), None)
            if not tmpl:
                continue
            for lob_name in (["Sales Support"] if not lob else [lob]):
                if lob_name != "Sales Support":
                    continue
                covered = any(s["status"] == "scheduled" and s["lob"] == lob_name and
                              s["start"] == tmpl[1] and s["end"] == tmpl[2] for s in schedules)
                if covered:
                    continue
                for ext in backups:
                    cand = next((s for s in schedules if s["employee_id"] == ext), None)
                    if not cand or cand["status"] != "scheduled":
                        continue
                    cand["start"], cand["end"] = tmpl[1], tmpl[2]
                    cand["hours"] = 8.5
                    cand["segments"] = _build_demo_segments(tmpl[1], tmpl[2], 3, 7)
                    cand["fill_in"] = True
                    warnings.append(f"Fill-in: {cand['employee']} covers {cat} ({tmpl[0]})")
                    break

    if skipped:
        warnings.append(f"Skipped {len(skipped)} employee(s): " + "; ".join(skipped))
    for s in schedules:
        s.pop("lob", None)
    return schedules, warnings


def get_demo_schedules(schedule_date=None):
    """Return a list of schedule dicts for the given date (all LOBs)."""
    if schedule_date is None:
        schedule_date = datetime.date.today()
    schedules, _ = plan_demo_day(schedule_date)
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

    # Shifts come from the same demo planner the Scheduling page uses
    day_plan, _ = plan_demo_day(date_obj, lob)
    shifts = [{"employee": s["employee"], "employee_id": s["employee_id"],
               "start": s["start"], "end": s["end"], "hours": s["hours"], "type": s["type"]}
              for s in day_plan if s["status"] == "scheduled"]
    unassigned = [s["employee"] for s in day_plan if s["status"] != "scheduled"]

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

    day_plan, _ = plan_demo_day(date_obj, lob)
    adherence = []
    unassigned = [s["employee"] for s in day_plan if s["status"] != "scheduled"]
    for s in day_plan:
        if s["status"] != "scheduled":
            continue
        name = s["employee"]
        emp = {"Employee ID": s["employee_id"]}
        pattern = (s["start"], s["end"], s["hours"])
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


# ── Real-time actuals demo data (ACD intervals + agent status) ─────

def get_demo_interval_actuals(lob, date_obj):
    """Fake ACD interval stats up to the current interval (whole day for past dates)."""
    now = datetime.datetime.now()
    if date_obj > now.date():
        return []
    cutoff = "23:59" if date_obj < now.date() else f"{now.hour:02d}:{(now.minute // 30) * 30:02d}"
    rng = random.Random(date_obj.toordinal() * 31 + hash(lob) % 1000)
    snap = get_demo_realtime_snapshot(lob, date_obj)
    sched_by_time = {iv["time"]: iv["scheduled"] for iv in snap["intervals"]}
    out = []
    for iv in _generate_intervals(lob, date_obj):
        t = iv["time"][11:]
        if t >= cutoff:
            break
        fc = iv["offered"]
        offered = max(0, int(round(fc * rng.uniform(0.82, 1.18))))
        agents = sched_by_time.get(t, 0)
        need = iv["agents_required"]
        # Coverage drives how many calls get answered quickly
        cov = (agents / need) if need else 1.0
        within_rate = max(0.35, min(0.97, 0.55 + 0.4 * cov + rng.uniform(-0.08, 0.08)))
        aband_rate = max(0.0, min(0.25, 0.10 - 0.08 * cov + rng.uniform(-0.02, 0.04)))
        abandoned = int(round(offered * aband_rate))
        rolled = int(round(offered * 0.01)) if rng.random() < 0.2 else 0
        answered = max(0, offered - abandoned - rolled)
        within = int(round(answered * within_rate))
        asa = round(max(5, 60 - 45 * within_rate + rng.uniform(-5, 10)), 1) if answered else None
        aht = round(iv["aht"] * rng.uniform(0.9, 1.12), 1) if answered else None
        out.append({
            "time": t, "timestamp": iv["time"],
            "offered": offered, "answered": answered, "answered_within": within,
            "abandoned": abandoned, "rolled": rolled, "asa": asa, "aht": aht,
            "max_queued": int(max(0, (offered - answered * 0.6) // 3 + rng.randint(0, 3))) if offered else 0,
        })
    return out


def get_demo_agent_events(date_obj):
    """Fake agent status timeline for everyone scheduled on the date."""
    now = datetime.datetime.now()
    if date_obj > now.date():
        return []
    is_today = date_obj == now.date()
    rng = random.Random(date_obj.toordinal() * 7)
    events = []
    for i, s in enumerate(get_demo_schedules(date_obj)):
        if s.get("status") == "off" or not s.get("start"):
            continue
        sh, sm = map(int, s["start"].split(":"))
        eh, em = map(int, s["end"].split(":"))
        sh_start = datetime.datetime.combine(date_obj, datetime.time(sh, sm))
        sh_end = datetime.datetime.combine(date_obj, datetime.time(eh, em))
        r = rng.random()
        if r < 0.06:
            continue  # no-show
        late_mins = rng.choice([0, 0, 0, 0, 2, 4, 7, 12, 18]) if r > 0.1 else 25
        cursor = sh_start + datetime.timedelta(minutes=late_mins)
        leave_early = rng.random() < 0.08
        planned_end = sh_end - datetime.timedelta(minutes=rng.randint(20, 45)) if leave_early else sh_end
        seq = []
        for seg in s.get("segments", []):
            st = datetime.datetime.combine(date_obj, datetime.time(*map(int, seg["start"].split(":"))))
            en = datetime.datetime.combine(date_obj, datetime.time(*map(int, seg["end"].split(":"))))
            typ = seg["type"]
            if typ == "on-call":
                # alternate On Queue / Interacting / ACW blocks
                t = max(st, cursor)
                while t < en:
                    block = min(en, t + datetime.timedelta(minutes=rng.randint(8, 25)))
                    status = rng.choice(["On Queue", "Interacting", "Interacting", "ACW"])
                    seq.append((status, t, block))
                    t = block
            else:
                label = {"break": "Break", "lunch": "Lunch"}.get(typ, typ.title())
                seq.append((label, max(st, cursor), en))
            cursor = max(cursor, en)
        # Clip to planned end, and to "now" for today
        for status, st, en in seq:
            if st >= planned_end:
                break
            en = min(en, planned_end)
            if is_today:
                if st >= now:
                    break
                if en > now:
                    en = None  # still in this status
            events.append({
                "employee": s["employee"], "employee_id": s["employee_id"], "status": status,
                "start": st.strftime("%Y-%m-%d %H:%M:%S"),
                "end": en.strftime("%Y-%m-%d %H:%M:%S") if en else None,
            })
        if (not is_today or planned_end <= now) and events and events[-1]["employee_id"] == s["employee_id"]:
            events.append({"employee": s["employee"], "employee_id": s["employee_id"], "status": "Offline",
                           "start": planned_end.strftime("%Y-%m-%d %H:%M:%S"),
                           "end": (planned_end + datetime.timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")})
    return events


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
        "lob_mappings": [
            {"id": 1, "source_name": "SS Sales Combined", "planning_unit_name": "SS Sales"},
            {"id": 2, "source_name": "CS Tier1 Overflow", "planning_unit_name": "Customer Service"},
            {"id": 3, "source_name": "Tech_Support_L2", "planning_unit_name": "Tech Support"},
            {"id": 4, "source_name": "Billing-Collections", "planning_unit_name": "Billing"},
        ],
    }


def get_demo_activities():
    """Demo data for Settings > Activities page — returns segment code format."""
    return get_demo_customization()["segments"]


def get_demo_customization():
    """Demo customization data including segment codes for scheduling."""
    return {
        "segments": [
            {"id": 1, "code": "on-call", "label": "On-Call", "color": "#059669",
             "is_productive": True, "is_paid": True, "is_default": True,
             "sort_order": 0, "is_active": True,
             "offset_mins": None, "duration_mins": None,
             "is_flexible": False, "window_start_mins": None, "window_end_mins": None},
            {"id": 2, "code": "break", "label": "Break", "color": "#d97706",
             "is_productive": False, "is_paid": True, "is_default": True,
             "sort_order": 1, "is_active": True,
             "offset_mins": 120, "duration_mins": 15,
             "is_flexible": True, "window_start_mins": 90, "window_end_mins": 180},
            {"id": 3, "code": "lunch", "label": "Lunch", "color": "#2563eb",
             "is_productive": False, "is_paid": False, "is_default": True,
             "sort_order": 2, "is_active": True,
             "offset_mins": 240, "duration_mins": 30,
             "is_flexible": True, "window_start_mins": 210, "window_end_mins": 300},
            {"id": 4, "code": "training", "label": "Training", "color": "#ea580c",
             "is_productive": False, "is_paid": True, "is_default": True,
             "sort_order": 3, "is_active": True,
             "offset_mins": 60, "duration_mins": 60,
             "is_flexible": False, "window_start_mins": None, "window_end_mins": None},
            {"id": 5, "code": "meeting", "label": "Meeting", "color": "#7c3aed",
             "is_productive": False, "is_paid": True, "is_default": True,
             "sort_order": 4, "is_active": True,
             "offset_mins": 0, "duration_mins": 30,
             "is_flexible": False, "window_start_mins": None, "window_end_mins": None},
            {"id": 6, "code": "coaching", "label": "Coaching", "color": "#06b6d4",
             "is_productive": False, "is_paid": True, "is_default": False,
             "sort_order": 5, "is_active": True,
             "offset_mins": 180, "duration_mins": 30,
             "is_flexible": True, "window_start_mins": 150, "window_end_mins": 240},
            {"id": 7, "code": "project", "label": "Project Work", "color": "#10b981",
             "is_productive": True, "is_paid": True, "is_default": False,
             "sort_order": 6, "is_active": True,
             "offset_mins": None, "duration_mins": 60,
             "is_flexible": False, "window_start_mins": None, "window_end_mins": None},
        ]
    }


def get_demo_contracts():
    """Demo data for Settings > Contracts page."""
    return [
        {"id": 1, "name": "Full-Time 42.5h", "contract_type": "full_time", "weekly_hours": 42.5, "days_per_week": 5,
         "daily_hours_min": 8.0, "daily_hours_max": 8.5, "break_minutes": 15, "lunch_minutes": 30,
         "rest_hours": 11, "overtime_eligible": True, "is_active": True, "employee_count": 12},
        {"id": 2, "name": "Full-Time 40h", "contract_type": "full_time", "weekly_hours": 40.0, "days_per_week": 5,
         "daily_hours_min": 7.5, "daily_hours_max": 8.0, "break_minutes": 15, "lunch_minutes": 30,
         "rest_hours": 11, "overtime_eligible": True, "is_active": True, "employee_count": 5},
        {"id": 3, "name": "Part-Time 32h", "contract_type": "part_time", "weekly_hours": 32.0, "days_per_week": 4,
         "daily_hours_min": 7.5, "daily_hours_max": 8.0, "break_minutes": 15, "lunch_minutes": 30,
         "rest_hours": 11, "overtime_eligible": False, "is_active": True, "employee_count": 3},
        {"id": 4, "name": "Part-Time 24h", "contract_type": "part_time", "weekly_hours": 24.0, "days_per_week": 3,
         "daily_hours_min": 7.5, "daily_hours_max": 8.0, "break_minutes": 15, "lunch_minutes": 30,
         "rest_hours": 11, "overtime_eligible": False, "is_active": True, "employee_count": 2},
        {"id": 5, "name": "Weekend Only", "contract_type": "part_time", "weekly_hours": 16.0, "days_per_week": 2,
         "daily_hours_min": 7.5, "daily_hours_max": 8.0, "break_minutes": 15, "lunch_minutes": 0,
         "rest_hours": 11, "overtime_eligible": False, "is_active": True, "employee_count": 1},
    ]


def get_demo_day_models():
    """Demo data for Settings > Day Models page."""
    return [
        {"id": 1, "name": "Early 6:00–14:30", "start_time": "06:00", "end_time": "14:30", "break_minutes": 30, "is_active": True},
        {"id": 2, "name": "Morning 8:00–16:30", "start_time": "08:00", "end_time": "16:30", "break_minutes": 30, "is_active": True},
        {"id": 3, "name": "Mid 10:00–18:30", "start_time": "10:00", "end_time": "18:30", "break_minutes": 30, "is_active": True},
        {"id": 4, "name": "Late 12:00–20:30", "start_time": "12:00", "end_time": "20:30", "break_minutes": 30, "is_active": True},
        {"id": 5, "name": "Evening 14:00–22:30", "start_time": "14:00", "end_time": "22:30", "break_minutes": 30, "is_active": True},
        {"id": 6, "name": "Short AM 8:00–12:00", "start_time": "08:00", "end_time": "12:00", "break_minutes": 0, "is_active": True},
        {"id": 7, "name": "Off", "start_time": None, "end_time": None, "break_minutes": 0, "is_active": True},
    ]


def get_demo_skills_config():
    """Demo data for Settings > Skills page."""
    return [
        {"id": 1, "name": "English", "description": "English language support", "is_active": True, "mapping_count": 20},
        {"id": 2, "name": "French", "description": "French language support", "is_active": True, "mapping_count": 8},
        {"id": 3, "name": "Spanish", "description": "Spanish language support", "is_active": True, "mapping_count": 5},
        {"id": 4, "name": "Billing", "description": "Billing and payment inquiries", "is_active": True, "mapping_count": 10},
        {"id": 5, "name": "Tech Support", "description": "Technical troubleshooting", "is_active": True, "mapping_count": 12},
        {"id": 6, "name": "Chat", "description": "Live chat channel", "is_active": True, "mapping_count": 15},
        {"id": 7, "name": "Email", "description": "Email channel", "is_active": True, "mapping_count": 18},
        {"id": 8, "name": "Escalations", "description": "Supervisor-level escalation handling", "is_active": True, "mapping_count": 4},
    ]


def get_demo_selections():
    """Demo data for Settings > Selections page."""
    return [
        {"id": 1, "name": "Team Alpha", "description": "Morning shift team", "is_active": True, "member_count": 7},
        {"id": 2, "name": "Team Bravo", "description": "Afternoon shift team", "is_active": True, "member_count": 6},
        {"id": 3, "name": "Night Shift Pool", "description": "Agents available for night rotation", "is_active": True, "member_count": 5},
        {"id": 4, "name": "New Hires Q4", "description": "Q4 2026 onboarding group", "is_active": True, "member_count": 3},
        {"id": 5, "name": "Bilingual Agents", "description": "French/English certified", "is_active": True, "member_count": 8},
    ]


def get_demo_shift_sequences():
    """Demo data for Settings > Shift Sequences page."""
    return {
        "items": [
            {"id": 1, "name": "2-Week Rotation A", "cycle_weeks": 2, "is_active": True, "row_count": 1},
            {"id": 2, "name": "4-Week Rotation B", "cycle_weeks": 4, "is_active": True, "row_count": 1},
            {"id": 3, "name": "Fixed Early", "cycle_weeks": 1, "is_active": True, "row_count": 1},
        ],
        "shift_templates": [
            {"id": 1, "name": "Early", "start": "06:00", "end": "14:30"},
            {"id": 2, "name": "Morning", "start": "08:00", "end": "16:30"},
            {"id": 3, "name": "Mid", "start": "10:00", "end": "18:30"},
            {"id": 4, "name": "Late", "start": "12:00", "end": "20:30"},
            {"id": 5, "name": "Evening", "start": "14:00", "end": "22:30"},
            {"id": 6, "name": "Short AM", "start": "08:00", "end": "12:00"},
        ],
    }


def get_demo_planning_calendars():
    """Demo data for Settings > Planning Calendars page."""
    import datetime as _dt
    cur_year = _dt.date.today().year
    return {
        "day_types": [
            {"id": 1, "name": "Public Holiday", "color": "#ef4444", "is_holiday": True},
            {"id": 2, "name": "Company Holiday", "color": "#f97316", "is_holiday": True},
            {"id": 3, "name": "Campaign Day", "color": "#8b5cf6", "is_holiday": False},
            {"id": 4, "name": "Reduced Hours", "color": "#f59e0b", "is_holiday": False},
        ],
        "calendars": [
            {"id": 1, "name": f"{cur_year} Ontario Holidays", "entry_count": 9},
            {"id": 2, "name": f"{cur_year} Campaign Calendar", "entry_count": 4},
        ],
    }


def get_demo_planning_units_config():
    """Demo data for Settings > Planning Units page."""
    return [
        {"id": 1, "name": "Sales Support", "is_active": True, "employee_count": 7},
        {"id": 2, "name": "Tech Help Desk", "is_active": True, "employee_count": 7},
        {"id": 3, "name": "Billing", "is_active": True, "employee_count": 6},
    ]


def get_demo_coaching_sessions(employee_id=None):
    """Demo coaching session data."""
    import datetime as _dt
    today = _dt.date.today()
    _all = [
        {"id": 1, "employee_id": "E1001", "employee_name": "Alex Morgan",
         "coach_name": "Lisa Tran", "session_date": (today - _dt.timedelta(days=3)).isoformat(),
         "session_time": "10:00", "duration_mins": 30, "topic": "Call handling improvement",
         "category": "quality", "quality_score": 82.5, "notes": "Reviewed 5 calls. Tone is good but needs to summarize issue earlier.",
         "outcome": "completed", "follow_up": "Shadow top performer for 2 hours next week"},
        {"id": 2, "employee_id": "E1001", "employee_name": "Alex Morgan",
         "coach_name": "Lisa Tran", "session_date": (today - _dt.timedelta(days=17)).isoformat(),
         "session_time": "14:00", "duration_mins": 30, "topic": "Schedule adherence review",
         "category": "adherence", "quality_score": None, "notes": "Break overruns averaging 4 min. Discussed time management strategies.",
         "outcome": "completed", "follow_up": "Check adherence next week"},
        {"id": 3, "employee_id": "E1002", "employee_name": "Jordan Rivera",
         "coach_name": "Lisa Tran", "session_date": (today - _dt.timedelta(days=1)).isoformat(),
         "session_time": "09:30", "duration_mins": 45, "topic": "Upselling techniques",
         "category": "performance", "quality_score": 91.0, "notes": "Strong closer. Focus on identifying opportunities earlier in the call.",
         "outcome": "completed", "follow_up": "Review conversion metrics in 2 weeks"},
        {"id": 4, "employee_id": "E1003", "employee_name": "Casey Chen",
         "coach_name": "Lisa Tran", "session_date": (today + _dt.timedelta(days=2)).isoformat(),
         "session_time": "11:00", "duration_mins": 30, "topic": "New product training follow-up",
         "category": "general", "quality_score": None, "notes": "",
         "outcome": "", "follow_up": ""},
        {"id": 5, "employee_id": "E1008", "employee_name": "Sam Patel",
         "coach_name": "Marcus Johnson", "session_date": (today - _dt.timedelta(days=5)).isoformat(),
         "session_time": "13:00", "duration_mins": 30, "topic": "Troubleshooting process review",
         "category": "quality", "quality_score": 78.0, "notes": "Needs to follow diagnostic tree more consistently. Skipping steps leads to repeat calls.",
         "outcome": "completed", "follow_up": "Side-by-side session next Tuesday"},
        {"id": 6, "employee_id": "E1015", "employee_name": "Morgan Bailey",
         "coach_name": "Priya Sharma", "session_date": (today - _dt.timedelta(days=10)).isoformat(),
         "session_time": "15:00", "duration_mins": 30, "topic": "Billing accuracy",
         "category": "quality", "quality_score": 88.0, "notes": "Good overall. Two credits issued incorrectly — reviewed policy.",
         "outcome": "completed", "follow_up": "Audit next 10 billing adjustments"},
    ]
    if employee_id:
        return [s for s in _all if s["employee_id"] == str(employee_id)]
    return _all


def get_demo_quality_evaluations(lob="All", date_from=None, date_to=None):
    """Demo quality evaluation data."""
    import datetime as _dt, random
    random.seed(42)
    today = _dt.date.today()
    agents = [
        ("E1001", "Alex Morgan", "Sales Support"),
        ("E1002", "Jordan Rivera", "Sales Support"),
        ("E1003", "Casey Chen", "Sales Support"),
        ("E1008", "Sam Patel", "Tech Help Desk"),
        ("E1009", "Avery Kim", "Tech Help Desk"),
        ("E1010", "Drew Foster", "Tech Help Desk"),
        ("E1015", "Morgan Bailey", "Billing"),
        ("E1016", "Riley Scott", "Billing"),
    ]
    evaluators = ["Lisa Tran", "Marcus Johnson", "Priya Sharma"]
    channels = ["voice", "voice", "voice", "chat", "email"]
    dispositions = ["resolved", "resolved", "resolved", "escalated", "callback", "transfer"]
    evals = []
    eid = 1
    for days_ago in range(30):
        d = today - _dt.timedelta(days=days_ago)
        if d.weekday() >= 5:
            continue
        n_evals = random.randint(2, 5)
        for _ in range(n_evals):
            ag = random.choice(agents)
            overall = round(random.uniform(65, 100), 1)
            crit = overall < 70 and random.random() < 0.3
            evals.append({
                "id": eid,
                "employee_id": ag[0],
                "employee_name": ag[1],
                "evaluator": random.choice(evaluators),
                "eval_date": d.isoformat(),
                "interaction_id": f"INT-{random.randint(100000, 999999)}",
                "channel": random.choice(channels),
                "lob": ag[2],
                "overall_score": overall,
                "greeting_score": round(random.uniform(max(60, overall-15), min(100, overall+10)), 1),
                "knowledge_score": round(random.uniform(max(60, overall-15), min(100, overall+10)), 1),
                "process_score": round(random.uniform(max(60, overall-15), min(100, overall+10)), 1),
                "communication_score": round(random.uniform(max(60, overall-15), min(100, overall+10)), 1),
                "resolution_score": round(random.uniform(max(60, overall-15), min(100, overall+10)), 1),
                "compliance_score": round(random.uniform(max(60, overall-15), min(100, overall+10)), 1),
                "call_duration_secs": random.randint(120, 900),
                "disposition": random.choice(dispositions),
                "notes": "",
                "critical_fail": crit,
            })
            eid += 1

    if lob and lob != "All":
        evals = [e for e in evals if e["lob"] == lob]
    if date_from:
        evals = [e for e in evals if e["eval_date"] >= date_from]
    if date_to:
        evals = [e for e in evals if e["eval_date"] <= date_to]
    return evals


def get_demo_quality_dashboard(lob="All"):
    """Aggregate quality dashboard stats from demo data."""
    evals = get_demo_quality_evaluations(lob=lob)
    if not evals:
        return {"avg_score": 0, "total_evals": 0, "critical_fails": 0,
                "by_agent": [], "by_category": {}, "trend": []}

    total = len(evals)
    avg = round(sum(e["overall_score"] for e in evals) / total, 1)
    crit = sum(1 for e in evals if e["critical_fail"])

    agent_scores = {}
    for e in evals:
        agent_scores.setdefault(e["employee_name"], []).append(e["overall_score"])
    by_agent = sorted([
        {"name": k, "avg_score": round(sum(v)/len(v), 1), "eval_count": len(v)}
        for k, v in agent_scores.items()
    ], key=lambda x: -x["avg_score"])

    cats = {}
    for attr in ["greeting", "knowledge", "process", "communication", "resolution", "compliance"]:
        vals = [e[attr + "_score"] for e in evals if e.get(attr + "_score") is not None]
        cats[attr] = round(sum(vals)/len(vals), 1) if vals else None

    # Weekly trend
    from collections import defaultdict
    weekly = defaultdict(list)
    for e in evals:
        import datetime as _dt
        d = _dt.date.fromisoformat(e["eval_date"])
        week_start = d - _dt.timedelta(days=d.weekday())
        weekly[week_start.isoformat()].append(e["overall_score"])
    trend = sorted([
        {"week": k, "avg_score": round(sum(v)/len(v), 1), "count": len(v)}
        for k, v in weekly.items()
    ], key=lambda x: x["week"])

    return {
        "avg_score": avg,
        "total_evals": total,
        "critical_fails": crit,
        "by_agent": by_agent,
        "by_category": cats,
        "trend": trend,
    }


# ── Demo data for remaining sections ────────────────────────────


def get_demo_announcements():
    """Return list of demo announcements."""
    today = datetime.date.today()
    return [
        {"id": 1, "title": "Office Closed — Thanksgiving", "body": "The office will be closed Thursday and Friday for the Thanksgiving holiday. Regular schedules resume Monday.", "category": "general", "is_pinned": True, "author": "Lisa Tran", "created_at": (today - datetime.timedelta(days=2)).isoformat(), "expires_at": (today + datetime.timedelta(days=5)).isoformat()},
        {"id": 2, "title": "New Quality Scorecard Rollout", "body": "Starting next week, all LOBs will use the updated quality scorecard. Training materials are available in the Training section.", "category": "policy", "is_pinned": False, "author": "Mike Johnson", "created_at": (today - datetime.timedelta(days=5)).isoformat(), "expires_at": None},
        {"id": 3, "title": "Parking Lot Maintenance", "body": "Lot B will be closed for repaving Oct 10-12. Please use Lot C during this time.", "category": "general", "is_pinned": False, "author": "Admin", "created_at": (today - datetime.timedelta(days=7)).isoformat(), "expires_at": (today + datetime.timedelta(days=10)).isoformat()},
        {"id": 4, "title": "Q4 Incentive Program", "body": "Agents hitting 95%+ quality and ≤5% absenteeism qualify for the Q4 bonus. Details on the intranet.", "category": "recognition", "is_pinned": True, "author": "Lisa Tran", "created_at": (today - datetime.timedelta(days=10)).isoformat(), "expires_at": None},
    ]


def get_demo_shift_notes():
    """Return list of demo shift notes."""
    today = datetime.date.today()
    return [
        {"id": 1, "note_date": today.isoformat(), "shift_label": "Morning", "body": "High call volume expected due to billing cycle close. Extra agents scheduled for Billing queue.", "category": "handoff", "is_resolved": False, "author": "Lisa Tran", "created_at": today.isoformat() + "T07:45:00"},
        {"id": 2, "note_date": today.isoformat(), "shift_label": "Morning", "body": "CRM system running slow — IT aware, ETA 10am fix.", "category": "issue", "is_resolved": True, "author": "Casey Chen", "created_at": today.isoformat() + "T08:15:00"},
        {"id": 3, "note_date": (today - datetime.timedelta(days=1)).isoformat(), "shift_label": "Afternoon", "body": "New product launch FAQ added to knowledge base. Agents should review before shift.", "category": "info", "is_resolved": False, "author": "Mike Johnson", "created_at": (today - datetime.timedelta(days=1)).isoformat() + "T13:00:00"},
        {"id": 4, "note_date": (today - datetime.timedelta(days=1)).isoformat(), "shift_label": "Morning", "body": "Two call-outs on Tech Help Desk. Redistributed to Sales Support overflow.", "category": "staffing", "is_resolved": True, "author": "Lisa Tran", "created_at": (today - datetime.timedelta(days=1)).isoformat() + "T07:30:00"},
        {"id": 5, "note_date": (today - datetime.timedelta(days=2)).isoformat(), "shift_label": "Evening", "body": "Phone system rebooted at 6pm. All lines restored by 6:15pm.", "category": "issue", "is_resolved": True, "author": "Admin", "created_at": (today - datetime.timedelta(days=2)).isoformat() + "T18:20:00"},
    ]


def get_demo_employee_docs():
    """Return list of demo employee documents."""
    today = datetime.date.today()
    agents = ["Alex Morgan", "Jordan Rivera", "Casey Chen", "Sam Patel", "Taylor Kim"]
    categories = ["Contract", "Certification", "ID Copy", "Performance Review", "Training Certificate"]
    docs = []
    for i, (agent, cat) in enumerate(zip(agents, categories)):
        docs.append({
            "id": i + 1,
            "employee_id": i + 1,
            "employee_name": agent,
            "title": f"{cat} — {agent}",
            "category": cat.lower().replace(" ", "_"),
            "filename": f"{cat.lower().replace(' ', '_')}_{agent.split()[1].lower()}.pdf",
            "mime_type": "application/pdf",
            "file_size": random.randint(50000, 500000),
            "notes": "",
            "uploaded_by": "Admin",
            "created_at": (today - datetime.timedelta(days=30 + i * 10)).isoformat(),
        })
    return docs


def get_demo_training_modules():
    """Return list of demo training modules."""
    return [
        {"id": 1, "title": "New Hire Orientation", "description": "Company overview, policies, and system access.", "category": "onboarding", "duration_mins": 120, "is_required": True, "assigned_count": 20, "completed_count": 18},
        {"id": 2, "title": "CRM Advanced Features", "description": "Deep dive into search, macros, and reporting in the CRM.", "category": "systems", "duration_mins": 60, "is_required": False, "assigned_count": 15, "completed_count": 10},
        {"id": 3, "title": "De-escalation Techniques", "description": "Handling difficult customers and reducing escalations.", "category": "soft_skills", "duration_mins": 45, "is_required": True, "assigned_count": 20, "completed_count": 16},
        {"id": 4, "title": "HIPAA Compliance", "description": "Annual refresher on data privacy and HIPAA requirements.", "category": "compliance", "duration_mins": 30, "is_required": True, "assigned_count": 20, "completed_count": 20},
        {"id": 5, "title": "Sales Upselling Workshop", "description": "Techniques for identifying upsell opportunities during calls.", "category": "sales", "duration_mins": 90, "is_required": False, "assigned_count": 7, "completed_count": 3},
    ]


def get_demo_training_assignments():
    """Return list of demo training assignments."""
    today = datetime.date.today()
    agents = [("Alex Morgan", 1), ("Jordan Rivera", 2), ("Casey Chen", 3),
              ("Sam Patel", 4), ("Taylor Kim", 5)]
    assignments = []
    aid = 1
    for agent_name, emp_id in agents:
        for mod in get_demo_training_modules()[:3]:
            status = random.choice(["completed", "completed", "in_progress", "assigned"])
            assignments.append({
                "id": aid,
                "module_id": mod["id"],
                "module_title": mod["title"],
                "module_category": mod["category"],
                "employee_id": emp_id,
                "employee_name": agent_name,
                "status": status,
                "due_date": (today + datetime.timedelta(days=14)).isoformat(),
                "completed_at": (today - datetime.timedelta(days=random.randint(1, 10))).isoformat() if status == "completed" else "",
                "score": random.randint(80, 100) if status == "completed" else None,
                "notes": "",
                "assigned_at": (today - datetime.timedelta(days=20)).isoformat(),
            })
            aid += 1
    return assignments


def get_demo_support_tickets():
    """Return list of demo support tickets."""
    today = datetime.date.today()
    return [
        {"id": 1, "subject": "Headset not working", "description": "Left ear cup has no audio. Tried restarting.", "category": "equipment", "priority": "medium", "status": "open", "submitted_by": 1, "submitter_name": "Alex Morgan", "assigned_to": None, "assignee_name": None, "resolution": None, "created_at": (today - datetime.timedelta(days=1)).isoformat(), "updated_at": (today - datetime.timedelta(days=1)).isoformat()},
        {"id": 2, "subject": "VPN disconnects frequently", "description": "VPN drops every 20 minutes when working from home.", "category": "it", "priority": "high", "status": "in_progress", "submitted_by": 3, "submitter_name": "Casey Chen", "assigned_to": 99, "assignee_name": "IT Support", "resolution": None, "created_at": (today - datetime.timedelta(days=3)).isoformat(), "updated_at": (today - datetime.timedelta(days=2)).isoformat()},
        {"id": 3, "subject": "Request for standing desk", "description": "Would like to switch to a standing desk setup.", "category": "facilities", "priority": "low", "status": "resolved", "submitted_by": 2, "submitter_name": "Jordan Rivera", "assigned_to": 99, "assignee_name": "Facilities", "resolution": "Approved — desk arriving next week.", "created_at": (today - datetime.timedelta(days=10)).isoformat(), "updated_at": (today - datetime.timedelta(days=7)).isoformat()},
        {"id": 4, "subject": "CRM password reset", "description": "Locked out of CRM after too many failed attempts.", "category": "it", "priority": "high", "status": "resolved", "submitted_by": 5, "submitter_name": "Taylor Kim", "assigned_to": 99, "assignee_name": "IT Support", "resolution": "Password reset and account unlocked.", "created_at": (today - datetime.timedelta(days=5)).isoformat(), "updated_at": (today - datetime.timedelta(days=5)).isoformat()},
    ]


def get_demo_wfm_tickets():
    """Return list of demo WFM tickets."""
    today = datetime.date.today()
    return [
        {"id": 1, "subject": "Schedule change request — Nov 15", "description": "Need to swap from morning to afternoon shift on Nov 15 for a doctor's appointment.", "category": "schedule_change", "priority": "medium", "status": "open", "submitted_by": 1, "submitter_name": "Alex Morgan", "assigned_to": None, "assignee_name": None, "resolution": None, "affected_agents": "Alex Morgan", "affected_date": (today + datetime.timedelta(days=5)).isoformat(), "internal_note": None, "created_at": (today - datetime.timedelta(days=1)).isoformat(), "updated_at": (today - datetime.timedelta(days=1)).isoformat()},
        {"id": 2, "subject": "Availability update — Fridays off", "description": "Starting next month, I'm no longer available on Fridays due to school.", "category": "availability", "priority": "low", "status": "in_progress", "submitted_by": 4, "submitter_name": "Sam Patel", "assigned_to": 99, "assignee_name": "WFM Analyst", "resolution": None, "affected_agents": "Sam Patel", "affected_date": None, "internal_note": "Need to adjust rotation.", "created_at": (today - datetime.timedelta(days=4)).isoformat(), "updated_at": (today - datetime.timedelta(days=3)).isoformat()},
        {"id": 3, "subject": "Overtime request — week of Oct 20", "description": "Willing to pick up extra hours during peak week.", "category": "overtime", "priority": "medium", "status": "resolved", "submitted_by": 2, "submitter_name": "Jordan Rivera", "assigned_to": 99, "assignee_name": "WFM Analyst", "resolution": "Approved for 10 extra hours.", "affected_agents": "Jordan Rivera", "affected_date": (today + datetime.timedelta(days=10)).isoformat(), "internal_note": None, "created_at": (today - datetime.timedelta(days=8)).isoformat(), "updated_at": (today - datetime.timedelta(days=6)).isoformat()},
    ]


def get_demo_attendance_dashboard(date_str=None):
    """Return demo attendance/time clock dashboard data."""
    if date_str:
        day = datetime.datetime.strptime(date_str[:10], "%Y-%m-%d").date()
    else:
        day = datetime.date.today()
    agents = [
        ("Alex Morgan", "E1001"), ("Jordan Rivera", "E1002"), ("Casey Chen", "E1003"),
        ("Sam Patel", "E1004"), ("Taylor Kim", "E1005"), ("Drew Nguyen", "E1006"),
        ("Riley Brooks", "E1007"), ("Priya Sharma", "E1008"), ("Marcus Johnson", "E1009"),
        ("Ava Williams", "E1010"),
    ]
    rows = []
    for i, (name, eid) in enumerate(agents):
        sched_hour = 7 + (i % 4)
        clock_in_hour = sched_hour + random.choice([0, 0, 0, 0, 1])  # mostly on time
        clock_in_min = random.randint(0, 15)
        late = max(0, (clock_in_hour - sched_hour) * 60 + clock_in_min)
        is_done = i < 6
        total_h = round(random.uniform(7.5, 8.5), 1) if is_done else round(random.uniform(2.0, 5.0), 1)
        rows.append({
            "id": i + 1,
            "employee_name": name,
            "employee_id_str": eid,
            "clock_in": f"{clock_in_hour:02d}:{clock_in_min:02d}",
            "clock_out": f"{clock_in_hour + 8:02d}:{random.randint(0,30):02d}" if is_done else "",
            "total_hours": total_h,
            "status": "completed" if is_done else "active",
            "scheduled_start": f"{sched_hour:02d}:00",
            "late_minutes": late if late > 0 else None,
        })
    currently_in = sum(1 for r in rows if r["status"] == "active")
    completed = sum(1 for r in rows if r["status"] == "completed")
    total_hours = round(sum(r["total_hours"] for r in rows), 1)
    late_count = sum(1 for r in rows if r["late_minutes"] and r["late_minutes"] > 0)
    return {
        "date": day.isoformat(),
        "rows": rows,
        "stats": {
            "total_entries": len(rows),
            "currently_in": currently_in,
            "completed": completed,
            "total_hours": total_hours,
            "late": late_count,
        },
    }


def get_demo_notifications(user_id=None):
    """Return list of demo notifications."""
    today = datetime.date.today()
    return [
        {"id": 1, "category": "schedule", "title": "Schedule Published", "message": "Your schedule for next week has been published.", "link": "/my-schedule/", "is_read": False, "created_at": (today - datetime.timedelta(hours=2)).isoformat() + "T10:00:00"},
        {"id": 2, "category": "pto", "title": "PTO Approved", "message": "Your time-off request for Oct 15 has been approved.", "link": "/my-time-off/", "is_read": False, "created_at": (today - datetime.timedelta(days=1)).isoformat() + "T14:30:00"},
        {"id": 3, "category": "announcement", "title": "New Announcement", "message": "Q4 Incentive Program details posted.", "link": "/announcements/", "is_read": True, "created_at": (today - datetime.timedelta(days=3)).isoformat() + "T09:00:00"},
        {"id": 4, "category": "training", "title": "Training Due Soon", "message": "HIPAA Compliance refresher is due in 3 days.", "link": "/training/", "is_read": True, "created_at": (today - datetime.timedelta(days=5)).isoformat() + "T11:00:00"},
        {"id": 5, "category": "quality", "title": "Quality Evaluation", "message": "You received a new quality evaluation. Score: 92/100.", "link": "/quality/", "is_read": True, "created_at": (today - datetime.timedelta(days=7)).isoformat() + "T16:00:00"},
    ]


def get_demo_audit_log():
    """Return list of demo audit log entries."""
    today = datetime.date.today()
    entries = [
        {"id": 1, "user_id": 1, "user_name": "Admin", "action": "schedule_published", "detail": "Published schedule for Sales Support, week of Oct 7.", "entity_type": "schedule", "entity_id": "1", "created_at": today.isoformat() + "T08:00:00"},
        {"id": 2, "user_id": 1, "user_name": "Admin", "action": "employee_added", "detail": "Added new employee: Taylor Kim (E1005).", "entity_type": "employee", "entity_id": "5", "created_at": (today - datetime.timedelta(days=1)).isoformat() + "T10:30:00"},
        {"id": 3, "user_id": 2, "user_name": "Lisa Tran", "action": "pto_approved", "detail": "Approved PTO for Alex Morgan on Oct 15.", "entity_type": "pto", "entity_id": "10", "created_at": (today - datetime.timedelta(days=2)).isoformat() + "T14:00:00"},
        {"id": 4, "user_id": 1, "user_name": "Admin", "action": "settings_changed", "detail": "Updated brand accent color.", "entity_type": "settings", "entity_id": None, "created_at": (today - datetime.timedelta(days=3)).isoformat() + "T09:15:00"},
        {"id": 5, "user_id": 1, "user_name": "Admin", "action": "data_imported", "detail": "Imported 18 forecast intervals for Billing.", "entity_type": "forecast", "entity_id": None, "created_at": (today - datetime.timedelta(days=4)).isoformat() + "T11:45:00"},
        {"id": 6, "user_id": 2, "user_name": "Lisa Tran", "action": "schedule_generated", "detail": "Generated schedule for Tech Help Desk, week of Oct 14.", "entity_type": "schedule", "entity_id": "2", "created_at": (today - datetime.timedelta(days=5)).isoformat() + "T16:00:00"},
        {"id": 7, "user_id": 1, "user_name": "Admin", "action": "employee_updated", "detail": "Updated contract for Jordan Rivera to Full-Time.", "entity_type": "employee", "entity_id": "2", "created_at": (today - datetime.timedelta(days=6)).isoformat() + "T13:30:00"},
        {"id": 8, "user_id": None, "user_name": "System", "action": "data_imported", "detail": "Auto-imported 24 interval actuals from connected system.", "entity_type": "actuals", "entity_id": None, "created_at": (today - datetime.timedelta(days=7)).isoformat() + "T06:00:00"},
    ]
    return entries


def get_demo_clock_status():
    """Return demo clock status for portal widget."""
    return {"clocked_in": False, "employee_found": True}


def get_demo_weekly_hours():
    """Return demo weekly hours breakdown for portal widget."""
    today = datetime.date.today()
    monday = today - datetime.timedelta(days=today.weekday())
    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    days = []
    total = 0
    for i in range(7):
        d = monday + datetime.timedelta(days=i)
        if d < today and d.weekday() < 5:
            hrs = round(random.uniform(7.5, 9.0), 1)
        elif d == today and d.weekday() < 5:
            hrs = round(random.uniform(3.0, 5.0), 1)
        else:
            hrs = 0
        total += hrs
        days.append({
            "date": d.isoformat(),
            "day": day_names[i],
            "hours": hrs,
            "active_hours": None,
            "is_today": d == today,
        })
    return {"days": days, "total_hours": round(total, 1)}


def get_demo_profile_attendance(employee_id=None, days=30):
    """Return demo attendance rows for the employee profile tab."""
    today = datetime.date.today()
    rows = []
    for i in range(min(days, 20)):
        d = today - datetime.timedelta(days=i + 1)
        if d.weekday() >= 5:
            continue
        hour = random.choice([7, 8, 9])
        total_h = round(random.uniform(7.5, 9.0), 1)
        rows.append({
            "id": 9000 + i,
            "date": d.isoformat(),
            "clock_in": f"{hour:02d}:{random.randint(0,15):02d}",
            "clock_out": f"{hour + 8}:{random.randint(0,30):02d}",
            "total_hours": total_h,
            "status": "completed",
        })
    completed = [r for r in rows if r["status"] == "completed"]
    total_hours = sum(r["total_hours"] for r in completed)
    avg_hours = round(total_hours / len(completed), 1) if completed else 0
    return {
        "rows": rows,
        "summary": {
            "total_entries": len(rows),
            "completed": len(completed),
            "total_hours": round(total_hours, 1),
            "avg_hours_per_shift": avg_hours,
            "days": days,
        },
    }


def get_demo_team_calendar(week_start=None, lob=""):
    """Return demo team calendar grid data."""
    if not week_start:
        today = datetime.date.today()
        week_start = today - datetime.timedelta(days=today.weekday())
    dates = [week_start + datetime.timedelta(days=i) for i in range(7)]

    agents = [
        ("Alex Morgan", "E1001"), ("Jordan Rivera", "E1002"), ("Casey Chen", "E1003"),
        ("Taylor Brooks", "E1004"), ("Sam Patel", "E1005"), ("Riley Kim", "E1006"),
        ("Jamie Torres", "E2001"), ("Avery Nguyen", "E2002"), ("Drew Campbell", "E2003"),
        ("Harper Wilson", "E3001"), ("Rowan Garcia", "E3002"), ("Emery Davis", "E3003"),
    ]
    shifts = [("08:00", "16:30"), ("09:00", "17:30"), ("10:00", "18:30"),
              ("07:00", "15:30"), ("11:00", "19:30")]
    rows = []
    daily_counts = {d.isoformat(): {"scheduled": 0, "off": 0, "pto": 0} for d in dates}

    for idx, (name, eid) in enumerate(agents):
        cells = []
        for d in dates:
            if d.weekday() >= 5:
                cells.append({"status": "off", "label": "OFF"})
                daily_counts[d.isoformat()]["off"] += 1
            elif idx == 4 and d.weekday() == 2:
                cells.append({"status": "pto", "label": "PTO"})
                daily_counts[d.isoformat()]["pto"] += 1
            else:
                sh = shifts[(idx + d.weekday()) % len(shifts)]
                cells.append({"status": "scheduled", "label": f"{sh[0]}–{sh[1]}", "hours": 8.5, "type": "full"})
                daily_counts[d.isoformat()]["scheduled"] += 1
        rows.append({"id": idx + 1, "name": name, "employee_id": eid, "cells": cells})

    return {
        "dates": [d.isoformat() for d in dates],
        "day_labels": [d.strftime("%a %b %d") for d in dates],
        "rows": rows,
        "daily_counts": daily_counts,
        "total_employees": len(agents),
    }


def get_demo_approvals_counts():
    """Return demo pending approval counts."""
    return {"pto": 2, "bids": 3, "swaps": 1, "total": 6}


def get_demo_approvals_pto(status="pending"):
    """Return demo PTO approval entries."""
    today = datetime.date.today()
    entries = [
        {"id": 1, "employee": "Alex Morgan", "employee_id": 1, "start_date": (today + datetime.timedelta(days=5)).isoformat(), "end_date": (today + datetime.timedelta(days=5)).isoformat(), "pto_type": "vacation", "time_off_type": "Vacation", "note": "Family event", "status": "pending"},
        {"id": 2, "employee": "Casey Chen", "employee_id": 3, "start_date": (today + datetime.timedelta(days=10)).isoformat(), "end_date": (today + datetime.timedelta(days=12)).isoformat(), "pto_type": "personal", "time_off_type": "Personal Day", "note": "Moving", "status": "pending"},
        {"id": 3, "employee": "Jordan Rivera", "employee_id": 2, "start_date": (today - datetime.timedelta(days=5)).isoformat(), "end_date": (today - datetime.timedelta(days=5)).isoformat(), "pto_type": "sick", "time_off_type": "Sick Leave", "note": "", "status": "approved"},
    ]
    if status != "all":
        entries = [e for e in entries if e["status"] == status]
    return entries


def get_demo_approvals_bids(status="pending"):
    """Return demo shift bid approval entries."""
    today = datetime.date.today()
    entries = [
        {"id": 1, "employee": "Sam Patel", "employee_id": 5, "shift_date": (today + datetime.timedelta(days=3)).isoformat(), "shift_start": "08:00", "shift_end": "16:30", "hours": 8.5, "preference": "preferred", "status": "pending", "created_at": (today - datetime.timedelta(days=1)).isoformat()},
        {"id": 2, "employee": "Riley Kim", "employee_id": 6, "shift_date": (today + datetime.timedelta(days=3)).isoformat(), "shift_start": "08:00", "shift_end": "16:30", "hours": 8.5, "preference": "willing", "status": "pending", "created_at": (today - datetime.timedelta(days=1)).isoformat()},
        {"id": 3, "employee": "Taylor Brooks", "employee_id": 4, "shift_date": (today + datetime.timedelta(days=5)).isoformat(), "shift_start": "10:00", "shift_end": "18:30", "hours": 8.5, "preference": "preferred", "status": "pending", "created_at": today.isoformat()},
    ]
    if status != "all":
        entries = [e for e in entries if e["status"] == status]
    return entries


def get_demo_approvals_swaps(status="accepted"):
    """Return demo shift swap approval entries."""
    today = datetime.date.today()
    entries = [
        {"id": 1, "requester": "Alex Morgan", "requester_id": 1, "target": "Jordan Rivera", "target_id": 2, "requester_date": (today + datetime.timedelta(days=4)).isoformat(), "target_date": (today + datetime.timedelta(days=6)).isoformat(), "requester_shift": "08:00–16:30", "target_shift": "10:00–18:30", "status": "accepted", "reason": "Appointment on Thursday", "created_at": (today - datetime.timedelta(days=1)).isoformat()},
    ]
    if status == "needs_approval":
        entries = [e for e in entries if e["status"] in ("accepted", "pending")]
    elif status != "all":
        entries = [e for e in entries if e["status"] == status]
    return entries


def get_demo_wfm_ticket(ticket_id):
    """Return a single demo WFM ticket with comments."""
    tickets = {t["id"]: t for t in get_demo_wfm_tickets()}
    ticket = tickets.get(ticket_id)
    if not ticket:
        ticket = tickets.get(1, {})
        ticket = dict(ticket, id=ticket_id)
    today = datetime.date.today()
    comments = [
        {"id": 1, "ticket_id": ticket["id"], "author_name": ticket.get("submitter_name", "Agent"), "body": "Submitted this request — please let me know if you need more info.", "is_internal": False, "created_at": ticket.get("created_at", today.isoformat())},
        {"id": 2, "ticket_id": ticket["id"], "author_name": "WFM Analyst", "body": "Looking into this now.", "is_internal": False, "created_at": (today - datetime.timedelta(days=0)).isoformat()},
    ]
    return {"ticket": ticket, "comments": comments}


def get_demo_support_ticket(ticket_id):
    """Return a single demo support ticket with comments."""
    tickets = {t["id"]: t for t in get_demo_support_tickets()}
    ticket = tickets.get(ticket_id)
    if not ticket:
        ticket = tickets.get(1, {})
        ticket = dict(ticket, id=ticket_id)
    today = datetime.date.today()
    comments = [
        {"id": 1, "ticket_id": ticket["id"], "author_name": ticket.get("submitter_name", "Agent"), "body": "Any update on this?", "is_internal": False, "created_at": ticket.get("created_at", today.isoformat())},
    ]
    return {"ticket": ticket, "comments": comments}


def get_demo_employee_doc_detail(doc_id):
    """Return a single demo employee document's metadata."""
    docs = {d["id"]: d for d in get_demo_employee_docs()}
    doc = docs.get(doc_id)
    if not doc:
        doc = docs.get(1, {})
        doc = dict(doc, id=doc_id)
    return doc


# ── Live Agent Status demo data ─────────────────────────────

_LIVE_STATUSES = [
    "Ready", "On-call", "Cool-Down", "On Break", "Lunch",
    "Unavailable", "Offline", "ooq Meeting", "ooq Training",
    "ooq Coaching", "ooq Personal",
]

_LIVE_STATUS_WEIGHTS = [20, 30, 8, 6, 4, 3, 5, 4, 3, 2, 2]


def get_demo_live_agents():
    """Generate realistic live agent status data for the Live Agents dashboard.

    Returns dict: {by_lob: {lobName: [agents]}, pre_shift: [agents]}
    """
    now = datetime.datetime.now()
    today = now.date()
    rng = random.Random(now.hour * 60 + now.minute // 5)  # changes every 5 min

    schedules = get_demo_schedules(today)
    sched_by_eid = {}
    for s in schedules:
        sched_by_eid[s["employee_id"]] = s

    by_lob = {}
    pre_shift = []

    for emp in DEMO_EMPLOYEES:
        eid = emp["Employee ID"]
        name = f"{emp['First Name']} {emp['Last Name']}"
        lob = emp["Latest Skill Name"]
        sched = sched_by_eid.get(eid)

        if not sched or sched.get("status") == "off":
            continue  # day off, skip entirely

        shift_start = sched.get("start", "")
        shift_end = sched.get("end", "")

        if not shift_start or not shift_end:
            continue

        sh, sm = map(int, shift_start.split(":"))
        eh, em = map(int, shift_end.split(":"))
        shift_start_mins = sh * 60 + sm
        shift_end_mins = eh * 60 + em
        now_mins = now.hour * 60 + now.minute

        # Determine schedule-now block
        sched_now = ""
        for seg in sched.get("segments", []):
            seg_start = seg.get("start", "")
            seg_end = seg.get("end", "")
            if seg_start and seg_end:
                ss = int(seg_start.split(":")[0]) * 60 + int(seg_start.split(":")[1])
                se = int(seg_end.split(":")[0]) * 60 + int(seg_end.split(":")[1])
                if ss <= now_mins < se:
                    activity = seg.get("type", seg.get("activity", ""))
                    if "break" in activity.lower():
                        sched_now = "break"
                    elif "lunch" in activity.lower():
                        sched_now = "lunch"
                    break

        # Pre-shift: not started yet
        if now_mins < shift_start_mins:
            mins_until = shift_start_mins - now_mins
            if mins_until <= 30:  # show agents starting within 30 min
                pre_shift.append({
                    "name": name,
                    "user_id": eid,
                    "lob": lob,
                    "shift_start": shift_start,
                    "shift_end": shift_end,
                    "indicator": "not_yet_started",
                    "mins_late": 0,
                })
            continue

        # Absent: shift started but not logged in (simulate ~6% no-show)
        if now_mins >= shift_start_mins + 15:
            r = rng.random()
            if r < 0.06:
                mins_late = now_mins - shift_start_mins
                pre_shift.append({
                    "name": name,
                    "user_id": eid,
                    "lob": lob,
                    "shift_start": shift_start,
                    "shift_end": shift_end,
                    "indicator": "absent",
                    "mins_late": mins_late,
                })
                continue

        # Past shift end — left early or shift done
        shift_done = now_mins >= shift_end_mins
        not_scheduled = False

        # Pick a status weighted by time-of-day realism
        status = rng.choices(_LIVE_STATUSES, weights=_LIVE_STATUS_WEIGHTS, k=1)[0]

        # If on break/lunch, bias duration
        if status == "On Break":
            mins_in = rng.randint(1, 18)
        elif status == "Lunch":
            mins_in = rng.randint(1, 35)
        elif status == "On-call":
            mins_in = rng.randint(1, 45)
        elif status == "Ready":
            mins_in = rng.randint(0, 12)
        elif status == "Offline":
            # If shift done, show as offline with shift_done flag
            if shift_done:
                mins_in = now_mins - shift_end_mins
            else:
                mins_in = rng.randint(1, 8)
        else:
            mins_in = rng.randint(1, 25)

        since_h = (now.hour * 60 + now.minute - mins_in) // 60
        since_m = (now.hour * 60 + now.minute - mins_in) % 60
        current_since = f"{since_h:02d}:{since_m:02d}"

        # If shift done, some agents leave (show as offline)
        if shift_done and rng.random() < 0.6:
            status = "Offline"
            mins_in = now_mins - shift_end_mins
            current_since = shift_end
            since_h, since_m = eh, em

        agent = {
            "name": name,
            "user_id": eid,
            "status": status,
            "minutes_in_status": mins_in,
            "current_since": current_since,
            "lob": lob,
            "shift_start": shift_start,
            "shift_end": shift_end,
            "shift_done": shift_done if not (status == "Offline" and shift_done) else True,
            "sched_now": sched_now,
            "not_scheduled": not_scheduled,
            "pre_shift": now_mins < shift_start_mins + 5 and now_mins >= shift_start_mins,
        }

        by_lob.setdefault(lob, []).append(agent)

    return {"by_lob": by_lob, "pre_shift": pre_shift}


def get_demo_agent_detail(user_id):
    """Generate demo agent detail data for the slide-out panel."""
    now = datetime.datetime.now()
    today = now.date()
    rng = random.Random(hash(user_id) + now.hour)

    emp = None
    for e in DEMO_EMPLOYEES:
        if e["Employee ID"] == user_id:
            emp = e
            break
    if not emp:
        return {"error": "Agent not found"}

    name = f"{emp['First Name']} {emp['Last Name']}"
    schedules = get_demo_schedules(today)
    sched = None
    for s in schedules:
        if s["employee_id"] == user_id:
            sched = s
            break

    shift_start = sched.get("start", "") if sched else ""
    shift_end = sched.get("end", "") if sched else ""

    # Current status
    status = rng.choices(_LIVE_STATUSES[:6], weights=_LIVE_STATUS_WEIGHTS[:6], k=1)[0]
    mins_in = rng.randint(1, 20)
    since_h = (now.hour * 60 + now.minute - mins_in) // 60
    since_m = (now.hour * 60 + now.minute - mins_in) % 60

    # Schedule segments
    segments = []
    if sched and sched.get("segments"):
        for seg in sched["segments"]:
            activity = seg.get("type", seg.get("activity", "On-call"))
            start = seg.get("start", "")
            end = seg.get("end", "")
            if not start or not end:
                continue
            ss = int(start.split(":")[0]) * 60 + int(start.split(":")[1])
            se = int(end.split(":")[0]) * 60 + int(end.split(":")[1])
            now_mins = now.hour * 60 + now.minute
            if now_mins >= se:
                adh_class = "ok"
                adherence = "✓ Met"
            elif now_mins >= ss:
                adh_class = "active"
                adherence = "● Active"
            else:
                adh_class = "upcoming"
                adherence = "Upcoming"
            segments.append({
                "activity": activity.replace("_", " ").title(),
                "start": start,
                "end": end,
                "adh_class": adh_class,
                "adherence": adherence,
            })

    # Status timeline
    timeline = []
    if shift_start:
        sh, sm = map(int, shift_start.split(":"))
        cursor = sh * 60 + sm
        statuses_seq = ["Ready", "On-call", "Ready", "On-call", "On Break",
                        "Ready", "On-call", "Lunch", "Ready", "On-call"]
        for st in statuses_seq:
            if cursor >= now.hour * 60 + now.minute:
                break
            timeline.append({
                "time": f"{cursor // 60:02d}:{cursor % 60:02d}",
                "status": st,
            })
            cursor += rng.randint(5, 45)

    return {
        "current_status": status,
        "current_since": f"{since_h:02d}:{since_m:02d}",
        "shift_start": shift_start,
        "shift_end": shift_end,
        "segments": segments,
        "timeline": timeline,
    }
