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
