"""
Serevo — Flask application factory
====================================
Creates and configures the Flask app. All routes are registered via
blueprints (to be added as modules are ported in).
"""

import logging
import sys
from datetime import timedelta
from flask import Flask
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
    """Start APScheduler for the 5-minute Call Potential sync.
    Only runs on the main Gunicorn worker (not in reloader or testing)."""
    global _scheduler_started
    if _scheduler_started:
        return

    import os
    # Don't start in testing, or when explicitly disabled
    if app.testing:
        return
    if os.environ.get("CALLPOTENTIAL_SYNC_ENABLED", "true").lower() != "true":
        log.info("Call Potential sync disabled (CALLPOTENTIAL_SYNC_ENABLED=false)")
        return

    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from app.routes.realtime_sync import run_sync

        scheduler = BackgroundScheduler(daemon=True)
        scheduler.add_job(
            func=run_sync,
            trigger="interval",
            minutes=5,
            id="callpotential_sync",
            kwargs={"app": app},
            replace_existing=True,
            max_instances=1,
        )
        scheduler.start()
        _scheduler_started = True
        log.info("Call Potential auto-sync started (every 5 minutes)")
    except ImportError:
        log.warning("APScheduler not installed — Call Potential auto-sync disabled. "
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
        # Auto-add missing columns to existing tables (works on both Postgres and SQLite)
        _ensure_columns = [
            ("employees", "external_id_1", "VARCHAR(50)"),
            ("employees", "external_id_2", "VARCHAR(50)"),
            ("employees", "contract_id", "INTEGER"),
            ("users", "employee_id", "INTEGER REFERENCES employees(id)"),
            ("pto_entries", "time_off_type_id", "INTEGER REFERENCES time_off_types(id)"),
            ("pto_entries", "approval_status", "VARCHAR(20) DEFAULT 'approved'"),
            ("pto_entries", "requested_by", "INTEGER REFERENCES users(id)"),
            ("pto_entries", "reviewed_by", "INTEGER REFERENCES users(id)"),
            ("pto_entries", "reviewed_at", "TIMESTAMP"),
            ("users", "oauth_provider", "VARCHAR(30)"),
            ("users", "oauth_id", "VARCHAR(255)"),
            ("users", "is_demo", "BOOLEAN DEFAULT FALSE"),
            ("users", "wfm_access", "BOOLEAN DEFAULT FALSE"),
            # ── segment_codes columns added in contract config ──
            ("data_feeds", "service_account_json", "TEXT"),
            ("data_feeds", "column_mapping", "TEXT"),
            ("day_models", "abbreviation", "VARCHAR(20)"),
            ("day_models", "total_hours", "FLOAT"),
            ("day_models", "model_type", "VARCHAR(20) DEFAULT 'Fixed'"),
            ("day_models", "color", "VARCHAR(7) DEFAULT '#4472C4'"),
            ("day_models", "day_type", "VARCHAR(20) DEFAULT 'any'"),
            ("day_models", "planning_unit_id", "INTEGER"),
            ("day_models", "sort_order", "INTEGER DEFAULT 0"),
            ("segment_codes", "activity_type", "VARCHAR(20) DEFAULT 'presence'"),
            ("segment_codes", "activity_category", "VARCHAR(20) DEFAULT 'status'"),
            ("segment_codes", "official_name", "VARCHAR(120)"),
            ("segment_codes", "abbreviation", "VARCHAR(20)"),
            ("segment_codes", "shortcut", "VARCHAR(10)"),
            ("segment_codes", "external_ids", "TEXT"),
            ("segment_codes", "parent_id", "INTEGER REFERENCES segment_codes(id)"),
            ("segment_codes", "is_multi_activity", "BOOLEAN DEFAULT FALSE"),
            ("segment_codes", "is_replaceable", "BOOLEAN DEFAULT TRUE"),
            ("segment_codes", "is_plannable", "BOOLEAN DEFAULT TRUE"),
            ("segment_codes", "importance", "INTEGER DEFAULT 50"),
            ("segment_codes", "priority", "INTEGER DEFAULT 50"),
            ("segment_codes", "comply_rest_period", "BOOLEAN DEFAULT TRUE"),
            ("segment_codes", "allow_overstaffing_zero", "BOOLEAN DEFAULT FALSE"),
            ("segment_codes", "is_requestable", "BOOLEAN DEFAULT FALSE"),
            ("segment_codes", "is_exchangeable", "BOOLEAN DEFAULT FALSE"),
            ("segment_codes", "allow_full_day", "BOOLEAN DEFAULT FALSE"),
            ("segment_codes", "special_handling", "BOOLEAN DEFAULT FALSE"),
            ("segment_codes", "can_be_day_status", "BOOLEAN DEFAULT FALSE"),
            ("segment_codes", "offset_mins", "INTEGER"),
            ("segment_codes", "duration_mins", "INTEGER"),
            ("segment_codes", "is_flexible", "BOOLEAN DEFAULT FALSE"),
            ("segment_codes", "window_start_mins", "INTEGER"),
            ("segment_codes", "window_end_mins", "INTEGER"),
        ]
        dialect = db.engine.dialect.name
        for tbl, col, col_type in _ensure_columns:
            try:
                if dialect == "sqlite":
                    r = db.session.execute(db.text(f"PRAGMA table_info({tbl})"))
                    existing = {row[1] for row in r.fetchall()}
                    has_col = col in existing
                else:
                    r = db.session.execute(db.text(
                        "SELECT 1 FROM information_schema.columns "
                        f"WHERE table_name='{tbl}' AND column_name='{col}'"
                    ))
                    has_col = r.fetchone() is not None
                if not has_col:
                    db.session.execute(db.text(
                        f"ALTER TABLE {tbl} ADD COLUMN {col} {col_type}"
                    ))
                    db.session.commit()
                    log.info(f"Added column {tbl}.{col}")
            except Exception as e:
                db.session.rollback()
                log.warning(f"Could not add {tbl}.{col}: {e}")

        # ── Widen columns that were originally too narrow ──
        _widen_columns = [
            ("employees", "contract_type", "VARCHAR(100)"),
        ]
        if dialect != "sqlite":
            for tbl, col, new_type in _widen_columns:
                try:
                    db.session.execute(db.text(
                        f"ALTER TABLE {tbl} ALTER COLUMN {col} TYPE {new_type}"
                    ))
                    db.session.commit()
                except Exception:
                    db.session.rollback()

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

    # ---- APScheduler: 5-minute Call Potential sync ----
    _start_scheduler(app)

    # ---- error handlers ----
    @app.errorhandler(403)
    def forbidden(e):
        from flask import render_template
        return render_template("403.html"), 403

    @app.errorhandler(404)
    def not_found(e):
        from flask import render_template
        return render_template("404.html"), 404

    @app.errorhandler(500)
    def server_error(e):
        from flask import render_template
        return render_template("500.html"), 500

    # ---- health check ----
    @app.route("/health")
    def health():
        return {"status": "ok", "brand": cfg.BRAND_NAME, "demo": cfg.is_demo}

    return app
