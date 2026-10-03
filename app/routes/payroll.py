"""
Payroll Export — aggregate timesheet data by employee for a pay period.
Admins can view summaries and export to CSV.
"""
import csv
import io
import logging
from datetime import date, timedelta, datetime

from flask import Blueprint, render_template, request, jsonify, Response
from app.auth import login_required, get_current_user
from app.models import db, Employee, TimeClock, Schedule, PTOEntry

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
        if s.start_time and s.end_time:
            diff = (
                datetime.combine(date.today(), s.end_time)
                - datetime.combine(date.today(), s.start_time)
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
            "department": emp.department or "",
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
            emp.employee_id, emp.full_name, emp.department or "",
            len(d["days_worked"]), actual, regular, overtime,
        ])

    filename = f"payroll_{start.isoformat()}_{end.isoformat()}.csv"
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
