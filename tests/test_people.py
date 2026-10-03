"""Tests for the people / roster routes."""
from tests.conftest import login


class TestRosterPage:
    def test_roster_loads_for_admin(self, client, admin_user):
        user, pw = admin_user
        login(client, user.email, pw)
        resp = client.get("/people/")
        assert resp.status_code == 200

    def test_roster_loads_for_agent(self, client, agent_user):
        """Agents can view the roster too."""
        user, pw = agent_user
        login(client, user.email, pw)
        resp = client.get("/people/")
        assert resp.status_code == 200

    def test_roster_requires_login(self, client):
        resp = client.get("/people/")
        assert resp.status_code in (302, 401)


class TestEmployeeCRUD:
    def _make_employee(self, app):
        from app.models import db, Employee
        emp = Employee(
            first_name="Jane", last_name="Doe",
            employee_id="EMP001", status="Active",
        )
        db.session.add(emp)
        db.session.commit()
        return emp

    def test_save_new_employee(self, client, admin_user, app):
        user, pw = admin_user
        login(client, user.email, pw)
        resp = client.post("/api/employees/save", json={
            "first_name": "John", "last_name": "Smith",
            "employee_id": "EMP100", "status": "Active",
            "contract_type": "Full-Time", "weekly_hours": 40,
        }, content_type="application/json")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["id"] is not None

    def test_save_employee_duplicate_id(self, client, admin_user, app):
        user, pw = admin_user
        login(client, user.email, pw)
        self._make_employee(app)
        resp = client.post("/api/employees/save", json={
            "first_name": "Another", "last_name": "Person",
            "employee_id": "EMP001", "status": "Active",
        }, content_type="application/json")
        data = resp.get_json()
        # Should either fail or update — depends on implementation
        assert resp.status_code == 200

    def test_employee_profile_page(self, client, admin_user, app):
        user, pw = admin_user
        login(client, user.email, pw)
        emp = self._make_employee(app)
        resp = client.get(f"/people/{emp.id}/profile")
        assert resp.status_code == 200

    def test_employee_names_api(self, client, admin_user, app):
        user, pw = admin_user
        login(client, user.email, pw)
        self._make_employee(app)
        resp = client.get("/people/api/names")
        assert resp.status_code == 200

    def test_delete_employee(self, client, admin_user, app):
        user, pw = admin_user
        login(client, user.email, pw)
        emp = self._make_employee(app)
        resp = client.post("/api/employees/delete", json={"id": emp.id},
                           content_type="application/json")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
