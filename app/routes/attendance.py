"""
Attendance — clock-in/out for agents, attendance dashboard for supervisors.
"""
import logging
from datetime import date, datetime, timedelta, timezone

from flask import Blueprint, render_template, request, jsonify
from app.auth import login_required, get_current_user
from sqlalchemy import func
from app.models import db, Employee, TimeClock, Schedule

log = logging.getLogger("serevo.attendance")
attendance_bp = Blueprint("attendance", __name__, url_prefix="/attendance")


def _utcnow():
    return datetime.now(timezone.utc)


# ── Agent: clock in / out ─────────────────────────────────────────────

@attendance_bp.route("/api/clock-in", methods=["POST"])
@login_required
def clock_in():
    """Agent clocks in."""
    user = get_current_user()
    if not user:
        return jsonify(error="Forbidden"), 403

    emp = Employee.query.filter_by(email=user.get("email"), status="Active").first()
    if not emp:
        return jsonify(success=False, error="No active employee record found for your account.")

    # Check if already clocked in
    active = TimeClock.query.filter_by(employee_id=emp.id, status="active").first()
    if active:
        return jsonify(success=False, error="You are already clocked in.",
                       clock_in=active.clock_in.isoformat())

    payload = request.get_json(silent=True) or {}
    now = _utcnow()
    entry = TimeClock(
        employee_id=emp.id,
        clock_in=now,
        clock_in_note=payload.get("note", ""),
        date=now.date(),
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

    emp = Employee.query.filter_by(email=user.get("email"), status="Active").first()
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

    emp = Employee.query.filter_by(email=user.get("email"), status="Active").first()
    if not emp:
        return jsonify(success=True, clocked_in=False, employee_found=False)

    active = TimeClock.query.filter_by(employee_id=emp.id, status="active").first()
    if active:
        ci = active.clock_in if active.clock_in.tzinfo else active.clock_in.replace(tzinfo=timezone.utc)
        elapsed = (_utcnow() - ci).total_seconds() / 3600
        return jsonify(success=True, clocked_in=True,
                       clock_in=active.clock_in.isoformat(),
                       elapsed_hours=round(elapsed, 2))
    return jsonify(success=True, clocked_in=False)


@attendance_bp.route("/api/history", methods=["POST"])
@login_required
def clock_history():
    """Get clock history for the logged-in agent."""
    user = get_current_user()
    if not user:
        return jsonify(error="Forbidden"), 403

    emp = Employee.query.filter_by(email=user.get("email"), status="Active").first()
    if not emp:
        return jsonify(success=True, entries=[])

    payload = request.get_json(silent=True) or {}
    days = payload.get("days", 14)
    since = date.today() - timedelta(days=days)

    entries = TimeClock.query.filter(
        TimeClock.employee_id == emp.id,
        TimeClock.date >= since,
    ).order_by(TimeClock.clock_in.desc()).limit(50).all()

    return jsonify(success=True, entries=[{
        "id": e.id,
        "date": e.date.isoformat(),
        "clock_in": e.clock_in.isoformat() if e.clock_in else None,
        "clock_out": e.clock_out.isoformat() if e.clock_out else None,
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
