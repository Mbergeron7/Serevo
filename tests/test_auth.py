"""Tests for authentication routes."""

from tests.conftest import login


class TestLogin:
    def test_login_page_loads(self, client):
        resp = client.get("/login")
        assert resp.status_code == 200
        assert b"Sign in" in resp.data or b"sign in" in resp.data.lower()

    def test_login_success(self, client, admin_user):
        user, pw = admin_user
        resp = client.post("/login", data={
            "email": user.email,
            "password": pw,
        }, follow_redirects=False)
        assert resp.status_code == 302  # redirect to dashboard

    def test_login_wrong_password(self, client, admin_user):
        user, _ = admin_user
        resp = client.post("/login", data={
            "email": user.email,
            "password": "wrongpass",
        })
        assert b"Invalid" in resp.data

    def test_login_nonexistent_user(self, client):
        resp = client.post("/login", data={
            "email": "nobody@test.com",
            "password": "anything",
        })
        assert b"Invalid" in resp.data

    def test_logout(self, client, admin_user):
        user, pw = admin_user
        login(client, user.email, pw)
        resp = client.get("/logout", follow_redirects=False)
        assert resp.status_code == 302
        # After logout, root shows landing page (not dashboard)
        resp2 = client.get("/", follow_redirects=False)
        assert resp2.status_code == 200
        assert b"Smarter scheduling" in resp2.data


class TestSetup:
    def _clear_users(self):
        from app.models import db, User
        User.query.delete()
        db.session.commit()

    def test_setup_page_loads_when_no_users(self, client):
        self._clear_users()
        resp = client.get("/setup")
        assert resp.status_code == 200
        assert b"Create" in resp.data

    def test_setup_redirects_when_users_exist(self, client, admin_user):
        resp = client.get("/setup", follow_redirects=False)
        assert resp.status_code == 302

    def test_setup_creates_admin(self, client):
        self._clear_users()
        resp = client.post("/setup", data={
            "email": "new@test.com",
            "name": "New Admin",
            "password": "securepass",
            "confirm": "securepass",
        }, follow_redirects=False)
        assert resp.status_code == 302  # redirect to dashboard

        from app.models import User
        user = User.query.filter_by(email="new@test.com").first()
        assert user is not None
        assert user.role == "admin"

    def test_setup_password_mismatch(self, client):
        self._clear_users()
        resp = client.post("/setup", data={
            "email": "new@test.com",
            "name": "New Admin",
            "password": "securepass",
            "confirm": "different",
        })
        assert b"match" in resp.data.lower()


class TestAccessControl:
    def test_root_shows_landing_for_visitors(self, client):
        resp = client.get("/", follow_redirects=False)
        assert resp.status_code == 200
        assert b"Smarter scheduling" in resp.data

    def test_people_requires_login(self, client):
        resp = client.get("/people/", follow_redirects=False)
        assert resp.status_code == 302

    def test_admin_can_access_settings(self, client, admin_user):
        user, pw = admin_user
        login(client, user.email, pw)
        resp = client.get("/settings/")
        assert resp.status_code == 200

    def test_agent_cannot_access_settings(self, client, agent_user):
        user, pw = agent_user
        login(client, user.email, pw)
        resp = client.get("/settings/", follow_redirects=False)
        # Should redirect or return 403
        assert resp.status_code in (302, 403)
