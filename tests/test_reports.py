"""Tests for the reports & analytics page."""
from tests.conftest import login


class TestReportsPage:
    def test_reports_page_loads(self, client, admin_user):
        user, pw = admin_user
        login(client, user.email, pw)
        resp = client.get("/reports/")
        assert resp.status_code == 200

    def test_reports_redirects_agent(self, client, app):
        from app.models import db, User
        from flask_bcrypt import generate_password_hash
        agent = User(
            email="agent@test.com",
            password_hash=generate_password_hash("pass123").decode("utf-8"),
            display_name="Test Agent", role="agent", is_active=True,
        )
        db.session.add(agent)
        db.session.commit()
        login(client, agent.email, "pass123")
        resp = client.get("/reports/")
        assert resp.status_code == 302


class TestReportsAPI:
    def test_summary_api(self, client, admin_user):
        user, pw = admin_user
        login(client, user.email, pw)
        resp = client.post("/reports/api/summary",
                           json={"days": 30}, content_type="application/json")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert "headcount" in data
        assert "schedule" in data
        assert "pto" in data
        assert "quality" in data
        assert "self_service" in data

    def test_summary_different_periods(self, client, admin_user):
        user, pw = admin_user
        login(client, user.email, pw)
        for days in [7, 14, 30, 60, 90]:
            resp = client.post("/reports/api/summary",
                               json={"days": days}, content_type="application/json")
            assert resp.status_code == 200
            assert resp.get_json()["success"] is True
