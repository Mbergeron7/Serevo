"""
Session helpers and route protection for Serevo.

Usage:
    from app.auth import login_required, admin_required, get_current_user

    @app.route("/dashboard")
    @login_required
    def dashboard():
        user = get_current_user()
        ...
"""

from functools import wraps
from flask import session, redirect, url_for, request
from config.users import ALLOWED_USERS, ADMIN_USERS, USER_NAMES


def get_current_user():
    """Return a dict for the logged-in user, or None."""
    email = session.get("user_email")
    if not email:
        return None
    return {
        "email": email,
        "name": USER_NAMES.get(email, email.split("@")[0].title()),
        "is_admin": email in ADMIN_USERS,
    }


def login_required(f):
    """Redirect to /login if no active session."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("user_email"):
            return redirect(url_for("auth.login", next=request.path))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    """Redirect to /login if user is not an admin."""
    @wraps(f)
    def decorated(*args, **kwargs):
        email = session.get("user_email")
        if not email:
            return redirect(url_for("auth.login", next=request.path))
        if email not in ADMIN_USERS:
            return redirect(url_for("auth.login"))
        return f(*args, **kwargs)
    return decorated
