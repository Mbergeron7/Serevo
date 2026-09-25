"""
routes/forecasting.py — Forecasting blueprint
===============================================
View forecasts, generate new ones, Erlang C, what-if, accuracy.
"""

import logging
import datetime

from flask import (Blueprint, render_template, request, jsonify)
from app.auth import login_required, get_current_user

log = logging.getLogger("serevo.forecasting")

forecasting_bp = Blueprint("forecasting", __name__, url_prefix="/forecasting")


def _get_sheet():
    try:
        from app.data_source import _open_capacity_sheet
        sheet, err = _open_capacity_sheet()
        return None if err else sheet
    except Exception:
        return None


# ── Main view ───────────────────────────────────────────────
@forecasting_bp.route("/")
@login_required
def index():
    user = get_current_user()
    from app.forecasting.engine import get_available_lobs
    sheet = _get_sheet()
    lobs = get_available_lobs(sheet)
    return render_template("forecasting/index.html", user=user, lobs=lobs)


# ── View existing forecast data (API) ──────────────────────
@forecasting_bp.route("/data", methods=["POST"])
@login_required
def forecast_data():
    """
    POST JSON: {lob, start_date, end_date}
    Returns interval-level forecast + requirements data.
    """
    from app.forecasting.engine import (get_forecast_data, get_requirements_data,
                                         compute_requirements_from_forecast,
                                         daily_summary)
    try:
        payload = request.get_json(silent=True) or {}
        lob = payload.get("lob", "").strip()
        start_str = payload.get("start_date", "")
        end_str = payload.get("end_date", "")

        if not lob or not start_str or not end_str:
            return jsonify({"success": False, "error": "LOB and date range are required"})

        start_date = datetime.datetime.strptime(start_str, "%Y-%m-%d").date()
        end_date = datetime.datetime.strptime(end_str, "%Y-%m-%d").date()
        sheet = _get_sheet()

        forecast, f_err = get_forecast_data(lob, start_date, end_date, sheet)
        requirements, r_err = get_requirements_data(lob, start_date, end_date, sheet)

        # Compute Erlang C requirements from forecast
        sl_target = float(payload.get("service_level", 0.80))
        asa_target = float(payload.get("target_asa", 30))
        shrinkage = float(payload.get("shrinkage", 0.30))

        erlang_results = compute_requirements_from_forecast(
            forecast,
            service_level_target=sl_target,
            target_asa=asa_target,
            shrinkage=shrinkage,
        )

        return jsonify({
            "success": True,
            "forecast": forecast,
            "requirements": requirements,
            "erlang": erlang_results,
            "daily_summary": daily_summary(erlang_results),
            "dates_available": sorted(set(r["date"] for r in forecast)),
        })
    except Exception as e:
        log.error(f"Forecast data error: {e}")
        return jsonify({"success": False, "error": str(e)})


# ── Generate forecast (API) ────────────────────────────────
@forecasting_bp.route("/generate", methods=["POST"])
@login_required
def generate():
    """
    POST JSON: {lob, method, historical_days, forecast_days, window?, service_level?, target_asa?, shrinkage?}
    """
    from app.forecasting.engine import (generate_forecast_moving_avg,
                                         generate_forecast_weighted,
                                         compute_requirements_from_forecast,
                                         daily_summary)
    try:
        payload = request.get_json(silent=True) or {}
        lob = payload.get("lob", "").strip()
        method = payload.get("method", "moving_average")
        hist_days = int(payload.get("historical_days", 30))
        fc_days = int(payload.get("forecast_days", 7))
        window = int(payload.get("window", 7))

        if not lob:
            return jsonify({"success": False, "error": "LOB is required"})

        sheet = _get_sheet()

        if method == "weighted_trend":
            result = generate_forecast_weighted(lob, hist_days, fc_days, sheet)
        else:
            result = generate_forecast_moving_avg(lob, hist_days, fc_days, window, sheet)

        if result.get("error"):
            return jsonify({"success": False, "error": result["error"]})

        # Compute Erlang C on forecast
        sl_target = float(payload.get("service_level", 0.80))
        asa_target = float(payload.get("target_asa", 30))
        shrinkage = float(payload.get("shrinkage", 0.30))

        erlang_forecast = compute_requirements_from_forecast(
            result["forecast"],
            service_level_target=sl_target,
            target_asa=asa_target,
            shrinkage=shrinkage,
        )

        result["erlang_forecast"] = erlang_forecast
        result["daily_summary"] = daily_summary(erlang_forecast)
        result["success"] = True
        return jsonify(result)

    except Exception as e:
        log.error(f"Forecast generation error: {e}")
        return jsonify({"success": False, "error": str(e)})


# ── What-if scenario (API) ─────────────────────────────────
@forecasting_bp.route("/what-if", methods=["POST"])
@login_required
def what_if():
    """
    POST JSON: {lob, start_date, end_date, volume_pct, aht_pct, service_level?, target_asa?, shrinkage?}
    """
    from app.forecasting.engine import (get_forecast_data, apply_what_if, daily_summary)

    try:
        payload = request.get_json(silent=True) or {}
        lob = payload.get("lob", "").strip()
        start_str = payload.get("start_date", "")
        end_str = payload.get("end_date", "")
        vol_pct = float(payload.get("volume_pct", 0))
        aht_pct = float(payload.get("aht_pct", 0))

        if not lob or not start_str or not end_str:
            return jsonify({"success": False, "error": "LOB and date range are required"})

        start_date = datetime.datetime.strptime(start_str, "%Y-%m-%d").date()
        end_date = datetime.datetime.strptime(end_str, "%Y-%m-%d").date()
        sheet = _get_sheet()

        forecast, err = get_forecast_data(lob, start_date, end_date, sheet)
        if err or not forecast:
            return jsonify({"success": False, "error": err or "No data"})

        sl_target = float(payload.get("service_level", 0.80))
        asa_target = float(payload.get("target_asa", 30))
        shrinkage = float(payload.get("shrinkage", 0.30))

        results = apply_what_if(
            forecast,
            volume_pct=vol_pct,
            aht_pct=aht_pct,
            service_level_target=sl_target,
            target_asa=asa_target,
            shrinkage=shrinkage,
        )

        return jsonify({
            "success": True,
            "scenario": {
                "volume_pct": vol_pct,
                "aht_pct": aht_pct,
            },
            "results": results,
            "daily_summary": daily_summary(results),
        })

    except Exception as e:
        log.error(f"What-if error: {e}")
        return jsonify({"success": False, "error": str(e)})


# ── Accuracy (API) ─────────────────────────────────────────
@forecasting_bp.route("/accuracy", methods=["POST"])
@login_required
def accuracy():
    """
    POST JSON: {lob, start_date, end_date}
    """
    from app.forecasting.engine import compute_accuracy

    try:
        payload = request.get_json(silent=True) or {}
        lob = payload.get("lob", "").strip()
        start_str = payload.get("start_date", "")
        end_str = payload.get("end_date", "")

        if not lob or not start_str or not end_str:
            return jsonify({"success": False, "error": "LOB and date range are required"})

        start_date = datetime.datetime.strptime(start_str, "%Y-%m-%d").date()
        end_date = datetime.datetime.strptime(end_str, "%Y-%m-%d").date()
        sheet = _get_sheet()

        result = compute_accuracy(lob, start_date, end_date, sheet)
        result["success"] = True
        return jsonify(result)

    except Exception as e:
        log.error(f"Accuracy error: {e}")
        return jsonify({"success": False, "error": str(e)})


# ── Erlang C calculator (API) ──────────────────────────────
@forecasting_bp.route("/erlang", methods=["POST"])
@login_required
def erlang():
    """
    Standalone Erlang C calculator.
    POST JSON: {offered, aht, interval_seconds?, service_level?, target_asa?, shrinkage?}
    """
    from app.forecasting.engine import erlang_c_staffing

    try:
        payload = request.get_json(silent=True) or {}
        offered = float(payload.get("offered", 0))
        aht = float(payload.get("aht", 0))
        interval = float(payload.get("interval_seconds", 1800))
        sl = float(payload.get("service_level", 0.80))
        asa = float(payload.get("target_asa", 30))
        shrinkage = float(payload.get("shrinkage", 0.30))

        result = erlang_c_staffing(offered, aht, interval, sl, asa, shrinkage)
        result["success"] = True
        return jsonify(result)

    except Exception as e:
        return jsonify({"success": False, "error": str(e)})
