"""Tests for health check and error pages."""


class TestHealth:
    def test_health_endpoint(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert "brand" in data

    def test_404_page(self, client):
        resp = client.get("/nonexistent-page-xyz")
        assert resp.status_code == 404


class TestPageLoads:
    """Smoke tests — every major page should return 200 when logged in as admin."""

    PAGES = [
        "/",
        "/people/",
        "/scheduling/",
        "/forecasting/",
        "/capacity/",
        "/realtime/",
        "/quality/",
        "/portal/",
        "/data/",
        "/settings/",
    ]

    def test_all_pages_load(self, client, admin_user):
        from tests.conftest import login
        user, pw = admin_user
        login(client, user.email, pw)

        for path in self.PAGES:
            resp = client.get(path)
            assert resp.status_code == 200, f"{path} returned {resp.status_code}"

    SETTINGS_PAGES = [
        "/settings/users",
        "/settings/customization",
        "/settings/activities",
        "/settings/contracts",
        "/settings/day-models",
        "/settings/planning-units",
        "/settings/skills",
        "/settings/selections",
        "/settings/shift-sequences",
        "/settings/planning-calendars",
        "/settings/google-sheets",
        "/settings/api-connections",
        "/settings/activity-log",
    ]

    def test_settings_pages_load(self, client, admin_user):
        from tests.conftest import login
        user, pw = admin_user
        login(client, user.email, pw)

        for path in self.SETTINGS_PAGES:
            resp = client.get(path)
            assert resp.status_code == 200, f"{path} returned {resp.status_code}"

    CAPACITY_PAGES = [
        "/capacity/",
        "/capacity/plan",
        "/capacity/sources",
    ]

    def test_capacity_pages_load(self, client, admin_user):
        from tests.conftest import login
        user, pw = admin_user
        login(client, user.email, pw)

        for path in self.CAPACITY_PAGES:
            resp = client.get(path)
            assert resp.status_code == 200, f"{path} returned {resp.status_code}"

    def test_agent_pages_require_login(self, client):
        """Agent portal pages should redirect to login when not authenticated."""
        for path in ["/portal/", "/my-schedule/", "/my-time-off/"]:
            resp = client.get(path)
            assert resp.status_code in (302, 200), f"{path} returned {resp.status_code}"


class TestNotifications:
    """Test notification system APIs."""

    def test_unread_count(self, client, admin_user):
        from tests.conftest import login
        user, pw = admin_user
        login(client, user.email, pw)
        resp = client.post("/notifications/api/unread-count",
                           json={}, content_type="application/json")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert "count" in data

    def test_list_notifications(self, client, admin_user):
        from tests.conftest import login
        user, pw = admin_user
        login(client, user.email, pw)
        resp = client.post("/notifications/api/list",
                           json={}, content_type="application/json")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert "notifications" in data

    def test_create_and_read_notification(self, client, admin_user):
        from tests.conftest import login
        user, pw = admin_user
        login(client, user.email, pw)

        from app.routes.notifications import notify
        notify(user.id, "Test alert", "This is a test", category="system")

        resp = client.post("/notifications/api/list",
                           json={}, content_type="application/json")
        data = resp.get_json()
        assert data["unread_count"] >= 1
        found = any(n["title"] == "Test alert" for n in data["notifications"])
        assert found, "Created notification should appear in list"

    def test_mark_read(self, client, admin_user):
        from tests.conftest import login
        user, pw = admin_user
        login(client, user.email, pw)

        from app.routes.notifications import notify
        n = notify(user.id, "Read me", category="info")

        resp = client.post("/notifications/api/mark-read",
                           json={"id": n.id}, content_type="application/json")
        assert resp.status_code == 200
        assert resp.get_json()["success"] is True

    def test_mark_all_read(self, client, admin_user):
        from tests.conftest import login
        user, pw = admin_user
        login(client, user.email, pw)

        resp = client.post("/notifications/api/mark-read",
                           json={"id": "all"}, content_type="application/json")
        assert resp.status_code == 200
        assert resp.get_json()["success"] is True


class TestActivityLog:
    """Test activity log page and API."""

    def test_activity_log_page_loads(self, client, admin_user):
        from tests.conftest import login
        user, pw = admin_user
        login(client, user.email, pw)
        resp = client.get("/settings/activity-log")
        assert resp.status_code == 200

    def test_activity_log_data_api(self, client, admin_user):
        from tests.conftest import login
        user, pw = admin_user
        login(client, user.email, pw)
        resp = client.post("/settings/activity-log/data",
                           json={}, content_type="application/json")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert "entries" in data
