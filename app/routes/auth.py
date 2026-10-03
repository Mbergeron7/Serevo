"""
Authentication routes — login / logout / initial setup.

Phase 2: DB-backed password auth with bcrypt. Demo mode still auto-seeds
two accounts (supervisor / admin) so the app works out of the box.
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
            email="supervisor@demo.serevo.app",
            password_hash=generate_password_hash("demo").decode("utf-8"),
            display_name="Demo Supervisor",
            role="supervisor",
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
                log.info("Login success: user_id=%s email=%s role=%s", user.id, email, user.role)
                try:
                    from app.audit import audit_log
                    audit_log("login", "user", user.id, f"Login: {email}")
                except Exception:
                    pass
                next_page = request.args.get("next") or url_for("dashboard.index")
                return redirect(next_page)

        log.warning("Login failed: email=%s ip=%s", email, request.remote_addr)
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
            log.info("Setup: admin account created email=%s", email)
            session["user_id"] = user.id
            session.permanent = True
            return redirect(url_for("dashboard.index"))

    return render_template("setup.html", error=error)


@auth_bp.route("/admin-setup", methods=["GET", "POST"])
def admin_setup():
    """
    Create an admin account from the browser — works even when users exist.
    Protected by a secret key passed as ?key=<WFM_SECRET_KEY>.
    """
    from config import cfg
    secret = request.args.get("key", "")
    if not secret or secret != cfg.SECRET_KEY:
        return "Not found", 404

    from app.models import db, User
    from flask_bcrypt import generate_password_hash

    error = None
    done = False

    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        name = (request.form.get("name") or "").strip()
        password = request.form.get("password") or ""

        is_demo_account = request.form.get("is_demo") == "on"

        if not email or not password:
            error = "Email and password are required."
        elif len(password) < 6:
            error = "Password must be at least 6 characters."
        else:
            try:
                existing = User.query.filter_by(email=email).first()
                if existing:
                    existing.password_hash = generate_password_hash(password).decode("utf-8")
                    existing.role = "admin"
                    existing.is_active = True
                    if name:
                        existing.display_name = name
                    try:
                        existing.is_demo = is_demo_account
                        db.session.commit()
                    except Exception:
                        db.session.rollback()
                        # is_demo column may not exist — retry without it
                        existing = User.query.filter_by(email=email).first()
                        existing.password_hash = generate_password_hash(password).decode("utf-8")
                        existing.role = "admin"
                        existing.is_active = True
                        if name:
                            existing.display_name = name
                        db.session.commit()
                    done = True
                else:
                    try:
                        user = User(
                            email=email,
                            password_hash=generate_password_hash(password).decode("utf-8"),
                            display_name=name or email.split("@")[0].title(),
                            role="admin",
                            is_active=True,
                            is_demo=is_demo_account,
                        )
                        db.session.add(user)
                        db.session.commit()
                    except Exception:
                        db.session.rollback()
                        # is_demo column may not exist — retry without it
                        user = User(
                            email=email,
                            password_hash=generate_password_hash(password).decode("utf-8"),
                            display_name=name or email.split("@")[0].title(),
                            role="admin",
                            is_active=True,
                        )
                        db.session.add(user)
                        db.session.commit()
                    done = True
            except Exception as e:
                db.session.rollback()
                error = f"Error creating account: {e}"

    return f"""
    <html><head><title>Admin Setup</title>
    <style>body{{font-family:system-ui;max-width:400px;margin:60px auto;padding:0 16px}}
    input{{width:100%;padding:8px;margin:4px 0 12px;box-sizing:border-box}}
    label.check{{display:flex;align-items:center;gap:8px;margin:8px 0 12px}}
    label.check input{{width:auto}}
    button{{padding:10px 20px;background:#0f766e;color:white;border:none;cursor:pointer;border-radius:4px}}
    .err{{color:red}} .ok{{color:green}}</style></head>
    <body><h2>Admin Account Setup</h2>
    {"<p class='ok'>Account created/updated! <a href='/login'>Go to login</a></p>" if done else ""}
    {"<p class='err'>" + error + "</p>" if error else ""}
    <form method="POST">
    <label>Email</label><input name="email" type="email" required>
    <label>Display Name</label><input name="name" placeholder="Optional">
    <label>Password</label><input name="password" type="password" required>
    <label class="check"><input name="is_demo" type="checkbox"> Demo account (shows sample data)</label>
    <button type="submit">Create Admin Account</button>
    </form></body></html>
    """



@auth_bp.route("/logout")
def logout():
    user_id = session.get("user_id")
    session.clear()
    log.info("Logout: user_id=%s", user_id)
    return redirect(url_for("auth.login"))
