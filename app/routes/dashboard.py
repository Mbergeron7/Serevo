"""
Dashboard — the landing page after login.

Stub for now; will be built out with real data views.
"""

from flask import Blueprint, render_template, redirect
from app.auth import login_required, get_current_user

dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.route("/")
@login_required
def index():
    user = get_current_user()
    # Agents go straight to their schedule
    if user and user.get("is_agent"):
        return redirect("/my-schedule/")
    demo_stats = None
    if user and user.get("is_demo"):
        from app.demo_data import get_demo_dashboard_stats
        demo_stats = get_demo_dashboard_stats()
    return render_template("dashboard.html", user=user, demo_stats=demo_stats)
