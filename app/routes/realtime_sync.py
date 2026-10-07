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
    """Parse date + time strings into a datetime. Handles common formats.

    Supports three layouts:
    1. Separate date and time columns (different values)
    2. Combined datetime column mapped to both date and time (same value)
    3. Normal date + time concatenation
    """
    date_str = str(date_str).strip()
    time_str = str(time_str).strip()

    # If both columns point to the same value (combined datetime column),
    # try parsing date_str as a full datetime on its own first
    _combined_fmts = (
        "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%m/%d/%Y %I:%M:%S %p",
        "%m/%d/%Y %I:%M %p", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
        "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M",
        "%m-%d-%Y %H:%M:%S", "%m-%d-%Y %H:%M",
        "%m/%d/%y %H:%M:%S", "%m/%d/%y %H:%M", "%m/%d/%y %I:%M %p",
    )
    if date_str == time_str:
        for fmt in _combined_fmts:
            try:
                return datetime.strptime(date_str, fmt)
            except (ValueError, TypeError):
                continue

    # Try concatenated date + time
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
        # Last resort: try date_str as a full datetime (even if time_str differs)
        for fmt in _combined_fmts:
            try:
                return datetime.strptime(date_str, fmt)
            except (ValueError, TypeError):
                continue
        raise ValueError(f"Cannot parse date: {date_str}")

    for tfmt in ("%H:%M", "%I:%M %p", "%H:%M:%S", "%I:%M:%S %p"):
        try:
            t = datetime.strptime(time_str, tfmt).time()
            return datetime.combine(dt.date(), t)
        except (ValueError, TypeError):
            continue

    raise ValueError(f"Cannot parse time: {time_str}")


def _parse_datetime(val, source_tz=None, target_tz=None):
    """Parse a single datetime string, optionally converting timezones.

    source_tz: pytz timezone the data is in (e.g. "US/Central")
    target_tz: pytz timezone to convert to (defaults to "US/Eastern")
    """
    val = str(val).strip()
    dt = None
    for fmt in ("%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%m/%d/%Y %I:%M:%S %p",
                "%m/%d/%Y %I:%M %p", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
                "%m/%d/%y %H:%M:%S", "%m/%d/%y %H:%M"):
        try:
            dt = datetime.strptime(val, fmt)
            break
        except (ValueError, TypeError):
            continue
    if dt is None:
        raise ValueError(f"Cannot parse datetime: {val}")

    # Convert timezone if source_tz is provided
    if source_tz:
        try:
            from zoneinfo import ZoneInfo
            src = ZoneInfo(source_tz)
            tgt = ZoneInfo(target_tz) if target_tz else ZoneInfo("US/Eastern")
            dt = dt.replace(tzinfo=src).astimezone(tgt).replace(tzinfo=None)
        except Exception:
            pass  # If tz is invalid, use as-is

    return dt


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


# ═══════════════════════════════════════════════════════════════
# EVENT-FORMAT COLUMN ALIASES
# ═══════════════════════════════════════════════════════════════
# When data_format == "event", each row is a raw call or agent event.
# These aliases map common raw-event column names to canonical fields
# that the aggregation layer uses.

_EVENT_CALL_COL_ALIASES = {
    # timestamp of the event
    "date_created": "timestamp", "date created": "timestamp",
    "call_date": "timestamp", "call date": "timestamp",
    "datetime": "timestamp", "timestamp": "timestamp",
    "created": "timestamp", "date": "timestamp", "time": "timestamp",
    "start_time": "timestamp", "start time": "timestamp",
    # call identifier (for dedup)
    "log_id": "call_id", "log id": "call_id", "logid": "call_id",
    "call_id": "call_id", "call id": "call_id", "callid": "call_id",
    "session_id": "call_id", "session id": "call_id",
    "unique_id": "call_id", "uniqueid": "call_id",
    # queue / LOB identifier
    "queue_id": "queue", "queue id": "queue", "queueid": "queue",
    "queue": "queue", "queue_name": "queue", "queue name": "queue",
    "skill_id": "queue", "skill id": "queue",
    "route_id": "queue", "route id": "queue",
    "lob": "queue", "line of business": "queue",
    # abandon flag
    "is_abandoned": "is_abandoned", "is abandoned": "is_abandoned",
    "abandoned": "is_abandoned", "abandon": "is_abandoned",
    "disposition": "is_abandoned",
    # rolled / overflow flag
    "is_rolled_over": "is_rolled", "is rolled over": "is_rolled",
    "is_rolled": "is_rolled", "rolled": "is_rolled",
    "overflow": "is_rolled", "overflowed": "is_rolled",
    # queue wait time (seconds)
    "queue_time": "wait_time", "queue time": "wait_time",
    "wait_time": "wait_time", "wait time": "wait_time",
    "speed_of_answer": "wait_time", "speed of answer": "wait_time",
    "ring_time": "wait_time", "ring time": "wait_time",
    # talk / handle duration (seconds)
    "duration": "duration", "talk_time": "duration", "talk time": "duration",
    "handle_time": "duration", "handle time": "duration",
    "call_duration": "duration", "call duration": "duration",
    "connected_time": "duration", "connected time": "duration",
}

_EVENT_AGENT_COL_ALIASES = {
    # agent identifier
    "user_id": "agent_id", "user id": "agent_id",
    "agent_id": "agent_id", "agent id": "agent_id",
    "employee_id": "agent_id", "employee id": "agent_id",
    "ext": "agent_id", "extension": "agent_id",
    "id": "agent_id",
    # agent name
    "agent_name": "agent", "agent name": "agent",
    "name": "agent", "agent": "agent",
    "employee": "agent", "rep": "agent",
    # timestamp
    "start_time": "timestamp", "start time": "timestamp",
    "timestamp": "timestamp", "datetime": "timestamp",
    "date_created": "timestamp", "date created": "timestamp",
    "event_time": "timestamp", "event time": "timestamp",
    # status / activity
    "c_activity_sid": "status", "activity_sid": "status",
    "activity_id": "status", "activity id": "status",
    "status": "status", "state": "status",
    "agent_status": "status", "agent status": "status",
    "agent_state": "status", "agent state": "status",
    # end time
    "end_time": "end", "end time": "end",
    "ended": "end", "end": "end",
    # duration
    "duration": "duration", "time_in_status": "duration",
    "duration_sec": "duration", "duration (sec)": "duration",
}


# ═══════════════════════════════════════════════════════════════
# EVENT-FORMAT AGGREGATION
# ═══════════════════════════════════════════════════════════════

def _aggregate_call_events(rows, headers, interval_minutes=15, custom_mapping=None,
                           source_timezone=None, target_timezone=None):
    """
    Convert raw per-call event rows into interval-aggregated rows
    that sync_call_volume can process.

    Each raw row is one call. We:
    1) Map columns using event-specific aliases
    2) Deduplicate by call_id (keep first occurrence per call)
    3) Bucket into time intervals by queue
    4) Count Offered, Answered, Abandoned; average Duration (AHT), WaitTime (ASA)

    Returns a list of header + data rows in the format sync_call_volume expects.
    """
    col_map = _match_columns(headers, _EVENT_CALL_COL_ALIASES, custom_mapping=custom_mapping)

    if "timestamp" not in col_map:
        return [["date", "time", "lob", "offered", "answered", "abandoned",
                 "rolled", "asa", "aht"]]  # just headers, no data

    # Parse all events, dedup by call_id
    events = []
    seen_calls = set()
    for row in rows[1:]:
        try:
            ts_str = row[col_map["timestamp"]].strip()
            if not ts_str:
                continue

            ts = _parse_datetime(ts_str, source_tz=source_timezone, target_tz=target_timezone)

            # Dedup by call_id if available
            call_id = None
            if "call_id" in col_map:
                call_id = row[col_map["call_id"]].strip()
                if call_id and call_id in seen_calls:
                    continue
                if call_id:
                    seen_calls.add(call_id)

            queue = row[col_map["queue"]].strip() if "queue" in col_map else "default"
            is_abn = _safe_int(row[col_map["is_abandoned"]]) if "is_abandoned" in col_map else 0
            is_rolled = _safe_int(row[col_map["is_rolled"]]) if "is_rolled" in col_map else 0
            wait = _safe_float(row[col_map["wait_time"]]) if "wait_time" in col_map else None
            dur = _safe_float(row[col_map["duration"]]) if "duration" in col_map else None

            events.append({
                "ts": ts, "queue": queue,
                "is_abandoned": is_abn > 0,
                "is_rolled": is_rolled > 0,
                "wait_time": wait, "duration": dur,
            })
        except Exception:
            continue

    if not events:
        return [["date", "time", "lob", "offered", "answered", "abandoned",
                 "rolled", "asa", "aht"]]

    # Bucket into intervals
    buckets = {}  # (queue, bucket_start) → list of events
    for evt in events:
        # Floor to interval boundary
        minute = (evt["ts"].minute // interval_minutes) * interval_minutes
        bucket_start = evt["ts"].replace(minute=minute, second=0, microsecond=0)
        key = (evt["queue"], bucket_start)
        buckets.setdefault(key, []).append(evt)

    # Build aggregated rows
    result_rows = [["date", "time", "route_id", "offered", "answered",
                    "abandoned", "rolled", "asa", "aht"]]

    for (queue, bucket_start), bucket_events in sorted(buckets.items()):
        offered = len(bucket_events)
        abandoned = sum(1 for e in bucket_events if e["is_abandoned"])
        answered = offered - abandoned
        rolled = sum(1 for e in bucket_events if e["is_rolled"])

        # Average wait time (ASA) — only for answered calls
        waits = [e["wait_time"] for e in bucket_events
                 if not e["is_abandoned"] and e["wait_time"] is not None]
        asa = round(sum(waits) / len(waits), 1) if waits else ""

        # Average duration (AHT) — only for answered calls
        durs = [e["duration"] for e in bucket_events
                if not e["is_abandoned"] and e["duration"] is not None]
        aht = round(sum(durs) / len(durs), 1) if durs else ""

        result_rows.append([
            bucket_start.strftime("%m/%d/%Y"),
            bucket_start.strftime("%H:%M"),
            str(queue),
            str(offered),
            str(answered),
            str(abandoned),
            str(rolled),
            str(asa),
            str(aht),
        ])

    log.info(f"Aggregated {len(events)} call events into {len(result_rows)-1} interval rows "
             f"({interval_minutes}-min buckets)")
    return result_rows


def _aggregate_agent_events(rows, headers, interval_minutes=15, custom_mapping=None,
                            source_timezone=None, target_timezone=None):
    """
    Convert raw per-event agent status rows into the format
    sync_agent_activity can process.

    Raw rows have (agent_id, start_time, activity_sid).
    We compute durations by sorting events per agent and measuring
    the gap between consecutive status changes.

    Returns a list of header + data rows with columns sync_agent_activity expects.
    """
    col_map = _match_columns(headers, _EVENT_AGENT_COL_ALIASES, custom_mapping=custom_mapping)

    if "timestamp" not in col_map:
        return [["agent_id", "status", "start", "end", "duration"]]

    has_agent = "agent_id" in col_map or "agent" in col_map

    if not has_agent or "status" not in col_map:
        return [["agent_id", "status", "start", "end", "duration"]]

    # Parse all events
    events = []
    for row in rows[1:]:
        try:
            ts_str = row[col_map["timestamp"]].strip()
            if not ts_str:
                continue
            ts = _parse_datetime(ts_str, source_tz=source_timezone, target_tz=target_timezone)

            agent_id = ""
            if "agent_id" in col_map:
                agent_id = row[col_map["agent_id"]].strip()
            agent_name = ""
            if "agent" in col_map:
                agent_name = row[col_map["agent"]].strip()

            status = row[col_map["status"]].strip() if "status" in col_map else ""

            events.append({
                "ts": ts, "agent_id": agent_id, "agent_name": agent_name,
                "status": status,
            })
        except Exception:
            continue

    if not events:
        return [["agent_id", "status", "start", "end", "duration"]]

    # Group by agent, sort by time, compute durations from gaps
    from collections import defaultdict
    by_agent = defaultdict(list)
    for evt in events:
        key = evt["agent_id"] or evt["agent_name"]
        by_agent[key].append(evt)

    result_rows = [["agent_id", "status", "start", "end", "duration"]]

    for agent_key, agent_events in by_agent.items():
        agent_events.sort(key=lambda e: e["ts"])
        for i, evt in enumerate(agent_events):
            # End time = next event's start time (or None if last)
            end_ts = agent_events[i + 1]["ts"] if i + 1 < len(agent_events) else None
            duration = ""
            if end_ts:
                duration = str(int((end_ts - evt["ts"]).total_seconds()))

            result_rows.append([
                evt["agent_id"] or evt["agent_name"],
                evt["status"],
                evt["ts"].strftime("%Y-%m-%d %H:%M:%S"),
                end_ts.strftime("%Y-%m-%d %H:%M:%S") if end_ts else "",
                duration,
            ])

    log.info(f"Processed {len(events)} agent events into {len(result_rows)-1} status spans "
             f"for {len(by_agent)} agents")
    return result_rows


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

    # Pre-aggregate: multiple routes can map to the same planning unit
    # (e.g. SS Sales has 44+ sub-queues). SUM count fields across all
    # routes per (pu_id, timestamp) and compute weighted averages for
    # rate fields (ASA weighted by answered, AHT weighted by answered).
    aggregated = {}  # (pu_id, timestamp) -> vals dict

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

            offered = _safe_int(row[col_map["offered"]]) if "offered" in col_map else 0
            answered = _safe_int(row[col_map["answered"]]) if "answered" in col_map else 0
            answered_within = _safe_int(row[col_map["answered_within"]]) if "answered_within" in col_map else 0
            abandoned = _safe_int(row[col_map["abandoned"]]) if "abandoned" in col_map else 0
            rolled = _safe_int(row[col_map["rolled"]]) if "rolled" in col_map else 0
            asa = _safe_float(row[col_map["asa"]]) if "asa" in col_map else None
            aht = _safe_float(row[col_map["aht"]]) if "aht" in col_map else None
            max_q = _safe_int(row[col_map["max_queued"]]) if "max_queued" in col_map else None

            key = (pu.id, ts)
            prev = aggregated.get(key)
            if prev is None:
                aggregated[key] = dict(
                    offered=offered, answered=answered,
                    answered_within=answered_within, abandoned=abandoned,
                    rolled=rolled, max_queued=max_q,
                    _asa_sum=((asa or 0) * answered) if asa is not None else 0,
                    _aht_sum=((aht or 0) * answered) if aht is not None else 0,
                    _ans_for_avg=answered if (asa is not None or aht is not None) else 0,
                    source="sheet",
                )
            else:
                prev["offered"] += offered
                prev["answered"] += answered
                prev["answered_within"] += answered_within
                prev["abandoned"] += abandoned
                prev["rolled"] += rolled
                if max_q is not None:
                    prev["max_queued"] = max(prev["max_queued"] or 0, max_q)
                if asa is not None:
                    prev["_asa_sum"] += (asa * answered)
                if aht is not None:
                    prev["_aht_sum"] += (aht * answered)
                if asa is not None or aht is not None:
                    prev["_ans_for_avg"] += answered

        except Exception as e:
            errors.append(f"Row {i}: {e}")
            if len(errors) > 50:
                errors.append("... (truncated)")
                break

    # Finalize weighted averages and upsert
    for (pu_id, ts), vals in aggregated.items():
        ans = vals.pop("_ans_for_avg", 0)
        asa_sum = vals.pop("_asa_sum", 0)
        aht_sum = vals.pop("_aht_sum", 0)
        vals["asa_secs"] = round(asa_sum / ans, 1) if ans > 0 and asa_sum else None
        vals["aht_secs"] = round(aht_sum / ans, 1) if ans > 0 and aht_sum else None
        try:
            existing = IntervalActual.query.filter_by(
                planning_unit_id=pu_id, timestamp=ts
            ).first()

            if existing:
                for k, v in vals.items():
                    setattr(existing, k, v)
                existing.uploaded_at = datetime.utcnow()
            else:
                rec = IntervalActual(
                    planning_unit_id=pu_id,
                    timestamp=ts,
                    **vals,
                )
                db.session.add(rec)

            upserted += 1
        except Exception as e:
            errors.append(f"PU {pu_id} @ {ts}: {e}")

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
# EMPLOYEE / HEADCOUNT SYNC
# ═══════════════════════════════════════════════════════════════

_EMPLOYEE_COL_ALIASES = {
    "employee id": "employee_id", "id": "employee_id", "emp id": "employee_id",
    "personnel #": "employee_id", "personnel no": "employee_id",
    "first name": "first_name", "first": "first_name", "given name": "first_name",
    "last name": "last_name", "last": "last_name", "surname": "last_name", "family name": "last_name",
    "start date": "start_date", "hire date": "start_date", "hired": "start_date",
    "end date": "end_date", "termination date": "end_date", "term date": "end_date",
    "planning unit": "planning_unit", "lob": "planning_unit", "department": "planning_unit",
    "latest skill name": "planning_unit",  # PeopleWare-specific alias
    "status": "status", "employment status": "status",
    "all skills": "all_skills", "skills": "all_skills",
    "contract type": "contract_type", "contract": "contract_type",
    "email": "email", "address email": "email", "work email": "email",
    "title": "title", "job title": "title",
    "external id": "external_id_1", "current id": "external_id_1",
}


def sync_employees(spreadsheet, tab_name=None, custom_mapping=None):
    """Sync employee / headcount data from a Google Sheet into the employees table.

    Upserts by employee_id. Returns (upserted, skipped, errors).
    """
    from app.models import Employee, PlanningUnit

    try:
        ws = spreadsheet.worksheet(tab_name) if tab_name else spreadsheet.sheet1
        rows = ws.get_all_values()
    except Exception as e:
        return 0, 0, [f"Cannot read sheet: {e}"]

    if not rows or len(rows) < 2:
        return 0, 0, ["Sheet is empty or has no data rows"]

    headers = rows[0]
    col_map = _match_columns(headers, _EMPLOYEE_COL_ALIASES, custom_mapping=custom_mapping)

    if "employee_id" not in col_map:
        return 0, 0, [f"Missing Employee ID column. Found: {headers}"]
    if "first_name" not in col_map or "last_name" not in col_map:
        return 0, 0, [f"Missing First Name / Last Name columns. Found: {headers}"]

    # Pre-load planning units for quick lookup
    pu_map = {}
    try:
        for pu in PlanningUnit.query.all():
            pu_map[pu.name.strip().lower()] = pu.id
    except Exception:
        pass

    upserted = 0
    skipped = 0
    errors = []

    def _cell(row, key):
        if key not in col_map:
            return ""
        idx = col_map[key]
        return row[idx].strip() if idx < len(row) else ""

    def _parse_date(val):
        if not val:
            return None
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y", "%m/%d/%y",
                    "%Y-%m-%d %H:%M:%S", "%m/%d/%Y %H:%M:%S"):
            try:
                return datetime.strptime(val, fmt).date()
            except (ValueError, TypeError):
                continue
        return None

    for i, row in enumerate(rows[1:], start=2):
        try:
            emp_id = _cell(row, "employee_id")
            first = _cell(row, "first_name")
            last = _cell(row, "last_name")

            if not emp_id or not first:
                skipped += 1
                continue

            status = _cell(row, "status") or "Active"
            # Normalize status
            status_lower = status.lower()
            if status_lower in ("inactive", "terminated", "deleted"):
                status = status.capitalize()
            else:
                status = "Active"

            # Resolve planning unit
            pu_name = _cell(row, "planning_unit")
            pu_id = None
            if pu_name:
                pu_id = pu_map.get(pu_name.strip().lower())

            # Upsert by employee_id
            emp = Employee.query.filter_by(employee_id=str(emp_id)).first()
            if emp:
                # Don't overwrite manually-edited employees
                if emp.manually_edited:
                    skipped += 1
                    continue
                emp.first_name = first
                emp.last_name = last
                emp.status = status
                if pu_id:
                    emp.planning_unit_id = pu_id
                end_dt = _parse_date(_cell(row, "end_date"))
                if end_dt and str(end_dt) != "4000-01-01":
                    emp.end_date = end_dt
                all_skills = _cell(row, "all_skills")
                if all_skills:
                    emp.all_skills = all_skills
                contract = _cell(row, "contract_type")
                if contract:
                    emp.contract_type = "Part-Time" if "part" in contract.lower() else "Full-Time"
                email = _cell(row, "email")
                if email:
                    emp.email = email
                ext_id = _cell(row, "external_id_1")
                if ext_id:
                    emp.external_id_1 = ext_id
            else:
                emp = Employee(
                    employee_id=str(emp_id),
                    first_name=first,
                    last_name=last,
                    status=status,
                    planning_unit_id=pu_id,
                    end_date=_parse_date(_cell(row, "end_date")),
                    all_skills=_cell(row, "all_skills") or "",
                    contract_type="Part-Time" if "part" in (_cell(row, "contract_type") or "").lower() else "Full-Time",
                    email=_cell(row, "email") or None,
                    external_id_1=_cell(row, "external_id_1") or None,
                )
                db.session.add(emp)

            upserted += 1

        except Exception as e:
            errors.append(f"Row {i}: {e}")
            if len(errors) > 50:
                errors.append("... (truncated)")
                break

    db.session.commit()
    return upserted, skipped, errors


# ═══════════════════════════════════════════════════════════════
# EVENT-FORMAT SYNC WRAPPER
# ═══════════════════════════════════════════════════════════════

def _sync_event_format(spreadsheet, feed_type, tab_name=None,
                       custom_mapping=None, interval_minutes=15,
                       source_timezone=None, target_timezone=None):
    """
    Read raw event rows from a sheet, aggregate them into interval format,
    then run the standard sync function on the aggregated data.

    This bridges event-level sources (like CallPotential) to the existing
    interval-based sync engine without modifying that engine.

    Returns (upserted, skipped, errors).
    """
    # Find the worksheet
    ws = None
    if tab_name:
        try:
            ws = spreadsheet.worksheet(tab_name)
        except Exception:
            return 0, 0, [f"Tab '{tab_name}' not found"]

    if ws is None:
        ws = spreadsheet.sheet1

    raw_rows = ws.get_all_values()
    if not raw_rows or len(raw_rows) < 2:
        return 0, 0, ["Sheet is empty or has no data rows"]

    headers = raw_rows[0]

    if feed_type == "call_volume":
        aggregated = _aggregate_call_events(
            raw_rows, headers,
            interval_minutes=interval_minutes,
            custom_mapping=custom_mapping,
            source_timezone=source_timezone,
            target_timezone=target_timezone,
        )
        if len(aggregated) <= 1:
            return 0, 0, ["No call events could be parsed. Check column mapping."]

        # The aggregated data has standard headers; sync_call_volume reads
        # from a worksheet object, but we can call its inner logic directly.
        # We'll create a synthetic worksheet-like result and call the sync.
        agg_headers = aggregated[0]
        agg_col_map = _match_columns(agg_headers, _VOLUME_COL_ALIASES)

        has_identifier = "route_id" in agg_col_map or "lob" in agg_col_map
        has_time = "date" in agg_col_map and "time" in agg_col_map
        if not has_identifier:
            return 0, 0, [f"Aggregation produced no queue/LOB column. Raw headers: {headers}"]
        if not has_time:
            return 0, 0, [f"Aggregation produced no date/time columns. Raw headers: {headers}"]

        upserted = 0
        skipped = 0
        errors = []

        # Pre-aggregate across routes that map to the same PU
        evt_aggregated = {}
        for i, row in enumerate(aggregated[1:], start=2):
            try:
                pu = None
                if "route_id" in agg_col_map:
                    rid = row[agg_col_map["route_id"]].strip()
                    if rid:
                        pu = _resolve_route_to_pu(rid)

                if not pu and "lob" in agg_col_map:
                    lob_name = row[agg_col_map["lob"]].strip()
                    if lob_name:
                        pu = _resolve_lob_to_pu(lob_name)

                if not pu:
                    skipped += 1
                    continue

                ts = _parse_timestamp(
                    row[agg_col_map["date"]],
                    row[agg_col_map["time"]],
                )

                offered = _safe_int(row[agg_col_map["offered"]]) if "offered" in agg_col_map else 0
                answered = _safe_int(row[agg_col_map["answered"]]) if "answered" in agg_col_map else 0
                abandoned = _safe_int(row[agg_col_map["abandoned"]]) if "abandoned" in agg_col_map else 0
                rolled = _safe_int(row[agg_col_map["rolled"]]) if "rolled" in agg_col_map else 0
                asa = _safe_float(row[agg_col_map["asa"]]) if "asa" in agg_col_map else None
                aht = _safe_float(row[agg_col_map["aht"]]) if "aht" in agg_col_map else None

                key = (pu.id, ts)
                prev = evt_aggregated.get(key)
                if prev is None:
                    evt_aggregated[key] = dict(
                        offered=offered, answered=answered,
                        abandoned=abandoned, rolled=rolled,
                        _asa_sum=((asa or 0) * answered) if asa is not None else 0,
                        _aht_sum=((aht or 0) * answered) if aht is not None else 0,
                        _ans_for_avg=answered if (asa is not None or aht is not None) else 0,
                        source="sheet",
                    )
                else:
                    prev["offered"] += offered
                    prev["answered"] += answered
                    prev["abandoned"] += abandoned
                    prev["rolled"] += rolled
                    if asa is not None:
                        prev["_asa_sum"] += (asa * answered)
                    if aht is not None:
                        prev["_aht_sum"] += (aht * answered)
                    if asa is not None or aht is not None:
                        prev["_ans_for_avg"] += answered

            except Exception as e:
                errors.append(f"Aggregated row {i}: {e}")
                if len(errors) > 50:
                    errors.append("... (truncated)")
                    break

        # Finalize and upsert
        for (pu_id, ts), vals in evt_aggregated.items():
            ans = vals.pop("_ans_for_avg", 0)
            asa_sum = vals.pop("_asa_sum", 0)
            aht_sum = vals.pop("_aht_sum", 0)
            vals["asa_secs"] = round(asa_sum / ans, 1) if ans > 0 and asa_sum else None
            vals["aht_secs"] = round(aht_sum / ans, 1) if ans > 0 and aht_sum else None

            try:
                existing = IntervalActual.query.filter_by(
                    planning_unit_id=pu_id, timestamp=ts
                ).first()
                if existing:
                    for k, v in vals.items():
                        setattr(existing, k, v)
                    existing.uploaded_at = datetime.utcnow()
                else:
                    rec = IntervalActual(
                        planning_unit_id=pu_id, timestamp=ts, **vals,
                    )
                    db.session.add(rec)
                upserted += 1
            except Exception as e:
                errors.append(f"PU {pu_id} @ {ts}: {e}")

        db.session.commit()
        log.info(f"Event sync (call_volume): {upserted} upserted, {skipped} skipped "
                 f"from {len(raw_rows)-1} raw events")
        return upserted, skipped, errors

    elif feed_type == "agent_status":
        aggregated = _aggregate_agent_events(
            raw_rows, headers,
            interval_minutes=interval_minutes,
            custom_mapping=custom_mapping,
            source_timezone=source_timezone,
            target_timezone=target_timezone,
        )
        if len(aggregated) <= 1:
            return 0, 0, ["No agent events could be parsed. Check column mapping."]

        # Sync agent status spans using existing logic
        agg_headers = aggregated[0]
        agg_col_map = _match_columns(agg_headers, _AGENT_COL_ALIASES)

        has_agent = "agent" in agg_col_map or "agent_id" in agg_col_map
        if not has_agent or "status" not in agg_col_map or "start" not in agg_col_map:
            return 0, 0, [
                f"Aggregation produced incomplete columns. Raw headers: {headers}"
            ]

        upserted = 0
        skipped = 0
        errors = []

        for i, row in enumerate(aggregated[1:], start=2):
            try:
                status = row[agg_col_map["status"]].strip()
                if not status:
                    skipped += 1
                    continue

                emp = None
                if "agent_id" in agg_col_map:
                    agent_id = row[agg_col_map["agent_id"]].strip()
                    if agent_id:
                        emp = Employee.query.filter(
                            (Employee.external_id_2 == agent_id) |
                            (Employee.employee_id == agent_id)
                        ).first()

                if not emp and "agent" in agg_col_map:
                    agent_name = row[agg_col_map["agent"]].strip()
                    if agent_name:
                        parts = agent_name.split()
                        if len(parts) >= 2:
                            emp = Employee.query.filter(
                                db.func.lower(Employee.first_name) == parts[0].lower(),
                                db.func.lower(Employee.last_name) == parts[-1].lower(),
                            ).first()

                if not emp:
                    skipped += 1
                    continue

                start_str = row[agg_col_map["start"]].strip()
                if not start_str:
                    skipped += 1
                    continue
                start_ts = _parse_datetime(start_str)

                end_ts = None
                if "end" in agg_col_map:
                    end_str = row[agg_col_map["end"]].strip()
                    if end_str:
                        end_ts = _parse_datetime(end_str)

                if end_ts is None and "duration" in agg_col_map:
                    dur_val = _safe_float(row[agg_col_map["duration"]])
                    if dur_val is not None and dur_val > 0:
                        end_ts = start_ts + timedelta(seconds=dur_val)

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
                errors.append(f"Aggregated row {i}: {e}")
                if len(errors) > 50:
                    errors.append("... (truncated)")
                    break

        db.session.commit()
        log.info(f"Event sync (agent_status): {upserted} upserted, {skipped} skipped "
                 f"from {len(raw_rows)-1} raw events")
        return upserted, skipped, errors

    return 0, 0, [f"Event format not supported for feed_type: {feed_type}"]


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

                        data_fmt = getattr(feed, "data_format", "interval") or "interval"
                        ivl_min = getattr(feed, "interval_minutes", 15) or 15

                        if data_fmt == "event" and feed.feed_type in ("call_volume", "agent_status"):
                            # Event format: read raw rows, aggregate, then sync
                            src_tz = getattr(feed, "source_timezone", "") or ""
                            up, skip, errs = _sync_event_format(
                                spreadsheet, feed.feed_type,
                                tab_name=feed.sheet_tab,
                                custom_mapping=custom_map,
                                interval_minutes=ivl_min,
                                source_timezone=src_tz if src_tz else None,
                            )
                        elif feed.feed_type == "call_volume":
                            up, skip, errs = sync_call_volume(spreadsheet, tab_name=feed.sheet_tab, custom_mapping=custom_map)
                        elif feed.feed_type == "employees":
                            up, skip, errs = sync_employees(spreadsheet, tab_name=feed.sheet_tab, custom_mapping=custom_map)
                        else:
                            up, skip, errs = sync_agent_activity(spreadsheet, tab_name=feed.sheet_tab, custom_mapping=custom_map)

                        feed.last_upserted = up
                        feed.last_skipped = skip
                        feed.last_status = "error" if errs else "ok"
                        feed.last_error = "; ".join(errs[:3]) if errs else ""
                        feed.last_sync_at = datetime.utcnow()
                        feed_result.update({"upserted": up, "skipped": skip, "errors": errs[:5]})
                        log.info(f"Feed '{feed.name}': {up} upserted, {skip} skipped, {len(errs)} errors")
                        if errs:
                            log.warning(f"Feed '{feed.name}' errors: {'; '.join(errs[:5])}")
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


@realtime_sync_bp.route("/realtime/resync/<int:feed_id>", methods=["POST"])
def resync_feed(feed_id):
    """Clear all data for a feed and re-sync from scratch.
    Useful after changing timezone or column mapping."""
    auth_err = _require_admin()
    if auth_err:
        return auth_err

    feed = DataFeed.query.get(feed_id)
    if not feed:
        return jsonify({"error": "Feed not found"}), 404

    deleted = 0
    if feed.feed_type == "call_volume":
        deleted = IntervalActual.query.filter_by(source="sheet").delete()
    elif feed.feed_type == "agent_status":
        deleted = AgentStatusEvent.query.filter_by(source="sheet").delete()
    db.session.commit()
    log.info(f"Resync: cleared {deleted} rows for feed '{feed.name}' (type={feed.feed_type})")

    # Now re-sync
    results = run_sync()
    return jsonify({"cleared": deleted, "sync": results})


@realtime_sync_bp.route("/realtime/debug-volume/<lob_name>", methods=["GET"])
def debug_volume(lob_name):
    """Temporary diagnostic: show interval_actuals + call_routes for a LOB."""
    auth_err = _require_admin()
    if auth_err:
        return auth_err
    from app.data_source import normalize_lob
    normalized = normalize_lob(lob_name)
    pu = PlanningUnit.query.filter(
        db.func.lower(PlanningUnit.name) == normalized.lower()
    ).first()
    if not pu:
        return jsonify({"error": f"No PU for '{lob_name}' (normalized: '{normalized}')",
                        "all_pus": [p.name for p in PlanningUnit.query.all()]})

    routes = CallRoute.query.filter_by(planning_unit_id=pu.id).all()
    from sqlalchemy import desc, func as sa_func
    rows = IntervalActual.query.filter_by(planning_unit_id=pu.id)\
        .order_by(desc(IntervalActual.timestamp)).limit(100).all()

    # Daily totals
    daily = {}
    for r in rows:
        d = r.timestamp.strftime("%Y-%m-%d")
        daily[d] = daily.get(d, 0) + (r.offered or 0)

    return jsonify({
        "planning_unit": {"id": pu.id, "name": pu.name},
        "call_routes": [{"route_id": r.route_id, "name": r.name, "active": r.is_active} for r in routes],
        "daily_totals": {k: daily[k] for k in sorted(daily.keys(), reverse=True)},
        "intervals": [{
            "timestamp": r.timestamp.strftime("%Y-%m-%d %H:%M"),
            "offered": r.offered, "answered": r.answered,
            "abandoned": r.abandoned, "aht": r.aht_secs,
            "source": r.source,
        } for r in rows],
    })


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
