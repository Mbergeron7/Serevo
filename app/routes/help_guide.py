"""
Help Guide — self-serve documentation for all users.
"""
from flask import Blueprint, render_template
from app.auth import login_required, get_current_user

help_bp = Blueprint("help_guide", __name__, url_prefix="/help")


@help_bp.route("/")
@login_required
def index():
    user = get_current_user()
    return render_template("help/index.html", user=user)
