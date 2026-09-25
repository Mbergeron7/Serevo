"""
data_source.py  —  pluggable data layer for the WFM Portal
===========================================================

Purpose
-------
The app originally read employees / planning units / requirements / forecast
straight from PeopleWare. This module puts ONE seam in front of that so the same
app can instead read the identical information from Google Sheets — which is what
makes it deployable for any client (and safe for a demo).

Two implementations sit behind one interface:

    PeopleWareSource  -> the original behaviour (wraps capacity_planner)
    SheetSource       -> reads the same shapes from the capacity Google Sheet
                         (the "EMPLOYEES", "REQUIREMENTS RAW", "FORECAST RAW"
                          tabs the app already uses as its cache)

A single environment variable picks which is live:

    DATA_SOURCE=generic       (default)  -> SheetSource
    DATA_SOURCE=peopleware               -> PeopleWareSource

IMPORTANT — return convention
-----------------------------
To stay drop-in compatible with the existing call sites, every method returns a
(data, error) TUPLE, exactly like the capacity_planner.fetch_* functions do.
    employees, err = src.get_employees()
    if err: ...            # handle
So you can literally replace:
    employees, err = capacity_planner.fetch_employees()
with:
    employees, err = data_source.get_source().get_employees()

Column names below MATCH the real tabs. If your Sheet uses different headers,
change the constants in one place (COLS_*).
"""

import os
import datetime
from sqlalchemy import func as sa_func

# ---- tab names (match the app's existing capacity-sheet tabs) ----
TAB_EMPLOYEES     = "EMPLOYEES"
TAB_REQUIREMENTS  = "REQUIREMENTS RAW"
TAB_FORECAST      = "FORECAST RAW"
TAB_PLANNINGUNITS = "PlanningUnits"   # small tab: id | name (create it, or units are derived)

# ---- REQUIREMENTS RAW / FORECAST RAW are LONG format: one row per interval ----
COL_TIMESTAMP = "Timestamp"
COL_LOB       = "LOB"
COL_AGENTS    = "agents_required"
COL_OFFERED   = "offered"
COL_AHT       = "aht"


def _get_sheets_config():
    """Get Google Sheets config from env vars, falling back to DB settings."""
    sheet_key = os.environ.get("CAPACITY_SHEET_KEY", "").strip()
    sa_file = os.environ.get("SERVICE_ACCOUNT_FILE", "").strip()
    sa_json = ""

    # Fall back to database settings if env vars aren't set
    if not sheet_key or not sa_file:
        try:
            from app.models import AppSetting
            if not sheet_key:
                sheet_key = AppSetting.get("google_sheet_key", "")
            if not sa_file:
                sa_json = AppSetting.get("google_service_account_json", "")
        except Exception:
            pass  # DB not available yet

    return sheet_key, sa_file, sa_json


def _open_capacity_sheet():
    """Open the capacity Google Sheet via the service account. Returns (sheet, err)."""
    try:
        import gspread
        from oauth2client.service_account import ServiceAccountCredentials
        scope = ["https://spreadsheets.google.com/feeds",
                 "https://www.googleapis.com/auth/drive"]

        sheet_key, sa_file, sa_json = _get_sheets_config()

        if not sheet_key:
            return None, "No Google Sheet key configured"

        if sa_json:
            # Use JSON credentials stored in the database
            import json
            creds_dict = json.loads(sa_json)
            creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        elif sa_file:
            creds = ServiceAccountCredentials.from_json_keyfile_name(sa_file, scope)
        else:
            return None, "No service account credentials configured"

        client = gspread.authorize(creds)
        return client.open_by_key(sheet_key), None
    except Exception as e:
        return None, f"could not open capacity sheet: {e}"


def _date_str(day):
    """Accept a date/datetime or an already-formatted 'YYYY-MM-DD' string."""
    if isinstance(day, (datetime.date, datetime.datetime)):
        return day.strftime("%Y-%m-%d")
    return str(day)[:10]


# =====================================================================
# PeopleWare — original behaviour, unchanged
# =====================================================================
class PeopleWareSource:
    def get_employees(self):
        import capacity_planner
        return capacity_planner.fetch_employees()

    def get_planning_units(self):
        import capacity_planner
        return capacity_planner.fetch_planning_units()

    def get_requirements(self, unit_id, day):
        import capacity_planner
        day_obj = day if isinstance(day, datetime.date) else \
            datetime.datetime.strptime(_date_str(day), "%Y-%m-%d").date()
        return capacity_planner.fetch_requirements_day(unit_id, day_obj)

    def get_forecast(self, workload_id, day):
        import capacity_planner
        day_obj = day if isinstance(day, datetime.date) else \
            datetime.datetime.strptime(_date_str(day), "%Y-%m-%d").date()
        utc_offset = int(os.environ.get("PW_UTC_OFFSET", "-4"))
        return capacity_planner.fetch_forecast_day(workload_id, day_obj, utc_offset)


# =====================================================================
# Generic — reads the same shapes from Google Sheets
# =====================================================================
class SheetSource:
    def __init__(self):
        self._sheet = None
        self._err = None

    def _sheet_or_err(self):
        if self._sheet is None and self._err is None:
            self._sheet, self._err = _open_capacity_sheet()
        return self._sheet, self._err

    def _records(self, tab):
        sheet, err = self._sheet_or_err()
        if err:
            return None, err
        try:
            return sheet.worksheet(tab).get_all_records(), None
        except Exception as e:
            return None, f"tab '{tab}' unreadable: {e}"

    def get_employees(self):
        """Returns (list_of_employee_dicts, err). Columns match the EMPLOYEES tab:
        Status, First Name, Last Name, Employee ID, Latest Skill Name,
        Latest Skill Start, Latest Skill End, All Skills, End Date."""
        return self._records(TAB_EMPLOYEES)

    def get_planning_units(self):
        """Returns (list_of_units, err). Uses a PlanningUnits tab if present,
        otherwise derives the distinct LOBs found in REQUIREMENTS RAW."""
        units, err = self._records(TAB_PLANNINGUNITS)
        if not err and units:
            return units, None
        rows, err = self._records(TAB_REQUIREMENTS)
        if err:
            return None, err
        seen, out = set(), []
        for r in rows:
            lob = str(r.get(COL_LOB, "")).strip()
            if lob and lob not in seen:
                seen.add(lob)
                out.append({"id": lob, "name": lob})
        return out, None

    def get_requirements(self, unit_id, day):
        """Returns (list_of_{time, agents_required}, err) for one unit + day."""
        rows, err = self._records(TAB_REQUIREMENTS)
        if err:
            return None, err
        target = _date_str(day)
        out = []
        for r in rows:
            if str(r.get(COL_LOB, "")).strip() != str(unit_id).strip():
                continue
            ts = str(r.get(COL_TIMESTAMP, ""))
            if ts[:10] != target:
                continue
            out.append({
                "time": ts,
                "agents_required": float(r.get(COL_AGENTS, 0) or 0),
            })
        return out, None

    def get_forecast(self, workload_id, day):
        """Returns (list_of_{time, offered, aht}, err) for one workload + day."""
        rows, err = self._records(TAB_FORECAST)
        if err:
            return None, err
        target = _date_str(day)
        out = []
        for r in rows:
            if str(r.get(COL_LOB, "")).strip() != str(workload_id).strip():
                continue
            ts = str(r.get(COL_TIMESTAMP, ""))
            if ts[:10] != target:
                continue
            out.append({
                "time": ts,
                "offered": float(r.get(COL_OFFERED, 0) or 0),
                "aht": float(r.get(COL_AHT, 0) or 0),
            })
        return out, None


# =====================================================================
# PostgreSQL — reads from the local database via SQLAlchemy
# =====================================================================
class PostgresSource:
    """Reads data from the PostgreSQL database."""

    def get_employees(self):
        try:
            from app.models import Employee
            emps = Employee.query.all()
            return [e.to_legacy_dict() for e in emps], None
        except Exception as e:
            return [], str(e)

    def get_planning_units(self):
        try:
            from app.models import PlanningUnit
            units = PlanningUnit.query.filter_by(is_active=True).all()
            return [{"id": u.name, "name": u.name} for u in units], None
        except Exception as e:
            return [], str(e)

    def get_requirements(self, unit_id, day):
        try:
            from app.models import RequirementInterval, PlanningUnit
            unit = PlanningUnit.query.filter_by(name=str(unit_id).strip()).first()
            if not unit:
                return [], None
            target = _date_str(day)
            rows = RequirementInterval.query.filter(
                RequirementInterval.planning_unit_id == unit.id,
                sa_func.date(RequirementInterval.timestamp) == target,
            ).order_by(RequirementInterval.timestamp).all()
            return [r.to_legacy_dict() for r in rows], None
        except Exception as e:
            return [], str(e)

    def get_forecast(self, workload_id, day):
        try:
            from app.models import ForecastInterval, PlanningUnit
            unit = PlanningUnit.query.filter_by(name=str(workload_id).strip()).first()
            if not unit:
                return [], None
            target = _date_str(day)
            rows = ForecastInterval.query.filter(
                ForecastInterval.planning_unit_id == unit.id,
                sa_func.date(ForecastInterval.timestamp) == target,
            ).order_by(ForecastInterval.timestamp).all()
            return [r.to_legacy_dict() for r in rows], None
        except Exception as e:
            return [], str(e)


# =====================================================================
# Demo — returns realistic fake data when no sheet is connected
# =====================================================================
class DemoSource:
    """Self-contained demo data — no Google Sheets needed."""

    def get_employees(self):
        from app.demo_data import get_demo_employees
        return get_demo_employees()

    def get_planning_units(self):
        from app.demo_data import get_demo_planning_units
        return get_demo_planning_units()

    def get_requirements(self, unit_id, day):
        from app.demo_data import get_demo_requirements
        return get_demo_requirements(unit_id, day)

    def get_forecast(self, workload_id, day):
        from app.demo_data import get_demo_forecast
        return get_demo_forecast(workload_id, day)


# =====================================================================
# Selector
# =====================================================================
_INSTANCE = None

def get_source():
    """Return the active data source (cached). Chosen by DATA_SOURCE env var."""
    global _INSTANCE
    if _INSTANCE is None:
        mode = os.environ.get("DATA_SOURCE", "generic").strip().lower()
        if mode == "peopleware":
            _INSTANCE = PeopleWareSource()
        elif mode == "postgres":
            _INSTANCE = PostgresSource()
        else:
            # Try SheetSource; fall back to DemoSource if no sheet key
            sheet_key, sa_file, sa_json = _get_sheets_config()
            demo = os.environ.get("DEMO_MODE", "false").strip().lower() == "true"
            if demo and not sheet_key:
                _INSTANCE = DemoSource()
            elif sheet_key and (sa_file or sa_json):
                _INSTANCE = SheetSource()
            else:
                # No sheets config — use Postgres if DATABASE_URL is set, else Demo
                db_url = os.environ.get("DATABASE_URL", "").strip()
                if db_url:
                    _INSTANCE = PostgresSource()
                elif demo:
                    _INSTANCE = DemoSource()
                else:
                    _INSTANCE = SheetSource()  # will error but keeps old behavior
    return _INSTANCE


def is_demo_source():
    """Check if the current data source is the demo provider."""
    return isinstance(get_source(), DemoSource)
