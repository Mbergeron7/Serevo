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
    lobs = _all_lobs(user)
    return render_template("realtime/index.html", user=user, lobs=lobs,
                           today=datetime.date.today().isoformat())


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
