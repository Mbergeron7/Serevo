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
    try:
        from app.data_source import SheetSource
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
                    lobs.add(lob)
            return sorted(lobs)
        lobs = set()
        for r in rows:
            lob = str(r.get("LOB", "")).strip()
            if lob:
                lobs.add(lob)
        return sorted(lobs)
    except Exception:
        return []


def get_forecast_data(lob, start_date, end_date, sheet=None):
    """
    Retrieve stored forecast data for a LOB across a date range.
    Returns list of {date, time, offered, aht} sorted by timestamp.
    """
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
            if str(r.get("LOB", "")).strip().lower() != lob.strip().lower():
                continue
            ts = str(r.get("Timestamp", ""))
            date_part = ts[:10]
            if date_part < start_str or date_part > end_str:
                continue
            time_part = ts[11:16] if len(ts) > 10 else ts
            results.append({
                "date": date_part,
                "time": time_part,
                "offered": float(r.get("offered", 0) or 0),
                "aht": float(r.get("aht", 0) or 0),
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
            if str(r.get("LOB", "")).strip().lower() != lob.strip().lower():
                continue
            ts = str(r.get("Timestamp", ""))
            date_part = ts[:10]
            if date_part < start_str or date_part > end_str:
                continue
            time_part = ts[11:16] if len(ts) > 10 else ts
            results.append({
                "date": date_part,
                "time": time_part,
                "agents_required": float(r.get("agents_required", 0) or 0),
            })

        results.sort(key=lambda x: (x["date"], x["time"]))
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
            if str(r.get("LOB", "")).strip().lower() != lob.strip().lower():
                continue
            ts = str(r.get("Timestamp", ""))
            if len(ts) >= 10:
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

    # Project forward
    forecast = []
    for d in range(1, forecast_days + 1):
        fc_date = today + datetime.timedelta(days=d - 1)
        date_str = fc_date.strftime("%Y-%m-%d")
        for time_str in sorted(avg_profile.keys()):
            prof = avg_profile[time_str]
            forecast.append({
                "date": date_str,
                "time": time_str,
                "offered": prof["offered"],
                "aht": prof["aht"],
            })

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

            forecast.append({
                "date": date_str,
                "time": time_str,
                "offered": round(avg_offered, 2),
                "aht": round(avg_aht, 1),
            })

    return {
        "method": "weighted_trend",
        "historical_days_used": historical_days,
        "historical": historical,
        "forecast": forecast,
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
