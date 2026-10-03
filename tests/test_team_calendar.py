"""Tests for Team Calendar."""
from tests.conftest import login


class TestTeamCalendar:
    def test_page_loads(self, client, admin_user):
        user, pw = admin_user
        login(client, user.email, pw)
        resp = client.get("/team-calendar/")
        assert resp.status_code == 200
        assert b"Team Calendar" in resp.data

    def test_forbidden_for_agent(self, client, app):
        from app.models import db, User
        from flask_bcrypt import generate_password_hash
        with app.app_context():
            agent = User(
                email="tcagent@test.com",
                password_hash=generate_password_hash("pass123").decode("utf-8"),
                display_name="Agent", role="agent", is_active=True
            )
            db.session.add(agent)
            db.session.commit()
            email = agent.email
        login(client, email, "pass123")
        resp = client.get("/team-calendar/")
        assert resp.status_code == 403

    def test_api_data_returns_grid(self, client, admin_user):
        user, pw = admin_user
        login(client, user.email, pw)
        resp = client.post("/team-calendar/api/data",
                           json={"week_start": "2026-10-05"},
                           content_type="application/json")
        data = resp.get_json()
        assert data["success"] is True
        assert len(data["dates"]) == 7
        assert len(data["day_labels"]) == 7
        assert "daily_counts" in data
        assert "total_employees" in data

    def test_api_data_default_week(self, client, admin_user):
        user, pw = admin_user
        login(client, user.email, pw)
        resp = client.post("/team-calendar/api/data",
                           json={},
                           content_type="application/json")
        data = resp.get_json()
        assert data["success"] is True
        assert len(data["dates"]) == 7
