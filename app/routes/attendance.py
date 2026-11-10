"""
Attendance — clock-in/out for agents, attendance dashboard for supervisors.
"""
import logging
from datetime import date, datetime, timedelta, timezone

from flask import Blueprint, render_template, request, jsonify
from app.auth import login_required, get_current_user
from sqlalchemy import func
from app.models import db, Employee, TimeClock, Schedule, AttendanceEvent, AdherenceException
from app.constants import DEFAULT_TIMEZONE

log = logging.getLogger("serevo.attendance")
attendance_bp = Blueprint("attendance", __name__, url_prefix="/attendance")


def _utcnow():
    return datetime.now(timezone.utc)


def _resolve_employee(user):
    """Find the Employee linked to this user account.
    Prefer the explicit employee_id link set in User Management,
    fall back to email match."""
    eid = user.get("employee_id")
    if eid:
        emp = Employee.query.filter_by(id=eid, status="Active").first()
        if emp:
            return emp
    return Employee.query.filter_by(email=user.get("email"), status="Active").first()


# ── Agent: clock in / out ─────────────────────────────────────────────

@attendance_bp.route("/api/clock-in", methods=["POST"])
@login_required
def clock_in():
    """Agent clocks in."""
    user = get_current_user()
    if not user:
        return jsonify(error="Forbidden"), 403
    if user.get("is_demo"):
        return jsonify(success=True, message="Demo mode")

    emp = _resolve_employee(user)
    if not emp:
        return jsonify(success=False, error="No active employee record found for your account.")

    # Check if already clocked in
    active = TimeClock.query.filter_by(employee_id=emp.id, status="active").first()
    if active:
        return jsonify(success=False, error="You are already clocked in.",
                       clock_in=active.clock_in.isoformat())

    payload = request.get_json(silent=True) or {}
    now = _utcnow()
    # Use employee's timezone for the date field so it matches their local calendar day
    from zoneinfo import ZoneInfo
    emp_tz = ZoneInfo(emp.timezone or DEFAULT_TIMEZONE)
    local_date = now.astimezone(emp_tz).date()
    entry = TimeClock(
        employee_id=emp.id,
        clock_in=now,
        clock_in_note=payload.get("note", ""),
        date=local_date,
        status="active",
    )
    db.session.add(entry)
    db.session.commit()
    return jsonify(success=True, id=entry.id, clock_in=now.isoformat())


@attendance_bp.route("/api/clock-out", methods=["POST"])
@login_required
def clock_out():
    """Agent clocks out."""
    user = get_current_user()
    if not user:
        return jsonify(error="Forbidden"), 403
    if user.get("is_demo"):
        return jsonify(success=True, message="Demo mode")

    emp = _resolve_employee(user)
    if not emp:
        return jsonify(success=False, error="No active employee record found.")

    active = TimeClock.query.filter_by(employee_id=emp.id, status="active").first()
    if not active:
        return jsonify(success=False, error="You are not currently clocked in.")

    payload = request.get_json(silent=True) or {}
    now = _utcnow()
    active.clock_out = now
    active.clock_out_note = payload.get("note", "")
    active.status = "completed"
    ci = active.clock_in if active.clock_in.tzinfo else active.clock_in.replace(tzinfo=timezone.utc)
    delta = (now - ci).total_seconds() / 3600
    active.total_hours = round(delta, 2)
    db.session.commit()
    return jsonify(success=True, total_hours=active.total_hours,
                   clock_out=now.isoformat())


@attendance_bp.route("/api/status", methods=["POST"])
@login_required
def clock_status():
    """Get current clock status for the logged-in agent."""
    user = get_current_user()
    if not user:
        return jsonify(error="Forbidden"), 403

    if user.get("is_demo"):
        from app.demo_data import get_demo_clock_status
        return jsonify(success=True, **get_demo_clock_status())

    emp = _resolve_employee(user)
    if not emp:
        return jsonify(success=True, clocked_in=False, employee_found=False)

    active = TimeClock.query.filter_by(employee_id=emp.id, status="active").first()
    if active:
        ci = active.clock_in if active.clock_in.tzinfo else active.clock_in.replace(tzinfo=timezone.utc)
        elapsed = (_utcnow() - ci).total_seconds() / 3600
        ci_iso = ci.isoformat()
        if not ci_iso.endswith('Z') and '+' not in ci_iso and '-' not in ci_iso[10:]:
            ci_iso += 'Z'
        return jsonify(success=True, clocked_in=True,
                       clock_in=ci_iso,
                       elapsed_hours=round(elapsed, 2))
    return jsonify(success=True, clocked_in=False)


@attendance_bp.route("/api/history", methods=["POST"])
@login_required
def clock_history():
    """Get clock history for the logged-in agent."""
    user = get_current_user()
    if not user:
        return jsonify(error="Forbidden"), 403
    if user.get("is_demo"):
        return jsonify(success=True, entries=[], message="Demo mode")

    emp = _resolve_employee(user)
    if not emp:
        return jsonify(success=True, entries=[])

    payload = request.get_json(silent=True) or {}
    days = payload.get("days", 14)
    since = date.today() - timedelta(days=days)

    entries = TimeClock.query.filter(
        TimeClock.employee_id == emp.id,
        TimeClock.date >= since,
    ).order_by(TimeClock.clock_in.desc()).limit(50).all()

    def _utc_iso(dt):
        """Ensure UTC datetimes include Z so browsers convert to local time."""
        if dt is None:
            return None
        s = dt.isoformat()
        if not s.endswith('Z') and '+' not in s and '-' not in s[10:]:
            s += 'Z'
        return s

    return jsonify(success=True, entries=[{
        "id": e.id,
        "date": e.date.isoformat(),
        "clock_in": _utc_iso(e.clock_in),
        "clock_out": _utc_iso(e.clock_out),
        "total_hours": e.total_hours,
        "status": e.status,
    } for e in entries])


# ── Supervisor: attendance dashboard ──────────────────────────────────

@attendance_bp.route("/")
@login_required
def dashboard():
    user = get_current_user()
    if not user or user.get("role") not in ("admin", "supervisor"):
        return jsonify(error="Forbidden"), 403
    return render_template("attendance/index.html", user=user)


@attendance_bp.route("/api/dashboard", methods=["POST"])
@login_required
def api_dashboard():
    """Attendance dashboard data for supervisors."""
    user = get_current_user()
    if not user or user.get("role") not in ("admin", "supervisor"):
        return jsonify(error="Forbidden"), 403

    if user and user.get("is_demo"):
        from app.demo_data import get_demo_attendance_dashboard
        payload = request.get_json(silent=True) or {}
        data = get_demo_attendance_dashboard(payload.get("date"))
        return jsonify(success=True, **data)

    payload = request.get_json(silent=True) or {}
    target_date_str = payload.get("date")
    try:
        target_date = datetime.strptime(target_date_str, "%Y-%m-%d").date() if target_date_str else date.today()
    except ValueError:
        return jsonify(success=False, error="Invalid date")

    entries = TimeClock.query.filter_by(date=target_date).order_by(TimeClock.clock_in).all()

    # Build schedule map for tardiness check
    sched_map = {}
    scheds = Schedule.query.filter_by(schedule_date=target_date).all()
    for s in scheds:
        if s.shift_start:
            sched_map[s.employee_id] = s.shift_start

    rows = []
    for e in entries:
        emp = Employee.query.get(e.employee_id) if e.employee_id else None
        # Tardiness: clock_in time vs scheduled shift_start
        late_minutes = None
        sched_start_str = ""
        if e.employee_id in sched_map and e.clock_in:
            sched_start = sched_map[e.employee_id]
            sched_start_str = sched_start.strftime("%H:%M")
            ci_time = e.clock_in.time()
            diff = (ci_time.hour * 60 + ci_time.minute) - (sched_start.hour * 60 + sched_start.minute)
            if diff > 0:
                late_minutes = diff
        rows.append({
            "id": e.id,
            "employee_name": emp.first_name + " " + emp.last_name if emp else "Unknown",
            "employee_id_str": emp.employee_id if emp else "",
            "clock_in": e.clock_in.strftime("%H:%M") if e.clock_in else "",
            "clock_out": e.clock_out.strftime("%H:%M") if e.clock_out else "",
            "total_hours": e.total_hours,
            "status": e.status,
            "scheduled_start": sched_start_str,
            "late_minutes": late_minutes,
        })

    # Summary stats
    total_entries = len(rows)
    currently_in = sum(1 for r in rows if r["status"] == "active")
    completed = sum(1 for r in rows if r["status"] == "completed")
    total_hours = sum(r["total_hours"] or 0 for r in rows)
    late_count = sum(1 for r in rows if r.get("late_minutes"))

    return jsonify(
        success=True,
        date=target_date.isoformat(),
        rows=rows,
        stats={
            "total_entries": total_entries,
            "currently_in": currently_in,
            "completed": completed,
            "total_hours": round(total_hours, 1),
            "late": late_count,
        },
    )


@attendance_bp.route("/api/weekly-hours", methods=["POST"])
@login_required
def weekly_hours():
    """Return daily hours breakdown for the current week for the logged-in agent."""
    user = get_current_user()
    if not user:
        return jsonify(error="Forbidden"), 403

    if user.get("is_demo"):
        from app.demo_data import get_demo_weekly_hours
        return jsonify(success=True, **get_demo_weekly_hours())

    emp = Employee.query.filter_by(email=user.get("email"), status="Active").first()
    if not emp:
        return jsonify(success=True, days=[], total_hours=0, employee_found=False)

    # Calculate Monday of current week
    today = date.today()
    monday = today - timedelta(days=today.weekday())

    days = []
    total = 0
    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    for i in range(7):
        d = monday + timedelta(days=i)
        hrs = db.session.query(
            func.coalesce(func.sum(TimeClock.total_hours), 0)
        ).filter(
            TimeClock.employee_id == emp.id,
            TimeClock.date == d,
            TimeClock.status == "completed",
        ).scalar()
        hrs_f = round(float(hrs), 1)

        # Check if currently active on this day
        active = None
        if d == today:
            active_entry = TimeClock.query.filter_by(
                employee_id=emp.id, date=d, status="active"
            ).first()
            if active_entry:
                ci = active_entry.clock_in
                if not ci.tzinfo:
                    ci = ci.replace(tzinfo=timezone.utc)
                elapsed = (_utcnow() - ci).total_seconds() / 3600
                active = round(elapsed, 1)

        total += hrs_f
        days.append({
            "date": d.isoformat(),
            "day": day_names[i],
            "hours": hrs_f,
            "active_hours": active,
            "is_today": d == today,
        })

    return jsonify(
        success=True,
        days=days,
        total_hours=round(total, 1),
        week_start=monday.isoformat(),
    )


# ═══════════════════════════════════════════════════════════════
# MANUAL ATTENDANCE CODING — exception codes + supervisor coding
# ═══════════════════════════════════════════════════════════════

_DEFAULT_CODES = [
    ("ABS",   "Absent",           "#ef4444", False, "absence",  True,  1),
    ("NCNS",  "No Call / No Show", "#dc2626", False, "absence",  False, 2),
    ("LATE",  "Late",             "#f59e0b", False, "late",     True,  3),
    ("EOUT",  "Early Out",        "#f97316", False, "early_out", True,  4),
    ("SICK",  "Sick",             "#a855f7", True,  "absence",  False, 5),
    ("VAC",   "Vacation",         "#3b82f6", True,  "absence",  False, 6),
    ("PER",   "Personal Day",     "#6366f1", True,  "absence",  False, 7),
    ("BRKV",  "Break Overrun",    "#fb923c", False, "break_overrun", False, 8),
    ("APPR",  "Approved Absence", "#10b981", True,  "absence",  False, 9),
    ("OTHER", "Other",            "#6b7280", False, "other",    False, 10),
]


def seed_adherence_codes(app=None):
    """Ensure the default AdherenceException codes exist."""
    try:
        existing = {c.code for c in AdherenceException.query.all()}
        for code, label, color, excused, cat, is_default, order in _DEFAULT_CODES:
            if code not in existing:
                db.session.add(AdherenceException(
                    code=code, label=label, color=color,
                    is_excused=excused, category=cat,
                    is_default=is_default, sort_order=order,
                ))
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        log.warning(f"seed_adherence_codes: {e}")


# ── API: list exception codes ─────────────────────────────────

@attendance_bp.route("/api/codes", methods=["GET"])
@login_required
def list_codes():
    """Return active adherence exception codes."""
    codes = AdherenceException.query.filter_by(is_active=True)\
        .order_by(AdherenceException.sort_order).all()
    if not codes:
        seed_adherence_codes()
        codes = AdherenceException.query.filter_by(is_active=True)\
            .order_by(AdherenceException.sort_order).all()
    return jsonify([c.to_dict() for c in codes])


# ── API: list coded attendance events for a date ──────────────

@attendance_bp.route("/api/events", methods=["POST"])
@login_required
def list_events():
    """Return manually coded attendance events for a date (and optionally LOB)."""
    data = request.get_json(silent=True) or {}
    date_str = data.get("date", date.today().isoformat())
    try:
        d = date.fromisoformat(date_str)
    except (ValueError, TypeError):
        return jsonify(error="Invalid date"), 400

    q = db.session.query(AttendanceEvent).join(Employee)\
        .filter(AttendanceEvent.event_date == d)

    lob = data.get("lob")
    if lob and lob != "all":
        from app.models import LobMapping
        emp_ids = db.session.query(LobMapping.employee_id)\
            .filter(LobMapping.lob_name == lob).subquery()
        q = q.filter(AttendanceEvent.employee_id.in_(emp_ids))

    events = q.order_by(Employee.last_name, Employee.first_name).all()
    return jsonify([e.to_dict() for e in events])


# ── API: create / update / delete attendance event ────────────

@attendance_bp.route("/api/events/save", methods=["POST"])
@login_required
def save_event():
    """Create or update an attendance event."""
    user = get_current_user()
    if not user:
        return jsonify(error="Not authenticated"), 401
    if user.get("is_demo"):
        return jsonify(success=True, message="Demo mode")

    data = request.get_json(silent=True) or {}
    emp_id = data.get("employee_id")
    date_str = data.get("date")
    code_id = data.get("adherence_exception_id")

    if not all([emp_id, date_str, code_id]):
        return jsonify(error="employee_id, date, and adherence_exception_id are required"), 400

    try:
        d = date.fromisoformat(date_str)
    except (ValueError, TypeError):
        return jsonify(error="Invalid date"), 400

    emp = Employee.query.get(emp_id)
    if not emp:
        return jsonify(error="Employee not found"), 404

    code = AdherenceException.query.get(code_id)
    if not code:
        return jsonify(error="Code not found"), 404

    event_id = data.get("id")
    if event_id:
        evt = AttendanceEvent.query.get(event_id)
        if not evt:
            return jsonify(error="Event not found"), 404
    else:
        # Check for duplicate: same employee + date + code
        evt = AttendanceEvent.query.filter_by(
            employee_id=emp_id, event_date=d, adherence_exception_id=code_id
        ).first()
        if not evt:
            evt = AttendanceEvent(employee_id=emp_id, event_date=d)
            db.session.add(evt)

    evt.adherence_exception_id = code_id
    evt.is_excused = data.get("is_excused", code.is_excused)
    evt.note = data.get("note", "")
    evt.coded_by = user.get("id")

    try:
        db.session.commit()
        return jsonify(success=True, event=evt.to_dict())
    except Exception as e:
        db.session.rollback()
        return jsonify(error=str(e)), 500


@attendance_bp.route("/api/events/delete", methods=["POST"])
@login_required
def delete_event():
    """Delete an attendance event."""
    user = get_current_user()
    if not user:
        return jsonify(error="Not authenticated"), 401
    if user.get("is_demo"):
        return jsonify(success=True, message="Demo mode")

    data = request.get_json(silent=True) or {}
    event_id = data.get("id")
    if not event_id:
        return jsonify(error="id required"), 400

    evt = AttendanceEvent.query.get(event_id)
    if not evt:
        return jsonify(error="Not found"), 404

    db.session.delete(evt)
    db.session.commit()
    return jsonify(success=True)


# ── API: bulk auto-detect + code from schedule ────────────────

@attendance_bp.route("/api/events/auto-detect", methods=["POST"])
@login_required
def auto_detect():
    """Auto-detect absences from schedule vs agent status for a date.
    Creates AttendanceEvent records for anyone scheduled but not logged in."""
    user = get_current_user()
    if not user:
        return jsonify(error="Not authenticated"), 401
    if user.get("is_demo"):
        return jsonify(success=True, created=0, message="Demo mode")

    data = request.get_json(silent=True) or {}
    date_str = data.get("date")
    if not date_str:
        return jsonify(error="date required"), 400

    try:
        d = date.fromisoformat(date_str)
    except (ValueError, TypeError):
        return jsonify(error="Invalid date"), 400

    # Don't auto-detect for future dates
    if d > date.today():
        return jsonify(error="Cannot auto-detect for future dates"), 400

    # Ensure codes exist
    seed_adherence_codes()
    abs_code = AdherenceException.query.filter_by(code="ABS", is_active=True).first()
    late_code = AdherenceException.query.filter_by(code="LATE", is_active=True).first()
    early_code = AdherenceException.query.filter_by(code="EOUT", is_active=True).first()
    if not abs_code:
        return jsonify(error="No absence code configured"), 500

    late_grace = int(data.get("late_grace_mins", 5))

    # Find all scheduled employees for this date
    schedules = Schedule.query.filter(
        Schedule.schedule_date == d,
        Schedule.status == "scheduled",
    ).all()

    # Get agent status events for this date
    from app.models import AgentStatusEvent, ShiftSegment
    day_start = datetime.combine(d, datetime.min.time())
    day_end = day_start + timedelta(days=1)

    # Build per-employee event list
    from collections import defaultdict
    events_by_emp = defaultdict(list)
    try:
        events = AgentStatusEvent.query.filter(
            AgentStatusEvent.start_ts >= day_start,
            AgentStatusEvent.start_ts < day_end,
        ).all()
        for evt in events:
            events_by_emp[evt.employee_id].append(evt)
    except Exception:
        pass

    # Also check TimeClock entries
    clock_by_emp = {}
    try:
        clocks = TimeClock.query.filter(TimeClock.date == d).all()
        for tc in clocks:
            if tc.employee_id not in clock_by_emp or (tc.clock_in and tc.clock_in < clock_by_emp[tc.employee_id].clock_in):
                clock_by_emp[tc.employee_id] = tc
    except Exception:
        pass

    created_abs = 0
    created_late = 0
    created_early = 0

    for sched in schedules:
        emp_id = sched.employee_id
        has_events = emp_id in events_by_emp
        has_clock = emp_id in clock_by_emp

        # Parse schedule start/end times (db.Time objects)
        try:
            if not sched.start_time or not sched.end_time:
                continue
            sh_start = datetime.combine(d, sched.start_time)
            sh_end = datetime.combine(d, sched.end_time)
            if sh_end <= sh_start:
                sh_end += timedelta(days=1)
            sh_start_str = sched.start_time.strftime("%H:%M")
            sh_end_str = sched.end_time.strftime("%H:%M")
        except Exception:
            continue

        if not has_events and not has_clock:
            # ── ABSENT: no login at all ──
            existing = AttendanceEvent.query.filter_by(
                employee_id=emp_id, event_date=d, adherence_exception_id=abs_code.id
            ).first()
            if not existing:
                db.session.add(AttendanceEvent(
                    employee_id=emp_id, event_date=d,
                    adherence_exception_id=abs_code.id, is_excused=False,
                    note=f"Auto: scheduled {sh_start_str}–{sh_end_str}, no login found",
                    coded_by=user.get("id"),
                ))
                created_abs += 1
            continue

        # Find first login time
        first_login = None
        if has_events:
            sorted_evts = sorted(events_by_emp[emp_id], key=lambda e: e.start_ts)
            first_login = sorted_evts[0].start_ts
        if has_clock:
            ci = clock_by_emp[emp_id].clock_in
            if ci and (first_login is None or ci < first_login):
                first_login = ci

        # ── LATE: first login well after shift start ──
        if late_code and first_login:
            mins_late = (first_login - sh_start).total_seconds() / 60
            if mins_late > late_grace:
                existing = AttendanceEvent.query.filter_by(
                    employee_id=emp_id, event_date=d, adherence_exception_id=late_code.id
                ).first()
                if not existing:
                    db.session.add(AttendanceEvent(
                        employee_id=emp_id, event_date=d,
                        adherence_exception_id=late_code.id, is_excused=False,
                        note=f"Auto: {int(mins_late)}m late (scheduled {sh_start_str}, first seen {first_login.strftime('%H:%M')})",
                        coded_by=user.get("id"),
                    ))
                    created_late += 1

        # ── EARLY OUT: last activity ended before shift end ──
        if early_code and has_events:
            sorted_evts = sorted(events_by_emp[emp_id], key=lambda e: e.start_ts)
            last_evt = sorted_evts[-1]
            last_seen = last_evt.end_ts or last_evt.start_ts
            if last_seen and last_seen < sh_end - timedelta(minutes=15):
                mins_early = (sh_end - last_seen).total_seconds() / 60
                existing = AttendanceEvent.query.filter_by(
                    employee_id=emp_id, event_date=d, adherence_exception_id=early_code.id
                ).first()
                if not existing:
                    db.session.add(AttendanceEvent(
                        employee_id=emp_id, event_date=d,
                        adherence_exception_id=early_code.id, is_excused=False,
                        note=f"Auto: left {int(mins_early)}m early (last seen {last_seen.strftime('%H:%M')}, scheduled to {sh_end_str})",
                        coded_by=user.get("id"),
                    ))
                    created_early += 1

    db.session.commit()
    total = created_abs + created_late + created_early
    return jsonify(success=True, created=total,
                   detail={"absent": created_abs, "late": created_late, "early_leave": created_early})
