"""
routes/realtime.py — Real-Time Monitoring blueprint
====================================================
Live queue monitoring, adherence, and staffing alerts.
"""

import logging
import datetime

from flask import (Blueprint, render_template, request, jsonify)
from app.auth import login_required, get_current_user

log = logging.getLogger("serevo.realtime")

realtime_bp = Blueprint("realtime", __name__, url_prefix="/realtime")


def _all_lobs(user):
    """Return list of LOB names for the current user."""
    if user and user.get("is_demo"):
        from app.demo_data import DEMO_LOBS
        return list(DEMO_LOBS)
    from app.realtime.engine import get_available_lobs
    sheet = _get_sheet()
    return get_available_lobs(sheet)


from app.routes._utils import get_sheet as _get_sheet


# ── "All LOBs" aggregation helpers ─────────────────────────

def _aggregate_snapshots(user, date_obj):
    """Merge intraday snapshots across all LOBs."""
    lobs = _all_lobs(user)
    is_demo = user and user.get("is_demo")
    sheet = None if is_demo else _get_sheet()

    combined_intervals = {}  # time -> {required, scheduled, offered, aht_sum, aht_cnt}
    all_shifts = []
    all_unassigned = []
    all_alerts = []
    current_interval = None
    current_time = None

    for lob_name in lobs:
        if is_demo:
            from app.demo_data import get_demo_realtime_snapshot
            snap = get_demo_realtime_snapshot(lob_name, date_obj)
        else:
            from app.realtime.engine import get_intraday_snapshot
            snap = get_intraday_snapshot(lob_name, date_obj, sheet)

        current_interval = snap.get("current_interval", current_interval)
        current_time = snap.get("current_time", current_time)

        for iv in snap.get("intervals", []):
            t = iv["time"]
            if t not in combined_intervals:
                combined_intervals[t] = {"required": 0, "scheduled": 0,
                                         "offered": 0.0, "aht_sum": 0.0, "aht_cnt": 0}
            c = combined_intervals[t]
            c["required"] += iv.get("required", 0)
            c["scheduled"] += iv.get("scheduled", 0)
            c["offered"] += iv.get("forecast_offered", 0)
            if iv.get("forecast_aht", 0) > 0:
                c["aht_sum"] += iv["forecast_aht"]
                c["aht_cnt"] += 1

        for s in snap.get("shifts", []):
            s_copy = dict(s)
            s_copy["employee"] = f"{s['employee']} ({lob_name})"
            all_shifts.append(s_copy)

        all_unassigned.extend(snap.get("unassigned", []))

        for a in snap.get("alerts", []):
            a_copy = dict(a)
            a_copy["message"] = f"[{lob_name}] {a['message']}"
            all_alerts.append(a_copy)

    # Build merged intervals list
    intervals = []
    for t in sorted(combined_intervals.keys()):
        c = combined_intervals[t]
        req = c["required"]
        sched = c["scheduled"]
        gap = sched - req
        pct = round((sched / req) * 100, 1) if req > 0 else (100.0 if sched > 0 else 0)
        avg_aht = round(c["aht_sum"] / c["aht_cnt"], 1) if c["aht_cnt"] > 0 else 0
        status = "critical" if pct < 70 and req > 0 else ("warning" if pct < 90 and req > 0 else ("over" if sched > req and req > 0 else "ok"))
        intervals.append({
            "time": t, "required": req, "scheduled": sched,
            "gap": round(gap, 1), "coverage_pct": pct,
            "forecast_offered": round(c["offered"], 1),
            "forecast_aht": avg_aht, "status": status,
        })

    # Current
    current = next((iv for iv in intervals if iv["time"] == current_interval), None)
    if not current:
        current = next((iv for iv in reversed(intervals) if iv["time"] <= (current_interval or "")), None)
    if not current:
        current = {"time": current_interval or "", "required": 0, "scheduled": 0,
                   "gap": 0, "coverage_pct": 0, "status": "ok",
                   "forecast_offered": 0, "forecast_aht": 0}

    understaffed = sum(1 for iv in intervals if iv["gap"] < 0)
    overstaffed = sum(1 for iv in intervals if iv["gap"] > 0 and iv["required"] > 0)
    avg_cov = sum(iv["coverage_pct"] for iv in intervals) / max(1, len(intervals))
    sev_order = {"critical": 0, "warning": 1, "info": 2}
    all_alerts.sort(key=lambda a: (sev_order.get(a["severity"], 9), a.get("time", "")))

    return {
        "lob": "All LOBs",
        "date": date_obj.isoformat(),
        "current_interval": current_interval or "",
        "current_time": current_time or "",
        "intervals": intervals,
        "current": current,
        "shifts": all_shifts,
        "unassigned": all_unassigned,
        "summary": {
            "total_intervals": len(intervals),
            "understaffed_count": understaffed,
            "overstaffed_count": overstaffed,
            "avg_coverage_pct": round(avg_cov, 1),
            "peak_required": max((iv["required"] for iv in intervals), default=0),
            "peak_gap": round(min((iv["gap"] for iv in intervals), default=0), 1),
            "total_scheduled": len(all_shifts),
            "total_unassigned": len(all_unassigned),
        },
        "alerts": all_alerts,
    }


def _aggregate_adherence(user, date_obj):
    """Merge adherence across all LOBs."""
    lobs = _all_lobs(user)
    is_demo = user and user.get("is_demo")
    sheet = None if is_demo else _get_sheet()

    all_adherence = []
    all_unassigned = []
    current_interval = None

    for lob_name in lobs:
        if is_demo:
            from app.demo_data import get_demo_adherence
            result = get_demo_adherence(lob_name, date_obj)
        else:
            from app.realtime.engine import get_adherence_snapshot
            result = get_adherence_snapshot(lob_name, date_obj, sheet)

        current_interval = result.get("current_interval", current_interval)
        for a in result.get("adherence", []):
            a_copy = dict(a)
            a_copy["employee"] = f"{a['employee']} ({lob_name})"
            all_adherence.append(a_copy)
        all_unassigned.extend(result.get("unassigned", []))

    all_adherence.sort(key=lambda a: (0 if a.get("is_on_shift") else 1, a["employee"]))

    return {
        "adherence": all_adherence,
        "unassigned": all_unassigned,
        "current_interval": current_interval or "",
        "on_shift_count": sum(1 for a in all_adherence if a.get("is_on_shift")),
        "total_scheduled": len(all_adherence),
    }


def _aggregate_service_level(user, date_obj):
    """Merge service-level data across all LOBs (weighted average SL)."""
    lobs = _all_lobs(user)
    is_demo = user and user.get("is_demo")
    sheet = None if is_demo else _get_sheet()

    combined = {}  # time -> {sl_weighted_sum, offered_sum, agents, traffic, aht_sum, aht_cnt}

    for lob_name in lobs:
        if is_demo:
            from app.demo_data import get_demo_service_level
            intervals = get_demo_service_level(lob_name, date_obj)
        else:
            from app.realtime.engine import get_service_level_intraday
            intervals = get_service_level_intraday(lob_name, date_obj, sheet)

        for iv in intervals:
            t = iv["time"]
            if t not in combined:
                combined[t] = {"sl_w": 0, "offered": 0, "agents": 0,
                               "traffic": 0, "aht_sum": 0, "aht_cnt": 0}
            c = combined[t]
            offered = iv.get("offered", 0)
            c["sl_w"] += iv.get("estimated_sl", 0) * offered
            c["offered"] += offered
            c["agents"] += iv.get("agents", 0)
            c["traffic"] += iv.get("traffic_intensity", 0)
            if iv.get("aht", 0) > 0:
                c["aht_sum"] += iv["aht"]
                c["aht_cnt"] += 1

    result = []
    for t in sorted(combined.keys()):
        c = combined[t]
        avg_sl = round(c["sl_w"] / c["offered"], 4) if c["offered"] > 0 else 0
        avg_aht = round(c["aht_sum"] / c["aht_cnt"], 1) if c["aht_cnt"] > 0 else 0
        result.append({
            "time": t, "estimated_sl": avg_sl, "agents": c["agents"],
            "traffic_intensity": round(c["traffic"], 2),
            "offered": round(c["offered"], 1), "aht": avg_aht,
        })
    return result


# ── Main view ───────────────────────────────────────────────
@realtime_bp.route("/")
@login_required
def index():
    user = get_current_user()
    if user and user.get("is_demo"):
        from app.demo_data import DEMO_LOBS
        lobs = list(DEMO_LOBS)
    else:
        from app.realtime.engine import get_available_lobs
        sheet = _get_sheet()
        lobs = get_available_lobs(sheet)
    return render_template("realtime/index.html", user=user, lobs=lobs)


# ── Intraday snapshot (API) ────────────────────────────────
@realtime_bp.route("/snapshot", methods=["POST"])
@login_required
def snapshot():
    """
    POST JSON: {lob, date?}
    Returns full intraday snapshot with intervals, alerts, summary.
    """
    from app.realtime.engine import get_intraday_snapshot

    try:
        payload = request.get_json(silent=True) or {}
        lob = payload.get("lob", "").strip()
        date_str = payload.get("date", "")

        if not lob:
            return jsonify({"success": False, "error": "LOB is required"})

        date_obj = (datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
                    if date_str else datetime.date.today())

        user = get_current_user()

        if lob == "All":
            result = _aggregate_snapshots(user, date_obj)
        elif user and user.get("is_demo"):
            from app.demo_data import get_demo_realtime_snapshot
            result = get_demo_realtime_snapshot(lob, date_obj)
        else:
            sheet = _get_sheet()
            result = get_intraday_snapshot(lob, date_obj, sheet)
        result["success"] = True
        return jsonify(result)

    except Exception as e:
        log.error(f"Snapshot error: {e}")
        return jsonify({"success": False, "error": str(e)})


# ── Adherence (API) ────────────────────────────────────────
@realtime_bp.route("/adherence", methods=["POST"])
@login_required
def adherence():
    """
    POST JSON: {lob, date?}
    Returns per-agent adherence data.
    """
    from app.realtime.engine import get_adherence_snapshot

    try:
        payload = request.get_json(silent=True) or {}
        lob = payload.get("lob", "").strip()
        date_str = payload.get("date", "")

        if not lob:
            return jsonify({"success": False, "error": "LOB is required"})

        date_obj = (datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
                    if date_str else datetime.date.today())

        user = get_current_user()

        if lob == "All":
            result = _aggregate_adherence(user, date_obj)
        elif user and user.get("is_demo"):
            from app.demo_data import get_demo_adherence
            result = get_demo_adherence(lob, date_obj)
        else:
            sheet = _get_sheet()
            result = get_adherence_snapshot(lob, date_obj, sheet)
        result["success"] = True
        return jsonify(result)

    except Exception as e:
        log.error(f"Adherence error: {e}")
        return jsonify({"success": False, "error": str(e)})


# ── Service level tracker (API) ────────────────────────────
@realtime_bp.route("/service-level", methods=["POST"])
@login_required
def service_level():
    """
    POST JSON: {lob, date?}
    Returns estimated SL per interval.
    """
    from app.realtime.engine import get_service_level_intraday

    try:
        payload = request.get_json(silent=True) or {}
        lob = payload.get("lob", "").strip()
        date_str = payload.get("date", "")

        if not lob:
            return jsonify({"success": False, "error": "LOB is required"})

        date_obj = (datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
                    if date_str else datetime.date.today())

        user = get_current_user()

        if lob == "All":
            result = _aggregate_service_level(user, date_obj)
        elif user and user.get("is_demo"):
            from app.demo_data import get_demo_service_level
            result = get_demo_service_level(lob, date_obj)
        else:
            sheet = _get_sheet()
            result = get_service_level_intraday(lob, date_obj, sheet)
        return jsonify({"success": True, "intervals": result})

    except Exception as e:
        log.error(f"SL tracker error: {e}")
        return jsonify({"success": False, "error": str(e)})


# ═════════════════════════════════════════════════════════════
# NUMERIC RTM REPORTS (Forecast/OTF, Interval, SVL, Absenteeism,
# Agent Status, Efficiency, Combined Dashboard)
# ═════════════════════════════════════════════════════════════

@realtime_bp.route("/reports")
@login_required
def reports():
    user = get_current_user()
    lobs = _all_lobs(user)
    return render_template("realtime/reports.html", user=user, lobs=lobs,
                           today=datetime.date.today().isoformat())


def _report_args():
    """Parse {lob | lobs, date} from JSON body. lob='All' → every LOB."""
    payload = request.get_json(silent=True) or {}
    user = get_current_user()
    all_lobs = _all_lobs(user)
    sel = payload.get("lobs") or payload.get("lob") or "All"
    if isinstance(sel, str):
        sel = [sel]
    sel = [s.strip() for s in sel if s and s.strip()]
    if not sel or "All" in sel:
        lobs = all_lobs
    else:
        lobs = [l for l in all_lobs if l in sel] or sel
    date_str = payload.get("date", "")
    date_obj = (datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
                if date_str else datetime.date.today())
    return user, lobs, date_obj


def _run_report(fn):
    try:
        user, lobs, date_obj = _report_args()
        sheet = None if (user and user.get("is_demo")) else _get_sheet()
        result = fn(lobs, date_obj, user, sheet)
        result["success"] = True
        return jsonify(result)
    except Exception as e:
        log.exception("RTM report error")
        return jsonify({"success": False, "error": str(e)})


@realtime_bp.route("/reports/forecast", methods=["POST"])
@login_required
def report_forecast():
    from app.realtime.reports import forecast_otf_report
    return _run_report(forecast_otf_report)


@realtime_bp.route("/reports/interval", methods=["POST"])
@login_required
def report_interval():
    from app.realtime.reports import interval_report
    return _run_report(interval_report)


@realtime_bp.route("/reports/svl", methods=["POST"])
@login_required
def report_svl():
    from app.realtime.reports import service_level_report
    return _run_report(service_level_report)


@realtime_bp.route("/reports/absenteeism", methods=["POST"])
@login_required
def report_absenteeism():
    from app.realtime.reports import absenteeism_report
    return _run_report(absenteeism_report)


@realtime_bp.route("/reports/agent-status", methods=["POST"])
@login_required
def report_agent_status():
    from app.realtime.reports import agent_status_report
    return _run_report(agent_status_report)


@realtime_bp.route("/reports/efficiency", methods=["POST"])
@login_required
def report_efficiency():
    from app.realtime.reports import efficiency_report
    return _run_report(efficiency_report)


@realtime_bp.route("/reports/dashboard", methods=["POST"])
@login_required
def report_dashboard():
    from app.realtime.reports import combined_dashboard
    return _run_report(combined_dashboard)
