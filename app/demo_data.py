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
            {"id": 4, "type": "on-call",  "start": f"{sh+4:02d}:30", "end": end_str, "duration_mins": int((hours - 4.75) * 60), "sort_order": 4, "notes": ""},
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
            db.session.commit()
