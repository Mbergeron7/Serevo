"""Tests for the manager approvals page and APIs."""
from tests.conftest import login


class TestApprovalsPage:
    def test_approvals_page_loads(self, client, admin_user):
        user, pw = admin_user
        login(client, user.email, pw)
        resp = client.get("/approvals/")
        assert resp.status_code == 200

    def test_approvals_redirects_agent(self, client, app):
        from app.models import db, User
        from flask_bcrypt import generate_password_hash
        agent = User(
            email="agent@test.com",
            password_hash=generate_password_hash("pass123").decode("utf-8"),
            display_name="Test Agent", role="agent", is_active=True,
        )
        db.session.add(agent)
        db.session.commit()
        login(client, agent.email, "pass123")
        resp = client.get("/approvals/")
        assert resp.status_code == 302


class TestApprovalsCounts:
    def test_pending_counts(self, client, admin_user):
        user, pw = admin_user
        login(client, user.email, pw)
        resp = client.post("/approvals/api/counts",
                           json={}, content_type="application/json")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert "total" in data


class TestPTOApprovals:
    def test_list_pto(self, client, admin_user):
        user, pw = admin_user
        login(client, user.email, pw)
        resp = client.post("/approvals/api/pto",
                           json={}, content_type="application/json")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert "entries" in data

    def test_decide_pto(self, client, admin_user):
        from app.models import db, PTOEntry, Employee
        from datetime import date
        user, pw = admin_user
        login(client, user.email, pw)

        emp = Employee(employee_id="EMP-J001", first_name="Jane", last_name="Doe", email="jane@test.com")
        db.session.add(emp)
        db.session.flush()

        entry = PTOEntry(
            employee_id=emp.id, start_date=date(2026, 11, 1),
            end_date=date(2026, 11, 3), approval_status="pending",
        )
        db.session.add(entry)
        db.session.commit()

        resp = client.post("/approvals/api/pto/decide",
                           json={"id": entry.id, "decision": "approved"},
                           content_type="application/json")
        assert resp.status_code == 200
        assert resp.get_json()["success"] is True

        db.session.refresh(entry)
        assert entry.approval_status == "approved"


class TestBidApprovals:
    def test_list_bids(self, client, admin_user):
        user, pw = admin_user
        login(client, user.email, pw)
        resp = client.post("/approvals/api/bids",
                           json={}, content_type="application/json")
        assert resp.status_code == 200
        assert resp.get_json()["success"] is True

    def test_decide_bid(self, client, admin_user):
        from app.models import db, ShiftPost, ShiftBid, Employee
        from datetime import date, time
        user, pw = admin_user
        login(client, user.email, pw)

        emp = Employee(employee_id="EMP-B001", first_name="Bob", last_name="Smith", email="bob@test.com")
        db.session.add(emp)
        db.session.flush()

        sp = ShiftPost(
            schedule_date=date(2026, 11, 5),
            shift_start=time(9, 0), shift_end=time(17, 0),
            hours=8, status="open",
        )
        db.session.add(sp)
        db.session.flush()

        bid = ShiftBid(shift_post_id=sp.id, employee_id=emp.id, preference=1)
        db.session.add(bid)
        db.session.commit()

        resp = client.post("/approvals/api/bids/decide",
                           json={"id": bid.id, "decision": "accepted"},
                           content_type="application/json")
        assert resp.status_code == 200
        assert resp.get_json()["success"] is True

        db.session.refresh(bid)
        assert bid.status == "accepted"
        db.session.refresh(sp)
        assert sp.status == "assigned"
        assert sp.assigned_to == emp.id


class TestSwapApprovals:
    def test_list_swaps(self, client, admin_user):
        user, pw = admin_user
        login(client, user.email, pw)
        resp = client.post("/approvals/api/swaps",
                           json={}, content_type="application/json")
        assert resp.status_code == 200
        assert resp.get_json()["success"] is True
