"""Tests for the WFM ticketing system."""
from tests.conftest import login


class TestWfmTicketPage:
    def test_wfm_loads_for_admin(self, client, admin_user):
        user, pw = admin_user
        login(client, user.email, pw)
        resp = client.get("/wfm-tickets/")
        assert resp.status_code == 200
        assert b"WFM Tickets" in resp.data

    def test_wfm_blocked_for_agent_without_access(self, client, agent_user):
        user, pw = agent_user
        login(client, user.email, pw)
        resp = client.get("/wfm-tickets/")
        assert resp.status_code == 403

    def test_wfm_loads_for_agent_with_access(self, client, agent_user, app):
        user, pw = agent_user
        from app.models import db, User
        with app.app_context():
            u = User.query.get(user.id)
            u.wfm_access = True
            db.session.commit()
        login(client, user.email, pw)
        resp = client.get("/wfm-tickets/")
        assert resp.status_code == 200

    def test_wfm_requires_login(self, client):
        resp = client.get("/wfm-tickets/")
        assert resp.status_code in (302, 401)


class TestWfmTicketAPI:
    def _grant_wfm(self, app, user):
        from app.models import db, User
        with app.app_context():
            u = User.query.get(user.id)
            u.wfm_access = True
            db.session.commit()

    def test_create_ticket(self, client, admin_user):
        user, pw = admin_user
        login(client, user.email, pw)
        resp = client.post("/wfm-tickets/api/tickets/create", json={
            "subject": "Schedule change needed",
            "description": "Need to move Agent X to morning shift",
            "category": "schedule_change",
            "priority": "high",
        }, content_type="application/json")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["ticket"]["subject"] == "Schedule change needed"
        assert data["ticket"]["category"] == "schedule_change"
        assert data["ticket"]["status"] == "open"

    def test_create_ticket_missing_fields(self, client, admin_user):
        user, pw = admin_user
        login(client, user.email, pw)
        resp = client.post("/wfm-tickets/api/tickets/create", json={
            "subject": "", "description": "",
        }, content_type="application/json")
        assert resp.status_code == 400

    def test_create_ticket_with_affected_date(self, client, admin_user):
        user, pw = admin_user
        login(client, user.email, pw)
        resp = client.post("/wfm-tickets/api/tickets/create", json={
            "subject": "OT request",
            "description": "Need OT for next Monday",
            "category": "overtime",
            "affected_date": "2026-10-05",
        }, content_type="application/json")
        data = resp.get_json()
        assert data["success"] is True
        assert data["ticket"]["affected_date"] == "2026-10-05"

    def test_list_tickets(self, client, admin_user):
        user, pw = admin_user
        login(client, user.email, pw)
        client.post("/wfm-tickets/api/tickets/create", json={
            "subject": "List test", "description": "Testing list",
        }, content_type="application/json")
        resp = client.post("/wfm-tickets/api/tickets", json={},
                           content_type="application/json")
        data = resp.get_json()
        assert data["success"] is True
        assert len(data["tickets"]) >= 1

    def test_agent_sees_only_own_tickets(self, client, admin_user, agent_user, app):
        # Admin creates a ticket
        user, pw = admin_user
        login(client, user.email, pw)
        client.post("/wfm-tickets/api/tickets/create", json={
            "subject": "Admin WFM ticket", "description": "Admin issue",
        }, content_type="application/json")
        client.get("/logout")

        # Grant agent WFM access and create a ticket
        auser, apw = agent_user
        self._grant_wfm(app, auser)
        login(client, auser.email, apw)
        client.post("/wfm-tickets/api/tickets/create", json={
            "subject": "Agent WFM ticket", "description": "Agent issue",
        }, content_type="application/json")
        resp = client.post("/wfm-tickets/api/tickets", json={},
                           content_type="application/json")
        data = resp.get_json()
        for t in data["tickets"]:
            assert t["submitted_by"] == auser.id

    def test_filter_by_status(self, client, admin_user):
        user, pw = admin_user
        login(client, user.email, pw)
        client.post("/wfm-tickets/api/tickets/create", json={
            "subject": "Filter test", "description": "Testing filter",
        }, content_type="application/json")
        resp = client.post("/wfm-tickets/api/tickets",
                           json={"status": "open"},
                           content_type="application/json")
        data = resp.get_json()
        assert data["success"] is True
        for t in data["tickets"]:
            assert t["status"] == "open"

    def test_get_single_ticket(self, client, admin_user):
        user, pw = admin_user
        login(client, user.email, pw)
        resp = client.post("/wfm-tickets/api/tickets/create", json={
            "subject": "Single test", "description": "Get one ticket",
        }, content_type="application/json")
        ticket_id = resp.get_json()["ticket"]["id"]

        resp = client.get(f"/wfm-tickets/api/tickets/{ticket_id}")
        data = resp.get_json()
        assert data["success"] is True
        assert data["ticket"]["id"] == ticket_id

    def test_update_ticket_status(self, client, admin_user):
        user, pw = admin_user
        login(client, user.email, pw)
        resp = client.post("/wfm-tickets/api/tickets/create", json={
            "subject": "Status test", "description": "Testing update",
        }, content_type="application/json")
        ticket_id = resp.get_json()["ticket"]["id"]

        resp = client.post("/wfm-tickets/api/tickets/update", json={
            "id": ticket_id, "status": "resolved",
            "resolution": "Schedule updated",
        }, content_type="application/json")
        data = resp.get_json()
        assert data["success"] is True
        assert data["ticket"]["status"] == "resolved"
        assert data["ticket"]["resolution"] == "Schedule updated"

    def test_add_comment(self, client, admin_user):
        user, pw = admin_user
        login(client, user.email, pw)
        resp = client.post("/wfm-tickets/api/tickets/create", json={
            "subject": "Comment test", "description": "Testing comments",
        }, content_type="application/json")
        ticket_id = resp.get_json()["ticket"]["id"]

        resp = client.post("/wfm-tickets/api/tickets/comment", json={
            "ticket_id": ticket_id, "body": "Working on this",
        }, content_type="application/json")
        data = resp.get_json()
        assert data["success"] is True
        assert data["comment"]["body"] == "Working on this"

    def test_internal_comment_hidden_from_agent(self, client, admin_user, agent_user, app):
        user, pw = admin_user
        login(client, user.email, pw)
        # Create ticket as admin
        resp = client.post("/wfm-tickets/api/tickets/create", json={
            "subject": "Internal comment test", "description": "Test",
        }, content_type="application/json")
        ticket_id = resp.get_json()["ticket"]["id"]

        # Add internal comment as analyst
        client.post("/wfm-tickets/api/tickets/comment", json={
            "ticket_id": ticket_id, "body": "Secret analyst note",
            "is_internal": True,
        }, content_type="application/json")

        # Add regular comment
        client.post("/wfm-tickets/api/tickets/comment", json={
            "ticket_id": ticket_id, "body": "Public reply",
        }, content_type="application/json")

        # Reassign ticket to agent so they can view it
        from app.models import db, WfmTicket
        with app.app_context():
            t = WfmTicket.query.get(ticket_id)
            auser, apw = agent_user
            t.submitted_by = auser.id
            db.session.commit()

        # Grant agent WFM access
        self._grant_wfm(app, auser)
        client.get("/logout")
        login(client, auser.email, apw)

        resp = client.get(f"/wfm-tickets/api/tickets/{ticket_id}")
        data = resp.get_json()
        # Agent should not see internal comment
        bodies = [c["body"] for c in data["comments"]]
        assert "Public reply" in bodies
        assert "Secret analyst note" not in bodies

    def test_agent_cannot_access_others_ticket(self, client, admin_user, agent_user, app):
        user, pw = admin_user
        login(client, user.email, pw)
        resp = client.post("/wfm-tickets/api/tickets/create", json={
            "subject": "Private ticket", "description": "Admin only",
        }, content_type="application/json")
        ticket_id = resp.get_json()["ticket"]["id"]
        client.get("/logout")

        auser, apw = agent_user
        self._grant_wfm(app, auser)
        login(client, auser.email, apw)
        resp = client.get(f"/wfm-tickets/api/tickets/{ticket_id}")
        assert resp.status_code == 403


class TestWfmAccessToggle:
    def test_toggle_wfm_access(self, client, admin_user, agent_user):
        user, pw = admin_user
        login(client, user.email, pw)
        auser, _ = agent_user
        resp = client.post("/wfm-tickets/api/users/wfm-access", json={
            "user_id": auser.id, "grant": True,
        }, content_type="application/json")
        data = resp.get_json()
        assert data["success"] is True
        assert data["wfm_access"] is True

    def test_revoke_wfm_access(self, client, admin_user, agent_user):
        user, pw = admin_user
        login(client, user.email, pw)
        auser, _ = agent_user
        # Grant then revoke
        client.post("/wfm-tickets/api/users/wfm-access", json={
            "user_id": auser.id, "grant": True,
        }, content_type="application/json")
        resp = client.post("/wfm-tickets/api/users/wfm-access", json={
            "user_id": auser.id, "grant": False,
        }, content_type="application/json")
        data = resp.get_json()
        assert data["wfm_access"] is False

    def test_agent_cannot_toggle_wfm(self, client, agent_user):
        user, pw = agent_user
        login(client, user.email, pw)
        resp = client.post("/wfm-tickets/api/users/wfm-access", json={
            "user_id": user.id, "grant": True,
        }, content_type="application/json")
        assert resp.status_code == 403

    def test_list_wfm_users(self, client, admin_user):
        user, pw = admin_user
        login(client, user.email, pw)
        resp = client.post("/wfm-tickets/api/users/wfm-list", json={},
                           content_type="application/json")
        data = resp.get_json()
        assert data["success"] is True
        assert len(data["users"]) >= 1
