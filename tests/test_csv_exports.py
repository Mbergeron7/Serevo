"""Tests for CSV export endpoints on reports."""
from tests.conftest import login


class TestCSVExports:
    def test_summary_csv(self, client, admin_user):
        user, pw = admin_user
        login(client, user.email, pw)
        resp = client.post("/reports/api/summary/csv",
                           json={"days": 30}, content_type="application/json")
        assert resp.status_code == 200
        assert resp.content_type == "text/csv; charset=utf-8"
        text = resp.data.decode()
        assert "Metric,Value" in text
        assert "Active Employees" in text

    def test_attendance_csv(self, client, admin_user):
        user, pw = admin_user
        login(client, user.email, pw)
        resp = client.post("/reports/api/attendance/csv",
                           json={"days": 30}, content_type="application/json")
        assert resp.status_code == 200
        assert resp.content_type == "text/csv; charset=utf-8"
        text = resp.data.decode()
        assert "Employee" in text

    def test_csv_forbidden_for_agent(self, client, agent_user):
        user, pw = agent_user
        login(client, user.email, pw)
        resp = client.post("/reports/api/summary/csv",
                           json={"days": 30}, content_type="application/json")
        assert resp.status_code == 403

    def test_attendance_csv_forbidden_for_agent(self, client, agent_user):
        user, pw = agent_user
        login(client, user.email, pw)
        resp = client.post("/reports/api/attendance/csv",
                           json={"days": 30}, content_type="application/json")
        assert resp.status_code == 403
