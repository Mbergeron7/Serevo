"""Tests for VTO/OT management."""
from tests.conftest import login


class TestVTOOTManagement:
    def test_vto_ot_page_loads(self, client, admin_user):
        user, pw = admin_user
        login(client, user.email, pw)
        resp = client.get("/realtime/vto-ot")
        assert resp.status_code == 200
        assert b"VTO / OT Management" in resp.data

    def test_vto_ot_page_forbidden_for_agent(self, client, app):
        from app.models import db, User
        from flask_bcrypt import generate_password_hash
        with app.app_context():
            agent = User(
                email="agent@test.com",
                password_hash=generate_password_hash("pass123").decode("utf-8"),
                display_name="Agent", role="agent", is_active=True
            )
            db.session.add(agent)
            db.session.commit()
            email = agent.email
        login(client, email, "pass123")
        resp = client.get("/realtime/vto-ot")
        assert resp.status_code == 403

    def test_create_and_list_post(self, client, admin_user):
        user, pw = admin_user
        login(client, user.email, pw)
        # Create a VTO post
        resp = client.post("/realtime/vto-ot/save",
                           json={"post_type": "vto", "schedule_date": "2026-12-01",
                                 "start_time": "08:00", "end_time": "12:00",
                                 "hours": 4, "slots": 3, "notes": "Slow day"},
                           content_type="application/json")
        data = resp.get_json()
        assert data["success"] is True
        post_id = data["id"]

        # List posts
        resp = client.post("/realtime/vto-ot/list",
                           json={"status": "all", "type": "all"},
                           content_type="application/json")
        data = resp.get_json()
        assert data["success"] is True
        assert any(p["id"] == post_id for p in data["posts"])

    def test_cancel_post(self, client, admin_user):
        user, pw = admin_user
        login(client, user.email, pw)
        resp = client.post("/realtime/vto-ot/save",
                           json={"post_type": "ot", "schedule_date": "2026-12-02",
                                 "slots": 2},
                           content_type="application/json")
        post_id = resp.get_json()["id"]

        resp = client.post("/realtime/vto-ot/cancel",
                           json={"id": post_id},
                           content_type="application/json")
        assert resp.get_json()["success"] is True

    def test_delete_post(self, client, admin_user):
        user, pw = admin_user
        login(client, user.email, pw)
        resp = client.post("/realtime/vto-ot/save",
                           json={"post_type": "vto", "schedule_date": "2026-12-03",
                                 "slots": 1},
                           content_type="application/json")
        post_id = resp.get_json()["id"]

        resp = client.post("/realtime/vto-ot/delete",
                           json={"id": post_id},
                           content_type="application/json")
        assert resp.get_json()["success"] is True
