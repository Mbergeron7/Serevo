"""
connectors/sync.py — sync connector data into the Serevo database
=================================================================

High-level functions that call a connector's fetch methods and upsert
the normalized data into the Employee, ForecastInterval, Schedule,
and PlanningUnit tables.

Usage:
    from app.connectors.sync import sync_employees, sync_forecasts, sync_schedules
    result = sync_employees(api_connection_id)
    result = sync_forecasts(api_connection_id, "2025-01-01", "2025-01-31")
"""

import logging
from datetime import datetime, date, time, timezone

from app.models import (
    db, APIConnection, Employee, PlanningUnit,
    ForecastInterval, Schedule, ShiftSegment,
)
from app.connectors.registry import get_connector

log = logging.getLogger("serevo.connectors.sync")


# ── Helpers ──────────────────────────────────────────────────

def _get_or_create_pu(name, cache):
    """Find or create a PlanningUnit by name. Uses `cache` dict to avoid repeated queries."""
    if not name:
        return None
    if name in cache:
        return cache[name]
    pu = PlanningUnit.query.filter_by(name=name).first()
    if not pu:
        pu = PlanningUnit(name=name, is_active=True)
        db.session.add(pu)
        db.session.flush()
    cache[name] = pu
    return pu


def _parse_date(val):
    """Parse a date string or date object into a Python date."""
    if isinstance(val, date):
        return val
    if not val:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(str(val)[:10], fmt).date()
        except ValueError:
            continue
    return None


def _parse_time(val):
    """Parse a time string (HH:MM or HH:MM:SS) into a Python time."""
    if isinstance(val, time):
        return val
    if not val:
        return None
    val = str(val).strip()
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            return datetime.strptime(val[:8], fmt).time()
        except ValueError:
            continue
    return None


def _parse_datetime(val):
    """Parse an ISO datetime string into a Python datetime."""
    if isinstance(val, datetime):
        return val
    if not val:
        return None
    val = str(val).strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(val[:19], fmt)
        except ValueError:
            continue
    return None


# ══════════════════════════════════════════════════════════════
# Sync Employees
# ══════════════════════════════════════════════════════════════

def sync_employees(connection_id):
    """Fetch employees from the connector and upsert into the database."""
    conn = APIConnection.query.get(connection_id)
    if not conn:
        return {"ok": False, "error": "Connection not found"}

    try:
        connector = get_connector(conn)
    except ValueError as e:
        return {"ok": False, "error": str(e)}

    employees, err = connector.fetch_employees()
    if err:
        return {"ok": False, "error": err}

    pu_cache = {}
    inserted = 0
    updated = 0
    skipped = 0

    for emp_data in employees:
        ext_id = str(emp_data.get("employee_id", "")).strip()
        if not ext_id:
            skipped += 1
            continue

        # Find or create planning unit
        pu_name = emp_data.get("planning_unit", "")
        pu = _get_or_create_pu(pu_name, pu_cache) if pu_name else None

        # Upsert employee by external employee_id
        existing = Employee.query.filter_by(employee_id=ext_id).first()
        if existing:
            # Don't overwrite manually edited employees
            if existing.manually_edited:
                skipped += 1
                continue
            existing.first_name = emp_data.get("first_name", existing.first_name)
            existing.last_name = emp_data.get("last_name", existing.last_name)
            existing.status = emp_data.get("status", existing.status)
            if pu:
                existing.planning_unit_id = pu.id
            existing.all_skills = ", ".join(emp_data.get("skills", [])) or existing.all_skills
            existing.contract_type = emp_data.get("contract_type", existing.contract_type)
            existing.end_date = _parse_date(emp_data.get("end_date"))
            updated += 1
        else:
            new_emp = Employee(
                employee_id=ext_id,
                first_name=emp_data.get("first_name", ""),
                last_name=emp_data.get("last_name", ""),
                status=emp_data.get("status", "Active"),
                planning_unit_id=pu.id if pu else None,
                all_skills=", ".join(emp_data.get("skills", [])),
                contract_type=emp_data.get("contract_type", "Full-Time"),
                end_date=_parse_date(emp_data.get("end_date")),
            )
            db.session.add(new_emp)
            inserted += 1

        if (inserted + updated) % 200 == 0:
            db.session.flush()

    db.session.commit()
    log.info(f"Employee sync: {inserted} new, {updated} updated, {skipped} skipped")
    return {"ok": True, "inserted": inserted, "updated": updated, "skipped": skipped}


# ══════════════════════════════════════════════════════════════
# Sync Forecasts
# ══════════════════════════════════════════════════════════════

def sync_forecasts(connection_id, date_from, date_to, workload_ids=None):
    """Fetch forecast data from the connector and upsert into ForecastInterval."""
    conn = APIConnection.query.get(connection_id)
    if not conn:
        return {"ok": False, "error": "Connection not found"}

    try:
        connector = get_connector(conn)
    except ValueError as e:
        return {"ok": False, "error": str(e)}

    forecasts, err = connector.fetch_forecasts(date_from, date_to, workload_ids)
    if err:
        return {"ok": False, "error": err}

    pu_cache = {}
    inserted = 0
    updated = 0
    skipped = 0

    for row in forecasts:
        lob_name = row.get("lob", "")
        if not lob_name:
            skipped += 1
            continue

        pu = _get_or_create_pu(lob_name, pu_cache)
        ts = _parse_datetime(row.get("timestamp"))
        if not ts:
            skipped += 1
            continue

        offered = row.get("offered", 0)
        aht = row.get("aht", 0)

        # Upsert by (planning_unit_id, timestamp)
        existing = ForecastInterval.query.filter_by(
            planning_unit_id=pu.id, timestamp=ts
        ).first()

        if existing:
            changed = False
            if existing.offered != offered:
                existing.offered = offered
                changed = True
            if existing.aht != aht:
                existing.aht = aht
                changed = True
            if changed:
                existing.source = "api"
                existing.uploaded_at = datetime.now(timezone.utc)
                updated += 1
        else:
            fi = ForecastInterval(
                planning_unit_id=pu.id,
                timestamp=ts,
                offered=offered,
                aht=aht,
                source="api",
            )
            db.session.add(fi)
            inserted += 1

        if (inserted + updated) % 500 == 0:
            db.session.flush()

    db.session.commit()
    log.info(f"Forecast sync: {inserted} new, {updated} updated, {skipped} skipped")
    return {"ok": True, "inserted": inserted, "updated": updated, "skipped": skipped,
            "total_rows": len(forecasts)}


# ══════════════════════════════════════════════════════════════
# Sync Schedules
# ══════════════════════════════════════════════════════════════

def sync_schedules(connection_id, date_from, date_to, employee_ids=None):
    """Fetch schedule data from the connector and upsert into Schedule + ShiftSegment."""
    conn = APIConnection.query.get(connection_id)
    if not conn:
        return {"ok": False, "error": "Connection not found"}

    try:
        connector = get_connector(conn)
    except ValueError as e:
        return {"ok": False, "error": str(e)}

    schedules, err = connector.fetch_schedules(date_from, date_to, employee_ids)
    if err:
        return {"ok": False, "error": err}

    inserted = 0
    updated = 0
    skipped = 0

    # Pre-load employee lookup by external ID
    emp_lookup = {}
    for emp in Employee.query.all():
        emp_lookup[emp.employee_id] = emp

    for sched_data in schedules:
        ext_id = str(sched_data.get("employee_id", "")).strip()
        emp = emp_lookup.get(ext_id)
        if not emp:
            skipped += 1
            continue

        sched_date = _parse_date(sched_data.get("date"))
        if not sched_date:
            skipped += 1
            continue

        shift_start = _parse_time(sched_data.get("start"))
        shift_end = _parse_time(sched_data.get("end"))
        hours = sched_data.get("hours", 0)

        # Upsert by (employee_id, schedule_date)
        existing = Schedule.query.filter_by(
            employee_id=emp.id, schedule_date=sched_date
        ).first()

        if existing:
            existing.shift_start = shift_start
            existing.shift_end = shift_end
            existing.hours = hours
            existing.planning_unit_id = emp.planning_unit_id

            # Replace segments
            ShiftSegment.query.filter_by(schedule_id=existing.id).delete()
            _add_segments(existing, sched_data.get("blocks", []))
            updated += 1
        else:
            new_sched = Schedule(
                employee_id=emp.id,
                planning_unit_id=emp.planning_unit_id,
                schedule_date=sched_date,
                shift_start=shift_start,
                shift_end=shift_end,
                hours=hours,
                status="scheduled",
            )
            db.session.add(new_sched)
            db.session.flush()  # need new_sched.id for segments
            _add_segments(new_sched, sched_data.get("blocks", []))
            inserted += 1

        if (inserted + updated) % 200 == 0:
            db.session.flush()

    db.session.commit()
    log.info(f"Schedule sync: {inserted} new, {updated} updated, {skipped} skipped")
    return {"ok": True, "inserted": inserted, "updated": updated, "skipped": skipped}


def _add_segments(schedule, blocks):
    """Add ShiftSegment rows from connector block data."""
    ACTIVITY_MAP = {
        "shift": "on-call",
        "on-call": "on-call",
        "break": "break",
        "lunch": "lunch",
        "meeting": "meeting",
        "training": "training",
    }
    for i, block in enumerate(blocks):
        activity_raw = str(block.get("activity", "")).lower()
        activity_type = ACTIVITY_MAP.get(block.get("type", "").lower(),
                        ACTIVITY_MAP.get(activity_raw, "other"))
        start = _parse_time(block.get("start"))
        end = _parse_time(block.get("end"))
        if not start or not end:
            continue

        # Calculate duration
        start_mins = start.hour * 60 + start.minute
        end_mins = end.hour * 60 + end.minute
        duration = end_mins - start_mins
        if duration < 0:
            duration += 24 * 60  # overnight

        seg = ShiftSegment(
            schedule_id=schedule.id,
            activity_type=activity_type,
            start_time=start,
            end_time=end,
            duration_mins=duration,
            sort_order=i,
            notes=block.get("activity", ""),
        )
        db.session.add(seg)


# ══════════════════════════════════════════════════════════════
# Test Connection (delegates to connector)
# ══════════════════════════════════════════════════════════════

def test_connection(connection_id):
    """Test a connection using the registered connector."""
    conn = APIConnection.query.get(connection_id)
    if not conn:
        return False, "Connection not found"

    try:
        connector = get_connector(conn)
    except ValueError:
        # No connector registered — fall back to generic HTTP test
        return _generic_test(conn)

    success, message = connector.test_connection()

    conn.last_tested = datetime.now(timezone.utc)
    conn.last_status = "ok" if success else "error"
    conn.last_error = "" if success else message
    db.session.commit()

    return success, message


def _generic_test(conn):
    """Fallback test for providers without a registered connector."""
    import urllib.request
    import urllib.error
    import json

    if not conn.base_url:
        return False, "No base URL configured"

    try:
        url = conn.base_url.rstrip("/")
        req = urllib.request.Request(url, method="GET")
        req.add_header("User-Agent", "Serevo/1.0")
        req.add_header("Accept", "application/json")

        creds = json.loads(conn.credentials) if conn.credentials else {}
        if conn.auth_type == "bearer":
            token = creds.get("token", creds.get("access_token", creds.get("api_key", "")))
            if token:
                req.add_header("Authorization", f"Bearer {token}")

        resp = urllib.request.urlopen(req, timeout=15)
        conn.last_tested = datetime.now(timezone.utc)
        conn.last_status = "ok"
        conn.last_error = ""
        db.session.commit()
        return True, f"OK ({resp.status})"

    except Exception as e:
        conn.last_tested = datetime.now(timezone.utc)
        conn.last_status = "error"
        conn.last_error = str(e)
        db.session.commit()
        return False, str(e)
