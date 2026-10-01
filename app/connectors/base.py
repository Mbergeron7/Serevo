"""
connectors/base.py — abstract base class for WFM platform connectors
=====================================================================

Every connector normalizes external API data into these shapes:

  Employees → list of dicts:
    {employee_id, first_name, last_name, planning_unit, status,
     skills: [str], contract_type, start_date, end_date,
     email, phone, personnel_number, ...}

  Forecasts → list of dicts:
    {timestamp (ISO), lob, offered (int), aht (float seconds)}

  Schedules → list of dicts:
    {date, employee_id, employee_name, start, end, hours,
     blocks: [{activity, start, end, type}]}

  Requirements → list of dicts:
    {timestamp (ISO), lob, agents_required (float)}
"""

import logging
from abc import ABC, abstractmethod

log = logging.getLogger("serevo.connectors")


class BaseConnector(ABC):
    """Abstract connector that all platform-specific connectors extend."""

    # Human-readable name shown in the UI
    display_name = "Unknown"

    # Which auth types this connector supports
    supported_auth_types = ("bearer",)

    # Default base URL (can be overridden per-connection)
    default_base_url = ""

    def __init__(self, connection):
        """
        Initialize with an APIConnection model instance.
        The connection provides base_url, auth_type, and credentials.
        """
        self.connection = connection
        self.base_url = (connection.base_url or self.default_base_url).rstrip("/")
        self._parse_credentials()

    def _parse_credentials(self):
        """Parse the JSON credentials blob from the connection."""
        import json
        try:
            self.creds = json.loads(self.connection.credentials) if self.connection.credentials else {}
        except (json.JSONDecodeError, TypeError):
            self.creds = {}

    def _auth_headers(self):
        """Build authorization headers based on auth_type."""
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        auth_type = self.connection.auth_type or "bearer"

        if auth_type == "bearer":
            token = self.creds.get("token", "")
            headers["Authorization"] = f"Bearer {token}"
        elif auth_type == "basic":
            import base64
            pair = f"{self.creds.get('username', '')}:{self.creds.get('password', '')}"
            headers["Authorization"] = f"Basic {base64.b64encode(pair.encode()).decode()}"
        elif auth_type == "api_key":
            key_name = self.creds.get("header_name", "X-API-Key")
            headers[key_name] = self.creds.get("api_key", "")

        return headers

    # ── Required methods ─────────────────────────────────────────

    @abstractmethod
    def test_connection(self):
        """
        Test that the connection is working.
        Returns (True, "OK") or (False, "error message").
        """
        ...

    @abstractmethod
    def fetch_employees(self):
        """
        Fetch the full employee roster.
        Returns (list_of_employee_dicts, error_string_or_None).
        """
        ...

    @abstractmethod
    def fetch_forecasts(self, date_from, date_to, workload_ids=None):
        """
        Fetch forecast data for a date range.
        Returns (list_of_forecast_dicts, error_string_or_None).
        """
        ...

    @abstractmethod
    def fetch_schedules(self, date_from, date_to, employee_ids=None):
        """
        Fetch schedule data for a date range.
        Returns (list_of_schedule_dicts, error_string_or_None).
        """
        ...

    # ── Optional methods (not all platforms have these) ───────────

    def fetch_planning_units(self):
        """Fetch planning units / LOBs. Returns (list, error)."""
        return [], None

    def fetch_workloads(self):
        """Fetch workload / queue definitions. Returns (list, error)."""
        return [], None

    def fetch_requirements(self, date_from, date_to, unit_ids=None):
        """Fetch staffing requirements. Returns (list, error)."""
        return [], None

    def fetch_activities(self):
        """Fetch activity / state definitions. Returns (list, error)."""
        return [], None
