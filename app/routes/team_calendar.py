"""
Team Calendar — weekly roster grid view for supervisors.

Shows all employees in a planning unit across a week with color-coded
shift blocks, PTO markers, and daily headcount summaries.
"""
import logging
from datetime import date, timedelta, datetime

from flask import Blueprint, render_template, request, jsonify
from app.auth import login_required, get_current_user
from app.models import (
    db, Employee, Schedule, PTOEntry, PlanningUnit, ShiftSegment,
)

log = logging.getLogger("serevo.team_calendar")
team_cal_bp = Blueprint("team_calendar", __name__, url_prefix="/team-calendar")


@team_cal_bp.route("/")
@login_required
def index():
    user = get_current_user()
    if not user or user.get("role") not in ("admin", "supervisor"):
        return jsonify(error="Forbidden"), 403
    lobs = [pu.name for pu in PlanningUnit.query.filter_by(is_active=True).order_by(PlanningUnit.name).all()]
    return render_template("team_calendar/index.html", user=user, lobs=lobs)


@team_cal_bp.route("/api/data", methods=["POST"])
@login_required
def api_data():
    user = get_current_user()
    if not user or user.get("role") not in ("admin", "supervisor"):
        return jsonify(error="Forbidden"), 403

    if user.get("is_demo"):
        from app.demo_data import get_demo_team_calendar
        payload = request.get_json(silent=True) or {}
        ws = payload.get("week_start")
        try:
            week_start = datetime.strptime(ws, "%Y-%m-%d").date() if ws else None
        except ValueError:
            week_start = None
        data = get_demo_team_calendar(week_start, payload.get("lob", ""))
        return jsonify(success=True, **data)

    payload = request.get_json(silent=True) or {}
    lob = payload.get("lob", "")
    week_start_str = payload.get("week_start")

    try:
        if week_start_str:
            week_start = datetime.strptime(week_start_str, "%Y-%m-%d").date()
        else:
            today = date.today()
            week_start = today - timedelta(days=today.weekday())  # Monday
    except ValueError:
        return jsonify(success=False, error="Invalid date")

    week_end = week_start + timedelta(days=6)
    dates = [week_start + timedelta(days=i) for i in range(7)]

    # Get employees for this LOB
    emp_q = Employee.query.filter_by(status="Active").order_by(Employee.last_name, Employee.first_name)
    if lob:
        pu = PlanningUnit.query.filter_by(name=lob).first()
        if pu:
            emp_q = emp_q.filter_by(planning_unit_id=pu.id)

    employees = emp_q.all()

    # Get schedules for the week
    sched_q = Schedule.query.filter(
        Schedule.schedule_date >= week_start,
        Schedule.schedule_date <= week_end,
    )
    if lob:
        pu = PlanningUnit.query.filter_by(name=lob).first()
        if pu:
            sched_q = sched_q.filter_by(planning_unit_id=pu.id)

    schedules = sched_q.all()
    sched_map = {}  # (emp_id, date_str) -> schedule info
    for s in schedules:
        key = (s.employee_id, s.schedule_date.isoformat())
        sched_map[key] = {
            "start": s.shift_start.strftime("%H:%M") if s.shift_start else "",
            "end": s.shift_end.strftime("%H:%M") if s.shift_end else "",
            "hours": s.hours,
            "type": s.shift_type or "full",
            "status": s.status,
        }

    # Get PTO for the week
    ptos = PTOEntry.query.filter(
        PTOEntry.start_date <= week_end,
        PTOEntry.end_date >= week_start,
        PTOEntry.approval_status == "approved",
    ).all()
    pto_map = {}  # (emp_id, date_str) -> True
    for p in ptos:
        d = max(p.start_date, week_start)
        while d <= min(p.end_date, week_end):
            pto_map[(p.employee_id, d.isoformat())] = True
            d += timedelta(days=1)

    # Build grid
    rows = []
    daily_counts = {d.isoformat(): {"scheduled": 0, "off": 0, "pto": 0} for d in dates}

    for emp in employees:
        cells = []
        for d in dates:
            ds = d.isoformat()
            key = (emp.id, ds)
            if key in pto_map:
                cells.append({"status": "pto", "label": "PTO"})
                daily_counts[ds]["pto"] += 1
            elif key in sched_map:
                s = sched_map[key]
                if s["status"] == "off":
                    cells.append({"status": "off", "label": "OFF"})
                    daily_counts[ds]["off"] += 1
                else:
                    label = f"{s['start']}–{s['end']}" if s["start"] and s["end"] else f"{s['hours']}h"
                    cells.append({"status": "scheduled", "label": label, "hours": s["hours"], "type": s["type"]})
                    daily_counts[ds]["scheduled"] += 1
            else:
                cells.append({"status": "none", "label": ""})
                daily_counts[ds]["off"] += 1

        rows.append({
            "id": emp.id,
            "name": emp.full_name,
            "employee_id": emp.employee_id,
            "cells": cells,
        })

    return jsonify(
        success=True,
        dates=[d.isoformat() for d in dates],
        day_labels=[d.strftime("%a %b %d") for d in dates],
        rows=rows,
        daily_counts=daily_counts,
        total_employees=len(employees),
    )
