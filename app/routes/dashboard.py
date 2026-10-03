"""
Dashboard — the landing page after login.

Shows live stats (employee count, planning units, schedules today, PTO today)
and a getting-started guide when no data has been loaded yet.
"""

from datetime import date

from flask import Blueprint, render_template, redirect
from app.auth import login_required, get_current_user
from app.models import Employee, PlanningUnit, Schedule, PTOEntry

dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.route("/")
@login_required
def index():
    user = get_current_user()
    # Agents go straight to their schedule
    if user and user.get("is_agent"):
        return redirect("/my-schedule/")
    demo_stats = None
    live_stats = None
    if user and user.get("is_demo"):
        from app.demo_data import get_demo_dashboard_stats
        demo_stats = get_demo_dashboard_stats()
    else:
        today = date.today()
        live_stats = {
            "total_employees": Employee.query.count(),
            "planning_units": PlanningUnit.query.filter_by(is_active=True).count(),
            "on_today": Schedule.query.filter_by(schedule_date=today).count(),
            "pto_today": PTOEntry.query.filter(
                PTOEntry.start_date <= today,
                PTOEntry.end_date >= today,
            ).count(),
        }
    return render_template("dashboard.html", user=user, demo_stats=demo_stats, live_stats=live_stats)
