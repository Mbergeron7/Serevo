"""
Serevo — Flask application factory
====================================
Creates and configures the Flask app. All routes are registered via
blueprints (to be added as modules are ported in).
"""

from flask import Flask
from config import cfg


def create_app():
    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
    )
    app.secret_key = cfg.SECRET_KEY

    # ---- database ----
    app.config["SQLALCHEMY_DATABASE_URI"] = cfg.SQLALCHEMY_DATABASE_URI
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = cfg.SQLALCHEMY_TRACK_MODIFICATIONS

    from app.models import db
    db.init_app(app)

    from flask_migrate import Migrate
    Migrate(app, db)

    # ---- inject brand variables into every template ----
    @app.context_processor
    def inject_brand():
        return cfg.brand_context

    # ---- inject current user into every template ----
    @app.context_processor
    def inject_user():
        from app.auth import get_current_user
        user = get_current_user()
        return {"current_user": user}

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

    # ---- health check ----
    @app.route("/health")
    def health():
        return {"status": "ok", "brand": cfg.BRAND_NAME, "demo": cfg.is_demo}

    return app
