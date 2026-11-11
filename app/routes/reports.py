"""
Reports & Analytics — cross-module dashboard with key workforce metrics.
"""
import csv
import io
import logging
from collections import defaultdict
from datetime import date, datetime, timedelta
from flask import Blueprint, jsonify, request, render_template, Response
from sqlalchemy import func, and_
from app.auth import login_required, get_current_user
from app.models import (
    db, Employee, Schedule, PTOEntry, ForecastInterval,
    QualityEvaluation, ShiftSwapRequest, ShiftBid, ShiftPost,
    PlanningUnit, TimeClock, AttendanceEvent, AdherenceException,
    IntervalActual, RequirementInterval, LeaveOfAbsence,
)

log = logging.getLogger(__name__)
reports_bp = Blueprint("reports", __name__, url_prefix="/reports")


def _require_admin_or_sup():
    user = get_current_user()
    if not user or user.get("role") not in ("admin", "supervisor"):
        return None
    return user


def _parse_filters(payload):
    """Parse shared filter fields from request payload."""
    start_str = payload.get("start_date")
    end_str = payload.get("end_date")
    days = int(payload.get("days", 30))

    if start_str and end_str:
        try:
            start_d = date.fromisoformat(start_str)
            end_d = date.fromisoformat(end_str)
        except (ValueError, TypeError):
            start_d = date.today() - timedelta(days=days)
            end_d = date.today()
    else:
        end_d = date.today()
        start_d = end_d - timedelta(days=days)

    lob = payload.get("lob", "").strip()
    team_lead = payload.get("team_lead", "").strip()
    employee_id = payload.get("employee_id")  # DB int id

    return start_d, end_d, lob, team_lead, employee_id


def _filter_employees(start_d, end_d, lob, team_lead, employee_id):
    """Return filtered Employee query."""
    q = Employee.query.filter_by(status="Active")
    if employee_id:
        q = q.filter(Employee.id == int(employee_id))
    if lob:
        # Support comma-separated LOB names
        lob_names = [l.strip() for l in lob.split(",") if l.strip()]
        if lob_names:
            pu_ids = [pu.id for pu in PlanningUnit.query.filter(
                db.func.lower(PlanningUnit.name).in_([n.lower() for n in lob_names])
            ).all()]
            if pu_ids:
                q = q.filter(Employee.planning_unit_id.in_(pu_ids))
            else:
                q = q.filter(db.false())
    if team_lead:
        q = q.filter(Employee.team_lead == team_lead)
    return q.order_by(Employee.last_name, Employee.first_name)


def _lob_name(emp):
    """Get LOB name for an employee."""
    if emp.planning_unit_id:
        pu = PlanningUnit.query.get(emp.planning_unit_id)
        return pu.name if pu else ""
    return ""


@reports_bp.route("/")
@login_required
def index():
    user = get_current_user()
    if not user or user.get("role") not in ("admin", "supervisor"):
        from flask import redirect
        return redirect("/")
    return render_template("reports/index.html")


@reports_bp.route("/api/summary", methods=["POST"])
@login_required
def summary():
    """Return summary metrics for the reports dashboard."""
    user = _require_admin_or_sup()
    if not user:
        return jsonify({"success": False, "error": "Forbidden"}), 403

    # Demo mode — return canned metrics
    if user.get("is_demo"):
        from app.demo_data import get_demo_reports_summary
        payload = request.get_json(silent=True) or {}
        days = int(payload.get("days", 30))
        return jsonify(get_demo_reports_summary(days))

    try:
        payload = request.get_json(silent=True) or {}
        start_d, end_d, lob, team_lead, employee_id = _parse_filters(payload)

        # Filter employees by LOB/team lead
        emps = _filter_employees(start_d, end_d, lob, team_lead, employee_id).all()
        emp_ids = {e.id for e in emps}

        # -- Headcount --
        total_employees = len(emps)

        # -- Schedule stats (filtered by employees and date range) --
        sched_q = Schedule.query.filter(
            Schedule.schedule_date >= start_d,
            Schedule.schedule_date <= end_d,
        )
        if emp_ids:
            sched_q = sched_q.filter(Schedule.employee_id.in_(emp_ids))
        total_scheduled_hours = db.session.query(
            func.coalesce(func.sum(Schedule.hours), 0)
        ).filter(
            Schedule.schedule_date >= start_d,
            Schedule.schedule_date <= end_d,
        )
        if emp_ids:
            total_scheduled_hours = total_scheduled_hours.filter(Schedule.employee_id.in_(emp_ids))
        total_scheduled_hours = total_scheduled_hours.scalar()
        total_shifts = sched_q.count()

        # -- PTO stats --
        pto_base = PTOEntry.query.filter(PTOEntry.start_date >= start_d, PTOEntry.start_date <= end_d)
        if emp_ids:
            pto_base = pto_base.filter(PTOEntry.employee_id.in_(emp_ids))
        pto_pending = pto_base.filter(PTOEntry.approval_status == "pending").count()
        pto_approved = pto_base.filter(PTOEntry.approval_status == "approved").count()
        pto_denied = pto_base.filter(PTOEntry.approval_status == "denied").count()

        # PTO days consumed
        pto_days = 0
        approved_q = PTOEntry.query.filter(
            PTOEntry.approval_status == "approved",
            PTOEntry.start_date >= start_d, PTOEntry.start_date <= end_d,
        )
        if emp_ids:
            approved_q = approved_q.filter(PTOEntry.employee_id.in_(emp_ids))
        for e in approved_q.all():
            pto_days += (e.end_date - e.start_date).days + 1

        # -- Quality --
        qual_q = QualityEvaluation.query.filter(
            QualityEvaluation.eval_date >= start_d, QualityEvaluation.eval_date <= end_d,
        )
        if emp_ids:
            qual_q = qual_q.filter(QualityEvaluation.employee_id.in_(emp_ids))
        quality_avg = db.session.query(func.avg(QualityEvaluation.overall_score)).filter(
            QualityEvaluation.eval_date >= start_d, QualityEvaluation.eval_date <= end_d,
        )
        if emp_ids:
            quality_avg = quality_avg.filter(QualityEvaluation.employee_id.in_(emp_ids))
        quality_avg = quality_avg.scalar()
        quality_count = qual_q.count()

        # -- Shift bids & swaps --
        open_shifts = ShiftPost.query.filter_by(status="open").count()
        pending_bids = ShiftBid.query.filter_by(status="pending").count()
        pending_swaps = ShiftSwapRequest.query.filter(
            ShiftSwapRequest.status.in_(["pending", "accepted"])
        ).count()

        # -- Scheduling hours by week --
        weekly_hours = []
        days_span = (end_d - start_d).days + 1
        num_weeks = max(1, min(days_span // 7 + 1, 12))
        for w in range(num_weeks):
            week_start = start_d + timedelta(weeks=w)
            week_end = min(week_start + timedelta(days=6), end_d)
            hrs_q = db.session.query(
                func.coalesce(func.sum(Schedule.hours), 0)
            ).filter(
                Schedule.schedule_date >= week_start,
                Schedule.schedule_date <= week_end,
            )
            if emp_ids:
                hrs_q = hrs_q.filter(Schedule.employee_id.in_(emp_ids))
            hrs = hrs_q.scalar()
            weekly_hours.append({
                "week": week_start.isoformat(),
                "hours": round(float(hrs), 1),
            })

        # -- PTO by type --
        pto_by_type = []
        try:
            from app.models import TimeOffType
            types = TimeOffType.query.all()
            for t in types:
                cnt_q = PTOEntry.query.filter(
                    PTOEntry.time_off_type_id == t.id,
                    PTOEntry.start_date >= start_d, PTOEntry.start_date <= end_d,
                    PTOEntry.approval_status == "approved",
                )
                if emp_ids:
                    cnt_q = cnt_q.filter(PTOEntry.employee_id.in_(emp_ids))
                cnt = cnt_q.count()
                if cnt:
                    pto_by_type.append({"type": t.label, "count": cnt})
        except Exception:
            pass

        # -- Quality trend by week --
        quality_trend = []
        for w in range(num_weeks):
            week_start = start_d + timedelta(weeks=w)
            week_end = min(week_start + timedelta(days=6), end_d)
            avg_q = db.session.query(
                func.avg(QualityEvaluation.overall_score)
            ).filter(
                QualityEvaluation.eval_date >= week_start,
                QualityEvaluation.eval_date <= week_end,
            )
            if emp_ids:
                avg_q = avg_q.filter(QualityEvaluation.employee_id.in_(emp_ids))
            avg = avg_q.scalar()
            quality_trend.append({
                "week": week_start.isoformat(),
                "avg_score": round(float(avg), 1) if avg else None,
            })

        return jsonify({
            "success": True,
            "period_days": (end_d - start_d).days + 1,
            "headcount": total_employees,
            "schedule": {
                "total_shifts": total_shifts,
                "total_hours": round(float(total_scheduled_hours), 1),
                "weekly_hours": weekly_hours,
            },
            "pto": {
                "pending": pto_pending,
                "approved": pto_approved,
                "denied": pto_denied,
                "days_consumed": pto_days,
                "by_type": pto_by_type,
            },
            "quality": {
                "avg_score": round(float(quality_avg), 1) if quality_avg else None,
                "eval_count": quality_count,
                "trend": quality_trend,
            },
            "self_service": {
                "open_shifts": open_shifts,
                "pending_bids": pending_bids,
                "pending_swaps": pending_swaps,
            },
        })
    except Exception as e:
        log.exception("Reports summary error")
        return jsonify({"success": False, "error": str(e)})


@reports_bp.route("/api/attendance", methods=["POST"])
@login_required
def attendance_report():
    """Attendance hours report — actual vs scheduled per employee."""
    user = _require_admin_or_sup()
    if not user:
        return jsonify(success=False, error="Forbidden"), 403

    # Demo mode
    if user.get("is_demo"):
        from app.demo_data import get_demo_reports_attendance
        payload = request.get_json(silent=True) or {}
        days = int(payload.get("days", 7))
        return jsonify(success=True, rows=get_demo_reports_attendance(days), period_days=days)

    payload = request.get_json(silent=True) or {}
    start_d, end_d, lob, team_lead, employee_id_f = _parse_filters(payload)

    employees = _filter_employees(start_d, end_d, lob, team_lead, employee_id_f).all()

    rows = []
    for emp in employees:
        # Actual hours from TimeClock
        actual = db.session.query(
            func.coalesce(func.sum(TimeClock.total_hours), 0)
        ).filter(
            TimeClock.employee_id == emp.id,
            TimeClock.date >= start_d,
            TimeClock.date <= end_d,
            TimeClock.status == "completed",
        ).scalar()

        # Scheduled hours
        scheduled = db.session.query(
            func.coalesce(func.sum(Schedule.hours), 0)
        ).filter(
            Schedule.employee_id == emp.id,
            Schedule.schedule_date >= start_d,
            Schedule.schedule_date <= end_d,
        ).scalar()

        actual_f = round(float(actual), 1)
        scheduled_f = round(float(scheduled), 1)
        diff = round(actual_f - scheduled_f, 1)

        if actual_f > 0 or scheduled_f > 0:
            rows.append({
                "name": emp.full_name,
                "employee_id": emp.employee_id,
                "lob": _lob_name(emp),
                "actual_hours": actual_f,
                "scheduled_hours": scheduled_f,
                "difference": diff,
            })

    return jsonify(success=True, rows=rows, period_days=(end_d - start_d).days + 1)


@reports_bp.route("/api/attendance/csv", methods=["POST"])
@login_required
def attendance_csv():
    """Download attendance report as CSV."""
    user = _require_admin_or_sup()
    if not user:
        return "Forbidden", 403
    if user.get("is_demo"):
        return jsonify(success=True, message="Demo mode")

    payload = request.get_json(silent=True) or {}
    days = int(payload.get("days", 7))
    cutoff = date.today() - timedelta(days=days)

    employees = Employee.query.filter_by(status="Active").order_by(
        Employee.last_name, Employee.first_name
    ).all()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Employee", "Employee ID", "Scheduled Hours", "Actual Hours", "Difference"])

    for emp in employees:
        actual = db.session.query(
            func.coalesce(func.sum(TimeClock.total_hours), 0)
        ).filter(
            TimeClock.employee_id == emp.id,
            TimeClock.date >= cutoff,
            TimeClock.status == "completed",
        ).scalar()
        scheduled = db.session.query(
            func.coalesce(func.sum(Schedule.hours), 0)
        ).filter(
            Schedule.employee_id == emp.id,
            Schedule.schedule_date >= cutoff,
        ).scalar()
        actual_f = round(float(actual), 1)
        scheduled_f = round(float(scheduled), 1)
        if actual_f > 0 or scheduled_f > 0:
            writer.writerow([emp.full_name, emp.employee_id, scheduled_f, actual_f, round(actual_f - scheduled_f, 1)])

    buf.seek(0)
    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=attendance_{date.today().isoformat()}.csv"},
    )


@reports_bp.route("/api/summary/csv", methods=["POST"])
@login_required
def summary_csv():
    """Download summary KPIs as CSV."""
    user = _require_admin_or_sup()
    if not user:
        return "Forbidden", 403
    if user.get("is_demo"):
        return jsonify(success=True, message="Demo mode")

    payload = request.get_json(silent=True) or {}
    days = int(payload.get("days", 30))
    cutoff = date.today() - timedelta(days=days)

    total_employees = Employee.query.filter_by(status="Active").count()
    total_hours = db.session.query(
        func.coalesce(func.sum(Schedule.hours), 0)
    ).filter(Schedule.schedule_date >= cutoff).scalar()
    total_shifts = Schedule.query.filter(Schedule.schedule_date >= cutoff).count()
    pto_pending = PTOEntry.query.filter_by(approval_status="pending").count()
    pto_approved = PTOEntry.query.filter(
        PTOEntry.approval_status == "approved", PTOEntry.start_date >= cutoff
    ).count()
    quality_avg = db.session.query(
        func.avg(QualityEvaluation.overall_score)
    ).filter(QualityEvaluation.eval_date >= cutoff).scalar()
    quality_count = QualityEvaluation.query.filter(
        QualityEvaluation.eval_date >= cutoff
    ).count()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Metric", "Value"])
    writer.writerow(["Period (days)", days])
    writer.writerow(["Active Employees", total_employees])
    writer.writerow(["Total Shifts", total_shifts])
    writer.writerow(["Total Scheduled Hours", round(float(total_hours), 1)])
    writer.writerow(["PTO Pending", pto_pending])
    writer.writerow(["PTO Approved", pto_approved])
    writer.writerow(["Quality Avg Score", round(float(quality_avg), 1) if quality_avg else "N/A"])
    writer.writerow(["Quality Evaluations", quality_count])

    buf.seek(0)
    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=summary_{date.today().isoformat()}.csv"},
    )


# ═══════════════════════════════════════════════════════════════
# NEW REPORT ENDPOINTS — all share the filter payload pattern
# ═══════════════════════════════════════════════════════════════

@reports_bp.route("/api/filters", methods=["POST"])
@login_required
def report_filters():
    """Return available filter options: LOBs, team leads, employees."""
    user = _require_admin_or_sup()
    if not user:
        return jsonify(success=False, error="Forbidden"), 403

    if user.get("is_demo"):
        from app.demo_data import get_demo_report_filters
        return jsonify(get_demo_report_filters())

    lobs = [{"id": pu.id, "name": pu.name}
            for pu in PlanningUnit.query.order_by(PlanningUnit.name).all()]

    tls = [r[0] for r in db.session.query(Employee.team_lead).filter(
        Employee.status == "Active",
        Employee.team_lead.isnot(None),
        Employee.team_lead != "",
    ).distinct().order_by(Employee.team_lead).all()]

    emps = [{"id": e.id, "name": e.full_name, "employee_id": e.employee_id}
            for e in Employee.query.filter_by(status="Active")
            .order_by(Employee.last_name, Employee.first_name).all()]

    return jsonify(success=True, lobs=lobs, team_leads=tls, employees=emps)


# ── 1. Attendance (coded events) ──────────────────────────────

@reports_bp.route("/api/attendance-coded", methods=["POST"])
@login_required
def attendance_coded():
    """Attendance report from coded AttendanceEvent records."""
    user = _require_admin_or_sup()
    if not user:
        return jsonify(success=False, error="Forbidden"), 403
    if user.get("is_demo"):
        from app.demo_data import get_demo_report_attendance_coded
        payload = request.get_json(silent=True) or {}
        sd = payload.get("start_date")
        ed = payload.get("end_date")
        return jsonify(get_demo_report_attendance_coded(
            date.fromisoformat(sd) if sd else None, date.fromisoformat(ed) if ed else None))

    try:
        payload = request.get_json(silent=True) or {}
        start_d, end_d, lob, team_lead, employee_id = _parse_filters(payload)

        emps = _filter_employees(start_d, end_d, lob, team_lead, employee_id).all()
        emp_ids = {e.id for e in emps}
        emp_map = {e.id: e for e in emps}

        # Total scheduled in range
        total_scheduled = Schedule.query.filter(
            Schedule.employee_id.in_(emp_ids),
            Schedule.schedule_date >= start_d,
            Schedule.schedule_date <= end_d,
            Schedule.status == "scheduled",
        ).count() if emp_ids else 0

        # Coded events
        events = AttendanceEvent.query.filter(
            AttendanceEvent.employee_id.in_(emp_ids),
            AttendanceEvent.event_date >= start_d,
            AttendanceEvent.event_date <= end_d,
        ).all() if emp_ids else []

        absent_count = sum(1 for e in events if e.adherence_exception and e.adherence_exception.category == "absence")
        late_count = sum(1 for e in events if e.adherence_exception and e.adherence_exception.category == "late")
        early_count = sum(1 for e in events if e.adherence_exception and e.adherence_exception.category == "early_out")

        absence_rate = round(absent_count / total_scheduled * 100, 1) if total_scheduled else 0

        # Daily trend
        daily = defaultdict(lambda: {"absent": 0, "late": 0, "early": 0})
        for e in events:
            d_key = e.event_date.isoformat()
            cat = e.adherence_exception.category if e.adherence_exception else "other"
            if cat == "absence":
                daily[d_key]["absent"] += 1
            elif cat == "late":
                daily[d_key]["late"] += 1
            elif cat == "early_out":
                daily[d_key]["early"] += 1

        trend = []
        d = start_d
        while d <= end_d:
            k = d.isoformat()
            trend.append({"date": k, **daily[k]})
            d += timedelta(days=1)

        # Per-employee rows
        emp_events = defaultdict(list)
        for e in events:
            emp_events[e.employee_id].append(e.to_dict())

        rows = []
        for eid, evt_list in emp_events.items():
            emp = emp_map.get(eid)
            if not emp:
                continue
            rows.append({
                "employee_id": eid,
                "employee_name": emp.full_name,
                "ext_id": emp.employee_id,
                "lob": _lob_name(emp),
                "team_lead": emp.team_lead or "",
                "events": sorted(evt_list, key=lambda x: x.get("event_date", "")),
                "absent_count": sum(1 for ev in evt_list if ev.get("category") == "absence"),
                "late_count": sum(1 for ev in evt_list if ev.get("category") == "late"),
                "early_count": sum(1 for ev in evt_list if ev.get("category") == "early_out"),
            })
        rows.sort(key=lambda r: -(r["absent_count"] + r["late_count"] + r["early_count"]))

        return jsonify(success=True, kpi={
            "total_scheduled": total_scheduled,
            "absent_count": absent_count,
            "late_count": late_count,
            "early_count": early_count,
            "absence_rate": absence_rate,
        }, daily_trend=trend, rows=rows)
    except Exception as e:
        log.exception("attendance-coded error")
        return jsonify(success=False, error=str(e))


@reports_bp.route("/api/attendance-coded/csv", methods=["POST"])
@login_required
def attendance_coded_csv():
    """CSV export of coded attendance events."""
    user = _require_admin_or_sup()
    if not user:
        return "Forbidden", 403
    if user.get("is_demo"):
        return jsonify(success=True, message="Demo mode")

    payload = request.get_json(silent=True) or {}
    start_d, end_d, lob, team_lead, employee_id = _parse_filters(payload)
    emps = _filter_employees(start_d, end_d, lob, team_lead, employee_id).all()
    emp_ids = {e.id for e in emps}
    emp_map = {e.id: e for e in emps}

    events = AttendanceEvent.query.filter(
        AttendanceEvent.employee_id.in_(emp_ids),
        AttendanceEvent.event_date >= start_d,
        AttendanceEvent.event_date <= end_d,
    ).order_by(AttendanceEvent.event_date).all() if emp_ids else []

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Date", "Employee", "Employee ID", "LOB", "Team Lead", "Code", "Label", "Category", "Excused", "Note"])
    for e in events:
        emp = emp_map.get(e.employee_id)
        exc = e.adherence_exception
        writer.writerow([
            e.event_date.isoformat(), emp.full_name if emp else "", emp.employee_id if emp else "",
            _lob_name(emp) if emp else "", emp.team_lead if emp else "",
            exc.code if exc else "", exc.label if exc else "", exc.category if exc else "",
            "Yes" if e.is_excused else "No", e.note or "",
        ])
    buf.seek(0)
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=attendance_coded_{date.today().isoformat()}.csv"})


# ── 2. Forecast Accuracy ─────────────────────────────────────

@reports_bp.route("/api/forecast-accuracy", methods=["POST"])
@login_required
def forecast_accuracy():
    """Forecast vs actual volume comparison."""
    user = _require_admin_or_sup()
    if not user:
        return jsonify(success=False, error="Forbidden"), 403
    if user.get("is_demo"):
        from app.demo_data import get_demo_report_forecast_accuracy
        payload = request.get_json(silent=True) or {}
        sd = payload.get("start_date")
        ed = payload.get("end_date")
        return jsonify(get_demo_report_forecast_accuracy(
            date.fromisoformat(sd) if sd else None, date.fromisoformat(ed) if ed else None))

    try:
        payload = request.get_json(silent=True) or {}
        start_d, end_d, lob, team_lead, employee_id = _parse_filters(payload)

        start_dt = datetime.combine(start_d, datetime.min.time())
        end_dt = datetime.combine(end_d + timedelta(days=1), datetime.min.time())

        # For today, cap at the current time so we only compare
        # forecast intervals that have had a chance to get actuals
        from app.utils.timezone import get_app_timezone
        from zoneinfo import ZoneInfo
        tz = get_app_timezone()
        now_local = datetime.now(tz).replace(tzinfo=None)
        today_str = now_local.date().isoformat()
        # Find the latest actual timestamp to use as the cap for today
        now_cap = now_local  # default: current time

        # Build PU filter
        pu_ids = None
        if lob:
            lob_names = [l.strip() for l in lob.split(",") if l.strip()]
            pus = PlanningUnit.query.filter(
                db.func.lower(PlanningUnit.name).in_([n.lower() for n in lob_names])
            ).all()
            pu_ids = [p.id for p in pus] if pus else []

        # Forecast by day — for today, only count intervals up to now
        fc_q = db.session.query(
            func.date(ForecastInterval.timestamp).label("d"),
            func.sum(ForecastInterval.offered).label("vol"),
        ).filter(ForecastInterval.timestamp >= start_dt, ForecastInterval.timestamp < end_dt)
        # Cap today's forecast at current time
        if end_d >= now_local.date():
            fc_q = fc_q.filter(
                db.or_(
                    func.date(ForecastInterval.timestamp) < today_str,
                    ForecastInterval.timestamp <= now_cap,
                )
            )
        if pu_ids is not None:
            fc_q = fc_q.filter(ForecastInterval.planning_unit_id.in_(pu_ids))
        fc_q = fc_q.group_by(func.date(ForecastInterval.timestamp))
        fc_by_day = {str(r.d): float(r.vol or 0) for r in fc_q.all()}

        # Actuals by day
        act_q = db.session.query(
            func.date(IntervalActual.timestamp).label("d"),
            func.sum(IntervalActual.offered).label("vol"),
        ).filter(IntervalActual.timestamp >= start_dt, IntervalActual.timestamp < end_dt)
        if pu_ids is not None:
            act_q = act_q.filter(IntervalActual.planning_unit_id.in_(pu_ids))
        act_q = act_q.group_by(func.date(IntervalActual.timestamp))
        act_by_day = {str(r.d): float(r.vol or 0) for r in act_q.all()}

        all_days = sorted(set(list(fc_by_day.keys()) + list(act_by_day.keys())))

        total_fc = sum(fc_by_day.values())
        total_act = sum(act_by_day.values())
        variance_pct = round((total_act - total_fc) / total_fc * 100, 1) if total_fc else 0

        # Per-day accuracy
        accuracies = []
        trend = []
        for d_str in all_days:
            fc = fc_by_day.get(d_str, 0)
            act = act_by_day.get(d_str, 0)
            acc = round(100 - abs(act - fc) / fc * 100, 1) if fc else None
            if acc is not None:
                accuracies.append(acc)
            trend.append({
                "date": d_str,
                "forecast": round(fc, 1),
                "actual": round(act, 1),
                "accuracy_pct": acc,
            })

        avg_accuracy = round(sum(accuracies) / len(accuracies), 1) if accuracies else None

        # By LOB breakdown
        rows = []
        if pu_ids is not None:
            pus = PlanningUnit.query.filter(PlanningUnit.id.in_(pu_ids)).all()
        else:
            pus = PlanningUnit.query.filter_by(is_active=True).all()
        if pus:
            for pu in pus:
                fc_lob_q = db.session.query(func.coalesce(func.sum(ForecastInterval.offered), 0)).filter(
                    ForecastInterval.planning_unit_id == pu.id,
                    ForecastInterval.timestamp >= start_dt, ForecastInterval.timestamp < end_dt,
                )
                if end_d >= now_local.date():
                    fc_lob_q = fc_lob_q.filter(
                        db.or_(
                            func.date(ForecastInterval.timestamp) < today_str,
                            ForecastInterval.timestamp <= now_cap,
                        )
                    )
                fc_vol = fc_lob_q.scalar()
                act_vol = db.session.query(func.coalesce(func.sum(IntervalActual.offered), 0)).filter(
                    IntervalActual.planning_unit_id == pu.id,
                    IntervalActual.timestamp >= start_dt, IntervalActual.timestamp < end_dt,
                ).scalar()
                fc_f = round(float(fc_vol), 1)
                act_f = round(float(act_vol), 1)
                if fc_f > 0 or act_f > 0:
                    acc = round(100 - abs(act_f - fc_f) / fc_f * 100, 1) if fc_f else None
                    rows.append({
                        "lob": pu.name, "forecast": fc_f, "actual": act_f,
                        "variance": round(act_f - fc_f, 1),
                        "variance_pct": round((act_f - fc_f) / fc_f * 100, 1) if fc_f else 0,
                        "accuracy_pct": acc,
                    })

        return jsonify(success=True, kpi={
            "avg_accuracy_pct": avg_accuracy,
            "total_forecast_volume": round(total_fc, 1),
            "total_actual_volume": round(total_act, 1),
            "volume_variance_pct": variance_pct,
        }, daily_trend=trend, rows=rows)
    except Exception as e:
        log.exception("forecast-accuracy error")
        return jsonify(success=False, error=str(e))


@reports_bp.route("/api/forecast-accuracy/csv", methods=["POST"])
@login_required
def forecast_accuracy_csv():
    """CSV export of forecast accuracy."""
    user = _require_admin_or_sup()
    if not user:
        return "Forbidden", 403
    if user.get("is_demo"):
        return jsonify(success=True, message="Demo mode")

    payload = request.get_json(silent=True) or {}
    start_d, end_d, lob, team_lead, employee_id = _parse_filters(payload)
    start_dt = datetime.combine(start_d, datetime.min.time())
    end_dt = datetime.combine(end_d + timedelta(days=1), datetime.min.time())

    pu_ids = None
    if lob:
        pu = PlanningUnit.query.filter(db.func.lower(PlanningUnit.name) == lob.lower()).first()
        pu_ids = [pu.id] if pu else []

    fc_q = db.session.query(
        func.date(ForecastInterval.timestamp).label("d"),
        func.sum(ForecastInterval.offered).label("vol"),
    ).filter(ForecastInterval.timestamp >= start_dt, ForecastInterval.timestamp < end_dt)
    if pu_ids is not None:
        fc_q = fc_q.filter(ForecastInterval.planning_unit_id.in_(pu_ids))
    fc_by_day = {str(r.d): float(r.vol or 0) for r in fc_q.group_by(func.date(ForecastInterval.timestamp)).all()}

    act_q = db.session.query(
        func.date(IntervalActual.timestamp).label("d"),
        func.sum(IntervalActual.offered).label("vol"),
    ).filter(IntervalActual.timestamp >= start_dt, IntervalActual.timestamp < end_dt)
    if pu_ids is not None:
        act_q = act_q.filter(IntervalActual.planning_unit_id.in_(pu_ids))
    act_by_day = {str(r.d): float(r.vol or 0) for r in act_q.group_by(func.date(IntervalActual.timestamp)).all()}

    all_days = sorted(set(list(fc_by_day.keys()) + list(act_by_day.keys())))

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Date", "Forecast Volume", "Actual Volume", "Variance", "Accuracy %"])
    for d_str in all_days:
        fc = fc_by_day.get(d_str, 0)
        act = act_by_day.get(d_str, 0)
        acc = round(100 - abs(act - fc) / fc * 100, 1) if fc else ""
        writer.writerow([d_str, round(fc, 1), round(act, 1), round(act - fc, 1), acc])
    buf.seek(0)
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=forecast_accuracy_{date.today().isoformat()}.csv"})


# ── 3. Schedule Coverage ─────────────────────────────────────

@reports_bp.route("/api/coverage", methods=["POST"])
@login_required
def coverage_report():
    """Schedule coverage: required vs scheduled staffing."""
    user = _require_admin_or_sup()
    if not user:
        return jsonify(success=False, error="Forbidden"), 403
    if user.get("is_demo"):
        from app.demo_data import get_demo_report_coverage
        payload = request.get_json(silent=True) or {}
        sd = payload.get("start_date")
        ed = payload.get("end_date")
        return jsonify(get_demo_report_coverage(
            date.fromisoformat(sd) if sd else None, date.fromisoformat(ed) if ed else None))

    try:
        payload = request.get_json(silent=True) or {}
        start_d, end_d, lob, team_lead, employee_id = _parse_filters(payload)

        start_dt = datetime.combine(start_d, datetime.min.time())
        end_dt = datetime.combine(end_d + timedelta(days=1), datetime.min.time())

        pu_ids = None
        if lob:
            lob_names = [l.strip() for l in lob.split(",") if l.strip()]
            pus_match = PlanningUnit.query.filter(
                db.func.lower(PlanningUnit.name).in_([n.lower() for n in lob_names])
            ).all()
            pu_ids = [p.id for p in pus_match] if pus_match else []

        # Requirements by day — average agents required per interval
        req_q = db.session.query(
            func.date(RequirementInterval.timestamp).label("d"),
            func.avg(RequirementInterval.agents_required).label("avg_req"),
            func.max(RequirementInterval.agents_required).label("peak_req"),
            func.count().label("intervals"),
        ).filter(RequirementInterval.timestamp >= start_dt, RequirementInterval.timestamp < end_dt)
        if pu_ids is not None:
            req_q = req_q.filter(RequirementInterval.planning_unit_id.in_(pu_ids))
        req_by_day = {str(r.d): {
            "avg_required": round(float(r.avg_req or 0), 1),
            "peak_required": round(float(r.peak_req or 0), 1),
            "intervals": r.intervals,
        } for r in req_q.group_by(func.date(RequirementInterval.timestamp)).all()}

        # Scheduled count by day (number of agents with shifts)
        sched_q = db.session.query(
            Schedule.schedule_date,
            func.count().label("cnt"),
            func.coalesce(func.sum(Schedule.hours), 0).label("hrs"),
        ).filter(
            Schedule.schedule_date >= start_d,
            Schedule.schedule_date <= end_d,
            Schedule.status == "scheduled",
        )
        if pu_ids is not None:
            sched_q = sched_q.filter(Schedule.planning_unit_id.in_(pu_ids))
        sched_q = sched_q.group_by(Schedule.schedule_date)
        sched_by_day = {r.schedule_date.isoformat(): {"scheduled": r.cnt, "hours": float(r.hrs)}
                        for r in sched_q.all()}

        all_days = sorted(set(list(req_by_day.keys()) + list(sched_by_day.keys())))

        daily_data = []
        total_understaffed = 0
        total_overstaffed = 0
        coverages = []
        peak_gap = 0

        for d_str in all_days:
            req_info = req_by_day.get(d_str, {"avg_required": 0, "peak_required": 0, "intervals": 0})
            sched_info = sched_by_day.get(d_str, {"scheduled": 0, "hours": 0})
            avg_req = req_info["avg_required"]
            peak_req = req_info["peak_required"]
            sched_total = sched_info["scheduled"]
            gap = round(sched_total - peak_req, 1)
            # Coverage: scheduled agents vs peak requirement
            cov_pct = round(sched_total / peak_req * 100, 1) if peak_req else (100 if sched_total else 0)

            if gap < 0:
                total_understaffed += 1
            elif gap > 0:
                total_overstaffed += 1
            if abs(gap) > abs(peak_gap):
                peak_gap = gap
            coverages.append(cov_pct)

            daily_data.append({
                "date": d_str,
                "avg_required": avg_req,
                "peak_required": peak_req,
                "scheduled": sched_total,
                "gap": gap,
                "coverage_pct": cov_pct,
                "hours": sched_info["hours"],
            })

        avg_cov = round(sum(coverages) / len(coverages), 1) if coverages else 0

        # By LOB
        rows = []
        if pu_ids is not None:
            lob_pus = PlanningUnit.query.filter(PlanningUnit.id.in_(pu_ids)).all()
        else:
            lob_pus = PlanningUnit.query.filter_by(is_active=True).all()
        if lob_pus:
            for pu in lob_pus:
                # Average and peak required per interval for this LOB
                req_row = db.session.query(
                    func.avg(RequirementInterval.agents_required).label("avg_req"),
                    func.max(RequirementInterval.agents_required).label("peak_req"),
                ).filter(
                    RequirementInterval.planning_unit_id == pu.id,
                    RequirementInterval.timestamp >= start_dt, RequirementInterval.timestamp < end_dt,
                ).first()
                avg_req = round(float(req_row.avg_req or 0), 1) if req_row else 0
                peak_req = round(float(req_row.peak_req or 0), 1) if req_row else 0
                sched_v = Schedule.query.filter(
                    Schedule.planning_unit_id == pu.id,
                    Schedule.schedule_date >= start_d, Schedule.schedule_date <= end_d,
                    Schedule.status == "scheduled",
                ).count()
                if peak_req > 0 or sched_v > 0:
                    gap = round(sched_v - peak_req, 1)
                    rows.append({
                        "lob": pu.name,
                        "avg_required": avg_req,
                        "peak_required": peak_req,
                        "scheduled": sched_v,
                        "gap": gap,
                        "coverage_pct": round(sched_v / peak_req * 100, 1) if peak_req else 100,
                    })

        return jsonify(success=True, kpi={
            "avg_coverage_pct": avg_cov,
            "understaffed_days": total_understaffed,
            "overstaffed_days": total_overstaffed,
            "peak_gap": round(peak_gap, 1),
        }, daily_data=daily_data, rows=rows)
    except Exception as e:
        log.exception("coverage error")
        return jsonify(success=False, error=str(e))


@reports_bp.route("/api/coverage/csv", methods=["POST"])
@login_required
def coverage_csv():
    """CSV export of coverage report."""
    user = _require_admin_or_sup()
    if not user:
        return "Forbidden", 403
    if user.get("is_demo"):
        return jsonify(success=True, message="Demo mode")

    payload = request.get_json(silent=True) or {}
    # Reuse the coverage endpoint logic
    resp = coverage_report()
    data = resp.get_json() if hasattr(resp, 'get_json') else {}
    daily = data.get("daily_data", [])

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Date", "Required", "Scheduled", "Gap", "Coverage %", "Hours"])
    for d in daily:
        writer.writerow([d["date"], d["required"], d["scheduled"], d["gap"], d["coverage_pct"], d.get("hours", "")])
    buf.seek(0)
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=coverage_{date.today().isoformat()}.csv"})


# ── 4. Agent Performance ─────────────────────────────────────

@reports_bp.route("/api/agent-performance", methods=["POST"])
@login_required
def agent_performance():
    """Agent scorecard: attendance + quality + hours."""
    user = _require_admin_or_sup()
    if not user:
        return jsonify(success=False, error="Forbidden"), 403
    if user.get("is_demo"):
        from app.demo_data import get_demo_report_agent_performance
        return jsonify(get_demo_report_agent_performance())

    try:
        payload = request.get_json(silent=True) or {}
        start_d, end_d, lob, team_lead, employee_id = _parse_filters(payload)

        emps = _filter_employees(start_d, end_d, lob, team_lead, employee_id).all()
        emp_ids = {e.id for e in emps}

        # Attendance events per employee
        att_events = AttendanceEvent.query.filter(
            AttendanceEvent.employee_id.in_(emp_ids),
            AttendanceEvent.event_date >= start_d,
            AttendanceEvent.event_date <= end_d,
        ).all() if emp_ids else []

        att_by_emp = defaultdict(lambda: {"absent": 0, "late": 0, "early": 0})
        for e in att_events:
            cat = e.adherence_exception.category if e.adherence_exception else "other"
            if cat == "absence":
                att_by_emp[e.employee_id]["absent"] += 1
            elif cat == "late":
                att_by_emp[e.employee_id]["late"] += 1
            elif cat == "early_out":
                att_by_emp[e.employee_id]["early"] += 1

        # Quality per employee
        qual_by_emp = {}
        try:
            qual_rows = db.session.query(
                QualityEvaluation.employee_id,
                func.avg(QualityEvaluation.overall_score).label("avg"),
                func.count().label("cnt"),
            ).filter(
                QualityEvaluation.employee_id.in_(emp_ids),
                QualityEvaluation.eval_date >= start_d,
                QualityEvaluation.eval_date <= end_d,
            ).group_by(QualityEvaluation.employee_id).all()
            for r in qual_rows:
                qual_by_emp[r.employee_id] = {"avg": round(float(r.avg), 1), "count": r.cnt}
        except Exception:
            pass

        # Hours per employee
        hours_by_emp = {}
        try:
            hrs_rows = db.session.query(
                Schedule.employee_id,
                func.coalesce(func.sum(Schedule.hours), 0).label("hrs"),
                func.count().label("shifts"),
            ).filter(
                Schedule.employee_id.in_(emp_ids),
                Schedule.schedule_date >= start_d,
                Schedule.schedule_date <= end_d,
                Schedule.status == "scheduled",
            ).group_by(Schedule.employee_id).all()
            for r in hrs_rows:
                hours_by_emp[r.employee_id] = {"hours": round(float(r.hrs), 1), "shifts": r.shifts}
        except Exception:
            pass

        rows = []
        for emp in emps:
            att = att_by_emp.get(emp.id, {"absent": 0, "late": 0, "early": 0})
            qual = qual_by_emp.get(emp.id, {"avg": None, "count": 0})
            hrs = hours_by_emp.get(emp.id, {"hours": 0, "shifts": 0})
            total_issues = att["absent"] + att["late"] + att["early"]
            attendance_rate = round(100 - (att["absent"] / hrs["shifts"] * 100), 1) if hrs["shifts"] else 100

            rows.append({
                "employee_id": emp.id,
                "employee_name": emp.full_name,
                "ext_id": emp.employee_id,
                "lob": _lob_name(emp),
                "team_lead": emp.team_lead or "",
                "total_hours": hrs["hours"],
                "total_shifts": hrs["shifts"],
                "absent_count": att["absent"],
                "late_count": att["late"],
                "early_count": att["early"],
                "attendance_rate": attendance_rate,
                "quality_avg": qual["avg"],
                "quality_evals": qual["count"],
            })
        rows.sort(key=lambda r: r["employee_name"])

        # KPI averages
        att_rates = [r["attendance_rate"] for r in rows if r["total_shifts"] > 0]
        qual_scores = [r["quality_avg"] for r in rows if r["quality_avg"] is not None]
        total_hrs = sum(r["total_hours"] for r in rows)

        return jsonify(success=True, kpi={
            "avg_attendance_rate": round(sum(att_rates) / len(att_rates), 1) if att_rates else None,
            "avg_quality": round(sum(qual_scores) / len(qual_scores), 1) if qual_scores else None,
            "total_agents": len(rows),
            "total_hours": round(total_hrs, 1),
        }, rows=rows)
    except Exception as e:
        log.exception("agent-performance error")
        return jsonify(success=False, error=str(e))


@reports_bp.route("/api/agent-performance/csv", methods=["POST"])
@login_required
def agent_performance_csv():
    """CSV export of agent performance."""
    user = _require_admin_or_sup()
    if not user:
        return "Forbidden", 403
    if user.get("is_demo"):
        return jsonify(success=True, message="Demo mode")

    payload = request.get_json(silent=True) or {}
    resp = agent_performance()
    data = resp.get_json() if hasattr(resp, 'get_json') else {}

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Employee", "Employee ID", "LOB", "Team Lead", "Hours", "Shifts",
                     "Absences", "Lates", "Early Leaves", "Attendance Rate %", "Quality Avg", "Quality Evals"])
    for r in data.get("rows", []):
        writer.writerow([r["name"], r["ext_id"], r["lob"], r["team_lead"], r["total_hours"],
                         r["total_shifts"], r["absent_count"], r["late_count"], r["early_count"],
                         r["attendance_rate"], r["quality_avg"] or "", r["quality_evals"]])
    buf.seek(0)
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=agent_performance_{date.today().isoformat()}.csv"})


# ── 5. PTO & Leave ───────────────────────────────────────────

@reports_bp.route("/api/pto-leave", methods=["POST"])
@login_required
def pto_leave():
    """PTO and Leave of Absence report."""
    user = _require_admin_or_sup()
    if not user:
        return jsonify(success=False, error="Forbidden"), 403
    if user.get("is_demo"):
        from app.demo_data import get_demo_report_pto_leave
        payload = request.get_json(silent=True) or {}
        sd = payload.get("start_date")
        ed = payload.get("end_date")
        return jsonify(get_demo_report_pto_leave(
            date.fromisoformat(sd) if sd else None, date.fromisoformat(ed) if ed else None))

    try:
        payload = request.get_json(silent=True) or {}
        start_d, end_d, lob, team_lead, employee_id = _parse_filters(payload)

        emps = _filter_employees(start_d, end_d, lob, team_lead, employee_id).all()
        emp_ids = {e.id for e in emps}
        emp_map = {e.id: e for e in emps}

        # PTO entries
        pto_q = PTOEntry.query.filter(
            PTOEntry.employee_id.in_(emp_ids),
            PTOEntry.start_date <= end_d,
            PTOEntry.end_date >= start_d,
        ) if emp_ids else PTOEntry.query.filter(db.false())

        ptos = pto_q.all()
        approved = sum(1 for p in ptos if p.approval_status == "approved")
        pending = sum(1 for p in ptos if p.approval_status == "pending")
        denied = sum(1 for p in ptos if p.approval_status == "denied")
        days_consumed = sum((min(p.end_date, end_d) - max(p.start_date, start_d)).days + 1
                           for p in ptos if p.approval_status == "approved")

        # By type
        by_type = []
        try:
            from app.models import TimeOffType
            types = TimeOffType.query.all()
            for t in types:
                cnt = sum(1 for p in ptos if p.time_off_type_id == t.id and p.approval_status == "approved")
                if cnt:
                    by_type.append({"type": t.label, "count": cnt})
        except Exception:
            pass

        # Per-employee rows
        emp_pto = defaultdict(list)
        for p in ptos:
            emp_pto[p.employee_id].append({
                "start": p.start_date.isoformat(),
                "end": p.end_date.isoformat(),
                "status": p.approval_status,
                "type": "",
                "days": (p.end_date - p.start_date).days + 1,
            })

        # LOA
        loa_count = 0
        try:
            loas = LeaveOfAbsence.query.filter(
                LeaveOfAbsence.employee_id.in_(emp_ids),
                LeaveOfAbsence.start_date <= end_d,
                ((LeaveOfAbsence.expected_return_date >= start_d) | (LeaveOfAbsence.expected_return_date.is_(None))),
            ).all() if emp_ids else []
            loa_count = len(loas)
            for loa in loas:
                emp_pto[loa.employee_id].append({
                    "start": loa.start_date.isoformat(),
                    "end": (loa.expected_return_date or loa.actual_return_date or end_d).isoformat(),
                    "status": loa.status,
                    "type": f"LOA: {loa.leave_type}",
                    "days": ((loa.expected_return_date or end_d) - loa.start_date).days + 1,
                })
        except Exception:
            pass

        rows = []
        for eid, entries in emp_pto.items():
            emp = emp_map.get(eid)
            if not emp:
                continue
            for entry in entries:
                rows.append({
                    "employee_id": eid,
                    "employee_name": emp.full_name,
                    "ext_id": emp.employee_id,
                    "lob": _lob_name(emp),
                    "type": entry.get("type", ""),
                    "start_date": entry["start"],
                    "end_date": entry["end"],
                    "days": entry["days"],
                    "status": entry["status"],
                })
        rows.sort(key=lambda r: r["start_date"], reverse=True)

        return jsonify(success=True, kpi={
            "total_requests": len(ptos),
            "approved": approved,
            "pending": pending,
            "denied": denied,
            "days_consumed": days_consumed,
            "active_loa": loa_count,
        }, by_type=by_type, rows=rows)
    except Exception as e:
        log.exception("pto-leave error")
        return jsonify(success=False, error=str(e))


@reports_bp.route("/api/pto-leave/csv", methods=["POST"])
@login_required
def pto_leave_csv():
    """CSV export of PTO & Leave."""
    user = _require_admin_or_sup()
    if not user:
        return "Forbidden", 403
    if user.get("is_demo"):
        return jsonify(success=True, message="Demo mode")

    payload = request.get_json(silent=True) or {}
    resp = pto_leave()
    data = resp.get_json() if hasattr(resp, 'get_json') else {}

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Employee", "Employee ID", "LOB", "Total Days", "Total Requests"])
    for r in data.get("rows", []):
        writer.writerow([r["name"], r["ext_id"], r["lob"], r["total_days"], r["total_requests"]])
    buf.seek(0)
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=pto_leave_{date.today().isoformat()}.csv"})
