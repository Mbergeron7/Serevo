"""
routes/realtime.py — Real-Time Monitoring blueprint
====================================================
Live queue monitoring, adherence, and staffing alerts.
"""

import logging
import datetime
import time
import threading

from flask import (Blueprint, render_template, request, jsonify)
from app.auth import login_required, get_current_user

log = logging.getLogger("serevo.realtime")

realtime_bp = Blueprint("realtime", __name__, url_prefix="/realtime")


@realtime_bp.teardown_request
def _rollback_on_error(exc):
    """Roll back the DB session if any unhandled exception occurred,
    preventing PendingRollbackError from poisoning subsequent requests."""
    if exc is not None:
        try:
            from app import db
            db.session.rollback()
        except Exception:
            pass


def _all_lobs(user):
    """Return list of LOB names for the current user."""
    if user and user.get("is_demo"):
        from app.demo_data import DEMO_LOBS
        return list(DEMO_LOBS)
    from app.realtime.engine import get_available_lobs
    return get_available_lobs()


# Google Sheet is no longer needed — DATA_SOURCE=postgres reads from the DB.
# Engine functions accept sheet=None and use DB first, with sheet fallback.
def _get_sheet():
    """Legacy helper — returns None so engine functions use the DB.
    The engine's own fallback will try Google Sheets if DB returns nothing."""
    return None


# ── Direct Google Sheet reading for live agent status ─────────
# Bypasses the 2-minute DB sync for near-real-time agent status,
# matching the old portal's approach of reading the sheet directly.

AGENT_STATUS_SHEET_ID = "15k9ahqgtuaigy4c7YNe2qy-OimAsjlbDQqd6F3VEt_g"
_SHEET_CACHE_TTL = 30  # seconds — short TTL for near-real-time data
_sheet_cache = {}       # key -> (timestamp, data)
_sheet_cache_lock = threading.Lock()
_sheet_fetch_lock = threading.Lock()  # prevents thundering-herd fetches

# Response-level cache for live-agents (avoids DB queries on every poll)
_live_agents_cache = {}  # key -> (timestamp, response_dict)
_live_agents_cache_lock = threading.Lock()
_LIVE_AGENTS_CACHE_TTL = 15  # seconds

# Dialer status SID → human-readable name
CP_STATUS_MAP = {
    "WA9c4e93d1de9b472ebb7e3b89426df574": "Ready",
    "WAd1f6c9952f3d04482bb9b6b28dd9819e": "Offline",
    "WA9a7153cbe2257eb452ff60067002087b": "On-call",
    "WA55e3a3eb3b9df10c69b902682ee87fbf": "Cool-Down",
    "WA3f5159a6c417b72f73c8128f5d3cc0ed": "Unavailable",
    "WA4090114d9b863e27e396b747e7071d7b": "No-Answer",
    "WAa8d8e71d8d79415ba585a53ca493521f": "Rejected",
    "WA960ed92496da0b023b5e69e4f5c783a2": "On Break",
    "WAa513fc91db454de83aefda3f5b689c5e": "Lunch",
    "WA009e46be32c8efb7132cfda1b5a42ea8": "ooq Client Account Work",
    "WA641c3fceafe43e72da443995033777a0": "No-Mic",
    "WA17446c1b845fe8159134a9e2da69dc8c": "Web Leads",
    "WA84686f853061b6271f6a43dd6a3c6384": "Long Distance",
    "WA0d324b251da94519738a9ee65fb89152": "eChat",
    "WA8cb16293fa633f5b155a5579c4199992": "Leader on Duty",
    "WAea2d79d49903a266c61e67a346ecf926": "ooq Meeting",
    "WA61f0205ad33fc03a62d1b21c6edd4cf5": "ooq Training",
    "WA1eb894d4d481b80387166e248bea38c4": "ooq Coaching",
    "WAaaf8f99bad17314f6908a2b20faf208a": "ooq After Shift",
    "WAcafc4a5f5503c8fbab4e19697b81edb3": "ooq System Issue",
    "WA2a678c82745f6019e3b4fdc994af7f18": "ooq Personal",
    "WA3f5159a6c417b72f73c8128f5d3cc0ed Duplicate": "Cascade",
}

# Dialer timestamps are in America/Chicago (CST/CDT).
# Compute offset to America/Toronto (EST/EDT) dynamically to handle DST.
def _cp_tz_offset_hours():
    """Return hours to add to CST/CDT to get EST/EDT.  Handles DST correctly."""
    try:
        from zoneinfo import ZoneInfo
        now = datetime.datetime.now(datetime.timezone.utc)
        cst_off = now.astimezone(ZoneInfo("America/Chicago")).utcoffset()
        est_off = now.astimezone(ZoneInfo("America/Toronto")).utcoffset()
        return (est_off - cst_off).total_seconds() / 3600
    except Exception:
        return 1  # fallback: CST is 1h behind EST when both are standard or both daylight


def _parse_datetime_str(s):
    """Parse a datetime string from the agent status sheet."""
    s = str(s or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.datetime.strptime(s, fmt)
        except ValueError:
            pass
    # Handle single-digit seconds like "2024-10-07 8:30:5"
    parts = s.split(" ")
    if len(parts) == 2:
        time_parts = parts[1].split(":")
        if len(time_parts) >= 2:
            padded = parts[0] + " " + ":".join(p.zfill(2) for p in time_parts)
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
                try:
                    return datetime.datetime.strptime(padded, fmt)
                except ValueError:
                    pass
    return None


def _read_agent_status_sheet():
    """Read agent status rows directly from Google Sheets with caching.
    Returns list of dicts with keys: user_id, start_time, c_activity_sid.
    Uses a 30-second cache to avoid hammering the API.
    A fetch lock prevents thundering-herd: only one thread fetches at a time,
    others wait and then get the freshly cached result."""
    cache_key = "agent_status"

    # Fast path: return cached data if fresh
    with _sheet_cache_lock:
        entry = _sheet_cache.get(cache_key)
        if entry:
            ts, data = entry
            if time.time() - ts < _SHEET_CACHE_TTL:
                return data

    # Serialize fetches so only one thread hits Google Sheets
    with _sheet_fetch_lock:
        # Re-check cache — another thread may have just populated it
        with _sheet_cache_lock:
            entry = _sheet_cache.get(cache_key)
            if entry:
                ts, data = entry
                if time.time() - ts < _SHEET_CACHE_TTL:
                    return data

        # Cache miss — fetch from Google Sheets
        try:
            from app.routes.realtime_sync import _get_gspread_client
            client, err = _get_gspread_client()
            if err:
                log.warning(f"Sheet auth failed: {err}")
                with _sheet_cache_lock:
                    entry = _sheet_cache.get(cache_key)
                    return entry[1] if entry else []

            doc = client.open_by_key(AGENT_STATUS_SHEET_ID)
            ws = doc.get_worksheet(0)
            rows = ws.get_all_records()
            log.info(f"Agent status sheet: fetched {len(rows)} rows directly")

            # Don't cache suspiciously small results — serve stale
            if len(rows) < 5:
                with _sheet_cache_lock:
                    entry = _sheet_cache.get(cache_key)
                    if entry and len(entry[1]) >= 5:
                        log.warning(f"Sheet returned only {len(rows)} rows — serving stale")
                        return entry[1]

            with _sheet_cache_lock:
                _sheet_cache[cache_key] = (time.time(), rows)
            return rows

        except Exception as e:
            log.error(f"Direct sheet read failed: {e}")
            with _sheet_cache_lock:
                entry = _sheet_cache.get(cache_key)
                if entry:
                    log.warning("Serving stale cache after sheet error")
                    return entry[1]
            return []


def _get_live_agents_from_sheet():
    """Process agent status sheet rows into per-agent current status.
    Returns dict: { user_id: { status, current_since, minutes_in_status } }
    """
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("America/Toronto")
    now_est = datetime.datetime.now(tz).replace(tzinfo=None)
    today_str = now_est.strftime("%Y-%m-%d")
    yesterday_str = (now_est.date() - datetime.timedelta(days=1)).strftime("%Y-%m-%d")

    rows = _read_agent_status_sheet()
    if not rows:
        return {}, now_est

    by_uid = {}
    for r in rows:
        uid = str(r.get("user_id", "") or "").strip()
        st = str(r.get("start_time", "") or "").strip()
        sid = str(r.get("c_activity_sid", "") or "").strip()
        if not uid:
            continue

        is_today = st.startswith(today_str)
        is_yesterday_cst = st.startswith(yesterday_str)
        if not is_today and not is_yesterday_cst:
            continue

        dt_cst = _parse_datetime_str(st)
        if not dt_cst:
            continue
        dt_est = dt_cst + datetime.timedelta(hours=_cp_tz_offset_hours())

        # For yesterday CST rows, only include if they fall on today in EST
        if is_yesterday_cst and dt_est.date() != now_est.date():
            continue

        status = CP_STATUS_MAP.get(sid, sid[:20] if sid else "Unknown")
        by_uid.setdefault(uid, []).append({"dt": dt_est, "status": status})

    # Derive current status per agent (latest event)
    agent_now = {}
    for uid, events in by_uid.items():
        events.sort(key=lambda x: x["dt"])
        last = events[-1]
        mins = round((now_est - last["dt"]).total_seconds() / 60, 1)
        agent_now[uid] = {
            "status": last["status"],
            "current_since": last["dt"].strftime("%H:%M"),
            "minutes_in_status": max(0, mins),
        }

    return agent_now, now_est


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
    lobs = _all_lobs(user)
    return render_template("realtime/index.html", user=user, lobs=lobs,
                           today=datetime.date.today().isoformat())


# ── Combined bundle (snapshot + adherence + service level in one call) ──
@realtime_bp.route("/api/bundle", methods=["POST"])
@login_required
def api_bundle():
    """Single endpoint returning snapshot, adherence, and service-level data.
    Avoids 3 separate round-trips and triples generate_shifts calls."""
    from app.realtime.engine import (get_intraday_snapshot,
                                      get_adherence_snapshot,
                                      get_service_level_intraday)
    try:
        payload = request.get_json(silent=True) or {}
        lob = payload.get("lob", "").strip()
        date_str = payload.get("date", "")
        if not lob:
            return jsonify({"success": False, "error": "LOB is required"})

        date_obj = (datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
                    if date_str else datetime.date.today())
        user = get_current_user()
        is_demo = user and user.get("is_demo")
        sheet = None if is_demo else _get_sheet()

        # Snapshot
        if lob == "All":
            snap = _aggregate_snapshots(user, date_obj)
        elif is_demo:
            from app.demo_data import get_demo_realtime_snapshot
            snap = get_demo_realtime_snapshot(lob, date_obj)
        else:
            snap = get_intraday_snapshot(lob, date_obj, sheet)
        snap["success"] = True

        # Adherence (generate_shifts is cached from snapshot call)
        if lob == "All":
            adh = _aggregate_adherence(user, date_obj)
        elif is_demo:
            from app.demo_data import get_demo_adherence
            adh = get_demo_adherence(lob, date_obj)
        else:
            adh = get_adherence_snapshot(lob, date_obj, sheet)
        if isinstance(adh, dict):
            adh["success"] = True
            adh_result = adh
        else:
            adh_result = {"success": True, "adherence": adh}

        # Service level (reuses snapshot data via cache)
        if is_demo:
            from app.demo_data import get_demo_service_level
            sl_data = get_demo_service_level(lob, date_obj)
        else:
            sl_data = get_service_level_intraday(lob, date_obj, sheet)
        sl_result = {"success": True, "intervals": sl_data}

        return jsonify({
            "success": True,
            "snapshot": snap,
            "adherence": adh_result,
            "service_level": sl_result,
        })
    except Exception as e:
        try:
            from app import db
            db.session.rollback()
        except Exception:
            pass
        log.error(f"Bundle API error: {e}")
        return jsonify({"success": False, "error": str(e)})


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


# ── Adherence Timeline (per-agent drill-down) ─────────────
@realtime_bp.route("/adherence-timeline", methods=["POST"])
@login_required
def adherence_timeline():
    """
    POST JSON: {lob, date?, employee_id}
    Returns per-agent schedule segments + simulated actual status for timeline view.
    """
    try:
        payload = request.get_json(silent=True) or {}
        lob = payload.get("lob", "").strip()
        date_str = payload.get("date", "")
        employee_id = payload.get("employee_id", "").strip()
        employee_name = payload.get("employee", "").strip()

        if not lob:
            return jsonify({"success": False, "error": "LOB is required"})

        date_obj = (datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
                    if date_str else datetime.date.today())

        user = get_current_user()
        is_demo = user and user.get("is_demo")

        # Get shifts for this day
        if is_demo:
            from app.demo_data import plan_demo_day
            day_plan, _ = plan_demo_day(date_obj, lob if lob != "All" else None)
            shifts = [s for s in day_plan if s.get("status") == "scheduled"]
        else:
            from app.scheduling.engine import generate_shifts
            sheet = _get_sheet()
            try:
                shifts, _, _ = generate_shifts(
                    lob if lob != "All" else None, date_obj, sheet=sheet
                )
            except Exception:
                log.warning("Failed to generate shifts for adherence", exc_info=True)
                shifts = []

        # Find the matching employee's shift
        target_shift = None
        for s in shifts:
            eid = s.get("employee_id", "")
            ename = s.get("employee", "")
            if employee_id and str(eid) == str(employee_id):
                target_shift = s
                break
            if employee_name and ename == employee_name:
                target_shift = s
                break

        if not target_shift:
            return jsonify({"success": False, "error": "Employee shift not found"})

        # Build scheduled segments (the "expected" timeline)
        segments = target_shift.get("segments", [])
        shift_start = target_shift["start"]
        shift_end = target_shift["end"]

        scheduled = []
        for seg in segments:
            scheduled.append({
                "type": seg.get("type", "on-call"),
                "start": seg.get("start", shift_start),
                "end": seg.get("end", shift_end),
            })
        if not scheduled:
            scheduled.append({
                "type": "on-call",
                "start": shift_start,
                "end": shift_end,
            })

        # Build actual status events
        actual = []
        if is_demo:
            actual = _build_demo_actuals(
                scheduled, shift_start, shift_end, date_obj,
                employee_name or target_shift.get("employee", "")
            )
        else:
            actual = _build_db_actuals(
                employee_id or target_shift.get("employee_id"),
                date_obj, shift_start, shift_end
            )

        # Compute adherence percentage
        adh_pct = _calc_adherence_pct(scheduled, actual, shift_start, shift_end)

        return jsonify({
            "success": True,
            "employee": target_shift.get("employee", ""),
            "employee_id": target_shift.get("employee_id", ""),
            "shift_start": shift_start,
            "shift_end": shift_end,
            "scheduled": scheduled,
            "actual": actual,
            "adherence_pct": round(adh_pct, 1),
        })

    except Exception as e:
        log.error(f"Adherence timeline error: {e}")
        return jsonify({"success": False, "error": str(e)})


def _time_to_mins(t):
    h, m = map(int, t.split(":"))
    return h * 60 + m


def _mins_to_time(m):
    return f"{m // 60:02d}:{m % 60:02d}"


def _build_demo_actuals(scheduled, shift_start, shift_end, date_obj, emp_name):
    """Generate simulated actual status events with small deviations from schedule."""
    import random
    rng = random.Random(hash(emp_name) + date_obj.toordinal())
    now = datetime.datetime.now()
    cur_mins = now.hour * 60 + now.minute
    s_mins = _time_to_mins(shift_start)
    e_mins = _time_to_mins(shift_end)

    # Only show actuals up to current time
    actual_end = min(cur_mins, e_mins)
    if cur_mins < s_mins:
        return []  # shift hasn't started

    actual = []
    for seg in scheduled:
        seg_start = _time_to_mins(seg["start"])
        seg_end = _time_to_mins(seg["end"])

        if seg_start >= actual_end:
            break

        clipped_end = min(seg_end, actual_end)

        # Add small time deviations for realism
        if seg["type"] in ("break", "lunch"):
            # Breaks sometimes start a bit late (0-5 min) and run over (0-3 min)
            late = rng.randint(0, 5)
            over = rng.randint(0, 3)
            act_start = min(seg_start + late, clipped_end)
            act_end = min(clipped_end + over, actual_end)
            # On-call before break deviation
            if actual and actual[-1]["end"] != _mins_to_time(act_start):
                actual[-1]["end"] = _mins_to_time(act_start)
            actual.append({
                "type": seg["type"],
                "start": _mins_to_time(act_start),
                "end": _mins_to_time(act_end),
            })
        else:
            # On-call: mostly adherent, occasionally a short aux/wrap
            actual.append({
                "type": "on-call",
                "start": _mins_to_time(seg_start),
                "end": _mins_to_time(clipped_end),
            })

    # Fix gaps and add a random short non-adherent period
    if len(actual) > 2 and rng.random() < 0.4:
        # Insert a short "aux" period somewhere in the on-call time
        idx = rng.randint(0, len(actual) - 1)
        if actual[idx]["type"] == "on-call":
            a_start = _time_to_mins(actual[idx]["start"])
            a_end = _time_to_mins(actual[idx]["end"])
            if a_end - a_start > 20:
                aux_start = a_start + rng.randint(10, max(11, a_end - a_start - 10))
                aux_dur = rng.randint(3, 8)
                aux_end = min(aux_start + aux_dur, a_end)
                # Split the on-call around the aux period
                new_events = []
                for i, ev in enumerate(actual):
                    if i == idx:
                        if aux_start > a_start:
                            new_events.append({"type": "on-call", "start": _mins_to_time(a_start), "end": _mins_to_time(aux_start)})
                        new_events.append({"type": "aux", "start": _mins_to_time(aux_start), "end": _mins_to_time(aux_end)})
                        if aux_end < a_end:
                            new_events.append({"type": "on-call", "start": _mins_to_time(aux_end), "end": _mins_to_time(a_end)})
                    else:
                        new_events.append(ev)
                actual = new_events

    return actual


def _build_db_actuals(employee_id, date_obj, shift_start, shift_end):
    """Load actual status events from AgentStatusEvent model."""
    if not employee_id:
        return []
    try:
        from app import db
        from app.models import AgentStatusEvent
        start_dt = datetime.datetime.combine(date_obj, datetime.time(
            *map(int, shift_start.split(":"))))
        end_dt = datetime.datetime.combine(date_obj, datetime.time(
            *map(int, shift_end.split(":"))))
        events = (AgentStatusEvent.query
                  .filter_by(employee_id=employee_id)
                  .filter(AgentStatusEvent.start_ts >= start_dt,
                          AgentStatusEvent.start_ts < end_dt)
                  .order_by(AgentStatusEvent.start_ts)
                  .all())
        result = []
        for ev in events:
            s = ev.start_ts.strftime("%H:%M")
            e = ev.end_ts.strftime("%H:%M") if ev.end_ts else datetime.datetime.now().strftime("%H:%M")
            status = (ev.status or "").lower()
            # Map ACD statuses to timeline types
            if status in ("available", "on call", "talking", "on-call"):
                typ = "on-call"
            elif status in ("break",):
                typ = "break"
            elif status in ("lunch",):
                typ = "lunch"
            elif status in ("aux", "not ready", "after call work", "wrap"):
                typ = "aux"
            else:
                typ = "aux"
            result.append({"type": typ, "start": s, "end": e})
        return result
    except Exception:
        log.warning("Failed to parse status intervals", exc_info=True)
        return []


def _calc_adherence_pct(scheduled, actual, shift_start, shift_end):
    """Calculate adherence % = minutes in-adherence / total scheduled minutes (up to now)."""
    now = datetime.datetime.now()
    cur_mins = now.hour * 60 + now.minute
    s_mins = _time_to_mins(shift_start)
    e_mins = _time_to_mins(shift_end)

    check_end = min(cur_mins, e_mins)
    if check_end <= s_mins:
        return 100.0

    total_mins = check_end - s_mins
    if total_mins <= 0:
        return 100.0

    # Build minute-by-minute scheduled state
    sched_state = ["on-call"] * total_mins
    for seg in scheduled:
        seg_s = max(_time_to_mins(seg["start"]) - s_mins, 0)
        seg_e = min(_time_to_mins(seg["end"]) - s_mins, total_mins)
        for m in range(seg_s, seg_e):
            sched_state[m] = seg["type"]

    # Build minute-by-minute actual state
    actual_state = ["unknown"] * total_mins
    for ev in actual:
        ev_s = max(_time_to_mins(ev["start"]) - s_mins, 0)
        ev_e = min(_time_to_mins(ev["end"]) - s_mins, total_mins)
        for m in range(ev_s, ev_e):
            actual_state[m] = ev["type"]

    # Compare
    in_adherence = sum(1 for m in range(total_mins) if sched_state[m] == actual_state[m])
    return (in_adherence / total_mins) * 100


# ── Leaderboard / Gamification (API) ──────────────────────
@realtime_bp.route("/leaderboard", methods=["POST"])
@login_required
def leaderboard():
    """
    POST JSON: {lob, date?}
    Returns adherence leaderboard with points and badges.
    """
    try:
        payload = request.get_json(silent=True) or {}
        lob = payload.get("lob", "").strip()
        date_str = payload.get("date", "")

        if not lob:
            return jsonify({"success": False, "error": "LOB is required"})

        date_obj = (datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
                    if date_str else datetime.date.today())

        user = get_current_user()
        is_demo = user and user.get("is_demo")

        # Get shifts with segments for adherence computation
        if is_demo:
            from app.demo_data import plan_demo_day
            day_plan, _ = plan_demo_day(date_obj, lob if lob != "All" else None)
            shifts = [s for s in day_plan if s.get("status") == "scheduled"]
        else:
            from app.scheduling.engine import generate_shifts
            sheet = _get_sheet()
            try:
                shifts, _, _ = generate_shifts(
                    lob if lob != "All" else None, date_obj, sheet=sheet
                )
            except Exception:
                log.warning("Failed to generate shifts for leaderboard", exc_info=True)
                shifts = []

        import random
        rng = random.Random(date_obj.toordinal() + hash(lob))
        now = datetime.datetime.now()
        cur_mins = now.hour * 60 + now.minute

        entries = []
        for s in shifts:
            emp = s.get("employee", "")
            emp_id = s.get("employee_id", "")
            s_mins = _time_to_mins(s["start"])
            e_mins = _time_to_mins(s["end"])

            if cur_mins < s_mins:
                continue  # shift hasn't started

            # Compute adherence from segments
            segments = s.get("segments", [])
            if not segments:
                segments = [{"type": "on-call", "start": s["start"], "end": s["end"]}]

            if is_demo:
                # Simulate adherence with some variance
                base_adh = rng.uniform(78, 100)
                adh_pct = round(min(100, base_adh), 1)
                ooa_count = 0 if adh_pct >= 98 else rng.randint(1, 3)
                logged_early = rng.random() < 0.3
            else:
                # Real: compute from DB actuals
                actual = _build_db_actuals(emp_id, date_obj, s["start"], s["end"])
                adh_pct = round(_calc_adherence_pct(segments, actual, s["start"], s["end"]), 1)
                ooa_count = len([1 for seg in actual if seg["type"] == "aux"])
                logged_early = False

            # Points: base 100, +/- based on adherence
            points = int(adh_pct)
            if adh_pct >= 100:
                points += 20
            elif adh_pct >= 95:
                points += 10
            if ooa_count == 0:
                points += 5
            if logged_early:
                points += 5

            # Badges
            badges = []
            if adh_pct >= 100:
                badges.append("🏆")
            elif adh_pct >= 95:
                badges.append("⭐")
            elif adh_pct >= 90:
                badges.append("🎯")
            if ooa_count == 0 and adh_pct >= 85:
                badges.append("🛡️")
            if logged_early:
                badges.append("⏰")
            # Streak badge (simulated for demo)
            if is_demo and rng.random() < 0.25 and adh_pct >= 95:
                badges.append("🔥")

            entries.append({
                "employee": emp,
                "employee_id": emp_id,
                "adherence_pct": adh_pct,
                "points": points,
                "badges": badges,
                "ooa_count": ooa_count,
            })

        # Sort by points descending
        entries.sort(key=lambda e: (-e["points"], -e["adherence_pct"]))

        # Stats
        avg_adh = round(sum(e["adherence_pct"] for e in entries) / max(1, len(entries)), 1)
        perfect = sum(1 for e in entries if e["adherence_pct"] >= 100)
        above_90 = sum(1 for e in entries if e["adherence_pct"] >= 90)

        return jsonify({
            "success": True,
            "entries": entries,
            "stats": {
                "avg_adherence": avg_adh,
                "perfect_count": perfect,
                "above_90_count": above_90,
                "total_agents": len(entries),
            },
        })

    except Exception as e:
        log.error(f"Leaderboard error: {e}")
        return jsonify({"success": False, "error": str(e)})


# ── Net Staffing (API) ─────────────────────────────────────
@realtime_bp.route("/net-staffing", methods=["POST"])
@login_required
def net_staffing():
    """
    POST JSON: {lob, date?}
    Returns per-interval net staffing with projected SL impact.
    If lob=="All", returns per-LOB breakdowns.
    """
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
            lobs = _all_lobs(user)
        else:
            lobs = [lob]

        from app.realtime.engine import get_intraday_snapshot, _get_actual_statuses
        from app.forecasting.engine import _service_level, _erlang_c

        # Get actual productive agent count for today
        actual_statuses = _get_actual_statuses(date_obj)
        actual_productive = sum(1 for v in actual_statuses.values() if v["is_productive"])

        results = []
        for l in lobs:
            if user and user.get("is_demo"):
                from app.demo_data import get_demo_realtime_snapshot
                snap = get_demo_realtime_snapshot(l, date_obj)
            else:
                sheet = _get_sheet()
                try:
                    snap = get_intraday_snapshot(l, date_obj, sheet)
                except Exception:
                    snap = {"intervals": []}

            intervals = snap.get("intervals", [])
            enriched = []
            for iv in intervals:
                offered = iv.get("forecast_offered", 0)
                aht = iv.get("forecast_aht", 0)
                sched = iv.get("scheduled", 0)
                req = iv.get("required", 0)

                # Compute projected SL with current staffing
                est_sl = None
                if offered > 0 and aht > 0 and sched > 0:
                    traffic = (offered * aht) / 1800  # 30-min interval
                    est_sl = round(_service_level(sched, traffic, 30, aht) * 100, 1)

                enriched.append({
                    "time": iv["time"],
                    "required": iv["required"],
                    "scheduled": iv["scheduled"],
                    "actual": actual_productive,
                    "net": round(iv["gap"], 1),
                    "actual_net": actual_productive - req if req else 0,
                    "coverage_pct": iv["coverage_pct"],
                    "est_sl": est_sl,
                    "forecast_offered": offered,
                    "status": iv["status"],
                    "actual_offered": iv.get("actual_offered"),
                    "actual_answered": iv.get("actual_answered"),
                    "actual_aht": iv.get("actual_aht"),
                    "actual_sl": iv.get("actual_sl"),
                })

            results.append({
                "lob": l,
                "intervals": enriched,
                "summary": snap.get("summary", {}),
            })

        return jsonify({
            "success": True,
            "lobs": results,
            "current_interval": snap.get("current_interval", ""),
        })

    except Exception as e:
        log.error(f"Net staffing error: {e}")
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
# INTRADAY OPTIMIZATION ENGINE
# ═════════════════════════════════════════════════════════════

import random as _random

@realtime_bp.route("/optimize", methods=["POST"])
@login_required
def optimize():
    """
    POST JSON: {lob, date?}
    Analyzes intraday staffing gaps and generates optimization
    recommendations: break moves, VTO offers, skill reassignments.
    """
    try:
        payload = request.get_json(silent=True) or {}
        lob = payload.get("lob", "").strip()
        date_str = payload.get("date", "")

        if not lob:
            return jsonify({"success": False, "error": "LOB is required"})

        date_obj = (datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
                    if date_str else datetime.date.today())

        user = get_current_user()

        # Get snapshot data (reuse existing infrastructure)
        if lob == "All":
            snap = _aggregate_snapshots(user, date_obj)
        elif user and user.get("is_demo"):
            from app.demo_data import get_demo_realtime_snapshot
            snap = get_demo_realtime_snapshot(lob, date_obj)
        else:
            from app.realtime.engine import get_intraday_snapshot
            sheet = _get_sheet()
            snap = get_intraday_snapshot(lob, date_obj, sheet)

        intervals = snap.get("intervals", [])
        shifts = snap.get("shifts", [])
        current_iv = snap.get("current_interval", "")

        recommendations = _generate_optimization_recs(
            intervals, shifts, current_iv, lob, user
        )

        # Compute savings summary
        total_savings_fte = sum(r.get("fte_impact", 0) for r in recommendations)
        total_sl_lift = sum(r.get("sl_impact", 0) for r in recommendations
                           if r.get("sl_impact", 0) > 0)

        return jsonify({
            "success": True,
            "recommendations": recommendations,
            "summary": {
                "total_recs": len(recommendations),
                "break_moves": sum(1 for r in recommendations if r["type"] == "break_move"),
                "vto_offers": sum(1 for r in recommendations if r["type"] == "vto"),
                "skill_reassign": sum(1 for r in recommendations if r["type"] == "skill_reassign"),
                "est_fte_savings": round(total_savings_fte, 1),
                "est_sl_lift": round(total_sl_lift, 1),
            },
            "current_interval": current_iv,
        })

    except Exception as e:
        log.error(f"Optimize error: {e}")
        return jsonify({"success": False, "error": str(e)})


def _generate_optimization_recs(intervals, shifts, current_iv, lob, user):
    """
    Analyze intervals for staffing gaps and generate actionable
    recommendations: break shifts, VTO for overstaffed periods,
    skill reassignment for understaffed ones.
    """
    from app.forecasting.engine import _service_level

    recs = []
    rec_id = 0

    # Classify intervals
    understaffed = []
    overstaffed = []
    for iv in intervals:
        if iv["time"] < current_iv:
            continue  # Only future/current intervals
        req = iv.get("required", 0)
        sched = iv.get("scheduled", 0)
        gap = iv.get("gap", 0)
        if req <= 0:
            continue
        if gap < -1:  # understaffed by more than 1
            understaffed.append(iv)
        elif gap > 2:  # overstaffed by more than 2
            overstaffed.append(iv)

    # ── 1. Break moves: shift breaks FROM understaffed TO overstaffed periods ──
    # Find pairs where moving a break improves both intervals
    for u_iv in understaffed[:8]:
        for o_iv in overstaffed[:8]:
            if abs(intervals.index(u_iv) - intervals.index(o_iv)) > 6:
                continue  # Too far apart for a break move
            deficit = abs(u_iv["gap"])
            surplus = o_iv["gap"]
            agents_to_move = min(int(min(deficit, surplus)), 3)
            if agents_to_move < 1:
                continue

            # Estimate SL improvement
            sl_lift = 0
            offered = u_iv.get("forecast_offered", 0)
            aht = u_iv.get("forecast_aht", 0)
            sched = u_iv.get("scheduled", 0)
            if offered > 0 and aht > 0 and sched > 0:
                traffic = (offered * aht) / 1800
                sl_before = _service_level(sched, traffic, 30, aht) * 100
                sl_after = _service_level(sched + agents_to_move, traffic, 30, aht) * 100
                sl_lift = round(sl_after - sl_before, 1)

            rec_id += 1
            recs.append({
                "id": rec_id,
                "type": "break_move",
                "priority": "high" if deficit >= 3 else "medium",
                "title": f"Move {agents_to_move} break(s) from {u_iv['time']} → {o_iv['time']}",
                "description": (
                    f"{u_iv['time']} is short {abs(u_iv['gap']):.0f} agents while "
                    f"{o_iv['time']} has {o_iv['gap']:.0f} surplus. "
                    f"Shifting {agents_to_move} break(s) adds coverage where needed."
                ),
                "from_interval": u_iv["time"],
                "to_interval": o_iv["time"],
                "agents": agents_to_move,
                "fte_impact": round(agents_to_move * 0.5, 1),
                "sl_impact": sl_lift,
            })
            break  # One rec per understaffed interval

    # ── 2. VTO offers for sustained overstaffing ──
    consecutive_over = []
    run = []
    for iv in intervals:
        if iv["time"] < current_iv:
            continue
        if iv.get("gap", 0) > 2 and iv.get("required", 0) > 0:
            run.append(iv)
        else:
            if len(run) >= 2:
                consecutive_over.append(run)
            run = []
    if len(run) >= 2:
        consecutive_over.append(run)

    for block in consecutive_over[:4]:
        surplus = min(iv["gap"] for iv in block)
        vto_agents = min(int(surplus), 4)
        if vto_agents < 1:
            continue
        start_t = block[0]["time"]
        end_t = block[-1]["time"]
        hours = len(block) * 0.5  # 30-min intervals

        rec_id += 1
        recs.append({
            "id": rec_id,
            "type": "vto",
            "priority": "medium" if vto_agents <= 2 else "low",
            "title": f"Offer VTO to {vto_agents} agent(s) for {start_t}–{end_t}",
            "description": (
                f"Overstaffed by {surplus:.0f}+ for {len(block)} intervals "
                f"({hours:.1f} hrs). VTO saves ~${vto_agents * hours * 18:.0f} "
                f"without impacting service level."
            ),
            "from_interval": start_t,
            "to_interval": end_t,
            "agents": vto_agents,
            "fte_impact": round(vto_agents * hours / 8, 1),
            "sl_impact": 0,
            "est_savings_usd": round(vto_agents * hours * 18, 0),
        })

    # ── 3. Skill reassignment for critical understaffing ──
    # Suggest pulling cross-trained agents from overstaffed LOBs
    is_demo = user and user.get("is_demo")
    if lob != "All" and is_demo:
        # In demo mode, simulate cross-skill agents
        from app.demo_data import DEMO_LOBS
        other_lobs = [l for l in DEMO_LOBS if l != lob]
        for u_iv in understaffed[:4]:
            if u_iv.get("gap", 0) >= -1:
                continue
            deficit = abs(u_iv["gap"])
            reassign = min(int(deficit * 0.5), 2)
            if reassign < 1:
                reassign = 1
            source_lob = other_lobs[rec_id % len(other_lobs)] if other_lobs else "Other"

            # Estimate SL improvement
            sl_lift = 0
            offered = u_iv.get("forecast_offered", 0)
            aht = u_iv.get("forecast_aht", 0)
            sched = u_iv.get("scheduled", 0)
            if offered > 0 and aht > 0 and sched > 0:
                traffic = (offered * aht) / 1800
                sl_before = _service_level(sched, traffic, 30, aht) * 100
                sl_after = _service_level(sched + reassign, traffic, 30, aht) * 100
                sl_lift = round(sl_after - sl_before, 1)

            rec_id += 1
            recs.append({
                "id": rec_id,
                "type": "skill_reassign",
                "priority": "high" if deficit >= 4 else "medium",
                "title": f"Reassign {reassign} agent(s) from {source_lob} at {u_iv['time']}",
                "description": (
                    f"{lob} is short {deficit:.0f} agents at {u_iv['time']}. "
                    f"Pull {reassign} cross-trained agent(s) from {source_lob} "
                    f"to cover the gap (est. +{sl_lift:.1f}% SL)."
                ),
                "from_interval": u_iv["time"],
                "to_interval": u_iv["time"],
                "agents": reassign,
                "source_lob": source_lob,
                "target_lob": lob,
                "fte_impact": round(reassign * 0.5, 1),
                "sl_impact": sl_lift,
            })

    # Sort by priority then SL impact
    prio_order = {"high": 0, "medium": 1, "low": 2}
    recs.sort(key=lambda r: (prio_order.get(r["priority"], 9), -(r.get("sl_impact", 0))))

    return recs


# ═════════════════════════════════════════════════════════════
# NUMERIC RTM REPORTS (Forecast/OTF, Interval, SVL, Absenteeism,
# Agent Status, Efficiency, Combined Dashboard)
# ═════════════════════════════════════════════════════════════

@realtime_bp.route("/reports")
@login_required
def reports():
    from flask import redirect, url_for
    return redirect(url_for("realtime.index"))


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
        try:
            from app import db
            db.session.rollback()
        except Exception:
            pass
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


# ═══════════════════════════════════════════════════════════════
# VTO / OT MANAGEMENT (supervisors & admins)
# ═══════════════════════════════════════════════════════════════

@realtime_bp.route("/vto-ot")
@login_required
def vto_ot_page():
    user = get_current_user()
    if not user or user.get("role") not in ("admin", "supervisor"):
        return jsonify(error="Forbidden"), 403
    from app.models import PlanningUnit
    lobs = [pu.name for pu in PlanningUnit.query.filter_by(is_active=True).order_by(PlanningUnit.name).all()]
    return render_template("realtime/vto_ot.html", user=user, lobs=lobs)


@realtime_bp.route("/vto-ot/list", methods=["POST"])
@login_required
def vto_ot_list():
    user = get_current_user()
    if not user or user.get("role") not in ("admin", "supervisor"):
        return jsonify(error="Forbidden"), 403
    from app.models import VTOOTPost, VTOOTSignup, Employee
    from datetime import date, timedelta

    payload = request.get_json(silent=True) or {}
    status_filter = payload.get("status", "all")
    type_filter = payload.get("type", "all")

    q = VTOOTPost.query.order_by(VTOOTPost.schedule_date.desc(), VTOOTPost.created_at.desc())
    if status_filter != "all":
        q = q.filter_by(status=status_filter)
    if type_filter != "all":
        q = q.filter_by(post_type=type_filter)

    posts = q.limit(100).all()
    result = []
    for p in posts:
        signups = VTOOTSignup.query.filter_by(post_id=p.id, status="confirmed").all()
        signup_names = []
        for s in signups:
            emp = Employee.query.get(s.employee_id)
            signup_names.append(emp.full_name if emp else f"Emp #{s.employee_id}")
        d = p.to_dict()
        d["signups"] = signup_names
        d["posted_by"] = p.posted_by
        result.append(d)

    return jsonify(success=True, posts=result)


@realtime_bp.route("/vto-ot/save", methods=["POST"])
@login_required
def vto_ot_save():
    user = get_current_user()
    if not user or user.get("role") not in ("admin", "supervisor"):
        return jsonify(error="Forbidden"), 403
    from app.models import db, VTOOTPost, PlanningUnit
    from datetime import datetime as dt

    payload = request.get_json(silent=True) or {}
    post_type = payload.get("post_type", "vto")
    schedule_date = payload.get("schedule_date")
    start_time = payload.get("start_time")
    end_time = payload.get("end_time")
    hours = payload.get("hours", 0)
    slots = payload.get("slots", 1)
    lob = payload.get("lob", "")
    notes = payload.get("notes", "")
    post_id = payload.get("id")

    if not schedule_date:
        return jsonify(success=False, error="Date is required")

    pu_id = None
    if lob:
        pu = PlanningUnit.query.filter_by(name=lob).first()
        if pu:
            pu_id = pu.id

    try:
        sdate = dt.strptime(schedule_date, "%Y-%m-%d").date()
        stime = dt.strptime(start_time, "%H:%M").time() if start_time else None
        etime = dt.strptime(end_time, "%H:%M").time() if end_time else None
    except (ValueError, TypeError):
        return jsonify(success=False, error="Invalid date/time format")

    if post_id:
        post = VTOOTPost.query.get(post_id)
        if not post:
            return jsonify(success=False, error="Post not found")
        post.post_type = post_type
        post.schedule_date = sdate
        post.start_time = stime
        post.end_time = etime
        post.hours = hours
        post.slots = max(1, int(slots))
        post.planning_unit_id = pu_id
        post.notes = notes
    else:
        post = VTOOTPost(
            post_type=post_type,
            schedule_date=sdate,
            start_time=stime,
            end_time=etime,
            hours=hours,
            slots=max(1, int(slots)),
            planning_unit_id=pu_id,
            status="open",
            posted_by=user.get("id"),
            notes=notes,
        )
        db.session.add(post)

    db.session.commit()
    return jsonify(success=True, id=post.id)


@realtime_bp.route("/vto-ot/cancel", methods=["POST"])
@login_required
def vto_ot_cancel():
    user = get_current_user()
    if not user or user.get("role") not in ("admin", "supervisor"):
        return jsonify(error="Forbidden"), 403
    from app.models import db, VTOOTPost

    payload = request.get_json(silent=True) or {}
    post_id = payload.get("id")
    post = VTOOTPost.query.get(post_id)
    if not post:
        return jsonify(success=False, error="Post not found")

    post.status = "cancelled"
    db.session.commit()
    return jsonify(success=True)


# ── Live Agents API ──────────────────────────────────────────

@realtime_bp.route("/api/live-agents", methods=["POST"])
@login_required
def api_live_agents():
    """Return live agent status data grouped by LOB."""
    user = get_current_user()
    if not user:
        return jsonify(error="Not authenticated"), 401

    if user.get("is_demo"):
        from app.demo_data import get_demo_live_agents
        data = get_demo_live_agents()
        return jsonify(data)

    # Real data: read directly from Google Sheets for near-real-time status
    # Check response-level cache first (shared across all users)
    with _live_agents_cache_lock:
        _la_entry = _live_agents_cache.get("result")
        if _la_entry:
            _la_ts, _la_data = _la_entry
            if time.time() - _la_ts < _LIVE_AGENTS_CACHE_TTL:
                return jsonify(_la_data)

    try:
        from app.models import db, Employee, Schedule, ShiftSegment

        agent_now, now_est = _get_live_agents_from_sheet()

        if not agent_now:
            log.warning("No agent status data from sheet — returning empty")
            return jsonify({"by_lob": {}, "pre_shift": []})

        log.debug(f"Sheet returned {len(agent_now)} unique agents with activity today")

        # Load active employees and build lookup by all ID fields
        # (CP user_id can match external_id_1, external_id_2, or employee_id)
        employees = Employee.query.filter_by(status="Active").all()
        ext_id_map = {}   # external id → Employee
        emp_id_set = set() # all employee IDs for schedule query
        for emp in employees:
            emp_id_set.add(emp.id)
            if emp.external_id_1:
                ext_id_map[emp.external_id_1.strip()] = emp
            if getattr(emp, "external_id_2", None):
                ext_id_map[emp.external_id_2.strip()] = emp
            if emp.employee_id:
                ext_id_map[str(emp.employee_id).strip()] = emp

        # ── Load today's schedules ─────────────────────────────
        today = now_est.date()
        now_time = now_est.time()
        schedules = Schedule.query.filter(
            Schedule.schedule_date == today,
            Schedule.status == "scheduled",
        ).all()  # segments are eager-loaded (lazy="joined" on model)

        # Build schedule lookup: employee_id → Schedule
        sched_by_emp = {}
        for s in schedules:
            sched_by_emp[s.employee_id] = s

        # Helper: what activity should the agent be doing right now?
        def _sched_now(sched):
            """Return 'break', 'lunch', or '' based on current segment."""
            if not sched or not sched.segments:
                return ""
            for seg in sched.segments:
                if seg.start_time <= now_time < seg.end_time:
                    if seg.activity_type == "break":
                        return "break"
                    if seg.activity_type == "lunch":
                        return "lunch"
            return ""

        # ── Build response ─────────────────────────────────────
        by_lob = {}
        pre_shift = []
        matched = 0
        unmatched_uids = []
        logged_in_emp_ids = set()  # track who IS logged in

        for uid, ag in agent_now.items():
            emp = ext_id_map.get(uid)
            if not emp:
                unmatched_uids.append(uid)
                continue
            matched += 1
            logged_in_emp_ids.add(emp.id)

            pu = emp.planning_unit
            lob = pu.name if pu else "Unassigned"

            sched = sched_by_emp.get(emp.id)
            shift_start = sched.shift_start.strftime("%H:%M") if sched and sched.shift_start else ""
            shift_end = sched.shift_end.strftime("%H:%M") if sched and sched.shift_end else ""

            # Determine schedule flags
            not_scheduled = sched is None
            is_pre_shift = bool(sched and sched.shift_start and now_time < sched.shift_start)
            is_shift_done = bool(sched and sched.shift_end and now_time > sched.shift_end)
            sched_now_val = _sched_now(sched)

            agent = {
                "name": emp.full_name,
                "user_id": uid,
                "status": ag["status"],
                "minutes_in_status": round(ag["minutes_in_status"]),
                "current_since": ag["current_since"],
                "lob": lob,
                "shift_start": shift_start,
                "shift_end": shift_end,
                "shift_done": is_shift_done,
                "sched_now": sched_now_val,
                "not_scheduled": not_scheduled,
                "pre_shift": is_pre_shift,
            }

            by_lob.setdefault(lob, []).append(agent)

        # ── Build "Scheduled — Not Logged In" list ─────────────
        PRE_SHIFT_LOOKAHEAD_MINS = 60  # show agents up to 60 min before shift
        for s in schedules:
            if s.employee_id in logged_in_emp_ids:
                continue  # already logged in, skip
            if not s.shift_start:
                continue
            emp = s.employee
            if not emp or emp.status != "Active":
                continue
            pu = emp.planning_unit
            lob = pu.name if pu else "Unassigned"

            start_dt = datetime.datetime.combine(today, s.shift_start)
            mins_until = (start_dt - now_est).total_seconds() / 60

            if now_time > s.shift_start:
                # Past start time — absent
                mins_late = round((now_est - start_dt).total_seconds() / 60)
                if s.shift_end and now_time > s.shift_end:
                    continue  # shift is over, don't show
                pre_shift.append({
                    "name": emp.full_name,
                    "lob": lob,
                    "shift_start": s.shift_start.strftime("%H:%M"),
                    "indicator": "absent",
                    "mins_late": mins_late,
                })
            elif mins_until <= PRE_SHIFT_LOOKAHEAD_MINS:
                # Upcoming within lookahead window — not yet started
                pre_shift.append({
                    "name": emp.full_name,
                    "lob": lob,
                    "shift_start": s.shift_start.strftime("%H:%M"),
                    "indicator": "not_yet_started",
                    "mins_late": 0,
                })

        # Sort pre_shift: absent first (by mins_late desc), then not_yet_started (by shift_start asc)
        pre_shift.sort(key=lambda x: (0 if x["indicator"] == "absent" else 1, -x.get("mins_late", 0), x["shift_start"]))

        if unmatched_uids:
            log.warning(f"Live agents: {matched} matched, {len(unmatched_uids)} unmatched UIDs: {unmatched_uids[:10]}")
        else:
            log.debug(f"Live agents: {matched} matched, 0 unmatched")

        result = {"by_lob": by_lob, "pre_shift": pre_shift}
        with _live_agents_cache_lock:
            _live_agents_cache["result"] = (time.time(), result)
        return jsonify(result)

    except Exception as e:
        log.exception("live-agents error")
        return jsonify(error=str(e)), 500


@realtime_bp.route("/api/agent-detail", methods=["POST"])
@login_required
def api_agent_detail():
    """Return detailed schedule + timeline for one agent."""
    user = get_current_user()
    if not user:
        return jsonify(error="Not authenticated"), 401

    payload = request.get_json(silent=True) or {}
    uid = payload.get("uid") or request.args.get("uid", "")
    if not uid:
        return jsonify(error="Missing uid"), 400

    if user.get("is_demo"):
        from app.demo_data import get_demo_agent_detail
        return jsonify(get_demo_agent_detail(uid))

    # Real data: look up agent's schedule + current status from sheet
    try:
        from app.models import db, Employee, Schedule
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("America/Toronto")
        now_est = datetime.datetime.now(tz).replace(tzinfo=None)
        today = now_est.date()

        # Find the employee by uid — match against all external ID fields
        emp = None
        employees = Employee.query.filter_by(status="Active").all()
        for e in employees:
            if (e.external_id_1 and e.external_id_1.strip() == uid) or \
               (getattr(e, "external_id_2", None) and e.external_id_2.strip() == uid) or \
               (e.employee_id and str(e.employee_id).strip() == uid):
                emp = e
                break

        if not emp:
            return jsonify(error="Agent not found"), 404

        now_time = now_est.time()
        sched = Schedule.query.filter_by(
            employee_id=emp.id, schedule_date=today, status="scheduled"
        ).first()

        segments = []
        if sched and sched.segments:
            for seg in sched.segments:
                # Determine adherence class
                if seg.end_time and now_time > seg.end_time:
                    adh_class = "ok"
                    adherence = "✓ Done"
                elif seg.start_time and seg.end_time and seg.start_time <= now_time < seg.end_time:
                    adh_class = "active"
                    adherence = "● Now"
                else:
                    adh_class = "upcoming"
                    adherence = "Upcoming"
                segments.append({
                    "activity": seg.activity_type,
                    "start": seg.start_time.strftime("%H:%M") if seg.start_time else "",
                    "end": seg.end_time.strftime("%H:%M") if seg.end_time else "",
                    "duration_mins": seg.duration_mins,
                    "adh_class": adh_class,
                    "adherence": adherence,
                })

        # Get status timeline from sheet
        agent_now, _ = _get_live_agents_from_sheet()
        rows = _read_agent_status_sheet()
        today_str = now_est.strftime("%Y-%m-%d")
        yesterday_str = (now_est.date() - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        timeline = []
        for r in rows:
            r_uid = str(r.get("user_id", "") or "").strip()
            if r_uid != uid:
                continue
            st = str(r.get("start_time", "") or "").strip()
            sid = str(r.get("c_activity_sid", "") or "").strip()
            if not st.startswith(today_str) and not st.startswith(yesterday_str):
                continue
            dt_cst = _parse_datetime_str(st)
            if not dt_cst:
                continue
            dt_est = dt_cst + datetime.timedelta(hours=_cp_tz_offset_hours())
            if dt_est.date() != today:
                continue
            status = CP_STATUS_MAP.get(sid, sid[:20] if sid else "Unknown")
            timeline.append({
                "time": dt_est.strftime("%H:%M"),
                "status": status,
            })
        timeline.sort(key=lambda x: x["time"])

        current = agent_now.get(uid, {})

        return jsonify({
            "name": emp.full_name,
            "lob": emp.planning_unit.name if emp.planning_unit else "Unassigned",
            "shift_start": sched.shift_start.strftime("%H:%M") if sched and sched.shift_start else "",
            "shift_end": sched.shift_end.strftime("%H:%M") if sched and sched.shift_end else "",
            "segments": segments,
            "timeline": timeline,
            "current_status": current.get("status", "Offline"),
            "current_since": current.get("current_since", ""),
            "minutes_in_status": round(current.get("minutes_in_status", 0)),
        })
    except Exception as e:
        log.exception("agent-detail error")
        return jsonify(error=str(e)), 500


@realtime_bp.route("/vto-ot/delete", methods=["POST"])
@login_required
def vto_ot_delete():
    user = get_current_user()
    if not user or user.get("role") not in ("admin", "supervisor"):
        return jsonify(error="Forbidden"), 403
    from app.models import db, VTOOTPost, VTOOTSignup

    payload = request.get_json(silent=True) or {}
    post_id = payload.get("id")
    post = VTOOTPost.query.get(post_id)
    if not post:
        return jsonify(success=False, error="Post not found")

    VTOOTSignup.query.filter_by(post_id=post_id).delete()
    db.session.delete(post)
    db.session.commit()
    return jsonify(success=True)
