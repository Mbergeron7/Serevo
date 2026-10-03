"""Tests for the audit log module."""
from tests.conftest import login


class TestAuditPage:
    def test_audit_page_loads_for_admin(self, client, admin_user):
        user, pw = admin_user
        login(client, user.email, pw)
        resp = client.get("/audit-log/")
        assert resp.status_code == 200
        assert b"Audit Log" in resp.data

    def test_audit_page_forbidden_for_agent(self, client, agent_user):
        user, pw = agent_user
        login(client, user.email, pw)
        resp = client.get("/audit-log/")
        assert resp.status_code == 403

    def test_audit_page_requires_login(self, client):
        resp = client.get("/audit-log/")
        assert resp.status_code in (302, 401)


class TestAuditAPI:
    def test_list_entries_returns_login(self, client, admin_user):
        user, pw = admin_user
        login(client, user.email, pw)
        resp = client.post("/audit-log/api/entries",
                           json={"days": 7}, content_type="application/json")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        # Login itself creates an audit entry
        assert data["total"] >= 1

    def test_list_entries_with_data(self, client, admin_user, app):
        user, pw = admin_user
        login(client, user.email, pw)

        # Create an audit entry
        from app.models import db, AuditLog
        entry = AuditLog(
            user_id=user.id,
            action="create",
            entity_type="employee",
            entity_id=1,
            detail="Created employee",
        )
        db.session.add(entry)
        db.session.commit()

        resp = client.post("/audit-log/api/entries",
                           json={"days": 7}, content_type="application/json")
        data = resp.get_json()
        assert data["success"] is True
        assert data["total"] >= 2  # login + our entry
        actions = [e["action"] for e in data["entries"]]
        assert "create" in actions

    def test_list_entries_filter_by_action(self, client, admin_user, app):
        user, pw = admin_user
        login(client, user.email, pw)

        from app.models import db, AuditLog
        for action in ("create", "update"):
            db.session.add(AuditLog(
                user_id=user.id,
                action=action, entity_type="user",
            ))
        db.session.commit()

        resp = client.post("/audit-log/api/entries",
                           json={"days": 7, "action": "create"},
                           content_type="application/json")
        data = resp.get_json()
        assert data["total"] >= 1
        assert all(e["action"] == "create" for e in data["entries"])

    def test_list_entries_forbidden_for_agent(self, client, agent_user):
        user, pw = agent_user
        login(client, user.email, pw)
        resp = client.post("/audit-log/api/entries",
                           json={"days": 7}, content_type="application/json")
        assert resp.status_code == 403
