"""
ml_engine.py — Machine-learning forecast engine
================================================
Uses scikit-learn GradientBoostingRegressor trained on IntervalActual data
(real ACD call stats) rather than past forecasts.

Features per interval:
  - slot_index     : integer position of the interval within the operating day
  - day_of_week    : 0=Mon … 6=Sun
  - is_weekend     : 1 if Sat/Sun
  - week_of_year   : ISO week number
  - month          : 1–12
  - lag_1d         : offered volume same slot yesterday
  - lag_7d         : offered volume same slot 7 days ago
  - rolling_7d_mean: rolling 7-day mean for that slot
  - rolling_7d_std : rolling 7-day std for that slot

Outputs:
  - offered  (predicted call volume per interval)
  - aht      (predicted average handle time per interval)

Prediction intervals are provided via residual-based ± 1.96 * std.
"""

import datetime
import logging
from collections import defaultdict

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor

log = logging.getLogger("serevo.ml_forecast")


# ═══════════════════════════════════════════════════════════════
# DATA LOADING — reads IntervalActual (real data, NOT forecasts)
# ═══════════════════════════════════════════════════════════════

def _load_actuals(lob, historical_days):
    """
    Load IntervalActual rows from Postgres for the given LOB.
    Returns a pandas DataFrame with columns:
        timestamp, offered, aht_secs, date, time, day_of_week, slot_index
    or an empty DataFrame if no data.
    """
    today = datetime.date.today()
    hist_start = today - datetime.timedelta(days=historical_days)

    try:
        from app.models import IntervalActual, PlanningUnit
        from app.data_source import normalize_lob
        from sqlalchemy import func as sa_func

        unit = PlanningUnit.query.filter_by(
            name=normalize_lob(str(lob).strip())
        ).first()
        if not unit:
            log.info(f"ML: No planning unit found for '{lob}'")
            return pd.DataFrame()

        rows = IntervalActual.query.filter(
            IntervalActual.planning_unit_id == unit.id,
            sa_func.date(IntervalActual.timestamp) >= hist_start,
            sa_func.date(IntervalActual.timestamp) < today,
        ).order_by(IntervalActual.timestamp).all()

        if not rows:
            log.info(f"ML: No actuals found for '{lob}' in last {historical_days} days")
            return pd.DataFrame()

        records = []
        for r in rows:
            records.append({
                "timestamp": r.timestamp,
                "offered": float(r.offered or 0),
                "aht_secs": float(r.aht_secs or 0),
            })

        df = pd.DataFrame(records)
        df["date"] = df["timestamp"].dt.strftime("%Y-%m-%d")
        df["time"] = df["timestamp"].dt.strftime("%H:%M")
        df["day_of_week"] = df["timestamp"].dt.dayofweek
        df["is_weekend"] = (df["day_of_week"] >= 5).astype(int)
        df["week_of_year"] = df["timestamp"].dt.isocalendar().week.astype(int)
        df["month"] = df["timestamp"].dt.month
        df["hour"] = df["timestamp"].dt.hour
        df["minute"] = df["timestamp"].dt.minute
        df["slot_index"] = df["hour"] * 4 + df["minute"] // 15  # 15-min slots

        log.info(f"ML: Loaded {len(df)} actual intervals for '{lob}'")
        return df

    except Exception as e:
        log.warning(f"ML: Failed to load actuals for '{lob}': {e}")
        return pd.DataFrame()


# ═══════════════════════════════════════════════════════════════
# FEATURE ENGINEERING
# ═══════════════════════════════════════════════════════════════

def _build_features(df):
    """
    Add lag and rolling features per time slot.
    Expects df sorted by timestamp with columns: offered, day_of_week,
    is_weekend, week_of_year, month, slot_index.
    """
    df = df.sort_values("timestamp").copy()

    # Build lag features per slot
    df["lag_1d"] = np.nan
    df["lag_7d"] = np.nan
    df["rolling_7d_mean"] = np.nan
    df["rolling_7d_std"] = np.nan

    for slot in df["slot_index"].unique():
        mask = df["slot_index"] == slot
        slot_series = df.loc[mask, "offered"]

        # Each row in this slot corresponds to roughly one day
        df.loc[mask, "lag_1d"] = slot_series.shift(1)
        df.loc[mask, "lag_7d"] = slot_series.shift(7)
        df.loc[mask, "rolling_7d_mean"] = slot_series.shift(1).rolling(7, min_periods=3).mean()
        df.loc[mask, "rolling_7d_std"] = slot_series.shift(1).rolling(7, min_periods=3).std()

    df["rolling_7d_std"] = df["rolling_7d_std"].fillna(0)
    return df


FEATURE_COLS = [
    "slot_index", "day_of_week", "is_weekend",
    "week_of_year", "month",
    "lag_1d", "lag_7d", "rolling_7d_mean", "rolling_7d_std",
]


# ═══════════════════════════════════════════════════════════════
# TRAIN + FORECAST
# ═══════════════════════════════════════════════════════════════

def generate_forecast_ml(lob, historical_days=90, forecast_days=7,
                          sheet=None):
    """
    Generate a forecast using GradientBoostingRegressor trained on
    IntervalActual data.

    Returns {
        method: "ml_gradient_boosting",
        historical_days_used: int,
        training_rows: int,
        forecast: [{date, time, offered, aht, offered_lower, offered_upper}],
        error: str | None,
    }
    """
    df = _load_actuals(lob, historical_days)
    if df.empty:
        return {
            "method": "ml_gradient_boosting",
            "historical": [],
            "forecast": [],
            "error": f"No actual interval data for '{lob}'. "
                     "ML forecasting requires imported actuals (not forecasts).",
        }

    df = _build_features(df)

    # Drop rows where lag features are NaN (first ~7 days)
    train_df = df.dropna(subset=FEATURE_COLS)
    if len(train_df) < 50:
        return {
            "method": "ml_gradient_boosting",
            "historical": [],
            "forecast": [],
            "error": f"Not enough training data ({len(train_df)} rows). "
                     "Need at least 50 interval-actual rows with lag features.",
        }

    X_train = train_df[FEATURE_COLS].values
    y_offered = train_df["offered"].values
    y_aht = train_df["aht_secs"].values

    # ---- Train offered model ----
    model_offered = GradientBoostingRegressor(
        n_estimators=200,
        max_depth=4,
        learning_rate=0.1,
        subsample=0.8,
        random_state=42,
    )
    model_offered.fit(X_train, y_offered)

    # ---- Train AHT model ----
    model_aht = GradientBoostingRegressor(
        n_estimators=100,
        max_depth=3,
        learning_rate=0.1,
        subsample=0.8,
        random_state=42,
    )
    model_aht.fit(X_train, y_aht)

    # Compute residual std for prediction intervals
    train_pred = model_offered.predict(X_train)
    residual_std = float(np.std(y_offered - train_pred))

    # ---- Build future feature rows ----
    today = datetime.date.today()

    # Get the most recent actual values per slot for lag features
    latest_by_slot = {}
    for _, row in df.sort_values("timestamp").iterrows():
        slot = row["slot_index"]
        if slot not in latest_by_slot:
            latest_by_slot[slot] = []
        latest_by_slot[slot].append(row["offered"])

    # Get operating slots from the last day of data
    last_day = df.sort_values("timestamp").iloc[-1]["date"]
    last_day_slots = sorted(df[df["date"] == last_day]["time"].unique())
    if not last_day_slots:
        last_day_slots = sorted(df["time"].unique())

    # Look up LOB operating hours for slot coverage
    from app.forecasting.engine import _get_lob_setting, _operating_slots
    lob_setting = _get_lob_setting(lob)

    forecast = []
    for d_offset in range(forecast_days):
        fc_date = today + datetime.timedelta(days=d_offset)
        date_str = fc_date.strftime("%Y-%m-%d")
        dow = fc_date.weekday()
        is_wknd = 1 if dow >= 5 else 0
        woy = fc_date.isocalendar()[1]
        mon = fc_date.month

        # Determine slots for this day
        operating = _operating_slots(lob_setting, fc_date) or last_day_slots

        for time_str in operating:
            parts = time_str.split(":")
            h, m = int(parts[0]), int(parts[1])
            slot_idx = h * 4 + m // 15

            # Get lag values from historical data
            slot_hist = latest_by_slot.get(slot_idx, [])
            lag_1d = slot_hist[-1] if slot_hist else 0
            lag_7d = slot_hist[-7] if len(slot_hist) >= 7 else (slot_hist[0] if slot_hist else 0)
            recent_7 = slot_hist[-7:] if len(slot_hist) >= 7 else slot_hist
            roll_mean = float(np.mean(recent_7)) if recent_7 else 0
            roll_std = float(np.std(recent_7)) if len(recent_7) > 1 else 0

            features = np.array([[
                slot_idx, dow, is_wknd, woy, mon,
                lag_1d, lag_7d, roll_mean, roll_std,
            ]])

            pred_offered = max(0, float(model_offered.predict(features)[0]))
            pred_aht = max(0, float(model_aht.predict(features)[0]))

            # Prediction interval (95%)
            lower = max(0, pred_offered - 1.96 * residual_std)
            upper = pred_offered + 1.96 * residual_std

            forecast.append({
                "date": date_str,
                "time": time_str,
                "offered": round(pred_offered, 2),
                "aht": round(pred_aht, 1),
                "offered_lower": round(lower, 2),
                "offered_upper": round(upper, 2),
            })

    # Also return historical in the standard format for the UI
    hist_records = []
    for _, row in df.iterrows():
        hist_records.append({
            "date": row["date"],
            "time": row["time"],
            "offered": float(row["offered"]),
            "aht": float(row["aht_secs"]),
        })

    return {
        "method": "ml_gradient_boosting",
        "historical_days_used": df["date"].nunique(),
        "training_rows": len(train_df),
        "residual_std": round(residual_std, 2),
        "historical": hist_records,
        "forecast": forecast,
    }


# ═══════════════════════════════════════════════════════════════
# ACCURACY — compares forecasts vs IntervalActual
# ═══════════════════════════════════════════════════════════════

def compute_accuracy_vs_actuals(lob, start_date, end_date):
    """
    Compare stored ForecastInterval data against IntervalActual data
    for the given date range. This is the correct accuracy measure:
    forecast vs. what actually happened.

    Returns {
        intervals: [{date, time, forecast_offered, actual_offered, variance_pct}],
        summary: {mape, wmape, bias, total_intervals, matched_intervals},
    }
    """
    try:
        from app.models import ForecastInterval, IntervalActual, PlanningUnit
        from app.data_source import normalize_lob
        from sqlalchemy import func as sa_func

        unit = PlanningUnit.query.filter_by(
            name=normalize_lob(str(lob).strip())
        ).first()
        if not unit:
            return {
                "intervals": [],
                "summary": {"mape": 0, "wmape": 0, "bias": 0,
                            "total_intervals": 0, "matched_intervals": 0},
                "error": f"No planning unit for '{lob}'",
            }

        start_str = start_date.strftime("%Y-%m-%d")
        end_str = end_date.strftime("%Y-%m-%d")

        # Load forecasts
        fc_rows = ForecastInterval.query.filter(
            ForecastInterval.planning_unit_id == unit.id,
            sa_func.date(ForecastInterval.timestamp) >= start_str,
            sa_func.date(ForecastInterval.timestamp) <= end_str,
        ).all()

        # Load actuals
        actual_rows = IntervalActual.query.filter(
            IntervalActual.planning_unit_id == unit.id,
            sa_func.date(IntervalActual.timestamp) >= start_str,
            sa_func.date(IntervalActual.timestamp) <= end_str,
        ).all()

        # Build actuals lookup
        actual_map = {}
        for r in actual_rows:
            key = r.timestamp.strftime("%Y-%m-%d %H:%M")
            actual_map[key] = {
                "offered": float(r.offered or 0),
                "aht": float(r.aht_secs or 0),
            }

        intervals = []
        abs_errors = []
        weighted_errors = []
        biases = []

        for fc in fc_rows:
            key = fc.timestamp.strftime("%Y-%m-%d %H:%M")
            if key not in actual_map:
                continue

            actual = actual_map[key]
            fc_val = float(fc.offered or 0)
            act_val = actual["offered"]

            if act_val > 0:
                variance_pct = round(((fc_val - act_val) / act_val) * 100, 1)
            elif fc_val > 0:
                variance_pct = 100.0
            else:
                variance_pct = 0.0

            intervals.append({
                "date": fc.timestamp.strftime("%Y-%m-%d"),
                "time": fc.timestamp.strftime("%H:%M"),
                "forecast_offered": round(fc_val, 1),
                "forecast_aht": round(float(fc.aht or 0), 1),
                "actual_offered": round(act_val, 1),
                "actual_aht": round(actual["aht"], 1),
                "variance_pct": variance_pct,
            })
            abs_errors.append(abs(variance_pct))
            weighted_errors.append(abs(fc_val - act_val))
            biases.append(fc_val - act_val)

        n = len(intervals)
        total_actual = sum(actual_map[fc.timestamp.strftime("%Y-%m-%d %H:%M")]["offered"]
                          for fc in fc_rows
                          if fc.timestamp.strftime("%Y-%m-%d %H:%M") in actual_map)

        mape = round(sum(abs_errors) / n, 1) if n > 0 else 0
        wmape = round((sum(weighted_errors) / total_actual) * 100, 1) if total_actual > 0 else 0
        bias = round(sum(biases) / n, 2) if n > 0 else 0

        return {
            "intervals": intervals,
            "summary": {
                "mape": mape,
                "wmape": wmape,
                "bias": bias,
                "total_intervals": len(fc_rows),
                "matched_intervals": n,
            },
        }

    except Exception as e:
        log.warning(f"ML accuracy computation failed: {e}")
        return {
            "intervals": [],
            "summary": {"mape": 0, "wmape": 0, "bias": 0,
                        "total_intervals": 0, "matched_intervals": 0},
            "error": str(e),
        }
