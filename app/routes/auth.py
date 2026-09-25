"""
Authentication routes — login / logout / initial setup.

Phase 2: DB-backed password auth with bcrypt. Demo mode still auto-seeds
two accounts (viewer / admin) so the app works out of the box.
"""

import logging
from flask import Blueprint, render_template, request, redirect, session, url_for, flash
from config import cfg

log = logging.getLogger("serevo.auth")

auth_bp = Blueprint("auth", __name__)


def _ensure_demo_accounts():
    """Seed demo accounts if DEMO_MODE is on and no users exist yet."""
    if not cfg.is_demo:
        return
    try:
        from app.models import db, User
        from flask_bcrypt import generate_password_hash
        if User.query.first() is not None:
            return  # already have users
        demo_admin = User(
            email="admin@demo.serevo.app",
            password_hash=generate_password_hash("demo").decode("utf-8"),
            display_name="Demo Admin",
            role="admin",
        )
        demo_viewer = User(
            email="viewer@demo.serevo.app",
            password_hash=generate_password_hash("demo").decode("utf-8"),
            display_name="Demo Viewer",
            role="viewer",
        )
        db.session.add_all([demo_admin, demo_viewer])
        db.session.commit()
        log.info("Seeded demo accounts")
    except Exception as e:
        log.warning("Could not seed demo accounts: %s", e)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    _ensure_demo_accounts()
    error = None

    if request.method == "POST":
        from app.models import User
        from flask_bcrypt import check_password_hash

        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""

        user = User.query.filter_by(email=email).first()

        if user and user.is_active and user.password_hash:
            if check_password_hash(user.password_hash, password):
                session.clear()
                session["user_id"] = user.id
                session.permanent = True
                next_page = request.args.get("next") or url_for("dashboard.index")
                return redirect(next_page)

        error = "Invalid email or password."

    # Check if any users exist (for first-time setup prompt)
    try:
        from app.models import User
        has_users = User.query.first() is not None
    except Exception:
        has_users = True  # assume yes if DB error

    return render_template("login.html", error=error, has_users=has_users)


@auth_bp.route("/setup", methods=["GET", "POST"])
def setup():
    """First-time admin account creation. Only works when no users exist."""
    from app.models import db, User
    from flask_bcrypt import generate_password_hash

    if User.query.first() is not None:
        return redirect(url_for("auth.login"))

    error = None
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        name = (request.form.get("name") or "").strip()
        password = request.form.get("password") or ""
        confirm = request.form.get("confirm") or ""

        if not email or not password:
            error = "Email and password are required."
        elif len(password) < 6:
            error = "Password must be at least 6 characters."
        elif password != confirm:
            error = "Passwords don't match."
        else:
            user = User(
                email=email,
                password_hash=generate_password_hash(password).decode("utf-8"),
                display_name=name or email.split("@")[0].title(),
                role="admin",
            )
            db.session.add(user)
            db.session.commit()
            session["user_id"] = user.id
            session.permanent = True
            return redirect(url_for("dashboard.index"))

    return render_template("setup.html", error=error)


@auth_bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
