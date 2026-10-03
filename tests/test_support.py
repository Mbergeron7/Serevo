"""Tests for the support ticket system."""
from tests.conftest import login


class TestSupportPage:
    def test_support_loads_for_admin(self, client, admin_user):
        user, pw = admin_user
        login(client, user.email, pw)
        resp = client.get("/support/")
        assert resp.status_code == 200
        assert b"Support Tickets" in resp.data

    def test_support_loads_for_agent(self, client, agent_user):
        user, pw = agent_user
        login(client, user.email, pw)
        resp = client.get("/support/")
        assert resp.status_code == 200

    def test_support_requires_login(self, client):
        resp = client.get("/support/")
        assert resp.status_code in (302, 401)


class TestSupportAPI:
    def test_create_ticket(self, client, admin_user):
        user, pw = admin_user
        login(client, user.email, pw)
        resp = client.post("/support/api/tickets/create", json={
            "subject": "Test issue",
            "description": "Something is broken",
            "category": "bug",
            "priority": "high",
        }, content_type="application/json")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["ticket"]["subject"] == "Test issue"
        assert data["ticket"]["status"] == "open"

    def test_create_ticket_missing_fields(self, client, admin_user):
        user, pw = admin_user
        login(client, user.email, pw)
        resp = client.post("/support/api/tickets/create", json={
            "subject": "",
            "description": "",
        }, content_type="application/json")
        assert resp.status_code == 400

    def test_list_tickets(self, client, admin_user):
        user, pw = admin_user
        login(client, user.email, pw)
        # Create a ticket first
        client.post("/support/api/tickets/create", json={
            "subject": "List test", "description": "Testing list",
        }, content_type="application/json")
        resp = client.post("/support/api/tickets", json={},
                           content_type="application/json")
        data = resp.get_json()
        assert data["success"] is True
        assert len(data["tickets"]) >= 1

    def test_agent_sees_only_own_tickets(self, client, admin_user, agent_user):
        # Admin creates a ticket
        user, pw = admin_user
        login(client, user.email, pw)
        client.post("/support/api/tickets/create", json={
            "subject": "Admin ticket", "description": "Admin issue",
        }, content_type="application/json")
        client.get("/logout")

        # Agent creates a ticket
        auser, apw = agent_user
        login(client, auser.email, apw)
        client.post("/support/api/tickets/create", json={
            "subject": "Agent ticket", "description": "Agent issue",
        }, content_type="application/json")
        resp = client.post("/support/api/tickets", json={},
                           content_type="application/json")
        data = resp.get_json()
        # Agent should only see their own ticket
        for t in data["tickets"]:
            assert t["submitted_by"] == auser.id

    def test_add_comment(self, client, admin_user):
        user, pw = admin_user
        login(client, user.email, pw)
        resp = client.post("/support/api/tickets/create", json={
            "subject": "Comment test", "description": "Testing comments",
        }, content_type="application/json")
        ticket_id = resp.get_json()["ticket"]["id"]

        resp = client.post("/support/api/tickets/comment", json={
            "ticket_id": ticket_id, "body": "This is a comment",
        }, content_type="application/json")
        data = resp.get_json()
        assert data["success"] is True
        assert data["comment"]["body"] == "This is a comment"

    def test_update_ticket_status(self, client, admin_user):
        user, pw = admin_user
        login(client, user.email, pw)
        resp = client.post("/support/api/tickets/create", json={
            "subject": "Status test", "description": "Testing update",
        }, content_type="application/json")
        ticket_id = resp.get_json()["ticket"]["id"]

        resp = client.post("/support/api/tickets/update", json={
            "id": ticket_id, "status": "resolved",
            "resolution": "Fixed the thing",
        }, content_type="application/json")
        data = resp.get_json()
        assert data["success"] is True
        assert data["ticket"]["status"] == "resolved"
        assert data["ticket"]["resolution"] == "Fixed the thing"


class TestHelpPage:
    def test_help_loads_for_admin(self, client, admin_user):
        user, pw = admin_user
        login(client, user.email, pw)
        resp = client.get("/help/")
        assert resp.status_code == 200
        assert b"Help Guide" in resp.data

    def test_help_loads_for_agent(self, client, agent_user):
        user, pw = agent_user
        login(client, user.email, pw)
        resp = client.get("/help/")
        assert resp.status_code == 200

    def test_help_requires_login(self, client):
        resp = client.get("/help/")
        assert resp.status_code in (302, 401)
