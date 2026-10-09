"""
Dashboard — the landing page after login.

Shows live stats (employee count, planning units, schedules today, PTO today)
and a getting-started guide when no data has been loaded yet.
"""

from datetime import date

from flask import Blueprint, render_template, redirect, session
from app.auth import get_current_user
from app.models import Employee, PlanningUnit, Schedule, PTOEntry, ShiftBid, ShiftSwapRequest

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
        return redirect("/my-schedule/")
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
            pu_count = PlanningUnit.query.filter_by(is_active=True).count()
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
                "has_forecast": ForecastInterval.query.first() is not None,
                "has_schedules": Schedule.query.first() is not None,
            }
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
            live_stats = {"total_employees": 0, "planning_units": 0, "on_today": 0, "pto_today": 0}
            setup = {"has_units": False, "has_employees": False, "has_forecast": False, "has_schedules": False}
            pending = {}
            upcoming_pto = 0
    return render_template("dashboard.html", user=user, demo_stats=demo_stats,
                           live_stats=live_stats, setup=setup if not demo_stats else None,
                           pending=pending if not demo_stats else {},
                           upcoming_pto=upcoming_pto if not demo_stats else 0)
