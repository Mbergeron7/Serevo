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
    }


def get_demo_activities():
    """Demo data for Settings > Activities page."""
    return [
        {"id": 1, "name": "On Queue", "short_name": "OQ", "color": "#22c55e", "is_paid": True, "is_productive": True, "is_active": True, "sort_order": 0},
        {"id": 2, "name": "Break", "short_name": "BRK", "color": "#f59e0b", "is_paid": True, "is_productive": False, "is_active": True, "sort_order": 1},
        {"id": 3, "name": "Lunch", "short_name": "LCH", "color": "#ef4444", "is_paid": False, "is_productive": False, "is_active": True, "sort_order": 2},
        {"id": 4, "name": "Training", "short_name": "TRN", "color": "#8b5cf6", "is_paid": True, "is_productive": False, "is_active": True, "sort_order": 3},
        {"id": 5, "name": "Team Meeting", "short_name": "MTG", "color": "#3b82f6", "is_paid": True, "is_productive": False, "is_active": True, "sort_order": 4},
        {"id": 6, "name": "Coaching", "short_name": "CCH", "color": "#06b6d4", "is_paid": True, "is_productive": False, "is_active": True, "sort_order": 5},
        {"id": 7, "name": "Project Work", "short_name": "PRJ", "color": "#10b981", "is_paid": True, "is_productive": True, "is_active": True, "sort_order": 6},
        {"id": 8, "name": "Admin", "short_name": "ADM", "color": "#64748b", "is_paid": True, "is_productive": False, "is_active": True, "sort_order": 7},
    ]


def get_demo_contracts():
    """Demo data for Settings > Contracts page."""
    return [
        {"id": 1, "name": "Full-Time 42.5h", "weekly_hours": 42.5, "days_per_week": 5, "hours_per_day": 8.5, "is_active": True, "employee_count": 12},
        {"id": 2, "name": "Full-Time 40h", "weekly_hours": 40.0, "days_per_week": 5, "hours_per_day": 8.0, "is_active": True, "employee_count": 5},
        {"id": 3, "name": "Part-Time 32h", "weekly_hours": 32.0, "days_per_week": 4, "hours_per_day": 8.0, "is_active": True, "employee_count": 3},
        {"id": 4, "name": "Part-Time 24h", "weekly_hours": 24.0, "days_per_week": 3, "hours_per_day": 8.0, "is_active": True, "employee_count": 2},
        {"id": 5, "name": "Weekend Only", "weekly_hours": 16.0, "days_per_week": 2, "hours_per_day": 8.0, "is_active": True, "employee_count": 1},
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
            {"id": 1, "name": "2-Week Rotation A", "cycle_weeks": 2, "is_active": True, "row_count": 2},
            {"id": 2, "name": "4-Week Rotation B", "cycle_weeks": 4, "is_active": True, "row_count": 3},
            {"id": 3, "name": "Fixed Early", "cycle_weeks": 1, "is_active": True, "row_count": 1},
        ],
        "day_models": [
            {"id": 1, "name": "Early 6:00–14:30"},
            {"id": 2, "name": "Morning 8:00–16:30"},
            {"id": 3, "name": "Mid 10:00–18:30"},
            {"id": 4, "name": "Late 12:00–20:30"},
            {"id": 5, "name": "Evening 14:00–22:30"},
            {"id": 6, "name": "Short AM 8:00–12:00"},
            {"id": 7, "name": "Off"},
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
