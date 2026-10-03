"""
routes/forecasting.py — Forecasting blueprint
===============================================
View forecasts, generate new ones, Erlang C, what-if, accuracy.
"""

import logging
import datetime
from datetime import date

from flask import (Blueprint, render_template, request, jsonify)
from app.auth import login_required, get_current_user

log = logging.getLogger("serevo.forecasting")

forecasting_bp = Blueprint("forecasting", __name__, url_prefix="/forecasting")

TIMEZONE = "America/Toronto"


def _demo_guard():
    """Return a mock-success JSON response if the current user is a demo user, else None."""
    from app.auth import get_current_user
    user = get_current_user()
    if user and user.get("is_demo"):
        return jsonify(success=True, demo=True, message="Changes are not saved in demo mode.")
    return None


from app.routes._utils import get_sheet as _get_sheet


# ── Main view (forecast dashboard) ───────────────────────────
@forecasting_bp.route("/")
@login_required
def index():
    from datetime import datetime as dt
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        from backports.zoneinfo import ZoneInfo

    user = get_current_user()
    now = dt.now(ZoneInfo(TIMEZONE))
    year = int(request.args.get("year", now.year))
    years = list(range(2024, now.year + 3))

    if user and user.get("is_demo"):
        from app.demo_data import DEMO_LOBS
        all_lobs = sorted(DEMO_LOBS)
    else:
        from app.models import PlanningUnit
        all_lobs = sorted([pu.name for pu in PlanningUnit.query.filter_by(is_active=True).all()])

    return render_template("forecasting/index.html",
        user=user, year=year, years=years, all_lobs=all_lobs)


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
                                         generate_forecast_holt_winters,
                                         auto_select_method,
                                         apply_holiday_adjustments,
                                         compute_requirements_from_forecast,
                                         daily_summary)
    from app.forecasting.ml_engine import generate_forecast_ml
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

            # Auto-select best method if requested
            if method == "auto":
                auto_result = auto_select_method(lob, hist_days, sheet)
                method = auto_result["best_method"]
                # Will be included in response

            if method == "ml_gradient_boosting":
                result = generate_forecast_ml(lob, hist_days, fc_days, sheet)
            elif method == "holt_winters":
                result = generate_forecast_holt_winters(lob, hist_days, fc_days, sheet)
            elif method == "weighted_trend":
                result = generate_forecast_weighted(lob, hist_days, fc_days, sheet)
            else:
                result = generate_forecast_moving_avg(lob, hist_days, fc_days, window, sheet)

            # Apply holiday adjustments
            if result.get("forecast"):
                result["forecast"] = apply_holiday_adjustments(
                    result["forecast"], result.get("historical"))

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
            # Prefer accuracy vs actuals (IntervalActual) when available
            import os
            if os.environ.get("DATA_SOURCE", "").strip().lower() == "postgres":
                from app.forecasting.ml_engine import compute_accuracy_vs_actuals
                result = compute_accuracy_vs_actuals(lob, start_date, end_date)
                if result.get("summary", {}).get("matched_intervals", 0) == 0:
                    # Fall back to old method if no actuals exist
                    sheet = _get_sheet()
                    result = compute_accuracy(lob, start_date, end_date, sheet)
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


# ── Daily aggregated data API (for dashboard charts) ────────
@forecasting_bp.route("/api/data", methods=["POST"])
@login_required
def forecast_api_data():
    """Return daily aggregated forecast + requirements data for a LOB.

    POST JSON: {lob, year, service_level?, target_asa?, shrinkage?}
    Returns: {success, daily: [{date, offered, aht, agents_required, is_historic}], totals: {...}}
    """
    from app.models import (ForecastInterval, RequirementInterval,
                            PlanningUnit, IntervalActual)
    from app.data_source import normalize_lob
    from sqlalchemy import func as sa_func
    from collections import defaultdict
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        from backports.zoneinfo import ZoneInfo

    try:
        payload = request.get_json(silent=True) or {}
        lob = payload.get("lob", "").strip()
        from datetime import datetime as dt
        year = int(payload.get("year", dt.now(ZoneInfo(TIMEZONE)).year))

        if not lob:
            return jsonify({"success": False, "error": "LOB is required"})

        user = get_current_user()

        # Demo mode
        if user and user.get("is_demo"):
            from app.demo_data import get_demo_forecast, get_demo_requirements
            import calendar
            daily = []
            today = date.today()
            for m in range(1, 13):
                for d_num in range(1, calendar.monthrange(year, m)[1] + 1):
                    d = date(year, m, d_num)
                    fc, _ = get_demo_forecast(lob, d)
                    rq, _ = get_demo_requirements(lob, d)
                    total_off = sum(r["offered"] for r in fc)
                    avg_aht = (sum(r["offered"] * r["aht"] for r in fc) / total_off) if total_off > 0 else 0
                    avg_req = sum(r["agents_required"] for r in rq) / max(1, len(rq)) if rq else 0
                    daily.append({
                        "date": d.isoformat(),
                        "offered": round(total_off),
                        "aht": round(avg_aht),
                        "agents_required": round(avg_req, 1),
                        "is_historic": d < today,
                    })
            total_off_all = sum(d["offered"] for d in daily)
            avg_aht_all = (sum(d["offered"] * d["aht"] for d in daily) / total_off_all) if total_off_all > 0 else 0
            return jsonify({
                "success": True,
                "daily": daily,
                "totals": {
                    "total_offered": total_off_all,
                    "avg_aht": round(avg_aht_all),
                    "total_person_hours": round(sum(d["agents_required"] * 8 for d in daily)),
                    "total_intervals": len(daily) * 28,
                }
            })

        lob_normalized = normalize_lob(lob)
        unit = PlanningUnit.query.filter_by(name=lob_normalized).first()
        if not unit:
            return jsonify({"success": False, "error": f"No planning unit found for '{lob}'"})

        start = date(year, 1, 1)
        end = date(year, 12, 31)
        today = date.today()

        # Get historical actuals (aggregated by day)
        hist_rows = (
            IntervalActual.query
            .filter(
                IntervalActual.planning_unit_id == unit.id,
                sa_func.date(IntervalActual.timestamp) >= start,
                sa_func.date(IntervalActual.timestamp) <= end,
            )
            .with_entities(
                sa_func.date(IntervalActual.timestamp).label("dt"),
                sa_func.sum(IntervalActual.offered).label("offered"),
                sa_func.avg(IntervalActual.aht_secs).label("aht"),
                sa_func.count().label("cnt"),
            )
            .group_by(sa_func.date(IntervalActual.timestamp))
            .all()
        )
        hist_map = {}
        for row in hist_rows:
            d_str = str(row.dt) if row.dt else None
            if d_str:
                hist_map[d_str] = {
                    "offered": int(row.offered or 0),
                    "aht": round(float(row.aht or 0)),
                    "intervals": int(row.cnt or 0),
                }

        # Get forecast intervals (aggregated by day)
        fc_rows = (
            ForecastInterval.query
            .filter(
                ForecastInterval.planning_unit_id == unit.id,
                sa_func.date(ForecastInterval.timestamp) >= start,
                sa_func.date(ForecastInterval.timestamp) <= end,
            )
            .with_entities(
                sa_func.date(ForecastInterval.timestamp).label("dt"),
                sa_func.sum(ForecastInterval.offered).label("offered"),
                sa_func.avg(ForecastInterval.aht).label("aht"),
                sa_func.count().label("cnt"),
            )
            .group_by(sa_func.date(ForecastInterval.timestamp))
            .all()
        )
        fc_map = {}
        for row in fc_rows:
            d_str = str(row.dt) if row.dt else None
            if d_str:
                fc_map[d_str] = {
                    "offered": round(float(row.offered or 0)),
                    "aht": round(float(row.aht or 0)),
                    "intervals": int(row.cnt or 0),
                }

        # Get requirements (aggregated by day)
        req_rows = (
            RequirementInterval.query
            .filter(
                RequirementInterval.planning_unit_id == unit.id,
                sa_func.date(RequirementInterval.timestamp) >= start,
                sa_func.date(RequirementInterval.timestamp) <= end,
            )
            .with_entities(
                sa_func.date(RequirementInterval.timestamp).label("dt"),
                sa_func.avg(RequirementInterval.agents_required).label("avg_req"),
                sa_func.max(RequirementInterval.agents_required).label("peak_req"),
            )
            .group_by(sa_func.date(RequirementInterval.timestamp))
            .all()
        )
        req_map = {}
        for row in req_rows:
            d_str = str(row.dt) if row.dt else None
            if d_str:
                req_map[d_str] = {
                    "avg": round(float(row.avg_req or 0), 1),
                    "peak": round(float(row.peak_req or 0), 1),
                }

        # Merge: prefer actuals for past, forecast for future
        all_dates = sorted(set(list(hist_map.keys()) + list(fc_map.keys()) + list(req_map.keys())))
        daily = []
        total_offered = 0
        total_aht_w = 0
        total_intervals = 0
        total_req_hours = 0

        for d_str in all_dates:
            is_historic = d_str <= today.isoformat()
            h = hist_map.get(d_str)
            f = fc_map.get(d_str)
            r = req_map.get(d_str)

            if is_historic and h:
                offered = h["offered"]
                aht = h["aht"]
                intervals = h["intervals"]
            elif f:
                offered = f["offered"]
                aht = f["aht"]
                intervals = f["intervals"]
            else:
                continue

            agents_req = r["avg"] if r else 0
            daily.append({
                "date": d_str,
                "offered": offered,
                "aht": aht,
                "agents_required": agents_req,
                "is_historic": is_historic,
            })
            total_offered += offered
            total_aht_w += offered * aht
            total_intervals += intervals
            total_req_hours += agents_req * 8

        avg_aht = round(total_aht_w / total_offered) if total_offered > 0 else 0

        return jsonify({
            "success": True,
            "daily": daily,
            "totals": {
                "total_offered": total_offered,
                "avg_aht": avg_aht,
                "total_person_hours": round(total_req_hours),
                "total_intervals": total_intervals,
            }
        })

    except Exception as e:
        log.exception("Forecast API data error")
        return jsonify({"success": False, "error": str(e)})


# ═══════════════════════════════════════════════════════════════
# MID-DAY REFORECASTING
# ═══════════════════════════════════════════════════════════════

@forecasting_bp.route("/reforecast", methods=["POST"])
@login_required
def run_reforecast():
    """
    Generate a mid-day reforecast for a LOB.

    POST JSON:
      lob: string (required)
      strategy: "ratio" | "delta" | "blended" (default "blended")
      date: "YYYY-MM-DD" (default today)

    Returns reforecast with original vs adjusted intervals and
    recomputed staffing requirements.
    """
    from app.forecasting.reforecast import reforecast as do_reforecast

    try:
        payload = request.get_json(silent=True) or {}
        lob = payload.get("lob", "").strip()
        strategy = payload.get("strategy", "blended")
        date_str = payload.get("date")

        if not lob:
            return jsonify({"success": False, "error": "LOB is required"})

        if strategy not in ("ratio", "delta", "blended"):
            strategy = "blended"

        target_date = None
        if date_str:
            try:
                target_date = datetime.date.fromisoformat(date_str)
            except (ValueError, TypeError):
                pass

        result = do_reforecast(lob, strategy, target_date)
        return jsonify(result)

    except Exception as e:
        log.exception("Reforecast error")
        return jsonify({"success": False, "error": str(e)})


@forecasting_bp.route("/reforecast/all", methods=["POST"])
@login_required
def run_reforecast_all():
    """
    Run reforecasting for all active LOBs.

    POST JSON:
      strategy: "ratio" | "delta" | "blended" (default "blended")

    Returns results per LOB.
    """
    from app.forecasting.reforecast import reforecast_all_lobs

    try:
        payload = request.get_json(silent=True) or {}
        strategy = payload.get("strategy", "blended")

        results = reforecast_all_lobs(strategy)
        successful = [r for r in results if r.get("success")]
        return jsonify({
            "success": True,
            "results": results,
            "summary": f"{len(successful)} of {len(results)} LOBs reforecast",
        })

    except Exception as e:
        log.exception("Reforecast all error")
        return jsonify({"success": False, "error": str(e)})


@forecasting_bp.route("/reforecast/alerts", methods=["POST"])
@login_required
def reforecast_alerts():
    """
    Check for LOBs where actuals deviate significantly from forecast.

    POST JSON:
      threshold_pct: float (default 10.0)

    Returns list of variance alerts sorted by severity.
    """
    from app.forecasting.reforecast import get_variance_alerts

    try:
        payload = request.get_json(silent=True) or {}
        threshold = float(payload.get("threshold_pct", 10.0))

        alerts = get_variance_alerts(threshold)
        return jsonify({
            "success": True,
            "alerts": alerts,
            "count": len(alerts),
        })

    except Exception as e:
        log.exception("Reforecast alerts error")
        return jsonify({"success": False, "error": str(e)})


# ══════════════════════════════════════════════════════════════
# Forecast Scenarios — save, list, delete, push-to-requirements
# ══════════════════════════════════════════════════════════════

@forecasting_bp.route("/scenarios", methods=["POST"])
@login_required
def list_scenarios():
    """List saved forecast scenarios for a LOB + year."""
    from app.models import ForecastScenario, PlanningUnit
    user = get_current_user()

    if user and user.get("is_demo"):
        return jsonify(success=True, scenarios=_demo_scenarios())

    payload = request.get_json(silent=True) or {}
    lob = payload.get("lob", "").strip()
    from datetime import datetime as dt
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        from backports.zoneinfo import ZoneInfo
    year = int(payload.get("year", dt.now(ZoneInfo(TIMEZONE)).year))

    if not lob:
        return jsonify(success=True, scenarios=[])

    from app.data_source import normalize_lob
    lob_n = normalize_lob(lob)
    unit = PlanningUnit.query.filter_by(name=lob_n).first()
    if not unit:
        return jsonify(success=True, scenarios=[])

    rows = ForecastScenario.query.filter_by(
        planning_unit_id=unit.id, year=year
    ).order_by(ForecastScenario.created_at.desc()).all()

    return jsonify(success=True, scenarios=[s.to_dict() for s in rows])


@forecasting_bp.route("/scenarios/save", methods=["POST"])
@login_required
def save_scenario():
    """Save current forecast settings as a named scenario."""
    from app.models import ForecastScenario, PlanningUnit
    dg = _demo_guard()
    if dg:
        return dg

    payload = request.get_json(silent=True) or {}
    lob = payload.get("lob", "").strip()
    name = payload.get("name", "").strip()
    scenario_type = payload.get("scenario_type", "regular")
    if not lob or not name:
        return jsonify(success=False, error="LOB and name are required")

    from app.data_source import normalize_lob
    from datetime import datetime as dt
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        from backports.zoneinfo import ZoneInfo

    year = int(payload.get("year", dt.now(ZoneInfo(TIMEZONE)).year))
    lob_n = normalize_lob(lob)
    unit = PlanningUnit.query.filter_by(name=lob_n).first()
    if not unit:
        unit = PlanningUnit(name=lob_n)
        db.session.add(unit)
        db.session.flush()

    # Check if scenario with same name + type + year already exists for this LOB
    existing = ForecastScenario.query.filter_by(
        planning_unit_id=unit.id, name=name, year=year
    ).first()

    if existing:
        # Update existing
        existing.scenario_type = scenario_type
        existing.method = payload.get("method", existing.method)
        existing.interval_minutes = int(payload.get("interval_minutes", existing.interval_minutes))
        existing.historical_days = int(payload.get("historical_days", existing.historical_days))
        existing.forecast_days = int(payload.get("forecast_days", existing.forecast_days))
        existing.service_level = float(payload.get("service_level", existing.service_level))
        existing.target_asa = float(payload.get("target_asa", existing.target_asa))
        existing.shrinkage = float(payload.get("shrinkage", existing.shrinkage))
        existing.notes = payload.get("notes", existing.notes)
        db.session.commit()
        return jsonify(success=True, scenario=existing.to_dict(), updated=True)

    scenario = ForecastScenario(
        planning_unit_id=unit.id,
        name=name,
        scenario_type=scenario_type,
        method=payload.get("method", "weighted"),
        interval_minutes=int(payload.get("interval_minutes", 30)),
        historical_days=int(payload.get("historical_days", 90)),
        forecast_days=int(payload.get("forecast_days", 90)),
        service_level=float(payload.get("service_level", 0.80)),
        target_asa=float(payload.get("target_asa", 30)),
        shrinkage=float(payload.get("shrinkage", 0.30)),
        year=year,
        notes=payload.get("notes", ""),
    )
    db.session.add(scenario)
    db.session.commit()
    return jsonify(success=True, scenario=scenario.to_dict(), updated=False)


@forecasting_bp.route("/scenarios/delete", methods=["POST"])
@login_required
def delete_scenario():
    """Delete a saved scenario."""
    from app.models import ForecastScenario
    dg = _demo_guard()
    if dg:
        return dg

    payload = request.get_json(silent=True) or {}
    sid = payload.get("id")
    if not sid:
        return jsonify(success=False, error="Scenario ID required")

    scenario = ForecastScenario.query.get(int(sid))
    if not scenario:
        return jsonify(success=False, error="Scenario not found")

    db.session.delete(scenario)
    db.session.commit()
    return jsonify(success=True)


@forecasting_bp.route("/scenarios/push", methods=["POST"])
@login_required
def push_scenario():
    """Push a saved scenario to staffing requirements.

    Re-runs generate + Erlang C with the scenario's saved settings,
    then marks it as the active scenario.
    """
    from app.models import ForecastScenario, PlanningUnit
    dg = _demo_guard()
    if dg:
        return dg

    payload = request.get_json(silent=True) or {}
    sid = payload.get("id")
    if not sid:
        return jsonify(success=False, error="Scenario ID required")

    scenario = ForecastScenario.query.get(int(sid))
    if not scenario:
        return jsonify(success=False, error="Scenario not found")

    unit = PlanningUnit.query.get(scenario.planning_unit_id)
    if not unit:
        return jsonify(success=False, error="Planning unit not found")

    # Re-generate forecast using scenario settings
    from app.forecasting.engine import generate_and_save_forecast
    result = generate_and_save_forecast(
        unit.name,
        method=scenario.method,
        historical_days=scenario.historical_days,
        forecast_days=scenario.forecast_days,
    )

    if not result.get("ok"):
        return jsonify(success=False, error=result.get("error", "Generation failed"))

    # Mark this scenario as active, unmark others for same LOB+year
    ForecastScenario.query.filter_by(
        planning_unit_id=unit.id, year=scenario.year
    ).update({"is_active": False})
    scenario.is_active = True
    db.session.commit()

    return jsonify(success=True, message=f"'{scenario.name}' pushed to requirements",
                   scenario=scenario.to_dict())


def _demo_scenarios():
    """Return demo scenario data."""
    return [
        {"id": 1, "lob_name": "Sales Support", "name": "Regular Forecast",
         "scenario_type": "regular", "method": "weighted", "interval_minutes": 30,
         "historical_days": 90, "forecast_days": 90, "service_level": 0.80,
         "target_asa": 30, "shrinkage": 0.30, "is_active": True, "year": 2026,
         "notes": "", "created_at": "2026-09-15T10:00:00", "updated_at": "2026-09-15T10:00:00"},
        {"id": 2, "lob_name": "Sales Support", "name": "Operational Forecast",
         "scenario_type": "operational", "method": "holt_winters", "interval_minutes": 30,
         "historical_days": 60, "forecast_days": 30, "service_level": 0.85,
         "target_asa": 20, "shrinkage": 0.25, "is_active": False, "year": 2026,
         "notes": "Short-term operational planning", "created_at": "2026-09-20T14:00:00", "updated_at": "2026-09-20T14:00:00"},
        {"id": 3, "lob_name": "Sales Support", "name": "Strategic Forecast",
         "scenario_type": "strategic", "method": "ml_gradient_boosting", "interval_minutes": 30,
         "historical_days": 180, "forecast_days": 365, "service_level": 0.80,
         "target_asa": 30, "shrinkage": 0.30, "is_active": False, "year": 2026,
         "notes": "Long-range strategic planning", "created_at": "2026-09-25T09:00:00", "updated_at": "2026-09-25T09:00:00"},
    ]

