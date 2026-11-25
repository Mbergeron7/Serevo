"""
Dashboard — the landing page after login.

Shows live stats (employee count, planning units, schedules today, PTO today)
and a getting-started guide when no data has been loaded yet.
"""

from datetime import date

from flask import Blueprint, render_template, redirect, session
from app.auth import get_current_user
from app.models import Employee, PlanningUnit, Schedule, PTOEntry, ShiftBid, ShiftSwapRequest, CallRoute, IntervalActual, AppSetting

dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.route("/")
def index():
    user = get_current_user()
    # Show landing page for visitors who aren't logged in
    if not user:
        from flask import current_app
        return render_template("landing.html")
    # Agents go straight to their schedule
    if user and user.get("is_agent"):
        return redirect("/portal/")
    demo_stats = None
    live_stats = None
    if user and user.get("is_demo"):
        from app.demo_data import get_demo_dashboard_stats
        demo_stats = get_demo_dashboard_stats()
    else:
        from app.models import ForecastInterval
        today = date.today()
        try:
            emp_count = Employee.query.count()
            pu_count = len(PlanningUnit.get_all_active())
            from app.models import TimeClock
            live_stats = {
                "total_employees": emp_count,
                "planning_units": pu_count,
                "on_today": Schedule.query.filter_by(schedule_date=today).count(),
                "pto_today": PTOEntry.query.filter(
                    PTOEntry.start_date <= today,
                    PTOEntry.end_date >= today,
                ).count(),
                "clocked_in": TimeClock.query.filter_by(status="active").count(),
            }
            # Setup progress for getting-started checklist
            setup = {
                "has_units": pu_count > 0,
                "has_employees": emp_count > 0,
                "has_sheets": bool(AppSetting.get("google_sheet_key")),
                "has_routes": CallRoute.query.first() is not None,
                "has_forecast": ForecastInterval.query.first() is not None,
                "has_history": IntervalActual.query.filter(IntervalActual.timestamp < today).first() is not None,
                "has_schedules": Schedule.query.first() is not None,
            }
            # Today's coverage stats
            coverage_stats = {"avg_pct": None, "at_risk": []}
            try:
                from app.scheduling.engine import analyze_coverage, coverage_summary
                from app.data_source import get_source
                sheet = None
                try:
                    src = get_source()
                    sheet = src._sheet if hasattr(src, '_sheet') else None
                except Exception:
                    pass
                lobs_with_schedules = set()
                today_schedules = Schedule.query.filter_by(schedule_date=today).filter(
                    Schedule.employee_id.isnot(None)
                ).all()
                for s in today_schedules:
                    if s.planning_unit_id:
                        pu_obj = PlanningUnit.query.get(s.planning_unit_id)
                        if pu_obj:
                            lobs_with_schedules.add(pu_obj.name)
                    elif s.employee and s.employee.planning_unit_id:
                        pu_obj = PlanningUnit.query.get(s.employee.planning_unit_id)
                        if pu_obj:
                            lobs_with_schedules.add(pu_obj.name)
                if lobs_with_schedules:
                    all_cov = []
                    for lob_name in lobs_with_schedules:
                        lob_shifts = [{"start": s.shift_start.strftime("%H:%M") if s.shift_start else "09:00",
                                       "end": s.shift_end.strftime("%H:%M") if s.shift_end else "17:00",
                                       "segments": []}
                                      for s in today_schedules
                                      if (s.planning_unit and s.planning_unit.name == lob_name) or
                                         (s.employee and s.employee.planning_unit and s.employee.planning_unit.name == lob_name)]
                        if lob_shifts:
                            cov = analyze_coverage(lob_name, today, lob_shifts, sheet)
                            summ = coverage_summary(cov)
                            if summ.get("total_intervals", 0) > 0:
                                pct = summ.get("avg_coverage_pct", 0)
                                all_cov.append(pct)
                                if pct < 90:
                                    coverage_stats["at_risk"].append(lob_name)
                    if all_cov:
                        coverage_stats["avg_pct"] = round(sum(all_cov) / len(all_cov), 1)
            except Exception:
                import logging
                logging.getLogger("serevo.dashboard").debug("Coverage calc failed", exc_info=True)

            # Pending approvals for admin/supervisor
            pending = {}
            if user and user.get("role") in ("admin", "supervisor"):
                pending = {
                    "pto": PTOEntry.query.filter_by(approval_status="pending").count(),
                    "bids": ShiftBid.query.filter_by(status="pending").count(),
                    "swaps": ShiftSwapRequest.query.filter(
                        ShiftSwapRequest.status.in_(["pending", "accepted"])
                    ).count(),
                }
            # Upcoming PTO (next 7 days)
            from datetime import timedelta
            week_end = today + timedelta(days=7)
            upcoming_pto = PTOEntry.query.filter(
                PTOEntry.start_date >= today,
                PTOEntry.start_date <= week_end,
                PTOEntry.approval_status == "approved",
            ).count()
        except Exception:
            import logging
            logging.getLogger("serevo.dashboard").exception("Failed to load dashboard stats")
            live_stats = {"total_employees": 0, "planning_units": 0, "on_today": 0, "pto_today": 0, "clocked_in": 0}
            setup = {"has_units": False, "has_employees": False, "has_sheets": False, "has_routes": False, "has_forecast": False, "has_history": False, "has_schedules": False}
            pending = {}
            upcoming_pto = 0
            coverage_stats = {"avg_pct": None, "at_risk": []}
    return render_template("dashboard.html", user=user, demo_stats=demo_stats,
                           live_stats=live_stats, setup=setup if not demo_stats else None,
                           pending=pending if not demo_stats else {},
                           upcoming_pto=upcoming_pto if not demo_stats else 0,
                           coverage_stats=coverage_stats if not demo_stats else {"avg_pct": 94.2, "at_risk": []})
