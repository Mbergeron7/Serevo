"""
Tests for the mid-day reforecasting engine and API endpoints.
"""

import datetime
import pytest
from tests.conftest import login


# ── Unit tests for reforecast engine ─────────────


def test_compute_trend_factor_basic():
    from app.forecasting.reforecast import compute_trend_factor

    forecast = {
        "08:00": {"offered": 100, "aht": 300},
        "08:30": {"offered": 120, "aht": 310},
        "09:00": {"offered": 110, "aht": 290},
    }
    actuals = {
        "08:00": {"offered": 120, "aht": 280},
        "08:30": {"offered": 140, "aht": 320},
    }

    trend = compute_trend_factor(forecast, actuals)

    assert trend["intervals_compared"] == 2
    assert trend["actual_total"] == 260
    assert trend["forecast_total"] == 220
    assert trend["ratio"] == round(260 / 220, 4)
    assert trend["aht_ratio"] == round((280 + 320) / (300 + 310), 4)
    assert trend["variance_pct"] == round(((260 - 220) / 220) * 100, 1)


def test_compute_trend_factor_empty():
    from app.forecasting.reforecast import compute_trend_factor

    trend = compute_trend_factor({}, {})
    assert trend["ratio"] == 1.0
    assert trend["intervals_compared"] == 0


def test_compute_trend_factor_no_overlap():
    from app.forecasting.reforecast import compute_trend_factor

    forecast = {"08:00": {"offered": 100, "aht": 300}}
    actuals = {"09:00": {"offered": 120, "aht": 280}}

    trend = compute_trend_factor(forecast, actuals)
    assert trend["intervals_compared"] == 0
    assert trend["ratio"] == 1.0


def test_apply_strategy_ratio():
    from app.forecasting.reforecast import _apply_strategy

    fc = {"offered": 100, "aht": 300}
    trend = {"ratio": 1.2, "delta": 20, "aht_ratio": 0.95}

    offered, aht = _apply_strategy("ratio", fc, trend, "10:00", {}, {})
    assert offered == pytest.approx(120, abs=0.1)
    assert aht == pytest.approx(285, abs=0.1)


def test_apply_strategy_delta():
    from app.forecasting.reforecast import _apply_strategy

    fc = {"offered": 100, "aht": 300}
    trend = {"ratio": 1.2, "delta": 15, "aht_ratio": 0.95}

    offered, aht = _apply_strategy("delta", fc, trend, "10:00", {}, {})
    assert offered == pytest.approx(115, abs=0.1)
    assert aht == 300  # delta doesn't adjust AHT


def test_apply_strategy_blended():
    from app.forecasting.reforecast import _apply_strategy

    fc = {"offered": 100, "aht": 300}
    trend = {"ratio": 1.2, "delta": 15, "aht_ratio": 0.95}
    forecast_by_slot = {
        "08:00": {"offered": 90, "aht": 300},
        "08:30": {"offered": 110, "aht": 310},
        "10:00": {"offered": 100, "aht": 300},
    }
    actuals_by_slot = {
        "08:00": {"offered": 108, "aht": 280},
        "08:30": {"offered": 132, "aht": 320},
    }

    offered, aht = _apply_strategy(
        "blended", fc, trend, "10:00", forecast_by_slot, actuals_by_slot
    )
    assert offered > 0
    assert aht == pytest.approx(285, abs=0.1)


def test_apply_strategy_clamps_negative():
    from app.forecasting.reforecast import _apply_strategy

    fc = {"offered": 10, "aht": 300}
    trend = {"ratio": 0.1, "delta": -50, "aht_ratio": 1.0}

    offered, _ = _apply_strategy("delta", fc, trend, "10:00", {}, {})
    assert offered == 0  # max(0, 10 + (-50))


# ── Integration tests with DB ───────────────────


@pytest.fixture()
def reforecast_data(app):
    """Set up planning unit, forecast, and actuals for today."""
    from app.models import db, PlanningUnit, ForecastInterval, IntervalActual

    unit = PlanningUnit(name="Sales", is_active=True)
    db.session.add(unit)
    db.session.commit()

    today = datetime.date.today()
    # Create forecast intervals for today
    for hour in range(8, 17):
        for minute in [0, 30]:
            ts = datetime.datetime.combine(today, datetime.time(hour, minute))
            db.session.add(ForecastInterval(
                planning_unit_id=unit.id,
                timestamp=ts,
                offered=100 + hour * 5,
                aht=300,
                source="test",
            ))

    # Create actuals for first few intervals (8:00–10:30)
    for hour in range(8, 11):
        for minute in [0, 30]:
            ts = datetime.datetime.combine(today, datetime.time(hour, minute))
            db.session.add(IntervalActual(
                planning_unit_id=unit.id,
                timestamp=ts,
                offered=120 + hour * 5,  # higher than forecast
                aht_secs=290,
                source="test",
            ))

    db.session.commit()
    return unit, today


def test_reforecast_engine(app, reforecast_data):
    from app.forecasting.reforecast import reforecast

    unit, today = reforecast_data
    result = reforecast("Sales", strategy="ratio", today=today)

    assert result["success"] is True
    assert result["lob"] == "Sales"
    assert result["strategy"] == "ratio"
    assert result["trend"]["intervals_compared"] >= 2
    assert len(result["intervals"]) > 0
    assert result["summary"]["original_total"] > 0
    assert result["summary"]["reforecast_total"] > 0

    # Verify actuals are marked
    actual_intervals = [i for i in result["intervals"] if i["is_actual"]]
    assert len(actual_intervals) >= 2


def test_reforecast_no_data(app):
    result = __import__('app.forecasting.reforecast', fromlist=['reforecast']).reforecast("NonExistent")
    assert result["success"] is False
    assert "No forecast data" in result["error"]


def test_reforecast_no_actuals(app, reforecast_data):
    from app.models import db, IntervalActual
    # Delete all actuals
    IntervalActual.query.delete()
    db.session.commit()

    from app.forecasting.reforecast import reforecast
    unit, today = reforecast_data
    result = reforecast("Sales", today=today)
    assert result["success"] is False
    assert "No actuals" in result["error"]


def test_variance_alerts(app, reforecast_data):
    from app.forecasting.reforecast import get_variance_alerts

    _, today = reforecast_data
    alerts = get_variance_alerts(threshold_pct=5.0, today=today)

    # With actuals higher than forecast, we should get an alert
    assert len(alerts) >= 1
    assert alerts[0]["lob"] == "Sales"
    assert alerts[0]["direction"] == "above"


def test_reforecast_all_lobs(app, reforecast_data):
    from app.forecasting.reforecast import reforecast_all_lobs

    _, today = reforecast_data
    results = reforecast_all_lobs(strategy="blended", today=today)

    assert len(results) >= 1
    assert results[0]["success"] is True


# ── API endpoint tests ──────────────────────────


def test_reforecast_api(client, admin_user, reforecast_data):
    user, password = admin_user
    login(client, user.email, password)

    resp = client.post("/forecasting/reforecast", json={
        "lob": "Sales",
        "strategy": "blended",
    })
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert "intervals" in data
    assert "summary" in data


def test_reforecast_all_api(client, admin_user, reforecast_data):
    user, password = admin_user
    login(client, user.email, password)

    resp = client.post("/forecasting/reforecast/all", json={
        "strategy": "ratio",
    })
    assert resp.status_code == 200
    data = resp.get_json()
    assert "results" in data


def test_alerts_api(client, admin_user, reforecast_data):
    user, password = admin_user
    login(client, user.email, password)

    resp = client.post("/forecasting/reforecast/alerts", json={
        "threshold_pct": 5,
    })
    assert resp.status_code == 200
    data = resp.get_json()
    assert "alerts" in data


def test_reforecast_api_requires_login(client):
    resp = client.post("/forecasting/reforecast", json={"lob": "Sales"})
    # Should redirect to login
    assert resp.status_code in (302, 401, 403)
