"""
ingestion/service.py — pull historical interval data from configured sources
=============================================================================

Each DataSource record tells us WHERE the data lives (Google Sheet tab, API
endpoint, or uploaded CSV) and HOW to map its columns to our IntervalActual
fields.  This module reads from the source, maps columns, and upserts rows
into `interval_actuals`.

Usage:
    from app.ingestion.service import sync_source, sync_all_sources
    result = sync_source(data_source_id)       # one source
    results = sync_all_sources()               # all active sources
"""

import logging
import json
from datetime import datetime

from app.models import db, DataSource, IntervalActual, PlanningUnit

log = logging.getLogger("serevo.ingestion")

# ── Default column mapping ────────────────────────────────────
DEFAULT_MAPPING = {
    "lob":       "LOB",
    "date":      "Date",
    "timestamp": "Timestamp",
    "offered":   "Offered",
    "answered":  "Answered",
    "abandoned": "Abandoned",
    "aht":       "AHT",
    "asa":       "ASA",
}


def _resolve_column(row, field_name, mapping):
    """Get a value from `row` using the column mapping for `field_name`.
    Tries the mapped name, then common variants (title, lower, upper)."""
    mapped = mapping.get(field_name, DEFAULT_MAPPING.get(field_name, field_name))
    for variant in (mapped, mapped.title(), mapped.lower(), mapped.upper(),
                    mapped.replace("_", " "), mapped.replace(" ", "_")):
        v = row.get(variant)
        if v is not None and str(v).strip():
            return str(v).strip()
    return ""


def _parse_timestamp(date_str, time_str=""):
    """Parse date + optional time into a datetime. Returns None on failure."""
    date_str = str(date_str).strip()
    time_str = str(time_str).strip() if time_str else ""

    # Combined datetime string (2024-01-15 08:00)
    if len(date_str) > 10 and not time_str:
        try:
            return datetime.strptime(date_str[:16], "%Y-%m-%d %H:%M")
        except ValueError:
            pass

    # Separate date + time
    date_part = date_str[:10]
    time_part = time_str[:5] if time_str else "00:00"
    try:
        return datetime.strptime(f"{date_part} {time_part}", "%Y-%m-%d %H:%M")
    except ValueError:
        pass

    # Try other date formats
    for fmt in ("%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            dt = datetime.strptime(date_part, fmt)
            if time_part:
                h, m = time_part.split(":")
                dt = dt.replace(hour=int(h), minute=int(m))
            return dt
        except ValueError:
            continue
    return None


def _safe_float(val, default=0.0):
    """Convert to float, stripping commas and whitespace."""
    if val is None or str(val).strip() == "":
        return default
    try:
        return float(str(val).replace(",", "").strip())
    except (ValueError, TypeError):
        return default


def _safe_int(val, default=0):
    try:
        return int(_safe_float(val, default))
    except (ValueError, TypeError):
        return default


def _get_or_create_planning_unit(name):
    """Find or create a PlanningUnit by name. Returns the PlanningUnit."""
    name = str(name).strip()
    if not name:
        return None
    pu = PlanningUnit.query.filter_by(name=name).first()
    if not pu:
        pu = PlanningUnit(name=name, is_active=True)
        db.session.add(pu)
        db.session.flush()  # get the id
    return pu


# ══════════════════════════════════════════════════════════════
# Google Sheets reader
# ══════════════════════════════════════════════════════════════

def _read_google_sheet(source):
    """Read rows from a Google Sheet tab. Returns (list_of_dicts, error)."""
    try:
        import gspread
        from oauth2client.service_account import ServiceAccountCredentials

        scope = ["https://spreadsheets.google.com/feeds",
                 "https://www.googleapis.com/auth/drive"]

        # Credentials: source-level, then fall back to app-level
        sa_json = source.service_account_json
        if not sa_json:
            from app.models import AppSetting
            sa_json = AppSetting.get("google_service_account_json", "")

        if not sa_json:
            # Try env-var file path
            import os
            sa_file = os.environ.get("SERVICE_ACCOUNT_FILE", "")
            if sa_file:
                creds = ServiceAccountCredentials.from_json_keyfile_name(sa_file, scope)
            else:
                return None, "No service account credentials for this source"
        else:
            creds_dict = json.loads(sa_json) if isinstance(sa_json, str) else sa_json
            creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)

        client = gspread.authorize(creds)
        sheet = client.open_by_key(source.sheet_key)
        ws = sheet.worksheet(source.tab_name or "ACTUALS RAW")

        # Detect header row (row 1 or row 3)
        first_row = ws.row_values(1)
        first_lower = [str(c).strip().lower() for c in first_row]

        # Check if row 1 has recognizable headers
        has_headers = any(h in first_lower for h in ("lob", "date", "timestamp", "offered"))
        if has_headers:
            records = ws.get_all_records()
        else:
            # Try row 3
            try:
                row3 = ws.row_values(3)
                row3_lower = [str(c).strip().lower() for c in row3]
                if any(h in row3_lower for h in ("lob", "date", "timestamp", "offered")):
                    records = ws.get_all_records(head=3)
                else:
                    records = ws.get_all_records()
            except Exception:
                records = ws.get_all_records()

        return records, None

    except Exception as e:
        return None, f"Sheet read error: {e}"


# ══════════════════════════════════════════════════════════════
# API reader
# ══════════════════════════════════════════════════════════════

def _read_api(source):
    """Read rows from an API endpoint. Returns (list_of_dicts, error)."""
    try:
        import requests
        from app.models import APIConnection

        conn = APIConnection.query.get(source.api_connection_id)
        if not conn or not conn.is_active:
            return None, "API connection not found or inactive"

        url = f"{conn.base_url.rstrip('/')}/{source.api_endpoint.lstrip('/')}"

        # Build auth headers
        headers = {"Content-Type": "application/json"}
        creds = json.loads(conn.credentials) if conn.credentials else {}

        if conn.auth_type == "bearer":
            token = creds.get("token", "")
            headers["Authorization"] = f"Bearer {token}"
        elif conn.auth_type == "api_key":
            key_name = creds.get("header_name", "X-API-Key")
            headers[key_name] = creds.get("api_key", "")
        elif conn.auth_type == "basic":
            import base64
            pair = f"{creds.get('username', '')}:{creds.get('password', '')}"
            headers["Authorization"] = f"Basic {base64.b64encode(pair.encode()).decode()}"

        resp = requests.get(url, headers=headers, timeout=60)
        resp.raise_for_status()
        data = resp.json()

        # API might return {data: [...]} or just [...]
        if isinstance(data, dict):
            data = data.get("data") or data.get("intervals") or data.get("results") or []
        if not isinstance(data, list):
            return None, f"Unexpected API response format: {type(data)}"

        return data, None

    except Exception as e:
        return None, f"API read error: {e}"


# ══════════════════════════════════════════════════════════════
# Core sync logic
# ══════════════════════════════════════════════════════════════

def _upsert_rows(rows, source):
    """Map and upsert a list of dicts into IntervalActual. Returns (inserted, updated, skipped)."""
    mapping = source.column_mapping or {}
    inserted = 0
    updated = 0
    skipped = 0

    # Cache planning units to avoid repeated queries
    pu_cache = {}

    for row in rows:
        # Get LOB / planning unit name
        lob_name = _resolve_column(row, "lob", mapping)
        if not lob_name:
            skipped += 1
            continue

        # Get or create planning unit
        if lob_name not in pu_cache:
            pu = _get_or_create_planning_unit(lob_name)
            if not pu:
                skipped += 1
                continue
            pu_cache[lob_name] = pu
        pu = pu_cache[lob_name]

        # Parse timestamp
        date_val = _resolve_column(row, "date", mapping)
        time_val = _resolve_column(row, "timestamp", mapping)
        ts = _parse_timestamp(date_val, time_val)
        if not ts:
            # Try timestamp as combined field
            ts = _parse_timestamp(time_val)
        if not ts:
            skipped += 1
            continue

        # Get metric values
        offered = _safe_int(_resolve_column(row, "offered", mapping))
        answered = _safe_int(_resolve_column(row, "answered", mapping))
        abandoned = _safe_int(_resolve_column(row, "abandoned", mapping))
        aht = _safe_float(_resolve_column(row, "aht", mapping))
        asa = _safe_float(_resolve_column(row, "asa", mapping))

        # Upsert: find existing row by (planning_unit_id, timestamp) or create
        existing = IntervalActual.query.filter_by(
            planning_unit_id=pu.id, timestamp=ts
        ).first()

        if existing:
            # Update only if values differ
            changed = False
            if offered and existing.offered != offered:
                existing.offered = offered
                changed = True
            if answered and existing.answered != answered:
                existing.answered = answered
                changed = True
            if abandoned and existing.abandoned != abandoned:
                existing.abandoned = abandoned
                changed = True
            if aht and existing.aht_secs != aht:
                existing.aht_secs = aht
                changed = True
            if asa and existing.asa_secs != asa:
                existing.asa_secs = asa
                changed = True
            if changed:
                existing.source = source.source_type
                existing.data_source_id = source.id
                existing.uploaded_at = datetime.utcnow()
                updated += 1
        else:
            actual = IntervalActual(
                planning_unit_id=pu.id,
                timestamp=ts,
                offered=offered,
                answered=answered,
                abandoned=abandoned,
                aht_secs=aht if aht else None,
                asa_secs=asa if asa else None,
                source=source.source_type,
                data_source_id=source.id,
            )
            db.session.add(actual)
            inserted += 1

        # Flush in batches to avoid huge memory usage
        if (inserted + updated) % 500 == 0:
            db.session.flush()

    db.session.flush()
    return inserted, updated, skipped


def sync_source(source_id):
    """Sync one DataSource. Returns dict with results."""
    source = DataSource.query.get(source_id)
    if not source:
        return {"ok": False, "error": "Data source not found"}
    if not source.is_active:
        return {"ok": False, "error": "Data source is inactive"}

    log.info(f"Syncing data source: {source.name} ({source.source_type})")

    # Read rows from the source
    if source.source_type == "google_sheet":
        rows, err = _read_google_sheet(source)
    elif source.source_type == "api":
        rows, err = _read_api(source)
    else:
        return {"ok": False, "error": f"Unknown source type: {source.source_type}"}

    if err:
        source.last_status = "error"
        source.last_error = err
        source.last_sync = datetime.utcnow()
        db.session.commit()
        return {"ok": False, "error": err}

    if not rows:
        source.last_status = "ok"
        source.last_error = ""
        source.last_sync = datetime.utcnow()
        source.rows_synced = 0
        db.session.commit()
        return {"ok": True, "inserted": 0, "updated": 0, "skipped": 0,
                "message": "No rows found in source"}

    # Upsert into IntervalActual
    try:
        inserted, updated, skipped = _upsert_rows(rows, source)
        source.last_status = "ok"
        source.last_error = ""
        source.last_sync = datetime.utcnow()
        source.rows_synced = inserted + updated
        db.session.commit()
        log.info(f"Sync complete: {inserted} new, {updated} updated, {skipped} skipped")
        return {"ok": True, "inserted": inserted, "updated": updated,
                "skipped": skipped, "total_rows": len(rows)}
    except Exception as e:
        db.session.rollback()
        source.last_status = "error"
        source.last_error = str(e)
        source.last_sync = datetime.utcnow()
        db.session.commit()
        log.exception(f"Sync error for source {source.name}")
        return {"ok": False, "error": str(e)}


def sync_all_sources():
    """Sync all active DataSources. Returns list of results."""
    sources = DataSource.query.filter_by(is_active=True).all()
    results = []
    for s in sources:
        result = sync_source(s.id)
        result["source_name"] = s.name
        result["source_id"] = s.id
        results.append(result)
    return results


def get_historical_stats():
    """Quick summary of historical data in the database."""
    from sqlalchemy import func
    total = IntervalActual.query.count()
    if not total:
        return {"total_rows": 0, "lobs": 0, "date_range": None}

    lob_count = db.session.query(
        func.count(func.distinct(IntervalActual.planning_unit_id))
    ).scalar()

    date_range = db.session.query(
        func.min(IntervalActual.timestamp),
        func.max(IntervalActual.timestamp),
    ).first()

    return {
        "total_rows": total,
        "lobs": lob_count,
        "earliest": date_range[0].strftime("%Y-%m-%d") if date_range[0] else None,
        "latest": date_range[1].strftime("%Y-%m-%d") if date_range[1] else None,
    }
