"""
Real-Time Sync — Pull Google Sheet data every 5 minutes.
=========================================================

Two separate Google Sheets feed Serevo:
  • Call volume sheet  → upserts into interval_actuals
  • Agent status sheet → upserts into agent_status_events

The sheets only retain ~1–1.5 days of data, so we persist everything in
Postgres and it becomes the historical source of truth.

Configuration (AppSettings in DB):
  realtime_calls_sheet_key   — Google Sheet key for call volume
  realtime_agents_sheet_key  — Google Sheet key for agent status
  google_service_account_json — JSON creds for service account
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
    CallRoute,
    DataFeed,
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

def _get_gspread_client(sa_json_override=None):
    """
    Build an authorized gspread client from stored credentials.
    If sa_json_override is provided, use it directly (per-feed creds).
    Otherwise fall back to global AppSetting, then env file.
    Returns (client, error_string).
    """
    try:
        import gspread
        from oauth2client.service_account import ServiceAccountCredentials

        scope = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive",
        ]

        sa_json = sa_json_override or ""

        if not sa_json:
            sa_file = os.environ.get("SERVICE_ACCOUNT_FILE", "service_account.json")
            # Prefer JSON creds from DB, then file
            try:
                sa_json = AppSetting.get("google_service_account_json", "")
            except Exception:
                pass

            if sa_json:
                pass  # use it below
            elif sa_file and os.path.exists(sa_file):
                creds = ServiceAccountCredentials.from_json_keyfile_name(sa_file, scope)
                client = gspread.authorize(creds)
                return client, None
            else:
                return None, "No service account credentials configured"

        creds_dict = json.loads(sa_json)
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)
        return client, None

    except Exception as e:
        return None, f"Could not authorize Google Sheets: {e}"


def _open_sheet(key_name, env_fallback=None, sheet_key_override=None, sa_json_override=None):
    """
    Open a Google Sheet by its AppSetting key name, or by direct key override.
    Also checks env_fallback for backwards compat.
    If sa_json_override is provided, use it for this sheet's credentials.
    Returns (spreadsheet, error_string).
    """
    sheet_key = sheet_key_override or ""

    if not sheet_key:
        # Check env first (backwards compat)
        if env_fallback:
            sheet_key = os.environ.get(env_fallback, "")

    if not sheet_key and key_name:
        try:
            sheet_key = AppSetting.get(key_name, "")
        except Exception:
            log.warning(f"Failed to read {key_name} from DB")

    if not sheet_key:
        return None, f"No sheet key configured."

    client, err = _get_gspread_client(sa_json_override=sa_json_override)
    if err:
        return None, err

    try:
        spreadsheet = client.open_by_key(sheet_key)
        return spreadsheet, None
    except Exception as e:
        return None, f"Could not open sheet {sheet_key[:8]}...: {e}"


# ═══════════════════════════════════════════════════════════════
# CALL VOLUME SYNC  (→ interval_actuals)
# ═══════════════════════════════════════════════════════════════

# Common column name aliases
_VOLUME_COL_ALIASES = {
    # date/time
    "date": "date", "interval date": "date",
    "time": "time", "interval": "time", "interval time": "time",
    "start time": "time", "interval start": "time",
    # LOB / queue
    "lob": "lob", "queue": "lob", "workload": "lob",
    "skill": "lob", "queue name": "lob", "skill name": "lob",
    "line of business": "lob", "program": "lob",
    "route": "route_id", "route id": "route_id", "route_id": "route_id",
    "routeid": "route_id", "queue id": "route_id", "queue_id": "route_id",
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


def _match_columns(headers, alias_map, custom_mapping=None):
    """Map sheet headers to canonical field names.

    If *custom_mapping* is provided (a dict of canonical_field → sheet_column_name),
    it is tried first.  The alias_map is used as a fallback for any canonical field
    not covered by the custom mapping.
    """
    mapping = {}

    # Build a quick header-name → index lookup
    header_idx = {}
    for idx, h in enumerate(headers):
        header_idx[h.strip().lower()] = idx
        header_idx[h.strip()] = idx          # case-sensitive fallback

    # 1) Apply custom mapping first
    if custom_mapping:
        for canonical, sheet_col in custom_mapping.items():
            if not sheet_col:
                continue
            # Try exact match first, then case-insensitive
            if sheet_col in header_idx:
                mapping[canonical] = header_idx[sheet_col]
            elif sheet_col.lower() in header_idx:
                mapping[canonical] = header_idx[sheet_col.lower()]

    # 2) Fill in gaps from alias_map
    for idx, h in enumerate(headers):
        key = h.strip().lower()
        if key in alias_map:
            canonical = alias_map[key]
            if canonical not in mapping:
                mapping[canonical] = idx

    return mapping


def _resolve_route_to_pu(route_id):
    """Resolve a route ID to a PlanningUnit via the call_routes table."""
    cr = CallRoute.query.filter_by(route_id=str(route_id).strip(), is_active=True).first()
    if cr:
        return cr.planning_unit
    return None


def _resolve_lob_to_pu(lob_name):
    """Resolve a LOB name to a PlanningUnit, using CallRoute name, LobMapping, or direct match."""
    # Check call route by name first
    cr = CallRoute.query.filter(
        db.func.lower(CallRoute.name) == lob_name.lower(),
        CallRoute.is_active == True,
    ).first()
    if cr:
        return cr.planning_unit

    # Check explicit LOB mapping
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


def sync_call_volume(spreadsheet, tab_name=None, custom_mapping=None):
    """
    Read call volume tab and upsert into interval_actuals.
    Returns (upserted, skipped, errors).
    """
    ws = None
    # If a specific tab is configured, use it
    if tab_name:
        try:
            ws = spreadsheet.worksheet(tab_name)
        except Exception:
            return 0, 0, [f"Tab '{tab_name}' not found in sheet"]

    if ws is None:
        # Try common tab names
        for name in ("Call Volume", "Calls", "Volume", "ACTUALS RAW",
                     "Interval Data", "ACD Data", "call_volume"):
            try:
                ws = spreadsheet.worksheet(name)
                break
            except Exception:
                continue

    if ws is None:
        ws = spreadsheet.sheet1
        log.info("No recognized call volume tab — using first sheet")

    rows = ws.get_all_values()
    if not rows:
        return 0, 0, ["Call volume tab is empty"]

    headers = rows[0]
    col_map = _match_columns(headers, _VOLUME_COL_ALIASES, custom_mapping=custom_mapping)

    # Need date + time + either route_id or lob
    has_identifier = "route_id" in col_map or "lob" in col_map
    has_time = "date" in col_map and "time" in col_map
    if not has_identifier:
        return 0, 0, [f"Missing route/LOB column. Found: {headers}"]
    if not has_time:
        return 0, 0, [f"Missing date/time columns. Found: {headers}"]

    upserted = 0
    skipped = 0
    errors = []

    for i, row in enumerate(rows[1:], start=2):
        try:
            # Resolve to planning unit: try route_id first, then LOB name
            pu = None
            if "route_id" in col_map:
                rid = row[col_map["route_id"]].strip()
                if rid:
                    pu = _resolve_route_to_pu(rid)

            if not pu and "lob" in col_map:
                lob_name = row[col_map["lob"]].strip()
                if lob_name:
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

def sync_agent_activity(spreadsheet, tab_name=None, custom_mapping=None):
    """
    Read agent activity tab and upsert into agent_status_events.
    Returns (upserted, skipped, errors).
    """
    ws = None
    if tab_name:
        try:
            ws = spreadsheet.worksheet(tab_name)
        except Exception:
            return 0, 0, [f"Tab '{tab_name}' not found in sheet"]

    if ws is None:
        for name in ("Agent Activity", "Agent Status", "Agent States",
                     "Agents", "agent_activity", "Status Events",
                     "Real Time", "RealTime"):
            try:
                ws = spreadsheet.worksheet(name)
                break
            except Exception:
                continue

    if ws is None:
        ws = spreadsheet.sheet1
        log.info("No recognized agent activity tab — using first sheet")

    rows = ws.get_all_values()
    if not rows:
        return 0, 0, ["Agent activity tab is empty"]

    headers = rows[0]
    col_map = _match_columns(headers, _AGENT_COL_ALIASES, custom_mapping=custom_mapping)

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
    Execute a full sync cycle — iterates all active DataFeed records,
    plus legacy AppSetting keys for backwards compatibility.
    Can be called by the scheduler (with app context) or from a route.
    Returns dict with results.
    """
    from flask import current_app

    if app is None:
        app = current_app._get_current_object()

    with app.app_context():
        results = {"feeds": []}

        # ── Sync all active DataFeed records ──
        try:
            feeds = DataFeed.query.filter_by(is_active=True).all()
        except Exception:
            feeds = []
            log.warning("DataFeed table not available yet — falling back to AppSettings only")

        for feed in feeds:
            feed_result = {"id": feed.id, "name": feed.name, "feed_type": feed.feed_type}
            try:
                if feed.source_type == "google_sheet" and feed.sheet_key:
                    spreadsheet, err = _open_sheet(None, sheet_key_override=feed.sheet_key,
                                                    sa_json_override=feed.service_account_json or None)
                    if err:
                        feed.last_status = "error"
                        feed.last_error = err
                        feed.last_sync_at = datetime.utcnow()
                        feed_result["error"] = err
                        log.warning(f"Feed '{feed.name}': {err}")
                    else:
                        # Parse custom column mapping if set
                        custom_map = None
                        if feed.column_mapping:
                            try:
                                custom_map = json.loads(feed.column_mapping)
                            except (json.JSONDecodeError, ValueError):
                                pass

                        if feed.feed_type == "call_volume":
                            up, skip, errs = sync_call_volume(spreadsheet, tab_name=feed.sheet_tab, custom_mapping=custom_map)
                        else:
                            up, skip, errs = sync_agent_activity(spreadsheet, tab_name=feed.sheet_tab, custom_mapping=custom_map)

                        feed.last_upserted = up
                        feed.last_skipped = skip
                        feed.last_status = "error" if errs else "ok"
                        feed.last_error = "; ".join(errs[:3]) if errs else ""
                        feed.last_sync_at = datetime.utcnow()
                        feed_result.update({"upserted": up, "skipped": skip, "errors": errs[:5]})
                        log.info(f"Feed '{feed.name}': {up} upserted, {skip} skipped, {len(errs)} errors")
                else:
                    feed.last_status = "error"
                    feed.last_error = "No sheet key or API not yet supported"
                    feed.last_sync_at = datetime.utcnow()
                    feed_result["error"] = feed.last_error
            except Exception as e:
                feed.last_status = "error"
                feed.last_error = str(e)[:500]
                feed.last_sync_at = datetime.utcnow()
                feed_result["error"] = str(e)
                log.error(f"Feed '{feed.name}' error: {e}", exc_info=True)

            results["feeds"].append(feed_result)

        # ── Legacy: AppSetting-based sheets (backwards compat) ──
        # Only sync these if no DataFeed records exist for the same type
        feed_types_covered = {f.feed_type for f in feeds}

        if "call_volume" not in feed_types_covered:
            calls_sheet, err = _open_sheet(
                "realtime_calls_sheet_key",
                env_fallback="CALLPOTENTIAL_SHEET_KEY",
            )
            if err:
                results["call_volume_legacy"] = {"error": err}
            else:
                try:
                    vol_up, vol_skip, vol_err = sync_call_volume(calls_sheet)
                    results["call_volume_legacy"] = {
                        "upserted": vol_up, "skipped": vol_skip, "errors": vol_err[:10],
                    }
                    log.info(f"Legacy call volume sync: {vol_up} upserted, {vol_skip} skipped")
                except Exception as e:
                    results["call_volume_legacy"] = {"error": str(e)}

        if "agent_status" not in feed_types_covered:
            agents_sheet, err = _open_sheet("realtime_agents_sheet_key")
            if err:
                results["agent_activity_legacy"] = {"error": err}
            else:
                try:
                    act_up, act_skip, act_err = sync_agent_activity(agents_sheet)
                    results["agent_activity_legacy"] = {
                        "upserted": act_up, "skipped": act_skip, "errors": act_err[:10],
                    }
                    log.info(f"Legacy agent activity sync: {act_up} upserted, {act_skip} skipped")
                except Exception as e:
                    results["agent_activity_legacy"] = {"error": str(e)}

        # Commit feed status updates
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()

        # Store last sync time
        try:
            AppSetting.set("realtime_last_sync", datetime.utcnow().isoformat())
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
    """Manual trigger for the sync.
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

    calls_key = os.environ.get("CALLPOTENTIAL_SHEET_KEY", "")
    if not calls_key:
        try:
            calls_key = AppSetting.get("realtime_calls_sheet_key", "")
        except Exception:
            pass

    agents_key = ""
    try:
        agents_key = AppSetting.get("realtime_agents_sheet_key", "")
    except Exception:
        pass

    last_sync = ""
    try:
        last_sync = AppSetting.get("realtime_last_sync", "")
        if not last_sync:
            last_sync = AppSetting.get("callpotential_last_sync", "")
    except Exception:
        pass

    sync_enabled = os.environ.get("CALLPOTENTIAL_SYNC_ENABLED", "true").lower() == "true"

    return jsonify({
        "configured": bool(calls_key or agents_key),
        "calls_sheet_key": calls_key[:8] + "..." if calls_key else "",
        "agents_sheet_key": agents_key[:8] + "..." if agents_key else "",
        "last_sync": last_sync,
        "sync_enabled": sync_enabled,
        "interval_minutes": 5,
    })


@realtime_sync_bp.route("/realtime/configure", methods=["POST"])
def configure_sync():
    """Save sheet keys to AppSettings."""
    auth_err = _require_admin()
    if auth_err:
        return auth_err

    data = request.get_json() or {}
    calls_key = data.get("calls_sheet_key", "").strip()
    agents_key = data.get("agents_sheet_key", "").strip()

    # Backwards compat: accept old field name
    if not calls_key and not agents_key:
        old_key = data.get("sheet_key", "").strip()
        if old_key:
            calls_key = old_key

    if not calls_key and not agents_key:
        return jsonify({"error": "At least one sheet key is required"}), 400

    tabs_info = {}

    if calls_key:
        AppSetting.set("realtime_calls_sheet_key", calls_key)
        sheet, err = _open_sheet("realtime_calls_sheet_key")
        if err:
            db.session.rollback()
            return jsonify({"ok": False, "error": f"Calls sheet: {err}"}), 400
        tabs_info["calls_tabs"] = [ws.title for ws in sheet.worksheets()]

    if agents_key:
        AppSetting.set("realtime_agents_sheet_key", agents_key)
        sheet, err = _open_sheet("realtime_agents_sheet_key")
        if err:
            db.session.rollback()
            return jsonify({"ok": False, "error": f"Agents sheet: {err}"}), 400
        tabs_info["agents_tabs"] = [ws.title for ws in sheet.worksheets()]

    db.session.commit()

    return jsonify({
        "ok": True,
        "message": "Connected successfully",
        **tabs_info,
    })
