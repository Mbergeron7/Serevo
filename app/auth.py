"""
Session helpers and route protection for Serevo.

Usage:
    from app.auth import login_required, admin_required, agent_required, get_current_user
"""

from functools import wraps
from flask import session, redirect, url_for, request


# Demo accounts are identified by email — no database column needed.
DEMO_EMAILS = {"demo@serevo.app", "admin@demo.serevo.app", "supervisor@demo.serevo.app"}


def _is_demo_user(user_obj):
    """Check if a user is a demo account by email or DB flag."""
    if user_obj.email in DEMO_EMAILS:
        return True
    # Fall back to DB column if it exists
    try:
        return bool(user_obj.is_demo)
    except Exception:
        return False


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
        emp_id = None
        try:
            emp_id = u.employee_id
        except Exception:
            pass
        return {
            "id": u.id,
            "email": u.email,
            "name": u.display_name or u.email.split("@")[0].title(),
            "is_admin": u.role == "admin",
            "is_supervisor": u.role == "supervisor",
            "is_agent": u.role == "agent",
            "role": u.role,
            "employee_id": emp_id,
            "is_demo": _is_demo_user(u),
            "wfm_access": getattr(u, "wfm_access", False) or False,
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


def agent_required(f):
    """Redirect to / if user is not an agent linked to an employee."""
    @wraps(f)
    def decorated(*args, **kwargs):
        user = get_current_user()
        if not user:
            return redirect(url_for("auth.login", next=request.path))
        if not user.get("is_agent") or not user.get("employee_id"):
            return redirect("/")
        return f(*args, **kwargs)
    return decorated


def supervisor_required(f):
    """Allow admin or supervisor roles, not agents."""
    @wraps(f)
    def decorated(*args, **kwargs):
        user = get_current_user()
        if not user:
            return redirect(url_for("auth.login", next=request.path))
        if user.get("is_agent"):
            return redirect("/my-schedule/")
        return f(*args, **kwargs)
    return decorated
