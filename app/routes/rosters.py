"""
routes/rosters.py — Roster Rotation blueprint
===============================================
CRUD for roster groups, members, slots, and roster generation/publish.
"""

import json
import logging
import datetime

from flask import Blueprint, render_template, request, jsonify
from app.auth import login_required, admin_required, get_current_user
from app.models import (db, RosterGroup, RosterMember, RosterSlot,
                         RosterRotationState, Employee, PlanningUnit, Schedule)

log = logging.getLogger("serevo.rosters")

rosters_bp = Blueprint("rosters", __name__, url_prefix="/scheduling/rosters")


# ── Page ─────────────────────────────────────────────────────

@rosters_bp.route("/")
@rosters_bp.route("")
@login_required
def index():
    user = get_current_user()
    return render_template("rosters.html", user=user)


# ── Planning Units (for dropdowns) ───────────────────────────

@rosters_bp.route("/api/planning-units")
@login_required
def api_planning_units():
    pus = PlanningUnit.query.filter_by(is_active=True).order_by(PlanningUnit.name).all()
    return jsonify([{"id": p.id, "name": p.name} for p in pus])


# ── Employees (for member picker) ────────────────────────────

@rosters_bp.route("/api/employees")
@login_required
def api_employees():
    pu_id = request.args.get("planning_unit_id", type=int)
    q = Employee.query.filter_by(active=True)
    if pu_id:
        q = q.filter_by(planning_unit_id=pu_id)
    emps = q.order_by(Employee.last_name, Employee.first_name).all()
    return jsonify([{"id": e.id, "employee_id": e.employee_id,
                     "name": e.full_name} for e in emps])


# ── Groups CRUD ──────────────────────────────────────────────

@rosters_bp.route("/api/groups")
@login_required
def api_groups():
    groups = RosterGroup.query.filter_by(active=True).order_by(RosterGroup.name).all()
    return jsonify([g.to_dict() for g in groups])


@rosters_bp.route("/api/groups", methods=["POST"])
@login_required
def api_create_group():
    d = request.get_json(force=True)
    g = RosterGroup(
        planning_unit_id=d["planning_unit_id"],
        name=d["name"],
        closers_required_weekday=d.get("closers_required_weekday", 1),
        closers_required_weekend=d.get("closers_required_weekend", 1),
        weekend_mode=d.get("weekend_mode", "rotate"),
        stat_role=d.get("stat_role"),
    )
    db.session.add(g)
    db.session.commit()
    return jsonify(g.to_dict()), 201


@rosters_bp.route("/api/groups/<int:gid>", methods=["PUT"])
@login_required
def api_update_group(gid):
    g = RosterGroup.query.get_or_404(gid)
    d = request.get_json(force=True)
    for k in ("name", "planning_unit_id", "closers_required_weekday",
              "closers_required_weekend", "weekend_mode", "stat_role"):
        if k in d:
            setattr(g, k, d[k])
    db.session.commit()
    return jsonify(g.to_dict())


@rosters_bp.route("/api/groups/<int:gid>", methods=["DELETE"])
@login_required
def api_delete_group(gid):
    g = RosterGroup.query.get_or_404(gid)
    g.active = False
    db.session.commit()
    return jsonify({"ok": True})


# ── Members CRUD ─────────────────────────────────────────────

@rosters_bp.route("/api/groups/<int:gid>/members", methods=["POST"])
@login_required
def api_add_member(gid):
    g = RosterGroup.query.get_or_404(gid)
    d = request.get_json(force=True)
    m = RosterMember(
        roster_group_id=g.id,
        employee_id=d["employee_id"],
        is_display_only=d.get("is_display_only", False),
        is_weekend_primary=d.get("is_weekend_primary", False),
        weekend_primary_days=json.dumps(d["weekend_primary_days"]) if d.get("weekend_primary_days") else None,
        max_closings_per_week=d.get("max_closings_per_week", 1),
        excluded_closing_days=json.dumps(d["excluded_closing_days"]) if d.get("excluded_closing_days") else None,
        static_days_json=json.dumps(d["static_days_json"]) if d.get("static_days_json") else None,
        accommodation_start=_parse_time(d.get("accommodation_start")),
        accommodation_end=_parse_time(d.get("accommodation_end")),
        accommodation_no_close=d.get("accommodation_no_close", False),
        work_days=json.dumps(d["work_days"]) if d.get("work_days") else None,
    )
    db.session.add(m)
    db.session.commit()
    return jsonify(m.to_dict()), 201


@rosters_bp.route("/api/members/<int:mid>", methods=["PUT"])
@login_required
def api_update_member(mid):
    m = RosterMember.query.get_or_404(mid)
    d = request.get_json(force=True)
    for k in ("is_display_only", "is_weekend_primary", "max_closings_per_week",
              "accommodation_no_close"):
        if k in d:
            setattr(m, k, d[k])
    for k in ("weekend_primary_days", "excluded_closing_days", "static_days_json", "work_days"):
        if k in d:
            setattr(m, k, json.dumps(d[k]) if d[k] else None)
    if "accommodation_start" in d:
        m.accommodation_start = _parse_time(d["accommodation_start"])
    if "accommodation_end" in d:
        m.accommodation_end = _parse_time(d["accommodation_end"])
    db.session.commit()
    return jsonify(m.to_dict())


@rosters_bp.route("/api/members/<int:mid>", methods=["DELETE"])
@login_required
def api_delete_member(mid):
    m = RosterMember.query.get_or_404(mid)
    m.active = False
    db.session.commit()
    return jsonify({"ok": True})


# ── Slots CRUD ───────────────────────────────────────────────

@rosters_bp.route("/api/groups/<int:gid>/slots", methods=["POST"])
@login_required
def api_add_slot(gid):
    g = RosterGroup.query.get_or_404(gid)
    d = request.get_json(force=True)
    s = RosterSlot(
        roster_group_id=g.id,
        day_type=d["day_type"],
        role=d["role"],
        shift_start=_parse_time(d["shift_start"]),
        shift_end=_parse_time(d["shift_end"]),
    )
    db.session.add(s)
    db.session.commit()
    return jsonify(s.to_dict()), 201


@rosters_bp.route("/api/slots/<int:sid>", methods=["PUT"])
@login_required
def api_update_slot(sid):
    s = RosterSlot.query.get_or_404(sid)
    d = request.get_json(force=True)
    if "day_type" in d:
        s.day_type = d["day_type"]
    if "role" in d:
        s.role = d["role"]
    if "shift_start" in d:
        s.shift_start = _parse_time(d["shift_start"])
    if "shift_end" in d:
        s.shift_end = _parse_time(d["shift_end"])
    db.session.commit()
    return jsonify(s.to_dict())


@rosters_bp.route("/api/slots/<int:sid>", methods=["DELETE"])
@login_required
def api_delete_slot(sid):
    s = RosterSlot.query.get_or_404(sid)
    db.session.delete(s)
    db.session.commit()
    return jsonify({"ok": True})


# ── Bulk slot save (save all slots for a group at once) ──────

@rosters_bp.route("/api/groups/<int:gid>/slots/bulk", methods=["POST"])
@login_required
def api_bulk_save_slots(gid):
    g = RosterGroup.query.get_or_404(gid)
    data = request.get_json(force=True)
    slots = data.get("slots", [])
    # Remove existing slots for this group
    RosterSlot.query.filter_by(roster_group_id=g.id).delete()
    for s in slots:
        db.session.add(RosterSlot(
            roster_group_id=g.id,
            day_type=s["day_type"],
            role=s["role"],
            shift_start=_parse_time(s["shift_start"]),
            shift_end=_parse_time(s["shift_end"]),
        ))
    db.session.commit()
    return jsonify({"ok": True, "count": len(slots)})


# ── Helpers ──────────────────────────────────────────────────

def _parse_time(v):
    if not v:
        return None
    if isinstance(v, datetime.time):
        return v
    try:
        parts = v.split(":")
        return datetime.time(int(parts[0]), int(parts[1]))
    except (ValueError, AttributeError, IndexError):
        return None
