"""Tests for session security configuration."""
from tests.conftest import login


class TestSessionSecurity:
    def test_session_cookie_httponly(self, app):
        assert app.config["SESSION_COOKIE_HTTPONLY"] is True

    def test_session_cookie_samesite(self, app):
        assert app.config["SESSION_COOKIE_SAMESITE"] == "Lax"

    def test_session_cookie_secure_false_for_sqlite(self, app):
        # SQLite URL doesn't start with 'postgresql', so Secure should be False
        assert app.config["SESSION_COOKIE_SECURE"] is False

    def test_permanent_session_lifetime(self, app):
        from datetime import timedelta
        assert app.config["PERMANENT_SESSION_LIFETIME"] == timedelta(hours=8)

    def test_session_becomes_permanent_on_login(self, client, admin_user):
        user, pw = admin_user
        with client.session_transaction() as sess:
            assert not sess.get("_permanent", False)
        login(client, user.email, pw)
        with client.session_transaction() as sess:
            assert sess.permanent is True


class TestSettingsPage:
    def test_settings_loads_for_admin(self, client, admin_user):
        user, pw = admin_user
        login(client, user.email, pw)
        resp = client.get("/settings/")
        assert resp.status_code == 200

    def test_settings_forbidden_for_agent(self, client, agent_user):
        user, pw = agent_user
        login(client, user.email, pw)
        resp = client.get("/settings/")
        # Settings may redirect or return 403
        assert resp.status_code in (302, 403)
