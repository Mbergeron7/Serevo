"""
rtm.py — Real Time Management data helpers
Reads from Google Sheets and returns structured data for RTM templates.

Data sources:
  Schedule sheet   → shift times per employee (personnel number)
  Schedule blocks  → individual activity blocks per employee (VTO detection)
  Employee Roster  → personnel number → name, LOB, employee_id
  Agent Status     → agent name → actual login, current CP status (joined by NAME)
  Call Status      → queue_id → actual call volume, SL, cascades
  Forecast         → workload forecasts
  KA sheet         → knowledge assessment completions (joined by NAME)

Timezone note:
  CP Agent Status times are in CST (UTC-6).
  Portal runs in EST (UTC-5). Add 1 hour to all CP times.
"""

import os
import logging
import time
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

EST = ZoneInfo("America/Toronto")

def _today_est():
    """Return today's date in EST/EDT regardless of server timezone."""
    return datetime.now(EST).date()

def _now_est():
    """Return current datetime in EST/EDT."""
    return datetime.now(EST).replace(tzinfo=None)

log = logging.getLogger("wfm_rtm")

# ── Thread-safe cache with per-key locks ──────────────────────────────────
# Prevents concurrent requests from all hitting Google simultaneously.
# Pattern: only ONE thread fetches a given sheet; others wait for it.
# Stale data is served rather than erroring when Google is unavailable.

import threading

RTM_CACHE_TTL  = 300        # 5 minutes — data freshness window
RTM_LOCK_WAIT  = 15         # max seconds to wait for another thread's fetch

# Sheets that change rarely — use a longer TTL to avoid midnight quota storms
_SLOW_SHEET_TTL = {
    "1SM4WDQFWhy-zBwMLCWER863m95Hi4mQjG7wEogOOR4U": 3600,  # Roster — 1 hour (changes rarely)
    "14gf8tV7LmQpGaMNEdYZ7CDt5wVsj5yaeFjgeRuPi1ss": 1800,  # Schedule blocks — 30 min
    "1sjLs3WxBdSLb-9rdTRAVZc-VTKMljXL7ym8dQ-mgAow": 1800,  # Knowledge — 30 min
    "15k9ahqgtuaigy4c7YNe2qy-OimAsjlbDQqd6F3VEt_g": 120,   # Agent Status — 2 min (live data)
    "1fWJkm6Woc5bKYkI2tNRZx6_Khrsje2GOsXL4YyPZ-bo": 120,   # Call Status — 2 min (live data)
}

_rtm_cache     = {}         # key → (timestamp, data)
_rtm_cache_lck = {}         # key → threading.Lock()
_rtm_meta_lock = threading.Lock()  # protects _rtm_cache_lck dict itself

# Stagger midnight invalidation — each sheet key gets a small random offset
# so they don't all expire at exactly the same second and flood the API.
import random as _random
_CACHE_JITTER  = {}  # cache_key → seconds offset applied at invalidation

def _midnight_stagger(cache_key):
    """Return a per-key jitter offset (0–299s) so midnight re-reads spread over 5 min."""
    if cache_key not in _CACHE_JITTER:
        _CACHE_JITTER[cache_key] = _random.randint(0, 299)
    return _CACHE_JITTER[cache_key]


def _get_key_lock(key):
    """Return the per-key lock, creating it if needed (thread-safe)."""
    with _rtm_meta_lock:
        if key not in _rtm_cache_lck:
            _rtm_cache_lck[key] = threading.Lock()
        return _rtm_cache_lck[key]


def _cache_get(key):
    entry = _rtm_cache.get(key)
    if not entry:
        return None
    ts, data = entry
    # Compare dates in EST — NOT server local time (UTC).
    # Server is UTC, so time.localtime(ts) gives UTC date.
    # After midnight UTC (= 8pm EDT), cached_date would be "tomorrow" UTC
    # while today_date is still "today" EDT → false DATE CHANGED every evening.
    cached_date = datetime.fromtimestamp(ts, tz=EST).strftime("%Y-%m-%d")
    today_date  = _today_est().isoformat()
    if cached_date != today_date:
        # Only invalidate once the stagger offset has elapsed past EST midnight
        midnight_elapsed = (time.time() - time.mktime(
            time.strptime(today_date, "%Y-%m-%d")))
        if midnight_elapsed >= _midnight_stagger(key):
            log.info(f"Cache DATE CHANGED — invalidating {key[:40]}")
            del _rtm_cache[key]
            return None
        # Otherwise keep serving yesterday's stale data a little longer
        return data
    ttl = _SLOW_SHEET_TTL.get(key.split(":")[0], RTM_CACHE_TTL)
    if (time.time() - ts) < ttl:
        log.info(f"Cache HIT: {key[:50]}")
        return data
    return None


def _cache_get_stale(key):
    """Return cached data regardless of age (emergency fallback)."""
    entry = _rtm_cache.get(key)
    return entry[1] if entry else None


def _cache_set(key, data):
    _rtm_cache[key] = (time.time(), data)
    return data


def expire_all_cache():
    """
    Force every cached entry to be treated as stale so the next read
    triggers a fresh fetch — but keep the underlying data in place as an
    emergency fallback if that fresh fetch fails (e.g. a brief Google API
    hiccup right after a manual cache-clear). This avoids the old
    behaviour where clearing the cache also destroyed the stale-fallback
    safety net, causing pages to briefly show "no data" if any one of
    several sheets failed to refetch immediately after a clear.
    """
    for key in list(_rtm_cache.keys()):
        ts, data = _rtm_cache[key]
        _rtm_cache[key] = (0, data)

# ── Sheet IDs ──────────────────────────────────────────────────────────────
SCHEDULE_SHEET_ID  = "1wc8JJ3HRQK3HE_Qa8k8n7GFwDQiuokFqzKDu1r_kBrY"
SCHEDULE_BLOCKS_ID = "14gf8tV7LmQpGaMNEdYZ7CDt5wVsj5yaeFjgeRuPi1ss"
FORECAST_SHEET_ID  = "11KuyI9z3N8HrWN8i4_ZnDFkcFiufna3ecwjsWR4jhJs"
CAP_FORECAST_ID    = "1Dk8nrTPcOexxYBF1cD0iKAYeKsBXJRmwF44QNv43fus"  # WFM Capacity Planning — per-interval forecast
AGENT_STATUS_ID    = "15k9ahqgtuaigy4c7YNe2qy-OimAsjlbDQqd6F3VEt_g"


CALL_STATUS_ID     = "1fWJkm6Woc5bKYkI2tNRZx6_Khrsje2GOsXL4YyPZ-bo"
KA_SHEET_ID        = "1sjLs3WxBdSLb-9rdTRAVZc-VTKMljXL7ym8dQ-mgAow"
ROSTER_SHEET_ID    = os.environ.get(
    "ROSTER_SHEET_KEY", "1SM4WDQFWhy-zBwMLCWER863m95Hi4mQjG7wEogOOR4U")

# ── CP Raw Status → Human Readable ────────────────────────────────────────
CP_STATUS_MAP = {
    "WA9c4e93d1de9b472ebb7e3b89426df574":           "Ready",
    "WAd1f6c9952f3d04482bb9b6b28dd9819e":           "Offline",
    "WA9a7153cbe2257eb452ff60067002087b":           "On-call",
    "WA55e3a3eb3b9df10c69b902682ee87fbf":           "Cool-Down",    # Cool-Down in CP (post-call wrap-up)
    "WA3f5159a6c417b72f73c8128f5d3cc0ed":           "Unavailable",
    "WA4090114d9b863e27e396b747e7071d7b":           "No-Answer",
    "WAa8d8e71d8d79415ba585a53ca493521f":           "Rejected",
    "WA960ed92496da0b023b5e69e4f5c783a2":           "On Break",
    "WAa513fc91db454de83aefda3f5b689c5e":           "Lunch",
    "WA009e46be32c8efb7132cfda1b5a42ea8":           "ooq Client Account Work",
    "WA641c3fceafe43e72da443995033777a0":           "No-Mic",
    "WA17446c1b845fe8159134a9e2da69dc8c":           "Web Leads",
    "WA84686f853061b6271f6a43dd6a3c6384":           "Long Distance",
    "WA0d324b251da94519738a9ee65fb89152":           "eChat",
    "WA8cb16293fa633f5b155a5579c4199992":           "Leader on Duty",
    "WAea2d79d49903a266c61e67a346ecf926":           "ooq Meeting",
    "WA61f0205ad33fc03a62d1b21c6edd4cf5":           "ooq Training",
    "WA1eb894d4d481b80387166e248bea38c4":           "ooq Coaching",
    "WAaaf8f99bad17314f6908a2b20faf208a":           "ooq After Shift",
    "WAcafc4a5f5503c8fbab4e19697b81edb3":           "ooq System Issue",
    "WA2a678c82745f6019e3b4fdc994af7f18":           "ooq Personal",
    "WA3f5159a6c417b72f73c8128f5d3cc0ed Duplicate": "Cascade",
}

# Queue ID (DBID) → (Combined LOB, Language)
# Source: Queue ID & CP Status List V2 Sheet1 active queues

# ── Planning Unit → Combined LOB ──────────────────────────────────────────
PU_TO_LOB = {
    "SS Sales":              "SS Sales",
    "PS Sales":              "PS Sales",
    "PS Care":               "PS Care",
    "PS Case Manager":       "PS Case Manager",
    "SS Case Management":    "SS Case Management",
    "MoveBuddy":             "MoveBuddy",
    "I.T Support":           "I.T Support",
    "Web Leads SS Inbound":  "Web Leads SS Inbound",
    "Web Leads SS":          "Web Leads SS",
    "Web Leads PS":          "Web Leads PS",
    "EChat":                 "EChat",
    "ERO":                   "ERO",
    "Admin":                 "Admin",
    "Team Lead":             "Team Lead",
    "PS Admin":              "PS Admin",
    "Workforce Management":  "Workforce Management",
}

# LOBs excluded from attendance/schedule tracking — don't log into Call Potential
ATTENDANCE_EXCLUDED_LOBS = {
    "Workforce Management",
}

# Employees excluded from ALL reporting — third-party contractors not managed by RC WFM
# Identified by Current ID and GUID from the PeopleWare roster
# Employees excluded from ALL reporting — third-party contractors not managed by RC WFM
# Identified by Current ID and GUID from the PeopleWare roster
EXCLUDED_CURRENT_IDS = {
    # Numeric Current IDs
    "37939",  # Saajid Junaid Quraishi               — IT Support Mouritech
    "39367",  # Sanjana Allad                         — IT Support Mouritech
    "39364",  # Venkat Sai Komminen                   — IT Support Mouritech
    "40598",  # Sai Tharun Kumar Reddy Belaganti      — IT Support Mouritech
    "38291",  # Venkata Alekhya Yadala                — IT Support Mouritech
    "27642",  # Teresa Livingston  (Current ID — Personnel # 1134)
    "27643",  # Adam Thibodeau     (Current ID — Personnel # 1097)
    # GUIDs (used by CP agent status sheet as user_id)
    "0DF5003C-0335-0633-89B5-46E33296B794",  # Saajid Junaid Quraishi
    "254B8AA4-B8C0-F880-FE4F-5C9C2B47AEBE",  # Sanjana Allad
    "EA6E10B5-ECD2-3D7B-8E45-DF4960D36882",  # Venkat Sai Komminen
    "1081CC73-0AD7-8BD2-87E2-7634097DA61C",  # Sai Tharun Kumar Reddy Belaganti
    "E8C822D3-6788-32B1-239B-8D89DA994BE1",  # Venkata Alekhya Yadala
}

# Queue IDs that represent the primary inbound queue per LOB.
# Used ONLY for OTF (Offered vs Forecast) calculation — ensures forecast
# comparison uses the same queue scope as PeopleWare's forecast workload.
# All other metrics (Offered, Answered, SVL, ASA) use ALL queues to match
# the automated interval report from the other department.
LOB_PRIMARY_QUEUES = {
    "I.T Support": {968},   # 968 = SVI IT Level 1 (main forecast workload)
                            # 832 = Helpdesk Backup, 1512 = Level 2, 1513 = Combo
                            # — all included in call counting, excluded from OTF only
}

# ── WorkloadMapping — from Sheet2 of Queue ID & Workload Mapping ──────────
def _build_fc_lookup_from_v1(fc_rows, target_str, fc_lookup):
    """Populate fc_lookup from Forecasts V1.0 rows (tall format with workload_name/metric columns)."""
    INTERVAL_FC_WORKLOADS = {
        "SS Sales Combined","SS CCA Combined","SS CCA CC Combined","SS CCA Store Combined",
        "SS Sales EN","SS Sales FR",
        "PS Sales Combined","PS Sales EN","PS Sales FR",
        "PS Care Combined","PS Care EN","PS Care FR",
        "PS Case Manager",
        "Web Leads SS Combined","Web Leads SS EN","Web Leads SS FR",
        "Web Leads SS Inbound Combined","Web Leads SS Inbound EN","Web Leads SS Inbound FR",
        "Web Leads PS Combined","Web Leads PS EN","Web Leads PS FR",
        "MoveBuddy","I.T Support",
    }
    for r in fc_rows:
        interval = str(r.get("interval_start", "") or "").strip()
        ftype    = str(r.get("forecast_type", "") or "").lower()
        metric   = str(r.get("metric", "") or "").lower()
        wname    = str(r.get("workload_name", "") or "").strip()
        if not interval.startswith(target_str): continue
        if "operational" not in ftype: continue
        if wname not in INTERVAL_FC_WORKLOADS: continue
        combined_lob = WORKLOAD_MAPPING.get(wname, wname)
        dt = _parse_datetime_str(interval)
        if not dt: continue
        bucket_min = (dt.minute // 30) * 30
        bucket_str = dt.replace(minute=bucket_min, second=0, microsecond=0).strftime("%H:%M:%S")
        key = (combined_lob, bucket_str)
        if key not in fc_lookup:
            fc_lookup[key] = {"offered": 0, "aht": []}
        try: val = float(r.get("value", 0) or 0)
        except: val = 0
        if "offered" in metric:
            fc_lookup[key]["offered"] += val
        elif "handling" in metric or "aht" in metric:
            if val > 0: fc_lookup[key]["aht"].append(val)


# ── Mapping: WFM Capacity Planning sheet column headers → interval-report LOB names ──
# Full header names from the sheet; update here when new LOBs are added.
CAP_FC_COLS = {
    "I.T Support":              ["I.T Support"],
    "MoveBuddy":                ["MoveBuddy"],
    "PS Care":                  ["PS Care Combined"],
    "PS Care EN":               ["PS Care EN"],
    "PS Care FR":               ["PS Care FR"],
    "PS Case Manager":          ["PS Case Manager"],
    "PS Sales":                 ["PS Sales Combined"],
    "PS Sales EN":              ["PS Sales EN"],
    "PS Sales FR":              ["PS Sales FR"],
    "SS Case Management":       ["SS CCA CC Combined"],  # CC only — Store queues not in QUEUE_MAP
    "SS Case Management EN":    ["SS CCA CC EN"],
    "SS Case Management FR":    ["SS CCA CC FR"],
    "SS Sales":                 ["SS Sales Combined"],
    "SS Sales EN":              ["SS Sales EN"],
    "SS Sales FR":              ["SS Sales FR"],
    "Web Leads SS Inbound":     ["Web Leads SS Inbound Combined"],
    "Web Leads SS Inbound EN":  ["Web Leads SS Inbound EN"],
    "Web Leads SS Inbound FR":  ["Web Leads SS Inbound FR"],
}

_CAP_FC_RAW   = None   # (fc_hdrs, fc_rows, aht_by_ts) — loaded once per day
_CAP_FC_RAW_TS = 0
_CAP_FC_RAW_TTL = 86400  # re-read at most once per 24 hours

def _get_cap_fc_raw(gc):
    """Load FORECAST RAW + AHT RAW once per day and hold in memory.
    Subsequent calls within 24h are free — no API reads at all.
    The raw data is ~12MB as lists-of-lists, well within the 512MB limit."""
    global _CAP_FC_RAW, _CAP_FC_RAW_TS
    import time
    now = time.time()
    if _CAP_FC_RAW is not None and now - _CAP_FC_RAW_TS < _CAP_FC_RAW_TTL:
        return _CAP_FC_RAW
    try:
        log.info("cap_forecast: loading full year from sheet (will cache 24h)")
        doc     = gc.open_by_key(CAP_FORECAST_ID)
        fc_all  = doc.worksheet("FORECAST RAW").get_all_values()
        aht_all = doc.worksheet("AHT RAW").get_all_values()
        fc_hdrs  = fc_all[0]  if fc_all  else []
        fc_rows  = fc_all[1:] if len(fc_all)  > 1 else []
        aht_hdrs = aht_all[0] if aht_all else []
        # Index AHT by timestamp so join is O(1) per FC row
        aht_by_ts = {}
        for row in (aht_all[1:] if len(aht_all) > 1 else []):
            ts = row[0] if row else ""
            if ts:
                aht_by_ts[ts] = dict(zip(aht_hdrs, row))
        del aht_all
        import gc as _gc; _gc.collect()
        _CAP_FC_RAW = (fc_hdrs, fc_rows, aht_by_ts)
        _CAP_FC_RAW_TS = now
        log.info(f"cap_forecast: cached {len(fc_rows)} FC rows, {len(aht_by_ts)} AHT rows")
        return _CAP_FC_RAW
    except Exception as e:
        log.warning(f"_get_cap_fc_raw error: {e}")
        return ([], [], {})

def _read_cap_forecast(gc, target_str):
    """Filter the cached full-year forecast to a single date and return fc_lookup."""
    try:
        fc_hdrs, fc_rows, aht_by_ts = _get_cap_fc_raw(gc)
        lookup = {}
        for row in fc_rows:
            ts = row[0] if row else ""
            if not ts.startswith(target_str):
                continue
            dt = _parse_datetime_str(ts)
            if not dt:
                continue
            bucket_min = (dt.minute // 30) * 30
            bucket_str = dt.replace(minute=bucket_min, second=0,
                                    microsecond=0).strftime("%H:%M:%S")
            r     = dict(zip(fc_hdrs, row))
            aht_r = aht_by_ts.get(ts, {})
            for lob, cols in CAP_FC_COLS.items():
                offered  = 0
                aht_vals = []
                for col in cols:
                    try: offered += float(r.get(col) or 0)
                    except (ValueError, TypeError): pass
                    try:
                        av = float(aht_r.get(col) or 0)
                        if av > 0: aht_vals.append(av)
                    except (ValueError, TypeError): pass
                if offered <= 0:
                    continue
                key = (lob, bucket_str)
                if key not in lookup:
                    lookup[key] = {"offered": 0, "aht": []}
                lookup[key]["offered"] += offered
                lookup[key]["aht"].extend(aht_vals)
        for v in lookup.values():
            al = v["aht"]
            v["aht"] = round(sum(al) / len(al)) if al else 0
        log.info(f"cap_forecast: {len(lookup)} LOB/interval entries for {target_str}")
        return lookup
    except Exception as e:
        log.warning(f"_read_cap_forecast error: {e}")
        return {}


WORKLOAD_MAPPING = {
    # ── SS Sales ──
    "SS Sales Combined":              "SS Sales",
    "SS Sales EN":                    "SS Sales EN",
    "SS Sales FR":                    "SS Sales FR",
    # ── SS Case Management ──
    "SS CCA Combined":                "SS Case Management",
    "SS CCA Combined EN":             "SS Case Management EN",
    "SS CCA Combined FR":             "SS Case Management FR",
    "SS CCA CC Combined":             "SS Case Management",
    "SS CCA CC EN":                   "SS Case Management EN",
    "SS CCA CC FR":                   "SS Case Management FR",
    "SS CCA Store Combined":          "SS Case Management",
    "SS CCA Store EN":                "SS Case Management EN",
    "SS CCA Store FR":                "SS Case Management FR",
    "SS CM CV EN":                    "SS Case Management EN",
    "SS CM CV FR":                    "SS Case Management FR",
    "SS CM Cases EN":                 "SS Case Management EN",
    "SS CM Cases FR":                 "SS Case Management FR",
    "SS CCA Payment":                 "SS Case Management",
    # ── PS Sales ──
    "PS Sales Combined":              "PS Sales",
    "PS Sales EN":                    "PS Sales EN",
    "PS Sales FR":                    "PS Sales FR",
    "PS Sales & Care Combined":       "PS Combined",
    # ── PS Care ──
    "PS Care Combined":               "PS Care",
    "PS Care EN":                     "PS Care EN",
    "PS Care FR":                     "PS Care FR",
    # ── PS Case Manager ──
    "PS Case Manager":                "PS Case Manager",
    # ── Web Leads SS ──
    "Web Leads SS Combined":          "Web Leads SS",
    "Web Leads SS EN":                "Web Leads SS EN",
    "Web Leads SS FR":                "Web Leads SS FR",
    # ── Web Leads SS Inbound ──
    "Web Leads SS Inbound Combined":  "Web Leads SS Inbound",
    "Web Leads SS Inbound EN":        "Web Leads SS Inbound EN",
    "Web Leads SS Inbound FR":        "Web Leads SS Inbound FR",
    # ── Web Leads PS ──
    "Web Leads PS Combined":          "Web Leads PS",
    "Web Leads PS EN":                "Web Leads PS EN",
    "Web Leads PS FR":                "Web Leads PS FR",
    "Web Leads PS & SS Combined":     "Web Leads Combined",
    # ── Other ──
    "MoveBuddy":                      "MoveBuddy",
    "I.T Support":                    "I.T Support",
}

VTO_ACTIVITIES     = {"vto", "voluntary time off"}
PARTIAL_ACTIVITIES = {
    "appointment", "bereavement", "late", "leave of absence", "loa",
    "system outage", "unpaid time off", "unpaid", "vacation", "weather",
    "store visit",
}
EARLY_LEAVE_ACTIVITIES = {
    "early leave", "early departure", "left early", "early release",
    "early out", "early end", "departed early",
}

# Business hours per LOB (for SVL/OTF/interval filtering) — times in EST
_DEFAULT_BIZ = ((8,0),(22,0),(9,0),(19,0),(9,0),(18,0))
LOB_BIZ_HOURS = {}   # all LOBs use the default; add overrides here if needed

def _in_business_hours(dt, lob=None):
    """Return True if datetime is within business hours for the given LOB."""
    from datetime import time as _time
    hours = LOB_BIZ_HOURS.get(lob, _DEFAULT_BIZ) if lob else _DEFAULT_BIZ
    dow = dt.weekday()  # 0=Mon, 6=Sun
    t   = dt.time()
    if dow < 5:    # Mon-Fri
        return _time(*hours[0]) <= t < _time(*hours[1])
    elif dow == 5: # Sat
        return _time(*hours[2]) <= t < _time(*hours[3])
    else:          # Sun
        return _time(*hours[4]) <= t < _time(*hours[5])

# ── Excluded personnel numbers ────────────────────────────────────────────
EXCLUDED_PERSONNEL = {"1097", "1134", "1002", "1005", "1329", "1209", "1003"}

# ── CP timezone offset (CST = EST - 1hr) ──────────────────────────────────
CP_TZ_OFFSET_HOURS = 1  # add 1hr to convert CST → EST


# ══════════════════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════════════════

def _get_client():
    from oauth2client.service_account import ServiceAccountCredentials
    import gspread
    scope = ["https://spreadsheets.google.com/feeds",
             "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name(
        os.environ.get("SERVICE_ACCOUNT_FILE", "service_account.json"), scope)
    return gspread.authorize(creds)


def _read_sheet(gc, sheet_id, tab=None, date_filter_col=None, date_filter_val=None):
    """
    Thread-safe cached sheet reader.
    - Fresh cache hit  → return immediately, no API call
    - Cache miss/stale → acquire per-key lock, fetch once, release
    - Concurrent miss  → second thread waits for lock, then gets cache
    - API failure      → return stale data if available, else []

    date_filter_col/val: if set, only return rows where that column starts
    with date_filter_val. Used to avoid loading the entire forecast sheet.
    """
    cache_key = f"{sheet_id}:{tab}:{date_filter_val or ''}"

    # Fast path — fresh cache available, no locking needed
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    # Slow path — need to fetch; use per-key lock so only one thread fetches
    key_lock = _get_key_lock(cache_key)
    acquired = key_lock.acquire(timeout=RTM_LOCK_WAIT)

    if not acquired:
        log.warning(f"Lock timeout for {sheet_id} tab={tab} — serving stale")
        stale = _cache_get_stale(cache_key)
        return stale if stale is not None else []

    try:
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached

        doc  = gc.open_by_key(sheet_id)
        ws   = doc.get_worksheet(0) if tab is None else doc.worksheet(tab)

        # Retry once on 429 quota errors with a short backoff
        rows = []
        for attempt in range(2):
            try:
                if date_filter_col and date_filter_val:
                    all_rows = ws.get_all_records()
                    rows = [r for r in all_rows
                            if str(r.get(date_filter_col, "") or "").startswith(date_filter_val)]
                    log.info(f"Sheet fetched {sheet_id} tab={tab}: {len(rows)} rows (filtered)")
                else:
                    rows = ws.get_all_records()
                    log.info(f"Sheet fetched {sheet_id} tab={tab}: {len(rows)} rows")
                break
            except Exception as e:
                if "429" in str(e) and attempt == 0:
                    log.warning(f"429 on {sheet_id} tab={tab} — retrying in 2s")
                    time.sleep(2)
                    continue
                raise

        # Don't cache empty results for live data sheets — IMPORTRANGE
        # returns 0 rows briefly during its refresh cycle. Serve stale data
        # instead so the dashboard doesn't show blank attendance.
        # Don't cache suspiciously small results for critical sheets.
        # IMPORTRANGE sheets return 0 during refresh; EMPLOYEES/roster should
        # never be genuinely empty — a 0 or near-0 result means a transient
        # Google API issue and we should serve stale rather than cache it.
        LIVE_SHEETS = {AGENT_STATUS_ID, CALL_STATUS_ID}
        CRITICAL_MIN = {
            AGENT_STATUS_ID:  5,
            CALL_STATUS_ID:   5,
            ROSTER_SHEET_ID:  50,   # EMPLOYEES — normally 450+ rows
        }
        min_rows = CRITICAL_MIN.get(sheet_id, 0)
        if min_rows and len(rows) < min_rows:
            stale = _cache_get_stale(cache_key)
            if stale is not None and len(stale) >= min_rows:
                stale_age = time.time() - (_rtm_cache.get(cache_key, (0, None))[0] or 0)
                if sheet_id in LIVE_SHEETS and stale_age >= 600:
                    pass  # Live sheets: don't serve stale older than 10 min
                else:
                    log.warning(f"Sheet {sheet_id} tab={tab} returned only {len(rows)} rows — serving stale ({len(stale)} rows)")
                    return stale
            log.warning(f"Sheet {sheet_id} tab={tab} returned only {len(rows)} rows, no usable stale")

        return _cache_set(cache_key, rows)

    except Exception as e:
        log.error(f"Sheet read FAILED {sheet_id} tab={tab}: {repr(e)}")
        stale = _cache_get_stale(cache_key)
        if stale is not None:
            log.warning(f"Serving stale cache for {sheet_id} tab={tab}")
            return stale
        return []

    finally:
        key_lock.release()


def _parse_date_flexible(val):
    """
    Parse a date value from a Google Sheet cell that could come back in
    any of several display formats depending on the cell's number format
    (Sheets returns whatever text is currently displayed, not a fixed
    format). Returns a date object, or None if unparseable.
    """
    if val is None:
        return None
    s = str(val).strip()
    if not s:
        return None
    # Try common explicit formats first
    for fmt in ("%Y/%m/%d", "%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y",
                "%B %d, %Y", "%b %d, %Y", "%d-%b-%Y", "%m-%d-%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    # Try ISO parsing (handles "2026-06-29T00:00:00" etc.)
    try:
        return datetime.fromisoformat(s.split("T")[0]).date()
    except (ValueError, IndexError):
        pass
    # Google Sheets serial date number (days since 1899-12-30)
    try:
        serial = float(s)
        return (datetime(1899, 12, 30) + timedelta(days=serial)).date()
    except (ValueError, OverflowError):
        pass
    return None


def _norm_name(name):
    """Normalise name for matching: lowercase, strip, collapse spaces."""
    return " ".join(str(name or "").lower().split())

def _fill_down_blocks(rows):
    """
    PeopleWare schedule block exports repeat the Employee/Personnel Number
    only on the FIRST row per agent — subsequent activity rows have blank
    Employee and Personnel Number fields. Fill those down so every row
    carries its agent's identity.
    """
    filled = []
    cur_emp  = ""
    cur_pers = ""
    for r in rows:
        emp  = str(r.get("Employee",          "") or "").strip()
        pers = str(r.get("Personnel Number",  "") or "").strip()
        if emp or pers:
            cur_emp  = emp
            cur_pers = pers
        filled_r = dict(r)
        if not filled_r.get("Employee"):
            filled_r["Employee"]         = cur_emp
        if not filled_r.get("Personnel Number"):
            filled_r["Personnel Number"] = cur_pers
        filled.append(filled_r)
    return filled


def _parse_time_str(val):
    if not val:
        return None
    s = str(val).strip()
    for fmt in ("%H:%M:%S", "%H:%M", "%I:%M %p", "%I:%M:%S %p"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    return None


def _parse_datetime_str(val):
    """Parse datetime string — handles single-digit hours like '2026-06-17 7:30:00'."""
    if not val:
        return None
    s = str(val).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
                "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %I:%M:%S %p",
                "%m/%d/%Y %I:%M %p", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    # Handle single-digit hour by zero-padding
    if len(s) >= 10 and s[10] == ' ':
        parts = s.split(' ', 1)
        if len(parts) == 2:
            time_parts = parts[1].split(':')
            if len(time_parts) >= 2 and len(time_parts[0]) == 1:
                padded = parts[0] + ' 0' + parts[1]
                try:
                    return datetime.strptime(padded, "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    try:
                        return datetime.strptime(padded, "%Y-%m-%d %H:%M")
                    except ValueError:
                        pass
    return None


def _cp_to_est(dt):
    """Convert CP datetime (CST) to EST by adding 1 hour."""
    if dt is None:
        return None
    return dt + timedelta(hours=CP_TZ_OFFSET_HOURS)


def _derive_shift_status(from_str, actual_start_str, now):
    """
    Derive ShiftStatus and ShiftTimingStatus.
    actual_start_str is already in EST.
    Returns (shift_status, timing_status)
    """
    from_t = _parse_time_str(from_str)
    if not from_t:
        return "Unknown", ""

    shift_start = from_t.replace(year=now.year, month=now.month, day=now.day)

    if now < shift_start:
        return "Scheduled - Not Yet Started", "Scheduled - Not Yet Started"

    actual = _parse_time_str(actual_start_str) or _parse_datetime_str(actual_start_str)
    if actual:
        actual_dt = actual.replace(year=now.year, month=now.month, day=now.day)
        delta_min = (actual_dt - shift_start).total_seconds() / 60
        if delta_min < -1:
            return "Present", "Started Early"
        elif delta_min < 2:
            return "Present", "Arrived On Time"
        else:
            return "Present", "Started Late"
    else:
        return "Absent", "No Show"


def _derive_early_leave(to_str, last_offline_str, now):
    """
    Return True if employee left early (last offline before scheduled end).
    last_offline_str is already in EST.
    No time cutoff — early leave should be visible all day.
    """
    if not last_offline_str or not to_str:
        return False
    to_t = _parse_time_str(to_str)
    if not to_t:
        return False
    shift_end = to_t.replace(year=now.year, month=now.month, day=now.day)
    lo = _parse_time_str(last_offline_str) or _parse_datetime_str(last_offline_str)
    if not lo:
        return False
    lo_dt = lo.replace(year=now.year, month=now.month, day=now.day)
    # Left early = last offline more than 2 min before shift end
    return lo_dt < (shift_end - timedelta(minutes=2))


# ══════════════════════════════════════════════════════════════════════════
#  DATA LOADERS
# ══════════════════════════════════════════════════════════════════════════

def _load_roster(gc):
    """
    Returns:
      personnel_map: personnel_number (str) → info dict
      name_map:      normalised name (str)  → info dict  (for CP join)
      title_map:     normalised Title (str) → info dict  (for backup CSV join)
    """
    rows = _read_sheet(gc, ROSTER_SHEET_ID, tab="EMPLOYEES")
    personnel_map = {}
    name_map      = {}
    title_map     = {}
    for r in rows:
        emp_id  = str(r.get("Employee ID", "") or "").strip()
        pers    = str(r.get("Personnel #", "") or "").strip()
        fname   = str(r.get("First Name",  "") or "").strip()
        lname   = str(r.get("Last Name",   "") or "").strip()
        name    = f"{fname} {lname}".strip()
        pu      = str(r.get("Planning Unit", "") or "").strip()
        lob     = PU_TO_LOB.get(pu, pu)
        status  = str(r.get("Status", "") or "").strip()
        title   = str(r.get("Title",  "") or "").strip()
        if status == "Inactive":
            continue
        current_id = str(r.get("Current ID", "") or "").strip()
        if current_id in EXCLUDED_CURRENT_IDS:
            continue
        info = {"lob": lob, "employee_id": emp_id,
                "personnel_number": pers, "name": name,
                "current_id": current_id}
        if pers:
            personnel_map[pers] = info
        if name:
            name_map[_norm_name(name)] = info
        if title and title != "N/A":
            title_map[_norm_name(title)] = info
    return personnel_map, name_map, title_map


def _get_agent_status_rows(gc, override_raw_rows=None):
    """
    Return Agent Status sheet rows — either live from Google Sheets,
    or from a manually uploaded backup CSV in the exact same raw format
    (user_id, start_time, c_activity_sid). Used by every function that
    reads the Agent Status sheet, so a single backup upload powers
    attendance, cascades, and Status Under 5 Sec consistently.
    """
    if override_raw_rows:
        return override_raw_rows
    return _read_sheet(gc, AGENT_STATUS_ID)


def _load_agent_status(gc, override_agent_rows=None, override_raw_rows=None):
    """
    Returns per normalised agent name (joined by name, not ID):
      - most_recent_status (decoded, EST)
      - actual_start_time  (first Ready/On-call today, EST)
      - last_offline_time  (EST)
    CP times are CST — converted to EST (+1hr).

    override_agent_rows: list of rows from the SUMMARY backup CSV
      (Agent Name, First Login, Log Off columns). Gives attendance timing
      only — not enough for cascades or Status Under 5 Sec.

    override_raw_rows: list of rows in the FULL granular format — same
      exact columns as the live Agent Status Google Sheet (user_id,
      start_time, c_activity_sid). Manually pulled from Call Potential
      when the live feed is down. Powers everything the live sheet does:
      attendance, cascades, Status Under 5 Sec, early leave detection.
    """
    # ── Full granular backup CSV path (same processing as live sheet) ────────
    if override_raw_rows:
        rows = override_raw_rows
        all_dates = set()
        for r in rows:
            st = str(r.get("start_time", "") or "").strip()
            if st and len(st) >= 10:
                all_dates.add(st[:10])
        use_date = sorted(all_dates)[-1] if all_dates else _today_est().strftime("%Y-%m-%d")

        by_uid = {}
        for r in rows:
            uid = str(r.get("user_id", "") or "").strip()
            st  = str(r.get("start_time", "") or "").strip()
            sid = str(r.get("c_activity_sid", "") or "").strip()
            if not uid or not st.startswith(use_date):
                continue
            dt_cst = _parse_datetime_str(st)
            if not dt_cst:
                continue
            dt_est = _cp_to_est(dt_cst)
            status = CP_STATUS_MAP.get(sid, sid)
            by_uid.setdefault(uid, []).append({"dt": dt_est, "status": status})

        result = {}
        for uid, events in by_uid.items():
            events.sort(key=lambda x: x["dt"])
            recent_status = events[-1]["status"] if events else ""

            actual_start = None
            for e in events:
                if e["status"] in ("Ready", "On-call", "Cool-Down",
                                   "ooq Client Account Work", "Leader on Duty"):
                    actual_start = e["dt"].strftime("%H:%M:%S")
                    break

            last_offline      = None
            last_offline_idx  = None
            for i, e in enumerate(reversed(events)):
                if e["status"] == "Offline":
                    last_offline     = e["dt"].strftime("%H:%M:%S")
                    last_offline_idx = len(events) - 1 - i
                    break

            logged_back_in = False
            if last_offline_idx is not None:
                for e in events[last_offline_idx + 1:]:
                    if e["status"] not in ("Offline", ""):
                        logged_back_in = True
                        break

            result[uid] = {
                "recent_status":     recent_status,
                "actual_start_time": actual_start or "",
                "last_offline":      last_offline or "",
                "logged_back_in":    logged_back_in,
                "has_activity":      any(e["status"] not in ("Offline", "")
                                         for e in events),
            }
        return result

    # ── Summary backup CSV path ──────────────────────────────────────────────
    if override_agent_rows:
        result = {}
        for r in override_agent_rows:
            full_title = str(r.get("Agent Name", "") or "").strip()
            login  = str(r.get("First Login", "") or "").strip()
            logoff = str(r.get("Log Off",     "") or "").strip()
            if not full_title:
                continue

            actual_start = ""
            last_offline = ""
            try:
                if login:
                    from datetime import datetime as _dt
                    dt = _dt.strptime(login, "%m/%d/%Y %H:%M:%S")
                    actual_start = dt.strftime("%H:%M:%S")
            except Exception:
                pass
            try:
                if logoff:
                    from datetime import datetime as _dt
                    dt = _dt.strptime(logoff, "%m/%d/%Y %H:%M:%S")
                    last_offline = dt.strftime("%H:%M:%S")
            except Exception:
                pass

            # Key by the full title string (normalised) — get_schedule_data
            # will join this against the roster Title column
            norm_title = _norm_name(full_title)
            result[norm_title] = {
                "recent_status":     "Offline" if last_offline else "Ready",
                "actual_start_time": actual_start,
                "last_offline":      last_offline,
                "logged_back_in":    False,  # CSV has no intermediate events to check
                "has_activity":      bool(actual_start),  # summary CSV: login implies activity
            }
        return result

    # ── Live CP sheet path ───────────────────────────────────────────────────
    rows = _read_sheet(gc, AGENT_STATUS_ID)
    today_str     = _today_est().strftime("%Y-%m-%d")
    yesterday_str = (_today_est() - timedelta(days=1)).strftime("%Y-%m-%d")

    # Find which date has data — only use today's data
    # If today has no data yet (CP script hasn't run), return empty
    # rather than showing yesterday's stale login times
    all_dates = set()
    for r in rows:
        st = str(r.get("start_time", "") or "").strip()
        if st and len(st) >= 10:
            all_dates.add(st[:10])

    if today_str in all_dates:
        use_date = today_str
        log.info(f"Agent status using TODAY: {use_date}")
    else:
        log.info(f"Agent status: no data for today ({today_str}), "
                 f"available={sorted(all_dates)[-3:]} — skipping")
        return {}  # Return empty — don't use yesterday's times

    # Need agent name lookup — load roster names separately
    # We'll build name→events mapping using user_id as intermediate
    # then attach names via a second pass if we have a CP name field
    # For now group by user_id and return; name join done in get_schedule_data
    by_uid = {}
    for r in rows:
        uid = str(r.get("user_id", "") or "").strip()
        st  = str(r.get("start_time", "") or "").strip()
        sid = str(r.get("c_activity_sid", "") or "").strip()
        if not uid or not st.startswith(use_date):
            continue
        dt_cst = _parse_datetime_str(st)
        if not dt_cst:
            continue
        dt_est = _cp_to_est(dt_cst)  # convert CST → EST
        status = CP_STATUS_MAP.get(sid, sid)
        if uid not in by_uid:
            by_uid[uid] = []
        by_uid[uid].append({"dt": dt_est, "status": status})

    result = {}
    for uid, events in by_uid.items():
        events.sort(key=lambda x: x["dt"])
        recent_status = events[-1]["status"] if events else ""

        actual_start = None
        for e in events:
            if e["status"] in ("Ready", "On-call", "Cool-Down",
                               "ooq Client Account Work", "Leader on Duty"):
                actual_start = e["dt"].strftime("%H:%M:%S")
                break

        last_offline = None
        last_offline_idx = None
        for i, e in enumerate(reversed(events)):
            if e["status"] == "Offline":
                last_offline     = e["dt"].strftime("%H:%M:%S")
                last_offline_idx = len(events) - 1 - i
                break

        # Check if there is any non-Offline activity AFTER the last offline event.
        # If yes, the agent logged back in — last_offline is a false early leave.
        logged_back_in = False
        if last_offline_idx is not None:
            for e in events[last_offline_idx + 1:]:
                if e["status"] not in ("Offline", ""):
                    logged_back_in = True
                    break

        result[uid] = {
            "recent_status":     recent_status,
            "actual_start_time": actual_start or "",
            "last_offline":      last_offline or "",
            "logged_back_in":    logged_back_in,
            "has_activity":      any(e["status"] not in ("Offline", "")
                                     for e in events),
        }
    return result


def _load_schedule_notes(gc, target_date=None):
    """
    Return dict of normalised employee name → schedule note for today.
    VTO = Voluntary Time Off
    Vacation/Appointment = partial day note
    """
    if target_date is None:
        target_date = _today_est()
    rows = _read_sheet(gc, SCHEDULE_BLOCKS_ID)
    notes = {}  # name → note label
    for r in rows:
        row_date = _parse_date_flexible(r.get("Date", ""))
        activity = str(r.get("Activity", "") or "").strip()
        act_lower = activity.lower()
        if row_date != target_date:
            continue
        name = str(r.get("Employee Name", "") or "").strip()
        if not name:
            continue
        norm = _norm_name(name)
        if act_lower in VTO_ACTIVITIES:
            notes[norm] = "VTO"
        elif act_lower in EARLY_LEAVE_ACTIVITIES:
            notes[norm] = "Early Leave"
        elif act_lower in PARTIAL_ACTIVITIES:
            notes[norm] = activity  # e.g. "Vacation" or "Appointment"
    return notes


def _load_ka(gc):
    """Return set of normalised names who completed KA (any date)."""
    rows = _read_sheet(gc, KA_SHEET_ID)
    today_str = _today_est().strftime("%Y-%m-%d")
    # Return names completed today AND all time for the schedule page
    completed_today = set()
    completed_all   = set()
    today_est = _today_est()
    for r in rows:
        name     = str(r.get("Name", "") or "").strip()
        comp_dt  = str(r.get("Completion time", "") or
                       r.get("completion_time", "") or "").strip()
        if name:
            completed_all.add(_norm_name(name))
            if comp_dt:
                # Handle both YYYY-MM-DD and MM/DD/YYYY formats
                parsed = _parse_datetime_str(comp_dt)
                if parsed and parsed.date() == today_est:
                    completed_today.add(_norm_name(name))
    return completed_today, completed_all


# ══════════════════════════════════════════════════════════════════════════
#  PUBLIC FUNCTIONS
# ══════════════════════════════════════════════════════════════════════════

def get_schedule_data(target_date=None, gc=None, override_agent_rows=None,
                       override_raw_agent_rows=None):
    """
    Return today's schedule rows enriched with LOB, ShiftStatus,
    ShiftTimingStatus, actual start (EST), last offline (EST),
    recent status, VTO flag, KA complete.
    Joins agent status by CP user_id → roster name.

    override_agent_rows: rows from the SUMMARY backup agent status CSV
                         (Agent Name, First Login, Log Off). Attendance
                         timing only.
    override_raw_agent_rows: rows from the FULL granular backup CSV —
                         same format as the live sheet (user_id, start_time,
                         c_activity_sid). Used when the live CP sheet has
                         stopped updating mid-day; powers everything the
                         live sheet does (cascades, Status Under 5 Sec, etc).
    """
    if target_date is None:
        target_date = _today_est()

    target_str = target_date.strftime("%Y/%m/%d")
    now        = _now_est()

    try:
        if gc is None:
            gc = _get_client()
    except Exception as e:
        log.error(f"gspread auth error: {e}")
        return []

    rows                              = _read_sheet(gc, SCHEDULE_SHEET_ID, tab="Schedule")
    personnel_map, name_map, title_map = _load_roster(gc)

    # When backup rows are explicitly provided, use them directly.
    # When live comes back, the caller stops passing override rows.
    using_backup = False
    if override_raw_agent_rows:
        log.info("get_schedule_data: using FULL granular backup agent status CSV")
        agent_status = _load_agent_status(gc, override_raw_rows=override_raw_agent_rows)
        # Raw override is uid-keyed, same shape as live — join like live path
    elif override_agent_rows:
        log.info("get_schedule_data: using summary backup agent status CSV")
        agent_status = _load_agent_status(gc, override_agent_rows=override_agent_rows)
        using_backup = True
    else:
        agent_status = _load_agent_status(gc)

    ka_today, ka_all   = _load_ka(gc)
    schedule_notes     = _load_schedule_notes(gc, target_date)

    # Build name→agent_status lookup
    ci_to_ag = {}
    if using_backup:
        # Backup: agent_status keyed by normalised Title (e.g. "ss ib sb en chantelle francis")
        # Join via title_map: Title → roster info → normalised name
        for norm_title, info in title_map.items():
            if norm_title in agent_status:
                norm_name = _norm_name(info["name"])
                ci_to_ag[norm_name] = agent_status[norm_title]
    else:
        # Live: keyed by user_id (Current ID), join via name_map
        for norm_key, info in name_map.items():
            current_id = str(info.get("current_id", "") or "").strip()
            if current_id and current_id in agent_status:
                ci_to_ag[norm_key] = agent_status[current_id]

    # Filter to target date — compare parsed dates, not raw strings, since
    # Google Sheets can display the Date column in different formats
    # depending on cell formatting (was previously an exact string match
    # against "YYYY/MM/DD" which silently returned zero rows if the sheet
    # displayed dates any other way)
    today_rows = [r for r in rows
                  if _parse_date_flexible(r.get("Date", "")) == target_date]
    if rows and not today_rows:
        all_dates = sorted(set(str(r.get("Date","")) for r in rows if r.get("Date","")))
        log.warning(f"get_schedule_data: 0 rows matched {target_date} out of "
                    f"{len(rows)} total — dates in sheet: {all_dates[-5:]} (last 5)")

    result = []
    for r in today_rows:
        pers  = str(r.get("Personnel Number", "") or "").strip()
        name  = str(r.get("Employee", "") or "").strip()
        from_ = str(r.get("From", "") or "").strip()
        to_   = str(r.get("To",   "") or "").strip()
        hrs   = r.get("Hrs.", "") or ""

        # LOB from roster via personnel number
        roster_info = personnel_map.get(pers, {})
        lob         = roster_info.get("lob", "")

        # Skip if not in personnel_map — means _load_roster excluded them
        # (Inactive, or in EXCLUDED_CURRENT_IDS like Teresa/Adam)
        if not roster_info:
            continue

        # Agent status — try current_id map first, then user_id map
        norm = _norm_name(name)
        ag   = ci_to_ag.get(norm, {})

        actual_start   = ag.get("actual_start_time", "")
        last_offline   = ag.get("last_offline", "")
        recent_status  = ag.get("recent_status", "")
        logged_back_in = ag.get("logged_back_in", False)

        # Derive shift status
        shift_status, timing_status = _derive_shift_status(from_, actual_start, now)

        # ── Activity override ────────────────────────────────────────
        # actual_start only captures queue-start statuses (Ready/On-call…),
        # so an agent who logged in but never hit one — e.g. logged out and
        # back in, or sat in Break/Meeting/OOQ statuses — would be wrongly
        # ruled Absent. Any non-Offline activity today means they showed up.
        if shift_status == "Absent" and (ag.get("has_activity") or logged_back_in):
            shift_status  = "Present"
            timing_status = "Logged In (no queue start)"

        # Schedule note (VTO, Vacation partial, Appointment partial)
        sched_note = schedule_notes.get(norm, "")
        has_vto    = sched_note == "VTO"

        # Early leave detection
        # 1) PW-coded early leave (from schedule blocks) — always wins
        is_pw_early_leave = (sched_note == "Early Leave")
        # 2) Derived from agent status — last offline before shift end
        #    Only flag if:
        #    - agent is currently Offline (not logged back in)
        #    - there is no activity after the last_offline event (logged_back_in=False)
        #    - last_offline time was before shift end
        is_currently_offline = (recent_status == "Offline" or recent_status == "")
        if is_pw_early_leave:
            # PW-coded early leave — verify against actual CP behaviour before
            # flagging. Mid-day offline segments followed by a return are
            # reboots, not departures: if the agent logged back in, is still
            # active, or their last logout wasn't before shift end, the coded
            # early leave didn't actually happen (or hasn't happened yet).
            # With no CP data at all we can't verify, so the PW code stands.
            behaviour_contradicts = bool(ag) and (
                logged_back_in or
                not is_currently_offline or
                (last_offline and not _derive_early_leave(to_, last_offline, now)))
            if not behaviour_contradicts and "left early" not in timing_status.lower():
                timing_status = timing_status + ", Left Early" if timing_status else "Left Early"
        elif (shift_status == "Present" and last_offline and
                not has_vto and
                is_currently_offline and
                not logged_back_in and
                "left early" not in timing_status.lower()):
            if _derive_early_leave(to_, last_offline, now):
                timing_status = timing_status + ", Left Early" if timing_status else "Left Early"

        # KA complete — check today's completions, fall back to all-time
        ka = "Yes" if norm in ka_today else ("Completed" if norm in ka_all else "No")

        # Skip LOBs that don't log into Call Potential (e.g. Workforce Management)
        if lob in ATTENDANCE_EXCLUDED_LOBS:
            continue

        result.append({
            "date":                target_date.strftime("%Y-%m-%d"),
            "employee":            name,
            "personnel_number":    pers,
            "lob":                 lob,
            "from":                from_,
            "to":                  to_,
            "scheduled_hours":     hrs,
            "shift_status":        shift_status,
            "shift_timing_status": timing_status,
            "actual_start_time":   actual_start,
            "last_offline":        last_offline,
            "recent_status":       recent_status,
            "schedule_note":       sched_note,
            "ka_complete":         ka,
        })

    result.sort(key=lambda x: (x["lob"], x["employee"]))
    return result


def get_absenteeism_summary(schedule_rows=None, target_date=None):
    """
    Build absenteeism summary by LOB.
    Excludes VTO employees from early leave count.
    Only counts employees whose shift has started.
    """
    if target_date is None:
        target_date = _today_est()
    if schedule_rows is None:
        schedule_rows = get_schedule_data(target_date)

    now = _now_est()

    def shift_started(row):
        from_t = _parse_time_str(row.get("from", ""))
        if not from_t:
            return False
        shift_start = from_t.replace(year=now.year, month=now.month, day=now.day)
        return now >= shift_start

    def is_no_show_vto(row):
        ts = row.get("shift_timing_status", "").lower()
        return "no show" in ts and "vto" in ts

    def is_late(row):
        ts = row.get("shift_timing_status", "").lower()
        return ("started late" in ts or "late arrival" in ts or
                "delayed start" in ts or
                ("late" in ts and "left early" not in ts
                 and "no show" not in ts))

    def is_early_leave(row):
        if row.get("schedule_note") in ("VTO",) or row.get("schedule_note","").lower() in ("vacation","appointment"):
            return False  # VTO/partial is not early leave
        return "left early" in row.get("shift_timing_status", "").lower()

    lob_data = {}
    for row in schedule_rows:
        lob = row.get("lob", "") or "Unknown"
        ts  = row.get("shift_timing_status", "")

        # Skip if shift hasn't started yet (based on clock time)
        if not shift_started(row):
            continue
        if ts == "Unscheduled Login":
            continue
        if is_no_show_vto(row):
            continue

        if lob not in lob_data:
            lob_data[lob] = {
                "lob": lob,
                "total_scheduled":   0,
                "total_absent":      0,
                "total_late":        0,
                "total_early_leave": 0,
                "total_vto":         0,
            }

        lob_data[lob]["total_scheduled"] += 1

        pers = str(row.get("personnel_number", "") or "").strip()
        if (row.get("shift_status") == "Absent" and
                pers not in EXCLUDED_PERSONNEL):
            lob_data[lob]["total_absent"] += 1

        if is_late(row):
            lob_data[lob]["total_late"] += 1

        if is_early_leave(row):
            lob_data[lob]["total_early_leave"] += 1

        if row.get("schedule_note") == "VTO":
            lob_data[lob]["total_vto"] += 1

    summary = []
    for lob, d in sorted(lob_data.items()):
        sched  = d["total_scheduled"]
        absent = d["total_absent"]
        late   = d["total_late"]
        early  = d["total_early_leave"]
        vto    = d["total_vto"]
        summary.append({
            "lob":               lob,
            "total_scheduled":   sched,
            "total_absent":      absent,
            "absence_pct":       round(absent / sched * 100, 1) if sched else 0,
            "total_late":        late,
            "late_pct":          round(late   / sched * 100, 1) if sched else 0,
            "total_early_leave": early,
            "early_leave_pct":   round(early  / sched * 100, 1) if sched else 0,
            "total_vto":         vto,
        })
    return summary


# ══════════════════════════════════════════════════════════════════════════
#  PW SCHEDULES
# ══════════════════════════════════════════════════════════════════════════

def get_pw_schedules(target_date=None, gc=None):
    if target_date is None:
        target_date = _today_est()
    try:
        if gc is None:
            gc = _get_client()
    except Exception as e:
        log.error(f"get_pw_schedules auth error: {e}")
        return []
    # Fill down Employee Name — PW exports only put name on first row per agent
    raw_rows = _read_sheet(gc, SCHEDULE_BLOCKS_ID)
    rows = _fill_down_blocks(raw_rows)
    _, name_map, _ = _load_roster(gc)
    result = []
    for r in rows:
        row_date = _parse_date_flexible(r.get("Date", ""))
        if row_date != target_date:
            continue
        employee = str(r.get("Employee Name", "") or "").strip()
        roster_info = name_map.get(_norm_name(employee), {})
        result.append({
            "date":       row_date.isoformat(),
            "employee":   employee,
            "lob":        roster_info.get("lob", "Unknown"),
            "activity":   str(r.get("Activity", "") or "").strip(),
            "start_time": str(r.get("Start Time", "") or "").strip(),
            "end_time":   str(r.get("End Time", "") or "").strip(),
            "block_type": str(r.get("Type", "") or "").strip(),
        })
    result.sort(key=lambda x: (x["employee"], x["start_time"]))
    return result


# ══════════════════════════════════════════════════════════════════════════
#  FORECAST
# ══════════════════════════════════════════════════════════════════════════

def get_forecast_data(target_date=None, gc=None):
    if target_date is None:
        target_date = _today_est()
    target_str = target_date.strftime("%Y-%m-%d")
    now        = _now_est()
    now_str    = now.strftime("%Y-%m-%d %H:%M:%S")

    # Adjusted time matching Power BI CallSummaryByLOB DAX:
    # On :00/:15/:30/:45 → use that exact time
    # Otherwise → subtract 30 minutes
    mins        = now.minute
    rounded_min = (mins // 15) * 15
    is_quarter  = (mins % 15) == 0
    if is_quarter:
        adj_time = now.replace(minute=rounded_min, second=0, microsecond=0)
    else:
        adj_time = now - timedelta(minutes=30)
    adj_str = adj_time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        if gc is None:
            gc = _get_client()
    except Exception as e:
        log.error(f"get_forecast_data auth error: {e}")
        return [], []
    rows = _read_sheet(gc, FORECAST_SHEET_ID, tab="Forecasts V1.0", date_filter_col="interval_start", date_filter_val=target_str)
    today_rows = []
    for r in rows:
        interval = str(r.get("interval_start", "") or "").strip()
        ftype    = str(r.get("forecast_type", "") or "").lower().strip()
        metric   = str(r.get("metric", "") or "").lower().strip()
        if (interval.startswith(target_str) and
                "operational" in ftype and "offered" in metric):
            today_rows.append(r)
    # Combined workloads only for the top LOB summary table
    # These represent the full combined volume per LOB — do not include EN/FR subs
    COMBINED_WORKLOADS = {
        "SS Sales Combined",
        "SS CCA Combined",
        "SS CCA CC Combined",
        "SS CCA Store Combined",
        "PS Sales Combined",
        "PS Care Combined",
        "PS Sales & Care Combined",
        "Web Leads SS Combined",
        "Web Leads PS Combined",
        "Web Leads PS & SS Combined",
        "Web Leads SS Inbound Combined",
        "MoveBuddy",
        "I.T Support",
        "PS Case Manager",
    }

    lob_daily      = {}
    lob_utonow     = {}
    workload_detail = {}
    for r in today_rows:
        wname    = str(r.get("workload_name", "") or "").strip()
        interval = str(r.get("interval_start", "") or "").strip()
        try:
            val = float(r.get("value", 0) or 0)
        except (ValueError, TypeError):
            val = 0
        parent_lob = WORKLOAD_MAPPING.get(wname, wname)

        # Only Combined workloads go into the top summary
        if wname in COMBINED_WORKLOADS:
            lob_daily[parent_lob]  = lob_daily.get(parent_lob, 0) + val
            if interval <= adj_str:
                lob_utonow[parent_lob] = lob_utonow.get(parent_lob, 0) + val

        # All workloads go into detail table
        if wname not in workload_detail:
            workload_detail[wname] = {"workload": wname, "lob": parent_lob,
                                      "daily_cv": 0, "utonow_cv": 0}
        workload_detail[wname]["daily_cv"] += val
        if interval <= adj_str:
            workload_detail[wname]["utonow_cv"] += val

    lob_summary = []
    for lob in sorted(lob_daily.keys()):
        daily  = round(lob_daily.get(lob, 0))
        utonow = round(lob_utonow.get(lob, 0))
        prog   = round(utonow / daily * 100, 1) if daily > 0 else 0
        lob_summary.append({"lob": lob, "daily_cv": daily,
                             "utonow_cv": utonow, "progress_pct": prog})
    detail_rows = sorted(workload_detail.values(),
                         key=lambda x: (x["lob"], x["workload"]))
    return lob_summary, detail_rows




# ══════════════════════════════════════════════════════════════════════════
#  CALL STATUS — actual call volume from Call Potential
# ══════════════════════════════════════════════════════════════════════════

def get_call_status_summary(target_date=None, gc=None):
    """
    Read call_status sheet and aggregate by Combined LOB.
    Times in sheet are CST — convert to EST (+1hr) for comparison.
    Returns list of dicts: {lob, calls_offered, answered_within,
                            abandoned, rolled_over, service_level}
    """
    if target_date is None:
        target_date = _today_est()

    target_str = target_date.strftime("%Y-%m-%d")
    now_est    = _now_est()

    # Adjusted time window matching Power BI CallSummaryByLOB DAX
    # Rounds to nearest 15-min mark OR subtracts 30 min
    from datetime import datetime as _dt
    mins = now_est.minute
    rounded_min = (mins // 15) * 15
    is_quarter  = (mins % 15) == 0
    if is_quarter:
        adj_time = now_est.replace(minute=rounded_min, second=0, microsecond=0)
    else:
        adj_time = now_est - timedelta(minutes=30)

    try:
        if gc is None:
            gc = _get_client()
    except Exception as e:
        log.error(f"get_call_status_summary auth error: {e}")
        return []

    rows = _read_sheet(gc, CALL_STATUS_ID)

    lob_data = {}

    for r in rows:
        # date_created in call_status is CST — convert to EST (+1hr)
        dc = str(r.get("date_created", "") or "").strip()
        dt_cst = _parse_datetime_str(dc)
        if not dt_cst:
            continue
        dt_est = _cp_to_est(dt_cst)

        # Only today up to adjusted time
        if dt_est.date() != target_date:
            continue
        if dt_est > adj_time:
            continue

        # Map queue_id to LOB + Language
        try:
            qid = int(r.get("queue_id", 0) or 0)
        except (ValueError, TypeError):
            qid = 0
        queue_info = QUEUE_MAP.get(qid)
        if not queue_info:
            continue
        lob, lang = queue_info

        # Key = LOB + language for EN/FR separation
        key = f"{lob} {lang}".strip() if lang else lob

        if key not in lob_data:
            lob_data[key] = {
                "lob":             lob,
                "lang":            lang,
                "key":             key,
                "calls_offered":   0,
                "in_biz_offered":  0,
                "answered_within": 0,
                "abandoned":       0,
                "rolled_over":     0,
                "seen_logs":       set(),
            }

        # CP flag mapping (matches their email report definition):
        # is_rolled_over=1 → caller abandoned (hung up in queue)
        # is_abandoned=1   → call rolled/overflowed to another queue
        raw_aband  = r.get("is_abandoned", "")
        raw_rolled = r.get("is_rolled_over", "")
        # is_abandoned=1  → caller hung up (ABND / lost call)
        # is_rolled_over=1 → call rolled to store/other location (not lost)
        is_aband  = 1 if str(raw_aband).strip()  in ("1", "1.0") else 0
        is_rolled = 1 if str(raw_rolled).strip() in ("1", "1.0") else 0
        try:
            qt = float(r.get("queue_time", 0) or 0)
        except:
            qt = 0

        # Count unique log_ids per LOB
        log_id = str(r.get("log_id", "") or "").strip()
        if not log_id:
            continue
        if log_id in lob_data[key]["seen_logs"]:
            continue
        lob_data[key]["seen_logs"].add(log_id)

        lob_data[key]["calls_offered"]   += 1
        lob_data[key]["abandoned"]       += is_aband
        lob_data[key]["rolled_over"]     += is_rolled

        # SVL/OTF only counts calls within business hours
        in_biz = _in_business_hours(dt_est, lob)
        lob_data[key]["in_biz_offered"]  += 1 if in_biz else 0

        # Answered within 20 seconds (inclusive) — 80/20 SVL threshold
        if not is_aband and not is_rolled and qt <= 20 and in_biz:
            lob_data[key]["answered_within"] += 1

    result = []
    for key in sorted(lob_data.keys()):
        d       = lob_data[key]
        offered = d["calls_offered"]
        ans        = d["answered_within"]
        biz        = d["in_biz_offered"]
        sl        = round(ans / biz * 100, 1) if biz > 0 else 0
        aband_pct = round(d["abandoned"] / offered * 100, 1) if offered > 0 else 0
        result.append({
            "lob":             d["lob"],
            "lang":            d["lang"],
            "display_lob":     key,
            "calls_offered":   offered,
            "answered_within": ans,
            "abandoned":       d["abandoned"],
            "abandoned_pct":   aband_pct,
            "rolled_over":     d["rolled_over"],
            "service_level":   sl,
        })
    return result



# ══════════════════════════════════════════════════════════════════════════
#  INTERVAL REPORT
# ══════════════════════════════════════════════════════════════════════════

def get_interval_report(target_date=None, gc=None, lob_filter=None, override_call_rows=None):
    """
    Build interval report matching Power BI Interval Summary DAX.
    Groups by LOB + 30-min interval.
    Joins forecast (offered calls + AHT) per interval.

    override_call_rows: if provided, use these rows instead of fetching from Google Sheets.
                        Used for backup/manual CSV upload mode.
    
    Key logic from DAX:
    - Calls Offered   = SUM(Calls Offered column) per unique log_id
    - Calls Answered  = COUNT where is_abandoned = 0
    - Answered Within = COUNT where queue_time <= 20
    - ASA             = AVG(queue_time)
    - Actual AHT      = AVG(duration)
    - Max Queued      = MAX(queue_time)
    - OTF             = Calls Offered / Forecasted Offered for that interval
    - Service Level   = Answered Within / Calls Offered
    """
    if target_date is None:
        target_date = _today_est()

    target_str = target_date.strftime("%Y-%m-%d")

    try:
        if gc is None:
            gc = _get_client()
    except Exception as e:
        log.error(f"get_interval_report auth error: {e}")
        return []

    # Load call status — use override if provided
    cs_rows = override_call_rows if override_call_rows is not None else _read_sheet(gc, CALL_STATUS_ID)
    # ── Build forecast lookup from WFM Capacity Planning sheet ──
    # This sheet has a full year of per-30-min interval data for all LOBs,
    # covering both today and all historical dates reliably.
    fc_lookup = _read_cap_forecast(gc, target_str)

    # Also try Forecasts V1.0 to supplement AHT where cap forecast has none
    if not any(v.get("aht") for v in fc_lookup.values()):
        fc_rows = _read_sheet(gc, FORECAST_SHEET_ID, tab="Forecasts V1.0",
                              date_filter_col="interval_start",
                              date_filter_val=target_str)
        _build_fc_lookup_from_v1(fc_rows, target_str, fc_lookup)
    # ── Process call status ──
    # Group by (LOB, interval_bucket, log_id) to get unique calls
    # Then aggregate per (LOB, interval_bucket)
    interval_data = {}  # (lob, bucket_str) → stats
    lob_seen_logs = {}  # lob → set of log_ids seen (LOB-wide dedup)

    for r in cs_rows:
        # date_created in call_status (CST) = when call LEFT the queue (answered/abandoned)
        # True offer time = date_created - queue_time (seconds in queue)
        # We must bucket by offer time to match the email interval report
        dc = str(r.get("date_created", "") or "").strip()
        dt_cst = _parse_datetime_str(dc)
        if not dt_cst:
            continue
        dt_est = _cp_to_est(dt_cst)
        if dt_est.date() != target_date:
            continue

        # Derive offer time by subtracting queue_time from date_created.
        # A blank/missing queue_time is NOT the same as "waited 0 seconds" —
        # treating it as 0 silently makes that call look instantly answered,
        # which inflates Service Level. Track whether it's genuinely known.
        raw_qt = r.get("queue_time", "")
        qt_known = raw_qt is not None and str(raw_qt).strip() != ""
        if qt_known:
            try:
                qt = float(raw_qt)
            except (ValueError, TypeError):
                qt = 0
                qt_known = False
        else:
            qt = 0
        offer_est = dt_est - timedelta(seconds=qt)
        # Safety: if offer_est drifts to previous day, clamp to today
        if offer_est.date() != target_date:
            offer_est = dt_est

        # Map queue to LOB
        try:
            qid = int(r.get("queue_id", 0) or 0)
        except:
            qid = 0
        queue_info = QUEUE_MAP.get(qid)
        if not queue_info:
            continue
        lob  = queue_info[0]  # base LOB (combined)
        lang = (queue_info[1] or "").strip() if len(queue_info) > 1 else ""

        # Build list of groups to count this call into:
        # always the base combined LOB, plus a language-specific group if known
        groups = [lob]
        if lang:
            groups.append(f"{lob} {lang}")

        if lob_filter and lob not in lob_filter:
            continue

        # Bucket by OFFER time (when call entered queue) — matches email interval report
        bucket_min = (offer_est.minute // 30) * 30
        bucket     = offer_est.replace(minute=bucket_min, second=0, microsecond=0)
        bucket_str = bucket.strftime("%H:%M:%S")

        log_id = str(r.get("log_id", "") or "").strip()
        if not log_id:
            continue
        # CP flag mapping
        raw_aband  = r.get("is_abandoned", "")
        raw_rolled = r.get("is_rolled_over", "")
        is_aband  = 1 if str(raw_aband).strip()  in ("1", "1.0") else 0
        is_rolled = 1 if str(raw_rolled).strip() in ("1", "1.0") else 0
        try:
            dur = float(r.get("duration", 0) or 0)
        except:
            dur = 0
        in_biz = _in_business_hours(offer_est, lob)

        # Count into each group (combined LOB + language-specific).
        # Dedup is per-group so the same log_id is counted once per group.
        for grp in groups:
            key = (grp, bucket_str)
            if key not in interval_data:
                interval_data[key] = {
                    "lob":            grp,
                    "interval":       bucket_str,
                    "in_biz":         in_biz,   # set correctly on init, not defaulted to False
                    "calls_offered":  0,
                    "calls_answered": 0,
                    "calls_abandoned":0,
                    "calls_rolled":   0,
                    "answered_within":0,
                    "calls_with_qt":  0,
                    "queue_times":    [],
                    "durations":      [],
                }
            if grp not in lob_seen_logs:
                lob_seen_logs[grp] = {}
            if log_id in lob_seen_logs[grp]:
                continue
            lob_seen_logs[grp][log_id] = {
                "key": key, "is_aband": is_aband, "is_rolled": is_rolled
            }

            interval_data[key]["in_biz"] = in_biz
            interval_data[key]["calls_offered"] += 1
            if not is_aband and not is_rolled:
                interval_data[key]["calls_answered"] += 1
            if is_aband:
                interval_data[key]["calls_abandoned"] += 1
            if is_rolled:
                interval_data[key]["calls_rolled"] += 1
            if in_biz and not is_aband and not is_rolled:
                if qt_known:
                    interval_data[key]["calls_with_qt"] += 1
                    if qt <= 20:
                        interval_data[key]["answered_within"] += 1
            if qt_known and not is_aband:
                interval_data[key]["queue_times"].append(qt)
            if dur > 0:
                interval_data[key]["durations"].append(dur)

    # ── Seed interval_data from forecast lookup ──
    # Only include intervals up to the current 30-min bucket (not future intervals)
    now_est = _now_est()
    now_bucket_min = (now_est.minute // 30) * 30
    now_bucket_str = now_est.replace(minute=now_bucket_min, second=0, microsecond=0).strftime("%H:%M:%S")

    for (lob, bucket_str), fc in fc_lookup.items():
        if fc.get("offered", 0) <= 0:
            continue
        # Skip future intervals — bucket_str is HH:MM:SS, compare as string (lexicographic = chronological)
        if bucket_str > now_bucket_str:
            continue
        key = (lob, bucket_str)
        if key not in interval_data:
            # Reconstruct full datetime for in_biz check
            dt_str = target_date.strftime("%Y-%m-%d") + " " + bucket_str
            dt = _parse_datetime_str(dt_str)
            in_biz = _in_business_hours(dt, lob) if dt else False
            interval_data[key] = {
                "lob":            lob,
                "interval":       bucket_str,
                "in_biz":         in_biz,
                "calls_offered":  0,
                "calls_answered": 0,
                "calls_abandoned":0,
                "calls_rolled":   0,
                "answered_within":0,
                "calls_with_qt":  0,
                "queue_times":    [],
                "durations":      [],
            }

    # ── Build result rows ──
    result = []
    for (lob, bucket_str), d in sorted(interval_data.items()):
        offered      = d["calls_offered"]
        answered     = d["calls_answered"]
        abandoned    = d["calls_abandoned"]
        rolled       = d["calls_rolled"]
        aw           = d["answered_within"]
        calls_with_qt = d["calls_with_qt"]
        qt_list      = d["queue_times"]
        dur_list     = d["durations"]

        calls_lost = abandoned + rolled
        asa        = round(sum(qt_list) / len(qt_list)) if qt_list else None
        actual_aht = round(sum(dur_list) / len(dur_list)) if dur_list else None
        max_q      = int(max(qt_list)) if qt_list else None
        # Service Level denominator is calls with KNOWN wait time, not total
        # offered — a call with a blank queue_time is neither "answered
        # within 20s" nor "not answered within 20s", it's simply unknown,
        # so it shouldn't silently pull SL toward 0% or 100%.
        sl         = round(aw / calls_with_qt * 100, 1) if calls_with_qt > 0 else None
        aband_pct  = round(abandoned / offered * 100, 1) if offered > 0 else None
        pct_lost   = round(calls_lost / offered * 100, 1) if offered > 0 else None

        # Forecast for this interval
        fc_key       = (lob, bucket_str)
        fc           = fc_lookup.get(fc_key, {})
        fc_offered   = round(fc.get("offered", 0))
        fc_aht_raw   = fc.get("aht", [])
        if isinstance(fc_aht_raw, list):
            fc_aht = round(sum(fc_aht_raw) / len(fc_aht_raw)) if fc_aht_raw else None
        else:
            fc_aht = int(fc_aht_raw) if fc_aht_raw else None
        otf          = round(offered / fc_offered * 100, 1) if fc_offered > 0 else None

        result.append({
            "lob":            lob,
            "interval":       bucket_str,
            "in_biz":         d["in_biz"],
            "forecasted_cv":  fc_offered,
            "forecasted_aht": fc_aht,
            "calls_offered":  offered,
            "calls_answered": answered,
            "answered_within":aw,
            "calls_with_qt":  calls_with_qt,
            "calls_abandoned":abandoned,
            "calls_rolled":   rolled,
            "calls_lost":     calls_lost,
            "abandoned_pct":  aband_pct,
            "pct_lost":       pct_lost,
            "otf":            otf,
            "service_level":  sl,
            "asa":            asa,
            "actual_aht":     actual_aht,
            "max_queued":     max_q,
        })

    return result



# ══════════════════════════════════════════════════════════════════════════
#  CASCADES — agents who entered Unavailable 20-24 sec (DAX match)
# ══════════════════════════════════════════════════════════════════════════

EXCLUDED_CASCADE_STATUSES = {"On-call", "Ready"}
CASCADE_STATUS      = "Unavailable"
CASCADE_MIN_SEC     = 20
CASCADE_MAX_SEC     = 25
CASCADE_NEXT_EXCLUDE    = {"Ready", "On-call", "Unavailable", "Cool-Down"}
CASCADE_EXCLUDE_TRIGGER = {"Cool-Down"}

def get_cascades(target_date=None, gc=None, override_raw_rows=None):
    """
    Find cascade events from Agent Status sheet.
    Matches Power BI DAX:
      - Status = "Unavailable"
      - Duration >= 20 AND < 25 seconds
      - Previous status NOT in {On-call, Ready}
      - Next status NOT in {On-call, Ready}
    Returns list of cascade events per agent.

    override_raw_rows: rows from the FULL granular backup CSV (same format
                        as the live sheet) — used when the live CP feed is
                        down and a manual export has been uploaded instead.
    """
    if target_date is None:
        target_date = _today_est()

    today_str = target_date.strftime("%Y-%m-%d")

    try:
        if gc is None:
            gc = _get_client()
    except Exception as e:
        log.error(f"get_cascades auth error: {e}")
        return []

    # Load agent status + roster
    rows      = _get_agent_status_rows(gc, override_raw_rows)
    roster_rows = _read_sheet(gc, ROSTER_SHEET_ID, tab="EMPLOYEES")

    # Build user_id → name + LOB from roster
    uid_to_info = {}
    for r in roster_rows:
        current_id = str(r.get("Current ID", "") or "").strip()
        fname      = str(r.get("First Name", "") or "").strip()
        lname      = str(r.get("Last Name",  "") or "").strip()
        name       = f"{fname} {lname}".strip()
        pu         = str(r.get("Planning Unit", "") or "").strip()
        lob        = PU_TO_LOB.get(pu, pu)
        status     = str(r.get("Status", "") or "").strip()
        if status == "Inactive" or not current_id:
            continue
        if current_id in EXCLUDED_CURRENT_IDS:
            continue
        uid_to_info[current_id] = {"name": name, "lob": lob}

    # Group events by user_id, today only, sorted by time
    by_uid = {}
    for r in rows:
        uid = str(r.get("user_id", "") or "").strip()
        st  = str(r.get("start_time", "") or "").strip()
        sid = str(r.get("c_activity_sid", "") or "").strip()
        if not uid or not st.startswith(today_str):
            continue
        dt_cst = _parse_datetime_str(st)
        if not dt_cst:
            continue
        dt_est = _cp_to_est(dt_cst)
        status = CP_STATUS_MAP.get(sid, "Unknown")
        if uid not in by_uid:
            by_uid[uid] = []
        by_uid[uid].append({"dt": dt_est, "status": status})

    # Detect cascades: Unavailable 20-24 sec, prev/next not Ready/On-call
    cascades = []
    # Also count calls offered per agent from agent status:
    # On-call = answered call, No-Answer = offered but not answered
    # For LOBs with primary queue restrictions, count calls_offered from call
    # status sheet (queue-filtered) rather than agent status events.
    # Note: cascades themselves are detected from ALL queues — the primary queue
    # filter only applies to calls_offered denominator for cascade %
    cs_rows_today = []
    if LOB_PRIMARY_QUEUES:
        cs_rows = _read_sheet(gc, CALL_STATUS_ID)
        today_str = target_date.strftime("%Y-%m-%d")
        for r in cs_rows:
            dc = str(r.get("date_created", "") or "").strip()
            if not dc.startswith(today_str):
                continue
            try:
                qid = int(r.get("queue_id", 0) or 0)
            except:
                continue
            cs_rows_today.append({"qid": qid, "log_id": str(r.get("log_id","") or "")})

    # Build per-LOB primary queue call count from call status sheet
    # keyed by LOB → set of log_ids seen
    lob_primary_offered = {}
    for lob, primary_qids in LOB_PRIMARY_QUEUES.items():
        seen = set()
        for r in cs_rows_today:
            if r["qid"] in primary_qids and r["log_id"] and r["log_id"] not in seen:
                seen.add(r["log_id"])
        lob_primary_offered[lob] = len(seen)

    agent_calls_offered = {}  # uid → count

    for uid, events in by_uid.items():
        # Skip excluded contractors (uid may equal Current ID directly)
        if uid in EXCLUDED_CURRENT_IDS:
            continue

        events.sort(key=lambda x: x["dt"])
        info = uid_to_info.get(uid, {"name": f"UID {uid}", "lob": "Unknown"})
        lob  = info.get("lob", "")

        if lob in LOB_PRIMARY_QUEUES:
            # Use LOB-level primary queue count — same for all agents in this LOB
            calls_offered = lob_primary_offered.get(lob, 0)
        else:
            # Default: count On-call + No-Answer from agent status events
            calls_offered = sum(1 for e in events if e["status"] in ("On-call", "No-Answer"))
        agent_calls_offered[uid] = calls_offered

        for i, ev in enumerate(events):
            if ev["status"] != CASCADE_STATUS:
                continue
            # Cool-Down is normal post-call wrap-up — never a cascade
            if ev["status"] in CASCADE_EXCLUDE_TRIGGER:
                continue

            # Calculate duration = time until next event
            if i + 1 < len(events):
                duration_sec = (events[i+1]["dt"] - ev["dt"]).total_seconds()
            else:
                continue  # no next event, can't calculate duration

            if not (CASCADE_MIN_SEC <= duration_sec < CASCADE_MAX_SEC):
                continue

            prev_status = events[i-1]["status"] if i > 0 else ""
            next_status = events[i+1]["status"] if i + 1 < len(events) else ""

            # Cascade = Unavailable 20-24s where next is NOT Ready/On-call/Unavailable/Cool-Down
            if next_status in CASCADE_NEXT_EXCLUDE:
                continue

            in_biz = _in_business_hours(ev["dt"], info.get("lob"))

            cascades.append({
                "name":          info["name"],
                "lob":           info["lob"],
                "uid":           uid,
                "time":          ev["dt"].strftime("%H:%M:%S"),
                "duration_sec":  int(duration_sec),
                "prev_status":   prev_status,
                "next_status":   next_status,
                "in_biz":        in_biz,
                "calls_offered": agent_calls_offered.get(uid, 0),
            })

    cascades.sort(key=lambda x: x["time"])
    return cascades

# ══════════════════════════════════════════════════════════════════════════
#  AGENT STATUS — current status of all agents
# ══════════════════════════════════════════════════════════════════════════

def get_agent_status_detail(gc=None, override_raw_rows=None):
    """
    Return current agent status for all roster employees.
    Joined by Current ID → CP user_id.
    Returns list of agents sorted by LOB, then employee name.

    override_raw_rows: rows from the FULL granular backup CSV — used when
                        the live CP feed is down.
    """
    try:
        if gc is None:
            gc = _get_client()
    except Exception as e:
        log.error(f"get_agent_status_detail auth error: {e}")
        return []

    now_est = _now_est()
    today   = _today_est()

    # Load all data sources
    rows           = _get_agent_status_rows(gc, override_raw_rows)
    roster_rows    = _read_sheet(gc, ROSTER_SHEET_ID, tab="EMPLOYEES")

    # Find today's date in agent status
    all_dates = set()
    for r in rows:
        st = str(r.get("start_time", "") or "").strip()
        if st and len(st) >= 10:
            all_dates.add(st[:10])
    today_str = today.strftime("%Y-%m-%d")
    if today_str not in all_dates:
        log.warning(f"Agent status: no data for today {today_str}")
        return []

    # Group by user_id → all events today, sorted by time
    by_uid = {}
    for r in rows:
        uid = str(r.get("user_id", "") or "").strip()
        st  = str(r.get("start_time", "") or "").strip()
        sid = str(r.get("c_activity_sid", "") or "").strip()
        if not uid or not st.startswith(today_str):
            continue
        dt_cst = _parse_datetime_str(st)
        if not dt_cst:
            continue
        dt_est  = _cp_to_est(dt_cst)
        status  = CP_STATUS_MAP.get(sid, sid[:20] if sid else "Unknown")
        if uid not in by_uid:
            by_uid[uid] = []
        by_uid[uid].append({"dt": dt_est, "status": status})

    # Sort events and derive per-agent summary
    agent_summary = {}  # uid → summary
    for uid, events in by_uid.items():
        events.sort(key=lambda x: x["dt"])
        current_status = events[-1]["status"]
        current_since  = events[-1]["dt"]
        minutes_in_status = round((now_est - current_since).total_seconds() / 60, 1)

        # First "work" event = actual start
        actual_start = None
        for e in events:
            if e["status"] in ("Ready", "On-call", "Cool-Down",
                               "ooq Client Account Work", "Leader on Duty",
                               "eChat", "Web Leads", "Long Distance"):
                actual_start = e["dt"].strftime("%H:%M")
                break

        agent_summary[uid] = {
            "current_status":    current_status,
            "current_since":     current_since.strftime("%H:%M:%S"),
            "minutes_in_status": minutes_in_status,
            "actual_start":      actual_start or "",
            "event_count":       len(events),
        }

    # Build result by joining roster → agent summary
    result = []
    for r in roster_rows:
        status = str(r.get("Status", "") or "").strip()
        if status == "Inactive":
            continue
        fname      = str(r.get("First Name",  "") or "").strip()
        lname      = str(r.get("Last Name",   "") or "").strip()
        name       = f"{fname} {lname}".strip()
        pu         = str(r.get("Planning Unit", "") or "").strip()
        lob        = PU_TO_LOB.get(pu, pu)
        current_id = str(r.get("Current ID",  "") or "").strip()
        emp_id     = str(r.get("Employee ID", "") or "").strip()

        ag = agent_summary.get(current_id, {})
        if not ag:
            continue  # Only show agents with activity today

        result.append({
            "name":              name,
            "lob":               lob,
            "current_status":    ag["current_status"],
            "current_since":     ag["current_since"],
            "minutes_in_status": ag["minutes_in_status"],
            "actual_start":      ag["actual_start"],
        })

    result.sort(key=lambda x: (x["lob"], x["name"]))
    return result


def get_agent_status_history(gc=None, override_raw_rows=None):
    """
    Return ALL status events for today for every agent — not just current status.
    One row per status event, sorted by time desc.

    override_raw_rows: rows from the FULL granular backup CSV — used when
                        the live CP feed is down.
    """
    try:
        if gc is None:
            gc = _get_client()
    except Exception as e:
        log.error(f"get_agent_status_history auth error: {e}")
        return []

    now_est = _now_est()
    today   = _today_est()
    today_str = today.strftime("%Y-%m-%d")

    rows        = _get_agent_status_rows(gc, override_raw_rows)
    roster_rows = _read_sheet(gc, ROSTER_SHEET_ID, tab="EMPLOYEES")

    # Check data exists for today
    all_dates = set(str(r.get("start_time","") or "")[:10] for r in rows if r.get("start_time"))
    if today_str not in all_dates:
        log.warning(f"get_agent_status_history: no data for {today_str}")
        return []

    # Build roster map current_id → name/lob
    uid_to_info = {}
    for r in roster_rows:
        status     = str(r.get("Status", "") or "").strip()
        if status == "Inactive":
            continue
        current_id = str(r.get("Current ID", "") or "").strip()
        if current_id in EXCLUDED_CURRENT_IDS:
            continue
        fname      = str(r.get("First Name",  "") or "").strip()
        lname      = str(r.get("Last Name",   "") or "").strip()
        pu         = str(r.get("Planning Unit","") or "").strip()
        lob        = PU_TO_LOB.get(pu, pu)
        if current_id:
            uid_to_info[current_id] = {"name": f"{fname} {lname}".strip(), "lob": lob}

    # Collect all events for today, per uid
    by_uid = {}
    for r in rows:
        uid = str(r.get("user_id", "") or "").strip()
        st  = str(r.get("start_time", "") or "").strip()
        sid = str(r.get("c_activity_sid", "") or "").strip()
        if not uid or not st.startswith(today_str):
            continue
        if uid in EXCLUDED_CURRENT_IDS:
            continue
        dt_cst = _parse_datetime_str(st)
        if not dt_cst:
            continue
        dt_est = _cp_to_est(dt_cst)
        status = CP_STATUS_MAP.get(sid, sid[:20] if sid else "Unknown")
        if uid not in by_uid:
            by_uid[uid] = []
        by_uid[uid].append({"dt": dt_est, "status": status})

    # Build flat event list with duration
    result = []
    for uid, events in by_uid.items():
        if uid in EXCLUDED_CURRENT_IDS:
            continue
        info = uid_to_info.get(uid, {"name": f"UID {uid}", "lob": "Unknown"})
        events.sort(key=lambda x: x["dt"])
        for i, ev in enumerate(events):
            # Duration = time until next event, or until now for last event
            if i + 1 < len(events):
                end_dt   = events[i+1]["dt"]
                duration = round((end_dt - ev["dt"]).total_seconds() / 60, 1)
                end_str  = end_dt.strftime("%H:%M:%S")
            else:
                end_dt   = now_est
                duration = round((end_dt - ev["dt"]).total_seconds() / 60, 1)
                end_str  = "now"

            result.append({
                "name":       info["name"],
                "lob":        info["lob"],
                "status":     ev["status"],
                "start_time": ev["dt"].strftime("%H:%M:%S"),
                "end_time":   end_str,
                "duration_min": duration,
            })

    # Sort by time descending (most recent first)
    result.sort(key=lambda x: (x["name"], x["start_time"]))
    return result
# Activity names in schedule blocks that represent breaks and lunches
_SCHED_BREAK_ACTIVITIES  = {"break", "morning break", "afternoon break", "pm break", "am break"}
_SCHED_LUNCH_ACTIVITIES  = {"lunch", "lunch break", "meal break"}

def _load_scheduled_breaks(gc, target_date=None):
    """
    Return dict: personnel_number → list of {type, start, end}
    for today's scheduled breaks and lunches.
    Joins via Personnel Number (reliable) with name fallback.
    """
    if target_date is None:
        target_date = _today_est()
    rows = _read_sheet(gc, SCHEDULE_BLOCKS_ID)
    result = {}
    for r in rows:
        row_date = _parse_date_flexible(r.get("Date", ""))
        if row_date != target_date:
            continue
        activity  = str(r.get("Activity", "") or "").strip()
        act_lower = activity.lower()
        pers      = str(r.get("Personnel Number", "") or "").strip()
        name      = str(r.get("Employee Name",   "") or "").strip()  # correct column
        if not name: name = str(r.get("Employee", "") or "").strip()  # fallback
        if not pers and name and "," in name:
            last, first = name.split(",", 1)
            name = f"{first.strip()} {last.strip()}"
        key = pers or _norm_name(name)
        if not key:
            continue
        from_str = str(r.get("From", "") or "").strip()
        to_str   = str(r.get("To",   "") or "").strip()
        start_dt = end_dt = None
        for fmt in ("%H:%M:%S", "%H:%M"):
            try:
                start_dt = datetime.combine(target_date, datetime.strptime(from_str, fmt).time())
                end_dt   = datetime.combine(target_date, datetime.strptime(to_str,   fmt).time())
                break
            except ValueError:
                pass
        if not start_dt or not end_dt:
            continue
        stype = "lunch" if act_lower in _SCHED_LUNCH_ACTIVITIES else "break"
        if key not in result:
            result[key] = []
        result[key].append({"type": stype, "start": start_dt, "end": end_dt})
    return result


def get_agent_day_detail(gc, user_id, target_date=None):
    """
    Return full-day schedule + adherence for one agent.
    Used by the RTM Live popup panel.
    Returns dict with: name, lob, shift, segments (list), current_status
    """
    if target_date is None:
        target_date = _today_est()
    today_str = target_date.strftime("%Y-%m-%d")

    # --- Agent CP history today ---
    all_rows = _get_agent_status_rows(gc)
    agent_rows = [r for r in all_rows
                  if str(r.get("user_id","") or "").strip() == str(user_id)
                  and str(r.get("start_time","") or "").startswith(today_str)]
    agent_rows.sort(key=lambda r: r.get("start_time",""))

    # Build status timeline
    timeline = []
    for r in agent_rows:
        st  = str(r.get("start_time","") or "").strip()
        sid = str(r.get("c_activity_sid","") or "").strip()
        dt_cst = _parse_datetime_str(st)
        if not dt_cst: continue
        dt_est = _cp_to_est(dt_cst)
        status = CP_STATUS_MAP.get(sid, sid[:20] if sid else "Unknown")
        timeline.append({"dt": dt_est, "status": status,
                         "time_str": dt_est.strftime("%H:%M")})

    current_status = timeline[-1]["status"] if timeline else "Unknown"
    current_since  = timeline[-1]["time_str"] if timeline else ""

    # --- Roster: name, LOB, Personnel # ---
    roster_rows = _read_sheet(gc, ROSTER_SHEET_ID, tab="EMPLOYEES")
    name, lob, pers_num = f"UID {user_id}", "", ""
    for r in roster_rows:
        if str(r.get("Current ID","") or "").strip() == str(user_id):
            fn       = str(r.get("First Name","") or "").strip()
            ln       = str(r.get("Last Name","")  or "").strip()
            pu       = str(r.get("Planning Unit","") or "").strip()
            pers_num = str(r.get("Personnel #","")   or "").strip()
            name     = f"{fn} {ln}".strip() or name
            lob      = PU_TO_LOB.get(pu, pu)
            break

    norm = _norm_name(name)

    def _match_row(r):
        """Match a schedule/block row to this agent via Personnel # or name.
        PeopleWare stores names as 'Last, First' so we try both formats."""
        rp = str(r.get("Personnel Number","") or "").strip()
        # Personnel Number match (most reliable)
        if pers_num and rp and rp == pers_num:
            return True
        # Name match — try raw then handle "Last, First" PW format
        emp_raw  = str(r.get("Employee","") or "").strip()
        emp_norm = _norm_name(emp_raw)
        if emp_norm == norm:
            return True
        if "," in emp_raw:
            last, first = emp_raw.split(",", 1)
            reversed_norm = _norm_name(f"{first.strip()} {last.strip()}")
            if reversed_norm == norm:
                return True
        return False

    # --- Scheduled shift ---
    sched_rows = _read_sheet(gc, SCHEDULE_SHEET_ID, tab="Schedule")
    shift_start, shift_end = None, None
    for r in sched_rows:
        if _parse_date_flexible(r.get("Date","")) != target_date: continue
        if not _match_row(r): continue
        for fmt in ("%H:%M:%S","%H:%M"):
            try:
                shift_start = datetime.combine(target_date,
                    datetime.strptime(str(r.get("From","") or ""), fmt).time())
                shift_end   = datetime.combine(target_date,
                    datetime.strptime(str(r.get("To","")   or ""), fmt).time())
                break
            except: pass
        if shift_start: break

    # --- Schedule blocks (breaks, lunches, activities) ---
    # Use same column names as get_pw_schedules which already works:
    # "Employee Name", "Start Time", "End Time", "Activity", "Type"
    block_rows = _read_sheet(gc, SCHEDULE_BLOCKS_ID)
    segments = []
    for r in block_rows:
        if _parse_date_flexible(r.get("Date","")) != target_date: continue
        emp_name = str(r.get("Employee Name","") or "").strip()
        rp       = str(r.get("Personnel Number","") or "").strip()
        # Match by Personnel # or normalised name (PW uses "Last, First" so handle both)
        pers_match = pers_num and rp and rp == pers_num
        emp_norm   = _norm_name(emp_name)
        name_match = emp_norm == norm
        if not name_match and "," in emp_name:
            last, first = emp_name.split(",", 1)
            name_match  = _norm_name(f"{first.strip()} {last.strip()}") == norm
        if not pers_match and not name_match:
            continue
        activity  = str(r.get("Activity","") or "").strip()
        blk_type  = str(r.get("Type","")     or "").strip()
        label     = activity if activity else blk_type
        fs = str(r.get("Start Time","") or "").strip()
        ts = str(r.get("End Time","")   or "").strip()
        seg_start = seg_end = None
        for fmt in ("%H:%M:%S","%H:%M"):
            try:
                seg_start = datetime.combine(target_date, datetime.strptime(fs, fmt).time())
                seg_end   = datetime.combine(target_date, datetime.strptime(ts, fmt).time())
                break
            except: pass
        if not seg_start: continue
        segments.append({"activity": label, "start": seg_start, "end": seg_end})
    segments.sort(key=lambda s: s["start"] or datetime.min)

    now_est = _now_est()

    # --- Adherence per segment ---
    BREAK_STATUSES  = {"On Break"}
    LUNCH_STATUSES  = {"Lunch"}
    SCHED_BREAK_ACT = _SCHED_BREAK_ACTIVITIES
    SCHED_LUNCH_ACT = _SCHED_LUNCH_ACTIVITIES

    def actual_status_during(seg_start, seg_end):
        """Find what CP status the agent had during this window."""
        hits = [e for e in timeline if seg_start <= e["dt"] <= seg_end]
        return hits[0]["status"] if hits else None

    def segment_adherence(seg):
        act_lower = seg["activity"].lower()
        s, e = seg["start"], seg["end"]
        if e < now_est:  # past
            actual = actual_status_during(s, e)
            if act_lower in SCHED_BREAK_ACT:
                return ("✓ Taken", "ok") if actual in BREAK_STATUSES else ("✗ Missed", "missed")
            if act_lower in SCHED_LUNCH_ACT:
                return ("✓ Taken", "ok") if actual in LUNCH_STATUSES else ("✗ Missed", "missed")
            return ("Complete", "ok")
        elif s <= now_est <= e:  # in progress
            actual = actual_status_during(s, now_est)
            return ("In progress", "active")
        else:  # future
            return ("Upcoming", "upcoming")

    result_segs = []
    for seg in segments:
        adh_label, adh_class = segment_adherence(seg)
        result_segs.append({
            "activity":  seg["activity"],
            "start":     seg["start"].strftime("%H:%M"),
            "end":       seg["end"].strftime("%H:%M"),
            "adherence": adh_label,
            "adh_class": adh_class,
        })

    return {
        "name":           name,
        "lob":            lob,
        "user_id":        user_id,
        "current_status": current_status,
        "current_since":  current_since,
        "shift_start":    shift_start.strftime("%H:%M") if shift_start else None,
        "shift_end":      shift_end.strftime("%H:%M")   if shift_end   else None,
        "segments":       result_segs,
        "timeline":       [{"time": t["time_str"], "status": t["status"]}
                           for t in timeline],
    }


def get_production_efficiency(target_date=None, gc=None):
    """
    Calculate production efficiency per agent and LOB.
    Efficiency = time in productive CP statuses / PW scheduled hours.
    Productive statuses: Ready, On-call, Cool-Down.
    """
    if target_date is None:
        target_date = _today_est()
    try:
        if gc is None:
            gc = _get_client()
    except Exception as e:
        log.error(f"get_production_efficiency auth error: {e}")
        return []

    PRODUCTIVE = {"Ready", "On-call", "Cool-Down",
                  "ooq Training", "ooq Meeting", "ooq Coaching",
                  "Leader on Duty"}
    today_str  = target_date.strftime("%Y-%m-%d")
    now_est    = _now_est()
    is_today   = (target_date == _today_est())

    # ── 1. CP agent status events → productive minutes per uid ──────────────
    rows = _get_agent_status_rows(gc)
    by_uid = {}
    for r in rows:
        uid = str(r.get("user_id", "") or "").strip()
        st  = str(r.get("start_time", "") or "").strip()
        sid = str(r.get("c_activity_sid", "") or "").strip()
        if not uid or not st.startswith(today_str):
            continue
        if uid in EXCLUDED_CURRENT_IDS:
            continue
        dt_cst = _parse_datetime_str(st)
        if not dt_cst:
            continue
        dt_est = _cp_to_est(dt_cst)
        status = CP_STATUS_MAP.get(sid, "Unknown")
        by_uid.setdefault(uid, []).append({"dt": dt_est, "status": status})

    cap_time = now_est if is_today else datetime.combine(target_date, datetime.strptime("23:59:59", "%H:%M:%S").time())
    OFFLINE_ST = {"Offline", "No-Mic"}

    CAW_ST = "ooq Client Account Work"   # OOQ CAW — off-queue client afterwork

    agent_productive = {}  # uid → productive minutes
    agent_loggedin   = {}  # uid → total logged-in minutes (any non-Offline status)
    agent_caw        = {}  # uid → minutes in OOQ Client Account Work (afterwork)
    for uid, events in by_uid.items():
        events.sort(key=lambda x: x["dt"])
        prod_min = 0.0
        login_min = 0.0
        caw_min = 0.0
        for i, ev in enumerate(events):
            end_dt = events[i + 1]["dt"] if i + 1 < len(events) else cap_time
            dur = (end_dt - ev["dt"]).total_seconds() / 60
            if ev["status"] in PRODUCTIVE:
                prod_min += dur
            if ev["status"] not in OFFLINE_ST:
                login_min += dur
            if ev["status"] == CAW_ST:
                caw_min += dur
        agent_productive[uid] = round(max(0, prod_min), 1)
        agent_loggedin[uid]   = round(max(0, login_min), 1)
        agent_caw[uid]        = round(max(0, caw_min), 1)

    # ── 2. PW schedule → scheduled minutes per Personnel # ──────────────────
    # For today: cap elapsed scheduled time at current time so early-day
    # efficiency isn't artificially deflated by hours not yet worked.
    # e.g. 8:00-16:30 shift at 14:00 → denominator is 6h not 8.5h.
    sched_rows    = _read_sheet(gc, SCHEDULE_SHEET_ID, tab="Schedule")
    sched_by_pers = {}   # pers → elapsed scheduled minutes
    shift_times   = {}   # pers → (shift_start, shift_end) for reference
    for r in sched_rows:
        if _parse_date_flexible(r.get("Date", "")) != target_date:
            continue
        pers  = str(r.get("Personnel Number", "") or "").strip()
        from_ = str(r.get("From", "") or "").strip()
        to_   = str(r.get("To",   "") or "").strip()
        if not pers:
            continue
        for fmt in ("%H:%M:%S", "%H:%M"):
            try:
                s = datetime.combine(target_date, datetime.strptime(from_, fmt).time())
                e = datetime.combine(target_date, datetime.strptime(to_,   fmt).time())
                # For today: only count elapsed portion of the shift
                if is_today:
                    effective_end = min(e, now_est)
                    elapsed = max(0, (effective_end - s).total_seconds() / 60)
                else:
                    elapsed = (e - s).total_seconds() / 60
                sched_by_pers[pers] = round(elapsed, 1)
                shift_times[pers]   = (s, e)
                break
            except ValueError:
                pass

    # ── 2b. Scheduled lunch per Personnel # — deducted from paid denominator ─
    # Lunch is unpaid: subtract it so denominator = paid working time.
    LUNCH_ACT  = _SCHED_LUNCH_ACTIVITIES
    block_rows = _fill_down_blocks(_read_sheet(gc, SCHEDULE_BLOCKS_ID))
    sched_lunch_min = {}
    for r in block_rows:
        if _parse_date_flexible(r.get("Date","")) != target_date:
            continue
        act = str(r.get("Activity","") or "").strip().lower()
        if act not in LUNCH_ACT:
            continue
        pers = str(r.get("Personnel Number","") or "").strip()
        if not pers:
            continue
        fs = str(r.get("Start Time","") or r.get("From","") or "").strip()
        ts = str(r.get("End Time",  "") or r.get("To",  "") or "").strip()
        for fmt in ("%H:%M:%S","%H:%M"):
            try:
                ls = datetime.combine(target_date, datetime.strptime(fs, fmt).time())
                le = datetime.combine(target_date, datetime.strptime(ts, fmt).time())
                if is_today and ls > now_est:
                    break
                if is_today:
                    le = min(le, now_est)
                sched_lunch_min[pers] = sched_lunch_min.get(pers, 0) + \
                    max(0, (le - ls).total_seconds() / 60)
                break
            except ValueError:
                pass

    # ── 3. Roster join → per-agent efficiency ────────────────────────────────
    roster_rows = _read_sheet(gc, ROSTER_SHEET_ID, tab="EMPLOYEES")
    seen_ids    = set()
    results     = []
    for r in roster_rows:
        if str(r.get("Status", "") or "").strip() == "Inactive":
            continue
        current_id = str(r.get("Current ID", "") or "").strip()
        if current_id in EXCLUDED_CURRENT_IDS or current_id in seen_ids:
            continue
        pers_num = str(r.get("Personnel #", "") or "").strip()
        fname    = str(r.get("First Name",  "") or "").strip()
        lname    = str(r.get("Last Name",   "") or "").strip()
        pu       = str(r.get("Planning Unit","") or "").strip()
        lob      = PU_TO_LOB.get(pu, pu)
        if not lob or lob in ATTENDANCE_EXCLUDED_LOBS:
            continue

        sched_min  = sched_by_pers.get(pers_num, 0)
        lunch_min  = round(sched_lunch_min.get(pers_num, 0), 1)
        paid_min   = max(0, sched_min - lunch_min)  # lunch is unpaid
        prod_min   = agent_productive.get(current_id, 0)
        if sched_min == 0 and prod_min == 0:
            continue   # no data for this agent today

        seen_ids.add(current_id)
        eff = round(prod_min / paid_min * 100, 1) if paid_min > 0 else None
        logged_min = round(agent_loggedin.get(current_id, 0), 1)
        caw_min    = round(agent_caw.get(current_id, 0), 1)
        # OOQ CAW % is measured against logged-in time, not paid/scheduled time
        caw_pct    = round(caw_min / logged_min * 100, 1) if logged_min > 0 else None
        results.append({
            "name":        f"{fname} {lname}".strip(),
            "lob":         lob,
            "sched_min":   sched_min,
            "lunch_min":   lunch_min,
            "paid_min":    paid_min,
            "logged_min":  logged_min,
            "prod_min":    round(prod_min, 1),
            "eff_pct":     eff,
            "caw_min":     caw_min,
            "caw_pct":     caw_pct,
        })

    results.sort(key=lambda x: (x["lob"], x["name"]))
    return results


def get_live_agents_by_lob(gc=None, override_raw_rows=None):
    """
    Return current agent status grouped by LOB for the live agent dashboard.
    Only includes agents who have activity today and are currently logged in
    (i.e. present in the Agent Status sheet today).
    Returns: { lob: [ {name, status, minutes_in_status, current_since}, ... ] }
    """
    try:
        if gc is None:
            gc = _get_client()
    except Exception as e:
        log.error(f"get_live_agents_by_lob auth error: {e}")
        return {}

    now_est   = _now_est()
    today_str = _today_est().strftime("%Y-%m-%d")

    rows         = _get_agent_status_rows(gc, override_raw_rows)
    roster_rows  = _read_sheet(gc, ROSTER_SHEET_ID, tab="EMPLOYEES")

    # Group by user_id → today's events
    # Also check yesterday's date string for CST→EST boundary near midnight
    import datetime as _dt_mod
    yesterday_str = (_dt_mod.date.today() - _dt_mod.timedelta(days=1)).strftime("%Y-%m-%d")
    by_uid = {}
    rows_today = 0
    for r in rows:
        uid = str(r.get("user_id", "") or "").strip()
        st  = str(r.get("start_time", "") or "").strip()
        sid = str(r.get("c_activity_sid", "") or "").strip()
        if not uid:
            continue
        is_today = st.startswith(today_str)
        is_yesterday_cst = st.startswith(yesterday_str)  # CST midnight = EST today
        if not is_today and not is_yesterday_cst:
            continue
        if uid in EXCLUDED_CURRENT_IDS:
            continue
        dt_cst = _parse_datetime_str(st)
        if not dt_cst:
            continue
        dt_est = _cp_to_est(dt_cst)
        # For yesterday CST rows, only include if they fall on today in EST
        if is_yesterday_cst and dt_est.date() != _today_est():
            continue
        if is_today:
            rows_today += 1
        status = CP_STATUS_MAP.get(sid, sid[:20] if sid else "Unknown")
        if uid not in by_uid:
            by_uid[uid] = []
        by_uid[uid].append({"dt": dt_est, "status": status})
    log.info(f"Agent status using TODAY: {today_str}, matched {rows_today}/{len(rows)} rows, {len(by_uid)} unique agents")

    # Derive current status per agent
    agent_now = {}
    for uid, events in by_uid.items():
        events.sort(key=lambda x: x["dt"])
        last = events[-1]
        mins = round((now_est - last["dt"]).total_seconds() / 60, 1)
        agent_now[uid] = {
            "status":           last["status"],
            "current_since":    last["dt"].strftime("%H:%M"),
            "minutes_in_status": max(0, mins),
        }

    # Load scheduled breaks/lunches for adherence check
    sched_breaks = _load_scheduled_breaks(gc)

    # Load schedule notes — to detect agents coded Absent/Vacation/etc in PW
    # but who are actually logged in and working.
    # Read directly from blocks sheet (already cached) so we have the time window.
    pw_note_by_norm = {}
    ABSENCE_NOTES = {
        "absent",
        "unpaid time off", "uto",
        "appointment",
        "vacation",
        "early leave", "early departure", "left early",
        "bereavement",
        "day in lieu", "lieu day",
        "stat day", "statutory",
        "vto", "voluntary time off",
    }
    try:
        block_rows_raw = _read_sheet(gc, SCHEDULE_BLOCKS_ID)
        filled_blocks  = _fill_down_blocks(block_rows_raw)
        for r in filled_blocks:
            if _parse_date_flexible(r.get("Date","")) != _today_est():
                continue
            activity  = str(r.get("Activity","") or "").strip()
            act_lower = activity.lower()
            if act_lower not in ABSENCE_NOTES:
                continue
            # Check if segment has already ended — if so, skip the badge
            to_str = str(r.get("End Time","") or r.get("To","") or "").strip()
            segment_over = False
            if to_str:
                for fmt in ("%H:%M:%S", "%H:%M"):
                    try:
                        seg_end = datetime.combine(_today_est(),
                            datetime.strptime(to_str, fmt).time())
                        if now_est > seg_end:
                            segment_over = True
                        break
                    except ValueError:
                        pass
            if segment_over:
                continue   # segment has ended — no badge
            pers = str(r.get("Personnel Number","") or "").strip()
            name = str(r.get("Employee Name","") or r.get("Employee","") or "").strip()
            if not pers and name and "," in name:
                last, first = name.split(",", 1)
                name = f"{first.strip()} {last.strip()}"
            key = pers or _norm_name(name)
            if key:
                pw_note_by_norm[key] = activity
    except Exception as e:
        log.warning(f"pw_note load error: {e}")

    sched_rows         = _read_sheet(gc, SCHEDULE_SHEET_ID, tab="Schedule")
    shift_end_by_pers  = {}
    shift_start_by_pers = {}
    for r in sched_rows:
        row_date = _parse_date_flexible(r.get("Date", ""))
        if row_date != _today_est():
            continue
        pers  = str(r.get("Personnel Number", "") or "").strip()
        from_ = str(r.get("From", "") or "").strip()
        to_   = str(r.get("To",   "") or "").strip()
        if not pers:
            continue
        for fmt in ("%H:%M:%S", "%H:%M"):
            try:
                if from_:
                    shift_start_by_pers[pers] = datetime.combine(
                        _today_est(), datetime.strptime(from_, fmt).time())
                if to_:
                    shift_end_by_pers[pers]   = datetime.combine(
                        _today_est(), datetime.strptime(to_,   fmt).time())
                break
            except ValueError:
                pass

    # Join roster → agent_now, group by LOB
    by_lob = {}
    log.info(f"roster_rows length: {len(roster_rows)}, agent_now keys: {len(agent_now)}, sample agent_now key: {next(iter(agent_now), 'NONE')}")
    _dbg = {"inactive":0,"excluded":0,"no_match":0,"no_lob":0,"added":0}
    for r in roster_rows:
        if str(r.get("Status", "") or "").strip() == "Inactive":
            _dbg["inactive"] += 1; continue
        current_id = str(r.get("Current ID", "") or "").strip()
        if current_id in EXCLUDED_CURRENT_IDS:
            _dbg["excluded"] += 1; continue
        ag = agent_now.get(current_id)
        if not ag:
            _dbg["no_match"] += 1; continue
        fname = str(r.get("First Name", "") or "").strip()
        lname = str(r.get("Last Name",  "") or "").strip()
        pu    = str(r.get("Planning Unit", "") or "").strip()
        lob   = PU_TO_LOB.get(pu, pu)
        if not lob:
            _dbg["no_lob"] += 1; continue
        _dbg["added"] += 1

        full_name = f"{fname} {lname}".strip()
        pers_num  = str(r.get("Personnel #", "") or "").strip()

        # Schedule adherence — join via Personnel # then name fallback
        norm      = _norm_name(full_name)
        schedules = sched_breaks.get(pers_num) or sched_breaks.get(norm, [])
        sched_now = None
        for s in schedules:
            if s["start"] <= now_est <= s["end"]:
                sched_now = s["type"]   # "break" or "lunch"
                break

        shift_end   = shift_end_by_pers.get(pers_num)   or shift_end_by_pers.get(norm)
        shift_start = shift_start_by_pers.get(pers_num) or shift_start_by_pers.get(norm)

        if shift_end:
            shift_done    = now_est >= shift_end
            shift_end_str = shift_end.strftime("%H:%M")
        else:
            shift_done    = None
            shift_end_str = None

        shift_start_str = shift_start.strftime("%H:%M") if shift_start else None
        pre_shift       = shift_start is not None and now_est < shift_start

        if lob not in by_lob:
            by_lob[lob] = []
        pw_note = pw_note_by_norm.get(pers_num) or pw_note_by_norm.get(_norm_name(full_name), "")

        by_lob[lob].append({
            "name":              full_name,
            "user_id":           current_id,
            "status":            ag["status"],
            "current_since":     ag["current_since"],
            "minutes_in_status": ag["minutes_in_status"],
            "sched_now":         sched_now,
            "shift_done":        shift_done,
            "shift_end":         shift_end_str,
            "shift_start":       shift_start_str,
            "pre_shift":         pre_shift,
            "not_scheduled":     shift_start is None and shift_end is None,
            "pw_note":           pw_note,
        })

    log.info(f"get_live_agents_by_lob: roster join — {_dbg} → {len(by_lob)} LOBs, {sum(len(v) for v in by_lob.values())} agents")
    # ── Pre-shift / Not-logged-in agents ────────────────────────────────────
    # Start from SCHEDULE entries (108 scheduled today) and check who hasn't
    # logged into CP yet. More reliable than roster→schedule join.
    ABSENT_GRACE_MIN = 5
    pre_shift_agents = []

    # Build Personnel # → roster row map
    pers_to_roster = {}
    for r in roster_rows:
        if str(r.get("Status","") or "").strip() == "Inactive":
            continue
        p = str(r.get("Personnel #","") or "").strip()
        if p and p not in pers_to_roster:
            pers_to_roster[p] = r

    _seen_pre_names = set()
    EXCLUDED_PERS_NUMS = {"1134", "1097"}   # Teresa Livingston, Adam Thibodeau
    for pers_num_s, shift_s in shift_start_by_pers.items():
        if pers_num_s in EXCLUDED_PERS_NUMS:
            continue
        shift_e = shift_end_by_pers.get(pers_num_s)
        if shift_e and now_est > shift_e:
            continue   # shift over

        roster_r = pers_to_roster.get(pers_num_s)
        if roster_r is None:
            continue

        current_id = str(roster_r.get("Current ID","") or "").strip()
        if current_id in EXCLUDED_CURRENT_IDS:
            continue
        if current_id in agent_now:
            continue   # already logged in

        fname = str(roster_r.get("First Name","") or "").strip()
        lname = str(roster_r.get("Last Name", "") or "").strip()
        pu    = str(roster_r.get("Planning Unit","") or "").strip()
        lob   = PU_TO_LOB.get(pu, pu)
        if not lob or lob in ATTENDANCE_EXCLUDED_LOBS:
            continue

        full_name = f"{fname} {lname}".strip()
        if not full_name:
            continue

        norm_name = _norm_name(full_name)
        if norm_name in _seen_pre_names:
            continue
        _seen_pre_names.add(norm_name)

        mins_late = round((now_est - shift_s).total_seconds() / 60, 0)
        if now_est < shift_s:
            indicator = "not_yet_started"
        elif mins_late >= ABSENT_GRACE_MIN:
            indicator = "absent"
        else:
            continue

        pre_shift_agents.append({
            "name":        full_name,
            "lob":         lob,
            "shift_start": shift_s.strftime("%H:%M"),
            "shift_end":   shift_e.strftime("%H:%M") if shift_e else "",
            "indicator":   indicator,
            "mins_late":   int(max(0, mins_late)),
        })

    pre_shift_agents.sort(key=lambda a: (a["indicator"] == "not_yet_started", a["shift_start"], a["name"]))
    log.info(f"pre_shift: shift_start_by_pers has {len(shift_start_by_pers)} entries, "
             f"sample keys: {list(shift_start_by_pers.keys())[:3]}, "
             f"pre_shift_agents: {len(pre_shift_agents)}, "
             f"names: {[a['name'] for a in pre_shift_agents[:5]]}")


    # Sort each LOB: Ready first, then On-call, then others alphabetically
    STATUS_ORDER = {"Ready": 0, "On-call": 1}
    for lob in by_lob:
        by_lob[lob].sort(key=lambda a: (
            STATUS_ORDER.get(a["status"], 2), a["name"]
        ))

    return {"by_lob": by_lob, "pre_shift": pre_shift_agents}


# ══════════════════════════════════════════════════════════════════════════

def get_ka_data(gc=None):
    try:
        if gc is None:
            gc = _get_client()
    except Exception as e:
        log.error(f"get_ka_data auth error: {e}")
        return []
    rows = _read_sheet(gc, KA_SHEET_ID)
    result = []
    for r in rows:
        name     = str(r.get("Name", "") or "").strip()
        start    = str(r.get("Start time", "") or
                       r.get("start_time", "") or "").strip()
        complete = str(r.get("Completion time", "") or
                       r.get("completion_time", "") or "").strip()
        if not name:
            continue
        duration_min = ""
        try:
            s = _parse_datetime_str(start)
            c = _parse_datetime_str(complete)
            if s and c:
                duration_min = round((c - s).total_seconds() / 60, 1)
        except Exception:
            pass
        result.append({"name": name, "start_time": start,
                        "complete_time": complete,
                        "duration_min": duration_min})
    result.sort(key=lambda x: x["complete_time"], reverse=True)
    return result# ── Queue ID (DBID) → (LOB, Language) ────────────────────────────────────
# Source: Sheet1 of Queue ID & Workload Mapping (active queues only)
# LOB names match the queue LOB column — aggregated by LOB for call status
QUEUE_MAP = {
    # ── SS Sales ──────────────────────────────────────────────────────────
    19:   ("SS Sales", "EN"),   # Access EN
    29:   ("SS Sales", "FR"),   # Access FR
    184:  ("SS Sales", "FR"),   # SS Sales FR
    295:  ("SS Sales", "EN"),   # SS Test Queue EN
    371:  ("SS Sales", "FR"),   # SS Test Queue FR
    478:  ("SS Sales", "FR"),   # SS Inbound One FR
    550:  ("SS Sales", "EN"),   # RS SnS All Agents
    719:  ("SS Sales", "EN"),   # SS Sales EN
    721:  ("SS Sales", "EN"),   # SS Sales Backup EN   <- rolled into SS Sales
    733:  ("SS Sales", "FR"),   # SS Sales Backup FR   <- rolled into SS Sales
    1071: ("SS Sales", "EN"),   # SS Sales Backup EN   <- rolled into SS Sales
    1072: ("SS Sales", "FR"),   # SS Sales Backup FR   <- rolled into SS Sales
    1553: ("SS Sales", "EN"),   # test cp queue
    1566: ("SS Sales", "EN"),   # Test SS RC Sales queue
    1571: ("SS Sales", "EN"),   # Test SS Store queue
    1572: ("SS Sales", "EN"),   # SS Sales EN test CG one
    1573: ("SS Sales", "FR"),   # SS Sales FR IB Direct
    1574: ("SS Sales", "EN"),   # SS Sales EN IB Direct
    1605: ("SS Sales", "EN"),   # SS Sales Infinite EN <- was missing, now added
    1606: ("SS Sales", "FR"),   # SS Sales Infinite FR <- was missing, now added
    # ── SS Case Management ────────────────────────────────────────────────
    195:  ("SS Case Management", "EN"),  # Emergency Alliance EN
    720:  ("SS Case Management", "EN"),  # SS TF Client Care EN
    734:  ("SS Case Management", "FR"),  # SS TF Client Care FR
    928:  ("SS Case Management", "EN"),  # SS RC CCA EN
    995:  ("SS Case Management", "FR"),  # Emergency Alliance FR
    1101: ("SS Case Management", "FR"),  # SS RC CCA FR
    1138: ("SS Case Management", "EN"),  # SS Case Mgmt EN
    1368: ("SS Case Management", "EN"),  # SS Loc CCA EN
    1379: ("SS Case Management", "FR"),  # SS Loc CCA FR
    1491: ("SS Case Management", "EN"),  # SS Payment EN
    # ── PS Sales ──────────────────────────────────────────────────────────
    148:  ("PS Sales", "EN"),   # PS Cubeit EN
    149:  ("PS Sales", "FR"),   # PS Cubeit FR
    150:  ("PS Sales", "EN"),   # PS PUPS EN
    152:  ("PS Sales", "EN"),   # PS Cubeit Costco EN
    153:  ("PS Sales", "FR"),   # PS Cubeit Costco FR
    190:  ("PS Sales", "EN"),   # PS Restoration CUBEIT
    226:  ("PS Sales", "EN"),   # PS PUPS Home Depot
    244:  ("PS Sales", "EN"),   # PS StorageVault Containers
    382:  ("PS Sales", "EN"),   # PS SVI Generic
    1441: ("PS Sales", "EN"),   # PS Sales Back Up EN
    1442: ("PS Sales", "FR"),   # PS Sales Back Up FR
    # ── PS Care ───────────────────────────────────────────────────────────
    373:  ("PS Care", "EN"),    # PS Client Care EN (lang blank in CP, confirmed EN)
    374:  ("PS Care", "FR"),    # PS Client Care FR
    # ── PS Case Manager ───────────────────────────────────────────────────
    384:  ("PS Case Manager", "FR"),  # PS Case Manager FR
    488:  ("PS Case Manager", "EN"),  # PS Case Manager EN
    528:  ("PS Case Manager", "EN"),  # PS Retention
    # ── Web Leads SS Inbound ──────────────────────────────────────────────
    147:  ("Web Leads SS Inbound", "EN"),  # Web Leads Inbound Pilot <- was SS Sales, corrected
    263:  ("Web Leads SS Inbound", "FR"),  # Web Leads FR (confirmed FR Inbound)
    526:  ("Web Leads SS Inbound", "FR"),  # Web Leads FR <- was missing, now added
    538:  ("Web Leads SS Inbound", "EN"),  # Web Leads EN
    # ── MoveBuddy ─────────────────────────────────────────────────────────
    503:  ("MoveBuddy", "EN"),  # PS Concierge Moving EN
    1137: ("MoveBuddy", "EN"),  # MoveBuddy EN
    # ── I.T Support ───────────────────────────────────────────────────────
    832:  ("I.T Support", ""),  # Helpdesk Support BackUp
    968:  ("I.T Support", ""),  # SVI IT
    1512: ("I.T Support", ""),  # SVI Help Desk
    1513: ("I.T Support", ""),  # SVI IT Combo (review - currently kept)
}
# Simple LOB-only map for compatibility
QUEUE_LOB_MAP = {k: v[0] for k, v in QUEUE_MAP.items()}
