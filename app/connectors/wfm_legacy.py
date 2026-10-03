"""
connectors/wfm_legacy.py — Legacy WFM platform API connector
=============================================================

Connector for WFM platforms that expose a dual-API surface:
  - Legacy REST API (v1): employees, schedules, planning units, contracts, skills
  - Modern REST API: forecasts (workload-based), people metadata

Pulls:
  - Employee roster with planning units, contracts, skills, addresses
  - Forecast volumes (offered calls + AHT) per workload per interval
  - Schedules (shift blocks per employee per day)
  - Activity definitions

Both API surfaces use the same Bearer token.
"""

import logging
from datetime import date, datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from app.connectors.base import BaseConnector

log = logging.getLogger("serevo.connectors.wfm_legacy")

# ── Constants ────────────────────────────────────────────────────
# Default API base URLs — overridden per-connection via base_url + credentials
LEGACY_BASE = "https://legacy-api.example.com/v1"
NEW_BASE = "https://api.example.com"
MAX_WORKERS = 10
TIMEOUT = 15


def _make_session():
    """Create a requests.Session with retry logic."""
    session = requests.Session()
    retry = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


class WFMLegacyConnector(BaseConnector):
    display_name = "WFM Platform (Legacy API)"
    supported_auth_types = ("bearer",)
    default_base_url = LEGACY_BASE

    def __init__(self, connection):
        super().__init__(connection)
        # Token may be stored under different keys depending on how the
        # connection was saved (bearer → "access_token"/"token", api_key → "api_key")
        self.token = (self.creds.get("access_token")
                      or self.creds.get("token")
                      or self.creds.get("api_key", "")).strip()
        self._headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        # Workload config — loaded on demand
        self._workload_ids = None
        self._activity_lookup = None

    # ── Connection test ──────────────────────────────────────────

    def test_connection(self):
        try:
            session = _make_session()
            resp = session.get(
                f"{LEGACY_BASE}/employees",
                headers=self._headers,
                timeout=TIMEOUT,
                params={"page[size]": 1},
            )
            if resp.ok:
                return True, "OK — connected to legacy API"
            # Show a clean message, not raw HTML
            detail = resp.text[:200] if resp.text and not resp.text.strip().startswith("<") else ""
            msg = f"HTTP {resp.status_code}"
            if resp.status_code == 401:
                msg += " Unauthorized — check that the API token is correct"
            elif resp.status_code == 403:
                msg += " Forbidden — the token may lack permissions"
            if detail:
                msg += f": {detail}"
            return False, msg
        except Exception as e:
            return False, str(e)

    # ── Planning units ───────────────────────────────────────────

    def fetch_planning_units(self):
        try:
            session = _make_session()
            resp = session.get(
                f"{LEGACY_BASE}/planning_units",
                headers=self._headers,
                timeout=TIMEOUT,
            )
            if not resp.ok:
                return [], f"HTTP {resp.status_code}"
            units = resp.json().get("planning_units", [])
            result = []
            for u in units:
                result.append({
                    "id": int(u.get("planning_unit_id", 0)),
                    "name": u.get("name", ""),
                })
            return result, None
        except Exception as e:
            return [], str(e)

    # ── Activities ───────────────────────────────────────────────

    def fetch_activities(self):
        try:
            session = _make_session()
            resp = session.get(
                f"{LEGACY_BASE}/activities",
                headers=self._headers,
                timeout=TIMEOUT,
            )
            if not resp.ok:
                return [], f"HTTP {resp.status_code}"
            activities = resp.json().get("activities", [])
            return [
                {"id": a.get("activity_id"), "name": a.get("name", "Unnamed")}
                for a in activities
            ], None
        except Exception as e:
            return [], str(e)

    def _get_activity_lookup(self):
        if self._activity_lookup is None:
            acts, _ = self.fetch_activities()
            self._activity_lookup = {a["id"]: a["name"] for a in acts}
        return self._activity_lookup

    # ── Employees ────────────────────────────────────────────────

    def fetch_employees(self):
        """
        Full employee roster with planning units, contracts, skills, and addresses.
        Uses concurrent API calls for per-employee detail (planning unit, skills, etc.).
        """
        try:
            session = _make_session()

            # 1. Get all employees
            resp = session.get(f"{LEGACY_BASE}/employees", headers=self._headers, timeout=TIMEOUT)
            if not resp.ok:
                return [], f"HTTP {resp.status_code}"
            emp_data = resp.json()
            employees = emp_data.get("employees", []) if isinstance(emp_data, dict) else emp_data

            # 2. Get planning unit names
            pu_names = {}
            try:
                pu_resp = session.get(f"{LEGACY_BASE}/planning_units", headers=self._headers, timeout=TIMEOUT)
                if pu_resp.ok:
                    for u in pu_resp.json().get("planning_units", []):
                        pu_names[int(u.get("planning_unit_id", 0))] = u.get("name", "")
            except Exception:
                pass

            # 3. Get contract type names
            contract_lookup = {}
            try:
                cr = session.get(f"{LEGACY_BASE}/contracts", headers=self._headers, timeout=TIMEOUT)
                if cr.ok:
                    for c in cr.json().get("contracts", []):
                        contract_lookup[str(c.get("contract_id", ""))] = c.get("name", "")
            except Exception:
                pass

            # 4. Get employment periods
            emp_periods = {}
            try:
                ep = session.get(f"{LEGACY_BASE}/employee_employment_periods", headers=self._headers, timeout=TIMEOUT)
                if ep.ok:
                    for p in ep.json().get("employee_employment_periods", []):
                        eid = str(p.get("employee_id", ""))
                        emp_periods[eid] = {
                            "start": p.get("start_date", ""),
                            "end": "" if p.get("end_date") == "4000-01-01" else p.get("end_date", ""),
                        }
            except Exception:
                pass

            # 5. Fetch per-employee details in parallel
            details = {}
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                futures = {
                    executor.submit(
                        self._fetch_employee_details, emp, pu_names, contract_lookup
                    ): emp
                    for emp in employees
                }
                for future in as_completed(futures):
                    try:
                        result = future.result()
                        details[result["employee_id"]] = result
                    except Exception as e:
                        emp = futures[future]
                        log.warning("Employee detail fetch error for %s: %s", emp.get("employee_id"), e)

            # 6. Build normalized result
            result = []
            for emp in employees:
                eid = str(emp.get("employee_id", ""))
                d = details.get(eid, {})
                period = emp_periods.get(eid, {})
                color = emp.get("color")
                status = self._status_from_color(color)

                result.append({
                    "employee_id": eid,
                    "first_name": emp.get("first_name", ""),
                    "last_name": emp.get("last_name", ""),
                    "personnel_number": emp.get("personnel_number", ""),
                    "planning_unit": d.get("planning_unit", ""),
                    "status": status,
                    "start_date": period.get("start", ""),
                    "end_date": period.get("end", ""),
                    "contract_type": d.get("contract_type", ""),
                    "contract_start": d.get("contract_start", ""),
                    "contract_end": d.get("contract_end", ""),
                    "skills": d.get("all_skills", []),
                    "latest_skill": d.get("latest_skill_name", ""),
                    "email": d.get("email", ""),
                    "phone": d.get("phone", ""),
                    "city": d.get("city", ""),
                    "deleted": bool(emp.get("deleted")),
                    "auto_shift_assignment": bool(emp.get("automated_shift_assignment")),
                })

            return result, None

        except Exception as e:
            log.exception("Employee fetch failed")
            return [], str(e)

    def _fetch_employee_details(self, emp, pu_names, contract_lookup):
        """Fetch planning unit, contracts, skills, and address for one employee."""
        session = _make_session()
        eid = str(emp.get("employee_id", ""))
        result = {"employee_id": eid}

        # Planning unit
        try:
            r = session.get(f"{LEGACY_BASE}/employees/{eid}/planning_units", headers=self._headers, timeout=TIMEOUT)
            if r.ok:
                data = r.json().get("data", [])
                if data:
                    if "assignment_date" in data[0]:
                        data.sort(key=lambda x: x.get("assignment_date", ""), reverse=True)
                    latest = data[0] if "assignment_date" in data[0] else data[-1]
                    pu_id = int(latest.get("planning_unit_id", 0))
                    result["planning_unit"] = pu_names.get(pu_id, "")
        except Exception:
            pass

        # Contracts
        try:
            r = session.get(f"{LEGACY_BASE}/employees/{eid}/contracts", headers=self._headers, timeout=TIMEOUT)
            if r.ok:
                contracts = r.json().get("data", [])
                chosen = self._pick_contract(contracts)
                if chosen:
                    cid = str(chosen.get("contract_id", ""))
                    result["contract_type"] = contract_lookup.get(cid, "")
                    result["contract_start"] = chosen.get("start_date", "")
                    end = chosen.get("end_date", "")
                    result["contract_end"] = "" if end == "4000-01-01" else end
        except Exception:
            pass

        # Skills
        try:
            r = session.get(f"{LEGACY_BASE}/employees/{eid}/skill_levels", headers=self._headers, timeout=TIMEOUT)
            if r.ok:
                skill_data = r.json().get("data", [])
                today = date.today()
                active = []
                for s in skill_data:
                    end_str = s.get("end_date")
                    if not end_str:
                        active.append(s)
                    else:
                        try:
                            if datetime.strptime(end_str, "%Y-%m-%d").date() >= today:
                                active.append(s)
                        except ValueError:
                            pass
                if active:
                    active.sort(key=lambda x: x.get("start_date", ""), reverse=True)
                    result["latest_skill_name"] = str(active[0].get("skill_id", ""))
                    result["all_skills"] = list({str(s.get("skill_id", "")) for s in active})
        except Exception:
            pass

        # Address
        try:
            r = session.get(f"{LEGACY_BASE}/employee_current_addresses/{eid}", headers=self._headers, timeout=TIMEOUT)
            if r.ok:
                a = r.json()
                if isinstance(a, dict):
                    # Handle nested response structures
                    for key in ("employee_current_address", "data"):
                        if key in a and a[key]:
                            a = a[key] if not isinstance(a[key], list) else a[key][0]
                            break
                    if isinstance(a, dict):
                        result["email"] = a.get("email", "") or ""
                        result["phone"] = a.get("phone", "") or ""
                        result["city"] = a.get("city", "") or ""
        except Exception:
            pass

        return result

    @staticmethod
    def _pick_contract(contracts):
        """Pick the most relevant contract (active > past > any)."""
        today = date.today()
        active, past = [], []
        for c in contracts:
            try:
                start = datetime.strptime(c.get("start_date", ""), "%Y-%m-%d").date() if c.get("start_date") else None
            except ValueError:
                start = None
            try:
                end_str = c.get("end_date", "")
                end = datetime.strptime(end_str, "%Y-%m-%d").date() if end_str and end_str != "4000-01-01" else None
            except ValueError:
                end = None

            if start and start <= today and (end is None or today <= end):
                active.append((start, c))
            elif end and end < today:
                past.append((start or date.min, c))

        if active:
            return max(active, key=lambda x: x[0])[1]
        if past:
            return max(past, key=lambda x: x[0])[1]
        if contracts:
            return max(contracts, key=lambda x: x.get("start_date", ""))
        return None

    @staticmethod
    def _status_from_color(color):
        """Derive employee status from the platform's color coding."""
        color_str = str(color) if color is not None else ""
        if color_str == "16711680":
            return "LOA"
        elif color_str in ("3739363", "255"):
            return "Inactive"
        return "Active"

    # ── Forecasts ────────────────────────────────────────────────

    def fetch_forecasts(self, date_from, date_to, workload_ids=None):
        """
        Fetch forecast data (offered calls + AHT) per 30-min interval
        for all configured workloads across the date range.

        workload_ids: list of workload UUIDs from the platform.
                      If None, uses workloads configured on the connection.
        """
        try:
            if workload_ids is None:
                workload_ids = self._get_workload_ids()
            if not workload_ids:
                return [], "No workloads configured"

            # Default business hours
            default_hours = ("08:00", "22:00", "09:00", "22:00", "09:00", "22:00")
            workload_hours = self.creds.get("workload_hours", {})

            import pandas as pd
            local_tz = self.creds.get("timezone", "America/Toronto")

            dates = pd.date_range(
                start=pd.Timestamp(str(date_from)),
                end=pd.Timestamp(str(date_to)),
                freq="D",
            )

            all_rows = []

            with _make_session() as session:
                session.headers.update(self._headers)

                with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                    futures = {}
                    for day in dates:
                        for wid in workload_ids:
                            wd = day.weekday()
                            mo, mc, sa, sc, su, uc = workload_hours.get(wid, default_hours)
                            open_str, close_str = (
                                (mo, mc) if wd < 5 else
                                (sa, sc) if wd == 5 else
                                (su, uc)
                            )
                            futures[executor.submit(
                                self._fetch_forecast_day, session, day, wid,
                                open_str, close_str, local_tz
                            )] = (day, wid)

                    for future in as_completed(futures):
                        try:
                            rows = future.result()
                            all_rows.extend(rows)
                        except Exception as e:
                            day, wid = futures[future]
                            log.warning("Forecast fetch error %s/%s: %s", day.date(), wid, e)

            return all_rows, None

        except Exception as e:
            log.exception("Forecast fetch failed")
            return [], str(e)

    def _fetch_forecast_day(self, session, day, wid, open_str, close_str, local_tz):
        """Fetch forecast for one workload on one day."""
        import pandas as pd

        open_dt = pd.to_datetime(f"{day.date()} {open_str}").tz_localize(local_tz)
        close_dt = pd.to_datetime(f"{day.date()} {close_str}").tz_localize(local_tz)
        if open_dt >= close_dt:
            return []

        start_iso = open_dt.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")
        end_iso = close_dt.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")

        url = f"{NEW_BASE}/workloads/{wid}/forecasts?startTime={start_iso}&endTime={end_iso}"
        resp = session.get(url, timeout=30)
        if not resp.ok:
            return []

        data = resp.json().get("data", {})
        interval = pd.to_timedelta(data.get("intervalDuration", "PT30M"))
        forecasts = data.get("forecasts", {})

        # Use operational forecast if available, fall back to auto
        block = forecasts.get("operational") or forecasts.get("auto") or forecasts.get("rawAuto")
        if not block:
            return []

        rows = []
        offered_vals = block.get("offered", {}).get("values", [])
        aht_vals = block.get("averageHandlingTime", {}).get("values", [])

        max_len = max(len(offered_vals), len(aht_vals))
        for idx in range(max_len):
            ts = open_dt + idx * interval
            if ts >= close_dt:
                break
            rows.append({
                "timestamp": ts.tz_localize(None).strftime("%Y-%m-%d %H:%M:%S"),
                "workload_id": wid,
                "offered": offered_vals[idx] if idx < len(offered_vals) else 0,
                "aht": aht_vals[idx] if idx < len(aht_vals) else 0,
            })

        return rows

    def _get_workload_ids(self):
        """Get workload IDs from connection credentials config."""
        if self._workload_ids is None:
            self._workload_ids = self.creds.get("workload_ids", [])
        return self._workload_ids

    # ── Schedules ────────────────────────────────────────────────

    def fetch_schedules(self, date_from, date_to, employee_ids=None):
        """
        Fetch schedule blocks for all (or specified) employees across
        the date range. Returns detailed blocks with activity names.
        """
        try:
            session = _make_session()

            # Get employee list if not provided
            if employee_ids is None:
                resp = session.get(f"{LEGACY_BASE}/employees", headers=self._headers, timeout=TIMEOUT)
                if not resp.ok:
                    return [], f"HTTP {resp.status_code}"
                emp_data = resp.json()
                employees = emp_data.get("employees", []) if isinstance(emp_data, dict) else emp_data
                employee_ids = [
                    emp for emp in employees
                    if isinstance(emp, dict) and not emp.get("deleted")
                ]
            else:
                # Convert IDs to dicts if needed
                employee_ids = [
                    {"employee_id": eid} if not isinstance(eid, dict) else eid
                    for eid in employee_ids
                ]

            activity_lookup = self._get_activity_lookup()

            # Build date list
            d = date_from if isinstance(date_from, date) else datetime.strptime(str(date_from)[:10], "%Y-%m-%d").date()
            d_end = date_to if isinstance(date_to, date) else datetime.strptime(str(date_to)[:10], "%Y-%m-%d").date()
            dates = []
            while d <= d_end:
                dates.append(d)
                d += timedelta(days=1)

            all_rows = []

            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                futures = {}
                for day in dates:
                    for emp in employee_ids:
                        futures[executor.submit(
                            self._fetch_schedule_day, session, emp, day, activity_lookup
                        )] = (emp, day)

                for future in as_completed(futures):
                    try:
                        rows = future.result()
                        all_rows.extend(rows)
                    except Exception as e:
                        emp, day = futures[future]
                        log.warning("Schedule fetch error: %s", e)

            return all_rows, None

        except Exception as e:
            log.exception("Schedule fetch failed")
            return [], str(e)

    def _fetch_schedule_day(self, session, emp, day, activity_lookup):
        """Fetch schedule for one employee on one day."""
        eid = emp.get("employee_id") if isinstance(emp, dict) else emp
        full_name = (
            f"{emp.get('first_name', '')} {emp.get('last_name', '')}".strip()
            if isinstance(emp, dict) else str(eid)
        )

        url = f"{LEGACY_BASE}/employees/{eid}/schedule/{day.isoformat()}"
        resp = session.get(url, headers=self._headers, timeout=TIMEOUT)
        if not resp.ok:
            return []

        blocks = [
            blk
            for entry in resp.json().get("schedules", [])
            for blk in entry.get("schedule_blocks", [])
        ]

        if not blocks:
            return []

        # Build detailed block list and find overall start/end
        earliest, latest = None, None
        block_list = []

        for blk in blocks:
            try:
                start_dt = datetime.fromisoformat(blk.get("time_start", ""))
                end_dt = datetime.fromisoformat(blk.get("time_end", ""))
                if end_dt < start_dt:
                    end_dt += timedelta(days=1)
            except (ValueError, TypeError):
                continue

            if not earliest or start_dt < earliest:
                earliest = start_dt
            if not latest or end_dt > latest:
                latest = end_dt

            block_list.append({
                "activity": activity_lookup.get(blk.get("activity_id"), f"Unknown #{blk.get('activity_id')}"),
                "start": start_dt.strftime("%H:%M"),
                "end": end_dt.strftime("%H:%M"),
                "type": blk.get("type", ""),
            })

        if not earliest or not latest:
            return []

        hours = round((latest - earliest).total_seconds() / 3600, 2)

        return [{
            "date": day.isoformat(),
            "employee_id": str(eid),
            "employee_name": full_name,
            "start": earliest.strftime("%H:%M"),
            "end": latest.strftime("%H:%M"),
            "hours": hours,
            "blocks": block_list,
        }]

    # ── Workloads ────────────────────────────────────────────────

    def fetch_workloads(self):
        """Fetch workload/queue definitions from the new API."""
        try:
            session = _make_session()
            # The new API doesn't have a list-all-workloads endpoint,
            # so we return what's configured in credentials
            workloads = self.creds.get("workloads", {})
            if workloads:
                return [
                    {"id": wid, "name": name}
                    for wid, name in workloads.items()
                ], None
            return [], None
        except Exception as e:
            return [], str(e)
