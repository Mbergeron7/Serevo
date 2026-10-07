"""
forecasting/engine.py — Forecast analysis, generation & Erlang C
================================================================
Provides:
  - Historical forecast data retrieval (volume, AHT, requirements)
  - Forecast generation via moving average & weighted trends
  - Erlang C staffing calculations
  - What-if scenario modelling
  - Accuracy tracking (forecast vs actuals)
"""

import math
import logging
import datetime
from collections import defaultdict

log = logging.getLogger("serevo.forecasting")

DEFAULT_INTERVAL_MINS = 30
DEFAULT_SERVICE_LEVEL_TARGET = 0.80   # 80 %
DEFAULT_TARGET_ASA = 30               # 30 seconds
DEFAULT_OCCUPANCY = 0.51
DEFAULT_SHRINKAGE = 0.30


# ═══════════════════════════════════════════════════════════════
# LOB OPERATING-HOURS HELPERS
# ═══════════════════════════════════════════════════════════════

def _get_lob_setting(lob):
    """Look up LOBSetting for a given LOB name (via its PlanningUnit)."""
    try:
        from app.models import PlanningUnit, LOBSetting
        from app.data_source import normalize_lob
        lob_norm = normalize_lob(str(lob).strip())
        pu = PlanningUnit.query.filter_by(name=lob_norm).first()
        if pu and pu.lob_setting:
            return pu.lob_setting
    except Exception as e:
        log.warning("Could not load LOB setting for '%s': %s", lob, e)
    return None


def _operating_slots(lob_setting, target_date):
    """
    Return sorted list of time-slot strings (e.g. '08:00', '08:30', …)
    covering every interval within the LOB's operating hours for a given date.

    If no LOB setting, returns None (caller should fall back to history-only).
    """
    if not lob_setting:
        return None

    dow = target_date.weekday()
    start_str, end_str = lob_setting.get_hours_for_day(dow)
    if not start_str or not end_str:
        return None

    interval = lob_setting.interval_minutes or DEFAULT_INTERVAL_MINS

    try:
        sh, sm = int(start_str.split(":")[0]), int(start_str.split(":")[1])
        eh, em = int(end_str.split(":")[0]), int(end_str.split(":")[1])
    except (ValueError, IndexError):
        return None

    start_mins = sh * 60 + sm
    end_mins = eh * 60 + em
    if end_mins <= start_mins:
        return None

    slots = []
    m = start_mins
    while m < end_mins:
        slots.append(f"{m // 60:02d}:{m % 60:02d}")
        m += interval
    return slots


def _fill_operating_hours(forecast_rows, lob_setting, fc_date):
    """
    Given forecast rows for a single date (from historical averages) and the
    LOB's operating hours, ensure every operating interval is represented.

    For intervals with no historical data, volume is distributed proportionally
    based on neighboring known intervals, or evenly if no history at all.
    AHT for missing slots uses the day's average AHT.

    Returns the complete list of rows for that date.
    """
    slots = _operating_slots(lob_setting, fc_date)
    if not slots:
        return forecast_rows  # no LOB setting — return as-is

    date_str = fc_date.strftime("%Y-%m-%d")

    # Build lookup of existing forecast data by time
    existing = {}
    for row in forecast_rows:
        existing[row["time"]] = row

    # If we have no historical data at all, return zero-volume rows
    # (Erlang C will produce 0 agents required — better than nothing)
    if not existing:
        return [{"date": date_str, "time": t, "offered": 0, "aht": 0}
                for t in slots]

    # Compute average AHT from known intervals (volume-weighted)
    total_vol = sum(r["offered"] for r in existing.values())
    if total_vol > 0:
        avg_aht = sum(r["offered"] * r["aht"] for r in existing.values()) / total_vol
    else:
        avg_aht = sum(r["aht"] for r in existing.values()) / len(existing) if existing else 0

    # For missing slots, distribute volume proportionally.
    # Strategy: use the average volume across known slots.
    known_count = len(existing)
    avg_vol = total_vol / known_count if known_count > 0 else 0

    result = []
    for t in slots:
        if t in existing:
            result.append(existing[t])
        else:
            # Fill with average volume and AHT from known intervals
            result.append({
                "date": date_str,
                "time": t,
                "offered": round(avg_vol, 2),
                "aht": round(avg_aht, 1),
            })

    return result


# ═══════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════

def _get_val(row, *keys):
    """Case-insensitive column lookup — try each key as-is, title-case, and lower."""
    for k in keys:
        for variant in (k, k.title(), k.lower(), k.upper()):
            v = row.get(variant)
            if v is not None and str(v).strip():
                return str(v).strip()
    return ""


# ═══════════════════════════════════════════════════════════════
# DATA RETRIEVAL
# ═══════════════════════════════════════════════════════════════

def _get_sheet():
    """Return the Google Sheet object, or None."""
    try:
        from app.data_source import _open_capacity_sheet
        sheet, err = _open_capacity_sheet()
        return None if err else sheet
    except Exception:
        return None


def get_available_lobs(sheet=None):
    """Return sorted list of distinct LOB names from forecast data."""
    import os
    # When using Postgres, pull LOBs from the PlanningUnit table
    if os.environ.get("DATA_SOURCE", "").strip().lower() == "postgres":
        try:
            from app.models import PlanningUnit
            units = PlanningUnit.query.order_by(PlanningUnit.name).all()
            if units:
                return [u.name for u in units]
        except Exception:
            pass

    try:
        from app.data_source import SheetSource, normalize_lob
        src = SheetSource()
        rows, err = src._records("FORECAST RAW")
        if err or not rows:
            # Fall back to employee roster LOBs
            from app.people.manager import get_employees
            employees, err2 = get_employees(sheet)
            if err2:
                return []
            lobs = set()
            for emp in employees:
                status = str(emp.get("Status", "")).strip().lower()
                if status in ("inactive", "terminated", "deleted"):
                    continue
                lob = (emp.get("Latest Skill Name") or "").strip()
                if lob:
                    lobs.add(normalize_lob(lob))
            return sorted(lobs)
        lobs = set()
        for r in rows:
            lob = _get_val(r, "LOB")
            if lob:
                lobs.add(normalize_lob(lob))
        return sorted(lobs)
    except Exception:
        return []


def get_forecast_data(lob, start_date, end_date, sheet=None):
    """
    Retrieve stored forecast data for a LOB across a date range.
    Returns list of {date, time, offered, aht} sorted by timestamp.
    """
    import os
    # When using Postgres, read from ForecastInterval table
    if os.environ.get("DATA_SOURCE", "").strip().lower() == "postgres":
        try:
            from app.data_source import normalize_lob
            from app.models import ForecastInterval, PlanningUnit
            from sqlalchemy import func as sa_func
            normalized = normalize_lob(str(lob).strip())
            unit = PlanningUnit.query.filter_by(name=normalized).first()
            if not unit:
                return [], f"No planning unit found for '{lob}'"
            start_str = start_date.strftime("%Y-%m-%d")
            end_str = end_date.strftime("%Y-%m-%d")
            rows = ForecastInterval.query.filter(
                ForecastInterval.planning_unit_id == unit.id,
                sa_func.date(ForecastInterval.timestamp) >= start_str,
                sa_func.date(ForecastInterval.timestamp) <= end_str,
            ).order_by(ForecastInterval.timestamp).all()
            results = []
            for r in rows:
                ts = r.timestamp
                results.append({
                    "date": ts.strftime("%Y-%m-%d"),
                    "time": ts.strftime("%H:%M"),
                    "offered": float(r.offered or 0),
                    "aht": float(r.aht or 0),
                })
            if results:
                return results, None
            # DB had the unit but no rows — fall through to sheets
        except Exception as e:
            log.info(f"DB forecast lookup failed for '{lob}': {e}")

    # Fallback: try Google Sheets
    try:
        from app.data_source import SheetSource
        src = SheetSource()
        rows, err = src._records("FORECAST RAW")
        if err or not rows:
            return [], "No forecast data available"

        start_str = start_date.strftime("%Y-%m-%d")
        end_str = end_date.strftime("%Y-%m-%d")
        results = []

        for r in rows:
            row_lob = _get_val(r, "LOB")
            if row_lob.lower() != lob.strip().lower():
                continue
            # Support separate Date + Timestamp columns, or combined Timestamp
            date_col = _get_val(r, "Date")
            ts_col = _get_val(r, "Timestamp")
            if date_col and len(date_col) >= 10:
                date_part = date_col[:10]
                time_part = ts_col[:5] if ts_col else "00:00"
            elif len(ts_col) > 10:
                date_part = ts_col[:10]
                time_part = ts_col[11:16]
            else:
                continue
            if date_part < start_str or date_part > end_str:
                continue
            offered_raw = _get_val(r, "Offered", "offered") or "0"
            aht_raw = _get_val(r, "AHT", "aht") or "0"
            results.append({
                "date": date_part,
                "time": time_part,
                "offered": float(offered_raw),
                "aht": float(aht_raw),
            })

        results.sort(key=lambda x: (x["date"], x["time"]))
        return results, None
    except Exception as e:
        return [], str(e)


def get_requirements_data(lob, start_date, end_date, sheet=None):
    """
    Retrieve stored requirements data for a LOB across a date range.
    Returns list of {date, time, agents_required} sorted by timestamp.
    """
    import os
    # When using Postgres, read from RequirementInterval table
    if os.environ.get("DATA_SOURCE", "").strip().lower() == "postgres":
        try:
            from app.data_source import normalize_lob
            from app.models import RequirementInterval, PlanningUnit
            from sqlalchemy import func as sa_func
            normalized = normalize_lob(str(lob).strip())
            unit = PlanningUnit.query.filter_by(name=normalized).first()
            if not unit:
                return [], None  # No requirements yet, not an error
            start_str = start_date.strftime("%Y-%m-%d")
            end_str = end_date.strftime("%Y-%m-%d")
            rows = RequirementInterval.query.filter(
                RequirementInterval.planning_unit_id == unit.id,
                sa_func.date(RequirementInterval.timestamp) >= start_str,
                sa_func.date(RequirementInterval.timestamp) <= end_str,
            ).order_by(RequirementInterval.timestamp).all()
            results = []
            for r in rows:
                ts = r.timestamp
                results.append({
                    "date": ts.strftime("%Y-%m-%d"),
                    "time": ts.strftime("%H:%M"),
                    "agents_required": float(r.agents_required or 0),
                })
            return results, None
        except Exception as e:
            return [], str(e)

    try:
        from app.data_source import SheetSource
        src = SheetSource()
        rows, err = src._records("REQUIREMENTS RAW")
        if err or not rows:
            return [], "No requirements data available"

        start_str = start_date.strftime("%Y-%m-%d")
        end_str = end_date.strftime("%Y-%m-%d")
        results = []

        for r in rows:
            row_lob = _get_val(r, "LOB")
            if row_lob.lower() != lob.strip().lower():
                continue
            date_col = _get_val(r, "Date")
            ts_col = _get_val(r, "Timestamp")
            if date_col and len(date_col) >= 10:
                date_part = date_col[:10]
                time_part = ts_col[:5] if ts_col else "00:00"
            elif len(ts_col) > 10:
                date_part = ts_col[:10]
                time_part = ts_col[11:16]
            else:
                continue
            if date_part < start_str or date_part > end_str:
                continue
            req_raw = _get_val(r, "Agents Required", "agents_required") or "0"
            results.append({
                "date": date_part,
                "time": time_part,
                "agents_required": float(req_raw),
            })

        results.sort(key=lambda x: (x["date"], x["time"]))
        return results, None
    except Exception as e:
        return [], str(e)


def get_historical_actuals(lob, start_date, end_date):
    """
    Retrieve actual call volume from IntervalActual for a LOB across a date range.
    Returns list of {date, time, offered, aht} sorted by timestamp.
    Used for: displaying actuals on the forecasting page AND as historical
    input when generating forecasts.
    """
    import os
    if os.environ.get("DATA_SOURCE", "").strip().lower() != "postgres":
        return [], None
    try:
        from app.data_source import normalize_lob
        from app.models import IntervalActual, PlanningUnit
        from sqlalchemy import func as sa_func
        normalized = normalize_lob(str(lob).strip())
        unit = PlanningUnit.query.filter_by(name=normalized).first()
        if not unit:
            return [], f"No planning unit found for '{lob}'"
        start_str = start_date.strftime("%Y-%m-%d")
        end_str = end_date.strftime("%Y-%m-%d")
        rows = IntervalActual.query.filter(
            IntervalActual.planning_unit_id == unit.id,
            sa_func.date(IntervalActual.timestamp) >= start_str,
            sa_func.date(IntervalActual.timestamp) <= end_str,
        ).order_by(IntervalActual.timestamp).all()
        results = []
        for r in rows:
            ts = r.timestamp
            results.append({
                "date": ts.strftime("%Y-%m-%d"),
                "time": ts.strftime("%H:%M"),
                "offered": float(r.offered or 0),
                "answered": float(r.answered or 0),
                "abandoned": float(r.abandoned or 0),
                "aht": float(r.aht_secs or 0),
                "asa": float(r.asa_secs or 0),
            })
        return results, None
    except Exception as e:
        return [], str(e)


def get_available_dates(lob, sheet=None):
    """Return sorted list of distinct dates with forecast data for a LOB."""
    try:
        from app.data_source import SheetSource
        src = SheetSource()
        rows, err = src._records("FORECAST RAW")
        if err or not rows:
            return []
        dates = set()
        for r in rows:
            row_lob = _get_val(r, "LOB")
            if row_lob.lower() != lob.strip().lower():
                continue
            date_col = _get_val(r, "Date")
            ts = _get_val(r, "Timestamp")
            if date_col and len(date_col) >= 10:
                dates.add(date_col[:10])
            elif len(ts) >= 10:
                dates.add(ts[:10])
        return sorted(dates)
    except Exception:
        return []


# ═══════════════════════════════════════════════════════════════
# DAILY AGGREGATION HELPERS
# ═══════════════════════════════════════════════════════════════

def aggregate_daily(intervals):
    """
    Aggregate interval data into daily summaries.
    Input: list of dicts with 'date', 'offered', 'aht' keys.
    Returns: dict keyed by date → {total_offered, avg_aht, intervals, peak_offered}
    """
    by_date = defaultdict(list)
    for row in intervals:
        by_date[row["date"]].append(row)

    daily = {}
    for date_str, rows in sorted(by_date.items()):
        total_offered = sum(r["offered"] for r in rows)
        total_aht_weighted = sum(r["offered"] * r["aht"] for r in rows)
        avg_aht = total_aht_weighted / total_offered if total_offered > 0 else 0
        peak_offered = max(r["offered"] for r in rows) if rows else 0

        daily[date_str] = {
            "date": date_str,
            "total_offered": round(total_offered, 1),
            "avg_aht": round(avg_aht, 1),
            "intervals": len(rows),
            "peak_offered": round(peak_offered, 1),
        }
    return daily


# ═══════════════════════════════════════════════════════════════
# FORECAST GENERATION
# ═══════════════════════════════════════════════════════════════

def generate_forecast_moving_avg(lob, historical_days, forecast_days,
                                  window=7, sheet=None):
    """
    Generate a forecast using Simple Moving Average.

    Looks back `historical_days` from today, computes a `window`-day
    moving average for each interval time slot, then projects forward
    `forecast_days` into the future.

    Returns {
        method: "moving_average",
        window: int,
        historical: [{date, time, offered, aht}],
        forecast: [{date, time, offered, aht}],
    }
    """
    today = datetime.date.today()
    hist_start = today - datetime.timedelta(days=historical_days)
    hist_end = today - datetime.timedelta(days=1)

    # Try actuals first (from IntervalActual / CallPotential sync),
    # then fall back to stored forecast data
    historical, err = get_historical_actuals(lob, hist_start, hist_end)
    if not historical:
        historical, err = get_forecast_data(lob, hist_start, hist_end, sheet)
    if err or not historical:
        return {
            "method": "moving_average",
            "window": window,
            "historical": [],
            "forecast": [],
            "error": err or "No historical data found",
        }

    # Group by (day_of_week, time) for pattern recognition
    # Simple approach: average each time slot across the last `window` days
    by_time = defaultdict(list)
    dates_seen = sorted(set(r["date"] for r in historical))

    # Use only the last `window` days
    recent_dates = dates_seen[-window:] if len(dates_seen) >= window else dates_seen
    recent_set = set(recent_dates)

    for row in historical:
        if row["date"] in recent_set:
            by_time[row["time"]].append(row)

    # Build averaged profile per time slot
    avg_profile = {}
    for time_str, rows in by_time.items():
        avg_offered = sum(r["offered"] for r in rows) / len(rows)
        total_weight = sum(r["offered"] for r in rows)
        avg_aht = (sum(r["offered"] * r["aht"] for r in rows) / total_weight
                   if total_weight > 0 else 0)
        avg_profile[time_str] = {
            "offered": round(avg_offered, 2),
            "aht": round(avg_aht, 1),
        }

    # Look up LOB operating hours
    lob_setting = _get_lob_setting(lob)

    # Project forward
    forecast = []
    for d in range(1, forecast_days + 1):
        fc_date = today + datetime.timedelta(days=d - 1)
        date_str = fc_date.strftime("%Y-%m-%d")
        day_rows = []
        for time_str in sorted(avg_profile.keys()):
            prof = avg_profile[time_str]
            day_rows.append({
                "date": date_str,
                "time": time_str,
                "offered": prof["offered"],
                "aht": prof["aht"],
            })
        # Fill all operating-hour intervals
        day_rows = _fill_operating_hours(day_rows, lob_setting, fc_date)
        forecast.extend(day_rows)

    return {
        "method": "moving_average",
        "window": window,
        "historical_days_used": len(recent_dates),
        "historical": historical,
        "forecast": forecast,
    }


def generate_forecast_weighted(lob, historical_days, forecast_days,
                                sheet=None):
    """
    Generate a forecast using Weighted Moving Average.

    More recent days get higher weight. Same-day-of-week matching
    is applied where possible (e.g., Mondays weighted by past Mondays).

    Returns same shape as moving_avg.
    """
    today = datetime.date.today()
    hist_start = today - datetime.timedelta(days=historical_days)
    hist_end = today - datetime.timedelta(days=1)

    historical, err = get_historical_actuals(lob, hist_start, hist_end)
    if not historical:
        historical, err = get_forecast_data(lob, hist_start, hist_end, sheet)
    if err or not historical:
        return {
            "method": "weighted_trend",
            "historical": [],
            "forecast": [],
            "error": err or "No historical data found",
        }

    # Group by day-of-week + time
    by_dow_time = defaultdict(list)
    for row in historical:
        try:
            d = datetime.datetime.strptime(row["date"], "%Y-%m-%d").date()
            dow = d.weekday()
            by_dow_time[(dow, row["time"])].append((row, d))
        except Exception:
            continue

    # Weight function: more recent = higher weight
    def calc_weight(row_date, ref_date):
        delta = (ref_date - row_date).days
        if delta <= 0:
            return 1.0
        return 1.0 / (1.0 + 0.1 * delta)

    # Look up LOB operating hours
    lob_setting = _get_lob_setting(lob)

    forecast = []
    for d_offset in range(forecast_days):
        fc_date = today + datetime.timedelta(days=d_offset)
        date_str = fc_date.strftime("%Y-%m-%d")
        dow = fc_date.weekday()

        # For each time slot, compute weighted average
        # First try same-DOW data, fall back to all data for that time
        time_slots = set()
        for (dw, ts) in by_dow_time.keys():
            time_slots.add(ts)

        day_rows = []
        for time_str in sorted(time_slots):
            entries = by_dow_time.get((dow, time_str), [])
            if not entries:
                # Fall back to any DOW
                for dw in range(7):
                    entries = by_dow_time.get((dw, time_str), [])
                    if entries:
                        break
            if not entries:
                continue

            total_w = 0
            w_offered = 0
            w_aht_num = 0

            for row, row_date in entries:
                w = calc_weight(row_date, today)
                total_w += w
                w_offered += w * row["offered"]
                w_aht_num += w * row["offered"] * row["aht"]

            if total_w > 0:
                avg_offered = w_offered / total_w
                avg_aht = w_aht_num / w_offered if w_offered > 0 else 0
            else:
                avg_offered = 0
                avg_aht = 0

            day_rows.append({
                "date": date_str,
                "time": time_str,
                "offered": round(avg_offered, 2),
                "aht": round(avg_aht, 1),
            })

        # Fill all operating-hour intervals
        day_rows = _fill_operating_hours(day_rows, lob_setting, fc_date)
        forecast.extend(day_rows)

    return {
        "method": "weighted_trend",
        "historical_days_used": historical_days,
        "historical": historical,
        "forecast": forecast,
    }


# ═══════════════════════════════════════════════════════════════
# HOLT-WINTERS (TRIPLE EXPONENTIAL SMOOTHING)
# ═══════════════════════════════════════════════════════════════

def _holt_winters_forecast(series, season_length=7, forecast_steps=7,
                            alpha=0.3, beta=0.05, gamma=0.15):
    """
    Multiplicative Holt-Winters triple exponential smoothing.

    Args:
        series: list of numeric values (daily totals), at least 2*season_length
        season_length: seasonal period (7 for weekly pattern)
        forecast_steps: how many steps ahead to forecast
        alpha: level smoothing (0-1)
        beta: trend smoothing (0-1)
        gamma: seasonal smoothing (0-1)

    Returns list of forecast_steps values.
    """
    n = len(series)
    if n < season_length * 2:
        # Not enough data — fall back to simple average
        avg = sum(series) / n if n > 0 else 0
        return [avg] * forecast_steps

    # Initialise level as average of first season
    first_season = series[:season_length]
    level = sum(first_season) / season_length

    # Initialise trend from first two seasons
    second_season = series[season_length:season_length * 2]
    trend = (sum(second_season) - sum(first_season)) / (season_length ** 2)

    # Initialise seasonal indices (multiplicative)
    seasonals = []
    for i in range(season_length):
        avg_s = sum(first_season) / season_length
        seasonals.append(first_season[i] / avg_s if avg_s > 0 else 1.0)

    # Smooth through the observed data
    for i in range(n):
        val = series[i]
        s_idx = i % season_length
        prev_seasonal = seasonals[s_idx]

        # Guard against zero seasonal
        if prev_seasonal == 0:
            prev_seasonal = 0.001

        new_level = alpha * (val / prev_seasonal) + (1 - alpha) * (level + trend)
        new_trend = beta * (new_level - level) + (1 - beta) * trend
        new_seasonal = gamma * (val / new_level if new_level > 0 else 1.0) + (1 - gamma) * prev_seasonal

        level = new_level
        trend = new_trend
        seasonals[s_idx] = new_seasonal

    # Forecast
    forecasts = []
    for step in range(1, forecast_steps + 1):
        s_idx = (n + step - 1) % season_length
        fc = (level + step * trend) * seasonals[s_idx]
        forecasts.append(max(0, fc))

    return forecasts


def generate_forecast_holt_winters(lob, historical_days, forecast_days,
                                    sheet=None):
    """
    Generate a forecast using Holt-Winters triple exponential smoothing.

    Uses multiplicative seasonality with a 7-day seasonal cycle.
    Decomposes each day into intraday patterns (by time slot) and
    applies Holt-Winters to the daily totals, then distributes
    across intervals using the average intraday shape.
    """
    today = datetime.date.today()
    hist_start = today - datetime.timedelta(days=historical_days)
    hist_end = today - datetime.timedelta(days=1)

    historical, err = get_historical_actuals(lob, hist_start, hist_end)
    if not historical:
        historical, err = get_forecast_data(lob, hist_start, hist_end, sheet)
    if err or not historical:
        return {
            "method": "holt_winters",
            "historical": [],
            "forecast": [],
            "error": err or "No historical data found",
        }

    # Aggregate historical to daily totals
    daily_totals = defaultdict(lambda: {"offered": 0, "aht_sum": 0, "count": 0})
    for row in historical:
        d = row["date"]
        daily_totals[d]["offered"] += row["offered"]
        daily_totals[d]["aht_sum"] += row["offered"] * row["aht"]
        daily_totals[d]["count"] += 1

    # Sort by date to get an ordered series
    sorted_dates = sorted(daily_totals.keys())
    if len(sorted_dates) < 14:
        return {
            "method": "holt_winters",
            "historical": historical,
            "forecast": [],
            "error": "Need at least 14 days of history for Holt-Winters",
        }

    daily_offered = [daily_totals[d]["offered"] for d in sorted_dates]
    daily_aht = [
        daily_totals[d]["aht_sum"] / daily_totals[d]["offered"]
        if daily_totals[d]["offered"] > 0 else 0
        for d in sorted_dates
    ]

    # Run Holt-Winters on daily offered volumes
    hw_offered = _holt_winters_forecast(daily_offered, season_length=7,
                                         forecast_steps=forecast_days)
    hw_aht = _holt_winters_forecast(daily_aht, season_length=7,
                                     forecast_steps=forecast_days)

    # Build intraday distribution shape by DOW
    # (average fraction of daily volume in each time slot)
    dow_shape = defaultdict(lambda: defaultdict(list))  # dow -> time -> [fractions]
    for row in historical:
        try:
            d = datetime.datetime.strptime(row["date"], "%Y-%m-%d").date()
            dow = d.weekday()
            day_total = daily_totals[row["date"]]["offered"]
            if day_total > 0:
                dow_shape[dow][row["time"]].append(row["offered"] / day_total)
        except Exception:
            continue

    avg_shape = {}  # (dow, time) -> fraction
    all_times = set()
    for dow in range(7):
        for time_str, fracs in dow_shape[dow].items():
            avg_shape[(dow, time_str)] = sum(fracs) / len(fracs)
            all_times.add(time_str)

    # Look up LOB operating hours
    lob_setting = _get_lob_setting(lob)

    # Distribute daily forecasts across time slots
    forecast = []
    for d_offset in range(forecast_days):
        fc_date = today + datetime.timedelta(days=d_offset)
        date_str = fc_date.strftime("%Y-%m-%d")
        dow = fc_date.weekday()

        day_offered = hw_offered[d_offset]
        day_aht = hw_aht[d_offset]

        day_rows = []
        # Get shape for this DOW, normalise
        shape_for_day = {t: avg_shape.get((dow, t), 0) for t in all_times}
        shape_total = sum(shape_for_day.values())

        for time_str in sorted(all_times):
            frac = shape_for_day[time_str] / shape_total if shape_total > 0 else 1.0 / max(1, len(all_times))
            day_rows.append({
                "date": date_str,
                "time": time_str,
                "offered": round(day_offered * frac, 2),
                "aht": round(day_aht, 1),
            })

        day_rows = _fill_operating_hours(day_rows, lob_setting, fc_date)
        forecast.extend(day_rows)

    return {
        "method": "holt_winters",
        "historical_days_used": len(sorted_dates),
        "historical": historical,
        "forecast": forecast,
    }


# ═══════════════════════════════════════════════════════════════
# HOLIDAY / SPECIAL DAY ADJUSTMENTS
# ═══════════════════════════════════════════════════════════════

def _get_holidays_in_range(start_date, end_date):
    """Return set of date strings that are holidays."""
    try:
        from app.models import Holiday
        holidays = Holiday.query.filter(
            Holiday.date >= start_date,
            Holiday.date <= end_date,
        ).all()
        return {h.date.isoformat() if hasattr(h.date, 'isoformat') else str(h.date)
                for h in holidays}
    except Exception:
        return set()


def _compute_holiday_factor(historical, holidays_set):
    """
    Compute the average ratio of holiday volume to same-DOW non-holiday volume.

    Returns a multiplier (e.g. 0.6 means holidays have 60% of normal volume).
    Returns 1.0 if insufficient data.
    """
    if not holidays_set:
        return 1.0

    daily = defaultdict(lambda: {"offered": 0})
    for row in historical:
        daily[row["date"]]["offered"] += row["offered"]

    # Split into holiday vs non-holiday by DOW
    dow_non_holiday = defaultdict(list)
    holiday_volumes = []

    for date_str, data in daily.items():
        try:
            d = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
            dow = d.weekday()
        except Exception:
            continue

        if date_str in holidays_set:
            holiday_volumes.append(data["offered"])
        else:
            dow_non_holiday[dow].append(data["offered"])

    if not holiday_volumes:
        return 1.0

    avg_holiday = sum(holiday_volumes) / len(holiday_volumes)

    # Average of all non-holiday daily volumes
    all_non_holiday = []
    for volumes in dow_non_holiday.values():
        all_non_holiday.extend(volumes)
    if not all_non_holiday:
        return 1.0

    avg_normal = sum(all_non_holiday) / len(all_non_holiday)
    if avg_normal == 0:
        return 1.0

    return avg_holiday / avg_normal


def apply_holiday_adjustments(forecast, historical=None):
    """
    Adjust forecast intervals for known holidays.

    If historical data is provided, computes a data-driven holiday factor.
    Otherwise uses a default 0.5 factor (50% of normal volume).
    """
    if not forecast:
        return forecast

    # Get date range of forecast
    dates = [row["date"] for row in forecast]
    try:
        start = datetime.datetime.strptime(min(dates), "%Y-%m-%d").date()
        end = datetime.datetime.strptime(max(dates), "%Y-%m-%d").date()
    except Exception:
        return forecast

    holidays_set = _get_holidays_in_range(start, end)
    if not holidays_set:
        return forecast

    # Compute factor from historical data if available
    if historical:
        hist_dates = [r["date"] for r in historical]
        try:
            hist_start = datetime.datetime.strptime(min(hist_dates), "%Y-%m-%d").date()
            hist_end = datetime.datetime.strptime(max(hist_dates), "%Y-%m-%d").date()
        except Exception:
            hist_start = start - datetime.timedelta(days=90)
            hist_end = start - datetime.timedelta(days=1)
        hist_holidays = _get_holidays_in_range(hist_start, hist_end)
        factor = _compute_holiday_factor(historical, hist_holidays)
    else:
        factor = 0.5  # default: holidays get ~50% of normal volume

    # Apply factor to forecast intervals on holiday dates
    adjusted = []
    for row in forecast:
        if row["date"] in holidays_set:
            row = dict(row)
            row["offered"] = round(row["offered"] * factor, 2)
            row["is_holiday"] = True
        adjusted.append(row)

    return adjusted


# ═══════════════════════════════════════════════════════════════
# AUTO-METHOD SELECTION
# ═══════════════════════════════════════════════════════════════

def _mape(actual, predicted):
    """Mean Absolute Percentage Error."""
    if not actual or not predicted or len(actual) != len(predicted):
        return float('inf')

    errors = []
    for a, p in zip(actual, predicted):
        if a > 0:
            errors.append(abs(a - p) / a)

    return (sum(errors) / len(errors) * 100) if errors else float('inf')


def auto_select_method(lob, historical_days=90, sheet=None):
    """
    Try all forecast methods on a holdout set and return the method
    with the lowest MAPE.

    Uses the last 7 days of historical data as the holdout set and
    trains on everything before that.

    Returns: {best_method, results: {method: mape, ...}}
    """
    holdout_days = 7
    train_days = historical_days - holdout_days

    if train_days < 14:
        return {"best_method": "weighted_trend",
                "results": {"note": "Not enough history for auto-select"}}

    today = datetime.date.today()
    holdout_start = today - datetime.timedelta(days=holdout_days)
    holdout_end = today - datetime.timedelta(days=1)

    # Get holdout actuals
    actual_data, err = get_forecast_data(lob, holdout_start, holdout_end, sheet)
    if err or not actual_data:
        return {"best_method": "weighted_trend",
                "results": {"note": f"No holdout data: {err}"}}

    # Daily totals for holdout
    actual_daily = defaultdict(float)
    for row in actual_data:
        actual_daily[row["date"]] += row["offered"]

    holdout_dates = sorted(actual_daily.keys())
    actual_values = [actual_daily[d] for d in holdout_dates]

    methods = {
        "moving_average": lambda: generate_forecast_moving_avg(
            lob, train_days, holdout_days, 7, sheet),
        "weighted_trend": lambda: generate_forecast_weighted(
            lob, train_days, holdout_days, sheet),
        "holt_winters": lambda: generate_forecast_holt_winters(
            lob, train_days, holdout_days, sheet),
    }
    # Include ML method if actuals data exists
    try:
        from app.forecasting.ml_engine import generate_forecast_ml
        methods["ml_gradient_boosting"] = lambda: generate_forecast_ml(
            lob, train_days, holdout_days, sheet)
    except ImportError:
        pass

    results = {}
    for method_name, gen_fn in methods.items():
        try:
            result = gen_fn()
            fc = result.get("forecast", [])
            if not fc:
                results[method_name] = float('inf')
                continue

            # Aggregate forecast to daily totals
            fc_daily = defaultdict(float)
            for row in fc:
                fc_daily[row["date"]] += row["offered"]

            # Match to holdout dates
            predicted_values = [fc_daily.get(d, 0) for d in holdout_dates]
            results[method_name] = round(_mape(actual_values, predicted_values), 2)
        except Exception as e:
            log.warning(f"Auto-select {method_name} failed: {e}")
            results[method_name] = float('inf')

    best = min(results, key=results.get) if results else "weighted_trend"

    return {
        "best_method": best,
        "results": results,
    }


# ═══════════════════════════════════════════════════════════════
# ERLANG C
# ═══════════════════════════════════════════════════════════════

def _erlang_c(agents, traffic_intensity):
    """
    Compute Erlang C probability — the probability a call waits.
    agents (N): number of agents (integer >= 1)
    traffic_intensity (A): offered load in Erlangs = (volume * AHT) / interval_seconds
    Returns P(wait) between 0 and 1.
    """
    N = int(agents)
    A = float(traffic_intensity)

    if N <= 0 or A <= 0:
        return 0.0
    if A >= N:
        return 1.0  # overloaded

    # Compute Erlang C using the iterative method for numerical stability
    # P(wait) = (A^N / N!) * (N / (N - A)) / (sum_{k=0}^{N-1} A^k/k! + A^N/N! * N/(N-A))

    # Build A^k / k! iteratively
    powers = [1.0]  # A^0 / 0! = 1
    for k in range(1, N + 1):
        powers.append(powers[-1] * A / k)

    sum_terms = sum(powers[:-1])  # sum_{k=0}^{N-1}
    last_term = powers[N] * N / (N - A)

    ec = last_term / (sum_terms + last_term)
    return min(max(ec, 0.0), 1.0)


def _service_level(agents, traffic_intensity, target_asa, aht):
    """
    Compute service level: P(answer within target_asa seconds).
    SL = 1 - Ec * exp(-(N - A) * target_asa / AHT)
    """
    N = int(agents)
    A = float(traffic_intensity)
    if N <= 0 or aht <= 0:
        return 0.0
    if A >= N:
        return 0.0

    ec = _erlang_c(N, A)
    sl = 1.0 - ec * math.exp(-(N - A) * target_asa / aht)
    return min(max(sl, 0.0), 1.0)


def erlang_c_staffing(offered, aht, interval_seconds=1800,
                       service_level_target=None, target_asa=None,
                       shrinkage=None, occupancy=None):
    """
    Calculate required agents using Erlang C.

    Parameters:
        offered: number of contacts offered in the interval
        aht: average handle time in seconds
        interval_seconds: length of the interval in seconds (default 1800 = 30 min)
        service_level_target: target SL (default 0.80 = 80%)
        target_asa: target average speed of answer in seconds (default 30)
        shrinkage: shrinkage factor (default 0.30)
        occupancy: max occupancy (not used in base Erlang C but reported)

    Returns dict with: agents_raw, agents_with_shrinkage, service_level,
                        erlang_c_prob, traffic_intensity, occupancy
    """
    if service_level_target is None:
        service_level_target = DEFAULT_SERVICE_LEVEL_TARGET
    if target_asa is None:
        target_asa = DEFAULT_TARGET_ASA
    if shrinkage is None:
        shrinkage = DEFAULT_SHRINKAGE
    if occupancy is None:
        occupancy = DEFAULT_OCCUPANCY

    if offered <= 0 or aht <= 0:
        return {
            "agents_raw": 0,
            "agents_with_shrinkage": 0,
            "service_level": 1.0,
            "erlang_c_prob": 0.0,
            "traffic_intensity": 0.0,
            "occupancy": 0.0,
        }

    # Traffic intensity (Erlangs)
    traffic = (offered * aht) / interval_seconds

    # Find minimum agents to meet SL target
    agents = max(1, math.ceil(traffic))  # start at traffic intensity
    max_agents = agents + 500  # safety cap

    while agents < max_agents:
        sl = _service_level(agents, traffic, target_asa, aht)
        if sl >= service_level_target:
            break
        agents += 1

    actual_occupancy = traffic / agents if agents > 0 else 0
    sl_achieved = _service_level(agents, traffic, target_asa, aht)
    ec_prob = _erlang_c(agents, traffic)

    # Apply shrinkage
    agents_shrinkage = math.ceil(agents / (1.0 - shrinkage)) if shrinkage < 1.0 else agents

    return {
        "agents_raw": agents,
        "agents_with_shrinkage": agents_shrinkage,
        "service_level": round(sl_achieved, 4),
        "erlang_c_prob": round(ec_prob, 4),
        "traffic_intensity": round(traffic, 2),
        "occupancy": round(actual_occupancy, 4),
    }


def compute_requirements_from_forecast(forecast_intervals,
                                        service_level_target=None,
                                        target_asa=None,
                                        shrinkage=None):
    """
    Run Erlang C on every interval in a forecast to produce staffing requirements.

    Input: list of {date, time, offered, aht}
    Returns: list of {date, time, offered, aht, agents_required, agents_with_shrinkage,
                       service_level, traffic_intensity}
    """
    results = []
    for row in forecast_intervals:
        ec = erlang_c_staffing(
            offered=row["offered"],
            aht=row["aht"],
            service_level_target=service_level_target,
            target_asa=target_asa,
            shrinkage=shrinkage,
        )
        results.append({
            **row,
            "agents_required": ec["agents_raw"],
            "agents_with_shrinkage": ec["agents_with_shrinkage"],
            "service_level": ec["service_level"],
            "traffic_intensity": ec["traffic_intensity"],
            "occupancy": ec["occupancy"],
        })
    return results


# ═══════════════════════════════════════════════════════════════
# WHAT-IF SCENARIOS
# ═══════════════════════════════════════════════════════════════

def apply_what_if(forecast_intervals, volume_pct=0, aht_pct=0,
                   service_level_target=None, target_asa=None,
                   shrinkage=None):
    """
    Apply what-if adjustments to forecast data and recompute Erlang C.

    volume_pct: percentage change to offered volume (e.g. +10 = 10% increase)
    aht_pct: percentage change to AHT (e.g. -5 = 5% decrease)

    Returns same shape as compute_requirements_from_forecast.
    """
    vol_mult = 1.0 + (volume_pct / 100.0)
    aht_mult = 1.0 + (aht_pct / 100.0)

    adjusted = []
    for row in forecast_intervals:
        adjusted.append({
            **row,
            "offered": round(row["offered"] * vol_mult, 2),
            "aht": round(row["aht"] * aht_mult, 1),
        })

    return compute_requirements_from_forecast(
        adjusted,
        service_level_target=service_level_target,
        target_asa=target_asa,
        shrinkage=shrinkage,
    )


# ═══════════════════════════════════════════════════════════════
# ACCURACY TRACKING
# ═══════════════════════════════════════════════════════════════

def compute_accuracy(lob, start_date, end_date, sheet=None):
    """
    Compare forecast data against requirements (actuals) for accuracy.

    Matches intervals by date + time between FORECAST RAW and REQUIREMENTS RAW.
    Returns {
        intervals: [{date, time, forecast_offered, actual_agents, variance_pct}],
        summary: {mape, wmape, bias, total_intervals, matched_intervals}
    }
    """
    forecast, f_err = get_forecast_data(lob, start_date, end_date, sheet)
    requirements, r_err = get_requirements_data(lob, start_date, end_date, sheet)

    if f_err or r_err:
        return {
            "intervals": [],
            "summary": {"mape": 0, "wmape": 0, "bias": 0,
                        "total_intervals": 0, "matched_intervals": 0},
            "error": f_err or r_err,
        }

    # Build requirements lookup
    req_map = {}
    for r in requirements:
        key = (r["date"], r["time"])
        req_map[key] = r["agents_required"]

    intervals = []
    abs_errors = []
    weighted_errors = []
    biases = []

    for fc in forecast:
        key = (fc["date"], fc["time"])
        if key not in req_map:
            continue

        actual = req_map[key]
        forecast_val = fc["offered"]

        if actual > 0:
            variance_pct = round(((forecast_val - actual) / actual) * 100, 1)
            abs_pct = abs(variance_pct)
        elif forecast_val > 0:
            variance_pct = 100.0
            abs_pct = 100.0
        else:
            variance_pct = 0.0
            abs_pct = 0.0

        intervals.append({
            "date": fc["date"],
            "time": fc["time"],
            "forecast_offered": round(forecast_val, 1),
            "forecast_aht": round(fc["aht"], 1),
            "actual_agents": round(actual, 1),
            "variance_pct": variance_pct,
        })
        abs_errors.append(abs_pct)
        weighted_errors.append(abs(forecast_val - actual))
        biases.append(forecast_val - actual)

    # Summary stats
    n = len(intervals)
    total_actual = sum(req_map.get((fc["date"], fc["time"]), 0)
                       for fc in forecast if (fc["date"], fc["time"]) in req_map)

    mape = round(sum(abs_errors) / n, 1) if n > 0 else 0
    wmape = round((sum(weighted_errors) / total_actual) * 100, 1) if total_actual > 0 else 0
    bias = round(sum(biases) / n, 2) if n > 0 else 0

    return {
        "intervals": intervals,
        "summary": {
            "mape": mape,
            "wmape": wmape,
            "bias": bias,
            "total_intervals": len(forecast),
            "matched_intervals": n,
        },
    }


# ═══════════════════════════════════════════════════════════════
# SUMMARY VIEWS
# ═══════════════════════════════════════════════════════════════

def daily_summary(intervals):
    """
    Aggregate interval-level forecast+requirements into daily summaries.
    Input: list of dicts with date, time, offered, aht, and optionally agents_required/agents_with_shrinkage.
    Returns: list of {date, total_offered, avg_aht, peak_offered, agents_required, agents_with_shrinkage, intervals}
    """
    by_date = defaultdict(list)
    for row in intervals:
        by_date[row["date"]].append(row)

    summaries = []
    for date_str in sorted(by_date.keys()):
        rows = by_date[date_str]
        total_offered = sum(r["offered"] for r in rows)
        total_weight = sum(r["offered"] for r in rows)
        avg_aht = (sum(r["offered"] * r["aht"] for r in rows) / total_weight
                   if total_weight > 0 else 0)
        peak = max(r["offered"] for r in rows) if rows else 0
        agents = max((r.get("agents_required", 0) for r in rows), default=0)
        agents_shrink = max((r.get("agents_with_shrinkage", 0) for r in rows), default=0)

        summaries.append({
            "date": date_str,
            "total_offered": round(total_offered, 1),
            "avg_aht": round(avg_aht, 1),
            "peak_offered": round(peak, 1),
            "peak_agents_required": agents,
            "peak_agents_with_shrinkage": agents_shrink,
            "intervals": len(rows),
        })
    return summaries


# ═══════════════════════════════════════════════════════════════
# GENERATE & SAVE TO DATABASE
# ═══════════════════════════════════════════════════════════════

def _get_db_historical(lob, historical_days):
    """
    Pull historical forecast intervals — tries DB first, then any other
    configured source (Google Sheets, etc.) as a fallback.
    Returns (list of {date, time, offered, aht}, error_or_None).
    """
    today = datetime.date.today()
    hist_start = today - datetime.timedelta(days=historical_days)

    # --- Try IntervalActual (real actuals) first ---
    hist_end = today - datetime.timedelta(days=1)
    actuals, _ = get_historical_actuals(lob, hist_start, hist_end)
    if actuals:
        log.info(f"Found {len(actuals)} actual intervals in DB for '{lob}'")
        return actuals, None

    # --- Fall back to ForecastInterval ---
    try:
        from app.models import ForecastInterval, PlanningUnit
        from sqlalchemy import func as sa_func

        from app.data_source import normalize_lob
        unit = PlanningUnit.query.filter_by(name=normalize_lob(str(lob).strip())).first()
        if unit:
            rows = ForecastInterval.query.filter(
                ForecastInterval.planning_unit_id == unit.id,
                sa_func.date(ForecastInterval.timestamp) >= hist_start,
                sa_func.date(ForecastInterval.timestamp) < today,
            ).order_by(ForecastInterval.timestamp).all()

            if rows:
                results = []
                for r in rows:
                    results.append({
                        "date": r.timestamp.strftime("%Y-%m-%d"),
                        "time": r.timestamp.strftime("%H:%M"),
                        "offered": float(r.offered or 0),
                        "aht": float(r.aht or 0),
                    })
                log.info(f"Found {len(results)} historical forecast intervals in DB for '{lob}'")
                return results, None
    except Exception as e:
        log.info(f"DB historical lookup failed for '{lob}': {e}")

    # --- Fallback: try Google Sheets or other configured source ---
    try:
        historical, err = get_forecast_data(lob, hist_start,
                                             today - datetime.timedelta(days=1))
        if not err and historical:
            log.info(f"Found {len(historical)} historical intervals from "
                     f"sheets/source for '{lob}'")
            return historical, None
        if err:
            log.info(f"Sheets/source fallback also failed for '{lob}': {err}")
    except Exception as e:
        log.info(f"Sheets/source fallback error for '{lob}': {e}")

    return [], f"No historical forecast data for '{lob}' in any source"


def _generate_from_db_history(lob, method, historical_days, forecast_days,
                               window=7):
    """
    Generate forecast intervals from DB historical data.
    Returns same shape as generate_forecast_weighted / generate_forecast_moving_avg.
    """
    historical, err = _get_db_historical(lob, historical_days)
    if err or not historical:
        return {
            "method": method,
            "historical": [],
            "forecast": [],
            "error": err or "No historical data found",
        }

    # Group by day-of-week + time
    by_dow_time = defaultdict(list)
    for row in historical:
        try:
            d = datetime.datetime.strptime(row["date"], "%Y-%m-%d").date()
            dow = d.weekday()
            by_dow_time[(dow, row["time"])].append((row, d))
        except Exception:
            continue

    today = datetime.date.today()

    # Look up LOB operating hours
    lob_setting = _get_lob_setting(lob)

    if method == "holt_winters":
        # Use the standalone Holt-Winters generator (it handles its own data)
        hw_result = generate_forecast_holt_winters(lob, historical_days, forecast_days)
        if hw_result.get("error"):
            return hw_result
        forecast = hw_result.get("forecast", [])
        forecast = apply_holiday_adjustments(forecast, historical)
        return {
            "method": "holt_winters",
            "historical_days_used": historical_days,
            "historical": historical,
            "forecast": forecast,
        }

    if method == "moving_average":
        # Simple moving average over the last `window` entries per timeslot
        forecast = []
        for d_offset in range(forecast_days):
            fc_date = today + datetime.timedelta(days=d_offset)
            date_str = fc_date.strftime("%Y-%m-%d")
            dow = fc_date.weekday()

            time_slots = set(ts for (_, ts) in by_dow_time.keys())
            day_rows = []
            for time_str in sorted(time_slots):
                entries = by_dow_time.get((dow, time_str), [])
                if not entries:
                    for dw in range(7):
                        entries = by_dow_time.get((dw, time_str), [])
                        if entries:
                            break
                if not entries:
                    continue
                recent = sorted(entries, key=lambda x: x[1], reverse=True)[:window]
                avg_offered = sum(r[0]["offered"] for r in recent) / len(recent)
                total_w = sum(r[0]["offered"] for r in recent)
                avg_aht = (sum(r[0]["offered"] * r[0]["aht"] for r in recent) / total_w
                           if total_w > 0 else 0)
                day_rows.append({
                    "date": date_str,
                    "time": time_str,
                    "offered": round(avg_offered, 2),
                    "aht": round(avg_aht, 1),
                })
            # Fill all operating-hour intervals
            day_rows = _fill_operating_hours(day_rows, lob_setting, fc_date)
            forecast.extend(day_rows)
    else:
        # Weighted trend (default) — same logic as generate_forecast_weighted
        def calc_weight(row_date, ref_date):
            delta = (ref_date - row_date).days
            return 1.0 / (1.0 + 0.1 * max(delta, 0))

        forecast = []
        for d_offset in range(forecast_days):
            fc_date = today + datetime.timedelta(days=d_offset)
            date_str = fc_date.strftime("%Y-%m-%d")
            dow = fc_date.weekday()

            time_slots = set(ts for (_, ts) in by_dow_time.keys())
            day_rows = []
            for time_str in sorted(time_slots):
                entries = by_dow_time.get((dow, time_str), [])
                if not entries:
                    for dw in range(7):
                        entries = by_dow_time.get((dw, time_str), [])
                        if entries:
                            break
                if not entries:
                    continue
                total_w = 0
                w_offered = 0
                w_aht_num = 0
                for row, row_date in entries:
                    w = calc_weight(row_date, today)
                    total_w += w
                    w_offered += w * row["offered"]
                    w_aht_num += w * row["offered"] * row["aht"]
                if total_w > 0:
                    avg_offered = w_offered / total_w
                    avg_aht = w_aht_num / w_offered if w_offered > 0 else 0
                else:
                    avg_offered = 0
                    avg_aht = 0
                day_rows.append({
                    "date": date_str,
                    "time": time_str,
                    "offered": round(avg_offered, 2),
                    "aht": round(avg_aht, 1),
                })
            # Fill all operating-hour intervals
            day_rows = _fill_operating_hours(day_rows, lob_setting, fc_date)
            forecast.extend(day_rows)

    return {
        "method": method,
        "historical_days_used": historical_days,
        "historical": historical,
        "forecast": forecast,
    }


def generate_and_save_forecast(lob, method="weighted", historical_days=90,
                                forecast_days=90, window=7):
    """
    Generate a forecast from DB historical data, run Erlang C to compute
    staffing requirements, and save both to the database.

    Returns dict with ok, message, forecast_count, requirements_count.
    """
    from app.models import db, ForecastInterval, RequirementInterval, PlanningUnit
    from app.data_source import normalize_lob

    # Normalize LOB name to planning unit name
    lob_normalized = normalize_lob(str(lob).strip())

    # Find or create the planning unit
    unit = PlanningUnit.query.filter_by(name=lob_normalized).first()
    if not unit:
        unit = PlanningUnit(name=lob_normalized)
        db.session.add(unit)
        db.session.flush()

    # Auto-select best method if requested
    if method == "auto":
        auto_result = auto_select_method(lob, historical_days)
        method = auto_result["best_method"]
        log.info(f"Auto-selected method '{method}' for '{lob}': {auto_result['results']}")

    # Generate forecast from DB history
    result = _generate_from_db_history(lob, method, historical_days,
                                        forecast_days, window)
    if result.get("error"):
        return {"ok": False, "error": result["error"]}

    forecast = result.get("forecast", [])
    if not forecast:
        return {"ok": False, "error": f"No forecast generated for '{lob}'"}

    # Use LOB-specific Erlang C parameters if available
    lob_setting = unit.lob_setting if unit else None
    sl_target = lob_setting.service_level_target if lob_setting else None
    asa_target = lob_setting.target_asa if lob_setting else None
    shrinkage_val = lob_setting.shrinkage_pct if lob_setting else None

    # Run Erlang C to compute requirements
    requirements = compute_requirements_from_forecast(
        forecast,
        service_level_target=sl_target,
        target_asa=asa_target,
        shrinkage=shrinkage_val,
    )

    # Delete existing future forecast + requirement intervals for this unit
    today = datetime.date.today()
    from sqlalchemy import func as sa_func

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
    fc_count = 0
    for row in forecast:
        try:
            ts = datetime.datetime.strptime(f"{row['date']} {row['time']}",
                                             "%Y-%m-%d %H:%M")
        except (ValueError, KeyError):
            continue
        fi = ForecastInterval(
            planning_unit_id=unit.id,
            timestamp=ts,
            offered=row["offered"],
            aht=row["aht"],
            source="generated",
        )
        db.session.add(fi)
        fc_count += 1

    # Save requirement intervals
    req_count = 0
    for row in requirements:
        try:
            ts = datetime.datetime.strptime(f"{row['date']} {row['time']}",
                                             "%Y-%m-%d %H:%M")
        except (ValueError, KeyError):
            continue
        ri = RequirementInterval(
            planning_unit_id=unit.id,
            timestamp=ts,
            agents_required=row["agents_required"],
            source="generated",
        )
        db.session.add(ri)
        req_count += 1

    db.session.commit()

    return {
        "ok": True,
        "lob": lob,
        "message": f"Generated {fc_count} forecast and {req_count} requirement intervals for '{lob}'",
        "forecast_count": fc_count,
        "requirements_count": req_count,
    }


def generate_all_forecasts(method="weighted", historical_days=90,
                            forecast_days=90, window=7):
    """
    Generate and save forecasts for ALL active planning units.
    Returns list of results (one per LOB).
    """
    from app.models import PlanningUnit

    units = PlanningUnit.query.filter_by(is_active=True).all()
    results = []
    for unit in units:
        result = generate_and_save_forecast(
            unit.name, method, historical_days, forecast_days, window
        )
        results.append(result)
    return results
