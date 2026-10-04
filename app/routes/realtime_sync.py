"""
Real-Time Sync — Pull Call Potential Google Sheet data every 5 minutes.
=====================================================================

The Call Potential sheet contains two key tabs:
  • Call volume data  → upserts into interval_actuals
  • Agent activity    → upserts into agent_status_events

The sheet only retains ~1–1.5 days of data, so we persist everything in
Postgres and it becomes the historical source of truth.

Configuration (env vars or AppSettings in DB):
  CALLPOTENTIAL_SHEET_KEY  — Google Sheet key
  SERVICE_ACCOUNT_FILE     — path to service account JSON (fallback)
  google_service_account_json — AppSetting with JSON creds (preferred)
"""

import json
import logging
import os
from datetime import datetime, timedelta

from flask import Blueprint, jsonify, request

from app.auth import get_current_user
from app.models import (
    AgentStatusEvent,
    AppSetting,
    Employee,
    IntervalActual,
    LobMapping,
    PlanningUnit,
    db,
)

log = logging.getLogger("serevo.realtime_sync")

realtime_sync_bp = Blueprint("realtime_sync", __name__, url_prefix="/sync")


# ═══════════════════════════════════════════════════════════════
# GOOGLE SHEET CONNECTION
# ═══════════════════════════════════════════════════════════════

def _open_callpotential_sheet():
    """
    Open the Call Potential Google Sheet.
    Returns (spreadsheet, error_string).
    """
    try:
        import gspread
        from oauth2client.service_account import ServiceAccountCredentials

        scope = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive",
        ]

        sheet_key = os.environ.get("CALLPOTENTIAL_SHEET_KEY", "")
        sa_file = os.environ.get("SERVICE_ACCOUNT_FILE", "service_account.json")
        sa_json = ""

        if not sheet_key:
            try:
                sheet_key = AppSetting.get("callpotential_sheet_key", "")
            except Exception:
                log.warning("Failed to read callpotential_sheet_key from DB")

        if not sheet_key:
            return None, (
                "No Call Potential sheet key configured. "
                "Set CALLPOTENTIAL_SHEET_KEY in environment or in Settings → Connections."
            )

        # Prefer JSON creds from DB, then file
        try:
            sa_json = AppSetting.get("google_service_account_json", "")
        except Exception:
            pass

        if sa_json:
            creds_dict = json.loads(sa_json)
            creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        elif sa_file and os.path.exists(sa_file):
            creds = ServiceAccountCredentials.from_json_keyfile_name(sa_file, scope)
        else:
            return None, "No service account credentials configured"

        client = gspread.authorize(creds)
        spreadsheet = client.open_by_key(sheet_key)
        return spreadsheet, None

    except Exception as e:
        return None, f"Could not open Call Potential sheet: {e}"


# ═══════════════════════════════════════════════════════════════
# CALL VOLUME SYNC  (→ interval_actuals)
# ═══════════════════════════════════════════════════════════════

# Common column name aliases for Call Potential sheets
_VOLUME_COL_ALIASES = {
    # date/time
    "date": "date", "interval date": "date",
    "time": "time", "interval": "time", "interval time": "time",
    "start time": "time", "interval start": "time",
    # LOB / queue
    "lob": "lob", "queue": "lob", "workload": "lob",
    "skill": "lob", "queue name": "lob", "skill name": "lob",
    "line of business": "lob", "program": "lob",
    # metrics
    "offered": "offered", "calls offered": "offered", "total offered": "offered",
    "answered": "answered", "calls answered": "answered", "total answered": "answered",
    "ans w/in sl": "answered_within", "answered within sl": "answered_within",
    "answered within": "answered_within", "sl met": "answered_within",
    "abandoned": "abandoned", "calls abandoned": "abandoned", "total abandoned": "abandoned",
    "abn": "abandoned",
    "rolled": "rolled", "overflow": "rolled", "overflowed": "rolled",
    "asa": "asa", "avg speed answer": "asa", "average speed of answer": "asa",
    "asa (sec)": "asa", "asa (seconds)": "asa", "asa_secs": "asa",
    "aht": "aht", "avg handle time": "aht", "average handle time": "aht",
    "aht (sec)": "aht", "aht (seconds)": "aht", "aht_secs": "aht",
    "max queued": "max_queued", "max queue": "max_queued",
}

_AGENT_COL_ALIASES = {
    "agent": "agent", "agent name": "agent", "name": "agent",
    "employee": "agent", "rep": "agent", "representative": "agent",
    "agent id": "agent_id", "employee id": "agent_id", "id": "agent_id",
    "ext": "agent_id", "extension": "agent_id",
    "status": "status", "state": "status", "agent status": "status",
    "agent state": "status", "current status": "status",
    "start": "start", "start time": "start", "started": "start",
    "login time": "start", "timestamp": "start",
    "end": "end", "end time": "end", "ended": "end",
    "duration": "duration", "time in status": "duration",
    "duration (sec)": "duration", "seconds": "duration",
    "date": "date",
}


def _match_columns(headers, alias_map):
    """Map sheet headers to canonical field names using alias dict."""
    mapping = {}
    for idx, h in enumerate(headers):
        key = h.strip().lower()
        if key in alias_map:
            mapping[alias_map[key]] = idx
    return mapping


def _resolve_lob_to_pu(lob_name):
    """Resolve a LOB name to a PlanningUnit, using LobMapping if available."""
    # Check explicit mapping first
    mapping = LobMapping.query.filter_by(source_name=lob_name).first()
    pu_name = mapping.planning_unit_name if mapping else lob_name

    pu = PlanningUnit.query.filter(
        db.func.lower(PlanningUnit.name) == pu_name.lower()
    ).first()
    return pu


def _parse_timestamp(date_str, time_str):
    """Parse date + time strings into a datetime. Handles common formats."""
    date_str = str(date_str).strip()
    time_str = str(time_str).strip()

    # Try combined first
    for fmt in ("%m/%d/%Y %H:%M", "%m/%d/%Y %I:%M %p",
                "%Y-%m-%d %H:%M", "%m-%d-%Y %H:%M",
                "%m/%d/%y %H:%M", "%m/%d/%y %I:%M %p"):
        try:
            return datetime.strptime(f"{date_str} {time_str}", fmt)
        except (ValueError, TypeError):
            continue

    # Parse date and time separately
    dt = None
    for dfmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y", "%m/%d/%y"):
        try:
            dt = datetime.strptime(date_str, dfmt)
            break
        except (ValueError, TypeError):
            continue

    if dt is None:
        raise ValueError(f"Cannot parse date: {date_str}")

    for tfmt in ("%H:%M", "%I:%M %p", "%H:%M:%S", "%I:%M:%S %p"):
        try:
            t = datetime.strptime(time_str, tfmt).time()
            return datetime.combine(dt.date(), t)
        except (ValueError, TypeError):
            continue

    raise ValueError(f"Cannot parse time: {time_str}")


def _parse_datetime(val):
    """Parse a single datetime string."""
    val = str(val).strip()
    for fmt in ("%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%m/%d/%Y %I:%M:%S %p",
                "%m/%d/%Y %I:%M %p", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
                "%m/%d/%y %H:%M:%S", "%m/%d/%y %H:%M"):
        try:
            return datetime.strptime(val, fmt)
        except (ValueError, TypeError):
            continue
    raise ValueError(f"Cannot parse datetime: {val}")


def _safe_int(val):
    try:
        if val is None or str(val).strip() == "":
            return 0
        return int(float(str(val).strip().replace(",", "")))
    except (ValueError, TypeError):
        return 0


def _safe_float(val):
    try:
        if val is None or str(val).strip() == "":
            return None
        return float(str(val).strip().replace(",", ""))
    except (ValueError, TypeError):
        return None


def sync_call_volume(spreadsheet):
    """
    Read call volume tab and upsert into interval_actuals.
    Returns (upserted, skipped, errors).
    """
    # Try common tab names
    ws = None
    for name in ("Call Volume", "Calls", "Volume", "ACTUALS RAW",
                 "Interval Data", "ACD Data", "call_volume"):
        try:
            ws = spreadsheet.worksheet(name)
            break
        except Exception:
            continue

    if ws is None:
        # Fall back to first sheet
        ws = spreadsheet.sheet1
        log.info("No recognized call volume tab — using first sheet")

    rows = ws.get_all_values()
    if not rows:
        return 0, 0, ["Call volume tab is empty"]

    headers = rows[0]
    col_map = _match_columns(headers, _VOLUME_COL_ALIASES)

    required = {"date", "time", "lob"}
    missing = required - set(col_map.keys())
    if missing:
        return 0, 0, [f"Missing required columns: {', '.join(missing)}. Found: {headers}"]

    upserted = 0
    skipped = 0
    errors = []

    for i, row in enumerate(rows[1:], start=2):
        try:
            lob_name = row[col_map["lob"]].strip()
            if not lob_name:
                skipped += 1
                continue

            pu = _resolve_lob_to_pu(lob_name)
            if not pu:
                skipped += 1
                continue

            ts = _parse_timestamp(
                row[col_map["date"]],
                row[col_map["time"]],
            )

            # Upsert: check for existing record
            existing = IntervalActual.query.filter_by(
                planning_unit_id=pu.id, timestamp=ts
            ).first()

            vals = dict(
                offered=_safe_int(row[col_map["offered"]]) if "offered" in col_map else 0,
                answered=_safe_int(row[col_map["answered"]]) if "answered" in col_map else 0,
                answered_within=_safe_int(row[col_map["answered_within"]]) if "answered_within" in col_map else 0,
                abandoned=_safe_int(row[col_map["abandoned"]]) if "abandoned" in col_map else 0,
                rolled=_safe_int(row[col_map["rolled"]]) if "rolled" in col_map else 0,
                asa_secs=_safe_float(row[col_map["asa"]]) if "asa" in col_map else None,
                aht_secs=_safe_float(row[col_map["aht"]]) if "aht" in col_map else None,
                max_queued=_safe_int(row[col_map["max_queued"]]) if "max_queued" in col_map else None,
                source="sheet",
            )

            if existing:
                for k, v in vals.items():
                    setattr(existing, k, v)
                existing.uploaded_at = datetime.utcnow()
            else:
                rec = IntervalActual(
                    planning_unit_id=pu.id,
                    timestamp=ts,
                    **vals,
                )
                db.session.add(rec)

            upserted += 1

        except Exception as e:
            errors.append(f"Row {i}: {e}")
            if len(errors) > 50:
                errors.append("... (truncated)")
                break

    db.session.commit()
    return upserted, skipped, errors


# ═══════════════════════════════════════════════════════════════
# AGENT ACTIVITY SYNC  (→ agent_status_events)
# ═══════════════════════════════════════════════════════════════

def sync_agent_activity(spreadsheet):
    """
    Read agent activity tab and upsert into agent_status_events.
    Returns (upserted, skipped, errors).
    """
    ws = None
    for name in ("Agent Activity", "Agent Status", "Agent States",
                 "Agents", "agent_activity", "Status Events",
                 "Real Time", "RealTime"):
        try:
            ws = spreadsheet.worksheet(name)
            break
        except Exception:
            continue

    if ws is None:
        # Try second sheet
        sheets = spreadsheet.worksheets()
        if len(sheets) >= 2:
            ws = sheets[1]
            log.info("No recognized agent activity tab — using second sheet")
        else:
            return 0, 0, ["No agent activity tab found"]

    rows = ws.get_all_values()
    if not rows:
        return 0, 0, ["Agent activity tab is empty"]

    headers = rows[0]
    col_map = _match_columns(headers, _AGENT_COL_ALIASES)

    # Need at least agent identifier + status + start time
    has_agent = "agent" in col_map or "agent_id" in col_map
    if not has_agent or "status" not in col_map or "start" not in col_map:
        return 0, 0, [
            f"Missing required columns (need agent/agent_id, status, start). Found: {headers}"
        ]

    upserted = 0
    skipped = 0
    errors = []

    for i, row in enumerate(rows[1:], start=2):
        try:
            status = row[col_map["status"]].strip()
            if not status:
                skipped += 1
                continue

            # Find employee by agent_id (external_id_2) or name
            emp = None
            if "agent_id" in col_map:
                agent_id = row[col_map["agent_id"]].strip()
                if agent_id:
                    emp = Employee.query.filter(
                        (Employee.external_id_2 == agent_id) |
                        (Employee.employee_id == agent_id)
                    ).first()

            if not emp and "agent" in col_map:
                agent_name = row[col_map["agent"]].strip()
                if agent_name:
                    # Try exact match on full name
                    parts = agent_name.split()
                    if len(parts) >= 2:
                        emp = Employee.query.filter(
                            db.func.lower(Employee.first_name) == parts[0].lower(),
                            db.func.lower(Employee.last_name) == parts[-1].lower(),
                        ).first()

            if not emp:
                skipped += 1
                continue

            # Parse start timestamp
            start_str = row[col_map["start"]].strip()
            if not start_str:
                skipped += 1
                continue

            # If there's a separate date column, combine
            if "date" in col_map:
                date_str = row[col_map["date"]].strip()
                if date_str:
                    start_ts = _parse_timestamp(date_str, start_str)
                else:
                    start_ts = _parse_datetime(start_str)
            else:
                start_ts = _parse_datetime(start_str)

            # End time
            end_ts = None
            if "end" in col_map:
                end_str = row[col_map["end"]].strip()
                if end_str:
                    if "date" in col_map:
                        date_str = row[col_map["date"]].strip()
                        if date_str:
                            try:
                                end_ts = _parse_timestamp(date_str, end_str)
                            except ValueError:
                                end_ts = _parse_datetime(end_str)
                        else:
                            end_ts = _parse_datetime(end_str)
                    else:
                        end_ts = _parse_datetime(end_str)

            # If no end but duration provided, compute it
            if end_ts is None and "duration" in col_map:
                dur_val = _safe_float(row[col_map["duration"]])
                if dur_val is not None and dur_val > 0:
                    end_ts = start_ts + timedelta(seconds=dur_val)

            # Upsert: match on employee + start_ts
            existing = AgentStatusEvent.query.filter_by(
                employee_id=emp.id, start_ts=start_ts
            ).first()

            if existing:
                existing.status = status
                existing.end_ts = end_ts
                existing.source = "sheet"
                existing.uploaded_at = datetime.utcnow()
            else:
                evt = AgentStatusEvent(
                    employee_id=emp.id,
                    status=status,
                    start_ts=start_ts,
                    end_ts=end_ts,
                    source="sheet",
                )
                db.session.add(evt)

            upserted += 1

        except Exception as e:
            errors.append(f"Row {i}: {e}")
            if len(errors) > 50:
                errors.append("... (truncated)")
                break

    db.session.commit()
    return upserted, skipped, errors


# ═══════════════════════════════════════════════════════════════
# COMBINED SYNC FUNCTION (called by scheduler + manual endpoint)
# ═══════════════════════════════════════════════════════════════

def run_sync(app=None):
    """
    Execute a full Call Potential sync cycle.
    Can be called by the scheduler (with app context) or from a route.
    Returns dict with results.
    """
    from flask import current_app

    if app is None:
        app = current_app._get_current_object()

    with app.app_context():
        spreadsheet, err = _open_callpotential_sheet()
        if err:
            log.warning(f"Call Potential sync failed: {err}")
            return {"ok": False, "error": err}

        results = {}

        # Sync call volume
        try:
            vol_up, vol_skip, vol_err = sync_call_volume(spreadsheet)
            results["call_volume"] = {
                "upserted": vol_up,
                "skipped": vol_skip,
                "errors": vol_err[:10],
            }
            log.info(f"Call volume sync: {vol_up} upserted, {vol_skip} skipped, {len(vol_err)} errors")
        except Exception as e:
            results["call_volume"] = {"error": str(e)}
            log.error(f"Call volume sync error: {e}", exc_info=True)

        # Sync agent activity
        try:
            act_up, act_skip, act_err = sync_agent_activity(spreadsheet)
            results["agent_activity"] = {
                "upserted": act_up,
                "skipped": act_skip,
                "errors": act_err[:10],
            }
            log.info(f"Agent activity sync: {act_up} upserted, {act_skip} skipped, {len(act_err)} errors")
        except Exception as e:
            results["agent_activity"] = {"error": str(e)}
            log.error(f"Agent activity sync error: {e}", exc_info=True)

        # Store last sync time
        try:
            AppSetting.set("callpotential_last_sync", datetime.utcnow().isoformat())
        except Exception:
            pass

        results["ok"] = True
        return results


# ═══════════════════════════════════════════════════════════════
# ROUTES
# ═══════════════════════════════════════════════════════════════

def _require_admin():
    user = get_current_user()
    if not user or user.get("role") != "admin":
        return jsonify({"error": "Unauthorized"}), 403
    return None


@realtime_sync_bp.route("/realtime", methods=["POST"])
def trigger_sync():
    """Manual trigger for the Call Potential sync.
    Can be called by admins or by the scheduler endpoint."""
    auth_err = _require_admin()
    if auth_err:
        # Also allow scheduler secret key
        sched_key = request.headers.get("X-Scheduler-Key", "")
        expected = os.environ.get("SCHEDULER_SECRET", "")
        if not expected or sched_key != expected:
            return auth_err

    results = run_sync()
    return jsonify(results)


@realtime_sync_bp.route("/realtime/status", methods=["GET"])
def sync_status():
    """Get the current sync configuration and last sync time."""
    auth_err = _require_admin()
    if auth_err:
        return auth_err

    sheet_key = os.environ.get("CALLPOTENTIAL_SHEET_KEY", "")
    if not sheet_key:
        try:
            sheet_key = AppSetting.get("callpotential_sheet_key", "")
        except Exception:
            pass

    last_sync = ""
    try:
        last_sync = AppSetting.get("callpotential_last_sync", "")
    except Exception:
        pass

    sync_enabled = os.environ.get("CALLPOTENTIAL_SYNC_ENABLED", "true").lower() == "true"

    return jsonify({
        "configured": bool(sheet_key),
        "sheet_key": sheet_key[:8] + "..." if sheet_key else "",
        "last_sync": last_sync,
        "sync_enabled": sync_enabled,
        "interval_minutes": 5,
    })


@realtime_sync_bp.route("/realtime/configure", methods=["POST"])
def configure_sync():
    """Save Call Potential sheet key to AppSettings."""
    auth_err = _require_admin()
    if auth_err:
        return auth_err

    data = request.get_json() or {}
    sheet_key = data.get("sheet_key", "").strip()
    if not sheet_key:
        return jsonify({"error": "Sheet key is required"}), 400

    AppSetting.set("callpotential_sheet_key", sheet_key)
    db.session.commit()

    # Test connection
    spreadsheet, err = _open_callpotential_sheet()
    if err:
        return jsonify({"ok": False, "error": err}), 400

    # List available tabs for the user
    tabs = [ws.title for ws in spreadsheet.worksheets()]

    return jsonify({
        "ok": True,
        "message": "Connected successfully",
        "tabs": tabs,
    })
