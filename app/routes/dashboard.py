"""
Dashboard — the landing page after login.

Stub for now; will be built out with real data views.
"""

from flask import Blueprint, render_template
from app.auth import login_required, get_current_user

dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.route("/")
@login_required
def index():
    user = get_current_user()
    return render_template("dashboard.html", user=user)
