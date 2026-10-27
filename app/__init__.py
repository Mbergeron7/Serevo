"""
Serevo — Flask application factory
====================================
Creates and configures the Flask app. All routes are registered via
blueprints (to be added as modules are ported in).
"""

import logging
import sys
from datetime import timedelta
from flask import Flask, jsonify
from flask_bcrypt import Bcrypt
from flask_wtf.csrf import CSRFProtect
from config import cfg

# Configure root serevo logger — ensures all serevo.* logs go to stdout
_serevo_logger = logging.getLogger("serevo")
if not _serevo_logger.handlers:
    _handler = logging.StreamHandler(sys.stdout)
    _handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    _serevo_logger.addHandler(_handler)
    _serevo_logger.setLevel(logging.INFO)

log = logging.getLogger("serevo.app")

bcrypt = Bcrypt()
csrf = CSRFProtect()

_scheduler_started = False


def _start_scheduler(app):
    """Start APScheduler for the periodic realtime data sync.
    Only runs on the main Gunicorn worker (not in reloader or testing)."""
    global _scheduler_started
    if _scheduler_started:
        return

    import os
    # Don't start in testing, or when explicitly disabled
    if app.testing:
        return
    if os.environ.get("REALTIME_SYNC_ENABLED", os.environ.get("CALLPOTENTIAL_SYNC_ENABLED", "true")).lower() != "true":
        log.info("Realtime sync disabled (REALTIME_SYNC_ENABLED=false)")
        return

    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from app.routes.realtime_sync import run_sync

        scheduler = BackgroundScheduler(daemon=True)
        scheduler.add_job(
            func=run_sync,
            trigger="interval",
            minutes=2,
            id="realtime_sync",
            kwargs={"app": app},
            replace_existing=True,
            max_instances=1,
        )
        scheduler.start()
        _scheduler_started = True
        log.info("Realtime auto-sync started (every 2 minutes)")
    except ImportError:
        log.warning("APScheduler not installed — realtime auto-sync disabled. "
                     "Install with: pip install APScheduler")
    except Exception as e:
        log.warning(f"Could not start scheduler: {e}")


def create_app():
    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
    )
    app.secret_key = cfg.SECRET_KEY

    # ---- session security ----
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = cfg.DATABASE_URL.startswith("postgresql")  # HTTPS in prod
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=8)

    # ---- database ----
    app.config["SQLALCHEMY_DATABASE_URI"] = cfg.SQLALCHEMY_DATABASE_URI
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = cfg.SQLALCHEMY_TRACK_MODIFICATIONS
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "pool_pre_ping": True,      # test connections before use, discard stale ones
        "pool_recycle": 300,         # recycle connections every 5 min to avoid SSL drops
    }

    from app.models import db
    db.init_app(app)
    bcrypt.init_app(app)
    csrf.init_app(app)

    from flask_migrate import Migrate
    Migrate(app, db)

    # Ensure all tables exist (fallback if migrations haven't run)
    with app.app_context():
        try:
            db.create_all()
        except Exception as e:
            log.warning(f"db.create_all() error (rolling back): {e}")
            db.session.rollback()
            # Retry once after rollback
            try:
                db.create_all()
            except Exception as e2:
                log.warning(f"db.create_all() retry also failed: {e2}")
                db.session.rollback()
        # All columns are now managed by Flask-Migrate / Alembic migrations.
        # The legacy _ensure_columns block was removed — run
        # `flask db upgrade` to apply any pending schema changes.

    # Seed the demo user if it doesn't exist (may fail on first run
    # before the is_demo migration has been applied — that's fine)
    try:
        from app.demo_data import seed_demo_user
        seed_demo_user(app)
    except Exception as e:
        log.warning(f"seed_demo_user() failed (may be expected on first run): {e}")

    # ---- inject brand variables into every template ----
    @app.context_processor
    def inject_brand():
        return cfg.brand_context

    # ---- inject constants into every template ----
    @app.context_processor
    def inject_constants():
        from app.constants import DEFAULT_TIMEZONE
        return {"DEFAULT_TIMEZONE": DEFAULT_TIMEZONE}

    # ---- inject current user into every template ----
    @app.context_processor
    def inject_user():
        from app.auth import get_current_user
        user = get_current_user()
        # Override is_demo if the logged-in user is a demo account
        is_user_demo = bool(user and user.get("is_demo"))
        from app.routes.oauth import is_oauth_enabled
        return {"current_user": user, "is_demo": is_user_demo or cfg.is_demo,
                "oauth_enabled": is_oauth_enabled()}

    # ---- register blueprints ----
    from app.routes.auth import auth_bp
    from app.routes.dashboard import dashboard_bp
    from app.routes.capacity import capacity_bp
    from app.routes.people import people_bp
    from app.routes.scheduling import scheduling_bp
    from app.routes.forecasting import forecasting_bp
    from app.routes.realtime import realtime_bp
    from app.routes.data_import import data_import_bp
    from app.routes.settings import settings_bp
    from app.routes.manual_entry import manual_entry_bp
    from app.routes.agent import agent_bp
    from app.routes.quality import quality_bp
    from app.routes.oauth import oauth_bp
    from app.routes.notifications import notif_bp
    from app.routes.approvals import approvals_bp
    from app.routes.reports import reports_bp
    from app.routes.search import search_bp
    from app.routes.team_calendar import team_cal_bp
    from app.routes.attendance import attendance_bp
    from app.routes.announcements import announce_bp
    from app.routes.payroll import payroll_bp
    from app.routes.shift_notes import shift_notes_bp
    from app.routes.employee_docs import employee_docs_bp
    from app.routes.training import training_bp
    from app.routes.audit import audit_bp
    from app.routes.support import support_bp
    from app.routes.help_guide import help_bp
    from app.routes.wfm_tickets import wfm_tickets_bp
    from app.routes.scheduling_config import scheduling_config_bp
    from app.routes.realtime_sync import realtime_sync_bp
    from app.routes.rosters import rosters_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(capacity_bp)
    app.register_blueprint(people_bp)
    app.register_blueprint(scheduling_bp)
    app.register_blueprint(forecasting_bp)
    app.register_blueprint(realtime_bp)
    app.register_blueprint(data_import_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(manual_entry_bp)
    app.register_blueprint(agent_bp)
    app.register_blueprint(quality_bp)
    app.register_blueprint(oauth_bp)
    app.register_blueprint(notif_bp)
    app.register_blueprint(approvals_bp)
    app.register_blueprint(reports_bp)
    app.register_blueprint(search_bp)
    app.register_blueprint(team_cal_bp)
    app.register_blueprint(attendance_bp)
    app.register_blueprint(announce_bp)
    app.register_blueprint(payroll_bp)
    app.register_blueprint(shift_notes_bp)
    app.register_blueprint(employee_docs_bp)
    app.register_blueprint(training_bp)
    app.register_blueprint(audit_bp)
    app.register_blueprint(support_bp)
    app.register_blueprint(help_bp)
    app.register_blueprint(wfm_tickets_bp)
    app.register_blueprint(scheduling_config_bp)
    app.register_blueprint(realtime_sync_bp)
    app.register_blueprint(rosters_bp)

    # Exempt blueprints whose POST endpoints are called via fetch() with
    # JSON payloads.  CSRF tokens are unnecessary for JSON requests —
    # browsers enforce CORS on non-simple Content-Types, blocking
    # cross-site forgery at the browser level.  Session-cookie auth
    # still protects every endpoint.
    for bp in [
        scheduling_bp, realtime_bp, capacity_bp, people_bp,
        forecasting_bp, data_import_bp, settings_bp, manual_entry_bp,
        quality_bp, approvals_bp, reports_bp, team_cal_bp,
        attendance_bp, announce_bp, payroll_bp, shift_notes_bp,
        employee_docs_bp, training_bp, support_bp, wfm_tickets_bp,
        scheduling_config_bp, realtime_sync_bp, notif_bp, search_bp,
        agent_bp, audit_bp, rosters_bp,
    ]:
        csrf.exempt(bp)

    # ---- APScheduler: periodic realtime data sync ----
    _start_scheduler(app)

    # ---- error handlers ----
    def _wants_json():
        """True when the caller expects JSON, not an HTML error page."""
        from app.auth import _is_api_request
        return _is_api_request()

    @app.errorhandler(400)
    def bad_request(e):
        if _wants_json():
            return jsonify({"success": False, "error": "Bad request"}), 400
        from flask import render_template
        return render_template("404.html"), 400

    @app.errorhandler(403)
    def forbidden(e):
        if _wants_json():
            return jsonify({"success": False, "error": "Forbidden"}), 403
        from flask import render_template
        return render_template("403.html"), 403

    @app.errorhandler(404)
    def not_found(e):
        if _wants_json():
            return jsonify({"success": False, "error": "Not found"}), 404
        from flask import render_template
        return render_template("404.html"), 404

    @app.errorhandler(500)
    def server_error(e):
        import traceback
        detail = str(e)
        try:
            detail = traceback.format_exc()
        except Exception:
            pass
        log.error(f"500 error: {detail}")
        if _wants_json():
            return jsonify({"success": False, "error": f"Internal server error: {e}"}), 500
        from flask import render_template
        return render_template("500.html"), 500

    # ---- prevent browser caching of HTML pages ----
    @app.after_request
    def _no_cache_html(response):
        if response.content_type and "text/html" in response.content_type:
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

    # ---- health check ----
    @app.route("/health")
    def health():
        return {"status": "ok", "brand": cfg.BRAND_NAME, "demo": cfg.is_demo}

    # ---- global DB session cleanup ----
    @app.teardown_appcontext
    def _shutdown_session(exc):
        """Roll back the DB session after any failed request so a
        PendingRollbackError cannot poison later queries on the same
        worker connection."""
        if exc is not None:
            try:
                db.session.rollback()
            except Exception:
                pass

    return app
