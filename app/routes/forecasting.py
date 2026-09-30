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


def _demo_guard():
    """Return a mock-success JSON response if the current user is a demo user, else None."""
    from app.auth import get_current_user
    user = get_current_user()
    if user and user.get("is_demo"):
        return jsonify(success=True, demo=True, message="Changes are not saved in demo mode.")
    return None


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
    if user and user.get("is_demo"):
        from app.demo_data import DEMO_LOBS
        lobs = list(DEMO_LOBS)
    else:
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

        user = get_current_user()
        if user and user.get("is_demo"):
            from app.demo_data import get_demo_forecast, get_demo_requirements
            forecast = []
            requirements = []
            d = start_date
            while d <= end_date:
                day_fc, _ = get_demo_forecast(lob, d)
                day_rq, _ = get_demo_requirements(lob, d)
                for row in day_fc:
                    forecast.append({**row, "date": d.isoformat()})
                for row in day_rq:
                    requirements.append({**row, "date": d.isoformat()})
                d += datetime.timedelta(days=1)
            f_err = None
            r_err = None
        else:
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

        user = get_current_user()
        if user and user.get("is_demo"):
            # Generate demo forecast for the requested number of days
            from app.demo_data import get_demo_forecast
            today = datetime.date.today()
            forecast_rows = []
            for i in range(fc_days):
                d = today + datetime.timedelta(days=i)
                day_fc, _ = get_demo_forecast(lob, d)
                for row in day_fc:
                    forecast_rows.append({**row, "date": d.isoformat()})
            result = {"forecast": forecast_rows}
        else:
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

        # Save generated forecast + requirements to DB when using Postgres
        import os
        is_demo_user = user and user.get("is_demo")
        if not is_demo_user and os.environ.get("DATA_SOURCE", "").strip().lower() == "postgres":
            try:
                from app.models import db, ForecastInterval, RequirementInterval, PlanningUnit
                from app.data_source import normalize_lob
                from sqlalchemy import func as sa_func

                lob_normalized = normalize_lob(lob)
                unit = PlanningUnit.query.filter_by(name=lob_normalized).first()
                if not unit:
                    unit = PlanningUnit(name=lob_normalized)
                    db.session.add(unit)
                    db.session.flush()

                today = datetime.date.today()

                # Clear existing generated future forecasts for this unit
                ForecastInterval.query.filter(
                    ForecastInterval.planning_unit_id == unit.id,
                    sa_func.date(ForecastInterval.timestamp) >= today,
                    ForecastInterval.source == "generated",
                ).delete(synchronize_session=False)

                RequirementInterval.query.filter(
                    RequirementInterval.planning_unit_id == unit.id,
                    sa_func.date(RequirementInterval.timestamp) >= today,
                    RequirementInterval.source == "generated",
                ).delete(synchronize_session=False)

                # Save forecast intervals
                for row in result.get("forecast", []):
                    try:
                        ts = datetime.datetime.strptime(
                            f"{row['date']} {row['time']}", "%Y-%m-%d %H:%M")
                    except (ValueError, KeyError):
                        continue
                    db.session.add(ForecastInterval(
                        planning_unit_id=unit.id,
                        timestamp=ts,
                        offered=row["offered"],
                        aht=row["aht"],
                        source="generated",
                    ))

                # Save requirement intervals
                for row in erlang_forecast:
                    try:
                        ts = datetime.datetime.strptime(
                            f"{row['date']} {row['time']}", "%Y-%m-%d %H:%M")
                    except (ValueError, KeyError):
                        continue
                    db.session.add(RequirementInterval(
                        planning_unit_id=unit.id,
                        timestamp=ts,
                        agents_required=row.get("agents_required", 0),
                        source="generated",
                    ))

                db.session.commit()
                log.info(f"Saved forecast + requirements to DB for '{lob_normalized}'")
            except Exception as e:
                log.warning(f"Failed to save forecast to DB: {e}")

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

        user = get_current_user()
        if user and user.get("is_demo"):
            from app.demo_data import get_demo_forecast
            forecast = []
            d = start_date
            while d <= end_date:
                day_fc, _ = get_demo_forecast(lob, d)
                for row in day_fc:
                    forecast.append({**row, "date": d.isoformat()})
                d += datetime.timedelta(days=1)
            err = None
        else:
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

        user = get_current_user()
        if user and user.get("is_demo"):
            # For demo, return a plausible accuracy result
            result = {
                "mape": 8.5,
                "bias": -1.2,
                "intervals_compared": 28 * ((end_date - start_date).days + 1),
                "summary": "Demo accuracy — based on sample data",
            }
        else:
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


# ── Generate & Save forecast from DB historical data ─────────
@forecasting_bp.route("/generate-from-history", methods=["POST"])
@login_required
def generate_from_history():
    """
    Generate a Serevo forecast from historical actuals in the database,
    then run Erlang C to produce requirements. Both are saved to DB.

    POST JSON:
      lob: string or "all" (required)
      method: "weighted" | "moving_average" (default "weighted")
      historical_days: int (default 90)
      forecast_days: int (default 90)
      window: int (default 7, for moving_average only)
    """
    dg = _demo_guard()
    if dg:
        return dg
    from app.forecasting.engine import (generate_and_save_forecast,
                                         generate_all_forecasts)
    try:
        payload = request.get_json(silent=True) or {}
        lob = payload.get("lob", "").strip()
        method = payload.get("method", "weighted")
        hist_days = int(payload.get("historical_days", 90))
        fc_days = int(payload.get("forecast_days", 90))
        window = int(payload.get("window", 7))

        if not lob:
            return jsonify({"success": False, "error": "LOB is required"})

        if lob.lower() == "all":
            results = generate_all_forecasts(method, hist_days, fc_days, window)
            ok_count = sum(1 for r in results if r.get("ok"))
            return jsonify({"success": True, "results": results,
                           "message": f"Generated forecasts for {ok_count} LOBs"})
        else:
            result = generate_and_save_forecast(lob, method, hist_days,
                                                 fc_days, window)
            result["success"] = result.get("ok", False)
            return jsonify(result)

    except Exception as e:
        log.error(f"Generate from history error: {e}")
        return jsonify({"success": False, "error": str(e)})
