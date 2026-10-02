"""
Agent Self-Service Portal — Phase 6
Routes for /my-schedule/, /my-time-off/, etc.
"""

from datetime import date, datetime, timedelta, timezone
from flask import Blueprint, render_template, request, jsonify, redirect, url_for
from app.auth import login_required, agent_required, get_current_user
from app.models import (
    db, Employee, Schedule, PTOEntry, TimeOffType,
    ShiftPost, ShiftBid, ShiftSwapRequest, VTOOTPost, VTOOTSignup,
    PlanningUnit,
)
from datetime import date as _date_type

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


def _demo_guard():
    user = get_current_user()
    if user and user.get("is_demo"):
        return jsonify(ok=True, demo=True, message="Changes are not saved in demo mode.")
    return None


def _is_demo():
    user = get_current_user()
    return user and user.get("is_demo")


# ───────────────────────────────────────────────────────
# Agent Portal (unified page)
# ───────────────────────────────────────────────────────

@agent_bp.route("/portal/")
@login_required
@agent_required
def portal():
    user = get_current_user()
    emp = _get_agent_employee(user)
    if not emp:
        return redirect("/")
    return render_template("agent/portal.html", employee=emp)


# ───────────────────────────────────────────────────────
# Shift Bidding (Phase 6.3)
# ───────────────────────────────────────────────────────

@agent_bp.route("/api/agent/open-shifts", methods=["POST"])
@login_required
@agent_required
def list_open_shifts():
    """Return open shift posts for agents to bid on."""
    if _is_demo():
        return jsonify(shifts=_demo_open_shifts())

    posts = (
        ShiftPost.query
        .filter_by(status="open")
        .filter(ShiftPost.schedule_date >= date.today())
        .order_by(ShiftPost.schedule_date)
        .all()
    )
    return jsonify(shifts=[p.to_dict() | {"bid_count": len(p.bids)} for p in posts])


@agent_bp.route("/api/agent/bid", methods=["POST"])
@login_required
@agent_required
def submit_bid():
    """Agent bids on an open shift."""
    dg = _demo_guard()
    if dg:
        return dg

    user = get_current_user()
    emp = _get_agent_employee(user)
    data = request.get_json(force=True)
    post_id = data.get("shift_post_id")
    post = ShiftPost.query.get(post_id)
    if not post or post.status != "open":
        return jsonify(error="Shift not available"), 400

    existing = ShiftBid.query.filter_by(shift_post_id=post_id, employee_id=emp.id).first()
    if existing:
        return jsonify(error="Already bid on this shift"), 400

    bid = ShiftBid(
        shift_post_id=post_id,
        employee_id=emp.id,
        preference=int(data.get("preference", 1)),
    )
    db.session.add(bid)
    db.session.commit()
    return jsonify(ok=True, id=bid.id)


@agent_bp.route("/api/agent/my-bids", methods=["POST"])
@login_required
@agent_required
def my_bids():
    """Return the agent's current bids."""
    if _is_demo():
        return jsonify(bids=[])
    user = get_current_user()
    emp = _get_agent_employee(user)
    bids = ShiftBid.query.filter_by(employee_id=emp.id).all()
    results = []
    for b in bids:
        p = b.shift_post
        results.append({
            "id": b.id,
            "shift_post_id": b.shift_post_id,
            "date": p.schedule_date.isoformat() if p else "",
            "start": p.shift_start.strftime("%H:%M") if p else "",
            "end": p.shift_end.strftime("%H:%M") if p else "",
            "preference": b.preference,
            "status": b.status,
        })
    return jsonify(bids=results)


@agent_bp.route("/api/agent/bid/<int:bid_id>", methods=["DELETE"])
@login_required
@agent_required
def cancel_bid(bid_id):
    dg = _demo_guard()
    if dg:
        return dg
    user = get_current_user()
    emp = _get_agent_employee(user)
    bid = ShiftBid.query.get(bid_id)
    if not bid or bid.employee_id != emp.id or bid.status != "pending":
        return jsonify(error="Cannot cancel"), 400
    db.session.delete(bid)
    db.session.commit()
    return jsonify(ok=True)


# ───────────────────────────────────────────────────────
# Shift Swaps (Phase 6.4)
# ───────────────────────────────────────────────────────

@agent_bp.route("/api/agent/swap-request", methods=["POST"])
@login_required
@agent_required
def submit_swap():
    """Agent requests to swap one of their shifts."""
    dg = _demo_guard()
    if dg:
        return dg

    user = get_current_user()
    emp = _get_agent_employee(user)
    data = request.get_json(force=True)

    my_sched_id = data.get("my_schedule_id")
    my_sched = Schedule.query.get(my_sched_id)
    if not my_sched or my_sched.employee_id != emp.id:
        return jsonify(error="Schedule not found"), 400

    swap = ShiftSwapRequest(
        requester_id=emp.id,
        requester_schedule_id=my_sched_id,
        target_id=data.get("target_employee_id"),
        target_schedule_id=data.get("target_schedule_id"),
        reason=data.get("reason", ""),
    )
    db.session.add(swap)
    db.session.commit()
    return jsonify(ok=True, id=swap.id)


@agent_bp.route("/api/agent/swaps", methods=["POST"])
@login_required
@agent_required
def list_swaps():
    """Return swap requests relevant to this agent."""
    if _is_demo():
        return jsonify(swaps=_demo_swaps())

    user = get_current_user()
    emp = _get_agent_employee(user)
    swaps = (
        ShiftSwapRequest.query
        .filter(
            db.or_(
                ShiftSwapRequest.requester_id == emp.id,
                ShiftSwapRequest.target_id == emp.id,
                ShiftSwapRequest.target_id.is_(None),  # open offers
            )
        )
        .order_by(ShiftSwapRequest.created_at.desc())
        .limit(50)
        .all()
    )
    return jsonify(swaps=[s.to_dict() for s in swaps])


@agent_bp.route("/api/agent/swap/<int:swap_id>/accept", methods=["POST"])
@login_required
@agent_required
def accept_swap(swap_id):
    """Target agent accepts a swap request (still needs manager approval)."""
    dg = _demo_guard()
    if dg:
        return dg

    user = get_current_user()
    emp = _get_agent_employee(user)
    swap = ShiftSwapRequest.query.get(swap_id)
    if not swap or swap.status != "pending":
        return jsonify(error="Swap not available"), 400
    if swap.requester_id == emp.id:
        return jsonify(error="Cannot accept your own swap"), 400

    data = request.get_json(force=True) or {}
    my_sched_id = data.get("my_schedule_id")
    if my_sched_id:
        my_sched = Schedule.query.get(my_sched_id)
        if not my_sched or my_sched.employee_id != emp.id:
            return jsonify(error="Schedule not found"), 400
        swap.target_schedule_id = my_sched_id

    swap.target_id = emp.id
    swap.status = "accepted"  # awaiting manager approval
    db.session.commit()
    return jsonify(ok=True)


@agent_bp.route("/api/agent/swap/<int:swap_id>/cancel", methods=["POST"])
@login_required
@agent_required
def cancel_swap(swap_id):
    dg = _demo_guard()
    if dg:
        return dg
    user = get_current_user()
    emp = _get_agent_employee(user)
    swap = ShiftSwapRequest.query.get(swap_id)
    if not swap or swap.requester_id != emp.id or swap.status not in ("pending",):
        return jsonify(error="Cannot cancel"), 400
    swap.status = "cancelled"
    db.session.commit()
    return jsonify(ok=True)


# ───────────────────────────────────────────────────────
# VTO / OT Sign-Up Board (Phase 6.5)
# ───────────────────────────────────────────────────────

@agent_bp.route("/api/agent/vto-ot", methods=["POST"])
@login_required
@agent_required
def list_vto_ot():
    """Return open VTO/OT opportunities."""
    if _is_demo():
        return jsonify(posts=_demo_vto_ot())

    posts = (
        VTOOTPost.query
        .filter_by(status="open")
        .filter(VTOOTPost.schedule_date >= date.today())
        .order_by(VTOOTPost.schedule_date)
        .all()
    )
    results = []
    for p in posts:
        d = p.to_dict()
        d["signed_up"] = False
        results.append(d)
    return jsonify(posts=results)


@agent_bp.route("/api/agent/vto-ot/signup", methods=["POST"])
@login_required
@agent_required
def signup_vto_ot():
    """Agent signs up for a VTO/OT opportunity."""
    dg = _demo_guard()
    if dg:
        return dg

    user = get_current_user()
    emp = _get_agent_employee(user)
    data = request.get_json(force=True)
    post_id = data.get("post_id")
    post = VTOOTPost.query.get(post_id)
    if not post or post.status != "open":
        return jsonify(error="Not available"), 400

    existing = VTOOTSignup.query.filter_by(post_id=post_id, employee_id=emp.id).first()
    if existing:
        return jsonify(error="Already signed up"), 400

    if post.slots_filled >= post.slots:
        return jsonify(error="No slots remaining"), 400

    signup = VTOOTSignup(post_id=post_id, employee_id=emp.id)
    db.session.add(signup)
    post.slots_filled += 1
    if post.slots_filled >= post.slots:
        post.status = "filled"
    db.session.commit()
    return jsonify(ok=True, id=signup.id)


@agent_bp.route("/api/agent/vto-ot/cancel", methods=["POST"])
@login_required
@agent_required
def cancel_vto_ot():
    dg = _demo_guard()
    if dg:
        return dg
    user = get_current_user()
    emp = _get_agent_employee(user)
    data = request.get_json(force=True)
    post_id = data.get("post_id")
    signup = VTOOTSignup.query.filter_by(post_id=post_id, employee_id=emp.id).first()
    if not signup:
        return jsonify(error="Not found"), 404
    post = signup.post
    db.session.delete(signup)
    if post:
        post.slots_filled = max(0, post.slots_filled - 1)
        if post.status == "filled":
            post.status = "open"
    db.session.commit()
    return jsonify(ok=True)


# ───────────────────────────────────────────────────────
# My upcoming schedules (for swap picker)
# ───────────────────────────────────────────────────────

@agent_bp.route("/api/agent/upcoming-shifts", methods=["POST"])
@login_required
@agent_required
def upcoming_shifts():
    """Return agent's shifts in the next 4 weeks for swap selection."""
    if _is_demo():
        return jsonify(shifts=_demo_upcoming_shifts())

    user = get_current_user()
    emp = _get_agent_employee(user)
    today = date.today()
    end = today + timedelta(days=28)
    schedules = (
        Schedule.query
        .filter_by(employee_id=emp.id, status="scheduled")
        .filter(Schedule.schedule_date >= today)
        .filter(Schedule.schedule_date <= end)
        .order_by(Schedule.schedule_date)
        .all()
    )
    return jsonify(shifts=[{
        "schedule_id": s.id,
        "date": s.schedule_date.isoformat(),
        "day": s.schedule_date.strftime("%a"),
        "start": s.shift_start.strftime("%H:%M") if s.shift_start else "",
        "end": s.shift_end.strftime("%H:%M") if s.shift_end else "",
        "hours": s.hours,
    } for s in schedules])


# ───────────────────────────────────────────────────────
# Demo data helpers
# ───────────────────────────────────────────────────────

def _demo_open_shifts():
    from datetime import time as _time
    today = date.today()
    return [
        {"id": 1, "date": (today + timedelta(days=2)).isoformat(), "start": "08:00", "end": "16:30",
         "hours": 8.5, "lob": "Sales Support", "required_skills": "", "status": "open",
         "assigned_to": None, "notes": "Coverage needed — PTO", "bid_count": 2, "created_at": today.isoformat()},
        {"id": 2, "date": (today + timedelta(days=3)).isoformat(), "start": "12:00", "end": "20:30",
         "hours": 8.5, "lob": "Tech Help Desk", "required_skills": "Tier 2", "status": "open",
         "assigned_to": None, "notes": "", "bid_count": 0, "created_at": today.isoformat()},
        {"id": 3, "date": (today + timedelta(days=5)).isoformat(), "start": "06:00", "end": "14:30",
         "hours": 8.5, "lob": "Billing", "required_skills": "", "status": "open",
         "assigned_to": None, "notes": "Early shift", "bid_count": 1, "created_at": today.isoformat()},
    ]


def _demo_swaps():
    today = date.today()
    return [
        {"id": 1, "requester": "Sarah Kim", "requester_date": (today + timedelta(days=1)).isoformat(),
         "requester_shift": "08:00-16:30", "target": "Open", "target_date": "", "target_shift": "",
         "status": "pending", "reason": "Doctor appointment", "created_at": today.isoformat()},
        {"id": 2, "requester": "James Porter", "requester_date": (today + timedelta(days=4)).isoformat(),
         "requester_shift": "12:00-20:30", "target": "Maria Lopez", "target_date": (today + timedelta(days=4)).isoformat(),
         "target_shift": "08:00-16:30", "status": "accepted", "reason": "Childcare schedule change",
         "created_at": (today - timedelta(days=1)).isoformat()},
    ]


def _demo_vto_ot():
    today = date.today()
    return [
        {"id": 1, "type": "vto", "date": (today + timedelta(days=1)).isoformat(), "start": "14:00", "end": "20:30",
         "hours": 6.5, "slots": 3, "slots_filled": 1, "lob": "Sales Support", "status": "open",
         "notes": "Low volume expected", "signed_up": False},
        {"id": 2, "type": "ot", "date": (today + timedelta(days=2)).isoformat(), "start": "06:00", "end": "14:30",
         "hours": 8.5, "slots": 2, "slots_filled": 0, "lob": "Tech Help Desk", "status": "open",
         "notes": "Product launch — extra coverage needed", "signed_up": False},
        {"id": 3, "type": "ot", "date": (today + timedelta(days=3)).isoformat(), "start": "16:00", "end": "22:00",
         "hours": 6.0, "slots": 4, "slots_filled": 2, "lob": "Billing", "status": "open",
         "notes": "Month-end billing surge", "signed_up": False},
        {"id": 4, "type": "vto", "date": (today + timedelta(days=5)).isoformat(), "start": "08:00", "end": "16:30",
         "hours": 8.5, "slots": 2, "slots_filled": 0, "lob": "Sales Support", "status": "open",
         "notes": "", "signed_up": False},
    ]


def _demo_upcoming_shifts():
    today = date.today()
    shifts = []
    for i in range(1, 15):
        d = today + timedelta(days=i)
        if d.weekday() < 5:  # weekdays
            shifts.append({
                "schedule_id": 1000 + i,
                "date": d.isoformat(),
                "day": d.strftime("%a"),
                "start": "08:00" if i % 2 == 0 else "12:00",
                "end": "16:30" if i % 2 == 0 else "20:30",
                "hours": 8.5,
            })
    return shifts
