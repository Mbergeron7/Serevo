"""
realtime/actuals.py — Access layer for ACD actuals
==================================================
Interval call statistics and agent status events can come from:
  1. Demo generator (demo users)
  2. Database tables (CSV upload / API connector)
  3. Google Sheet tabs 'ACTUALS RAW' and 'AGENT STATUS RAW'

All functions return plain dicts so report builders never touch the ORM.

Interval actual dict:
  {time "HH:MM", timestamp "YYYY-MM-DD HH:MM", offered, answered,
   answered_within, abandoned, rolled, asa, aht, max_queued}

Agent event dict:
  {employee, employee_id, status, start "YYYY-MM-DD HH:MM:SS", end|None}
"""

import datetime
import logging

log = logging.getLogger("serevo.actuals")

TAB_ACTUALS = "ACTUALS RAW"
TAB_AGENT_STATUS = "AGENT STATUS RAW"

# Statuses that count as productive / logged in. Compared lower-case.
PRODUCTIVE_STATUSES = {"on queue", "available", "interacting", "on call", "acw",
                       "after call work", "idle", "ready", "busy", "talking", "wrap up"}
LOGGED_IN_STATUSES = PRODUCTIVE_STATUSES | {"break", "lunch", "meeting", "training",
                                            "coaching", "meal", "not responding", "away"}
OFFLINE_STATUSES = {"offline", "logged out", "logout", "off queue"}


def _parse_ts(v):
    s = str(v or "").strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M"):
        try:
            return datetime.datetime.strptime(s[:19], fmt)
        except ValueError:
            continue
    return None


def _num(v, default=None):
    try:
        s = str(v).replace(",", "").strip()
        return float(s) if s not in ("", "None") else default
    except (TypeError, ValueError):
        return default


def is_productive(status):
    return str(status or "").strip().lower() in PRODUCTIVE_STATUSES


def is_logged_in(status):
    return str(status or "").strip().lower() in LOGGED_IN_STATUSES


# ── Interval actuals ──────────────────────────────────────────

def get_interval_actuals(lob, date_obj, user=None, sheet=None):
    """Return list of interval actual dicts for one LOB + date (sorted)."""
    if user and user.get("is_demo"):
        from app.demo_data import get_demo_interval_actuals
        return get_demo_interval_actuals(lob, date_obj)

    rows = _db_interval_actuals(lob, date_obj)
    if rows is None or not rows:
        sheet_rows = _sheet_interval_actuals(lob, date_obj, sheet)
        if sheet_rows:
            rows = sheet_rows
    return sorted(rows or [], key=lambda r: r["time"])


def _db_interval_actuals(lob, date_obj):
    try:
        from app.models import db, IntervalActual, PlanningUnit
        pu = PlanningUnit.query.filter(db.func.lower(PlanningUnit.name) == lob.lower()).first()
        if not pu:
            return []
        start = datetime.datetime.combine(date_obj, datetime.time.min)
        end = start + datetime.timedelta(days=1)
        rows = IntervalActual.query.filter(
            IntervalActual.planning_unit_id == pu.id,
            IntervalActual.timestamp >= start,
            IntervalActual.timestamp < end,
        ).order_by(IntervalActual.timestamp).all()
        return [r.to_dict() for r in rows]
    except Exception as e:
        try:
            from app import db as _db
            _db.session.rollback()
        except Exception:
            pass
        log.debug(f"DB actuals unavailable: {e}")
        return None


def _sheet_records(tab, sheet=None):
    try:
        if sheet is None:
            from app.data_source import _open_capacity_sheet
            sheet, err = _open_capacity_sheet()
            if err:
                return []
        return sheet.worksheet(tab).get_all_records()
    except Exception as e:
        log.debug(f"Sheet tab {tab} unavailable: {e}")
        return []


def _sheet_interval_actuals(lob, date_obj, sheet=None):
    target = date_obj.strftime("%Y-%m-%d")
    out = []
    for r in _sheet_records(TAB_ACTUALS, sheet):
        if str(r.get("LOB", "")).strip().lower() != lob.lower():
            continue
        ts = _parse_ts(r.get("Timestamp"))
        if not ts or ts.strftime("%Y-%m-%d") != target:
            continue
        answered = int(_num(r.get("Answered"), 0) or 0)
        aw = _num(r.get("Answered Within"))
        out.append({
            "time": ts.strftime("%H:%M"),
            "timestamp": ts.strftime("%Y-%m-%d %H:%M"),
            "offered": int(_num(r.get("Offered"), 0) or 0),
            "answered": answered,
            "answered_within": int(aw) if aw is not None else answered,
            "abandoned": int(_num(r.get("Abandoned"), 0) or 0),
            "rolled": int(_num(r.get("Rolled"), 0) or 0),
            "asa": _num(r.get("ASA")),
            "aht": _num(r.get("AHT")),
            "max_queued": (int(_num(r.get("Max Queued"))) if _num(r.get("Max Queued")) is not None else None),
        })
    return out


# ── Agent status events ───────────────────────────────────────

def get_agent_events(date_obj, user=None, sheet=None, employee_ext_ids=None):
    """Return agent status events overlapping the given date.
    employee_ext_ids: optional set of external IDs to restrict to."""
    if user and user.get("is_demo"):
        from app.demo_data import get_demo_agent_events
        events = get_demo_agent_events(date_obj)
    else:
        events = _db_agent_events(date_obj)
        if not events:
            events = _sheet_agent_events(date_obj, sheet)
    if employee_ext_ids is not None:
        ids = {str(i) for i in employee_ext_ids}
        events = [e for e in events if str(e["employee_id"]) in ids]
    return sorted(events, key=lambda e: (e["employee"], e["start"]))


def _db_agent_events(date_obj):
    try:
        from app.models import AgentStatusEvent
        start = datetime.datetime.combine(date_obj, datetime.time.min)
        end = start + datetime.timedelta(days=1)
        rows = AgentStatusEvent.query.filter(
            AgentStatusEvent.start_ts < end,
            (AgentStatusEvent.end_ts.is_(None)) | (AgentStatusEvent.end_ts >= start),
        ).order_by(AgentStatusEvent.start_ts).all()
        return [r.to_dict() for r in rows]
    except Exception as e:
        try:
            from app import db as _db
            _db.session.rollback()
        except Exception:
            pass
        log.debug(f"DB agent events unavailable: {e}")
        return []


def _sheet_agent_events(date_obj, sheet=None):
    target = date_obj.strftime("%Y-%m-%d")
    out = []
    name_by_id = {}
    try:
        from app.people.manager import get_employees
        emps, _ = get_employees(sheet)
        for e in emps or []:
            name_by_id[str(e.get("Employee ID", ""))] = f"{e.get('First Name','')} {e.get('Last Name','')}".strip()
    except Exception:
        pass
    for r in _sheet_records(TAB_AGENT_STATUS, sheet):
        start = _parse_ts(r.get("Start"))
        if not start:
            continue
        end = _parse_ts(r.get("End"))
        if start.strftime("%Y-%m-%d") != target and not (end and end.strftime("%Y-%m-%d") == target):
            continue
        ext = str(r.get("Employee ID", "")).strip()
        out.append({
            "employee": r.get("Employee") or name_by_id.get(ext, ext),
            "employee_id": ext,
            "status": str(r.get("Status", "")).strip(),
            "start": start.strftime("%Y-%m-%d %H:%M:%S"),
            "end": end.strftime("%Y-%m-%d %H:%M:%S") if end else None,
        })
    return out
