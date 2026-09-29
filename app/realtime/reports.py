"""
realtime/reports.py — Numeric real-time management reports
==========================================================
Table-first reports modelled on a classic RTM portal:

  forecast_otf_report      Forecast vs actual by LOB (Daily CV, up-to-now, OTF %, SL, ABND %)
  interval_report          Half-hour interval report for one LOB (or all)
  service_level_report     SVL calculator with what-if to reach target
  absenteeism_report       Late / absent / early-leave vs schedule
  agent_status_report      Current status per agent + history
  efficiency_report        Scheduled vs logged-in vs productive hours
  combined_dashboard       One-page numeric summary of everything above

Every builder takes (lobs, date_obj, user, sheet) and returns JSON-ready dicts.
"""

import datetime
import logging
from collections import defaultdict

from app.realtime.actuals import (get_interval_actuals, get_agent_events,
                                  is_productive, is_logged_in)

log = logging.getLogger("serevo.reports")


# ── Helpers ───────────────────────────────────────────────────

def _pct(num, den, nd=1):
    return round(num / den * 100, nd) if den else None


def _now():
    try:
        from zoneinfo import ZoneInfo
        from config import cfg
        tz = getattr(cfg, "TIMEZONE", None) or "America/Toronto"
        return datetime.datetime.now(ZoneInfo(tz)).replace(tzinfo=None)
    except Exception:
        return datetime.datetime.now()


def _hm(ts):
    """'YYYY-MM-DD HH:MM[:SS]' or 'HH:MM' -> 'HH:MM'."""
    ts = str(ts or "")
    return ts[11:16] if len(ts) > 10 else ts[:5]


def _mins(hhmm):
    try:
        h, m = str(hhmm)[:5].split(":")
        return int(h) * 60 + int(m)
    except Exception:
        return 0


def _parse_dt(s):
    try:
        return datetime.datetime.strptime(str(s)[:19], "%Y-%m-%d %H:%M:%S")
    except Exception:
        try:
            return datetime.datetime.strptime(str(s)[:16], "%Y-%m-%d %H:%M")
        except Exception:
            return None


def _lob_targets(lob):
    """Service level target and threshold for a LOB (defaults 80% / 20s)."""
    try:
        from app.models import db, LOBSetting, PlanningUnit
        pu = PlanningUnit.query.filter(db.func.lower(PlanningUnit.name) == lob.lower()).first()
        if pu:
            ls = LOBSetting.query.filter_by(planning_unit_id=pu.id).first()
            if ls:
                return (ls.service_level_target or 0.8), (ls.target_asa or 20)
    except Exception:
        pass
    return 0.8, 20


def _forecast(lob, date_obj, user):
    """[{time 'HH:MM', offered, aht}] for the LOB/day."""
    try:
        if user and user.get("is_demo"):
            from app.demo_data import get_demo_forecast
            rows, _ = get_demo_forecast(lob, date_obj)
        else:
            from app.data_source import get_source
            rows, _ = get_source().get_forecast(lob, date_obj)
        return [{"time": _hm(r.get("time")), "offered": float(r.get("offered") or 0),
                 "aht": float(r.get("aht") or 0)} for r in (rows or [])]
    except Exception as e:
        log.debug(f"forecast unavailable for {lob}: {e}")
        return []


def _employees_for_lob(lob, sheet=None):
    try:
        from app.scheduling.engine import _get_employees_for_lob
        return _get_employees_for_lob(lob, sheet)
    except Exception:
        return []


def _shifts(lob, date_obj, user, sheet=None):
    """Scheduled shifts for LOB/day: saved schedules first, else generated."""
    if user and user.get("is_demo"):
        from app.demo_data import get_demo_schedules, DEMO_EMPLOYEES
        ids = {e["Employee ID"] for e in DEMO_EMPLOYEES if e["Latest Skill Name"] == lob}
        return [s for s in get_demo_schedules(date_obj)
                if s["employee_id"] in ids and s.get("status") != "off" and s.get("start")]
    try:
        from app.models import db, Schedule, PlanningUnit
        pu = PlanningUnit.query.filter(db.func.lower(PlanningUnit.name) == lob.lower()).first()
        if pu:
            rows = Schedule.query.filter_by(planning_unit_id=pu.id, schedule_date=date_obj).all()
            if rows:
                return [r.to_dict() for r in rows if r.shift_start]
    except Exception:
        pass
    try:
        from app.scheduling.engine import generate_shifts
        shifts, _, _ = generate_shifts(lob, date_obj, sheet=sheet)
        return shifts
    except Exception:
        return []


def _up_to_now_cutoff(date_obj, now):
    """Interval cutoff for 'up to now' comparisons (whole intervals only)."""
    if date_obj < now.date():
        return "23:59"
    if date_obj > now.date():
        return "00:00"
    m = (now.minute // 30) * 30
    return f"{now.hour:02d}:{m:02d}"


# ═════════════════════════════════════════════════════════════
# 1. FORECAST & OTF BY LOB
# ═════════════════════════════════════════════════════════════

def forecast_otf_report(lobs, date_obj, user=None, sheet=None):
    now = _now()
    cutoff = _up_to_now_cutoff(date_obj, now)
    rows = []
    totals = defaultdict(float)
    for lob in lobs:
        fc = _forecast(lob, date_obj, user)
        act = get_interval_actuals(lob, date_obj, user, sheet)
        sl_target, threshold = _lob_targets(lob)

        daily_cv = sum(f["offered"] for f in fc)
        utonow_cv = sum(f["offered"] for f in fc if f["time"] < cutoff)
        offered = sum(a["offered"] for a in act)
        answered = sum(a["answered"] for a in act)
        within = sum(a["answered_within"] for a in act)
        aband = sum(a["abandoned"] for a in act)

        row = {
            "lob": lob,
            "forecast_daily_cv": round(daily_cv),
            "forecast_up_to_now": round(utonow_cv),
            "actual_offered": offered,
            "otf_pct": _pct(offered, utonow_cv),
            "answered": answered,
            "answered_within": within,
            "service_level": _pct(within, offered),
            "sl_target": round(sl_target * 100),
            "threshold_secs": threshold,
            "abandoned": aband,
            "abandoned_pct": _pct(aband, offered),
            "progress_pct": _pct(utonow_cv, daily_cv),
            "remaining_forecast": round(max(0, daily_cv - utonow_cv)),
            "has_actuals": bool(act),
        }
        rows.append(row)
        for k in ("forecast_daily_cv", "forecast_up_to_now", "actual_offered",
                  "answered", "answered_within", "abandoned"):
            totals[k] += row[k]

    total_row = {
        "lob": "All LOBs",
        "forecast_daily_cv": round(totals["forecast_daily_cv"]),
        "forecast_up_to_now": round(totals["forecast_up_to_now"]),
        "actual_offered": int(totals["actual_offered"]),
        "otf_pct": _pct(totals["actual_offered"], totals["forecast_up_to_now"]),
        "answered": int(totals["answered"]),
        "answered_within": int(totals["answered_within"]),
        "service_level": _pct(totals["answered_within"], totals["actual_offered"]),
        "abandoned": int(totals["abandoned"]),
        "abandoned_pct": _pct(totals["abandoned"], totals["actual_offered"]),
        "progress_pct": _pct(totals["forecast_up_to_now"], totals["forecast_daily_cv"]),
        "remaining_forecast": round(max(0, totals["forecast_daily_cv"] - totals["forecast_up_to_now"])),
    }
    return {"date": date_obj.isoformat(), "as_of": now.strftime("%H:%M"),
            "cutoff": cutoff, "rows": rows, "total": total_row}


# ═════════════════════════════════════════════════════════════
# 2. INTERVAL REPORT
# ═════════════════════════════════════════════════════════════

def interval_report(lobs, date_obj, user=None, sheet=None):
    """Per-interval forecast vs actual. Multiple LOBs are summed."""
    now = _now()
    cutoff = _up_to_now_cutoff(date_obj, now)
    fc_map = defaultdict(lambda: {"offered": 0.0, "aht_w": 0.0})
    act_map = defaultdict(lambda: {"offered": 0, "answered": 0, "answered_within": 0,
                                   "abandoned": 0, "rolled": 0, "asa_w": 0.0, "asa_n": 0,
                                   "aht_w": 0.0, "aht_n": 0, "max_queued": None})
    for lob in lobs:
        for f in _forecast(lob, date_obj, user):
            fc_map[f["time"]]["offered"] += f["offered"]
            fc_map[f["time"]]["aht_w"] += f["aht"] * f["offered"]
        for a in get_interval_actuals(lob, date_obj, user, sheet):
            m = act_map[a["time"]]
            for k in ("offered", "answered", "answered_within", "abandoned", "rolled"):
                m[k] += int(a.get(k) or 0)
            if a.get("asa") is not None and a["answered"]:
                m["asa_w"] += a["asa"] * a["answered"]; m["asa_n"] += a["answered"]
            if a.get("aht") is not None and a["answered"]:
                m["aht_w"] += a["aht"] * a["answered"]; m["aht_n"] += a["answered"]
            if a.get("max_queued") is not None:
                m["max_queued"] = max(m["max_queued"] or 0, a["max_queued"])

    times = sorted(set(fc_map) | set(act_map))
    rows = []
    tot = defaultdict(float)
    for t in times:
        f = fc_map.get(t, {"offered": 0.0, "aht_w": 0.0})
        a = act_map.get(t)
        fc_off = f["offered"]
        fc_aht = round(f["aht_w"] / fc_off) if fc_off else None
        if a:
            offered = a["offered"]
            lost = a["abandoned"] + a["rolled"]
            row = {
                "time": t, "is_past": t < cutoff, "is_current": t == cutoff,
                "forecast_cv": round(fc_off), "offered": offered,
                "otf_pct": _pct(offered, fc_off),
                "answered": a["answered"], "answered_within": a["answered_within"],
                "abandoned": a["abandoned"], "rolled": a["rolled"],
                "pct_lost": _pct(lost, offered),
                "service_level": _pct(a["answered_within"], offered),
                "asa": round(a["asa_w"] / a["asa_n"]) if a["asa_n"] else None,
                "actual_aht": round(a["aht_w"] / a["aht_n"]) if a["aht_n"] else None,
                "forecast_aht": fc_aht,
                "max_queued": a["max_queued"],
            }
            tot["offered"] += offered; tot["answered"] += a["answered"]
            tot["within"] += a["answered_within"]; tot["abandoned"] += a["abandoned"]
            tot["rolled"] += a["rolled"]; tot["asa_w"] += a["asa_w"]; tot["asa_n"] += a["asa_n"]
            tot["aht_w"] += a["aht_w"]; tot["aht_n"] += a["aht_n"]
            if t < cutoff or t == cutoff:
                tot["fc_utonow"] += fc_off
        else:
            row = {"time": t, "is_past": t < cutoff, "is_current": t == cutoff,
                   "forecast_cv": round(fc_off), "offered": None, "otf_pct": None,
                   "answered": None, "answered_within": None, "abandoned": None,
                   "rolled": None, "pct_lost": None, "service_level": None, "asa": None,
                   "actual_aht": None, "forecast_aht": fc_aht, "max_queued": None}
        tot["fc_daily"] += fc_off
        rows.append(row)

    total = {
        "forecast_cv": round(tot["fc_daily"]),
        "forecast_up_to_now": round(tot["fc_utonow"]),
        "offered": int(tot["offered"]),
        "otf_pct": _pct(tot["offered"], tot["fc_utonow"]),
        "answered": int(tot["answered"]), "answered_within": int(tot["within"]),
        "abandoned": int(tot["abandoned"]), "rolled": int(tot["rolled"]),
        "pct_lost": _pct(tot["abandoned"] + tot["rolled"], tot["offered"]),
        "service_level": _pct(tot["within"], tot["offered"]),
        "asa": round(tot["asa_w"] / tot["asa_n"]) if tot["asa_n"] else None,
        "actual_aht": round(tot["aht_w"] / tot["aht_n"]) if tot["aht_n"] else None,
    }
    return {"date": date_obj.isoformat(), "lobs": lobs, "cutoff": cutoff,
            "rows": rows, "total": total}


# ═════════════════════════════════════════════════════════════
# 3. SERVICE LEVEL CALCULATOR
# ═════════════════════════════════════════════════════════════

def service_level_report(lobs, date_obj, user=None, sheet=None):
    """Per-LOB SL so far, plus what it takes to finish the day on target."""
    base = forecast_otf_report(lobs, date_obj, user, sheet)
    rows = []
    for r in base["rows"]:
        target = r["sl_target"] / 100.0
        offered = r["actual_offered"]
        within = r["answered_within"]
        remaining = r["remaining_forecast"]
        projected_total = offered + remaining
        need_total_within = target * projected_total
        need_remaining_within = max(0.0, need_total_within - within)
        required_rate = _pct(need_remaining_within, remaining) if remaining else None
        # Can we still make it? If required rate > 100 the day is lost.
        if remaining <= 0:
            outlook = "final"
        elif required_rate is None:
            outlook = "n/a"
        elif required_rate > 100:
            outlook = "unreachable"
        elif required_rate > 95:
            outlook = "at risk"
        else:
            outlook = "on track"
        # Calls that could still be missed while staying on target
        cushion = int(max(0, (within + remaining) - need_total_within)) if remaining else 0
        rows.append({**r,
                     "projected_total_offered": round(projected_total),
                     "needed_within_remaining": round(need_remaining_within),
                     "required_rate_remaining_pct": required_rate,
                     "cushion_calls": cushion,
                     "outlook": outlook})
    return {"date": base["date"], "as_of": base["as_of"], "rows": rows, "total": base["total"]}


# ═════════════════════════════════════════════════════════════
# 4. ABSENTEEISM
# ═════════════════════════════════════════════════════════════

def _first_last_login(events):
    """(first_login_dt, last_offline_dt, current_status) from an agent's events."""
    first = None
    last_end = None
    current = None
    for e in sorted(events, key=lambda x: x["start"]):
        st = _parse_dt(e["start"])
        en = _parse_dt(e["end"]) if e.get("end") else None
        if is_logged_in(e["status"]):
            if first is None or (st and st < first):
                first = st
            if en and (last_end is None or en > last_end):
                last_end = en
            if not en:
                last_end = None  # still logged in
        current = e["status"]
    return first, last_end, current


def absenteeism_report(lobs, date_obj, user=None, sheet=None, late_grace_mins=5, early_grace_mins=5):
    now = _now()
    is_today = date_obj == now.date()
    by_lob = []
    late_rows, absent_rows, early_rows = [], [], []
    events_all = get_agent_events(date_obj, user, sheet)
    ev_by_emp = defaultdict(list)
    for e in events_all:
        ev_by_emp[str(e["employee_id"])].append(e)

    for lob in lobs:
        shifts = _shifts(lob, date_obj, user, sheet)
        sched = len(shifts)
        absent = late = early = 0
        for s in shifts:
            ext = str(s.get("employee_id", ""))
            sh_start = datetime.datetime.combine(date_obj, datetime.time(*map(int, s["start"].split(":")[:2])))
            sh_end = datetime.datetime.combine(date_obj, datetime.time(*map(int, s["end"].split(":")[:2])))
            first, last_end, current = _first_last_login(ev_by_emp.get(ext, []))
            base = {"employee": s.get("employee", ext), "employee_id": ext, "lob": lob,
                    "scheduled_from": s["start"], "scheduled_to": s["end"]}

            if first is None:
                # No login at all
                if is_today and now < sh_start + datetime.timedelta(minutes=late_grace_mins):
                    continue  # shift hasn't started yet
                absent += 1
                absent_rows.append({**base, "status": "No login" if (not is_today or now >= sh_end) else "Not logged in"})
                continue

            delta = (first - sh_start).total_seconds() / 60
            if delta > late_grace_mins:
                late += 1
                late_rows.append({**base, "actual_start": first.strftime("%H:%M"),
                                  "minutes_late": int(delta), "recent_status": current})

            if last_end and last_end < sh_end - datetime.timedelta(minutes=early_grace_mins):
                if not is_today or now > last_end + datetime.timedelta(minutes=15):
                    early += 1
                    early_rows.append({**base, "last_offline": last_end.strftime("%H:%M"),
                                       "minutes_early": int((sh_end - last_end).total_seconds() / 60)})

        by_lob.append({"lob": lob, "scheduled": sched, "absent": absent,
                       "absence_pct": _pct(absent, sched), "late": late,
                       "late_pct": _pct(late, sched), "early_leave": early,
                       "early_leave_pct": _pct(early, sched)})

    tot_s = sum(r["scheduled"] for r in by_lob)
    tot_a = sum(r["absent"] for r in by_lob)
    tot_l = sum(r["late"] for r in by_lob)
    tot_e = sum(r["early_leave"] for r in by_lob)
    return {"date": date_obj.isoformat(), "by_lob": by_lob,
            "total": {"lob": "All LOBs", "scheduled": tot_s, "absent": tot_a,
                      "absence_pct": _pct(tot_a, tot_s), "late": tot_l,
                      "late_pct": _pct(tot_l, tot_s), "early_leave": tot_e,
                      "early_leave_pct": _pct(tot_e, tot_s)},
            "late": sorted(late_rows, key=lambda r: -r["minutes_late"]),
            "absent": absent_rows,
            "early_leave": sorted(early_rows, key=lambda r: -r["minutes_early"]),
            "has_events": bool(events_all)}


# ═════════════════════════════════════════════════════════════
# 5. AGENT STATUS
# ═════════════════════════════════════════════════════════════

def agent_status_report(lobs, date_obj, user=None, sheet=None):
    now = _now()
    lob_by_emp = {}
    for lob in lobs:
        for e in _employees_for_lob(lob, sheet):
            lob_by_emp[str(e.get("employee_id"))] = lob
    events = get_agent_events(date_obj, user, sheet, employee_ext_ids=set(lob_by_emp))
    by_emp = defaultdict(list)
    for e in events:
        by_emp[str(e["employee_id"])].append(e)

    current_rows = []
    history = []
    status_counts = defaultdict(lambda: defaultdict(int))
    for ext, evs in by_emp.items():
        evs.sort(key=lambda x: x["start"])
        last = evs[-1]
        st = _parse_dt(last["start"])
        en = _parse_dt(last["end"]) if last.get("end") else None
        ref = en or (now if date_obj == now.date() else st)
        dur = int((ref - st).total_seconds()) if st and ref else 0
        lob = lob_by_emp.get(ext, "")
        current_rows.append({"employee": last["employee"], "employee_id": ext, "lob": lob,
                             "status": last["status"], "since": st.strftime("%H:%M") if st else "",
                             "duration_mins": dur // 60, "is_live": en is None})
        status_counts[lob][last["status"]] += 1
        for e in evs:
            s = _parse_dt(e["start"]); en2 = _parse_dt(e["end"]) if e.get("end") else None
            secs = int(((en2 or now) - s).total_seconds()) if s else 0
            history.append({"employee": e["employee"], "employee_id": ext, "lob": lob,
                            "status": e["status"], "start": s.strftime("%H:%M:%S") if s else "",
                            "end": en2.strftime("%H:%M:%S") if en2 else "",
                            "minutes": secs // 60, "seconds": secs})

    current_rows.sort(key=lambda r: (r["lob"], r["status"], r["employee"]))
    history.sort(key=lambda r: (r["employee"], r["start"]))
    summary = [{"lob": lob, "statuses": dict(c), "total": sum(c.values())}
               for lob, c in sorted(status_counts.items())]
    return {"date": date_obj.isoformat(), "as_of": now.strftime("%H:%M:%S"),
            "current": current_rows, "history": history, "summary": summary,
            "has_events": bool(events)}


# ═════════════════════════════════════════════════════════════
# 6. PRODUCTION EFFICIENCY
# ═════════════════════════════════════════════════════════════

def efficiency_report(lobs, date_obj, user=None, sheet=None):
    now = _now()
    events = get_agent_events(date_obj, user, sheet)
    by_emp = defaultdict(list)
    for e in events:
        by_emp[str(e["employee_id"])].append(e)

    agents = []
    lob_tot = defaultdict(lambda: {"agents": 0, "sched": 0.0, "paid": 0.0, "logged": 0.0, "prod": 0.0})
    for lob in lobs:
        for s in _shifts(lob, date_obj, user, sheet):
            ext = str(s.get("employee_id", ""))
            sched_hrs = float(s.get("hours") or 0)
            # Paid hours = scheduled minus unpaid segments (lunch by default)
            unpaid = 0.0
            for seg in s.get("segments") or []:
                if str(seg.get("type", "")).lower().startswith("lunch"):
                    unpaid += float(seg.get("duration_mins") or 0) / 60
            paid_hrs = max(0.0, sched_hrs - unpaid) if unpaid else sched_hrs
            logged = prod = 0.0
            for e in by_emp.get(ext, []):
                st = _parse_dt(e["start"]); en = _parse_dt(e["end"]) if e.get("end") else None
                if not st:
                    continue
                en = en or (now if date_obj == now.date() else st)
                hrs = max(0.0, (en - st).total_seconds() / 3600)
                if is_logged_in(e["status"]):
                    logged += hrs
                if is_productive(e["status"]):
                    prod += hrs
            eff = _pct(prod, paid_hrs) if paid_hrs else None
            agents.append({"employee": s.get("employee", ext), "employee_id": ext, "lob": lob,
                           "scheduled_hrs": round(sched_hrs, 2), "paid_hrs": round(paid_hrs, 2),
                           "logged_in_hrs": round(logged, 2), "productive_hrs": round(prod, 2),
                           "efficiency_pct": eff})
            t = lob_tot[lob]
            t["agents"] += 1; t["sched"] += sched_hrs; t["paid"] += paid_hrs
            t["logged"] += logged; t["prod"] += prod

    by_lob = [{"lob": lob, "agents": t["agents"], "scheduled_hrs": round(t["sched"], 1),
               "paid_hrs": round(t["paid"], 1), "logged_in_hrs": round(t["logged"], 1),
               "productive_hrs": round(t["prod"], 1), "efficiency_pct": _pct(t["prod"], t["paid"])}
              for lob, t in sorted(lob_tot.items())]
    tp = sum(t["paid"] for t in lob_tot.values()); tpr = sum(t["prod"] for t in lob_tot.values())
    total = {"lob": "All LOBs", "agents": sum(t["agents"] for t in lob_tot.values()),
             "scheduled_hrs": round(sum(t["sched"] for t in lob_tot.values()), 1),
             "paid_hrs": round(tp, 1),
             "logged_in_hrs": round(sum(t["logged"] for t in lob_tot.values()), 1),
             "productive_hrs": round(tpr, 1), "efficiency_pct": _pct(tpr, tp)}
    agents.sort(key=lambda a: (a["lob"], -(a["efficiency_pct"] or 0)))
    return {"date": date_obj.isoformat(), "by_lob": by_lob, "total": total,
            "agents": agents, "has_events": bool(events)}


# ═════════════════════════════════════════════════════════════
# 7. COMBINED DASHBOARD
# ═════════════════════════════════════════════════════════════

def combined_dashboard(lobs, date_obj, user=None, sheet=None):
    otf = forecast_otf_report(lobs, date_obj, user, sheet)
    absn = absenteeism_report(lobs, date_obj, user, sheet)
    stat = agent_status_report(lobs, date_obj, user, sheet)
    eff = efficiency_report(lobs, date_obj, user, sheet)

    eff_by_lob = {r["lob"]: r for r in eff["by_lob"]}
    abs_by_lob = {r["lob"]: r for r in absn["by_lob"]}
    lob_rows = []
    for r in otf["rows"]:
        a = abs_by_lob.get(r["lob"], {})
        e = eff_by_lob.get(r["lob"], {})
        lob_rows.append({
            "lob": r["lob"],
            "service_level": r["service_level"], "sl_target": r["sl_target"],
            "otf_pct": r["otf_pct"], "abandoned_pct": r["abandoned_pct"],
            "offered": r["actual_offered"], "forecast_up_to_now": r["forecast_up_to_now"],
            "scheduled": a.get("scheduled", 0), "absent": a.get("absent", 0),
            "absence_pct": a.get("absence_pct"), "late": a.get("late", 0),
            "efficiency_pct": e.get("efficiency_pct"),
        })

    # Actions: what needs attention right now
    actions = []
    for r in otf["rows"]:
        if r["service_level"] is not None and r["service_level"] < r["sl_target"]:
            actions.append({"level": "high" if r["service_level"] < r["sl_target"] - 10 else "medium",
                            "lob": r["lob"], "action": f"Service level {r['service_level']}% vs {r['sl_target']}% target",
                            "owner": "RTA", "status": "open"})
        if r["otf_pct"] is not None and r["otf_pct"] > 115:
            actions.append({"level": "medium", "lob": r["lob"],
                            "action": f"Volume {r['otf_pct']}% of forecast — consider overtime / skill borrowing",
                            "owner": "WFM", "status": "open"})
        if r["otf_pct"] is not None and r["otf_pct"] < 80 and r["forecast_up_to_now"] > 20:
            actions.append({"level": "low", "lob": r["lob"],
                            "action": f"Volume only {r['otf_pct']}% of forecast — offer VTO / training",
                            "owner": "WFM", "status": "open"})
        if r["abandoned_pct"] is not None and r["abandoned_pct"] > 5:
            actions.append({"level": "high", "lob": r["lob"],
                            "action": f"Abandon rate {r['abandoned_pct']}%", "owner": "RTA", "status": "open"})
    for a in absn["absent"]:
        actions.append({"level": "medium", "lob": a["lob"],
                        "action": f"{a['employee']} not logged in (scheduled {a['scheduled_from']})",
                        "owner": "Team Lead", "status": "open"})
    for a in absn["late"][:5]:
        actions.append({"level": "low", "lob": a["lob"],
                        "action": f"{a['employee']} started {a['minutes_late']} min late",
                        "owner": "Team Lead", "status": "open"})
    order = {"high": 0, "medium": 1, "low": 2}
    actions.sort(key=lambda x: (order[x["level"]], x["lob"]))

    return {"date": date_obj.isoformat(), "as_of": otf["as_of"],
            "lobs": lob_rows, "totals": otf["total"], "actions": actions,
            "absent": absn["absent"], "late": absn["late"], "early_leave": absn["early_leave"],
            "status_summary": stat["summary"], "efficiency_total": eff["total"],
            "has_actuals": any(r["has_actuals"] for r in otf["rows"]),
            "has_events": absn["has_events"]}
