"""
Serevo — Flask application factory
====================================
Creates and configures the Flask app. All routes are registered via
blueprints (to be added as modules are ported in).
"""

import logging
from datetime import timedelta
from flask import Flask
from flask_bcrypt import Bcrypt
from config import cfg

log = logging.getLogger("serevo.app")

bcrypt = Bcrypt()


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
    app.config["SESSION_COOKIE_SECURE"] = not cfg.is_demo  # HTTPS in prod
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=8)

    # ---- database ----
    app.config["SQLALCHEMY_DATABASE_URI"] = cfg.SQLALCHEMY_DATABASE_URI
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = cfg.SQLALCHEMY_TRACK_MODIFICATIONS

    from app.models import db
    db.init_app(app)
    bcrypt.init_app(app)

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
            except Exception:
                db.session.rollback()
        # Auto-add missing columns to existing tables
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
        ]
        for tbl, col, col_type in _ensure_columns:
            try:
                r = db.session.execute(db.text(
                    "SELECT 1 FROM information_schema.columns "
                    f"WHERE table_name='{tbl}' AND column_name='{col}'"
                ))
                if not r.fetchone():
                    db.session.execute(db.text(
                        f"ALTER TABLE {tbl} ADD COLUMN {col} {col_type}"
                    ))
                    db.session.commit()
                    log.info(f"Added column {tbl}.{col}")
            except Exception as e:
                db.session.rollback()
                log.warning(f"Could not add {tbl}.{col}: {e}")

        # Ensure is_demo column exists on users table
        try:
            result = db.session.execute(db.text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'users' AND column_name = 'is_demo'"
            ))
            has_col = result.fetchone() is not None
            if not has_col:
                db.session.execute(db.text(
                    "ALTER TABLE users ADD COLUMN is_demo BOOLEAN DEFAULT FALSE"
                ))
                db.session.commit()
                log.info("Added is_demo column to users table")
            else:
                log.info("is_demo column already exists")
        except Exception as e:
            db.session.rollback()
            log.warning("Could not ensure is_demo column: %s", e)

    # Seed the demo user if it doesn't exist (may fail on first run
    # before the is_demo migration has been applied — that's fine)
    try:
        from app.demo_data import seed_demo_user
        seed_demo_user(app)
    except Exception:
        pass

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
        return {"current_user": user, "is_demo": is_user_demo or cfg.is_demo}

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

    # ---- error handlers ----
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
