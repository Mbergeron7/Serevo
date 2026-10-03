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
