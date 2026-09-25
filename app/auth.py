"""
Session helpers and route protection for Serevo.

Phase 2: reads from the User database table instead of a flat allow-list.

Usage:
    from app.auth import login_required, admin_required, get_current_user
"""

from functools import wraps
from flask import session, redirect, url_for, request


def get_current_user():
    """Return a dict for the logged-in user, or None."""
    user_id = session.get("user_id")
    if not user_id:
        return None
    try:
        from app.models import User
        u = User.query.get(user_id)
        if not u or not u.is_active:
            return None
        return {
            "id": u.id,
            "email": u.email,
            "name": u.display_name or u.email.split("@")[0].title(),
            "is_admin": u.role == "admin",
            "role": u.role,
        }
    except Exception:
        return None


def login_required(f):
    """Redirect to /login if no active session."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not get_current_user():
            return redirect(url_for("auth.login", next=request.path))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    """Redirect to / if user is not an admin."""
    @wraps(f)
    def decorated(*args, **kwargs):
        user = get_current_user()
        if not user:
            return redirect(url_for("auth.login", next=request.path))
        if not user["is_admin"]:
            return redirect("/")
        return f(*args, **kwargs)
    return decorated
