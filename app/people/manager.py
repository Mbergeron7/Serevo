"""
people/manager.py — Employee roster, accommodations & PTO
==========================================================
Reads employee data from Google Sheets when configured, otherwise falls back
to the PostgreSQL database. All writes go to whichever backend is active.
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


# ── Helpers ──────────────────────────────────────────────────

def _using_db():
    """Return True if the active data source is the PostgreSQL backend."""
    from app.data_source import get_source, PostgresSource
    return isinstance(get_source(), PostgresSource)


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

        # Always prefer DB when it has employees (manual edits go there)
        db_result, db_err = _get_employees_from_db()
        if not db_err and db_result:
            return db_result, None

        # Database-only mode
        if _using_db():
            return db_result, db_err

        sheet, err = _open_sheet()
        if err:
            return _get_employees_from_db()

    try:
        ws = sheet.worksheet(TAB_EMPLOYEES)
        records = ws.get_all_records()
        return records, None
    except Exception as e:
        return [], str(e)


def _get_employees_from_db():
    """Read employees from the PostgreSQL database."""
    try:
        from app.models import Employee
        emps = Employee.query.order_by(Employee.last_name, Employee.first_name).all()
        if emps:
            # One-time back-fill: if some employees lack a planning_unit_id,
            # try to repair from the Google Sheet's "Latest Skill Name" column.
            missing = [e for e in emps if e.planning_unit_id is None]
            if missing:
                try:
                    _backfill_planning_units(missing)
                    # Re-query after possible updates
                    emps = Employee.query.order_by(Employee.last_name, Employee.first_name).all()
                except Exception as bf_err:
                    log.warning(f"Backfill planning units failed (non-fatal): {bf_err}")
        return [e.to_legacy_dict() for e in emps], None
    except Exception as e:
        return [], str(e)


_backfill_ran = False          # track whether backfill succeeded

def _backfill_planning_units(employees_missing_pu):
    """Try to set planning_unit_id from Google Sheet data."""
    global _backfill_ran
    if _backfill_ran:
        return
    try:
        sheet, err = _open_sheet()
        if err or not sheet:
            # Don't set _backfill_ran so it retries next request
            return
        ws = sheet.worksheet(TAB_EMPLOYEES)
        records = ws.get_all_records()
        # Build lookup: employee_id → Latest Skill Name
        # Use str() on both sides to handle int vs string mismatch
        sheet_lobs = {}
        for rec in records:
            eid = str(rec.get("Employee ID", "")).strip()
            lob = (rec.get("Latest Skill Name") or "").strip()
            if eid and lob:
                sheet_lobs[eid] = lob
        if not sheet_lobs:
            import logging
            logging.getLogger(__name__).warning(
                f"Back-fill: sheet returned {len(records)} records but none had Employee ID + Latest Skill Name")
            return
        from app.models import db, PlanningUnit
        pu_cache = {}
        updated = 0
        unmatched = []
        for emp in employees_missing_pu:
            # Try matching with str() to handle type differences
            lob_name = sheet_lobs.get(str(emp.employee_id).strip())
            if not lob_name:
                unmatched.append(emp.employee_id)
                continue
            if lob_name not in pu_cache:
                pu = PlanningUnit.query.filter(db.func.lower(PlanningUnit.name) == lob_name.lower()).first()
                if not pu:
                    pu = PlanningUnit(name=lob_name)
                    db.session.add(pu)
                    db.session.flush()
                pu_cache[lob_name] = pu
            emp.planning_unit_id = pu_cache[lob_name].id
            updated += 1
        if updated:
            db.session.commit()
            _backfill_ran = True  # only mark done on success
            import logging
            logging.getLogger(__name__).info(
                f"Back-filled planning_unit_id for {updated} employees from sheet"
                + (f" ({len(unmatched)} unmatched)" if unmatched else ""))
        elif unmatched:
            import logging
            logging.getLogger(__name__).warning(
                f"Back-fill: 0 matched, {len(unmatched)} unmatched. "
                f"DB IDs sample: {unmatched[:5]}, Sheet IDs sample: {list(sheet_lobs.keys())[:5]}")
            _backfill_ran = True  # IDs genuinely don't match; stop retrying
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Planning unit back-fill error: {e}")
        try:
            from app.models import db
            db.session.rollback()
        except Exception:
            pass


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
    """Return (list_of_dicts, error) from the ACCOMMODATIONS tab or DB."""
    if sheet is None:
        from app.data_source import is_demo_source
        if is_demo_source():
            from app.demo_data import get_demo_accommodations
            return get_demo_accommodations()

        if _using_db():
            return _get_accommodations_from_db()

        sheet, err = _open_sheet()
        if err:
            return _get_accommodations_from_db()

    try:
        ws = _ensure_tab(sheet, TAB_ACCOMMODATIONS, ACCOM_HEADERS)
        records = ws.get_all_records()
        return records, None
    except Exception as e:
        return [], str(e)


def _get_accommodations_from_db():
    """Read accommodations from the PostgreSQL database."""
    try:
        from app.models import Accommodation
        accoms = Accommodation.query.all()
        return [a.to_legacy_dict() for a in accoms], None
    except Exception as e:
        return [], str(e)


def save_accommodation(data, sheet=None):
    """
    Add or update an accommodation row.
    data = {Employee, Group, LOB, Mon..Sun, Shift Start, Shift End, Notes}
    """
    if sheet is None:
        from app.data_source import is_demo_source
        if is_demo_source():
            return _save_accommodation_to_db(data)

        if _using_db():
            return _save_accommodation_to_db(data)

        sheet, err = _open_sheet()
        if err:
            return _save_accommodation_to_db(data)

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


def _save_accommodation_to_db(data):
    """Save accommodation to the PostgreSQL database."""
    try:
        from app.models import db, Accommodation, Employee
        emp_name = data.get("Employee", "").strip()
        if not emp_name:
            return "Employee name is required"

        # Find the employee by name
        parts = emp_name.split(None, 1)
        first = parts[0] if parts else ""
        last = parts[1] if len(parts) > 1 else ""
        emp = Employee.query.filter_by(first_name=first, last_name=last).first()
        if not emp:
            # Try reverse (Last First)
            emp = Employee.query.filter_by(first_name=last, last_name=first).first()
        if not emp:
            return f"Employee '{emp_name}' not found in database"

        # Find existing accommodation or create new
        accom = Accommodation.query.filter_by(employee_id=emp.id).first()
        if not accom:
            accom = Accommodation(employee_id=emp.id)
            db.session.add(accom)

        accom.mon = data.get("Mon", "fill")
        accom.tue = data.get("Tue", "fill")
        accom.wed = data.get("Wed", "fill")
        accom.thu = data.get("Thu", "fill")
        accom.fri = data.get("Fri", "fill")
        accom.sat = data.get("Sat", "fill")
        accom.sun = data.get("Sun", "fill")
        accom.shift_start = data.get("Shift Start", "")
        accom.shift_end = data.get("Shift End", "")
        accom.notes = data.get("Notes", "")

        db.session.commit()
        return None
    except Exception as e:
        from app.models import db
        db.session.rollback()
        return str(e)


def delete_accommodation(employee_name, sheet=None):
    """Remove an accommodation row by employee name."""
    if sheet is None:
        if _using_db():
            return _delete_accommodation_from_db(employee_name)
        sheet, err = _open_sheet()
        if err:
            return _delete_accommodation_from_db(employee_name)

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


def _delete_accommodation_from_db(employee_name):
    """Delete accommodation from the PostgreSQL database."""
    try:
        from app.models import db, Accommodation, Employee
        emp_name = employee_name.strip()
        parts = emp_name.split(None, 1)
        first = parts[0] if parts else ""
        last = parts[1] if len(parts) > 1 else ""
        emp = Employee.query.filter_by(first_name=first, last_name=last).first()
        if not emp:
            emp = Employee.query.filter_by(first_name=last, last_name=first).first()
        if not emp:
            return "Employee not found"
        accom = Accommodation.query.filter_by(employee_id=emp.id).first()
        if not accom:
            return "No accommodation found"
        db.session.delete(accom)
        db.session.commit()
        return None
    except Exception as e:
        from app.models import db
        db.session.rollback()
        return str(e)


# ═════════════════════════════════════════════════════════════
# PTO / TIME OFF
# ═════════════════════════════════════════════════════════════

def get_pto(sheet=None):
    """Return (list_of_dicts, error) from the PTO tab or DB."""
    if sheet is None:
        from app.data_source import is_demo_source
        if is_demo_source():
            from app.demo_data import get_demo_pto
            return get_demo_pto()

        if _using_db():
            return _get_pto_from_db()

        sheet, err = _open_sheet()
        if err:
            return _get_pto_from_db()

    try:
        ws = _ensure_tab(sheet, TAB_PTO, PTO_HEADERS)
        records = ws.get_all_records()
        return records, None
    except Exception as e:
        return [], str(e)


def _get_pto_from_db():
    """Read PTO entries from the PostgreSQL database."""
    try:
        from app.models import PTOEntry
        entries = PTOEntry.query.order_by(PTOEntry.start_date).all()
        return [p.to_legacy_dict() for p in entries], None
    except Exception as e:
        return [], str(e)


def save_pto(data, sheet=None):
    """
    Add a PTO entry.
    data = {Employee, Group, LOB, Start Date, End Date, Type, Note}
    """
    if sheet is None:
        from app.data_source import is_demo_source
        if is_demo_source():
            return _save_pto_to_db(data)

        if _using_db():
            return _save_pto_to_db(data)

        sheet, err = _open_sheet()
        if err:
            return _save_pto_to_db(data)

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


def _save_pto_to_db(data):
    """Save PTO entry to the PostgreSQL database."""
    try:
        from app.models import db, PTOEntry, Employee
        emp_name = data.get("Employee", "").strip()
        if not emp_name:
            return "Employee name is required"

        parts = emp_name.split(None, 1)
        first = parts[0] if parts else ""
        last = parts[1] if len(parts) > 1 else ""
        emp = Employee.query.filter_by(first_name=first, last_name=last).first()
        if not emp:
            emp = Employee.query.filter_by(first_name=last, last_name=first).first()
        if not emp:
            return f"Employee '{emp_name}' not found in database"

        start_str = data.get("Start Date", "")
        end_str = data.get("End Date", "")
        if not start_str or not end_str:
            return "Start and End dates are required"

        entry = PTOEntry(
            employee_id=emp.id,
            start_date=datetime.datetime.strptime(start_str[:10], "%Y-%m-%d").date(),
            end_date=datetime.datetime.strptime(end_str[:10], "%Y-%m-%d").date(),
            pto_type=data.get("Type", "full"),
            note=data.get("Note", ""),
        )
        db.session.add(entry)
        db.session.commit()
        return None
    except Exception as e:
        from app.models import db
        db.session.rollback()
        return str(e)


def delete_pto(row_index, sheet=None):
    """Delete a PTO row by its 1-based sheet row index, or DB id."""
    if sheet is None:
        if _using_db():
            return _delete_pto_from_db(row_index)
        sheet, err = _open_sheet()
        if err:
            return _delete_pto_from_db(row_index)

    try:
        ws = _ensure_tab(sheet, TAB_PTO, PTO_HEADERS)
        ws.delete_rows(row_index)
        return None
    except Exception as e:
        return str(e)


def _delete_pto_from_db(entry_id):
    """Delete a PTO entry from the PostgreSQL database by ID."""
    try:
        from app.models import db, PTOEntry
        entry = PTOEntry.query.get(int(entry_id))
        if not entry:
            return "PTO entry not found"
        db.session.delete(entry)
        db.session.commit()
        return None
    except Exception as e:
        from app.models import db
        db.session.rollback()
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
