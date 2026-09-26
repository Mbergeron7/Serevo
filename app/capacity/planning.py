"""
capacity/planning.py
--------------------
Capacity planning business logic for Serevo.

Handles:
  - PeopleWare API calls (forecast, requirements, employees)
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
API_NEW    = os.environ.get("PW_API_NEW",    "https://api.peopleware.com")
API_LEGACY = os.environ.get("PW_API_LEGACY", "https://legacy-api.peopleware.com/v1")

PW_TOKEN      = os.environ.get("PW_API_TOKEN", "")
PW_UTC_OFFSET = int(os.environ.get("PW_UTC_OFFSET", "-4"))
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

def _pw_headers():
    token = PW_TOKEN or os.environ.get("PW_API_TOKEN", "")
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json",
    }


def _to_utc(local_dt, utc_offset=None):
    """Convert local datetime to UTC ISO string for API calls."""
    if utc_offset is None:
        utc_offset = PW_UTC_OFFSET
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
    token = os.environ.get("PW_API_TOKEN", "")
    if not token:
        return {"ok": False, "error": "PW_API_TOKEN not set in environment"}

    results = {}

    # Test legacy API
    try:
        r = requests.get(f"{API_LEGACY}/employees",
                         headers=_pw_headers(), timeout=15)
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
        r = requests.get(url, headers=_pw_headers(), timeout=15)
        results["new_api"] = {"status": r.status_code, "ok": r.status_code == 200}
    except Exception as e:
        results["new_api"] = {"status": 0, "ok": False, "error": str(e)}

    results["ok"] = results.get("legacy", {}).get("ok", False) and \
                    results.get("new_api", {}).get("ok", False)
    return results


def fetch_forecast_for_day(workload_name, workload_id, day_date, utc_offset=None):
    """Fetch forecast intervals for one workload for one day."""
    if utc_offset is None:
        utc_offset = PW_UTC_OFFSET

    open_dt = _biz_open(day_date)
    close_dt = _biz_close(day_date)
    url = (f"{API_NEW}/workloads/{workload_id}/forecasts"
           f"?startTime={_to_utc(open_dt, utc_offset)}"
           f"&endTime={_to_utc(close_dt, utc_offset)}")
    try:
        r = requests.get(url, headers=_pw_headers(), timeout=20)
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
                         headers=_pw_headers(), timeout=20)
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
        r = requests.get(url, headers=_pw_headers(), timeout=20)
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


def fetch_employees(page_size=500):
    """Fetch employee roster from new API."""
    employees = []
    page = 1
    while True:
        try:
            url = f"{API_NEW}/people?include=employeeId&page={page}&pageSize={page_size}"
            r = requests.get(url, headers=_pw_headers(), timeout=30)
            if r.status_code != 200:
                break
            data = r.json()
            batch = data.get("employees") or data.get("data") or data.get("people") or []
            if isinstance(batch, dict):
                batch = [batch]
            if not batch:
                break
            employees.extend(batch)
            meta = data.get("meta") or data.get("pagination") or {}
            total = meta.get("total") or meta.get("totalCount") or 0
            if total and len(employees) >= int(total):
                break
            if len(batch) < page_size:
                break
            page += 1
        except Exception as e:
            log.warning(f"Employee fetch error page {page}: {e}")
            break
    return employees


# =========================================================
# GOOGLE SHEET WRITE HELPERS
# =========================================================

def write_forecast_to_sheet(forecast_ws, workload_name, day_intervals):
    """Write forecast intervals for one workload+day to the FORECAST RAW sheet."""
    if not day_intervals:
        return 0

    all_data = forecast_ws.get_all_values()
    headers = all_data[2] if len(all_data) > 2 else []

    ts_col = 1
    lob_col = None
    for i, h in enumerate(headers):
        if h.strip() == workload_name:
            lob_col = i
            break

    if lob_col is None:
        new_col = len(headers)
        forecast_ws.update_cell(3, new_col + 1, workload_name)
        lob_col = new_col
        headers.append(workload_name)

    ts_index = {}
    for row_i, row in enumerate(all_data[3:], start=4):
        if ts_col < len(row) and row[ts_col]:
            ts_index[str(row[ts_col])[:16]] = row_i

    written = 0
    for (slot_dt, offered, aht) in day_intervals:
        ts_str = slot_dt.strftime("%Y-%m-%d %H:%M")
        row_num = ts_index.get(ts_str)
        if row_num is None:
            next_row = len(all_data) + 1
            forecast_ws.update_cell(next_row, ts_col + 1, ts_str)
            all_data.append([""] * max(len(headers), lob_col + 1))
            ts_index[ts_str] = next_row
            row_num = next_row
        forecast_ws.update_cell(row_num, lob_col + 1, offered)
        written += 1

    return written


def write_requirements_to_sheet(req_ws, day_requirements):
    """Write requirements for one planning unit+day to REQUIREMENTS RAW sheet."""
    if not day_requirements:
        return 0

    all_data = req_ws.get_all_values()
    headers = all_data[2] if len(all_data) > 2 else []
    ts_col = 1

    ts_index = {}
    for row_i, row in enumerate(all_data[3:], start=4):
        if ts_col < len(row) and row[ts_col]:
            ts_index[str(row[ts_col])[:16]] = row_i

    written = 0
    for lob_name, intervals in day_requirements.items():
        lob_col = None
        for i, h in enumerate(headers):
            if h.strip() == lob_name:
                lob_col = i
                break
        if lob_col is None:
            new_col = len(headers)
            req_ws.update_cell(3, new_col + 1, lob_name)
            lob_col = new_col
            headers.append(lob_name)

        for (slot_dt, agents) in intervals:
            ts_str = slot_dt.strftime("%Y-%m-%d %H:%M")
            row_num = ts_index.get(ts_str)
            if row_num is None:
                next_row = len(all_data) + 1
                req_ws.update_cell(next_row, ts_col + 1, ts_str)
                all_data.append([""] * max(len(headers), lob_col + 1))
                ts_index[ts_str] = next_row
                row_num = next_row
            req_ws.update_cell(row_num, lob_col + 1, agents)
            written += 1

    return written


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
