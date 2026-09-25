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

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)

    # ---- health check ----
    @app.route("/health")
    def health():
        return {"status": "ok", "brand": cfg.BRAND_NAME, "demo": cfg.is_demo}

    return app
