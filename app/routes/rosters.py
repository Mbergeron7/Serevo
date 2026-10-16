"""
routes/rosters.py — Roster Rotation blueprint
===============================================
CRUD for roster groups, members, slots, and roster generation/publish.
"""

import csv
import io
import json
import logging
import datetime

from flask import Blueprint, render_template, request, jsonify, Response
from app.auth import login_required, admin_required, get_current_user
from app.models import (db, RosterGroup, RosterMember, RosterSlot,
                         RosterRotationState, Employee, PlanningUnit, Schedule)
from app.scheduling.roster_engine import (
    generate_roster, publish_roster, clear_roster_drafts, get_roster_calendar,
)

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
    q = Employee.query.filter_by(status="Active")
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
        sunday_non_closer=d.get("sunday_non_closer", False),
        weekend_close_week_parity=d.get("weekend_close_week_parity") or None,
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
              "closers_required_weekend", "weekend_mode", "stat_role",
              "sunday_non_closer", "weekend_close_week_parity"):
        if k in d:
            val = d[k]
            if k == "weekend_close_week_parity":
                val = val or None  # empty string → null
            setattr(g, k, val)
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
        is_fill_in=d.get("is_fill_in", False),
        weekend_primary_days=json.dumps(d["weekend_primary_days"]) if d.get("weekend_primary_days") else None,
        max_closings_per_week=d.get("max_closings_per_week", 1),
        excluded_closing_days=json.dumps(d["excluded_closing_days"]) if d.get("excluded_closing_days") else None,
        static_days_json=json.dumps(d["static_days_json"]) if d.get("static_days_json") else None,
        accommodation_start=_parse_time(d.get("accommodation_start")),
        accommodation_end=_parse_time(d.get("accommodation_end")),
        accommodation_no_close=d.get("accommodation_no_close", False),
        work_days=json.dumps(d["work_days"]) if d.get("work_days") else None,
        rotation_order=d.get("rotation_order", 0),
    )
    db.session.add(m)
    db.session.commit()
    return jsonify(m.to_dict()), 201


@rosters_bp.route("/api/members/<int:mid>", methods=["PUT"])
@login_required
def api_update_member(mid):
    m = RosterMember.query.get_or_404(mid)
    d = request.get_json(force=True)
    for k in ("is_display_only", "is_weekend_primary", "is_fill_in",
              "max_closings_per_week", "accommodation_no_close", "rotation_order"):
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
        label=d.get("label"),
        shift_start=_parse_time(d["shift_start"]),
        shift_end=_parse_time(d["shift_end"]),
        sort_order=d.get("sort_order", 0),
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
    if "label" in d:
        s.label = d["label"]
    if "shift_start" in d:
        s.shift_start = _parse_time(d["shift_start"])
    if "shift_end" in d:
        s.shift_end = _parse_time(d["shift_end"])
    if "sort_order" in d:
        s.sort_order = d.get("sort_order", 0)
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
            label=s.get("label"),
            shift_start=_parse_time(s["shift_start"]),
            shift_end=_parse_time(s["shift_end"]),
            sort_order=s.get("sort_order", 0),
        ))
    db.session.commit()
    return jsonify({"ok": True, "count": len(slots)})


# ── Generation / Publish / Clear ─────────────────────────────

@rosters_bp.route("/api/generate", methods=["POST"])
@login_required
def api_generate():
    d = request.get_json(force=True)
    group_id = d.get("group_id")
    start = _parse_date(d.get("start_date"))
    end = _parse_date(d.get("end_date"))
    if not group_id or not start or not end:
        return jsonify({"error": "group_id, start_date, and end_date required"}), 400
    if end < start:
        return jsonify({"error": "end_date must be >= start_date"}), 400
    result = generate_roster(group_id, start, end)
    if "error" in result:
        return jsonify(result), 400
    return jsonify(result)


@rosters_bp.route("/api/publish", methods=["POST"])
@login_required
def api_publish():
    d = request.get_json(force=True)
    group_id = d.get("group_id")
    start = _parse_date(d.get("start_date"))
    end = _parse_date(d.get("end_date"))
    if not group_id or not start or not end:
        return jsonify({"error": "group_id, start_date, and end_date required"}), 400
    result = publish_roster(group_id, start, end)
    if "error" in result:
        return jsonify(result), 400
    return jsonify(result)


@rosters_bp.route("/api/clear", methods=["POST"])
@login_required
def api_clear():
    d = request.get_json(force=True)
    group_id = d.get("group_id")
    start = _parse_date(d.get("start_date"))
    end = _parse_date(d.get("end_date"))
    if not group_id or not start or not end:
        return jsonify({"error": "group_id, start_date, and end_date required"}), 400
    result = clear_roster_drafts(group_id, start, end)
    if "error" in result:
        return jsonify(result), 400
    return jsonify(result)


@rosters_bp.route("/api/calendar")
@login_required
def api_calendar():
    group_id = request.args.get("group_id", type=int)
    start = _parse_date(request.args.get("start_date"))
    end = _parse_date(request.args.get("end_date"))
    if not group_id or not start or not end:
        return jsonify({"error": "group_id, start_date, and end_date required"}), 400
    result = get_roster_calendar(group_id, start, end)
    if "error" in result:
        return jsonify(result), 400
    return jsonify(result)


# ── Export Groups & Members CSV ─────────────────────────────

@rosters_bp.route("/api/export-groups-csv")
@login_required
def api_export_groups_csv():
    """Export all roster groups and their members as CSV."""
    groups = RosterGroup.query.order_by(RosterGroup.name).all()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "Group", "Member", "Employee ID", "Role", "Rotation Order",
        "Work Days", "Max Closings/Week", "Weekend Primary", "Weekend Days",
        "Fill-In Eligible", "Display Only", "No Closing Shifts",
    ])
    for g in groups:
        members = RosterMember.query.filter_by(group_id=g.id).order_by(
            RosterMember.rotation_order
        ).all()
        for m in members:
            emp = Employee.query.get(m.employee_id)
            name = emp.full_name if emp else f"ID {m.employee_id}"
            eid = emp.employee_id if emp else ""
            work_days = ""
            try:
                wd = json.loads(m.work_days) if m.work_days else None
                if wd:
                    work_days = ", ".join(wd)
            except Exception:
                pass
            wpd = ""
            try:
                wp = json.loads(m.weekend_primary_days) if m.weekend_primary_days else None
                if wp:
                    wpd = ", ".join(wp)
            except Exception:
                pass
            writer.writerow([
                g.name, name, eid, m.role or "",
                m.rotation_order or "", work_days,
                m.max_closings_per_week if m.max_closings_per_week is not None else "",
                "Yes" if m.is_weekend_primary else "",
                wpd,
                "Yes" if m.is_fill_in else "",
                "Yes" if m.is_display_only else "",
                "Yes" if m.accommodation_no_close else "",
            ])

    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=roster_groups_members.csv"},
    )


# ── Export CSV ──────────────────────────────────────────────

@rosters_bp.route("/api/export-csv", methods=["POST"])
@login_required
def api_export_csv():
    d = request.get_json(force=True)
    group_id = d.get("group_id")
    start = _parse_date(d.get("start_date"))
    end = _parse_date(d.get("end_date"))
    if not group_id or not start or not end:
        return jsonify({"error": "group_id, start_date, and end_date required"}), 400

    result = get_roster_calendar(group_id, start, end)
    rows = result.get("rows", [])
    group = RosterGroup.query.get(group_id)
    group_name = group.name if group else "roster"

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Date", "Employee", "Role", "Shift Start", "Shift End", "Status"])
    for r in rows:
        writer.writerow([r["date"], r["employee"], r["type"], r["start"], r["end"], r["status"]])

    output = buf.getvalue()
    safe_name = group_name.replace(" ", "_")
    filename = f"roster_{safe_name}_{start}_{end}.csv"
    return Response(
        output,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@rosters_bp.route("/api/export-xlsx", methods=["POST"])
@login_required
def api_export_xlsx():
    d = request.get_json(force=True)
    group_id = d.get("group_id")
    start = _parse_date(d.get("start_date"))
    end = _parse_date(d.get("end_date"))
    if not group_id or not start or not end:
        return jsonify({"error": "group_id, start_date, and end_date required"}), 400

    result = get_roster_calendar(group_id, start, end)
    rows = result.get("rows", [])
    group = RosterGroup.query.get(group_id)
    group_name = group.name if group else "roster"

    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        from openpyxl.utils import get_column_letter
    except ImportError:
        return jsonify({"error": "openpyxl not installed on server"}), 500

    wb = openpyxl.Workbook()
    thin = Side(style="thin", color="D0D0D0")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    def _fill(hex_color):
        return PatternFill("solid", fgColor=hex_color.lstrip("#"))

    def _write_header(ws, headers, fill_color):
        hdr_font = Font(name="Arial", bold=True, size=10, color="FFFFFF")
        for ci, h in enumerate(headers, 1):
            c = ws.cell(1, ci, h)
            c.font = hdr_font
            c.fill = _fill(fill_color)
            c.alignment = Alignment(horizontal="center", vertical="center")
            c.border = border
        ws.row_dimensions[1].height = 25
        ws.freeze_panes = "A2"

    def _set_widths(ws, widths):
        for i, w in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(i)].width = w

    # ── Sheet 1: Roster Schedule ──────────────────────────────
    ws_sched = wb.active
    ws_sched.title = "Roster Schedule"
    sched_headers = ["Date", "Day", "Employee", "Role", "Shift Start", "Shift End", "Status"]
    _write_header(ws_sched, sched_headers, "1F4973")

    alt_fill = _fill("EBF1FC")
    white_fill = _fill("FFFFFF")
    draft_font = Font(name="Arial", size=10, italic=True, color="B45309")
    normal_font = Font(name="Arial", size=10)
    center = Alignment(horizontal="center", vertical="center")
    left = Alignment(horizontal="left", vertical="center")

    for ri, r in enumerate(rows, 2):
        bg = alt_fill if ri % 2 == 0 else white_fill
        # Parse date for day-of-week
        try:
            dt = datetime.date.fromisoformat(r["date"])
            day_name = dt.strftime("%a")
        except (ValueError, AttributeError):
            day_name = ""

        vals = [r["date"], day_name, r["employee"], r["type"],
                r["start"], r["end"], r["status"]]
        is_draft = r.get("status", "").lower() == "draft"
        for ci, v in enumerate(vals, 1):
            c = ws_sched.cell(ri, ci, v or "")
            c.fill = bg
            c.border = border
            c.font = draft_font if is_draft else normal_font
            c.alignment = center if ci != 3 else left
        ws_sched.row_dimensions[ri].height = 20

    _set_widths(ws_sched, [12, 6, 20, 10, 12, 12, 12])

    # ── Sheet 2: Summary ──────────────────────────────────────
    ws_sum = wb.create_sheet("Summary")

    # Build per-employee stats from rows
    emp_stats = {}
    for r in rows:
        name = r.get("employee", "Unknown")
        if name not in emp_stats:
            emp_stats[name] = {
                "total": 0, "weekday": 0, "weekend": 0,
                "draft": 0, "published": 0,
                "roles": {},
            }
        s = emp_stats[name]
        s["total"] += 1
        try:
            dt = datetime.date.fromisoformat(r["date"])
            if dt.weekday() < 5:
                s["weekday"] += 1
            else:
                s["weekend"] += 1
        except (ValueError, AttributeError):
            pass
        status = r.get("status", "").lower()
        if status == "draft":
            s["draft"] += 1
        else:
            s["published"] += 1
        role = r.get("type", "")
        s["roles"][role] = s["roles"].get(role, 0) + 1

    # Collect all unique roles for columns
    all_roles = sorted({role for s in emp_stats.values() for role in s["roles"]})

    sum_headers = ["Employee", "Total", "Weekday", "Weekend", "Draft", "Published"] + all_roles
    _write_header(ws_sum, sum_headers, "548235")

    sum_alt = _fill("EBF4E6")
    bold_font = Font(name="Arial", size=10, bold=True)
    for ri, (name, s) in enumerate(sorted(emp_stats.items()), 2):
        bg = sum_alt if ri % 2 == 0 else white_fill
        vals = [name, s["total"], s["weekday"], s["weekend"],
                s["draft"], s["published"]] + [s["roles"].get(r, 0) for r in all_roles]
        for ci, v in enumerate(vals, 1):
            c = ws_sum.cell(ri, ci, v)
            c.fill = bg
            c.border = border
            c.font = bold_font if ci == 1 else normal_font
            c.alignment = left if ci == 1 else center
        ws_sum.row_dimensions[ri].height = 20

    # Totals row
    total_row = len(emp_stats) + 2
    ws_sum.cell(total_row, 1, "TOTAL").font = Font(name="Arial", size=10, bold=True, color="FFFFFF")
    ws_sum.cell(total_row, 1).fill = _fill("548235")
    ws_sum.cell(total_row, 1).border = border
    ws_sum.cell(total_row, 1).alignment = left
    for ci in range(2, len(sum_headers) + 1):
        c = ws_sum.cell(total_row, ci)
        c.value = sum(ws_sum.cell(r, ci).value or 0 for r in range(2, total_row))
        c.font = Font(name="Arial", size=10, bold=True, color="FFFFFF")
        c.fill = _fill("548235")
        c.border = border
        c.alignment = center
    ws_sum.row_dimensions[total_row].height = 25

    base_widths = [20, 8, 10, 10, 8, 10]
    role_widths = [max(10, len(r) + 4) for r in all_roles]
    _set_widths(ws_sum, base_widths + role_widths)

    # ── Sheet 3: Info ─────────────────────────────────────────
    ws_info = wb.create_sheet("Info")
    _write_header(ws_info, ["Field", "Value"], "6B4C9A")
    info_rows = [
        ["Group", group_name],
        ["Period", f"{start} to {end}"],
        ["Generated", datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")],
        ["Total Assignments", len(rows)],
        ["Employees", len(emp_stats)],
        ["Powered by", "Serevo — Bergeron WFM Solutions"],
    ]
    info_alt = _fill("F5F0FF")
    for ri, (field, val) in enumerate(info_rows, 2):
        bg = info_alt if ri % 2 == 0 else white_fill
        for ci, v in enumerate([field, val], 1):
            c = ws_info.cell(ri, ci, v)
            c.fill = bg
            c.border = border
            c.font = bold_font if ci == 1 else normal_font
            c.alignment = left
    _set_widths(ws_info, [20, 40])

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    safe_name = group_name.replace(" ", "_")
    filename = f"roster_{safe_name}_{start}_{end}.xlsx"
    return Response(
        buf.getvalue(),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


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


def _parse_date(v):
    if not v:
        return None
    if isinstance(v, datetime.date):
        return v
    try:
        return datetime.date.fromisoformat(v)
    except (ValueError, AttributeError):
        return None
