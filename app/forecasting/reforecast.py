"""
reforecast.py — Mid-day reforecasting engine
=============================================
Takes today's actual volume so far, compares to the original forecast,
calculates a trend factor per LOB, and adjusts the remaining intervals.

Three adjustment strategies:
  1. ratio   — scale remaining intervals by (actual / forecast) ratio
  2. delta   — shift remaining intervals by the absolute difference
  3. blended — weighted combination of ratio + ML re-prediction

Also recomputes Erlang C staffing requirements for adjusted intervals.
"""

import datetime
import logging
from collections import defaultdict

import numpy as np

log = logging.getLogger("serevo.reforecast")


def _get_today_data(lob, today=None):
    """
    Load today's original forecast and actual data so far for a LOB.
    Returns (forecast_by_slot, actuals_by_slot, planning_unit, lob_setting)
    where each dict maps "HH:MM" → {offered, aht}.
    """
    from app.models import (ForecastInterval, IntervalActual,
                            PlanningUnit, LOBSetting)
    from app.data_source import normalize_lob
    from sqlalchemy import func as sa_func

    if today is None:
        today = datetime.date.today()

    lob_name = normalize_lob(str(lob).strip())
    unit = PlanningUnit.query.filter_by(name=lob_name).first()
    if not unit:
        return {}, {}, None, None

    lob_setting = LOBSetting.query.filter_by(
        planning_unit_id=unit.id
    ).first()

    today_str = today.strftime("%Y-%m-%d")

    # Original forecast for today
    fc_rows = ForecastInterval.query.filter(
        ForecastInterval.planning_unit_id == unit.id,
        sa_func.date(ForecastInterval.timestamp) == today_str,
    ).all()

    forecast_by_slot = {}
    for r in fc_rows:
        slot = r.timestamp.strftime("%H:%M")
        forecast_by_slot[slot] = {
            "offered": float(r.offered or 0),
            "aht": float(r.aht or 0),
        }

    # Actuals received so far today
    act_rows = IntervalActual.query.filter(
        IntervalActual.planning_unit_id == unit.id,
        sa_func.date(IntervalActual.timestamp) == today_str,
    ).all()

    actuals_by_slot = {}
    for r in act_rows:
        slot = r.timestamp.strftime("%H:%M")
        actuals_by_slot[slot] = {
            "offered": float(r.offered or 0),
            "aht": float(r.aht_secs or 0),
        }

    return forecast_by_slot, actuals_by_slot, unit, lob_setting


def compute_trend_factor(forecast_by_slot, actuals_by_slot):
    """
    Compare actual vs forecast for completed intervals.
    Returns {
        ratio: float (actual / forecast volume ratio),
        delta: float (average absolute difference per interval),
        aht_ratio: float (actual / forecast AHT ratio),
        intervals_compared: int,
        actual_total: float,
        forecast_total: float,
        variance_pct: float,
    }
    """
    fc_total = 0.0
    act_total = 0.0
    aht_fc_total = 0.0
    aht_act_total = 0.0
    count = 0

    for slot in sorted(actuals_by_slot.keys()):
        if slot not in forecast_by_slot:
            continue
        fc = forecast_by_slot[slot]
        act = actuals_by_slot[slot]
        fc_total += fc["offered"]
        act_total += act["offered"]
        if fc["aht"] > 0:
            aht_fc_total += fc["aht"]
            aht_act_total += act["aht"]
        count += 1

    if count == 0 or fc_total == 0:
        return {
            "ratio": 1.0,
            "delta": 0.0,
            "aht_ratio": 1.0,
            "intervals_compared": 0,
            "actual_total": 0,
            "forecast_total": 0,
            "variance_pct": 0.0,
        }

    ratio = act_total / fc_total
    delta = (act_total - fc_total) / count
    aht_ratio = (aht_act_total / aht_fc_total) if aht_fc_total > 0 else 1.0
    variance_pct = round(((act_total - fc_total) / fc_total) * 100, 1)

    return {
        "ratio": round(ratio, 4),
        "delta": round(delta, 2),
        "aht_ratio": round(aht_ratio, 4),
        "intervals_compared": count,
        "actual_total": round(act_total, 1),
        "forecast_total": round(fc_total, 1),
        "variance_pct": variance_pct,
    }


def reforecast(lob, strategy="blended", today=None):
    """
    Generate a mid-day reforecast for a LOB.

    Strategies:
      - "ratio":   multiply remaining intervals by the trend ratio
      - "delta":   add the average difference to remaining intervals
      - "blended": 70% ratio + 30% time-of-day weighted adjustment

    Returns {
        success: bool,
        lob: str,
        strategy: str,
        trend: { ratio, delta, aht_ratio, variance_pct, ... },
        cutoff_time: str (last actual interval),
        intervals: [{
            time, original_offered, original_aht, reforecast_offered,
            reforecast_aht, is_actual, agents_required
        }],
        summary: {
            original_total, reforecast_total, change_pct,
            original_agents_total, reforecast_agents_total,
        },
        error: str | None,
    }
    """
    from app.forecasting.engine import erlang_c_staffing, _get_lob_setting

    if today is None:
        today = datetime.date.today()

    forecast_by_slot, actuals_by_slot, unit, lob_setting = \
        _get_today_data(lob, today)

    if not forecast_by_slot:
        return {
            "success": False,
            "error": f"No forecast data for '{lob}' on {today}",
        }

    if not actuals_by_slot:
        return {
            "success": False,
            "error": f"No actuals received yet for '{lob}' on {today}. "
                     "Reforecasting requires at least some actual data.",
        }

    # Compute the trend factor from completed intervals
    trend = compute_trend_factor(forecast_by_slot, actuals_by_slot)

    if trend["intervals_compared"] < 2:
        return {
            "success": False,
            "error": "Need at least 2 completed intervals with actuals "
                     "to generate a meaningful reforecast.",
        }

    # Get Erlang C parameters from LOB settings
    sl_target = None
    target_asa = None
    shrinkage = None
    interval_secs = 1800
    if lob_setting:
        sl_target = lob_setting.service_level_target
        target_asa = lob_setting.target_asa
        shrinkage = lob_setting.shrinkage_pct
        interval_secs = (lob_setting.interval_minutes or 30) * 60

    # Determine the cutoff (last actual interval)
    cutoff_time = max(actuals_by_slot.keys())

    # Build reforecast intervals
    intervals = []
    original_total = 0.0
    reforecast_total = 0.0
    orig_agents_total = 0.0
    refc_agents_total = 0.0

    for slot in sorted(forecast_by_slot.keys()):
        fc = forecast_by_slot[slot]
        original_total += fc["offered"]

        if slot in actuals_by_slot and slot <= cutoff_time:
            # Past interval — use actual data
            act = actuals_by_slot[slot]
            refc_offered = act["offered"]
            refc_aht = act["aht"]
            is_actual = True
        else:
            # Future interval — apply adjustment strategy
            refc_offered, refc_aht = _apply_strategy(
                strategy, fc, trend, slot, forecast_by_slot, actuals_by_slot
            )
            is_actual = False

        reforecast_total += refc_offered

        # Compute Erlang C for reforecast
        ec_orig = erlang_c_staffing(
            fc["offered"], fc["aht"], interval_secs,
            sl_target, target_asa, shrinkage,
        )
        ec_refc = erlang_c_staffing(
            refc_offered, refc_aht, interval_secs,
            sl_target, target_asa, shrinkage,
        )

        orig_agents = ec_orig.get("agents_with_shrinkage", 0)
        refc_agents = ec_refc.get("agents_with_shrinkage", 0)
        orig_agents_total += orig_agents
        refc_agents_total += refc_agents

        intervals.append({
            "time": slot,
            "original_offered": round(fc["offered"], 1),
            "original_aht": round(fc["aht"], 1),
            "reforecast_offered": round(refc_offered, 1),
            "reforecast_aht": round(refc_aht, 1),
            "is_actual": is_actual,
            "original_agents": round(orig_agents, 1),
            "reforecast_agents": round(refc_agents, 1),
        })

    change_pct = round(
        ((reforecast_total - original_total) / original_total * 100)
        if original_total > 0 else 0.0,
        1
    )

    return {
        "success": True,
        "lob": lob,
        "date": today.strftime("%Y-%m-%d"),
        "strategy": strategy,
        "trend": trend,
        "cutoff_time": cutoff_time,
        "intervals": intervals,
        "summary": {
            "original_total": round(original_total, 1),
            "reforecast_total": round(reforecast_total, 1),
            "change_pct": change_pct,
            "original_agents_total": round(orig_agents_total, 1),
            "reforecast_agents_total": round(refc_agents_total, 1),
            "agents_change": round(refc_agents_total - orig_agents_total, 1),
        },
    }


def _apply_strategy(strategy, fc, trend, slot, forecast_by_slot,
                     actuals_by_slot):
    """Apply the chosen adjustment strategy to a future interval."""
    fc_offered = fc["offered"]
    fc_aht = fc["aht"]

    if strategy == "ratio":
        # Simple ratio scaling
        refc_offered = max(0, fc_offered * trend["ratio"])
        refc_aht = max(0, fc_aht * trend["aht_ratio"])

    elif strategy == "delta":
        # Absolute shift
        refc_offered = max(0, fc_offered + trend["delta"])
        refc_aht = fc_aht  # AHT delta is less meaningful

    elif strategy == "blended":
        # Blended: 70% ratio scaling + 30% time-weighted adjustment
        ratio_adj = fc_offered * trend["ratio"]

        # Time-weighted: more recent actuals get more weight
        sorted_actual_slots = sorted(actuals_by_slot.keys(), reverse=True)
        if sorted_actual_slots and slot in forecast_by_slot:
            # Weight recent actuals more heavily
            recent_ratios = []
            for i, act_slot in enumerate(sorted_actual_slots[:6]):
                if act_slot in forecast_by_slot:
                    fc_val = forecast_by_slot[act_slot]["offered"]
                    act_val = actuals_by_slot[act_slot]["offered"]
                    if fc_val > 0:
                        weight = 1.0 / (i + 1)  # recency weight
                        recent_ratios.append((act_val / fc_val, weight))

            if recent_ratios:
                weighted_ratio = (sum(r * w for r, w in recent_ratios)
                                  / sum(w for _, w in recent_ratios))
                time_adj = fc_offered * weighted_ratio
            else:
                time_adj = ratio_adj
        else:
            time_adj = ratio_adj

        refc_offered = max(0, 0.7 * ratio_adj + 0.3 * time_adj)
        refc_aht = max(0, fc_aht * trend["aht_ratio"])

    else:
        # Default to ratio
        refc_offered = max(0, fc_offered * trend["ratio"])
        refc_aht = max(0, fc_aht * trend["aht_ratio"])

    return refc_offered, refc_aht


def reforecast_all_lobs(strategy="blended", today=None):
    """
    Run reforecasting for all active LOBs that have both forecast and
    actuals data for today.
    Returns list of results per LOB.
    """
    from app.models import PlanningUnit

    if today is None:
        today = datetime.date.today()

    units = PlanningUnit.query.filter_by(is_active=True).all()
    results = []

    for unit in units:
        result = reforecast(unit.name, strategy, today)
        results.append(result)

    return results


def get_variance_alerts(threshold_pct=10.0, today=None):
    """
    Check all LOBs for significant variance between forecast and actuals.
    Returns alerts for LOBs where variance exceeds the threshold.
    """
    from app.models import PlanningUnit

    if today is None:
        today = datetime.date.today()

    units = PlanningUnit.query.filter_by(is_active=True).all()
    alerts = []

    for unit in units:
        forecast_by_slot, actuals_by_slot, _, _ = \
            _get_today_data(unit.name, today)

        if not forecast_by_slot or not actuals_by_slot:
            continue

        trend = compute_trend_factor(forecast_by_slot, actuals_by_slot)

        if trend["intervals_compared"] < 2:
            continue

        abs_variance = abs(trend["variance_pct"])
        if abs_variance >= threshold_pct:
            direction = "above" if trend["variance_pct"] > 0 else "below"
            severity = ("critical" if abs_variance >= 25
                        else "warning" if abs_variance >= 15
                        else "info")

            alerts.append({
                "lob": unit.name,
                "variance_pct": trend["variance_pct"],
                "direction": direction,
                "severity": severity,
                "intervals_compared": trend["intervals_compared"],
                "actual_total": trend["actual_total"],
                "forecast_total": trend["forecast_total"],
                "message": (
                    f"{unit.name}: Actuals are {abs_variance}% {direction} "
                    f"forecast ({trend['actual_total']:.0f} actual vs "
                    f"{trend['forecast_total']:.0f} forecast over "
                    f"{trend['intervals_compared']} intervals)"
                ),
            })

    # Sort by severity (critical first)
    severity_order = {"critical": 0, "warning": 1, "info": 2}
    alerts.sort(key=lambda a: severity_order.get(a["severity"], 3))

    return alerts
