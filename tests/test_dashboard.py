"""Tests for the dashboard route."""
from tests.conftest import login


class TestDashboard:
    def test_dashboard_requires_login(self, client):
        resp = client.get("/")
        assert resp.status_code in (302, 401)

    def test_dashboard_loads_for_admin(self, client, admin_user):
        user, pw = admin_user
        login(client, user.email, pw)
        resp = client.get("/")
        assert resp.status_code == 200

    def test_dashboard_redirects_agent(self, client, agent_user):
        user, pw = agent_user
        login(client, user.email, pw)
        resp = client.get("/")
        # Agents may be redirected to their own view
        assert resp.status_code in (200, 302)


class TestErrorPages:
    def test_404(self, client, admin_user):
        user, pw = admin_user
        login(client, user.email, pw)
        resp = client.get("/nonexistent-page-xyz")
        assert resp.status_code == 404
