"""
Shared test fixtures for Serevo.
Uses an in-memory SQLite database so tests are fast and isolated.
"""

import os
import pytest

# Force test config before any app imports
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["DEMO_MODE"] = "false"
os.environ["WFM_SECRET_KEY"] = "test-secret-key"
os.environ["DATA_SOURCE"] = "generic"


@pytest.fixture()
def app():
    """Create a fresh app instance with an in-memory DB for each test."""
    # Re-import to pick up env overrides
    from config import cfg
    cfg.SQLALCHEMY_DATABASE_URI = "sqlite://"
    cfg.DATABASE_URL = "sqlite://"
    cfg.DEMO_MODE = False

    from app import create_app
    application = create_app()
    application.config["TESTING"] = True
    application.config["WTF_CSRF_ENABLED"] = False  # disable CSRF in tests
    application.config["SQLALCHEMY_DATABASE_URI"] = "sqlite://"

    with application.app_context():
        from app.models import db
        db.create_all()
        yield application
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    """Flask test client."""
    return app.test_client()


@pytest.fixture()
def admin_user(app):
    """Create and return an admin user. Returns (user, password)."""
    from app.models import db, User
    from flask_bcrypt import generate_password_hash

    user = User(
        email="admin@test.com",
        password_hash=generate_password_hash("password123").decode("utf-8"),
        display_name="Test Admin",
        role="admin",
        is_active=True,
    )
    db.session.add(user)
    db.session.commit()
    return user, "password123"


@pytest.fixture()
def agent_user(app):
    """Create and return an agent user. Returns (user, password)."""
    from app.models import db, User
    from flask_bcrypt import generate_password_hash

    user = User(
        email="agent@test.com",
        password_hash=generate_password_hash("password123").decode("utf-8"),
        display_name="Test Agent",
        role="agent",
        is_active=True,
    )
    db.session.add(user)
    db.session.commit()
    return user, "password123"


def login(client, email, password):
    """Helper to log in via POST."""
    return client.post("/login", data={
        "email": email,
        "password": password,
    }, follow_redirects=True)
