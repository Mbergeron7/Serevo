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
API_NEW    = os.environ.get("WFM_API_NEW",    os.environ.get("PW_API_NEW", "https://api.peopleware.com"))
API_LEGACY = os.environ.get("WFM_API_LEGACY", os.environ.get("PW_API_LEGACY", "https://legacy-api.peopleware.com/v1"))

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

def _connection_token():
    """Token from the saved WFM API connection (Settings → API Connections).
    Falls back to the WFM_API_TOKEN environment variable."""
    try:
        import json as _json
        from app.models import APIConnection
        # Try wfm_legacy first, then injixo for backwards compat
        conn = (APIConnection.query
                .filter(APIConnection.provider.in_(["wfm_legacy", "injixo"]),
                        APIConnection.is_active == True)
                .order_by(APIConnection.updated_at.desc()).first())
        if conn and conn.credentials:
            creds = _json.loads(conn.credentials)
            raw = creds.get("access_token") or creds.get("api_key", "")
            if raw:
                from app.routes.settings import _clean_token
                return _clean_token(raw)
    except Exception as e:
        log.debug(f"No saved WFM connection: {e}")
    return ""


def _wfm_headers():
    token = _connection_token() or WFM_TOKEN or os.environ.get("WFM_API_TOKEN", "")
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
        r = session.get(f"{API_LEGACY}/{path}", headers=_wfm_headers(), timeout=25, **kw)
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

def compute_capacity_plan(forecast_ws, req_ws, emp_ws, year,
                          shrinkage=None, occupancy=None, answer_rate=None):
    """Read cached raw sheets and compute the monthly capacity plan."""
    fc_data = forecast_ws.get_all_values() if forecast_ws else []
    rq_data = req_ws.get_all_values() if req_ws else []
    emp_data = emp_ws.get_all_values() if emp_ws else []

    fc_headers = fc_data[2] if len(fc_data) > 2 else []
    rq_headers = rq_data[2] if len(rq_data) > 2 else []
    emp_headers = emp_data[0] if emp_data else []

    months = list(range(1, 13))

    def parse_ts(s):
        if not s:
            return None
        try:
            return datetime.datetime.strptime(str(s)[:16], "%Y-%m-%d %H:%M")
        except Exception:
            return None

    # Aggregate forecast by LOB by month
    fc_monthly = {}
    fc_ts_col = next((i for i, h in enumerate(fc_headers)
                      if "timestamp" in h.lower()), 1)
    for row in fc_data[3:]:
        if not row or fc_ts_col >= len(row):
            continue
        ts = parse_ts(row[fc_ts_col])
        if not ts or ts.year != year:
            continue
        m = ts.month
        for ci, h in enumerate(fc_headers):
            if ci <= fc_ts_col or not h:
                continue
            try:
                v = float(row[ci]) if ci < len(row) and row[ci] else 0
                if v > 0:
                    fc_monthly.setdefault(h, {})
                    fc_monthly[h][m] = fc_monthly[h].get(m, 0) + v
            except Exception:
                pass

    # Aggregate requirements by LOB by month
    rq_monthly = {}
    rq_ts_col = next((i for i, h in enumerate(rq_headers)
                      if "timestamp" in h.lower()), 1)
    for row in rq_data[3:]:
        if not row or rq_ts_col >= len(row):
            continue
        ts = parse_ts(row[rq_ts_col])
        if not ts or ts.year != year:
            continue
        m = ts.month
        for ci, h in enumerate(rq_headers):
            if ci <= rq_ts_col or not h:
                continue
            try:
                v = float(row[ci]) if ci < len(row) and row[ci] else 0
                if v > 0:
                    rq_monthly.setdefault(h, {})
                    rq_monthly[h][m] = rq_monthly[h].get(m, 0) + (v * 0.5)
            except Exception:
                pass

    # Headcount by planning unit by month
    emp_col_map = {h.strip().lower(): i for i, h in enumerate(emp_headers)}
    pu_col = emp_col_map.get("planning unit", emp_col_map.get("planningunit", -1))
    status_col = emp_col_map.get("status", -1)

    hc_by_pu = {}
    for row in emp_data[1:]:
        if not row:
            continue
        status = (row[status_col].strip().lower()
                  if 0 <= status_col < len(row) else "")
        if status in ("inactive", "deleted", "terminated"):
            continue
        pu = row[pu_col].strip() if 0 <= pu_col < len(row) else ""
        if not pu:
            continue
        for m in months:
            hc_by_pu.setdefault(pu, {})
            hc_by_pu[pu][m] = hc_by_pu[pu].get(m, 0) + 1

    # Resolve parameter defaults
    shr = shrinkage if shrinkage is not None else DEFAULT_SHRINKAGE
    occ = occupancy if occupancy is not None else DEFAULT_OCCUPANCY
    ar  = answer_rate if answer_rate is not None else 0.92

    # Build plan per LOB
    plan = []
    all_lobs = sorted(set(list(fc_monthly.keys()) + list(rq_monthly.keys())))

    for lob in all_lobs:
        lob_plan = {"lob": lob, "months": []}
        pu_name = lob_to_planning_unit(lob)

        for m in months:
            wd = _working_days_in_month(year, m)
            fc_calls = fc_monthly.get(lob, {}).get(m, 0)
            fc_answered = round(fc_calls * ar)
            psih_raw = rq_monthly.get(lob, {}).get(m, 0)
            psih_shr = psih_raw / (1 - shr) if psih_raw > 0 else 0
            working_hrs = wd * 7.5
            fte_req = round(psih_shr / working_hrs, 1) if working_hrs > 0 and psih_shr > 0 else 0
            actual_hc = hc_by_pu.get(pu_name, {}).get(m, 0)
            gap = round(actual_hc - fte_req, 1)

            # Peak / avg agents from requirements data (per-interval)
            peak_agents = 0
            total_agent_intervals = 0
            interval_count = 0
            for row in rq_data[3:]:
                if not row or rq_ts_col >= len(row):
                    continue
                ts = parse_ts(row[rq_ts_col])
                if not ts or ts.year != year or ts.month != m:
                    continue
                for ci, h in enumerate(rq_headers):
                    if ci <= rq_ts_col or not h or h != lob:
                        continue
                    try:
                        v = float(row[ci]) if ci < len(row) and row[ci] else 0
                        if v > 0:
                            peak_agents = max(peak_agents, v)
                            total_agent_intervals += v
                            interval_count += 1
                    except Exception:
                        pass
            avg_agents = round(total_agent_intervals / interval_count, 1) if interval_count > 0 else 0

            lob_plan["months"].append({
                "month":        m,
                "month_label":  datetime.date(year, m, 1).strftime("%b-%y"),
                "fc_offered":   round(fc_calls),
                "fc_answered":  fc_answered,
                "aht":          "—",
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
        unit = _unit(lob_name)

        row = Employee.query.filter_by(employee_id=ext_id).first()
        if row:
            if row.manually_edited:
                continue  # skip — user made manual changes
            row.first_name, row.last_name, row.status = first, last, status
            if unit:
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
                planning_unit_id=unit.id if unit else None, all_skills=skills_txt,
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


def fetch_pw_schedules(start_date, end_date, employee_ext_ids=None, max_workers=8,
                       diagnose=False):
    """Pull schedules from the connected WFM system for a date range.

    Uses the bulk planning-unit endpoint when planning units are available
    (one call per planning-unit per day) and falls back to per-employee calls
    if no planning units are found.

    Returns list of {employee_id, date, start, end, hours, segments:[…]}.
    Days with no schedule blocks are omitted.
    If diagnose=True, returns (shifts, diag_dict) instead.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    session = requests.Session()
    diag = {} if diagnose else None

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

    if diagnose:
        diag["mode"] = "per_employee"
        diag["employees_count"] = len(employees)
        diag["days"] = len(days)
        diag["total_api_calls"] = len(employees) * len(days)
        # Capture token info for debugging
        token_used = _connection_token() or WFM_TOKEN or os.environ.get("WFM_API_TOKEN", "")
        diag["token_len"] = len(token_used)
        diag["token_preview"] = f"{token_used[:4]}...{token_used[-4:]}" if len(token_used) > 8 else "(short)"
        diag["api_base"] = API_LEGACY
        # Capture sample employee object keys and first few employee IDs
        if employees:
            diag["sample_emp_keys"] = list(employees[0].keys())[:20]
            diag["first_5_eids"] = [str(e.get("employee_id")) for e in employees[:5]]
            # Also check if there's an 'id' field distinct from 'employee_id'
            diag["first_5_ids"] = [str(e.get("id", "N/A")) for e in employees[:5]]
            diag["first_5_personnel"] = [str(e.get("personnel_number", "N/A")) for e in employees[:5]]
            # Try with personnel_number instead to see if that works
            pn = str(employees[0].get("personnel_number", ""))
            if pn and pn != "N/A" and pn != "None":
                pn_url = f"{API_LEGACY}/employees/{pn}/schedule/{days[0].isoformat()}"
                try:
                    pn_r = session.get(pn_url, headers=_wfm_headers(), timeout=25)
                    diag["test_personnel_call"] = {
                        "url": pn_url,
                        "status": pn_r.status_code,
                        "body_preview": pn_r.text[:500],
                    }
                except Exception as ex:
                    diag["test_personnel_call"] = {"error": str(ex)}
            # Also try today's date instead of tomorrow
            today_url = f"{API_LEGACY}/employees/{str(employees[0].get('employee_id'))}/schedule/{datetime.date.today().isoformat()}"
            try:
                today_r = session.get(today_url, headers=_wfm_headers(), timeout=25)
                diag["test_today"] = {
                    "url": today_url,
                    "status": today_r.status_code,
                    "body_preview": today_r.text[:500],
                }
            except Exception as ex:
                diag["test_today"] = {"error": str(ex)}
        # Do one raw test call to see the actual HTTP status
        if employees and days:
            test_eid = str(employees[0].get("employee_id"))
            test_day = days[0].isoformat()
            test_url = f"{API_LEGACY}/employees/{test_eid}/schedule/{test_day}"
            try:
                test_r = session.get(test_url, headers=_wfm_headers(), timeout=25)
                diag["test_call"] = {
                    "url": test_url,
                    "status": test_r.status_code,
                    "body_len": len(test_r.text),
                    "body_preview": test_r.text[:500],
                    "headers": dict(test_r.headers),
                }
            except Exception as ex:
                diag["test_call"] = {"error": str(ex)}
        sample_raw = []

    out = []
    api_ok = 0
    api_empty = 0
    api_fail = 0

    def _one(emp, day):
        nonlocal api_ok, api_empty, api_fail
        sess = requests.Session()
        eid = str(emp.get("employee_id"))
        # SINGULAR /schedule/ — confirmed working in original PW scripts
        data = _legacy_get(sess, f"employees/{eid}/schedule/{day.isoformat()}") or {}
        schedules = data.get("schedules", []) if isinstance(data, dict) else []
        if schedules:
            api_ok += 1
        elif data:
            api_empty += 1
        else:
            api_fail += 1
        if diagnose and len(sample_raw) < 5:
            sample_raw.append({
                "eid": eid, "day": day.isoformat(),
                "data_keys": list(data.keys()) if isinstance(data, dict) else type(data).__name__,
                "schedule_count": len(schedules),
                "preview": str(data)[:400],
            })
        blocks = _parse_blocks(schedules)
        return _build_shift(eid, day, blocks)

    actual_workers = min(max_workers, 6)
    with ThreadPoolExecutor(max_workers=actual_workers) as ex:
        futs = [ex.submit(_one, e, day) for e in employees for day in days]
        for f in as_completed(futs):
            try:
                r = f.result()
                if r:
                    out.append(r)
            except Exception as e:
                log.warning(f"schedule fetch error: {e}")

    log.info(f"WFM schedule results: {api_ok} with data, {api_empty} empty, {api_fail} failed")

    if diagnose:
        diag["sample_raw_responses"] = sample_raw

    log.info(f"WFM schedules: {len(out)} shifts fetched")

    if diagnose:
        diag["shifts_found"] = len(out)
        return out, diag
    return out


def upsert_pw_schedules(shifts):
    """Write pulled schedules into Serevo's Schedule/ShiftSegment tables.
    Replaces any existing schedule for the same employee+date.
    Returns (created, replaced, skipped_unknown_employee)."""
    from app.models import db, Employee, Schedule, ShiftSegment
    created = replaced = skipped = 0
    emp_cache = {}
    for s in shifts:
        ext = str(s["employee_id"])
        if ext not in emp_cache:
            emp_cache[ext] = Employee.query.filter_by(employee_id=ext).first()
        emp = emp_cache[ext]
        if not emp:
            skipped += 1
            continue
        day = datetime.date.fromisoformat(s["date"])
        existing = Schedule.query.filter_by(employee_id=emp.id, schedule_date=day).all()
        for ex_ in existing:
            db.session.delete(ex_)
            replaced += 1
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

    # ── Headcount by planning_unit (active employees) ────────
    hc_query = (
        db.session.query(
            Employee.planning_unit_id,
            sa_func.count().label("hc"),
        )
        .filter(Employee.status.in_(["Active", "active", ""]))
        .filter(Employee.planning_unit_id.isnot(None))
        .group_by(Employee.planning_unit_id)
        .all()
    )
    hc_by_pu = {row.planning_unit_id: row.hc for row in hc_query}

    # ── Find all planning units that have forecast or requirement data ─
    all_pu_ids = sorted(set(list(fc_data.keys()) + list(rq_data.keys())))
    if not all_pu_ids:
        return []

    # Get planning unit names
    pus = PlanningUnit.query.filter(PlanningUnit.id.in_(all_pu_ids)).all()
    pu_names = {pu.id: pu.name for pu in pus}

    months = list(range(1, 13))
    plan = []

    for pu_id in all_pu_ids:
        lob_name = pu_names.get(pu_id, f"Unit {pu_id}")
        lob_plan = {"lob": lob_name, "months": []}

        for m in months:
            wd = _working_days_in_month(year, m)
            working_hrs = wd * 7.5

            # Forecast data
            fc_month = fc_data.get(pu_id, {}).get(m, {})
            fc_offered = fc_month.get("offered", 0)
            avg_aht = fc_month.get("aht", 0)
            fc_answered = round(fc_offered * ar)

            # Requirements data — psih_raw is total agent-half-hours
            rq_month = rq_data.get(pu_id, {}).get(m, {})
            # total is sum of agents_required across intervals;
            # each interval is 30 min, so total × 0.5 = agent-hours = PSIH
            psih_raw = rq_month.get("total", 0) * 0.5
            psih_shr = psih_raw / (1 - shr) if psih_raw > 0 else 0
            fte_req = round(psih_shr / working_hrs, 1) if working_hrs > 0 and psih_shr > 0 else 0

            actual_hc = hc_by_pu.get(pu_id, 0)
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
