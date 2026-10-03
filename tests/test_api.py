"""Tests for key API endpoints."""

import json
from tests.conftest import login


class TestEmployeeAPI:
    def test_save_employee(self, client, admin_user):
        user, pw = admin_user
        login(client, user.email, pw)

        resp = client.post("/api/employees/save", data=json.dumps({
            "first_name": "Jane",
            "last_name": "Doe",
            "employee_id": "EMP001",
            "status": "Active",
            "lob": "Sales",
            "contract_type": "Full-Time",
            "weekly_hours": 40,
            "days_per_week": 5,
            "hours_per_day": 8,
        }), content_type="application/json")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data.get("success") is True

    def test_save_employee_requires_auth(self, client):
        resp = client.post("/api/employees/save", data=json.dumps({
            "first_name": "Jane",
            "last_name": "Doe",
        }), content_type="application/json", follow_redirects=False)
        # Should redirect to login or return error
        assert resp.status_code in (302, 401, 403)


class TestDataImport:
    def test_upload_requires_auth(self, client):
        resp = client.post("/data/upload", follow_redirects=False)
        assert resp.status_code in (302, 401, 403)

    def test_upload_no_file(self, client, admin_user):
        user, pw = admin_user
        login(client, user.email, pw)
        resp = client.post("/data/upload", data={
            "upload_type": "employees",
        }, content_type="multipart/form-data")
        data = resp.get_json()
        assert data.get("success") is False
        assert "file" in data.get("error", "").lower() or "No file" in data.get("error", "")
