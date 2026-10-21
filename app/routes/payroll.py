"""
Payroll Export — aggregate timesheet data by employee for a pay period.
Admins can view summaries, export to CSV, and approve/adjust punch times.
"""
import csv
import io
import logging
from datetime import date, timedelta, datetime, timezone

from flask import Blueprint, render_template, request, jsonify, Response
from app.auth import login_required, get_current_user
from app.models import db, Employee, TimeClock, Schedule, PTOEntry, User

log = logging.getLogger(__name__)
payroll_bp = Blueprint("payroll", __name__, url_prefix="/payroll")


@payroll_bp.route("/")
@login_required
def index():
    user = get_current_user()
    if not user or user.get("role") not in ("admin", "supervisor"):
        return "Forbidden", 403
    return render_template("payroll/index.html", user=user)


@payroll_bp.route("/api/summary", methods=["POST"])
@login_required
def payroll_summary():
    """Aggregate hours by employee for a date range."""
    user = get_current_user()
    if not user or user.get("role") not in ("admin", "supervisor"):
        return jsonify(error="Forbidden"), 403

    # Demo mode
    if user.get("is_demo"):
        from app.demo_data import get_demo_payroll_summary
        data = request.get_json(silent=True) or {}
        try:
            s = date.fromisoformat(data.get("start_date", ""))
            e = date.fromisoformat(data.get("end_date", ""))
        except (ValueError, TypeError):
            e = date.today()
            s = e - timedelta(days=13)
        return jsonify(get_demo_payroll_summary(s, e))

    data = request.get_json(silent=True) or {}
    try:
        start = date.fromisoformat(data.get("start_date", ""))
        end = date.fromisoformat(data.get("end_date", ""))
    except (ValueError, TypeError):
        # Default to current bi-weekly period (last 14 days)
        end = date.today()
        start = end - timedelta(days=13)

    if (end - start).days > 90:
        return jsonify(success=False, error="Date range cannot exceed 90 days")

    # Get all completed time entries in range
    entries = (
        TimeClock.query
        .filter(TimeClock.date >= start, TimeClock.date <= end)
        .filter(TimeClock.status.in_(["completed", "edited"]))
        .all()
    )

    # Aggregate by employee
    emp_data = {}
    for tc in entries:
        eid = tc.employee_id
        if eid not in emp_data:
            emp_data[eid] = {
                "total_hours": 0.0,
                "days_worked": set(),
                "entries": 0,
                "edited_entries": 0,
            }
        hours = tc.total_hours or 0.0
        emp_data[eid]["total_hours"] += hours
        emp_data[eid]["days_worked"].add(tc.date)
        emp_data[eid]["entries"] += 1
        if tc.status == "edited":
            emp_data[eid]["edited_entries"] += 1

    # Get scheduled hours for comparison
    schedules = (
        Schedule.query
        .filter(Schedule.schedule_date >= start, Schedule.schedule_date <= end)
        .all()
    )
    sched_hours = {}
    for s in schedules:
        eid = s.employee_id
        hrs = 0
        if s.shift_start and s.shift_end:
            diff = (
                datetime.combine(date.today(), s.shift_end)
                - datetime.combine(date.today(), s.shift_start)
            )
            hrs = diff.total_seconds() / 3600
            if hrs < 0:
                hrs += 24
        sched_hours[eid] = sched_hours.get(eid, 0) + hrs

    # Get PTO days in range
    pto_entries = (
        PTOEntry.query
        .filter(
            PTOEntry.start_date <= end,
            PTOEntry.end_date >= start,
            PTOEntry.approval_status == "approved",
        )
        .all()
    )
    pto_days = {}
    for p in pto_entries:
        eid = p.employee_id
        # Count days within our range
        ps = max(p.start_date, start)
        pe = min(p.end_date, end)
        days = (pe - ps).days + 1
        pto_days[eid] = pto_days.get(eid, 0) + days

    # Fetch employee info
    all_eids = set(emp_data.keys()) | set(sched_hours.keys())
    employees = Employee.query.filter(Employee.id.in_(all_eids)).all() if all_eids else []
    emp_map = {e.id: e for e in employees}

    rows = []
    for eid in sorted(all_eids):
        emp = emp_map.get(eid)
        if not emp:
            continue
        d = emp_data.get(eid, {"total_hours": 0, "days_worked": set(), "entries": 0, "edited_entries": 0})
        actual = round(d["total_hours"], 2)
        scheduled = round(sched_hours.get(eid, 0), 2)
        overtime = max(0, round(actual - 40, 2)) if actual > 40 else 0
        regular = round(actual - overtime, 2)

        rows.append({
            "employee_id": emp.employee_id,
            "name": emp.full_name,
            "department": (emp.planning_unit.name if emp.planning_unit else ""),
            "days_worked": len(d["days_worked"]),
            "total_hours": actual,
            "regular_hours": regular,
            "overtime_hours": overtime,
            "scheduled_hours": scheduled,
            "variance": round(actual - scheduled, 2),
            "pto_days": pto_days.get(eid, 0),
            "entries": d["entries"],
            "edited_entries": d["edited_entries"],
        })

    # Summary totals
    totals = {
        "total_employees": len(rows),
        "total_hours": round(sum(r["total_hours"] for r in rows), 2),
        "total_regular": round(sum(r["regular_hours"] for r in rows), 2),
        "total_overtime": round(sum(r["overtime_hours"] for r in rows), 2),
        "total_pto_days": sum(r["pto_days"] for r in rows),
    }

    return jsonify(
        success=True,
        start_date=start.isoformat(),
        end_date=end.isoformat(),
        rows=rows,
        totals=totals,
    )


@payroll_bp.route("/api/export-csv", methods=["POST"])
@login_required
def export_csv():
    """Export payroll summary as CSV."""
    user = get_current_user()
    if not user or user.get("role") not in ("admin", "supervisor"):
        return jsonify(error="Forbidden"), 403

    data = request.get_json(silent=True) or {}
    try:
        start = date.fromisoformat(data.get("start_date", ""))
        end = date.fromisoformat(data.get("end_date", ""))
    except (ValueError, TypeError):
        end = date.today()
        start = end - timedelta(days=13)

    # Reuse summary logic
    from flask import make_response
    # Get the summary data
    with payroll_bp.test_request_context():
        pass  # Can't easily reuse, so inline the query

    entries = (
        TimeClock.query
        .filter(TimeClock.date >= start, TimeClock.date <= end)
        .filter(TimeClock.status.in_(["completed", "edited"]))
        .all()
    )

    emp_data = {}
    for tc in entries:
        eid = tc.employee_id
        if eid not in emp_data:
            emp_data[eid] = {"total_hours": 0.0, "days_worked": set(), "entries": 0}
        emp_data[eid]["total_hours"] += tc.total_hours or 0.0
        emp_data[eid]["days_worked"].add(tc.date)
        emp_data[eid]["entries"] += 1

    all_eids = set(emp_data.keys())
    employees = Employee.query.filter(Employee.id.in_(all_eids)).all() if all_eids else []
    emp_map = {e.id: e for e in employees}

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Employee ID", "Name", "Department", "Days Worked",
        "Total Hours", "Regular Hours", "Overtime Hours",
    ])
    for eid in sorted(all_eids):
        emp = emp_map.get(eid)
        if not emp:
            continue
        d = emp_data[eid]
        actual = round(d["total_hours"], 2)
        overtime = max(0, round(actual - 40, 2)) if actual > 40 else 0
        regular = round(actual - overtime, 2)
        writer.writerow([
            emp.employee_id, emp.full_name, (emp.planning_unit.name if emp.planning_unit else ""),
            len(d["days_worked"]), actual, regular, overtime,
        ])

    filename = f"payroll_{start.isoformat()}_{end.isoformat()}.csv"
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ═══════════════════════════════════════════════════════════════
# ADMIN PUNCH MANAGEMENT
# ═══════════════════════════════════════════════════════════════

@payroll_bp.route("/admin")
@login_required
def punch_admin():
    """Admin page for reviewing / adjusting punch times."""
    user = get_current_user()
    if not user or user.get("role") not in ("admin", "supervisor"):
        return "Forbidden", 403
    return render_template("payroll/admin.html", user=user)


def _utcnow():
    return datetime.now(timezone.utc)


def _utc_iso(dt):
    if dt is None:
        return None
    s = dt.isoformat()
    if not s.endswith("Z") and "+" not in s and "-" not in s[10:]:
        s += "Z"
    return s


@payroll_bp.route("/api/punches", methods=["POST"])
@login_required
def list_punches():
    """List individual punch records for admin review."""
    user = get_current_user()
    if not user or user.get("role") not in ("admin", "supervisor"):
        return jsonify(error="Forbidden"), 403

    data = request.get_json(silent=True) or {}
    try:
        start = date.fromisoformat(data.get("start_date", ""))
        end = date.fromisoformat(data.get("end_date", ""))
    except (ValueError, TypeError):
        end = date.today()
        start = end - timedelta(days=6)

    employee_id = data.get("employee_id")  # optional filter
    status_filter = data.get("status")     # optional: pending, approved, flagged

    q = TimeClock.query.filter(
        TimeClock.date >= start,
        TimeClock.date <= end,
    )
    if employee_id:
        q = q.filter(TimeClock.employee_id == employee_id)
    if status_filter:
        q = q.filter(TimeClock.approval_status == status_filter)

    q = q.order_by(TimeClock.date.desc(), TimeClock.clock_in.desc())
    entries = q.limit(500).all()

    # Gather employee & editor info
    emp_ids = {e.employee_id for e in entries}
    user_ids = {e.edited_by for e in entries if e.edited_by}
    user_ids |= {e.approved_by for e in entries if e.approved_by}

    emps = Employee.query.filter(Employee.id.in_(emp_ids)).all() if emp_ids else []
    emp_map = {e.id: e for e in emps}
    users = User.query.filter(User.id.in_(user_ids)).all() if user_ids else []
    user_map = {u.id: u for u in users}

    rows = []
    for e in entries:
        emp = emp_map.get(e.employee_id)
        editor = user_map.get(e.edited_by) if e.edited_by else None
        approver = user_map.get(e.approved_by) if e.approved_by else None
        rows.append({
            "id": e.id,
            "employee_id": e.employee_id,
            "employee_name": (emp.first_name + " " + emp.last_name) if emp else "Unknown",
            "employee_id_str": emp.employee_id if emp else "",
            "date": e.date.isoformat(),
            "clock_in": _utc_iso(e.clock_in),
            "clock_out": _utc_iso(e.clock_out),
            "total_hours": e.total_hours,
            "status": e.status,
            "approval_status": e.approval_status or "pending",
            "original_clock_in": _utc_iso(e.original_clock_in),
            "original_clock_out": _utc_iso(e.original_clock_out),
            "edited_by": editor.username if editor else None,
            "edited_at": _utc_iso(e.edited_at),
            "approved_by": approver.username if approver else None,
            "approved_at": _utc_iso(e.approved_at),
            "admin_note": e.admin_note,
            "clock_in_note": e.clock_in_note,
            "clock_out_note": e.clock_out_note,
        })

    # Stats
    total = len(rows)
    pending = sum(1 for r in rows if r["approval_status"] == "pending")
    approved = sum(1 for r in rows if r["approval_status"] == "approved")
    flagged = sum(1 for r in rows if r["approval_status"] == "flagged")

    return jsonify(
        success=True,
        start_date=start.isoformat(),
        end_date=end.isoformat(),
        rows=rows,
        stats={"total": total, "pending": pending, "approved": approved, "flagged": flagged},
    )


@payroll_bp.route("/api/punch/<int:punch_id>/adjust", methods=["POST"])
@login_required
def adjust_punch(punch_id):
    """Admin adjusts clock_in / clock_out for a punch entry."""
    user = get_current_user()
    if not user or user.get("role") not in ("admin", "supervisor"):
        return jsonify(error="Forbidden"), 403

    entry = TimeClock.query.get(punch_id)
    if not entry:
        return jsonify(success=False, error="Punch entry not found"), 404

    data = request.get_json(silent=True) or {}
    new_in = data.get("clock_in")
    new_out = data.get("clock_out")
    note = data.get("note", "")

    if not new_in and not new_out:
        return jsonify(success=False, error="Provide clock_in or clock_out to adjust")

    # Save originals before first edit
    if not entry.original_clock_in and entry.clock_in:
        entry.original_clock_in = entry.clock_in
    if not entry.original_clock_out and entry.clock_out:
        entry.original_clock_out = entry.clock_out

    if new_in:
        try:
            entry.clock_in = datetime.fromisoformat(new_in.replace("Z", "+00:00"))
        except ValueError:
            return jsonify(success=False, error="Invalid clock_in format")
    if new_out:
        try:
            entry.clock_out = datetime.fromisoformat(new_out.replace("Z", "+00:00"))
        except ValueError:
            return jsonify(success=False, error="Invalid clock_out format")

    # Recalculate total_hours
    if entry.clock_in and entry.clock_out:
        ci = entry.clock_in if entry.clock_in.tzinfo else entry.clock_in.replace(tzinfo=timezone.utc)
        co = entry.clock_out if entry.clock_out.tzinfo else entry.clock_out.replace(tzinfo=timezone.utc)
        entry.total_hours = round((co - ci).total_seconds() / 3600, 2)

    entry.status = "edited"
    entry.edited_by = user.get("id")
    entry.edited_at = _utcnow()
    if note:
        entry.admin_note = note

    db.session.commit()
    return jsonify(success=True, total_hours=entry.total_hours)


@payroll_bp.route("/api/punch/<int:punch_id>/approve", methods=["POST"])
@login_required
def approve_punch(punch_id):
    """Admin approves or flags a punch entry."""
    user = get_current_user()
    if not user or user.get("role") not in ("admin", "supervisor"):
        return jsonify(error="Forbidden"), 403

    entry = TimeClock.query.get(punch_id)
    if not entry:
        return jsonify(success=False, error="Punch entry not found"), 404

    data = request.get_json(silent=True) or {}
    new_status = data.get("approval_status", "approved")
    if new_status not in ("approved", "flagged", "pending"):
        return jsonify(success=False, error="Invalid approval_status")

    entry.approval_status = new_status
    entry.approved_by = user.get("id")
    entry.approved_at = _utcnow()
    if data.get("note"):
        entry.admin_note = data["note"]

    db.session.commit()
    return jsonify(success=True, approval_status=new_status)


@payroll_bp.route("/api/punches/bulk-approve", methods=["POST"])
@login_required
def bulk_approve():
    """Approve multiple punch entries at once."""
    user = get_current_user()
    if not user or user.get("role") not in ("admin", "supervisor"):
        return jsonify(error="Forbidden"), 403

    data = request.get_json(silent=True) or {}
    ids = data.get("ids", [])
    if not ids:
        return jsonify(success=False, error="No punch IDs provided")

    now = _utcnow()
    count = 0
    for pid in ids:
        entry = TimeClock.query.get(pid)
        if entry and entry.approval_status != "approved":
            entry.approval_status = "approved"
            entry.approved_by = user.get("id")
            entry.approved_at = now
            count += 1

    db.session.commit()
    return jsonify(success=True, approved_count=count)
