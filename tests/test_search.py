"""Tests for global search."""
from tests.conftest import login


class TestSearch:
    def test_search_requires_login(self, client):
        rv = client.post("/search/api/query", json={"q": "test"})
        assert rv.status_code in (302, 401)

    def test_search_short_query(self, client, admin_user):
        user, pw = admin_user
        login(client, user.email, pw)
        rv = client.post("/search/api/query", json={"q": "a"})
        d = rv.get_json()
        assert d["success"] is True
        assert d["results"] == []

    def test_search_nav_links(self, client, admin_user):
        user, pw = admin_user
        login(client, user.email, pw)
        rv = client.post("/search/api/query", json={"q": "Scheduling"})
        d = rv.get_json()
        assert d["success"] is True
        pages = [r for r in d["results"] if r["type"] == "page"]
        assert any("Scheduling" in p["title"] for p in pages)

    def test_search_employees(self, client, admin_user, app):
        user, pw = admin_user
        login(client, user.email, pw)
        from app.models import db, Employee, PlanningUnit
        with app.app_context():
            pu = PlanningUnit(name="TestUnit")
            db.session.add(pu)
            db.session.flush()
            emp = Employee(first_name="Jane", last_name="Doe",
                           employee_id="E999", email="jane@test.com",
                           planning_unit_id=pu.id, status="Active")
            db.session.add(emp)
            db.session.commit()

        rv = client.post("/search/api/query", json={"q": "Jane"})
        d = rv.get_json()
        assert d["success"] is True
        emps = [r for r in d["results"] if r["type"] == "employee"]
        assert len(emps) == 1
        assert emps[0]["title"] == "Jane Doe"
