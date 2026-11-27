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

# Rate limiter — uses in-memory storage (suitable for single-instance Render)
try:
    from flask_limiter import Limiter
    from flask_limiter.util import get_remote_address
    limiter = Limiter(
        key_func=get_remote_address,
        default_limits=[],  # no global limit — applied per-route
        storage_uri="memory://",
    )
except ImportError:
    limiter = None

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
    Uses a file lock so only one Gunicorn worker runs the scheduler."""
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

    # File-lock guard: only one worker should run the scheduler
    import fcntl
    lock_path = os.path.join(os.environ.get("TMPDIR", "/tmp"), ".serevo_scheduler.lock")
    try:
        _lock_fd = open(lock_path, "w")
        fcntl.flock(_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Keep fd open — lock released when process dies
        app._scheduler_lock_fd = _lock_fd
    except (IOError, OSError):
        log.info("Scheduler lock held by another worker — skipping")
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
    if app.secret_key == "change-me":
        log.warning("⚠️  WFM_SECRET_KEY is using the default value — set it in your environment for production!")

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
    if limiter:
        limiter.init_app(app)

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
        # Ensure new columns exist on existing tables (db.create_all doesn't add columns)
        _new_columns = [
            ("users", "organization_id", "INTEGER"),
            ("users", "is_superadmin", "BOOLEAN DEFAULT FALSE"),
            ("organizations", "domain", "VARCHAR(200) DEFAULT ''"),
            ("organizations", "logo_url", "VARCHAR(500) DEFAULT ''"),
            ("organizations", "settings_json", "TEXT DEFAULT ''"),
            ("employees", "organization_id", "INTEGER"),
            ("planning_units", "organization_id", "INTEGER"),
            ("data_uploads", "organization_id", "INTEGER"),
            ("data_feeds", "organization_id", "INTEGER"),
            ("data_sources", "organization_id", "INTEGER"),
            ("api_connections", "organization_id", "INTEGER"),
            ("segment_codes", "organization_id", "INTEGER"),
            ("shift_templates", "organization_id", "INTEGER"),
            ("contracts", "organization_id", "INTEGER"),
            ("day_models", "organization_id", "INTEGER"),
            ("employee_quartiles", "organization_id", "INTEGER"),
            ("selections", "organization_id", "INTEGER"),
            ("selection_members", "organization_id", "INTEGER"),
            ("skill_groups", "organization_id", "INTEGER"),
            ("skill_mappings", "organization_id", "INTEGER"),
            ("shift_sequences", "organization_id", "INTEGER"),
            ("shift_sequence_rows", "organization_id", "INTEGER"),
            ("employee_shift_sequences", "organization_id", "INTEGER"),
            ("lob_settings", "organization_id", "INTEGER"),
            ("lob_mappings", "organization_id", "INTEGER"),
            ("holidays", "organization_id", "INTEGER"),
            ("special_days", "organization_id", "INTEGER"),
            ("announcements", "organization_id", "INTEGER"),
            ("wfm_tickets", "organization_id", "INTEGER"),
            ("support_tickets", "organization_id", "INTEGER"),
            ("audit_log", "organization_id", "INTEGER"),
            ("time_off_types", "organization_id", "INTEGER"),
            ("overtime_rules", "organization_id", "INTEGER"),
            ("schedule_rules", "organization_id", "INTEGER"),
            ("alert_configs", "organization_id", "INTEGER"),
            ("brand_settings", "organization_id", "INTEGER"),
            ("adherence_exceptions", "organization_id", "INTEGER"),
            ("schema_mappings", "organization_id", "INTEGER"),
            ("training_modules", "organization_id", "INTEGER"),
            ("shift_notes", "organization_id", "INTEGER"),
            ("roster_groups", "organization_id", "INTEGER"),
        ]
        for table, col, col_type in _new_columns:
            try:
                db.session.execute(db.text(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}"))
                db.session.commit()
                log.info(f"Added column {table}.{col}")
            except Exception:
                db.session.rollback()  # column already exists

    # Seed default organization if none exists
    try:
        from app.models import Organization
        with app.app_context():
            if Organization.query.first() is None:
                default_org = Organization(
                    name=cfg.BRAND_NAME or "Serevo",
                    slug="default",
                    timezone="America/Toronto",
                )
                db.session.add(default_org)
                db.session.commit()
                log.info("Seeded default organization: %s", default_org.name)
    except Exception as e:
        log.warning(f"seed_default_org() failed (may be expected on first run): {e}")
        try:
            db.session.rollback()
        except Exception:
            pass

    # Backfill organization_id — assign all NULL records to the default org
    try:
        with app.app_context():
            from app.models import Organization
            default_org = Organization.query.filter_by(slug="default").first()
            if default_org:
                _backfill_tables = [
                    "employees", "planning_units", "users",
                    "data_uploads", "data_feeds", "data_sources", "api_connections",
                    "segment_codes", "shift_templates", "contracts", "day_models",
                    "employee_quartiles", "selections", "selection_members",
                    "skill_groups", "skill_mappings", "shift_sequences",
                    "shift_sequence_rows", "employee_shift_sequences",
                    "lob_settings", "lob_mappings", "holidays", "special_days",
                    "announcements", "wfm_tickets", "support_tickets", "audit_log",
                    "time_off_types", "overtime_rules", "schedule_rules",
                    "alert_configs", "brand_settings", "adherence_exceptions",
                    "schema_mappings", "training_modules", "shift_notes", "roster_groups",
                ]
                total_updated = 0
                for tbl in _backfill_tables:
                    try:
                        result = db.session.execute(db.text(
                            f"UPDATE {tbl} SET organization_id = :oid WHERE organization_id IS NULL"
                        ), {"oid": default_org.id})
                        count = result.rowcount
                        db.session.commit()
                        if count > 0:
                            log.info(f"Backfill: set organization_id={default_org.id} on {count} rows in {tbl}")
                            total_updated += count
                    except Exception:
                        db.session.rollback()
                if total_updated > 0:
                    log.info(f"Backfill complete: {total_updated} total rows assigned to org '{default_org.name}'")
    except Exception as e:
        log.warning(f"org backfill failed: {e}")
        try:
            db.session.rollback()
        except Exception:
            pass

    # Seed the demo user if it doesn't exist (may fail on first run
    # before the is_demo migration has been applied — that's fine)
    try:
        from app.demo_data import seed_demo_user
        seed_demo_user(app)
    except Exception as e:
        log.warning(f"seed_demo_user() failed (may be expected on first run): {e}")

    # Seed default adherence exception codes
    try:
        from app.routes.attendance import seed_adherence_codes
        seed_adherence_codes(app)
    except Exception as e:
        log.warning(f"seed_adherence_codes() failed: {e}")

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
    from app.routes.organizations import org_bp

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
    app.register_blueprint(org_bp)

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
        agent_bp, audit_bp, rosters_bp, org_bp,
    ]:
        csrf.exempt(bp)

    # ---- rate limiting on sensitive endpoints ----
    if limiter:
        limiter.limit("5 per minute")(app.view_functions["auth.login"])
        limiter.limit("3 per minute")(app.view_functions["auth.setup"])
        limiter.limit("3 per minute")(app.view_functions["auth.admin_setup"])

    # ---- APScheduler: periodic realtime data sync ----
    _start_scheduler(app)

    # ---- error handlers ----
    def _wants_json():
        """True when the caller expects JSON, not an HTML error page."""
        from app.auth import _is_api_request
        return _is_api_request()

    @app.errorhandler(429)
    def rate_limit_exceeded(e):
        if _wants_json():
            return jsonify({"success": False, "error": "Too many requests. Please wait a moment and try again."}), 429
        from flask import render_template
        return "<h2>Too many requests</h2><p>Please wait a moment and try again.</p>", 429

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

    # ---- security headers + prevent browser caching of HTML pages ----
    @app.after_request
    def _security_headers(response):
        # Prevent clickjacking
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        # Prevent MIME-type sniffing
        response.headers["X-Content-Type-Options"] = "nosniff"
        # XSS protection (legacy browsers)
        response.headers["X-XSS-Protection"] = "1; mode=block"
        # Referrer policy
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        # Permissions policy — disable unused browser features
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        # No cache for HTML pages
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
