"""
update_capacity_sheets.py
--------------------------
Pulls forecast, requirements, and employee data from PeopleWare
and writes it to the Google Sheets capacity planning spreadsheet.

CONFIGURATION: Edit the CONFIG section below, then run.

SCHEDULING OPTIONS:
  - SCHEDULE_MODE = "once"      → run once and exit
  - SCHEDULE_MODE = "hourly"    → run every hour between START_HOUR and END_HOUR
  - SCHEDULE_MODE = "halfhour"  → run every 30 min between START_HOUR and END_HOUR
  - SCHEDULE_MODE = "daily"     → run once per day at DAILY_RUN_TIME
  - SCHEDULE_MODE = "monthly"   → run on MONTHLY_RUN_DAY of each month

WHAT TO RUN:
  - WHAT = "forecast"           → FORECAST RAW only
  - WHAT = "aht"                → AHT RAW only
  - WHAT = "requirements"       → REQUIREMENTS RAW only
  - WHAT = "employees"          → EMPLOYEES only
  - WHAT = "all"                → forecast + aht + requirements (not employees)

LOB FILTER:
  - LOB_FILTER = []             → run all LOBs (default)
  - LOB_FILTER = ["SS Sales Combined", "SS Sales EN", "SS Sales FR"]
                                → run only those LOBs (set APPEND=True)

DATE MODES:
  - YEAR = 2026                 → full calendar year (clears + rewrites)
  - START_DATE / END_DATE       → specific range (clears + rewrites)
  - APPEND = True               → merge new data without deleting existing rows
"""

import os
import time
import requests
import traceback
import pandas as pd
from datetime import datetime, date, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# =========================================================
# CONFIG — edit this section before running
# =========================================================

CREDS_FILE         = r"C:\Users\mikeb\OneDrive - StorageVault Canada Inc\3.  Workforce Management\Mike Files\Power BI Files\Power Automate Schedule Files\employee-headcount.json"
CAPACITY_SHEET_KEY = "1Dk8nrTPcOexxYBF1cD0iKAYeKsBXJRmwF44QNv43fus"
LOCAL_TZ           = "America/Toronto"
TOKEN              = "MGE1M2YwNGNiN2JlY2NjZDNhNTM0ODZhYTA5YzdkZDQ="

# ── What to run ────────────────────────────────────────────
WHAT  = "all"  # "forecast" | "aht" | "requirements" | "employees" | "all"
                    # NOTE: employees are pulled by upload_employee_roster.py

# ── LOB filter (forecast, aht, requirements only) ──────────
# Leave empty [] to run ALL LOBs/planning units (default)
# Specify one or more LOB names to run only those:
#   LOB_FILTER = ["SS Sales Combined"]
#   LOB_FILTER = ["SS Sales Combined", "SS Sales EN", "SS Sales FR"]
#   LOB_FILTER = ["PS Care Combined", "PS Care EN", "PS Care FR"]
# When filtering, set APPEND = True to preserve other LOB columns
LOB_FILTER = []

# ── Date range ─────────────────────────────────────────────
YEAR       = 2026   # set to 0 to use START_DATE / END_DATE instead
START_DATE = ""     # e.g. "2026-01-01"  (ignored if YEAR is set)
END_DATE   = ""     # e.g. "2026-06-30"  (ignored if YEAR is set)

# ── Append mode ────────────────────────────────────────────
# True  = merge new data into existing sheet (keeps other LOBs/date ranges)
# False = clear sheet and rewrite from scratch (safer for full runs)
# NOTE: always use APPEND = True when LOB_FILTER is set
APPEND = False

# ── Scheduling ─────────────────────────────────────────────
SCHEDULE_MODE   = "once"    # "once" | "hourly" | "halfhour" | "daily" | "monthly"
START_HOUR      = 6         # scheduling window start (24h)
END_HOUR        = 23        # scheduling window end   (24h)
DAILY_RUN_TIME  = "06:30"   # for "daily" mode  (HH:MM)
MONTHLY_RUN_DAY = 1         # for "monthly" mode (day of month)

# ── Performance ────────────────────────────────────────────
MAX_WORKERS = 10
TIMEOUT     = 15
CHUNK_SIZE  = 500

# ── Debug mode ──────────────────────────────────────────────
# True = print available forecast types from API for first workload/date only
# Use this to verify "operational" exists before running a full pull
DEBUG_FORECAST = False

# =========================================================
# CONSTANTS
# =========================================================

HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept":        "application/json",
    "Content-Type":  "application/json",
}
API_NEW    = "https://api.peopleware.com"
API_LEGACY = "https://legacy-api.peopleware.com/v1"

WORKLOAD_NAMES = {
    "e2d2558b-0050-4719-8040-f55d6ad10c25": "I.T Support",
    "c3184f43-b095-4b13-a5be-371c48b83ead": "MoveBuddy",
    "e13311bb-714d-498c-a27b-dc6d9bc7576d": "PS Care Combined",
    "6913778e-ce1d-4ddb-97d8-93044d65f626": "PS Care EN",
    "63ee18a5-8eeb-4fd8-952b-ba9166db3fdd": "PS Care FR",
    "286243f3-6059-4eb4-99fb-286476921064": "PS Case Manager",
    "e19109ca-a910-4189-b1df-64457fb0289d": "PS Sales & Care Combined",
    "42248eab-5f8f-4b1e-a3ac-f2c6fe79e898": "PS Sales Combined",
    "1aaeee54-85ef-447d-a2b9-f09a785806c7": "PS Sales EN",
    "99f9cec2-844d-48fa-84c8-b4af5605896e": "PS Sales FR",
    "450d20b5-1105-4316-9082-f3ca4b5f9cdf": "SS CCA CC Combined",
    "978881de-68c2-4f63-b2dd-b1892471a29c": "SS CCA CC EN",
    "0e45cee2-7eff-4787-afda-00fee763ad17": "SS CCA CC FR",
    "1c09003e-aba4-4c8e-bb25-7b8a5e583aaa": "SS CCA Combined",
    "254aef98-e3b4-4c3d-9519-2c555194be0e": "SS CCA Combined EN",
    "511cc324-dc8a-4e2f-8a50-52f1f2ae1c81": "SS CCA Combined FR",
    "81fb4718-91cf-4de3-b73b-4ae3f3da1757": "SS CCA Store Combined",
    "399919c9-4285-4e5f-862f-4cca5765612f": "SS CCA Store EN",
    "fa0afb59-33fa-4a83-90eb-eb21f6b36c13": "SS CCA Store FR",
    "455a57fb-6664-4907-ba52-0fea4d123d3e": "SS Sales Combined",
    "8cbe2728-a958-4677-930a-2e2554668004": "SS Sales EN",
    "d0a075a5-5a73-46b0-8c63-206dc6c0b275": "SS Sales FR",
    "da69b74f-3b0f-460a-b87a-576e5008d2a7": "Web Leads PS Combined",
    "9f56e267-68de-492b-a2c9-ef219feed864": "Web Leads PS EN",
    "4d94afbe-8d92-4f04-a17b-f0843ec3beed": "Web Leads PS FR",
    "b478b01d-9166-4108-a6db-842008706234": "Web Leads PS & SS Combined",
    "99db45c7-155e-484a-909c-cdb69a0581b8": "Web Leads SS Combined",
    "5f31de17-ba2e-4e11-88d0-548a1b4669ed": "Web Leads SS EN",
    "e95ff578-25e9-40b3-956f-0efd434c85ce": "Web Leads SS FR",
    "26fc920a-1181-4b62-a71f-b43b104d8c2a": "Web Leads SS Inbound Combined",
    "8e61f24b-1fd6-4815-adbf-e560736d41af": "Web Leads SS Inbound EN",
    "39221bbe-3524-44bb-b348-86e0c5ffe1e0": "Web Leads SS Inbound FR",
}
EXCLUDED = {"All Queues Test", "SS CCA Payment"}
ACTIVE_WORKLOADS = {wid: name for wid, name in WORKLOAD_NAMES.items()
                    if name not in EXCLUDED}

BIZ_HOURS = {
    "weekday":  ("08:00", "22:00"),
    "saturday": ("09:00", "22:00"),
    "sunday":   ("09:00", "22:00"),
}

SKILL_MAP = {
    43709: "Admin", 49710: "All Queues Test", 45455: "EChat", 43635: "ERO",
    43862: "FlexSpace", 44085: "I.T Security", 43445: "I.T Support",
    43839: "MoveBuddy", 44803: "Operations", 43852: "PS Accounts Receivable",
    43708: "PS Admin", 46142: "PS Care Combined", 1009: "PS Care EN",
    1010: "PS Care FR", 43838: "PS Case Manager", 43853: "PS Dispatch",
    43851: "PS Gate Keeper", 43840: "PS Long Distance",
    43478: "PS New Hire Training", 43481: "PS Onboarding", 43850: "PS Outbound",
    46141: "PS Sales Combined", 1007: "PS Sales EN", 1008: "PS Sales FR",
    43841: "Quality Assurance", 44182: "Split Shift",
    43879: "SS CCA CC Combined", 43532: "SS CCA CC EN", 43534: "SS CCA CC FR",
    43531: "SS CCA Combined", 43479: "SS CCA New Hire Training",
    44730: "SS CCA Payment", 43880: "SS CCA Store Combined",
    43533: "SS CCA Store EN", 43535: "SS CCA Store FR",
    49751: "SS CM Combined", 49752: "SS CM EN", 49754: "SS CM FR",
    43482: "SS CM Onboarding", 43477: "SS New Hire Training",
    43480: "SS Onboarding", 46140: "SS Sales Combined",
    1001: "SS Sales EN", 1002: "SS Sales FR", 43842: "Store Liaison",
    1006: "Team Lead", 43559: "Web Leads", 43565: "Web Leads PS",
    43566: "Web Leads PS EN", 43567: "Web Leads PS FR",
    43568: "Web Leads SS", 43569: "Web Leads SS EN",
    43570: "Web Leads SS FR", 1005: "Workforce Management",
    53545: "Web Leads SS Inbound", 53546: "Web Leads SS Inbound EN",
    53547: "Web Leads SS Inbound FR",
}

INACTIVE_COLORS = {"3739363", "255"}
LOA_COLOR       = "16711680"

SHEET_HEADERS_EMPLOYEES = [
    "Employee ID", "First Name", "Last Name",
    "Start Date", "End Date", "Planning Unit",
    "Birth Place", "Birth Date", "Personnel #", "Current ID",
    "Auto Shift Assignment", "Deleted", "Status", "Color",
    "Schedule Position", "Contract Type", "Contract Start", "Contract End",
    "Latest Skill Start", "Latest Skill Name", "Latest Skill End",
    "All Skills", "Title",
]

# =========================================================
# ACTIVITY ID → LOB NAME MAP
# Mirrors VBA SVC_ActivityName() exactly.
# =========================================================
ACTIVITY_NAME_MAP = {
    "1005": "SS Sales Combined",
    "1003": "SS Sales EN",
    "1004": "SS Sales FR",
    "1058": "SS Case Management Combined",
    "1089": "SS Case Management CC Combined",
    "1059": "SS Case Management CC EN",
    "1060": "SS Case Management CC FR",
    "1044": "PS Sales EN",
    "1045": "PS Sales FR",
    "1049": "PS Care Combined",
    "1047": "PS Care EN",
    "1048": "PS Care FR",
    "1079": "PS Case Manager",
    "1063": "Web Leads SS Combined",
    "1067": "Web Leads SS EN",
    "1068": "Web Leads SS FR",
    "75181": "Web Leads SS Inbound Combined",
    "75183": "Web Leads SS Inbound EN",
    "75184": "Web Leads SS Inbound FR",
    "1064": "Web Leads PS Combined",
    "1065": "Web Leads PS EN",
    "1066": "Web Leads PS FR",
    "1050": "I.T Support",
    "1072": "MoveBuddy",
    "1025": "Team Lead",
    "1024": "Workforce Management",
    "46131": "EChat",
    "1070": "ERO",
    "1081": "PS Admin",
}

# LOB name → planning unit name in PeopleWare API.
# Used to skip entire planning units when LOB_FILTER is set.
LOB_TO_PU = {
    "SS Sales Combined":                "SS Sales",
    "SS Sales EN":                      "SS Sales",
    "SS Sales FR":                      "SS Sales",
    "SS Case Management Combined":      "SS Case Management",
    "SS Case Management CC Combined":   "SS Case Management",
    "SS Case Management CC EN":         "SS Case Management",
    "SS Case Management CC FR":         "SS Case Management",
    "PS Sales EN":                      "PS Sales",
    "PS Sales FR":                      "PS Sales",
    "PS Care Combined":                 "PS Care",
    "PS Care EN":                       "PS Care",
    "PS Care FR":                       "PS Care",
    "PS Case Manager":                  "PS Case Manager",
    "Web Leads SS Combined":            "Web Leads SS",
    "Web Leads SS EN":                  "Web Leads SS",
    "Web Leads SS FR":                  "Web Leads SS",
    "Web Leads SS Inbound Combined":    "Web Leads SS Inbound",
    "Web Leads SS Inbound EN":          "Web Leads SS Inbound",
    "Web Leads SS Inbound FR":          "Web Leads SS Inbound",
    "Web Leads PS Combined":            "Web Leads PS",
    "Web Leads PS EN":                  "Web Leads PS",
    "Web Leads PS FR":                  "Web Leads PS",
    "I.T Support":                      "I.T Support",
    "MoveBuddy":                        "MoveBuddy",
    "Team Lead":                        "Team Lead",
    "Workforce Management":             "Workforce Management",
    "EChat":                            "EChat",
    "ERO":                              "ERO",
    "PS Admin":                         "PS Admin",
}


# =========================================================
# HELPERS
# =========================================================

def make_session():
    session = requests.Session()
    retry   = Retry(total=3, backoff_factor=1,
                    status_forcelist=[429, 500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://",  adapter)
    return session

def get_gsheet():
    scope  = ["https://spreadsheets.google.com/feeds",
               "https://www.googleapis.com/auth/spreadsheets",
               "https://www.googleapis.com/auth/drive"]
    creds  = ServiceAccountCredentials.from_json_keyfile_name(CREDS_FILE, scope)
    client = gspread.authorize(creds)
    return client.open_by_key(CAPACITY_SHEET_KEY)

def chunk(lst, size=CHUNK_SIZE):
    for i in range(0, len(lst), size):
        yield lst[i:i + size]

def resolve_status(color):
    c = str(color)
    if c == LOA_COLOR:       return "LOA"
    if c in INACTIVE_COLORS: return "Inactive"
    return "Active"

def date_range(start_str, end_str):
    start = datetime.strptime(start_str, "%Y-%m-%d").date()
    end   = datetime.strptime(end_str,   "%Y-%m-%d").date()
    dates, d = [], start
    while d <= end:
        dates.append(d)
        d += timedelta(days=1)
    return dates

def resolve_dates():
    if YEAR:
        return f"{YEAR}-01-01", f"{YEAR}-12-31"
    if START_DATE and END_DATE:
        return START_DATE, END_DATE
    raise ValueError("Set YEAR or both START_DATE and END_DATE in CONFIG.")

def pick_contract(contracts):
    today  = date.today()
    active, past = [], []
    for c in contracts:
        s = c.get("start_date")
        e = c.get("end_date")
        try:    start = datetime.strptime(s, "%Y-%m-%d").date() if s else None
        except: start = None
        try:    end = datetime.strptime(e, "%Y-%m-%d").date() if e and e != "4000-01-01" else None
        except: end = None
        if start and start <= today and (end is None or today <= end):
            active.append((start, c))
        elif end and end < today:
            past.append((start, c))
    if active: return max(active, key=lambda x: x[0])[1]
    if past:   return max(past,   key=lambda x: x[0])[1]
    return contracts[0] if contracts else None

def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")

def _apply_lob_filter(lob_names):
    """Return filtered list if LOB_FILTER is set, otherwise return all."""
    if not LOB_FILTER:
        return lob_names
    filtered = [n for n in lob_names if n in LOB_FILTER]
    missing  = [n for n in LOB_FILTER if n not in lob_names]
    if missing:
        log(f"  ⚠ LOB_FILTER: these names not found in workload list: {missing}")
    log(f"  LOB_FILTER active — {len(filtered)} of {len(lob_names)} LOBs: {filtered}")
    return filtered

def _merge_into_sheet(ws, new_data, filtered_lob_names):
    """
    Read existing sheet and merge new data into it,
    preserving columns for LOBs that were not re-pulled.
    """
    existing = ws.get_all_values()
    if not existing or len(existing) < 2:
        return new_data, filtered_lob_names

    existing_headers = existing[0]
    all_lobs = existing_headers[1:] if existing_headers else []

    for lob in filtered_lob_names:
        if lob not in all_lobs:
            all_lobs.append(lob)

    merged = {}
    for row in existing[1:]:
        ts = row[0]
        if not ts:
            continue
        merged[ts] = {}
        for i, col in enumerate(existing_headers[1:], 1):
            if i < len(row) and row[i] not in ("", None):
                merged[ts][col] = row[i]

    for ts, lob_vals in new_data.items():
        if ts not in merged:
            merged[ts] = {}
        merged[ts].update(lob_vals)

    return merged, all_lobs


# =========================================================
# FORECAST
# =========================================================

def pull_forecast(start_str, end_str, append=False):
    log(f"FORECAST  {start_str} → {end_str}  (append={append})")
    dates         = date_range(start_str, end_str)
    wid_list      = list(ACTIVE_WORKLOADS.keys())
    all_lob_names = [ACTIVE_WORKLOADS[w] for w in wid_list]

    filtered_names = _apply_lob_filter(all_lob_names)
    filtered_wids  = [wid for wid in wid_list
                      if ACTIVE_WORKLOADS[wid] in filtered_names]

    data: dict = {}

    for wid in filtered_wids:
        name = ACTIVE_WORKLOADS[wid]
        log(f"  Pulling {name}...")
        count = 0

        for d in dates:
            wd = d.weekday()
            if wd < 5:    open_str, close_str = BIZ_HOURS["weekday"]
            elif wd == 5: open_str, close_str = BIZ_HOURS["saturday"]
            else:         open_str, close_str = BIZ_HOURS["sunday"]

            open_dt   = pd.Timestamp(f"{d} {open_str}").tz_localize(LOCAL_TZ)
            close_dt  = pd.Timestamp(f"{d} {close_str}").tz_localize(LOCAL_TZ)
            start_utc = open_dt.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")
            end_utc   = close_dt.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")

            url = f"{API_NEW}/workloads/{wid}/forecasts?startTime={start_utc}&endTime={end_utc}"
            try:
                resp = make_session().get(url, headers=HEADERS, timeout=TIMEOUT)
                if not resp.ok:
                    continue
                result    = resp.json()
                raw_data  = result.get("data", result)
                interval  = pd.to_timedelta(
                    raw_data.get("intervalDuration", "PT30M")
                    if isinstance(raw_data, dict) else "PT30M")
                forecasts = raw_data.get("forecasts", {}) if isinstance(raw_data, dict) else {}
                # Prefer "operational" (planner-approved) over "auto" (system-generated)
                fc_block  = forecasts.get("operational") or forecasts.get("auto") or {}
                offered   = fc_block.get("offered", {})
                vals      = offered.get("values", []) if isinstance(offered, dict) else []

                for idx, val in enumerate(vals):
                    ts = open_dt + idx * interval
                    if ts >= close_dt:
                        break
                    ts_str = ts.tz_localize(None).strftime("%Y-%m-%d %H:%M:%S")
                    if ts_str not in data:
                        data[ts_str] = {}
                    data[ts_str][name] = float(val or 0)
                    count += 1

                time.sleep(0.05)
            except Exception as e:
                log(f"    ⚠ {d}: {e}")

        log(f"    → {count} intervals")

    doc = get_gsheet()
    ws  = doc.worksheet("FORECAST RAW")

    if LOB_FILTER or append:
        merged, lob_names = _merge_into_sheet(ws, data, filtered_names)
        rows = [[ts] + [merged[ts].get(n, 0) for n in lob_names]
                for ts in sorted(merged.keys())]
    else:
        lob_names = filtered_names
        rows = [[ts] + [data[ts].get(n, 0) for n in lob_names]
                for ts in sorted(data.keys())]

    log(f"Writing {len(rows)} rows to FORECAST RAW ({len(lob_names)} LOBs)...")
    ws.clear()
    ws.append_row(["Timestamp"] + lob_names)
    for c in chunk(rows):
        ws.append_rows(c, value_input_option="RAW")

    log(f"✅ FORECAST RAW — {len(rows)} rows written.")
    return len(rows)


# =========================================================
# AHT
# =========================================================

def pull_aht(start_str, end_str, append=False):
    log(f"AHT  {start_str} → {end_str}  (append={append})")
    dates         = date_range(start_str, end_str)
    wid_list      = list(ACTIVE_WORKLOADS.keys())
    all_lob_names = [ACTIVE_WORKLOADS[w] for w in wid_list]

    filtered_names = _apply_lob_filter(all_lob_names)
    filtered_wids  = [wid for wid in wid_list
                      if ACTIVE_WORKLOADS[wid] in filtered_names]

    data: dict = {}

    for wid in filtered_wids:
        name = ACTIVE_WORKLOADS[wid]
        log(f"  Pulling AHT {name}...")
        count = 0

        for d in dates:
            wd = d.weekday()
            if wd < 5:    open_str, close_str = BIZ_HOURS["weekday"]
            elif wd == 5: open_str, close_str = BIZ_HOURS["saturday"]
            else:         open_str, close_str = BIZ_HOURS["sunday"]

            open_dt   = pd.Timestamp(f"{d} {open_str}").tz_localize(LOCAL_TZ)
            close_dt  = pd.Timestamp(f"{d} {close_str}").tz_localize(LOCAL_TZ)
            start_utc = open_dt.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")
            end_utc   = close_dt.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")

            url = (f"{API_NEW}/workloads/{wid}/forecasts"
                   f"?startTime={start_utc}&endTime={end_utc}")
            try:
                resp = make_session().get(url, headers=HEADERS, timeout=TIMEOUT)
                if not resp.ok:
                    continue
                result    = resp.json()
                raw_data  = result.get("data", result)
                interval  = pd.to_timedelta(
                    raw_data.get("intervalDuration", "PT30M")
                    if isinstance(raw_data, dict) else "PT30M")
                forecasts = raw_data.get("forecasts", {}) if isinstance(raw_data, dict) else {}

                # Debug: show what forecast types are available
                if DEBUG_FORECAST:
                    avail = [k for k, v in forecasts.items() if v and v.get("offered", {}).get("values")]
                    log(f"    DEBUG {name} {d}: available forecast types with offered values = {avail}")
                    log(f"    DEBUG forecasts keys = {list(forecasts.keys())}")
                    DEBUG_FORECAST_DONE = True  # only need to see one

                fc_block  = forecasts.get("operational") or forecasts.get("auto") or {}  # prefer planner-approved
                aht_field = fc_block.get("averageHandlingTime", {})
                aht_vals  = (aht_field.get("values", [])
                             if isinstance(aht_field, dict) else [])

                for idx, val in enumerate(aht_vals):
                    ts = open_dt + idx * interval
                    if ts >= close_dt:
                        break
                    if val and float(val) > 0:
                        ts_str = ts.tz_localize(None).strftime("%Y-%m-%d %H:%M:%S")
                        if ts_str not in data:
                            data[ts_str] = {}
                        data[ts_str][name] = float(val)
                        count += 1

                time.sleep(0.05)
            except Exception as e:
                log(f"    ⚠ {d}: {e}")

        log(f"    → {count} AHT intervals")

    doc = get_gsheet()
    ws  = doc.worksheet("AHT RAW")

    if LOB_FILTER or append:
        merged, lob_names = _merge_into_sheet(ws, data, filtered_names)
        rows = []
        for ts in sorted(merged.keys()):
            row = [ts]
            for n in lob_names:
                row.append(merged[ts].get(n, ""))
            rows.append(row)
    else:
        lob_names = filtered_names
        rows = []
        for ts in sorted(data.keys()):
            row = [ts]
            for n in lob_names:
                row.append(data[ts].get(n, ""))
            rows.append(row)

    log(f"Writing {len(rows)} rows to AHT RAW ({len(lob_names)} LOBs)...")
    ws.clear()
    ws.append_row(["Timestamp"] + lob_names)
    for c in chunk(rows):
        ws.append_rows(c, value_input_option="RAW")

    log(f"✅ AHT RAW — {len(rows)} rows written.")
    return len(rows)


# =========================================================
# REQUIREMENTS
# =========================================================

def pull_requirements(start_str, end_str, append=False):
    log(f"REQUIREMENTS  {start_str} → {end_str}  (append={append})")
    dates   = date_range(start_str, end_str)
    session = make_session()

    # Build filtered activity map
    if LOB_FILTER:
        act_map = {k: v for k, v in ACTIVITY_NAME_MAP.items() if v in LOB_FILTER}
        missing = [n for n in LOB_FILTER if n not in ACTIVITY_NAME_MAP.values()]
        if missing:
            log(f"  ⚠ LOB_FILTER: no activity ID mapping for: {missing}")
        log(f"  LOB_FILTER active — {len(act_map)} activity IDs: "
            f"{list(act_map.values())}")
    else:
        act_map = ACTIVITY_NAME_MAP

    log("  Fetching planning units...")
    resp  = session.get(f"{API_LEGACY}/planning_units", headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    all_units = [u for u in resp.json().get("planning_units", [])
                 if u.get("name", "") not in EXCLUDED]

    # Only pull planning units that contain the filtered LOBs
    if LOB_FILTER:
        needed_pus = set(LOB_TO_PU.get(lob, "") for lob in LOB_FILTER)
        needed_pus.discard("")
        units = [u for u in all_units if u.get("name", "") in needed_pus]
        log(f"  LOB_FILTER active — pulling {len(units)} of {len(all_units)} "
            f"planning units: {[u.get('name') for u in units]}")
    else:
        units = all_units
        log(f"  {len(units)} planning units")

    data: dict           = {}
    lob_names_seen: list = []

    for pu in units:
        pu_id   = str(pu.get("planning_unit_id") or pu.get("id", ""))
        pu_name = pu.get("name", "")
        if not pu_id:
            continue
        log(f"  Pulling {pu_name}...")
        count = 0

        for d in dates:
            url = f"{API_LEGACY}/planning_units/{pu_id}/requirements/{d.strftime('%Y-%m-%d')}"
            try:
                resp = make_session().get(url, headers=HEADERS, timeout=TIMEOUT)
                if not resp.ok:
                    continue
                result = resp.json()

                items = result if isinstance(result, list) else (
                    result.get("requirements") or result.get("data") or
                    result.get("planning_unit_requirements") or [])
                if isinstance(items, dict) and "values" in items:
                    items = [items]
                if not items and "values" in str(result)[:200]:
                    items = [result]
                if DEBUG_FORECAST and not items:
                    log(f"    DEBUG requirements {pu_name} {d}: no items found. Raw keys = {list(result.keys()) if isinstance(result, dict) else type(result).__name__}")

                for item in items:
                    act_id   = str(item.get("activity_id", ""))
                    lob_name = act_map.get(act_id, "")
                    if not lob_name:
                        continue

                    if lob_name not in lob_names_seen:
                        lob_names_seen.append(lob_name)

                    raster_s = item.get("raster", 1800)
                    try:    i_mins = int(raster_s) // 60
                    except: i_mins = 30
                    if i_mins < 1:
                        i_mins = 30

                    values    = item.get("values", [])
                    day_start = pd.Timestamp(f"{d} 00:00:00").tz_localize(LOCAL_TZ)

                    for idx, val in enumerate(values):
                        slot   = day_start + pd.Timedelta(minutes=idx * i_mins)
                        ts_str = slot.tz_localize(None).strftime("%Y-%m-%d %H:%M:%S")
                        fval   = float(val or 0)
                        if ts_str not in data:
                            data[ts_str] = {}
                        data[ts_str][lob_name] = fval
                        count += 1

                time.sleep(0.05)
            except Exception as e:
                log(f"    ⚠ {d}: {e}")

        log(f"    → {count} intervals")

    if not lob_names_seen:
        log("⚠ No LOB data collected — check activity ID map or date range.")
        return 0

    doc = get_gsheet()
    ws  = doc.worksheet("REQUIREMENTS RAW")

    if LOB_FILTER or append:
        merged, lob_names = _merge_into_sheet(ws, data, lob_names_seen)
        rows = [[ts] + [merged[ts].get(n, 0) for n in lob_names]
                for ts in sorted(merged.keys())]
    else:
        lob_names = lob_names_seen
        rows = [[ts] + [data[ts].get(n, 0) for n in lob_names]
                for ts in sorted(data.keys())]

    log(f"Writing {len(rows)} rows to REQUIREMENTS RAW ({len(lob_names)} LOBs)...")
    ws.clear()
    ws.append_row(["Timestamp"] + lob_names)
    for c in chunk(rows):
        ws.append_rows(c, value_input_option="RAW")

    log(f"✅ REQUIREMENTS RAW — {len(rows)} rows written, {len(lob_names)} LOBs.")
    return len(rows)


# =========================================================
# EMPLOYEES
# =========================================================

def fetch_employee_extra(emp, planning_unit_names, contract_lookup):
    session = make_session()
    emp_id  = str(emp.get("employee_id"))

    planning_unit = ""
    try:
        resp = session.get(f"{API_LEGACY}/employees/{emp_id}/planning_units",
                           headers=HEADERS, timeout=TIMEOUT)
        if resp.ok:
            assign_data = resp.json().get("data", [])
            if assign_data:
                if "assignment_date" in assign_data[0]:
                    assign_data.sort(key=lambda x: x.get("assignment_date", ""), reverse=True)
                latest        = assign_data[0]
                pu_id         = int(latest.get("planning_unit_id"))
                planning_unit = planning_unit_names.get(pu_id, "")
    except Exception:
        pass

    contract_type = contract_start = contract_end = ""
    try:
        resp = session.get(f"{API_LEGACY}/employees/{emp_id}/contracts",
                           headers=HEADERS, timeout=TIMEOUT)
        if resp.ok:
            chosen = pick_contract(resp.json().get("data", []))
            if chosen:
                contract_type  = contract_lookup.get(str(chosen.get("contract_id", "")), "")
                contract_start = chosen.get("start_date", "")
                contract_end   = ("" if chosen.get("end_date") == "4000-01-01"
                                  else chosen.get("end_date", ""))
    except Exception:
        pass

    latest_skill_start = latest_skill_name = latest_skill_end = ""
    all_skills = []
    try:
        resp = session.get(f"{API_LEGACY}/employees/{emp_id}/skill_levels",
                           headers=HEADERS, timeout=TIMEOUT)
        if resp.ok:
            skill_data    = resp.json().get("data", [])
            today         = date.today()
            active_skills = []
            for s in skill_data:
                end_str = s.get("end_date")
                if not end_str:
                    active_skills.append(s)
                else:
                    try:
                        if datetime.strptime(end_str, "%Y-%m-%d").date() >= today:
                            active_skills.append(s)
                    except Exception:
                        pass
            if active_skills:
                active_skills.sort(key=lambda x: x.get("start_date", ""), reverse=True)
                latest             = active_skills[0]
                latest_skill_start = latest.get("start_date", "")
                latest_skill_end   = latest.get("end_date",   "")
                skill_id           = latest.get("skill_id")
                latest_skill_name  = SKILL_MAP.get(skill_id, f"Unknown ({skill_id})")
                all_skills         = list({
                    SKILL_MAP.get(s.get("skill_id"), f"Unknown ({s.get('skill_id')})")
                    for s in active_skills
                })
    except Exception as e:
        log(f"  ⚠ Skill error for {emp_id}: {e}")

    return {
        "emp_id":             emp_id,
        "planning_unit":      planning_unit,
        "contract_type":      contract_type,
        "contract_start":     contract_start,
        "contract_end":       contract_end,
        "latest_skill_start": latest_skill_start,
        "latest_skill_name":  latest_skill_name,
        "latest_skill_end":   latest_skill_end,
        "all_skills":         all_skills,
    }

def get_title_lookup(session):
    titles, page = {}, 1
    try:
        while True:
            resp = session.get(f"{API_NEW}/people", headers=HEADERS,
                               params={"include": "employeeId",
                                       "page[size]": 100,
                                       "page[number]": page}, timeout=TIMEOUT)
            if not resp.ok:
                break
            result = resp.json()
            for p in result.get("data", []):
                eid = p.get("employeeId")
                if eid:
                    titles[str(eid)] = p.get("title", "") or ""
            if not result.get("next"):
                break
            page += 1
    except Exception as e:
        log(f"  ⚠ Title lookup error: {e}")
    return titles

def pull_employees():
    log("EMPLOYEES")
    session = make_session()

    log("  Fetching planning units...")
    r = session.get(f"{API_LEGACY}/planning_units", headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    planning_unit_names = {int(u["planning_unit_id"]): u["name"]
                           for u in r.json().get("planning_units", [])}

    log("  Fetching employment periods...")
    r = session.get(f"{API_LEGACY}/employee_employment_periods",
                    headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    employment_lookup = {
        str(p["employee_id"]): {
            "start": p.get("start_date", ""),
            "end":   "" if p.get("end_date") == "4000-01-01" else p.get("end_date", "")
        }
        for p in r.json().get("employee_employment_periods", [])
    }

    log("  Fetching contract types...")
    r = session.get(f"{API_LEGACY}/contracts", headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    contract_lookup = {str(c["contract_id"]): c.get("name", "")
                       for c in r.json().get("contracts", []) if "contract_id" in c}

    log("  Fetching employees...")
    r = session.get(f"{API_LEGACY}/employees", headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    employees = r.json().get("employees", [])
    log(f"  {len(employees)} employees found")

    log("  Fetching job titles...")
    title_lookup = get_title_lookup(session)

    log(f"  Fetching per-employee details ({MAX_WORKERS} threads)...")
    results_lookup = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(fetch_employee_extra, emp,
                            planning_unit_names, contract_lookup): emp
            for emp in employees
        }
        done = 0
        for future in as_completed(futures):
            try:
                res = future.result()
                results_lookup[res["emp_id"]] = res
            except Exception as e:
                emp = futures[future]
                log(f"  ⚠ Worker error for {emp.get('employee_id')}: {e}")
            done += 1
            if done % 50 == 0:
                log(f"  {done}/{len(employees)} employees processed...")

    rows = []
    for emp in employees:
        emp_id     = str(emp.get("employee_id"))
        status     = resolve_status(emp.get("color"))
        employment = employment_lookup.get(emp_id, {})
        d          = results_lookup.get(emp_id, {})
        title      = title_lookup.get(emp_id, "")
        rows.append([
            emp_id, emp.get("first_name", ""), emp.get("last_name", ""),
            employment.get("start", ""), employment.get("end", ""),
            d.get("planning_unit", ""),
            emp.get("birth_place", ""), emp.get("birth_date", ""),
            emp.get("personnel_number", ""), emp.get("current_identification", ""),
            str(emp.get("automated_shift_assignment", "")),
            str(emp.get("deleted", "")),
            status, str(emp.get("color", "")),
            str(emp.get("schedule_position", "")),
            d.get("contract_type",  ""), d.get("contract_start", ""),
            d.get("contract_end",   ""),
            d.get("latest_skill_start", ""), d.get("latest_skill_name",  ""),
            d.get("latest_skill_end",   ""),
            ", ".join(d.get("all_skills", [])),
            title,
        ])

    log(f"  Writing {len(rows)} employees to sheet...")
    doc = get_gsheet()
    ws  = doc.worksheet("EMPLOYEES")
    ws.clear()
    ws.append_row(SHEET_HEADERS_EMPLOYEES)
    for c in chunk(rows):
        ws.append_rows(c, value_input_option="RAW")

    log(f"✅ EMPLOYEES — {len(rows)} rows written.")
    return len(rows)


# =========================================================
# SINGLE RUN
# =========================================================

def run_once():
    start_str, end_str = (None, None)
    if WHAT in ("forecast", "aht", "requirements", "all"):
        start_str, end_str = resolve_dates()

    if LOB_FILTER and not APPEND:
        log("⚠ WARNING: LOB_FILTER is set but APPEND=False.")
        log("  This will CLEAR the sheet and only write the filtered LOBs.")
        log("  Set APPEND=True to preserve other LOB columns.")

    t0 = datetime.now()
    log(f"{'='*60}")
    log(f"RUN  what={WHAT}  dates={start_str or 'n/a'} → {end_str or 'n/a'}"
        f"  append={APPEND}  lob_filter={LOB_FILTER or 'all'}")
    log(f"{'='*60}")

    if WHAT in ("forecast", "all"):
        pull_forecast(start_str, end_str, append=APPEND)

    if WHAT in ("aht", "all"):
        pull_aht(start_str, end_str, append=APPEND)

    if WHAT in ("requirements", "all"):
        pull_requirements(start_str, end_str, append=APPEND)

    if WHAT == "employees":
        pull_employees()
    # NOTE: "all" intentionally skips employees — use upload_employee_roster.py instead

    elapsed = (datetime.now() - t0).seconds
    log(f"{'='*60}")
    log(f"✅ Complete — {elapsed // 60}m {elapsed % 60}s")
    log(f"{'='*60}")


# =========================================================
# SCHEDULER
# =========================================================

def within_window():
    now = datetime.now()
    return START_HOUR <= now.hour < END_HOUR

def next_run_time():
    now = datetime.now()
    if SCHEDULE_MODE == "hourly":
        candidate = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    elif SCHEDULE_MODE == "halfhour":
        if now.minute < 30:
            candidate = now.replace(minute=30, second=0, microsecond=0)
        else:
            candidate = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    elif SCHEDULE_MODE == "daily":
        h, m      = [int(x) for x in DAILY_RUN_TIME.split(":")]
        candidate = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
    elif SCHEDULE_MODE == "monthly":
        candidate = now.replace(day=MONTHLY_RUN_DAY,
                                hour=int(DAILY_RUN_TIME.split(":")[0]),
                                minute=int(DAILY_RUN_TIME.split(":")[1]),
                                second=0, microsecond=0)
        if candidate <= now:
            if candidate.month == 12:
                candidate = candidate.replace(year=candidate.year + 1, month=1)
            else:
                candidate = candidate.replace(month=candidate.month + 1)
    else:
        return None
    return candidate

def run_with_schedule():
    log(f"Scheduler started — mode={SCHEDULE_MODE}  window={START_HOUR}:00–{END_HOUR}:00")

    if within_window():
        try:
            run_once()
        except Exception as e:
            log(f"❌ Run failed: {e}")
            traceback.print_exc()

    if SCHEDULE_MODE == "once":
        return

    while True:
        nxt = next_run_time()
        if nxt is None:
            break
        delay = max(0, (nxt - datetime.now()).total_seconds())
        log(f"⏳ Next run: {nxt.strftime('%Y-%m-%d %H:%M:%S')}  "
            f"(in {int(delay//60)}m {int(delay%60)}s)")
        time.sleep(delay)

        if not within_window():
            log("Outside scheduling window — skipping.")
            continue

        try:
            run_once()
        except Exception as e:
            log(f"❌ Run failed: {e}")
            traceback.print_exc()


# =========================================================
# ENTRY POINT  (works in both Jupyter and terminal)
# =========================================================

if __name__ == "__main__" or True:
    run_with_schedule()