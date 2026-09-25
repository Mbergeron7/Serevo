"""
capacity_planner.py
--------------------
PeopleWare Capacity Planning — Flask-integrated module.

Handles:
  - API calls to PeopleWare (forecast, requirements, employees)
  - Google Sheets caching (FORECAST RAW, REQUIREMENTS RAW, EMPLOYEES)
  - Capacity plan calculations matching the Excel file logic
  - Capacity summary calculations
  - Headcount by LOB view
"""

import os
import logging
import datetime
from datetime import date
import urllib.request
import urllib.error
import json
import base64
from collections import defaultdict

log = logging.getLogger(__name__)

# =========================================================
# CONFIGURATION
# =========================================================

API_NEW    = "https://api.peopleware.com"
API_LEGACY = "https://legacy-api.peopleware.com/v1"

EXCLUDED_WORKLOADS = {
    "All Queues Test",
    "SS CCA Payment",
    # SS CCA Combined/EN/FR are duplicates of SS CCA CC Combined/EN/FR — exclude to avoid
    # double-counting when both columns exist in the FORECAST RAW sheet.
    "SS CCA Combined",
    "SS CCA Combined EN",
    "SS CCA Combined FR",
}

# Display name overrides — raw sheet column names → human-friendly names
# shown on the Capacity Plan and Summary pages.
LOB_DISPLAY_NAMES = {
    "SS CCA CC Combined":   "SS Case Management Combined",
    "SS CCA CC EN":         "SS Case Management EN",
    "SS CCA CC FR":         "SS Case Management FR",
    "SS CCA Combined":      "SS Case Management Combined",   # same data as CC Combined
    "SS CCA Combined EN":   "SS Case Management EN",         # same data as CC EN
    "SS CCA Combined FR":   "SS Case Management FR",         # same data as CC FR
    "SS CCA Store Combined":"SS Case Management Store Combined",
    "SS CCA Store EN":      "SS Case Management Store EN",
    "SS CCA Store FR":      "SS Case Management Store FR",
}

def _display_lob(raw_name):
    """Return the human-friendly display name for a LOB, or the raw name if no mapping."""
    return LOB_DISPLAY_NAMES.get(raw_name, raw_name)

# Workload ID → Name mapping (from VBA hardcoded list)
WORKLOADS = [
    ("e2d2558b-0050-4719-8040-f55d6ad10c25", "I.T Support"),
    ("c3184f43-b095-4b13-a5be-371c48b83ead", "MoveBuddy"),
    ("e13311bb-714d-498c-a27b-dc6d9bc7576d", "PS Care Combined"),
    ("6913778e-ce1d-4ddb-97d8-93044d65f626", "PS Care EN"),
    ("63ee18a5-8eeb-4fd8-952b-ba9166db3fdd", "PS Care FR"),
    ("286243f3-6059-4eb4-99fb-286476921064", "PS Case Manager"),
    ("e19109ca-a910-4189-b1df-64457fb0289d", "PS Sales & Care Combined"),
    ("42248eab-5f8f-4b1e-a3ac-f2c6fe79e898", "PS Sales Combined"),
    ("1aaeee54-85ef-447d-a2b9-f09a785806c7", "PS Sales EN"),
    ("99f9cec2-844d-48fa-84c8-b4af5605896e", "PS Sales FR"),
    ("450d20b5-1105-4316-9082-f3ca4b5f9cdf", "SS CCA CC Combined"),
    ("978881de-68c2-4f63-b2dd-b1892471a29c", "SS CCA CC EN"),
    ("0e45cee2-7eff-4787-afda-00fee763ad17", "SS CCA CC FR"),
    ("1c09003e-aba4-4c8e-bb25-7b8a5e583aaa", "SS CCA Combined"),
    ("254aef98-e3b4-4c3d-9519-2c555194be0e", "SS CCA Combined EN"),
    ("511cc324-dc8a-4e2f-8a50-52f1f2ae1c81", "SS CCA Combined FR"),
    ("81fb4718-91cf-4de3-b73b-4ae3f3da1757", "SS CCA Store Combined"),
    ("399919c9-4285-4e5f-862f-4cca5765612f", "SS CCA Store EN"),
    ("fa0afb59-33fa-4a83-90eb-eb21f6b36c13", "SS CCA Store FR"),
    ("455a57fb-6664-4907-ba52-0fea4d123d3e", "SS Sales Combined"),
    ("8cbe2728-a958-4677-930a-2e2554668004", "SS Sales EN"),
    ("d0a075a5-5a73-46b0-8c63-206dc6c0b275", "SS Sales FR"),
    ("da69b74f-3b0f-460a-b87a-576e5008d2a7", "Web Leads PS Combined"),
    ("9f56e267-68de-492b-a2c9-ef219feed864", "Web Leads PS EN"),
    ("4d94afbe-8d92-4f04-a17b-f0843ec3beed", "Web Leads PS FR"),
    ("b478b01d-9166-4108-a6db-842008706234", "Web Leads PS & SS Combined"),
    ("99db45c7-155e-484a-909c-cdb69a0581b8", "Web Leads SS Combined"),
    ("5f31de17-ba2e-4e11-88d0-548a1b4669ed", "Web Leads SS EN"),
    ("e95ff578-25e9-40b3-956f-0efd434c85ce", "Web Leads SS FR"),
    ("26fc920a-1181-4b62-a71f-b43b104d8c2a", "Web Leads SS Inbound Combined"),
    ("8e61f24b-1fd6-4815-adbf-e560736d41af", "Web Leads SS Inbound EN"),
    ("39221bbe-3524-44bb-b348-86e0c5ffe1e0", "Web Leads SS Inbound FR"),
    ("1c09003e-aba4-4c8e-bb25-7b8a5e583aaa", "SS Case Management Combined"),
    ("254aef98-e3b4-4c3d-9519-2c555194be0e", "SS Case Management Combined EN"),
    ("511cc324-dc8a-4e2f-8a50-52f1f2ae1c81", "SS Case Management Combined FR"),
]

# Active workloads (excludes excluded ones)
ACTIVE_WORKLOADS = [(wid, name) for wid, name in WORKLOADS
                    if name not in EXCLUDED_WORKLOADS]


# =========================================================
# API HELPERS
# =========================================================

def get_token():
    """Get decoded PeopleWare API token from environment."""
    raw = os.environ.get("PW_API_TOKEN", "")
    if not raw:
        return ""
    try:
        decoded = base64.b64decode(raw).decode("utf-8")
        return decoded.replace("\n", "").replace("\r", "").strip()
    except Exception:
        return raw.replace("\n", "").replace("\r", "").strip()

def get_utc_offset():
    try:
        return int(os.environ.get("PW_UTC_OFFSET", "-4"))
    except Exception:
        return -4

def pw_get(url, token=None):
    """Make authenticated GET request to PeopleWare API."""
    if token is None:
        token = get_token()
    if not token:
        return None, "No API token configured"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8")), None
    except urllib.error.HTTPError as e:
        msg = f"HTTP {e.code}: {e.reason}"
        try:
            msg += f" — {e.read().decode('utf-8')[:200]}"
        except Exception:
            pass
        return None, msg
    except Exception as e:
        return None, str(e)

def to_utc(local_dt, utc_offset):
    """Convert local datetime to UTC ISO string."""
    utc_dt = local_dt - datetime.timedelta(hours=utc_offset)
    return utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

def biz_open(is_weekend=False):
    return "09:00" if is_weekend else "08:00"

def biz_close(is_weekend=False):
    return "22:00"


# =========================================================
# API DATA FETCHERS
# =========================================================

def fetch_employees(token=None):
    """Fetch all employees from PeopleWare legacy API."""
    data, err = pw_get(f"{API_LEGACY}/employees", token)
    if err:
        return None, err
    employees = data if isinstance(data, list) else data.get("employees", [])
    return employees, None

def fetch_planning_units(token=None):
    """Fetch all planning units from PeopleWare legacy API."""
    data, err = pw_get(f"{API_LEGACY}/planning_units", token)
    if err:
        return None, err
    units = data if isinstance(data, list) else data.get("planning_units", [])
    return units, None

def fetch_forecast_day(workload_id, date, utc_offset, token=None):
    """
    Fetch 30-min interval forecast for one workload on one day.
    Returns list of {time, offered, aht} dicts.
    """
    is_weekend = date.weekday() >= 5
    open_h  = biz_open(is_weekend)
    close_h = biz_close(is_weekend)

    open_dt  = datetime.datetime.combine(date, datetime.time(
        int(open_h.split(":")[0]), int(open_h.split(":")[1])))
    close_dt = datetime.datetime.combine(date, datetime.time(
        int(close_h.split(":")[0]), int(close_h.split(":")[1])))

    start_utc = to_utc(open_dt,  utc_offset)
    end_utc   = to_utc(close_dt, utc_offset)

    url  = f"{API_NEW}/workloads/{workload_id}/forecasts?startTime={start_utc}&endTime={end_utc}"
    data, err = pw_get(url, token)
    if err or not data:
        return [], err

    intervals = []
    try:
        fc_data = data.get("data", [])
        for item in fc_data:
            forecasts = item.get("forecasts", {})
            auto_fc   = forecasts.get("auto") or forecasts.get("operational", {})
            offered   = auto_fc.get("offered", {})
            aht_val   = auto_fc.get("aht", 0)
            count     = offered.get("mean", 0) if isinstance(offered, dict) else offered
            ts        = item.get("startTime", "")
            intervals.append({
                "time":    ts,
                "offered": float(count or 0),
                "aht":     float(aht_val or 0),
            })
    except Exception as e:
        log.warning(f"Forecast parse error: {e}")

    return intervals, None

def fetch_requirements_day(planning_unit_id, date, token=None):
    """
    Fetch 30-min interval requirements for one planning unit on one day.
    Returns list of {time, agents_required} dicts.
    """
    date_str = date.strftime("%Y-%m-%d")
    url      = f"{API_LEGACY}/planning_units/{planning_unit_id}/requirements/{date_str}"
    data, err = pw_get(url, token)
    if err or not data:
        return [], err

    intervals = []
    try:
        items = data if isinstance(data, list) else (
            data.get("requirements") or data.get("data") or
            data.get("planning_unit_requirements") or [])
        if isinstance(items, dict) and "values" in items:
            items = [items]
        for item in items:
            values = item.get("values", [])
            for v in values:
                intervals.append({
                    "time":             v.get("startTime", ""),
                    "agents_required":  float(v.get("agentsRequired", 0) or 0),
                })
    except Exception as e:
        log.warning(f"Requirements parse error: {e}")

    return intervals, None


# =========================================================
# GOOGLE SHEETS CACHE
# =========================================================

def get_cap_sheet():
    """Get the capacity planning Google Sheet."""
    try:
        import gspread
        from oauth2client.service_account import ServiceAccountCredentials
        scope  = ["https://spreadsheets.google.com/feeds",
                  "https://www.googleapis.com/auth/drive"]
        creds  = ServiceAccountCredentials.from_json_keyfile_name(
            os.environ.get("SERVICE_ACCOUNT_FILE", "service_account.json"), scope)
        client = gspread.authorize(creds)
        key    = os.environ.get("CAPACITY_SHEET_KEY", "")
        if not key:
            return None, "CAPACITY_SHEET_KEY not set"
        doc = client.open_by_key(key)
        return doc, None
    except Exception as e:
        return None, str(e)

def write_forecast_to_sheet(doc, workload_name, intervals, append=True):
    """Write forecast intervals to FORECAST RAW sheet."""
    try:
        ws = doc.worksheet("FORECAST RAW")
        if not append:
            ws.clear()
            ws.append_row(["timestamp", "workload", "offered", "aht"])
        rows = [[i["time"], workload_name, i["offered"], i["aht"]]
                for i in intervals]
        if rows:
            ws.append_rows(rows, value_input_option="RAW")
        return True, None
    except Exception as e:
        return False, str(e)

def write_requirements_to_sheet(doc, pu_name, intervals, append=True):
    """Write requirements intervals to REQUIREMENTS RAW sheet."""
    try:
        ws = doc.worksheet("REQUIREMENTS RAW")
        if not append:
            ws.clear()
            ws.append_row(["timestamp", "planning_unit", "agents_required"])
        rows = [[i["time"], pu_name, i["agents_required"]]
                for i in intervals]
        if rows:
            ws.append_rows(rows, value_input_option="RAW")
        return True, None
    except Exception as e:
        return False, str(e)

def write_employees_to_sheet(doc, employees):
    """Write employee roster to EMPLOYEES sheet."""
    try:
        ws = doc.worksheet("EMPLOYEES")
        ws.clear()
        if not employees:
            return True, None
        # Get all keys from first employee for headers
        headers = list(employees[0].keys()) if employees else []
        ws.append_row(headers)
        rows = [[str(emp.get(h, "")) for h in headers] for emp in employees]
        if rows:
            ws.append_rows(rows, value_input_option="RAW")
        return True, None
    except Exception as e:
        return False, str(e)

def read_forecast_raw(doc):
    """
    Read FORECAST RAW sheet and aggregate to monthly totals.
    Supports wide format: Timestamp | LOB1 | LOB2 | ...
    Supports long format: timestamp | workload | offered | aht
    Aggregates at read time to avoid memory explosion on free tier.
    """
    try:
        ws   = doc.worksheet("FORECAST RAW")
        data = ws.get_all_values()
        if not data or len(data) < 2:
            log.warning("FORECAST RAW: empty or only headers")
            return [], None
        headers = data[0]
        if not headers or not headers[0]:
            return [], None

        second_col     = headers[1].strip().lower() if len(headers) > 1 else ""
        is_long_format = second_col in ("workload", "lob", "planning_unit")
        log.info(f"FORECAST RAW: {len(data)-1} rows, "
                 f"format={'long' if is_long_format else 'wide'}")

        # Aggregate directly to {(lob, month): {offered, aht_weight, count}}
        monthly = defaultdict(lambda: {"offered": 0.0, "aht_weight": 0.0, "count": 0})

        if is_long_format:
            for row in data[1:]:
                if not row or not row[0]: continue
                ts = parse_ts(row[0])
                if not ts: continue
                lob     = (row[1] if len(row) > 1 else "").strip()
                offered = float(row[2] or 0) if len(row) > 2 else 0.0
                aht     = float(row[3] or 0) if len(row) > 3 else 0.0
                if not lob: continue
                key = (lob, ts.strftime("%Y-%m"))
                monthly[key]["offered"]    += offered
                monthly[key]["aht_weight"] += offered * aht
                monthly[key]["count"]      += 1
        else:
            lob_names = [_display_lob(h) if h not in EXCLUDED_WORKLOADS else None
                         for h in headers[1:]]
            for row in data[1:]:
                if not row or not row[0]: continue
                ts = parse_ts(row[0])
                if not ts: continue
                month = ts.strftime("%Y-%m")
                for i, lob in enumerate(lob_names):
                    if not lob: continue
                    val = row[i + 1] if i + 1 < len(row) else "0"
                    try:    offered = float(val or 0)
                    except: offered = 0.0
                    key = (lob, month)
                    monthly[key]["offered"] += offered
                    monthly[key]["count"]   += 1

        # Convert to list of dicts — one per LOB/month
        rows = []
        for (lob, month), vals in monthly.items():
            offered = vals["offered"]
            avg_aht = (vals["aht_weight"] / offered) if offered > 0 else 0.0
            rows.append({
                "timestamp": f"{month}-01 00:00:00",
                "workload":  lob,
                "offered":   offered,
                "aht":       avg_aht,
                "_count":    vals["count"],
            })

        log.info(f"FORECAST RAW: aggregated to {len(rows)} LOB/month rows")
        return rows, None
    except Exception as e:
        log.exception(f"read_forecast_raw error: {e}")
        return [], str(e)

def read_requirements_raw(doc):
    """
    Read REQUIREMENTS RAW sheet and aggregate to monthly totals.
    Supports wide format: Timestamp | PU1 | PU2 | ...
    Supports long format: timestamp | planning_unit | agents_required
    Aggregates at read time to avoid memory explosion on free tier.
    """
    try:
        ws   = doc.worksheet("REQUIREMENTS RAW")
        data = ws.get_all_values()
        if not data or len(data) < 2:
            log.warning("REQUIREMENTS RAW: empty or only headers")
            return [], None
        headers = data[0]
        if not headers or not headers[0]:
            return [], None

        second_col     = headers[1].strip().lower() if len(headers) > 1 else ""
        is_long_format = second_col in ("planning_unit", "workload", "lob")
        log.info(f"REQUIREMENTS RAW: {len(data)-1} rows, "
                 f"format={'long' if is_long_format else 'wide'}")

        # Aggregate: {(pu, month): {total_agents, peak, count}}
        monthly = defaultdict(lambda: {"total": 0.0, "peak": 0.0, "count": 0})

        if is_long_format:
            for row in data[1:]:
                if not row or not row[0]: continue
                ts = parse_ts(row[0])
                if not ts: continue
                pu     = (row[1] if len(row) > 1 else "").strip()
                agents = float(row[2] or 0) if len(row) > 2 else 0.0
                if not pu: continue
                key = (pu, ts.strftime("%Y-%m"))
                monthly[key]["total"] += agents
                monthly[key]["peak"]   = max(monthly[key]["peak"], agents)
                monthly[key]["count"] += 1
        else:
            pu_names = headers[1:]
            for row in data[1:]:
                if not row or not row[0]: continue
                ts = parse_ts(row[0])
                if not ts: continue
                month = ts.strftime("%Y-%m")
                for i, pu in enumerate(pu_names):
                    if not pu: continue
                    val = row[i + 1] if i + 1 < len(row) else "0"
                    try:    agents = float(val or 0)
                    except: agents = 0.0
                    key = (pu, month)
                    monthly[key]["total"] += agents
                    monthly[key]["peak"]   = max(monthly[key]["peak"], agents)
                    monthly[key]["count"] += 1

        # Convert to list — one per PU/month
        rows = []
        for (pu, month), vals in monthly.items():
            rows.append({
                "timestamp":       f"{month}-01 00:00:00",
                "planning_unit":   pu,
                "agents_required": vals["total"] / vals["count"] if vals["count"] > 0 else 0,
                "_peak":           vals["peak"],
                "_count":          vals["count"],
            })

        log.info(f"REQUIREMENTS RAW: aggregated to {len(rows)} PU/month rows")
        return rows, None
    except Exception as e:
        log.exception(f"read_requirements_raw error: {e}")
        return [], str(e)

def read_employees_raw(doc):
    """
    Read EMPLOYEES sheet — captures skill date columns for monthly HC calculation.
    Columns: Employee ID, First Name, Last Name, Start Date, End Date, Planning Unit,
             Deleted, Status, Latest Skill Start, Latest Skill Name, Latest Skill End,
             All Skills, etc.
    """
    try:
        ws   = doc.worksheet("EMPLOYEES")
        data = ws.get_all_values()
        if not data or len(data) < 2:
            return [], None
        headers = data[0]
        rows    = []
        for row in data[1:]:
            if not any(row): continue
            emp = dict(zip(headers, row))
            rows.append({
                "status":       emp.get("Status", "").strip(),
                "deleted":      emp.get("Deleted", "").strip(),
                "planningUnit": emp.get("Planning Unit", "").strip(),
                "firstName":    emp.get("First Name", ""),
                "lastName":     emp.get("Last Name", ""),
                "employeeId":   emp.get("Employee ID", ""),
                "skillName":    emp.get("Latest Skill Name", "").strip(),
                "skillStart":   (emp.get("Planning Unit Start Date", "") or
                                 emp.get("Latest Skill Start", "")).strip(),
                "skillEnd":     emp.get("Latest Skill End", "").strip(),
                "allSkills":    emp.get("All Skills", "").strip(),
                "empStart":     emp.get("Start Date", "").strip(),
                "empEnd":       emp.get("End Date", "").strip(),
            })
        log.info(f"EMPLOYEES: {len(rows)} rows read")
        return rows, None
    except Exception as e:
        log.exception(f"read_employees_raw error: {e}")
        return [], str(e)

def get_refresh_status(doc):
    """Read data status from sheets — works for both wide and long formats."""
    status = {}
    for sheet_name in ["FORECAST RAW", "REQUIREMENTS RAW", "EMPLOYEES", "AHT RAW"]:
        try:
            ws   = doc.worksheet(sheet_name)
            data = ws.get_all_values()
            rows = len(data) - 1 if len(data) > 1 else 0
            status[sheet_name] = {"rows": rows, "has_data": rows > 0}
        except Exception:
            status[sheet_name] = {"rows": 0, "has_data": False}
    return status

def read_aht_raw(doc):
    """
    Read AHT RAW sheet and aggregate to monthly average AHT per LOB.
    Wide format: Timestamp | LOB1 | LOB2 | ...
    Values are AHT in seconds — blank = no data for that interval.
    Returns: {lob_name: {month_str: avg_aht_seconds}}
    Falls back to {} if sheet doesn't exist or has no data.
    """
    try:
        ws   = doc.worksheet("AHT RAW")
        data = ws.get_all_values()
        if not data or len(data) < 2:
            log.warning("AHT RAW: empty or only headers — will use 470s default")
            return {}

        headers   = data[0]
        lob_names = [_display_lob(h) if h not in EXCLUDED_WORKLOADS else None
                     for h in headers[1:]]
        log.info(f"AHT RAW: {len(data)-1} rows, {len(lob_names)} LOBs")

        # Aggregate: {lob: {month: [values]}}
        monthly = defaultdict(lambda: defaultdict(list))

        for row in data[1:]:
            if not row or not row[0]: continue
            ts = parse_ts(row[0])
            if not ts: continue
            month = ts.strftime("%Y-%m")
            for i, lob in enumerate(lob_names):
                if not lob: continue
                val = row[i + 1] if i + 1 < len(row) else ""
                if val == "" or val is None: continue
                try:
                    fval = float(val)
                    if fval > 0:
                        monthly[lob][month].append(fval)
                except Exception:
                    continue

        # Compute monthly averages
        result = {}
        for lob, months in monthly.items():
            result[lob] = {}
            for month, vals in months.items():
                if vals:
                    result[lob][month] = round(sum(vals) / len(vals))

        lob_count   = len(result)
        month_count = len(set(m for lob in result.values() for m in lob))
        log.info(f"AHT RAW: {lob_count} LOBs aggregated across {month_count} months")

        # Log a sample so we can verify in Render logs
        for sample_lob in ["SS Sales Combined", "SS Sales EN", "PS Care Combined"]:
            if sample_lob in result:
                months_sample = dict(list(result[sample_lob].items())[:3])
                log.info(f"AHT sample — {sample_lob}: {months_sample}")

        return result

    except Exception as e:
        log.exception(f"read_aht_raw error: {e} — will use 470s default")
        return {}


# =========================================================
# CAPACITY CALCULATIONS
# =========================================================

def parse_ts(ts_str):
    """Parse timestamp — handles datetime objects, ISO strings, Excel serials."""
    if not ts_str:
        return None
    # Already a datetime
    if isinstance(ts_str, datetime.datetime):
        return ts_str
    if isinstance(ts_str, datetime.date):
        return datetime.datetime.combine(ts_str, datetime.time.min)
    ts_str = str(ts_str).strip()
    if not ts_str:
        return None
    # Excel serial number
    try:
        serial = float(ts_str)
        if 40000 < serial < 60000:
            return datetime.datetime(1899, 12, 30) + datetime.timedelta(days=serial)
    except Exception:
        pass
    # String formats
    for fmt in (
        "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S+00:00",
        "%Y-%m-%d %H:%M", "%Y-%m-%d",
        "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%m/%d/%Y",
        "%d/%m/%Y %H:%M:%S", "%d/%m/%Y",
    ):
        try:
            return datetime.datetime.strptime(ts_str, fmt)
        except Exception:
            continue
    try:
        return datetime.datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except Exception:
        return None

def _working_hours_per_month(year, month):
    """
    Calculate available working hours per FTE per month.
    StorageVault operates 7 days/week — agents work any 5 of 7 days.
    Standard shift: 8.5 hrs scheduled, 0.5 hr unpaid lunch = 8.0 hrs paid.
    Working days per month = calendar days × (5/7) to account for 2 days off per week.
    """
    import calendar
    total_days   = calendar.monthrange(year, month)[1]
    working_days = total_days * (5 / 7)   # avg working days for 7-day operation
    return working_days * 8.0             # 8 paid hours per day

def build_capacity_plan(forecast_data, requirements_data, employees_data,
                        shrinkage=0.30, occupancy=0.85, answer_rate=0.92,
                        aht_data=None):
    """
    Build monthly capacity plan.
    Data is pre-aggregated by read_forecast_raw/read_requirements_raw
    so each row represents one LOB/month combination.
    """
    # Group forecast by workload + month (already one row per LOB/month)
    fc_by_lob_month = {}  # {lob: {month: {offered, aht, count}}}
    for row in forecast_data:
        ts = parse_ts(str(row.get("timestamp", "")))
        if not ts: continue
        lob   = str(row.get("workload", "")).strip()
        month = ts.strftime("%Y-%m")
        if not lob: continue
        if lob not in fc_by_lob_month:
            fc_by_lob_month[lob] = {}
        fc_by_lob_month[lob][month] = {
            "offered": float(row.get("offered", 0) or 0),
            "aht":     float(row.get("aht", 0) or 0),
            "count":   int(row.get("_count", 1) or 1),
        }

    # Group requirements by planning unit + month
    req_by_pu_month = {}  # {pu: {month: {avg_agents, peak, count}}}
    for row in requirements_data:
        ts = parse_ts(str(row.get("timestamp", "")))
        if not ts: continue
        pu    = str(row.get("planning_unit", "")).strip()
        month = ts.strftime("%Y-%m")
        if not pu: continue
        if pu not in req_by_pu_month:
            req_by_pu_month[pu] = {}
        req_by_pu_month[pu][month] = {
            "avg_agents": float(row.get("agents_required", 0) or 0),
            "peak":       float(row.get("_peak", 0) or 0),
            "count":      int(row.get("_count", 1) or 1),
        }

    # LOB name → Planning Unit name mapping (from VBA SVC_GetLOBDefs)
    # When skill name doesn't match, fall back to Planning Unit
    LOB_TO_PU = {
        "ss sales combined":              "ss sales",
        "ss sales en":                    "ss sales",
        "ss sales fr":                    "ss sales",
        "ss case management combined":    "ss case management",
        "ss case management combined en": "ss case management",
        "ss case management combined fr": "ss case management",
        "ss cca cc combined":             "ss case management",
        "ss cca cc en":                   "ss case management",
        "ss cca cc fr":                   "ss case management",
        "ss cca combined":                "ss case management",
        "ss cca combined en":             "ss case management",
        "ss cca combined fr":             "ss case management",
        "ss cca store combined":          "ss cca store",
        "ss cca store en":                "ss cca store",
        "ss cca store fr":                "ss cca store",
        "ps sales combined":              "ps sales",
        "ps sales en":                    "ps sales",
        "ps sales fr":                    "ps sales",
        "ps care combined":               "ps care",
        "ps care en":                     "ps care",
        "ps care fr":                     "ps care",
        "ps case manager":                "ps case manager",
        "ps sales & care combined":       "ps sales",
        "web leads ss combined":          "web leads ss",
        "web leads ss en":                "web leads ss",
        "web leads ss fr":                "web leads ss",
        "web leads ss inbound combined":  "web leads ss inbound",
        "web leads ss inbound en":        "web leads ss inbound",
        "web leads ss inbound fr":        "web leads ss inbound",
        "web leads ps combined":          "web leads ps",
        "web leads ps en":                "web leads ps",
        "web leads ps fr":                "web leads ps",
        "web leads ps & ss combined":     "web leads ps",
        "i.t support":                    "i.t support",
        "movebuddy":                      "movebuddy",
    }

    # Build skill-date-aware headcount function
    def _parse_skill_date(val):
        if not val or str(val).strip() in ("", "None", "nan"):
            return None
        s = str(val).strip()[:10]
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y"):
            try:
                return datetime.datetime.strptime(s, fmt).date()
            except Exception:
                continue
        return None

    # Include ALL non-deleted employees (regardless of current Active/Inactive status)
    # and rely on empStart/empEnd dates to determine who was employed in each month.
    # Pre-filtering to status=="active" was wrong for historical months because
    # departed employees are now marked Inactive but WERE active in earlier months.
    all_employees = [
        emp for emp in employees_data
        if str(emp.get("deleted", "")).strip().lower()
           not in ("true", "yes", "1", "deleted")
    ]

    log.info(f"build_capacity_plan: {len(fc_by_lob_month)} LOBs, "
             f"{len(req_by_pu_month)} PUs, {len(all_employees)} total employees (incl. historical)")

    # LOBs that represent an entire planning unit (count all PU members)
    # vs sub-LOBs (EN/FR) which count by exact skill name only
    # Skill name aliases — maps actual PeopleWare skill names to LOB names
    SKILL_ALIASES = {
        "ss cm combined":       "ss case management combined",
        "ss cm combined en":    "ss case management combined en",
        "ss cm combined fr":    "ss case management combined fr",
        "web leads ss inbound": "web leads ss inbound combined",
        "web leads ps":         "web leads ps combined",
        "web leads ss":         "web leads ss combined",
    }

    COMBINED_LOBS = {
        "ss sales combined", "ss case management combined",
        "ss case management combined en", "ss case management combined fr",
        "ss cca cc combined", "ss cca combined",
        "ss cca store combined", "ps sales combined",
        "ps care combined", "ps sales & care combined",
        "web leads ss combined", "web leads ss inbound combined",
        "web leads ps combined", "web leads ps & ss combined",
        "i.t support", "movebuddy", "ps case manager",
    }

    _hc_cache = {}

    def _lang_of(skill_lower):
        """Classify an employee's (resolved) skill name by language side.
        A trailing ' en'/' english' → EN, ' fr'/' french' → FR, else Combined
        (bilingual agents and any non-language-split skill)."""
        s = skill_lower.strip()
        if s.endswith(" en") or s.endswith(" english"):
            return "en"
        if s.endswith(" fr") or s.endswith(" french"):
            return "fr"
        return "combined"

    _EMPTY_HC = {"combined": 0, "en": 0, "fr": 0, "total": 0, "has_lang": False}

    def _hc_for_lob_month(lob, month_str):
        """Skill-date-aware headcount for a LOB/month, broken out by language.
        Returns {combined, en, fr, total, has_lang}. 'total' is the full
        headcount (unchanged from before); combined/en/fr split the same
        matched agents by their skill language so LOBs with English and
        French sides can be reported three ways. has_lang is True when any
        EN- or FR-specific agents were matched."""
        if (lob, month_str) in _hc_cache:
            return _hc_cache[(lob, month_str)]
        try:
            year, mon   = int(month_str[:4]), int(month_str[5:7])
            month_start = datetime.date(year, mon, 1)
            month_end   = (datetime.date(year, 12, 31) if mon == 12
                           else datetime.date(year, mon + 1, 1) - datetime.timedelta(days=1))
        except Exception:
            return dict(_EMPTY_HC)

        lob_lower   = lob.lower()
        mapped_pu   = LOB_TO_PU.get(lob_lower, lob_lower)
        is_combined = lob_lower in COMBINED_LOBS

        counts = {"combined": 0, "en": 0, "fr": 0}
        for emp in all_employees:
            raw_skill = str(emp.get("skillName",   "")).strip().lower()
            pu        = str(emp.get("planningUnit", "")).strip().lower()
            # Resolve alias — e.g. "ss cm combined" → "ss case management combined"
            skill = SKILL_ALIASES.get(raw_skill, raw_skill)

            if is_combined:
                # Combined LOBs: match by Planning Unit.
                # Active   → count if empStart ≤ month_end
                # LOA      → historical months: count if empEnd > month_end
                #             current/future: exclude (not available capacity)
                # Inactive → count if empEnd > month_end (still employed that month)
                # Note: departed employees have no skill dates in PW, so we use
                # empStart/empEnd as the best available proxy. Current and recent
                # months are accurate; older months may vary slightly due to PU
                # transfer history not being tracked in the current data snapshot.
                if pu != mapped_pu:
                    continue
                emp_status = str(emp.get("status", "")).strip().lower()
                emp_s = _parse_skill_date(emp.get("empStart", ""))
                emp_e = _parse_skill_date(emp.get("empEnd",   ""))

                if emp_status == "active":
                    if emp_s and emp_s > month_end:
                        continue
                elif emp_status in ("loa", "inactive"):
                    is_historical = month_end < date.today()
                    if emp_status == "loa" and not is_historical:
                        continue   # LOA = not available for current/future
                    if not emp_e or emp_e.year >= 2090:
                        continue   # no end date — unknown tenure
                    if emp_e <= month_end:
                        continue   # left before or during this month
                    if emp_s and emp_s > month_end:
                        continue   # hadn't started yet
                else:
                    continue
            else:
                # EN/FR sub-LOBs: match by Planning Unit + Latest Skill Name.
                # "Combined" skill agents (bilingual) count for BOTH EN and FR —
                # they appear in each language queue's available headcount.
                # Apply the same Active/LOA/Inactive + date logic as combined LOBs.
                if pu != mapped_pu:
                    continue

                # Check skill match: Latest Skill Name must be the EN/FR variant
                # or the Combined variant (bilingual agents handle both queues)
                raw_skill_lower = str(emp.get("skillName", "")).strip().lower()
                # e.g. lob_lower = "ss sales en", combined = "ss sales combined"
                lob_base     = " ".join(lob_lower.split()[:-1])  # strip "en"/"fr"
                combined_var = lob_base + " combined"
                is_match = (raw_skill_lower == lob_lower or
                            raw_skill_lower == combined_var)
                if not is_match:
                    continue

                emp_status = str(emp.get("status", "")).strip().lower()
                emp_s = _parse_skill_date(emp.get("empStart", ""))
                emp_e = _parse_skill_date(emp.get("empEnd",   ""))

                if emp_status == "active":
                    ss = _parse_skill_date(emp.get("skillStart", "")) or emp_s
                    if ss and ss > month_end:
                        continue
                elif emp_status in ("loa", "inactive"):
                    is_historical = month_end < date.today()
                    if emp_status == "loa" and not is_historical:
                        continue
                    if not emp_e or emp_e.year >= 2090:
                        continue
                    if emp_e <= month_end:
                        continue
                    if emp_s and emp_s > month_end:
                        continue
                else:
                    continue
                # (date checks already applied above per status branch)
                if emp_e and emp_e.year < 2090 and emp_e < month_start:
                    continue

            counts[_lang_of(skill)] += 1

        result = {
            "combined": counts["combined"],
            "en":       counts["en"],
            "fr":       counts["fr"],
            "total":    counts["combined"] + counts["en"] + counts["fr"],
            "has_lang": (counts["en"] > 0 or counts["fr"] > 0),
        }
        _hc_cache[(lob, month_str)] = result
        return result

    all_months = sorted(set(m for lob in fc_by_lob_month for m in fc_by_lob_month[lob]))
    plan = {}

    for lob, months in fc_by_lob_month.items():
        plan[lob] = {}
        for month in all_months:
            fc = months.get(month)
            if not fc: continue

            total_offered  = fc["offered"]

            # AHT: real monthly average from AHT RAW → forecast weighted avg → 470s default
            real_aht = None
            if aht_data:
                # Try exact match first, then case-insensitive
                real_aht = aht_data.get(lob, {}).get(month)
                if real_aht is None:
                    lob_lower = lob.lower()
                    for aht_lob, aht_months in aht_data.items():
                        if aht_lob.lower() == lob_lower:
                            real_aht = aht_months.get(month)
                            break

            if real_aht and real_aht > 0:
                avg_aht = real_aht
            elif fc["aht"] and fc["aht"] > 0:
                avg_aht = fc["aht"]
            else:
                avg_aht = 470  # VBA default fallback
            interval_count = fc["count"]
            calls_answered = total_offered * answer_rate

            # Requirements lookup — try exact match first, then case-insensitive,
            # then via LOB_TO_PU mapping, then fuzzy (strip dots/spaces)
            lob_lower = lob.lower()
            req = req_by_pu_month.get(lob, {}).get(month)
            if req is None:
                req = req_by_pu_month.get(lob_lower, {}).get(month)
            if req is None:
                pu_name = LOB_TO_PU.get(lob_lower)
                if pu_name:
                    req = req_by_pu_month.get(pu_name, {}).get(month)
                    if req is None:
                        # case-insensitive PU lookup
                        for k, v in req_by_pu_month.items():
                            if k.lower() == pu_name.lower():
                                req = v.get(month)
                                break
            if req is None:
                # Last resort — strip dots and spaces and compare
                lob_clean = lob_lower.replace(".", "").replace(" ", "")
                for k, v in req_by_pu_month.items():
                    if k.lower().replace(".", "").replace(" ", "") == lob_clean:
                        req = v.get(month)
                        break

            if req:
                avg_agents   = req["avg_agents"]
                peak_agents  = req["peak"]
                req_count    = req["count"]
                # PeopleWare agentsRequired already includes shrinkage (30% in Erlang-C)
                # psih_with_shrink = gross hours PW says you need (shrinkage included)
                # psih_no_shrink   = net productive hours only (back out the shrinkage)
                psih_with_shrink = avg_agents * req_count * 0.5
                psih_no_shrink   = psih_with_shrink * (1 - shrinkage)
                interval_count   = req_count
            else:
                avg_agents       = 0
                peak_agents      = 0
                # No PW requirements — calculate from forecast using Erlang-C inputs
                psih_no_shrink   = (calls_answered * avg_aht / 3600) / occupancy if occupancy else 0
                psih_with_shrink = psih_no_shrink / (1 - shrinkage) if shrinkage < 1 else 0

            try:
                year, mon     = int(month[:4]), int(month[5:7])
                working_hours = _working_hours_per_month(year, mon)
            except Exception:
                working_hours = 173.3  # fallback: 365/12 × 5/7 × 8
            fte_required = round(psih_with_shrink / working_hours, 1) if working_hours else 0

            # Skill-date-aware headcount per LOB per month (with EN/FR breakdown)
            hc_bd  = _hc_for_lob_month(lob, month)
            hc     = hc_bd["total"]

            plan[lob][month] = {
                "month":            month,
                "forecasted_calls": round(total_offered),
                "calls_answered":   round(calls_answered, 1),
                "aht":              round(avg_aht),
                "occupancy":        occupancy,
                "psih_no_shrink":   round(psih_no_shrink, 1),
                "psih_with_shrink": round(psih_with_shrink, 1),
                "fte_required":     fte_required,
                "actual_hc":        hc,
                "hc_combined":      hc_bd["combined"],
                "hc_en":            hc_bd["en"],
                "hc_fr":            hc_bd["fr"],
                "hc_has_lang":      hc_bd["has_lang"],
                "req_vs_actual":    round(hc - fte_required, 1),
                "shrinkage":        shrinkage,
                "peak_agents":      round(peak_agents, 2),
                "avg_agents":       round(avg_agents, 4),
                "interval_count":   interval_count,
            }

    return plan, all_months

def build_capacity_summary(plan, all_months, filter_month=None, filter_months=None):
    """
    Build summary table — one row per LOB per month so months are never collapsed.
    filter_month:  single month string (legacy compat)
    filter_months: list of month strings (multi-select)
    Returns rows sorted by LOB then month, each with a 'month' key.
    """
    if filter_months:
        month_list = [m for m in all_months if m in set(filter_months)]
    elif filter_month:
        month_list = [m for m in all_months if m == filter_month]
    else:
        month_list = list(all_months)

    rows = []
    for lob in sorted(plan.keys()):
        months_data = plan[lob]
        for month in month_list:
            m = months_data.get(month)
            if not m:
                continue
            hc      = m.get("actual_hc", 0)
            fte_req = round(m.get("fte_required", 0), 1)
            gap     = round(hc - fte_req, 1)
            rows.append({
                "lob":          lob,
                "month":        month,
                "date_range":   _month_label(month),
                "peak_agents":  round(m.get("peak_agents", 0), 2),
                "avg_agents":   round(m.get("avg_agents", 0), 4),
                "total_calls":  m.get("forecasted_calls", 0),
                "active_hc":    hc,
                "hc_combined":  m.get("hc_combined", 0),
                "hc_en":        m.get("hc_en", 0),
                "hc_fr":        m.get("hc_fr", 0),
                "hc_has_lang":  m.get("hc_has_lang", False),
                "fte_required": fte_req,
                "gap":          gap,
                "aht":          round(m.get("aht", 470)) if m.get("aht") else 470,
                "intervals":    m.get("interval_count", 0),
            })
    return rows

def build_headcount_view(employees_data):
    """
    Build headcount by Planning Unit.
    Groups by Planning Unit (not skill name).
    Active   = Status == Active AND Deleted != True/Yes/1
    LOA      = Status == LOA
    Inactive = everything else (including Inactive status)
    """
    by_pu = defaultdict(lambda: {"active": 0, "inactive": 0, "on_leave": 0, "total": 0})
    for emp in employees_data:
        status  = str(emp.get("status",  "")).strip()
        deleted = str(emp.get("deleted", "")).strip().lower()
        pu      = str(emp.get("planningUnit", "")).strip() or "Unknown"

        # "False" string = not deleted (from PeopleWare export)
        is_deleted = deleted in ("true", "yes", "1", "deleted")
        status_l   = status.lower()

        if not is_deleted and status_l == "active":
            by_pu[pu]["active"]   += 1
        elif not is_deleted and status_l == "loa":
            by_pu[pu]["on_leave"] += 1
        else:
            by_pu[pu]["inactive"] += 1
        by_pu[pu]["total"] += 1

    rows = []
    for pu, counts in sorted(by_pu.items()):
        total   = counts["total"]
        active  = counts["active"]
        loa_pct = round(counts["on_leave"] / total * 100, 1) if total > 0 else 0
        rows.append({
            "planning_unit": pu,
            "active":        active,
            "inactive":      counts["inactive"],
            "on_leave":      counts["on_leave"],
            "total":         total,
            "loa_pct":       loa_pct,
        })
    return rows

def _month_label(month_str):
    """Convert '2026-06' to 'Jun 2026'."""
    try:
        dt = datetime.datetime.strptime(month_str, "%Y-%m")
        return dt.strftime("%b %Y")
    except Exception:
        return month_str


# =========================================================
# DATE HELPERS
# =========================================================

def date_range(start_str, end_str):
    """Return list of dates from start to end inclusive."""
    try:
        start = datetime.datetime.strptime(start_str, "%Y-%m-%d").date()
        end   = datetime.datetime.strptime(end_str,   "%Y-%m-%d").date()
    except Exception:
        return []
    dates, d = [], start
    while d <= end:
        dates.append(d)
        d += datetime.timedelta(days=1)
    return dates

def year_range(year):
    """Return (start, end) date strings for a full calendar year."""
    return f"{year}-01-01", f"{year}-12-31"

def current_year():
    return datetime.date.today().year

def months_in_range(start_str, end_str):
    """Return sorted list of 'YYYY-MM' strings in the range."""
    dates   = date_range(start_str, end_str)
    months  = sorted(set(d.strftime("%Y-%m") for d in dates))
    return months
