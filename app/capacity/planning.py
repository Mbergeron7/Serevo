"""
capacity/planning.py
--------------------
Capacity planning business logic for Serevo.

Handles:
  - WFM platform API calls (forecast, requirements, employees)
  - Google Sheets caching (FORECAST RAW, REQUIREMENTS RAW, EMPLOYEES)
  - Capacity plan computation (monthly FTE, gap analysis)

Ported from wfm_ticket_portal/capacity_planning.py into the new
blueprint-based architecture.  Client-specific data (workload IDs,
LOB mappings) is now loaded from config rather than hardcoded.
"""

import os
import logging
import datetime
import calendar
import requests
from zoneinfo import ZoneInfo

log = logging.getLogger("serevo.capacity")

# ── API endpoints ────────────────────────────────────────────────
API_NEW    = os.environ.get("WFM_API_NEW",    os.environ.get("WFM_API_NEW", "https://api.example.com"))
API_LEGACY = os.environ.get("WFM_API_LEGACY", os.environ.get("WFM_API_LEGACY", "https://legacy-api.example.com/v1"))

WFM_TOKEN      = os.environ.get("WFM_API_TOKEN", os.environ.get("PW_API_TOKEN", ""))
WFM_UTC_OFFSET = int(os.environ.get("WFM_UTC_OFFSET", os.environ.get("PW_UTC_OFFSET", "-4")))
TIMEZONE      = os.environ.get("WFM_TZ", "America/Toronto")

# ── Business hours (local) ───────────────────────────────────────
BIZ_OPEN_WD  = "08:00"
BIZ_CLOSE_WD = "22:00"
BIZ_OPEN_WE  = "09:00"
BIZ_CLOSE_WE = "22:00"

# ── Defaults ─────────────────────────────────────────────────────
DEFAULT_OCCUPANCY = 0.51
DEFAULT_SHRINKAGE = 0.30

# ── Workload ID map ──────────────────────────────────────────────
# Loaded from environment JSON or falls back to built-in demo set.
# In production, set CAPACITY_WORKLOADS as a JSON string in .env.
import json as _json

_WORKLOADS_DEFAULT = {
    "Sales Support":   "demo-sales-support",
    "Tech Help Desk":  "demo-tech-help-desk",
    "Billing":         "demo-billing",
}

def _load_workloads():
    env = os.environ.get("CAPACITY_WORKLOADS", "")
    if env:
        try:
            return _json.loads(env)
        except Exception:
            log.warning("Could not parse CAPACITY_WORKLOADS env var; using defaults")
    return _WORKLOADS_DEFAULT

WORKLOADS = _load_workloads()


# =========================================================
# HELPERS
# =========================================================

def _get_wfm_connection():
    """Return (base_url, token) from the saved WFM API connection
    (Settings → Connections).  Falls back to environment variables."""
    db_url = ""
    db_token = ""
    try:
        import json as _json
        from app.models import APIConnection
        conn = (APIConnection.query
                .filter(APIConnection.provider.in_(["wfm_legacy", "injixo"]),
                        APIConnection.is_active == True)
                .order_by(APIConnection.updated_at.desc()).first())
        if conn:
            if conn.base_url:
                db_url = conn.base_url.rstrip("/")
            if conn.credentials:
                creds = _json.loads(conn.credentials)
                raw = creds.get("access_token") or creds.get("api_key", "")
                if raw:
                    from app.routes.settings import _clean_token
                    db_token = _clean_token(raw)
    except Exception as e:
        log.debug(f"No saved WFM connection: {e}")

    url = db_url or API_LEGACY
    token = db_token or WFM_TOKEN or os.environ.get("WFM_API_TOKEN", "")
    return url, token


def _connection_token():
    """Token from the saved WFM API connection (Settings → API Connections).
    Falls back to the WFM_API_TOKEN environment variable."""
    _, token = _get_wfm_connection()
    return token


def _wfm_headers():
    token = _connection_token()
    return {
        "Authorization": f"Bearer {token}",
        "Accept":        "application/json",
    }


def _to_utc(local_dt, utc_offset=None):
    """Convert local datetime to UTC ISO string for API calls."""
    if utc_offset is None:
        utc_offset = WFM_UTC_OFFSET
    utc_dt = local_dt - datetime.timedelta(hours=utc_offset)
    return utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_weekend(d):
    return d.weekday() >= 5


def _biz_open(d):
    t = BIZ_OPEN_WE if _is_weekend(d) else BIZ_OPEN_WD
    h, m = map(int, t.split(":"))
    return datetime.datetime(d.year, d.month, d.day, h, m)


def _biz_close(d):
    t = BIZ_CLOSE_WE if _is_weekend(d) else BIZ_CLOSE_WD
    h, m = map(int, t.split(":"))
    return datetime.datetime(d.year, d.month, d.day, h, m)


def _working_days_in_month(year, month):
    """Count working days (Mon-Fri) in a month."""
    _, days = calendar.monthrange(year, month)
    return sum(1 for d in range(1, days + 1)
               if datetime.date(year, month, d).weekday() < 5)


def _parse_duration_mins(dur_str):
    """Parse ISO 8601 duration like PT30M to minutes."""
    if not dur_str:
        return 30
    dur_str = dur_str.upper().replace("PT", "")
    mins = 0
    if "H" in dur_str:
        parts = dur_str.split("H")
        try:
            mins += int(parts[0]) * 60
        except ValueError:
            pass
        dur_str = parts[1] if len(parts) > 1 else ""
    if "M" in dur_str:
        try:
            mins += int(dur_str.replace("M", ""))
        except ValueError:
            pass
    return mins or 30


# ── LOB → Planning Unit mapping ─────────────────────────────────
_LOB_TO_PU = {
    "SS Sales Combined":              "SS Sales",
    "SS Sales EN":                    "SS Sales",
    "SS Sales FR":                    "SS Sales",
    "SS Case Management Combined":    "SS Case Management",
    "SS Case Management Combined EN": "SS Case Management",
    "SS Case Management Combined FR": "SS Case Management",
    "SS Case Management CC Combined": "SS Case Management",
    "SS Case Management CC EN":       "SS Case Management",
    "SS Case Management CC FR":       "SS Case Management",
    "PS Sales Combined":              "PS Sales",
    "PS Sales EN":                    "PS Sales",
    "PS Sales FR":                    "PS Sales",
    "PS Care Combined":               "PS Care",
    "PS Care EN":                     "PS Care",
    "PS Care FR":                     "PS Care",
    "PS Case Manager":                "PS Case Manager",
    "Web Leads SS Combined":          "Web Leads SS",
    "Web Leads SS EN":                "Web Leads SS",
    "Web Leads SS FR":                "Web Leads SS",
    "Web Leads SS Inbound Combined":  "Web Leads SS Inbound",
    "Web Leads SS Inbound EN":        "Web Leads SS Inbound",
    "Web Leads SS Inbound FR":        "Web Leads SS Inbound",
    "Web Leads PS Combined":          "Web Leads PS",
    "Web Leads PS EN":                "Web Leads PS",
    "Web Leads PS FR":                "Web Leads PS",
    "I.T Support":                    "I.T Support",
    "MoveBuddy":                      "MoveBuddy",
}


def lob_to_planning_unit(lob_name):
    """Map a LOB display name to its planning unit name for headcount lookup."""
    return _LOB_TO_PU.get(lob_name, lob_name)


def _activity_id_to_name(activity_id, planning_unit_name):
    """Map a requirements activity_id back to a LOB name."""
    if not activity_id:
        return planning_unit_name
    return planning_unit_name


# =========================================================
# API CALLS
# =========================================================

def test_connection():
    """Test both API endpoints. Returns dict with status."""
    token = _connection_token() or os.environ.get("PW_API_TOKEN", "")
    if not token:
        return {"ok": False, "error": "No API token — add a WFM connection in Settings → API Connections"}

    results = {}

    # Test legacy API
    try:
        r = requests.get(f"{API_LEGACY}/employees",
                         headers=_wfm_headers(), timeout=8)
        results["legacy"] = {"status": r.status_code, "ok": r.status_code == 200}
    except Exception as e:
        results["legacy"] = {"status": 0, "ok": False, "error": str(e)}

    # Test new API
    try:
        today = datetime.date.today()
        open_dt = _biz_open(today)
        close_dt = _biz_close(today)
        wid = list(WORKLOADS.values())[0] if WORKLOADS else ""
        url = (f"{API_NEW}/workloads/{wid}/forecasts"
               f"?startTime={_to_utc(open_dt)}&endTime={_to_utc(close_dt)}")
        r = requests.get(url, headers=_wfm_headers(), timeout=8)
        results["new_api"] = {"status": r.status_code, "ok": r.status_code == 200}
    except Exception as e:
        results["new_api"] = {"status": 0, "ok": False, "error": str(e)}

    results["ok"] = results.get("legacy", {}).get("ok", False) and \
                    results.get("new_api", {}).get("ok", False)
    return results


def fetch_forecast_for_day(workload_name, workload_id, day_date, utc_offset=None):
    """Fetch forecast intervals for one workload for one day."""
    if utc_offset is None:
        utc_offset = WFM_UTC_OFFSET

    open_dt = _biz_open(day_date)
    close_dt = _biz_close(day_date)
    url = (f"{API_NEW}/workloads/{workload_id}/forecasts"
           f"?startTime={_to_utc(open_dt, utc_offset)}"
           f"&endTime={_to_utc(close_dt, utc_offset)}")
    try:
        r = requests.get(url, headers=_wfm_headers(), timeout=20)
        if r.status_code != 200:
            return []
        data = r.json().get("data", {})
        if not data:
            return []

        dur_str = data.get("intervalDuration", "PT30M")
        interval_mins = _parse_duration_mins(dur_str)

        forecasts = data.get("forecasts", {})
        auto_blk = forecasts.get("auto") or forecasts.get("operational", {})
        offered = auto_blk.get("offered", {}).get("values", [])
        aht_vals = auto_blk.get("averageHandlingTime", {}).get("values", [])

        results = []
        for i, val in enumerate(offered):
            slot_dt = open_dt + datetime.timedelta(minutes=i * interval_mins)
            if slot_dt >= close_dt:
                break
            aht = aht_vals[i] if i < len(aht_vals) else 0
            results.append((slot_dt, float(val or 0), float(aht or 0)))
        return results

    except Exception as e:
        log.warning(f"Forecast fetch error {workload_name} {day_date}: {e}")
        return []


def fetch_planning_units():
    """Fetch all planning units from legacy API."""
    try:
        r = requests.get(f"{API_LEGACY}/planning_units",
                         headers=_wfm_headers(), timeout=20)
        if r.status_code != 200:
            return []
        data = r.json()
        return data.get("planning_units", data if isinstance(data, list) else [])
    except Exception as e:
        log.warning(f"Planning units fetch error: {e}")
        return []


def fetch_requirements_for_day(planning_unit_id, planning_unit_name, day_date):
    """Fetch requirements for one planning unit for one day."""
    date_str = day_date.strftime("%Y-%m-%d")
    url = f"{API_LEGACY}/planning_units/{planning_unit_id}/requirements/{date_str}"
    try:
        r = requests.get(url, headers=_wfm_headers(), timeout=20)
        if r.status_code != 200:
            return {}
        raw = r.json()

        if isinstance(raw, dict):
            items = (raw.get("requirements") or raw.get("data")
                     or raw.get("planning_unit_requirements") or [])
            if isinstance(items, dict):
                items = [items]
        elif isinstance(raw, list):
            items = raw
        else:
            return {}

        results = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            activity_id = item.get("activity_id", "")
            act_name = _activity_id_to_name(activity_id, planning_unit_name)
            if not act_name:
                continue
            values = item.get("values", [])
            raster = item.get("raster", 1800)
            interval_mins = max(int(raster) // 60, 1) if raster else 30

            slots = []
            base_dt = datetime.datetime(day_date.year, day_date.month, day_date.day, 0, 0)
            for i, v in enumerate(values):
                slot_dt = base_dt + datetime.timedelta(minutes=i * interval_mins)
                slots.append((slot_dt, float(v or 0)))

            if act_name not in results:
                results[act_name] = []
            results[act_name].extend(slots)

        return results

    except Exception as e:
        log.warning(f"Requirements fetch error {planning_unit_name} {day_date}: {e}")
        return {}


def _legacy_get(session, path, **kw):
    try:
        base, _ = _get_wfm_connection()
        r = session.get(f"{base}/{path}", headers=_wfm_headers(), timeout=25, **kw)
        if r.ok:
            try:
                return r.json()
            except ValueError:
                log.warning(f"legacy GET {path}: non-JSON response (status {r.status_code})")
                return None
        log.warning(f"legacy GET {path}: HTTP {r.status_code}")
        return None
    except requests.exceptions.Timeout:
        log.warning(f"legacy GET {path}: timeout")
        return None
    except Exception as e:
        log.debug(f"legacy GET {path}: {e}")
        return None


def fetch_employees(max_workers=4):
    """Employee roster via the legacy WFM API.

    Uses only bulk endpoints (employees, planning_units, employment_periods,
    skills) — NO per-employee API calls — so it finishes well within
    Render's 30-second request timeout.

    Returns a list of dicts shaped like the /people output so callers
    (write_employees_to_sheet, upsert_employees_to_db) keep working."""
    session = requests.Session()

    # ── 1. Bulk fetch employees list ────────────────────────────
    base = _legacy_get(session, "employees")
    if not base:
        log.warning("fetch_employees: /employees returned nothing")
        return []
    employees = base.get("employees", base) if isinstance(base, dict) else base
    employees = [e for e in employees if isinstance(e, dict) and not e.get("deleted")]
    log.info(f"fetch_employees: {len(employees)} raw employees from API")

    # ── 2. Bulk fetch planning unit names ───────────────────────
    pu_names = {}
    pus = _legacy_get(session, "planning_units") or {}
    for u in pus.get("planning_units", []):
        try:
            pu_names[int(u.get("planning_unit_id"))] = u.get("name", "")
        except Exception:
            pass

    # ── 3. Bulk fetch employment periods ────────────────────────
    periods = {}
    per = _legacy_get(session, "employee_employment_periods") or {}
    for p in per.get("employee_employment_periods", []):
        periods[str(p.get("employee_id"))] = {
            "start": p.get("start_date", "") or "",
            "end": "" if p.get("end_date") in (None, "", "4000-01-01") else p.get("end_date", ""),
        }

    # ── 4. Bulk fetch skill names ───────────────────────────────
    skills = {}
    sk = _legacy_get(session, "skills") or {}
    for item in sk.get("skills", []):
        try:
            skills[int(item.get("skill_id"))] = item.get("name", "")
        except Exception:
            pass

    # ── 5. Status from row colour ───────────────────────────────
    INACTIVE_COLORS = {"3739363", "255"}
    LOA_COLOR = "16711680"

    def _status(color):
        c = str(color)
        if c == LOA_COLOR:
            return "LOA"
        if c in INACTIVE_COLORS:
            return "Inactive"
        return "Active"

    # ── 6. Build result from bulk data only (no per-employee calls) ──
    result = []
    for e in employees:
        eid = str(e.get("employee_id"))
        per_ = periods.get(eid, {})

        # Try to get planning unit from the employee record itself
        pu_name = ""
        pu_id = e.get("planning_unit_id")
        if pu_id:
            try:
                pu_name = pu_names.get(int(pu_id), "")
            except Exception:
                pass

        result.append({
            "employeeId": eid,
            "firstName": e.get("first_name", ""),
            "lastName": e.get("last_name", ""),
            "email": "",
            "planningUnit": pu_name,
            "status": _status(e.get("color")),
            "skills": [],
            "startDate": per_.get("start", ""),
            "endDate": per_.get("end", ""),
            "latestSkillName": "",
            "latestSkillStart": "",
            "latestSkillEnd": "",
            "personnelNumber": e.get("personnel_number", ""),
        })
    no_pu = sum(1 for r in result if not r["planningUnit"])
    if no_pu:
        log.warning(f"WFM roster: {no_pu}/{len(result)} employees have no planning unit in the API")
    log.info(f"WFM roster: {len(result)} employees")
    return result


# =========================================================
# GOOGLE SHEET WRITE HELPERS
# =========================================================

def write_forecast_to_sheet(forecast_ws, workload_name, day_intervals):
    """Write forecast intervals for one workload+day to the FORECAST RAW sheet.
    Uses flat row format: LOB | Date | Timestamp | Offered | AHT
    """
    if not day_intervals:
        return 0

    all_data = forecast_ws.get_all_values()

    # Ensure header row exists (row 1 = title handled by caller, row 2 = blank, row 3 or row 1 = headers)
    HEADERS = ["LOB", "Date", "Timestamp", "Offered", "AHT"]
    has_header = False
    for row in all_data[:3]:
        if any(str(c).strip().lower() == "lob" for c in row):
            has_header = True
            break

    if not has_header:
        # Put headers in row 1 if sheet is empty, else after existing title rows
        header_row = 1
        if all_data and all_data[0] and "FORECAST" in str(all_data[0][0]).upper():
            header_row = 3  # title in row 1, blank row 2, headers in row 3
        forecast_ws.update(f"A{header_row}", [HEADERS])

    next_row = len(all_data) + 1
    batch = []
    for (slot_dt, offered, aht) in day_intervals:
        batch.append([
            workload_name,
            slot_dt.strftime("%Y-%m-%d"),
            slot_dt.strftime("%Y-%m-%d %H:%M"),
            round(offered, 2),
            round(aht, 1),
        ])

    if batch:
        forecast_ws.update(f"A{next_row}", batch)

    return len(batch)


def write_requirements_to_sheet(req_ws, day_requirements):
    """Write requirements for one planning unit+day to REQUIREMENTS RAW sheet.
    Uses flat row format: LOB | Date | Timestamp | Agents Required
    """
    if not day_requirements:
        return 0

    all_data = req_ws.get_all_values()

    HEADERS = ["LOB", "Date", "Timestamp", "Agents Required"]
    has_header = False
    for row in all_data[:3]:
        if any(str(c).strip().lower() == "lob" for c in row):
            has_header = True
            break

    if not has_header:
        header_row = 1
        if all_data and all_data[0] and "REQUIREMENTS" in str(all_data[0][0]).upper():
            header_row = 3
        req_ws.update(f"A{header_row}", [HEADERS])

    next_row = len(all_data) + 1
    batch = []
    for lob_name, intervals in day_requirements.items():
        for (slot_dt, agents) in intervals:
            batch.append([
                lob_name,
                slot_dt.strftime("%Y-%m-%d"),
                slot_dt.strftime("%Y-%m-%d %H:%M"),
                round(agents, 2),
            ])

    if batch:
        req_ws.update(f"A{next_row}", batch)

    return len(batch)


def write_employees_to_sheet(emp_ws, employees):
    """Write employee roster to EMPLOYEES sheet."""
    if not employees:
        return 0

    headers = [
        "Employee ID", "First Name", "Last Name", "Email",
        "Planning Unit", "Status", "Skills", "Start Date", "End Date",
    ]
    rows = [headers]
    for emp in employees:
        pu = emp.get("planningUnit")
        if isinstance(pu, dict):
            pu_name = pu.get("name", "")
        else:
            pu_name = str(pu or emp.get("planning_unit", ""))

        row = [
            emp.get("employeeId") or emp.get("id", ""),
            emp.get("firstName") or emp.get("first_name", ""),
            emp.get("lastName") or emp.get("last_name", ""),
            emp.get("email", ""),
            pu_name,
            emp.get("status", ""),
            ", ".join(s.get("name", "") for s in emp.get("skills", [])
                      if isinstance(s, dict))
            if isinstance(emp.get("skills"), list) else "",
            str(emp.get("startDate") or emp.get("start_date", "")),
            str(emp.get("endDate") or emp.get("end_date", "")),
        ]
        rows.append(row)

    emp_ws.clear()
    emp_ws.update("A1", rows)
    return len(employees)


# =========================================================
# CAPACITY PLAN COMPUTATION
# =========================================================

def _month_aware_headcount(year):
    """Build month-aware headcount from the DB Employee table.

    Returns two dicts:
        hc_by_pu   = {pu_name: {month: count}}          (Combined)
        hc_by_lang = {pu_name: {"EN": {m: n}, "FR": {m: n}}}
    An employee is counted for a month if they were active during it:
        - skill_start (or created_at) <= last day of month
        - end_date is NULL or >= first day of month
        - status is not terminated/deleted
    """
    from app.models import db, Employee, PlanningUnit

    employees = (
        Employee.query
        .filter(Employee.planning_unit_id.isnot(None))
        .filter(~Employee.status.in_(["Terminated", "terminated", "Deleted", "deleted"]))
        .all()
    )
    # Build PU id→name cache
    pu_ids = {e.planning_unit_id for e in employees}
    pus = PlanningUnit.query.filter(PlanningUnit.id.in_(pu_ids)).all() if pu_ids else []
    pu_names = {pu.id: pu.name for pu in pus}

    hc_by_pu = {}    # {pu_name: {month: int}}
    hc_by_lang = {}  # {pu_name: {"EN": {m: int}, "FR": {m: int}}}

    for emp in employees:
        pu_name = pu_names.get(emp.planning_unit_id, "")
        if not pu_name:
            continue

        # Determine active range
        start = emp.skill_start or (emp.created_at.date() if emp.created_at else datetime.date(year, 1, 1))
        end = emp.end_date  # None means still active

        # Language classification
        langs_raw = (emp.languages or "English").lower()
        is_french = "french" in langs_raw or "français" in langs_raw or "francais" in langs_raw
        is_english = "english" in langs_raw or "anglais" in langs_raw

        for m in range(1, 13):
            first_of_month = datetime.date(year, m, 1)
            _, last_day = calendar.monthrange(year, m)
            last_of_month = datetime.date(year, m, last_day)

            # Employee must have started on or before end of month
            if start and start > last_of_month:
                continue
            # Employee must not have ended before start of month
            if end and end < first_of_month:
                continue

            hc_by_pu.setdefault(pu_name, {})
            hc_by_pu[pu_name][m] = hc_by_pu[pu_name].get(m, 0) + 1

            hc_by_lang.setdefault(pu_name, {"EN": {}, "FR": {}})
            if is_english:
                hc_by_lang[pu_name]["EN"][m] = hc_by_lang[pu_name]["EN"].get(m, 0) + 1
            if is_french:
                hc_by_lang[pu_name]["FR"][m] = hc_by_lang[pu_name]["FR"].get(m, 0) + 1

    return hc_by_pu, hc_by_lang


def compute_capacity_plan(forecast_ws, req_ws, emp_ws, year,
                          shrinkage=None, occupancy=None, answer_rate=None):
    """Read cached flat-row sheets and compute the monthly capacity plan.

    Sheets use flat row format:
        FORECAST RAW:      LOB | Date | Timestamp | Offered | AHT
        REQUIREMENTS RAW:  LOB | Date | Timestamp | Agents Required

    Produces plan rows for every LOB found in the sheets (Combined, EN, FR).
    Headcount comes from the DB with month-aware start/end date logic.
    """
    fc_data = forecast_ws.get_all_values() if forecast_ws else []
    rq_data = req_ws.get_all_values() if req_ws else []

    months = list(range(1, 13))

    def parse_ts(s):
        if not s:
            return None
        try:
            return datetime.datetime.strptime(str(s)[:16], "%Y-%m-%d %H:%M")
        except Exception:
            return None

    def _find_col(headers, name):
        """Find column index by name (case-insensitive)."""
        for i, h in enumerate(headers):
            if h.strip().lower() == name.lower():
                return i
        return -1

    # ── Parse flat-row forecast data ────────────────────────────
    # Find header row (look in first 3 rows for one containing "LOB")
    fc_headers = []
    fc_start = 0
    for i, row in enumerate(fc_data[:5]):
        if any(str(c).strip().lower() == "lob" for c in row):
            fc_headers = [str(c).strip() for c in row]
            fc_start = i + 1
            break

    fc_lob_col = _find_col(fc_headers, "LOB")
    fc_ts_col = _find_col(fc_headers, "Timestamp")
    fc_offered_col = _find_col(fc_headers, "Offered")
    fc_aht_col = _find_col(fc_headers, "AHT")

    # {lob_name: {month: {"offered": float, "aht_sum": float, "aht_count": int}}}
    fc_monthly = {}
    for row in fc_data[fc_start:]:
        if not row or fc_lob_col < 0 or fc_lob_col >= len(row):
            continue
        lob = str(row[fc_lob_col]).strip()
        if not lob:
            continue
        ts = parse_ts(row[fc_ts_col]) if fc_ts_col >= 0 and fc_ts_col < len(row) else None
        if not ts or ts.year != year:
            continue
        m = ts.month
        try:
            offered = float(row[fc_offered_col]) if fc_offered_col >= 0 and fc_offered_col < len(row) and row[fc_offered_col] else 0
        except (ValueError, TypeError):
            offered = 0
        try:
            aht = float(row[fc_aht_col]) if fc_aht_col >= 0 and fc_aht_col < len(row) and row[fc_aht_col] else 0
        except (ValueError, TypeError):
            aht = 0

        fc_monthly.setdefault(lob, {})
        bucket = fc_monthly[lob].setdefault(m, {"offered": 0, "aht_sum": 0, "aht_count": 0})
        bucket["offered"] += offered
        if aht > 0:
            bucket["aht_sum"] += aht
            bucket["aht_count"] += 1

    # ── Parse flat-row requirements data ────────────────────────
    rq_headers = []
    rq_start = 0
    for i, row in enumerate(rq_data[:5]):
        if any(str(c).strip().lower() == "lob" for c in row):
            rq_headers = [str(c).strip() for c in row]
            rq_start = i + 1
            break

    rq_lob_col = _find_col(rq_headers, "LOB")
    rq_ts_col = _find_col(rq_headers, "Timestamp")
    rq_agents_col = _find_col(rq_headers, "Agents Required")

    # {lob_name: {month: {"total": float, "peak": float, "count": int}}}
    rq_monthly = {}
    for row in rq_data[rq_start:]:
        if not row or rq_lob_col < 0 or rq_lob_col >= len(row):
            continue
        lob = str(row[rq_lob_col]).strip()
        if not lob:
            continue
        ts = parse_ts(row[rq_ts_col]) if rq_ts_col >= 0 and rq_ts_col < len(row) else None
        if not ts or ts.year != year:
            continue
        m = ts.month
        try:
            agents = float(row[rq_agents_col]) if rq_agents_col >= 0 and rq_agents_col < len(row) and row[rq_agents_col] else 0
        except (ValueError, TypeError):
            agents = 0

        rq_monthly.setdefault(lob, {})
        bucket = rq_monthly[lob].setdefault(m, {"total": 0, "peak": 0, "count": 0})
        bucket["total"] += agents * 0.5  # 30-min intervals → agent-hours
        if agents > bucket["peak"]:
            bucket["peak"] = agents
        bucket["count"] += 1

    # ── Month-aware headcount from DB ───────────────────────────
    hc_by_pu, hc_by_lang = _month_aware_headcount(year)

    # Resolve parameter defaults
    shr = shrinkage if shrinkage is not None else DEFAULT_SHRINKAGE
    occ = occupancy if occupancy is not None else DEFAULT_OCCUPANCY
    ar  = answer_rate if answer_rate is not None else 0.92

    # ── Build plan per LOB ──────────────────────────────────────
    plan = []
    all_lobs = sorted(set(list(fc_monthly.keys()) + list(rq_monthly.keys())))

    for lob in all_lobs:
        lob_plan = {"lob": lob, "months": []}
        pu_name = lob_to_planning_unit(lob)

        # Determine which headcount to use for this LOB row
        lob_lower = lob.lower()
        if lob_lower.endswith(" en"):
            hc_source = hc_by_lang.get(pu_name, {}).get("EN", {})
        elif lob_lower.endswith(" fr"):
            hc_source = hc_by_lang.get(pu_name, {}).get("FR", {})
        else:
            # Combined or LOBs without language split
            hc_source = hc_by_pu.get(pu_name, {})

        for m in months:
            wd = _working_days_in_month(year, m)
            working_hrs = wd * 7.5

            fc_bucket = fc_monthly.get(lob, {}).get(m, {})
            fc_calls = fc_bucket.get("offered", 0) if isinstance(fc_bucket, dict) else 0
            fc_answered = round(fc_calls * ar)
            avg_aht = 0
            if isinstance(fc_bucket, dict) and fc_bucket.get("aht_count", 0) > 0:
                avg_aht = fc_bucket["aht_sum"] / fc_bucket["aht_count"]

            rq_bucket = rq_monthly.get(lob, {}).get(m, {})
            psih_raw = rq_bucket.get("total", 0) if isinstance(rq_bucket, dict) else 0
            psih_shr = psih_raw / (1 - shr) if psih_raw > 0 else 0
            fte_req = round(psih_shr / working_hrs, 1) if working_hrs > 0 and psih_shr > 0 else 0

            actual_hc = hc_source.get(m, 0)
            gap = round(actual_hc - fte_req, 1)

            peak_agents = rq_bucket.get("peak", 0) if isinstance(rq_bucket, dict) else 0
            rq_count = rq_bucket.get("count", 0) if isinstance(rq_bucket, dict) else 0
            avg_agents = round((rq_bucket.get("total", 0) / 0.5) / rq_count, 1) if rq_count > 0 else 0

            lob_plan["months"].append({
                "month":        m,
                "month_label":  datetime.date(year, m, 1).strftime("%b-%y"),
                "fc_offered":   round(fc_calls),
                "fc_answered":  fc_answered,
                "aht":          round(avg_aht, 1) if avg_aht else "—",
                "psih_raw":     round(psih_raw, 1),
                "psih_shr":     round(psih_shr, 1),
                "fte_req":      fte_req,
                "actual_hc":    actual_hc,
                "gap":          gap,
                "occupancy":    occ,
                "shrinkage":    shr,
                "working_days": wd,
                "peak_agents":  round(peak_agents, 1),
                "avg_agents":   avg_agents,
            })

        plan.append(lob_plan)

    return plan


def upsert_employees_to_db(employees):
    """Write a WFM roster into Serevo's Employee table.
    Returns (created, updated)."""
    from app.models import db, Employee, PlanningUnit
    created = updated = 0
    pu_cache = {}

    def _unit(name):
        name = (name or "").strip()
        if not name:
            return None
        if name not in pu_cache:
            pu = PlanningUnit.query.filter(db.func.lower(PlanningUnit.name) == name.lower()).first()
            if not pu:
                pu = PlanningUnit(name=name)
                db.session.add(pu)
                db.session.flush()
            pu_cache[name] = pu
        return pu_cache[name]

    def _date(v):
        v = str(v or "")[:10]
        try:
            return datetime.datetime.strptime(v, "%Y-%m-%d").date()
        except Exception:
            return None

    for emp in employees:
        ext_id = str(emp.get("employeeId") or emp.get("id") or "").strip()
        first = (emp.get("firstName") or emp.get("first_name") or "").strip()
        last = (emp.get("lastName") or emp.get("last_name") or "").strip()
        if not ext_id or not (first or last):
            continue
        pu = emp.get("planningUnit")
        pu_name = pu.get("name", "") if isinstance(pu, dict) else str(pu or emp.get("planning_unit", ""))
        # Serevo groups people into LOBs by their latest skill; fall back to PU
        lob_name = emp.get("latestSkillName") or pu_name
        status_raw = str(emp.get("status") or "Active").strip()
        status = {"active": "Active", "inactive": "Inactive", "loa": "LOA",
                  "terminated": "Inactive", "deleted": "Inactive"}.get(status_raw.lower(), status_raw or "Active")
        skills = emp.get("skills")
        skills_txt = ", ".join(s.get("name", "") for s in skills if isinstance(s, dict)) if isinstance(skills, list) else ""
        unit = _unit(lob_name) or _unit("Unassigned")

        row = Employee.query.filter_by(employee_id=ext_id).first()
        if row:
            if row.manually_edited:
                continue  # skip — user made manual changes
            row.first_name, row.last_name, row.status = first, last, status
            row.planning_unit_id = unit.id
            if skills_txt:
                row.all_skills = skills_txt
            ed = _date(emp.get("endDate") or emp.get("end_date"))
            row.end_date = ed
            ss = _date(emp.get("latestSkillStart"))
            if ss:
                row.skill_start = ss
            row.skill_end = _date(emp.get("latestSkillEnd"))
            updated += 1
        else:
            db.session.add(Employee(
                employee_id=ext_id, first_name=first, last_name=last, status=status,
                planning_unit_id=unit.id, all_skills=skills_txt,
                skill_start=_date(emp.get("latestSkillStart")) or _date(emp.get("startDate") or emp.get("start_date")),
                skill_end=_date(emp.get("latestSkillEnd")),
                end_date=_date(emp.get("endDate") or emp.get("end_date")),
            ))
            created += 1
    db.session.commit()
    return created, updated


# =========================================================
# WFM SCHEDULE IMPORT
# =========================================================

def _segment_type_for(activity_name):
    """Map a WFM activity name onto a Serevo segment code.
    Exact/partial match against SegmentCode labels first, then keywords."""
    name = (activity_name or "").strip().lower()
    try:
        from app.models import SegmentCode
        for sc in SegmentCode.query.filter_by(is_active=True).all():
            if name == (sc.label or "").lower() or name == (sc.code or "").lower():
                return sc.code
        for sc in SegmentCode.query.filter_by(is_active=True).all():
            if (sc.label or "").lower() in name and len(sc.label or "") > 3:
                return sc.code
    except Exception:
        pass
    for key, code in (("lunch", "lunch"), ("meal", "lunch"), ("break", "break"),
                      ("train", "training"), ("meeting", "meeting"), ("coach", "coaching"),
                      ("1-on-1", "coaching"), ("project", "project"), ("admin", "other")):
        if key in name:
            return code
    return "on-call"


def fetch_pw_schedules(start_date, end_date, employee_ext_ids=None):
    """Pull schedules from the connected WFM system for a date range.

    Uses sequential per-employee calls to the legacy API (the API
    rate-limits concurrent requests).

    Returns list of {employee_id, date, start, end, hours, segments:[…]}.
    Days with no schedule blocks are omitted.
    """
    session = requests.Session()

    # ── Activity name lookup ──────────────────────────────────────
    acts = _legacy_get(session, "activities") or {}
    activity_names = {}
    for a in acts.get("activities", []):
        if a.get("activity_id") is not None:
            activity_names[str(a.get("activity_id"))] = a.get("name", "")

    # ── Date list ─────────────────────────────────────────────────
    days = []
    d = start_date
    while d <= end_date:
        days.append(d)
        d += datetime.timedelta(days=1)

    # ── Employee filter set (if provided) ─────────────────────────
    wanted_eids = {str(i) for i in employee_ext_ids} if employee_ext_ids else None

    # ── Helper: parse schedule blocks for one employee-day ────────
    def _parse_blocks(data_entries):
        """Parse schedule_blocks from a list of schedule entries."""
        blocks = []
        for entry in data_entries:
            for blk in entry.get("schedule_blocks", []):
                try:
                    st = datetime.datetime.fromisoformat(str(blk.get("time_start"))[:19])
                    en = datetime.datetime.fromisoformat(str(blk.get("time_end"))[:19])
                except Exception:
                    continue
                if en <= st:
                    en += datetime.timedelta(days=1)
                aname = activity_names.get(str(blk.get("activity_id")),
                                           str(blk.get("type") or ""))
                blocks.append((st, en, aname))
        return blocks

    def _build_shift(eid, day, blocks):
        """Build a shift dict from parsed blocks."""
        if not blocks:
            return None
        blocks.sort(key=lambda b: b[0])
        first = min(b[0] for b in blocks)
        last = max(b[1] for b in blocks)
        segs = []
        for st, en, aname in blocks:
            segs.append({
                "type": _segment_type_for(aname),
                "start": st.strftime("%H:%M"), "end": en.strftime("%H:%M"),
                "duration_mins": int((en - st).total_seconds() // 60),
                "notes": aname,
            })
        return {
            "employee_id": eid, "date": day.isoformat(),
            "start": first.strftime("%H:%M"), "end": last.strftime("%H:%M"),
            "hours": round((last - first).total_seconds() / 3600, 2),
            "segments": segs,
        }

    # ── Per-employee schedule fetch (singular /schedule/ endpoint) ──
    # This matches the proven working pattern from the original scripts.
    base = _legacy_get(session, "employees") or {}
    employees = base.get("employees", base) if isinstance(base, dict) else base
    employees = [e for e in employees if isinstance(e, dict) and not e.get("deleted")]
    if wanted_eids:
        employees = [e for e in employees if str(e.get("employee_id")) in wanted_eids]

    # Filter to only active (non-deleted) employees with an employee_id
    employees = [e for e in employees if e.get("employee_id") is not None]
    log.info(f"WFM schedule fetch: {len(employees)} employees × {len(days)} days")

    import time as _time

    out = []
    api_ok = 0
    api_fail = 0
    total_calls = len(employees) * len(days)
    done_count = 0

    def _update_progress():
        import sys
        me = sys.modules[__name__]
        status = getattr(me, '_import_status', None)
        if isinstance(status, dict):
            status["message"] = (
                f"Fetching schedules… {done_count}/{total_calls} "
                f"({done_count * 100 // total_calls}%)"
            )

    # Sequential calls with a single reused session — requests.Session
    # is NOT thread-safe, so concurrent approaches cause silent failures.
    for emp in employees:
        eid = str(emp.get("employee_id"))
        for day in days:
            data = _legacy_get(session, f"employees/{eid}/schedule/{day.isoformat()}") or {}
            schedules = data.get("schedules", []) if isinstance(data, dict) else []
            if schedules:
                api_ok += 1
            else:
                api_fail += 1
            done_count += 1
            blocks = _parse_blocks(schedules)
            shift = _build_shift(eid, day, blocks)
            if shift:
                out.append(shift)
            # Update progress every 5 calls
            if done_count % 5 == 0 or done_count == total_calls:
                _update_progress()
            # Small delay every 50 calls to stay under rate limits
            if done_count % 50 == 0:
                _time.sleep(0.5)

    log.info(f"WFM schedule results: {api_ok} with data, {api_fail} no data, {len(out)} shifts built")
    return out


def upsert_pw_schedules(shifts, start_date=None, end_date=None):
    """Write pulled schedules into Serevo's Schedule/ShiftSegment tables.

    If start_date and end_date are given, clears ALL existing schedules for
    matched employees across the entire date range first. This ensures that
    employees whose schedules were removed in PeopleWare (e.g., day off)
    don't keep stale shifts in Serevo.

    Returns (created, replaced, skipped_unknown_employee)."""
    from app.models import db, Employee, Schedule, ShiftSegment
    created = replaced = skipped = 0
    emp_cache = {}

    def _find_employee(ext_id):
        """Match a WFM employee_id against Serevo's employee_id, external_id_1, or external_id_2."""
        emp = Employee.query.filter_by(employee_id=ext_id).first()
        if emp:
            return emp
        emp = Employee.query.filter_by(external_id_1=ext_id).first()
        if emp:
            return emp
        emp = Employee.query.filter_by(external_id_2=ext_id).first()
        return emp

    # First pass: resolve all employee IDs and count skipped
    resolved = []  # list of (emp, shift_dict)
    seen_ext_ids = set()
    for s in shifts:
        ext = str(s["employee_id"])
        if ext not in emp_cache:
            emp_cache[ext] = _find_employee(ext)
        emp = emp_cache[ext]
        if not emp:
            if ext not in seen_ext_ids:
                skipped += 1
                seen_ext_ids.add(ext)
            continue
        seen_ext_ids.add(ext)
        resolved.append((emp, s))

    # Bulk-clear existing schedules for all matched employees in the date range
    # This removes shifts for employees who are now OFF in PeopleWare
    matched_emp_ids = {emp.id for emp, _ in resolved}
    if start_date and end_date and matched_emp_ids:
        old = Schedule.query.filter(
            Schedule.employee_id.in_(matched_emp_ids),
            Schedule.schedule_date >= start_date,
            Schedule.schedule_date <= end_date,
        ).all()
        replaced = len(old)
        for ex_ in old:
            db.session.delete(ex_)
        db.session.flush()

    # Insert new shifts
    for emp, s in resolved:
        day = datetime.date.fromisoformat(s["date"])
        sh, sm = map(int, s["start"].split(":"))
        eh, em = map(int, s["end"].split(":"))
        sched = Schedule(
            employee_id=emp.id, planning_unit_id=emp.planning_unit_id, schedule_date=day,
            shift_start=datetime.time(sh, sm), shift_end=datetime.time(eh, em),
            shift_type="half" if s["hours"] <= 5 else "full", hours=s["hours"], status="scheduled",
        )
        db.session.add(sched)
        db.session.flush()
        for i, seg in enumerate(s["segments"]):
            a, b = map(int, seg["start"].split(":")), map(int, seg["end"].split(":"))
            a, b = list(a), list(b)
            db.session.add(ShiftSegment(
                schedule_id=sched.id, activity_type=seg["type"],
                start_time=datetime.time(a[0], a[1]), end_time=datetime.time(b[0], b[1]),
                duration_mins=seg["duration_mins"], sort_order=i, notes=seg.get("notes", ""),
            ))
        created += 1
    db.session.commit()
    log.info(f"WFM upsert: {created} created, {replaced} cleared, {skipped} skipped (unmatched)")
    return created, replaced, skipped


# ══════════════════════════════════════════════════════════════
# DB-backed capacity plan (replaces sheet-based version)
# ══════════════════════════════════════════════════════════════

def build_capacity_plan_from_db(year, shrinkage=None, occupancy=None, answer_rate=None):
    """Build the monthly capacity plan from ForecastInterval + RequirementInterval + Employee tables.

    Returns list of {lob, months: [{month, month_label, fc_offered, fc_answered, aht,
    psih_raw, psih_shr, fte_req, actual_hc, gap, occupancy, shrinkage,
    working_days, peak_agents, avg_agents}]}
    """
    from sqlalchemy import func as sa_func, extract
    from app.models import (db, PlanningUnit, ForecastInterval,
                            RequirementInterval, Employee)

    shr = shrinkage if shrinkage is not None else DEFAULT_SHRINKAGE
    occ = occupancy if occupancy is not None else DEFAULT_OCCUPANCY
    ar = answer_rate if answer_rate is not None else 0.92

    # ── Aggregate forecasts by planning_unit × month ─────────
    fc_query = (
        db.session.query(
            ForecastInterval.planning_unit_id,
            extract("month", ForecastInterval.timestamp).label("month"),
            sa_func.sum(ForecastInterval.offered).label("total_offered"),
            sa_func.avg(ForecastInterval.aht).label("avg_aht"),
        )
        .filter(extract("year", ForecastInterval.timestamp) == year)
        .group_by(ForecastInterval.planning_unit_id,
                  extract("month", ForecastInterval.timestamp))
        .all()
    )
    # {pu_id: {month: {offered, aht}}}
    fc_data = {}
    for row in fc_query:
        fc_data.setdefault(row.planning_unit_id, {})[int(row.month)] = {
            "offered": float(row.total_offered or 0),
            "aht": float(row.avg_aht or 0),
        }

    # ── Aggregate requirements by planning_unit × month ──────
    rq_query = (
        db.session.query(
            RequirementInterval.planning_unit_id,
            extract("month", RequirementInterval.timestamp).label("month"),
            sa_func.sum(RequirementInterval.agents_required).label("total_agents"),
            sa_func.max(RequirementInterval.agents_required).label("peak_agents"),
            sa_func.avg(RequirementInterval.agents_required).label("avg_agents"),
            sa_func.count().label("interval_count"),
        )
        .filter(extract("year", RequirementInterval.timestamp) == year)
        .group_by(RequirementInterval.planning_unit_id,
                  extract("month", RequirementInterval.timestamp))
        .all()
    )
    # {pu_id: {month: {total, peak, avg, count}}}
    rq_data = {}
    for row in rq_query:
        rq_data.setdefault(row.planning_unit_id, {})[int(row.month)] = {
            "total": float(row.total_agents or 0),
            "peak": float(row.peak_agents or 0),
            "avg": float(row.avg_agents or 0),
            "count": int(row.interval_count or 0),
        }

    # ── Month-aware headcount ──────────────────────────────────
    hc_by_pu, hc_by_lang = _month_aware_headcount(year)

    # ── Find all planning units that have forecast or requirement data ─
    all_pu_ids = sorted(set(list(fc_data.keys()) + list(rq_data.keys())))
    if not all_pu_ids:
        return []

    # Get planning unit names
    pus = PlanningUnit.query.filter(PlanningUnit.id.in_(all_pu_ids)).all()
    pu_names = {pu.id: pu.name for pu in pus}

    # Build reverse map: pu_name → pu_id
    pu_name_to_id = {pu.name: pu.id for pu in pus}

    # Determine which PUs have language splits (EN/FR LOBs in _LOB_TO_PU)
    pus_with_lang = set()
    for lob_key, pu_val in _LOB_TO_PU.items():
        if lob_key.lower().endswith(" en") or lob_key.lower().endswith(" fr"):
            pus_with_lang.add(pu_val)

    months = list(range(1, 13))
    plan = []

    for pu_id in all_pu_ids:
        pu_name = pu_names.get(pu_id, f"Unit {pu_id}")

        # Build rows: Combined first, then EN and FR if this PU has language splits
        row_variants = [("Combined", pu_name)]
        if pu_name in pus_with_lang:
            row_variants = [
                ("Combined", f"{pu_name} Combined"),
                ("EN", f"{pu_name} EN"),
                ("FR", f"{pu_name} FR"),
            ]

        for variant, lob_label in row_variants:
            lob_plan = {"lob": lob_label, "months": []}

            # Pick headcount source
            if variant == "EN":
                hc_source = hc_by_lang.get(pu_name, {}).get("EN", {})
            elif variant == "FR":
                hc_source = hc_by_lang.get(pu_name, {}).get("FR", {})
            else:
                hc_source = hc_by_pu.get(pu_name, {})

            for m in months:
                wd = _working_days_in_month(year, m)
                working_hrs = wd * 7.5

                # Forecast data (DB only has combined per PU)
                fc_month = fc_data.get(pu_id, {}).get(m, {})
                fc_offered = fc_month.get("offered", 0)
                avg_aht = fc_month.get("aht", 0)
                fc_answered = round(fc_offered * ar)

                # Requirements data
                rq_month = rq_data.get(pu_id, {}).get(m, {})
                psih_raw = rq_month.get("total", 0) * 0.5
                psih_shr = psih_raw / (1 - shr) if psih_raw > 0 else 0
                fte_req = round(psih_shr / working_hrs, 1) if working_hrs > 0 and psih_shr > 0 else 0

                actual_hc = hc_source.get(m, 0)
                gap = round(actual_hc - fte_req, 1)

                peak_agents = rq_month.get("peak", 0)
                avg_agents = rq_month.get("avg", 0)

                lob_plan["months"].append({
                    "month":        m,
                    "month_label":  datetime.date(year, m, 1).strftime("%b-%y"),
                    "fc_offered":   round(fc_offered),
                    "fc_answered":  fc_answered,
                    "aht":          round(avg_aht, 1) if avg_aht else "—",
                    "psih_raw":     round(psih_raw, 1),
                    "psih_shr":     round(psih_shr, 1),
                    "fte_req":      fte_req,
                    "actual_hc":    actual_hc,
                    "gap":          gap,
                    "occupancy":    occ,
                    "shrinkage":    shr,
                    "working_days": wd,
                    "peak_agents":  round(peak_agents, 1),
                    "avg_agents":   round(avg_agents, 1),
                })

            plan.append(lob_plan)

    return plan
