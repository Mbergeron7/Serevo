"""
Agent Self-Service Portal — Phase 6
Routes for /my-schedule/, /my-time-off/, etc.
"""

from datetime import date, datetime, timedelta, timezone
from flask import Blueprint, render_template, request, jsonify, redirect, url_for
from app.auth import login_required, agent_required, get_current_user
from app.models import db, Employee, Schedule, PTOEntry, TimeOffType

agent_bp = Blueprint("agent", __name__)


def _get_agent_employee(user):
    """Resolve the Employee record for the logged-in agent."""
    if not user or not user.get("employee_id"):
        return None
    return Employee.query.get(user["employee_id"])


# ───────────────────────────────────────────────────────
# My Schedule
# ───────────────────────────────────────────────────────

@agent_bp.route("/my-schedule/")
@login_required
@agent_required
def my_schedule():
    user = get_current_user()
    emp = _get_agent_employee(user)
    if not emp:
        return redirect("/")

    # Default to current week (Mon–Sun)
    today = date.today()
    start_str = request.args.get("start")
    if start_str:
        try:
            week_start = date.fromisoformat(start_str)
        except ValueError:
            week_start = today - timedelta(days=today.weekday())
    else:
        week_start = today - timedelta(days=today.weekday())

    week_end = week_start + timedelta(days=6)

    schedules = (
        Schedule.query
        .filter_by(employee_id=emp.id)
        .filter(Schedule.schedule_date >= week_start)
        .filter(Schedule.schedule_date <= week_end)
        .order_by(Schedule.schedule_date, Schedule.shift_start)
        .all()
    )

    # Build day-by-day data
    days = []
    for i in range(7):
        d = week_start + timedelta(days=i)
        day_schedules = [s for s in schedules if s.schedule_date == d]
        days.append({
            "date": d,
            "day_name": d.strftime("%A"),
            "short": d.strftime("%b %d"),
            "is_today": d == today,
            "schedules": day_schedules,
        })

    prev_week = (week_start - timedelta(days=7)).isoformat()
    next_week = (week_start + timedelta(days=7)).isoformat()

    return render_template(
        "agent/my_schedule.html",
        employee=emp,
        days=days,
        week_start=week_start,
        week_end=week_end,
        prev_week=prev_week,
        next_week=next_week,
        today=today,
    )


# ───────────────────────────────────────────────────────
# My Time Off
# ───────────────────────────────────────────────────────

@agent_bp.route("/my-time-off/")
@login_required
@agent_required
def my_time_off():
    user = get_current_user()
    emp = _get_agent_employee(user)
    if not emp:
        return redirect("/")

    # Get time-off types for the dropdown
    try:
        types = TimeOffType.query.filter_by(is_active=True).order_by(TimeOffType.sort_order).all()
    except Exception:
        types = []

    # Get this employee's PTO entries, most recent first
    entries = (
        PTOEntry.query
        .filter_by(employee_id=emp.id)
        .order_by(PTOEntry.start_date.desc())
        .all()
    )

    # Split into upcoming and past
    today = date.today()
    upcoming = [e for e in entries if e.end_date >= today]
    past = [e for e in entries if e.end_date < today]

    return render_template(
        "agent/my_time_off.html",
        employee=emp,
        types=types,
        upcoming=upcoming,
        past=past,
        today=today,
    )


@agent_bp.route("/api/agent/time-off", methods=["POST"])
@login_required
@agent_required
def submit_time_off():
    """Agent submits a new time-off request."""
    user = get_current_user()
    emp = _get_agent_employee(user)
    if not emp:
        return jsonify({"error": "Employee not found"}), 404

    data = request.get_json(force=True)
    try:
        start = date.fromisoformat(data["start_date"])
        end = date.fromisoformat(data["end_date"])
    except (KeyError, ValueError):
        return jsonify({"error": "Invalid dates"}), 400

    if end < start:
        return jsonify({"error": "End date cannot be before start date"}), 400

    type_id = data.get("time_off_type_id")
    pto_type = data.get("pto_type", "full")
    note = data.get("note", "")

    entry = PTOEntry(
        employee_id=emp.id,
        start_date=start,
        end_date=end,
        pto_type=pto_type,
        time_off_type_id=int(type_id) if type_id else None,
        approval_status="pending",
        requested_by=user["id"],
        note=note,
    )
    db.session.add(entry)
    db.session.commit()

    return jsonify({"ok": True, "id": entry.id, "status": "pending"})


@agent_bp.route("/api/agent/time-off/<int:entry_id>", methods=["DELETE"])
@login_required
@agent_required
def cancel_time_off(entry_id):
    """Agent cancels a pending time-off request."""
    user = get_current_user()
    emp = _get_agent_employee(user)
    if not emp:
        return jsonify({"error": "Employee not found"}), 404

    entry = PTOEntry.query.get(entry_id)
    if not entry or entry.employee_id != emp.id:
        return jsonify({"error": "Not found"}), 404

    # Can only cancel pending requests
    approval = getattr(entry, "approval_status", "approved")
    if approval not in ("pending",):
        return jsonify({"error": "Can only cancel pending requests"}), 400

    db.session.delete(entry)
    db.session.commit()
    return jsonify({"ok": True})


# ───────────────────────────────────────────────────────
# API: My schedule data (JSON for potential future use)
# ───────────────────────────────────────────────────────

@agent_bp.route("/api/agent/schedule")
@login_required
@agent_required
def api_my_schedule():
    user = get_current_user()
    emp = _get_agent_employee(user)
    if not emp:
        return jsonify({"error": "Employee not found"}), 404

    start_str = request.args.get("start", date.today().isoformat())
    end_str = request.args.get("end")
    try:
        start = date.fromisoformat(start_str)
    except ValueError:
        start = date.today()
    if end_str:
        try:
            end = date.fromisoformat(end_str)
        except ValueError:
            end = start + timedelta(days=6)
    else:
        end = start + timedelta(days=6)

    schedules = (
        Schedule.query
        .filter_by(employee_id=emp.id)
        .filter(Schedule.schedule_date >= start)
        .filter(Schedule.schedule_date <= end)
        .order_by(Schedule.schedule_date, Schedule.shift_start)
        .all()
    )

    return jsonify({
        "employee": emp.full_name,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "schedules": [s.to_dict() for s in schedules],
    })
