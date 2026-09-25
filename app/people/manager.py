"""
people/manager.py — Employee roster, accommodations & PTO
==========================================================
Reads employee data from the existing EMPLOYEES sheet tab (populated by
the Capacity module's PeopleWare pull). Stores accommodations and PTO in
their own tabs on the same Google Sheet so data persists across sessions.

All writes go through gspread to the capacity Google Sheet.
"""

import os
import logging
import datetime
from collections import defaultdict

log = logging.getLogger("serevo.people")

# ── Sheet tab names ──────────────────────────────────────────
TAB_EMPLOYEES      = "EMPLOYEES"
TAB_ACCOMMODATIONS = "ACCOMMODATIONS"
TAB_PTO            = "PTO"

ACCOM_HEADERS = [
    "Employee", "Group", "LOB",
    "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun",
    "Shift Start", "Shift End", "Notes", "Updated",
]

PTO_HEADERS = [
    "Employee", "Group", "LOB",
    "Start Date", "End Date", "Type", "Note", "Updated",
]


# ── Sheet access (reuse the same opener as capacity) ─────────
def _open_sheet():
    """Return (gspread.Spreadsheet, error_string)."""
    try:
        from app.data_source import _open_capacity_sheet
        return _open_capacity_sheet()
    except Exception as e:
        return None, str(e)


def _ensure_tab(sheet, tab_name, headers):
    """Get or create a worksheet tab with the given headers."""
    try:
        ws = sheet.worksheet(tab_name)
    except Exception:
        ws = sheet.add_worksheet(tab_name, rows="500", cols=str(len(headers)))
        ws.update("A1", [headers])
    return ws


# ═════════════════════════════════════════════════════════════
# EMPLOYEE ROSTER
# ═════════════════════════════════════════════════════════════

def get_employees(sheet=None):
    """
    Return (list_of_dicts, error).
    Each dict has keys matching the EMPLOYEES tab headers:
      Status, First Name, Last Name, Employee ID,
      Latest Skill Name, Latest Skill Start, Latest Skill End,
      All Skills, End Date
    """
    # Demo mode fallback
    if sheet is None:
        from app.data_source import is_demo_source
        if is_demo_source():
            from app.demo_data import get_demo_employees
            return get_demo_employees()
        sheet, err = _open_sheet()
        if err:
            return [], err

    try:
        ws = sheet.worksheet(TAB_EMPLOYEES)
        records = ws.get_all_records()
        return records, None
    except Exception as e:
        return [], str(e)


def get_employees_by_lob(sheet=None):
    """Return {lob_name: [employee_dicts]} grouped by Latest Skill Name."""
    employees, err = get_employees(sheet)
    if err:
        return {}, err
    grouped = defaultdict(list)
    for emp in employees:
        lob = (emp.get("Latest Skill Name") or "Unassigned").strip()
        grouped[lob].append(emp)
    return dict(grouped), None


def get_active_employees(sheet=None):
    """Return only employees whose Status is 'Active' (or similar)."""
    employees, err = get_employees(sheet)
    if err:
        return [], err
    active = [e for e in employees
              if str(e.get("Status", "")).strip().lower() in ("active", "")]
    return active, None


def get_employee_names(sheet=None):
    """Return sorted list of 'First Last' for active employees."""
    employees, err = get_active_employees(sheet)
    if err:
        return [], err
    names = []
    for e in employees:
        first = str(e.get("First Name", "")).strip()
        last = str(e.get("Last Name", "")).strip()
        if first or last:
            names.append(f"{first} {last}".strip())
    return sorted(set(names)), None


# ═════════════════════════════════════════════════════════════
# ACCOMMODATIONS
# ═════════════════════════════════════════════════════════════

def get_accommodations(sheet=None):
    """Return (list_of_dicts, error) from the ACCOMMODATIONS tab."""
    if sheet is None:
        from app.data_source import is_demo_source
        if is_demo_source():
            from app.demo_data import get_demo_accommodations
            return get_demo_accommodations()
        sheet, err = _open_sheet()
        if err:
            return [], err
    try:
        ws = _ensure_tab(sheet, TAB_ACCOMMODATIONS, ACCOM_HEADERS)
        records = ws.get_all_records()
        return records, None
    except Exception as e:
        return [], str(e)


def save_accommodation(data, sheet=None):
    """
    Add or update an accommodation row.
    data = {Employee, Group, LOB, Mon..Sun, Shift Start, Shift End, Notes}
    If a row with the same Employee already exists, it is updated in place.
    """
    if sheet is None:
        sheet, err = _open_sheet()
        if err:
            return err
    try:
        ws = _ensure_tab(sheet, TAB_ACCOMMODATIONS, ACCOM_HEADERS)
        all_vals = ws.get_all_values()

        emp_name = data.get("Employee", "").strip()
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        row_data = [
            emp_name,
            data.get("Group", ""),
            data.get("LOB", ""),
            data.get("Mon", "fill"),
            data.get("Tue", "fill"),
            data.get("Wed", "fill"),
            data.get("Thu", "fill"),
            data.get("Fri", "fill"),
            data.get("Sat", "fill"),
            data.get("Sun", "fill"),
            data.get("Shift Start", ""),
            data.get("Shift End", ""),
            data.get("Notes", ""),
            now_str,
        ]

        # Find existing row for this employee
        found_row = None
        for i, row in enumerate(all_vals):
            if i == 0:
                continue  # header
            if row and row[0].strip().lower() == emp_name.lower():
                found_row = i + 1  # 1-indexed
                break

        if found_row:
            ws.update(f"A{found_row}", [row_data])
        else:
            ws.append_row(row_data, value_input_option="USER_ENTERED")

        return None
    except Exception as e:
        return str(e)


def delete_accommodation(employee_name, sheet=None):
    """Remove an accommodation row by employee name."""
    if sheet is None:
        sheet, err = _open_sheet()
        if err:
            return err
    try:
        ws = _ensure_tab(sheet, TAB_ACCOMMODATIONS, ACCOM_HEADERS)
        all_vals = ws.get_all_values()
        for i, row in enumerate(all_vals):
            if i == 0:
                continue
            if row and row[0].strip().lower() == employee_name.strip().lower():
                ws.delete_rows(i + 1)
                return None
        return "Employee not found"
    except Exception as e:
        return str(e)


# ═════════════════════════════════════════════════════════════
# PTO / TIME OFF
# ═════════════════════════════════════════════════════════════

def get_pto(sheet=None):
    """Return (list_of_dicts, error) from the PTO tab."""
    if sheet is None:
        from app.data_source import is_demo_source
        if is_demo_source():
            from app.demo_data import get_demo_pto
            return get_demo_pto()
        sheet, err = _open_sheet()
        if err:
            return [], err
    try:
        ws = _ensure_tab(sheet, TAB_PTO, PTO_HEADERS)
        records = ws.get_all_records()
        return records, None
    except Exception as e:
        return [], str(e)


def save_pto(data, sheet=None):
    """
    Add a PTO entry.
    data = {Employee, Group, LOB, Start Date, End Date, Type, Note}
    """
    if sheet is None:
        sheet, err = _open_sheet()
        if err:
            return err
    try:
        ws = _ensure_tab(sheet, TAB_PTO, PTO_HEADERS)
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        row_data = [
            data.get("Employee", "").strip(),
            data.get("Group", ""),
            data.get("LOB", ""),
            data.get("Start Date", ""),
            data.get("End Date", ""),
            data.get("Type", "full"),
            data.get("Note", ""),
            now_str,
        ]
        ws.append_row(row_data, value_input_option="USER_ENTERED")
        return None
    except Exception as e:
        return str(e)


def delete_pto(row_index, sheet=None):
    """Delete a PTO row by its 1-based sheet row index (header = row 1)."""
    if sheet is None:
        sheet, err = _open_sheet()
        if err:
            return err
    try:
        ws = _ensure_tab(sheet, TAB_PTO, PTO_HEADERS)
        ws.delete_rows(row_index)
        return None
    except Exception as e:
        return str(e)


def get_pto_for_employee(employee_name, sheet=None):
    """Return all PTO entries for a specific employee."""
    all_pto, err = get_pto(sheet)
    if err:
        return [], err
    return [p for p in all_pto
            if p.get("Employee", "").strip().lower() == employee_name.strip().lower()], None


def get_pto_dates(employee_name, pto_list=None, sheet=None):
    """
    Return a set of dates an employee is off.
    Used by the scheduling module to check availability.
    """
    if pto_list is None:
        pto_list, err = get_pto(sheet)
        if err:
            return set()

    dates = set()
    for entry in pto_list:
        if entry.get("Employee", "").strip().lower() != employee_name.strip().lower():
            continue
        try:
            start = datetime.datetime.strptime(entry["Start Date"], "%Y-%m-%d").date()
            end = datetime.datetime.strptime(entry["End Date"], "%Y-%m-%d").date()
            d = start
            while d <= end:
                dates.add(d)
                d += datetime.timedelta(days=1)
        except Exception:
            continue
    return dates


def get_availability_map(sheet=None):
    """
    Build a combined availability view for the scheduling module.
    Returns {employee_name: {pto_dates: set, accommodations: dict}}.
    """
    accoms, _ = get_accommodations(sheet)
    pto_list, _ = get_pto(sheet)

    accom_map = {}
    for a in accoms:
        name = a.get("Employee", "").strip()
        if name:
            accom_map[name] = a

    pto_map = defaultdict(set)
    for entry in pto_list:
        name = entry.get("Employee", "").strip()
        if not name:
            continue
        try:
            start = datetime.datetime.strptime(entry["Start Date"], "%Y-%m-%d").date()
            end = datetime.datetime.strptime(entry["End Date"], "%Y-%m-%d").date()
            d = start
            while d <= end:
                pto_map[name].add(d)
                d += datetime.timedelta(days=1)
        except Exception:
            continue

    # Merge into one map
    all_names = set(accom_map.keys()) | set(pto_map.keys())
    result = {}
    for name in all_names:
        result[name] = {
            "pto_dates": pto_map.get(name, set()),
            "accommodations": accom_map.get(name, {}),
        }
    return result
