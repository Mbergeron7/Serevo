"""
Authentication routes — login / logout.

Demo mode uses a simple email allow-list stored in config/users.py.
Production would swap this for OAuth / SSO.
"""

from flask import Blueprint, render_template, request, redirect, session, url_for
from config.users import ALLOWED_USERS, USER_NAMES

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    error = None

    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()

        if email in ALLOWED_USERS:
            session["user_email"] = email
            session["user_name"] = USER_NAMES.get(
                email, email.split("@")[0].title()
            )
            next_page = request.args.get("next") or url_for("dashboard.index")
            return redirect(next_page)

        error = "That email isn't recognised. Try a demo account."

    return render_template("login.html", error=error)


@auth_bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
