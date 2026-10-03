"""Tests for Attendance (clock in/out)."""
from tests.conftest import login


class TestAttendance:
    def test_dashboard_loads(self, client, admin_user):
        user, pw = admin_user
        login(client, user.email, pw)
        resp = client.get("/attendance/")
        assert resp.status_code == 200
        assert b"Attendance" in resp.data

    def test_dashboard_forbidden_for_agent(self, client, app):
        from app.models import db, User
        from flask_bcrypt import generate_password_hash
        with app.app_context():
            agent = User(
                email="attagent@test.com",
                password_hash=generate_password_hash("pass123").decode("utf-8"),
                display_name="Agent", role="agent", is_active=True
            )
            db.session.add(agent)
            db.session.commit()
            email = agent.email
        login(client, email, "pass123")
        resp = client.get("/attendance/")
        assert resp.status_code == 403

    def test_clock_in_requires_employee(self, client, admin_user):
        """Admin user without an employee record can't clock in."""
        user, pw = admin_user
        login(client, user.email, pw)
        resp = client.post("/attendance/api/clock-in",
                           json={}, content_type="application/json")
        data = resp.get_json()
        assert data["success"] is False
        assert "employee" in data["error"].lower()

    def test_clock_in_out_flow(self, client, app, admin_user):
        """Create an employee linked to admin, clock in, then clock out."""
        from app.models import db, Employee
        user, pw = admin_user
        with app.app_context():
            emp = Employee(
                employee_id="ATT001", first_name="Test", last_name="Clock",
                email=user.email, status="Active"
            )
            db.session.add(emp)
            db.session.commit()

        login(client, user.email, pw)

        # Clock in
        resp = client.post("/attendance/api/clock-in",
                           json={}, content_type="application/json")
        data = resp.get_json()
        assert data["success"] is True
        assert "clock_in" in data

        # Can't clock in again
        resp = client.post("/attendance/api/clock-in",
                           json={}, content_type="application/json")
        assert resp.get_json()["success"] is False

        # Clock out
        resp = client.post("/attendance/api/clock-out",
                           json={}, content_type="application/json")
        data = resp.get_json()
        assert data["success"] is True
        assert data["total_hours"] >= 0

    def test_clock_status(self, client, app, admin_user):
        from app.models import db, Employee
        user, pw = admin_user
        with app.app_context():
            emp = Employee(
                employee_id="ATT002", first_name="Status", last_name="Test",
                email=user.email, status="Active"
            )
            db.session.add(emp)
            db.session.commit()

        login(client, user.email, pw)

        # Not clocked in
        resp = client.post("/attendance/api/status",
                           json={}, content_type="application/json")
        data = resp.get_json()
        assert data["success"] is True
        assert data["clocked_in"] is False

    def test_dashboard_api(self, client, admin_user):
        user, pw = admin_user
        login(client, user.email, pw)
        resp = client.post("/attendance/api/dashboard",
                           json={"date": "2026-10-03"},
                           content_type="application/json")
        data = resp.get_json()
        assert data["success"] is True
        assert "stats" in data
        assert "rows" in data
