"""
Serevo — SQLAlchemy models
============================
All database tables for the production platform.
"""

from datetime import datetime, date, time
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


# ═══════════════════════════════════════════════════════════════
# USERS & AUTH
# ═══════════════════════════════════════════════════════════════

class User(db.Model):
    __tablename__ = "users"

    id            = db.Column(db.Integer, primary_key=True)
    email         = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=True)  # nullable for demo accounts
    display_name  = db.Column(db.String(120), nullable=False, default="")
    role          = db.Column(db.String(20), nullable=False, default="viewer")  # viewer | admin
    is_demo       = db.Column(db.Boolean, default=False)
    is_active     = db.Column(db.Boolean, default=True)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at    = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<User {self.email}>"


# ═══════════════════════════════════════════════════════════════
# PLANNING UNITS (LOBs / workloads)
# ═══════════════════════════════════════════════════════════════

class PlanningUnit(db.Model):
    __tablename__ = "planning_units"

    id         = db.Column(db.Integer, primary_key=True)
    name       = db.Column(db.String(120), unique=True, nullable=False)
    is_active  = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    employees    = db.relationship("Employee", backref="planning_unit", lazy="dynamic")
    requirements = db.relationship("RequirementInterval", backref="planning_unit", lazy="dynamic")
    forecasts    = db.relationship("ForecastInterval", backref="planning_unit", lazy="dynamic")

    def __repr__(self):
        return f"<PlanningUnit {self.name}>"


# ═══════════════════════════════════════════════════════════════
# EMPLOYEES
# ═══════════════════════════════════════════════════════════════

class Employee(db.Model):
    __tablename__ = "employees"

    id               = db.Column(db.Integer, primary_key=True)
    employee_id      = db.Column(db.String(30), unique=True, nullable=False, index=True)
    first_name       = db.Column(db.String(80), nullable=False)
    last_name        = db.Column(db.String(80), nullable=False)
    status           = db.Column(db.String(20), nullable=False, default="Active")
    planning_unit_id = db.Column(db.Integer, db.ForeignKey("planning_units.id"), nullable=True)
    all_skills       = db.Column(db.Text, default="")
    skill_start      = db.Column(db.Date, nullable=True)
    skill_end        = db.Column(db.Date, nullable=True)
    end_date         = db.Column(db.Date, nullable=True)
    languages        = db.Column(db.String(100), default="English")
    contract_type    = db.Column(db.String(20), default="Full-Time")    # Full-Time | Part-Time
    weekly_hours     = db.Column(db.Float, default=42.5)
    days_per_week    = db.Column(db.Integer, default=5)
    hours_per_day    = db.Column(db.Float, default=8.5)
    timezone         = db.Column(db.String(60), default="America/New_York")
    team_lead        = db.Column(db.String(100), default="")
    schedule_excluded = db.Column(db.Boolean, default=False, server_default="false")
    manually_edited  = db.Column(db.Boolean, default=False, server_default="false")
    created_at       = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at       = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    accommodations = db.relationship("Accommodation", backref="employee", lazy="dynamic")
    pto_entries    = db.relationship("PTOEntry", backref="employee", lazy="dynamic")

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip()

    def to_legacy_dict(self):
        """Return a dict matching the flat format existing templates expect."""
        pu = self.planning_unit
        return {
            "_db_id": self.id,
            "Status": self.status,
            "First Name": self.first_name,
            "Last Name": self.last_name,
            "Employee ID": self.employee_id,
            "Latest Skill Name": pu.name if pu else "",
            "Latest Skill Start": self.skill_start.isoformat() if self.skill_start else "",
            "Latest Skill End": self.skill_end.isoformat() if self.skill_end else "",
            "All Skills": self.all_skills or "",
            "Languages": self.languages or "English",
            "Contract Type": self.contract_type or "Full-Time",
            "Weekly Hours": self.weekly_hours or 40.0,
            "Days Per Week": self.days_per_week or 5,
            "Hours Per Day": self.hours_per_day or 8.0,
            "Timezone": self.timezone or "America/New_York",
            "Schedule Excluded": self.schedule_excluded or False,
            "End Date": self.end_date.isoformat() if self.end_date else "",
            "Team Lead": self.team_lead or "",
        }

    def __repr__(self):
        return f"<Employee {self.employee_id} {self.full_name}>"


# ═══════════════════════════════════════════════════════════════
# ACCOMMODATIONS
# ═══════════════════════════════════════════════════════════════

class Accommodation(db.Model):
    __tablename__ = "accommodations"

    id          = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=False, index=True)
    mon         = db.Column(db.String(10), default="fill")
    tue         = db.Column(db.String(10), default="fill")
    wed         = db.Column(db.String(10), default="fill")
    thu         = db.Column(db.String(10), default="fill")
    fri         = db.Column(db.String(10), default="fill")
    sat         = db.Column(db.String(10), default="off")
    sun         = db.Column(db.String(10), default="off")
    shift_start = db.Column(db.String(10), default="")
    shift_end   = db.Column(db.String(10), default="")
    notes       = db.Column(db.Text, default="")
    updated_at  = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_legacy_dict(self):
        emp = self.employee
        pu = emp.planning_unit if emp else None
        return {
            "Employee": emp.full_name if emp else "",
            "Group": (pu.name if pu else "").split()[0] if pu else "",
            "LOB": pu.name if pu else "",
            "Mon": self.mon, "Tue": self.tue, "Wed": self.wed,
            "Thu": self.thu, "Fri": self.fri, "Sat": self.sat, "Sun": self.sun,
            "Shift Start": self.shift_start,
            "Shift End": self.shift_end,
            "Notes": self.notes,
            "Updated": self.updated_at.strftime("%Y-%m-%d %H:%M") if self.updated_at else "",
        }


# ═══════════════════════════════════════════════════════════════
# PTO / TIME OFF
# ═══════════════════════════════════════════════════════════════

class PTOEntry(db.Model):
    __tablename__ = "pto_entries"

    id          = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=False, index=True)
    start_date  = db.Column(db.Date, nullable=False)
    end_date    = db.Column(db.Date, nullable=False)
    pto_type    = db.Column(db.String(20), default="full")   # full | partial
    note        = db.Column(db.Text, default="")
    updated_at  = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_legacy_dict(self):
        emp = self.employee
        pu = emp.planning_unit if emp else None
        return {
            "Employee": emp.full_name if emp else "",
            "Group": (pu.name if pu else "").split()[0] if pu else "",
            "LOB": pu.name if pu else "",
            "Start Date": self.start_date.isoformat(),
            "End Date": self.end_date.isoformat(),
            "Type": self.pto_type,
            "Note": self.note,
            "Updated": self.updated_at.strftime("%Y-%m-%d %H:%M") if self.updated_at else "",
        }


# ═══════════════════════════════════════════════════════════════
# FORECAST & REQUIREMENTS (interval-level data)
# ═══════════════════════════════════════════════════════════════

class ForecastInterval(db.Model):
    __tablename__ = "forecast_intervals"

    id               = db.Column(db.Integer, primary_key=True)
    planning_unit_id = db.Column(db.Integer, db.ForeignKey("planning_units.id"), nullable=False, index=True)
    timestamp        = db.Column(db.DateTime, nullable=False, index=True)
    offered          = db.Column(db.Float, default=0)
    aht              = db.Column(db.Float, default=0)
    source           = db.Column(db.String(30), default="upload")  # upload | api | manual
    uploaded_at      = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.Index("ix_forecast_unit_ts", "planning_unit_id", "timestamp"),
    )

    def to_legacy_dict(self):
        return {
            "time": self.timestamp.strftime("%Y-%m-%d %H:%M"),
            "offered": self.offered,
            "aht": self.aht,
        }


class RequirementInterval(db.Model):
    __tablename__ = "requirement_intervals"

    id               = db.Column(db.Integer, primary_key=True)
    planning_unit_id = db.Column(db.Integer, db.ForeignKey("planning_units.id"), nullable=False, index=True)
    timestamp        = db.Column(db.DateTime, nullable=False, index=True)
    agents_required  = db.Column(db.Float, default=0)
    source           = db.Column(db.String(30), default="upload")
    uploaded_at      = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.Index("ix_req_unit_ts", "planning_unit_id", "timestamp"),
    )

    def to_legacy_dict(self):
        return {
            "time": self.timestamp.strftime("%Y-%m-%d %H:%M"),
            "agents_required": self.agents_required,
        }


# ═══════════════════════════════════════════════════════════════
# SCHEDULES
# ═══════════════════════════════════════════════════════════════

class Schedule(db.Model):
    __tablename__ = "schedules"

    id               = db.Column(db.Integer, primary_key=True)
    employee_id      = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=False, index=True)
    planning_unit_id = db.Column(db.Integer, db.ForeignKey("planning_units.id"), nullable=True)
    schedule_date    = db.Column(db.Date, nullable=False)
    shift_start      = db.Column(db.Time, nullable=True)
    shift_end        = db.Column(db.Time, nullable=True)
    shift_type       = db.Column(db.String(10), default="full")      # full | half
    hours            = db.Column(db.Float, default=0)
    status           = db.Column(db.String(20), default="scheduled")  # scheduled | off | pto
    created_at       = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at       = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    employee = db.relationship("Employee", backref="schedules")
    segments = db.relationship("ShiftSegment", backref="schedule",
                               cascade="all, delete-orphan",
                               order_by="ShiftSegment.sort_order",
                               lazy="joined")

    __table_args__ = (
        db.Index("ix_sched_date_unit", "schedule_date", "planning_unit_id"),
    )

    def to_dict(self):
        emp = self.employee
        return {
            "id": self.id,
            "employee": emp.full_name if emp else "",
            "employee_id": emp.employee_id if emp else "",
            "date": self.schedule_date.isoformat() if self.schedule_date else "",
            "start": self.shift_start.strftime("%H:%M") if self.shift_start else "",
            "end": self.shift_end.strftime("%H:%M") if self.shift_end else "",
            "type": self.shift_type or "full",
            "hours": self.hours or 0,
            "status": self.status or "scheduled",
            "segments": [seg.to_dict() for seg in self.segments],
        }


class ShiftSegment(db.Model):
    __tablename__ = "shift_segments"

    id            = db.Column(db.Integer, primary_key=True)
    schedule_id   = db.Column(db.Integer, db.ForeignKey("schedules.id", ondelete="CASCADE"),
                              nullable=False, index=True)
    activity_type = db.Column(db.String(20), nullable=False, default="on-call")
    # on-call | break | lunch | meeting | training | other
    start_time    = db.Column(db.Time, nullable=False)
    end_time      = db.Column(db.Time, nullable=False)
    duration_mins = db.Column(db.Integer, default=0)
    sort_order    = db.Column(db.Integer, default=0)
    notes         = db.Column(db.String(255), default="")
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.Index("ix_seg_schedule", "schedule_id", "sort_order"),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "type": self.activity_type,
            "start": self.start_time.strftime("%H:%M") if self.start_time else "",
            "end": self.end_time.strftime("%H:%M") if self.end_time else "",
            "duration_mins": self.duration_mins,
            "sort_order": self.sort_order,
            "notes": self.notes or "",
        }


# ═══════════════════════════════════════════════════════════════
# API CONNECTIONS (Phase 1.3)
# ═══════════════════════════════════════════════════════════════

class APIConnection(db.Model):
    __tablename__ = "api_connections"

    id            = db.Column(db.Integer, primary_key=True)
    name          = db.Column(db.String(120), nullable=False)
    provider      = db.Column(db.String(60), nullable=False)   # e.g. "generic", "nice", "genesys", "five9"
    base_url      = db.Column(db.String(500), default="")
    auth_type     = db.Column(db.String(30), default="bearer") # bearer | basic | api_key | oauth2
    credentials   = db.Column(db.Text, default="")             # encrypted JSON blob
    is_active     = db.Column(db.Boolean, default=True)
    last_tested   = db.Column(db.DateTime, nullable=True)
    last_status   = db.Column(db.String(20), default="untested") # untested | ok | error
    last_error    = db.Column(db.Text, default="")
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at    = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<APIConnection {self.name} ({self.provider})>"


# ═══════════════════════════════════════════════════════════════
# DATA UPLOADS (track CSV/Excel import history)
# ═══════════════════════════════════════════════════════════════

class DataUpload(db.Model):
    __tablename__ = "data_uploads"

    id           = db.Column(db.Integer, primary_key=True)
    filename     = db.Column(db.String(255), nullable=False)
    upload_type  = db.Column(db.String(30), nullable=False)  # employees | forecast | requirements | pto | accommodations
    rows_total   = db.Column(db.Integer, default=0)
    rows_imported = db.Column(db.Integer, default=0)
    rows_skipped = db.Column(db.Integer, default=0)
    status       = db.Column(db.String(20), default="pending")  # pending | processing | complete | error
    error_detail = db.Column(db.Text, default="")
    uploaded_by  = db.Column(db.String(255), default="")
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<DataUpload {self.filename} ({self.upload_type})>"


# ═══════════════════════════════════════════════════════════════
# DATA SOURCES (multi-source historical data ingestion)
# ═══════════════════════════════════════════════════════════════

class DataSource(db.Model):
    """A configured source of historical interval data.
    Supports Google Sheets, API connections, and CSV uploads.
    Each source maps to a tab/endpoint/file that feeds IntervalActual."""
    __tablename__ = "data_sources"

    id                = db.Column(db.Integer, primary_key=True)
    name              = db.Column(db.String(200), nullable=False)
    source_type       = db.Column(db.String(50), nullable=False)  # google_sheet | api | csv_upload

    # ── Google Sheets config ──
    sheet_key         = db.Column(db.String(200), default="")
    tab_name          = db.Column(db.String(100), default="")
    service_account_json = db.Column(db.Text, default="")  # JSON creds (encrypted at rest)

    # ── API config ──
    api_connection_id = db.Column(db.Integer, db.ForeignKey("api_connections.id"), nullable=True)
    api_endpoint      = db.Column(db.String(500), default="")  # e.g. /workloads/{lob}/actuals

    # ── Column mapping: source column names → our fields ──
    # JSON: {"timestamp": "Date", "offered": "Calls Offered", "aht": "AHT", "lob": "LOB", ...}
    column_mapping    = db.Column(db.JSON, default=dict)

    # ── LOB filter: which planning units this source feeds ──
    # If empty, auto-creates planning units from the LOB column
    planning_unit_ids = db.Column(db.JSON, default=list)  # [1, 2, 3] or []

    # ── Sync status ──
    is_active         = db.Column(db.Boolean, default=True)
    last_sync         = db.Column(db.DateTime, nullable=True)
    last_status       = db.Column(db.String(50), default="never_synced")  # never_synced | ok | error
    last_error        = db.Column(db.Text, default="")
    rows_synced       = db.Column(db.Integer, default=0)
    sync_interval_hours = db.Column(db.Integer, default=24)

    created_at        = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at        = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    api_connection    = db.relationship("APIConnection", backref="data_sources")

    def __repr__(self):
        return f"<DataSource {self.name} ({self.source_type})>"


# ═══════════════════════════════════════════════════════════════
# APP SETTINGS (key-value store for runtime configuration)
# ═══════════════════════════════════════════════════════════════

class AppSetting(db.Model):
    __tablename__ = "app_settings"

    key   = db.Column(db.String(120), primary_key=True)
    value = db.Column(db.Text, default="")

    @staticmethod
    def get(key, default=""):
        """Get a setting value by key."""
        row = AppSetting.query.get(key)
        return row.value if row else default

    @staticmethod
    def set(key, value):
        """Set a setting value (upsert)."""
        row = AppSetting.query.get(key)
        if row:
            row.value = value
        else:
            row = AppSetting(key=key, value=value)
            db.session.add(row)
        db.session.flush()

    def __repr__(self):
        return f"<AppSetting {self.key}>"


# ═══════════════════════════════════════════════════════════════
# CUSTOMIZATION — Admin-configurable settings per client
# ═══════════════════════════════════════════════════════════════

class SegmentCode(db.Model):
    """Activity/segment codes used in scheduling (e.g. on-call, break, lunch).
    Clients can rename, recolor, and add their own codes."""
    __tablename__ = "segment_codes"

    id           = db.Column(db.Integer, primary_key=True)
    code         = db.Column(db.String(40), unique=True, nullable=False)  # internal key
    label        = db.Column(db.String(80), nullable=False)               # display name
    color        = db.Column(db.String(20), default="#6b7280")            # hex color for UI
    is_productive = db.Column(db.Boolean, default=True)                   # counts toward productive time
    is_paid      = db.Column(db.Boolean, default=True)                    # counts as paid time
    is_default   = db.Column(db.Boolean, default=False)                   # system default (can't be deleted)
    sort_order   = db.Column(db.Integer, default=0)
    is_active    = db.Column(db.Boolean, default=True)
    # ── Placement rules ──────────────────────────────────────
    offset_mins       = db.Column(db.Integer, nullable=True)   # default offset from shift start (e.g. 120 = 2hrs in)
    duration_mins     = db.Column(db.Integer, nullable=True)    # segment length (e.g. 15 for break, 30 for lunch)
    is_flexible       = db.Column(db.Boolean, default=False)    # True = stagger within window; False = fixed placement
    window_start_mins = db.Column(db.Integer, nullable=True)    # earliest offset from shift start (flexible only)
    window_end_mins   = db.Column(db.Integer, nullable=True)    # latest offset from shift start (flexible only)
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id, "code": self.code, "label": self.label,
            "color": self.color, "is_productive": self.is_productive,
            "is_paid": self.is_paid, "is_default": self.is_default,
            "sort_order": self.sort_order, "is_active": self.is_active,
            "offset_mins": self.offset_mins,
            "duration_mins": self.duration_mins,
            "is_flexible": self.is_flexible,
            "window_start_mins": self.window_start_mins,
            "window_end_mins": self.window_end_mins,
        }


class ShiftTemplate(db.Model):
    """Reusable shift definitions — start/end, break placement, segment structure.

    Enhanced fields:
      - planning_unit_id: optional FK linking the shift to a specific LOB
      - day_type: weekday | saturday | sunday | holiday | any
      - shift_category: opening | closing | mid | any
    These let you define e.g. "SS TL Weekday Closing 13:30-22:00" vs
    "PS TL Saturday Opening 9:00-17:30" as separate templates.
    """
    __tablename__ = "shift_templates"

    id          = db.Column(db.Integer, primary_key=True)
    name        = db.Column(db.String(80), nullable=False)           # e.g. "SS TL Weekday Closing"
    start_time  = db.Column(db.String(5), nullable=False)            # "13:30"
    end_time    = db.Column(db.String(5), nullable=False)            # "22:00"
    hours       = db.Column(db.Float, default=8.0)
    shift_type  = db.Column(db.String(20), default="full")           # full | half | split
    segments_json = db.Column(db.Text, default="[]")                 # JSON array of segment defs
    # ── New classification fields ──
    planning_unit_id = db.Column(db.Integer, db.ForeignKey("planning_units.id"),
                                 nullable=True, index=True)          # NULL = applies to all LOBs
    day_type     = db.Column(db.String(20), default="any")           # weekday | saturday | sunday | holiday | any
    shift_category = db.Column(db.String(20), default="any")         # opening | closing | mid | any
    is_active   = db.Column(db.Boolean, default=True)
    sort_order  = db.Column(db.Integer, default=0)
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)

    planning_unit = db.relationship("PlanningUnit", backref="shift_templates", foreign_keys=[planning_unit_id])

    def to_dict(self):
        import json
        pu = self.planning_unit
        return {
            "id": self.id, "name": self.name,
            "start_time": self.start_time, "end_time": self.end_time,
            "hours": self.hours, "shift_type": self.shift_type,
            "segments": json.loads(self.segments_json) if self.segments_json else [],
            "planning_unit_id": self.planning_unit_id or "",
            "lob_name": pu.name if pu else "All",
            "day_type": self.day_type or "any",
            "shift_category": self.shift_category or "any",
            "is_active": self.is_active, "sort_order": self.sort_order,
        }


class RotationPattern(db.Model):
    """Multi-week rotation patterns — e.g. Week 1: days, Week 2: closing, Week 3: weekend."""
    __tablename__ = "rotation_patterns"

    id          = db.Column(db.Integer, primary_key=True)
    name        = db.Column(db.String(80), nullable=False)           # e.g. "3-Week Day/Close/Weekend"
    weeks_json  = db.Column(db.Text, nullable=False, default="[]")   # JSON array of week definitions
    # Each week: {label, shifts: {mon: template_id|null, tue: ..., sun: ...}}
    cycle_weeks = db.Column(db.Integer, default=1)                   # total weeks in cycle
    is_active   = db.Column(db.Boolean, default=True)
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)

    assignments = db.relationship("RotationAssignment", backref="pattern",
                                  cascade="all, delete-orphan", lazy="joined")

    def to_dict(self):
        import json
        return {
            "id": self.id, "name": self.name,
            "weeks": json.loads(self.weeks_json) if self.weeks_json else [],
            "cycle_weeks": self.cycle_weeks, "is_active": self.is_active,
            "assignments": [a.to_dict() for a in self.assignments],
        }


class RotationAssignment(db.Model):
    """Links an employee to a rotation pattern with their current week offset."""
    __tablename__ = "rotation_assignments"

    id           = db.Column(db.Integer, primary_key=True)
    rotation_id  = db.Column(db.Integer, db.ForeignKey("rotation_patterns.id", ondelete="CASCADE"),
                             nullable=False, index=True)
    employee_id  = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=False, index=True)
    current_week = db.Column(db.Integer, default=0)       # 0-indexed week in the cycle
    start_date   = db.Column(db.Date, nullable=True)      # when this assignment starts
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)

    employee = db.relationship("Employee", backref="rotation_assignments")

    __table_args__ = (
        db.UniqueConstraint("rotation_id", "employee_id", name="uq_rotation_employee"),
    )

    def to_dict(self):
        emp = self.employee
        return {
            "id": self.id, "rotation_id": self.rotation_id,
            "employee_id": emp.employee_id if emp else "",
            "employee_name": emp.full_name if emp else "",
            "current_week": self.current_week,
            "start_date": self.start_date.isoformat() if self.start_date else "",
        }


class FillInRule(db.Model):
    """Defines backup/fill-in employees for a shift category when the primary is unavailable.

    When the rotation-assigned employee for a shift category (e.g. closing) is on PTO
    or otherwise absent, the system picks the highest-priority eligible fill-in.
    """
    __tablename__ = "fill_in_rules"

    id               = db.Column(db.Integer, primary_key=True)
    shift_category   = db.Column(db.String(30), nullable=False)    # closing | opening | mid | weekend
    employee_id      = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=False, index=True)
    priority         = db.Column(db.Integer, default=0)            # lower = higher priority
    planning_unit_id = db.Column(db.Integer, db.ForeignKey("planning_units.id"), nullable=True)
    fallback_template_id = db.Column(db.Integer, db.ForeignKey("shift_templates.id"), nullable=True)
    is_active        = db.Column(db.Boolean, default=True)
    created_at       = db.Column(db.DateTime, default=datetime.utcnow)

    employee = db.relationship("Employee", backref="fill_in_rules")
    fallback_template = db.relationship("ShiftTemplate", foreign_keys=[fallback_template_id])

    __table_args__ = (
        db.UniqueConstraint("shift_category", "employee_id", name="uq_fillin_cat_employee"),
    )

    def to_dict(self):
        emp = self.employee
        tmpl = self.fallback_template
        return {
            "id": self.id,
            "shift_category": self.shift_category,
            "employee_id": emp.id if emp else None,
            "employee_name": emp.full_name if emp else "",
            "employee_ext_id": emp.employee_id if emp else "",
            "priority": self.priority,
            "planning_unit_id": self.planning_unit_id,
            "fallback_template_id": self.fallback_template_id,
            "fallback_template_name": tmpl.name if tmpl else "",
            "is_active": self.is_active,
        }


class LOBSetting(db.Model):
    """Per-LOB operational defaults — SLA targets, shrinkage, interval length, etc.

    Operating hours can differ by day type:
      - Weekday (Mon–Fri)
      - Saturday
      - Sunday
    The original operating_start/end columns are kept as the weekday default.
    """
    __tablename__ = "lob_settings"

    id                    = db.Column(db.Integer, primary_key=True)
    planning_unit_id      = db.Column(db.Integer, db.ForeignKey("planning_units.id"),
                                      unique=True, nullable=False, index=True)
    service_level_target  = db.Column(db.Float, default=0.80)        # 80%
    target_asa            = db.Column(db.Float, default=30)           # 30 seconds
    interval_minutes      = db.Column(db.Integer, default=30)         # 15 or 30
    shrinkage_pct         = db.Column(db.Float, default=0.30)         # 30%
    occupancy_target      = db.Column(db.Float, default=0.85)         # 85%
    max_occupancy         = db.Column(db.Float, default=0.92)         # 92%
    default_shift_hrs     = db.Column(db.Float, default=8.0)
    # Weekday operating hours (Mon–Fri)
    operating_start       = db.Column(db.String(5), default="08:00")
    operating_end         = db.Column(db.String(5), default="22:00")
    # Saturday operating hours (NULL = same as weekday)
    sat_operating_start   = db.Column(db.String(5), nullable=True)
    sat_operating_end     = db.Column(db.String(5), nullable=True)
    # Sunday operating hours (NULL = same as weekday)
    sun_operating_start   = db.Column(db.String(5), nullable=True)
    sun_operating_end     = db.Column(db.String(5), nullable=True)
    updated_at            = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    planning_unit = db.relationship("PlanningUnit", backref=db.backref("lob_setting", uselist=False))

    def get_hours_for_day(self, day_of_week):
        """Return (start, end) for a given day (0=Mon … 6=Sun)."""
        if day_of_week == 5 and self.sat_operating_start:  # Saturday
            return self.sat_operating_start, self.sat_operating_end or self.operating_end
        if day_of_week == 6 and self.sun_operating_start:  # Sunday
            return self.sun_operating_start, self.sun_operating_end or self.operating_end
        return self.operating_start, self.operating_end

    def to_dict(self):
        pu = self.planning_unit
        return {
            "id": self.id,
            "planning_unit_id": self.planning_unit_id,
            "lob_name": pu.name if pu else "",
            "service_level_target": self.service_level_target,
            "target_asa": self.target_asa,
            "interval_minutes": self.interval_minutes,
            "shrinkage_pct": self.shrinkage_pct,
            "occupancy_target": self.occupancy_target,
            "max_occupancy": self.max_occupancy,
            "default_shift_hrs": self.default_shift_hrs,
            "operating_start": self.operating_start,
            "operating_end": self.operating_end,
            "sat_operating_start": self.sat_operating_start or "",
            "sat_operating_end": self.sat_operating_end or "",
            "sun_operating_start": self.sun_operating_start or "",
            "sun_operating_end": self.sun_operating_end or "",
        }


# ═══════════════════════════════════════════════════════════════
# TIME-OFF REQUEST TYPES — Configurable categories
# ═══════════════════════════════════════════════════════════════

class TimeOffType(db.Model):
    """Admin-configurable PTO/time-off categories (vacation, sick, personal, FMLA, etc.)."""
    __tablename__ = "time_off_types"

    id            = db.Column(db.Integer, primary_key=True)
    code          = db.Column(db.String(40), unique=True, nullable=False)
    label         = db.Column(db.String(80), nullable=False)
    color         = db.Column(db.String(20), default="#6b7280")
    is_paid       = db.Column(db.Boolean, default=True)
    requires_approval = db.Column(db.Boolean, default=True)
    max_days_per_year = db.Column(db.Integer, nullable=True)   # None = unlimited
    min_notice_days   = db.Column(db.Integer, default=0)       # advance notice required
    is_default    = db.Column(db.Boolean, default=False)
    is_active     = db.Column(db.Boolean, default=True)
    sort_order    = db.Column(db.Integer, default=0)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id, "code": self.code, "label": self.label,
            "color": self.color, "is_paid": self.is_paid,
            "requires_approval": self.requires_approval,
            "max_days_per_year": self.max_days_per_year,
            "min_notice_days": self.min_notice_days,
            "is_default": self.is_default, "is_active": self.is_active,
            "sort_order": self.sort_order,
        }


# ═══════════════════════════════════════════════════════════════
# OVERTIME RULES
# ═══════════════════════════════════════════════════════════════

class OvertimeRule(db.Model):
    """Overtime policies — max hours, approval, voluntary/mandatory, blackout periods."""
    __tablename__ = "overtime_rules"

    id                = db.Column(db.Integer, primary_key=True)
    name              = db.Column(db.String(80), nullable=False)
    rule_type         = db.Column(db.String(20), default="voluntary")    # voluntary | mandatory | restricted
    max_ot_hours_week = db.Column(db.Float, default=10.0)
    max_ot_hours_day  = db.Column(db.Float, default=4.0)
    requires_approval = db.Column(db.Boolean, default=True)
    min_notice_hours  = db.Column(db.Integer, default=24)                # advance notice
    blackout_dates_json = db.Column(db.Text, default="[]")              # JSON array of date strings
    eligible_after_days = db.Column(db.Integer, default=90)             # days of employment before eligible
    pay_multiplier    = db.Column(db.Float, default=1.5)                # time-and-a-half, double, etc.
    is_active         = db.Column(db.Boolean, default=True)
    created_at        = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        import json
        return {
            "id": self.id, "name": self.name, "rule_type": self.rule_type,
            "max_ot_hours_week": self.max_ot_hours_week,
            "max_ot_hours_day": self.max_ot_hours_day,
            "requires_approval": self.requires_approval,
            "min_notice_hours": self.min_notice_hours,
            "blackout_dates": json.loads(self.blackout_dates_json) if self.blackout_dates_json else [],
            "eligible_after_days": self.eligible_after_days,
            "pay_multiplier": self.pay_multiplier,
            "is_active": self.is_active,
        }


# ═══════════════════════════════════════════════════════════════
# SCHEDULING RULES / CONSTRAINTS
# ═══════════════════════════════════════════════════════════════

class ScheduleRule(db.Model):
    """Scheduling constraints — min/max hours, consecutive days, rest periods."""
    __tablename__ = "schedule_rules"

    id                    = db.Column(db.Integer, primary_key=True)
    name                  = db.Column(db.String(80), nullable=False)
    min_hours_week        = db.Column(db.Float, default=20.0)
    max_hours_week        = db.Column(db.Float, default=40.0)
    max_hours_day         = db.Column(db.Float, default=10.0)
    max_consecutive_days  = db.Column(db.Integer, default=6)
    min_rest_between_shifts_hrs = db.Column(db.Float, default=10.0)     # hours between shifts
    min_days_off_per_week = db.Column(db.Integer, default=1)
    max_split_shifts_week = db.Column(db.Integer, default=0)            # 0 = not allowed
    allow_back_to_back    = db.Column(db.Boolean, default=False)        # close then open
    is_default            = db.Column(db.Boolean, default=False)
    is_active             = db.Column(db.Boolean, default=True)
    created_at            = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id, "name": self.name,
            "min_hours_week": self.min_hours_week,
            "max_hours_week": self.max_hours_week,
            "max_hours_day": self.max_hours_day,
            "max_consecutive_days": self.max_consecutive_days,
            "min_rest_between_shifts_hrs": self.min_rest_between_shifts_hrs,
            "min_days_off_per_week": self.min_days_off_per_week,
            "max_split_shifts_week": self.max_split_shifts_week,
            "allow_back_to_back": self.allow_back_to_back,
            "is_default": self.is_default, "is_active": self.is_active,
        }


# ═══════════════════════════════════════════════════════════════
# HOLIDAY CALENDAR
# ═══════════════════════════════════════════════════════════════

class Holiday(db.Model):
    """Company holidays that affect scheduling and forecasting."""
    __tablename__ = "holidays"

    id            = db.Column(db.Integer, primary_key=True)
    name          = db.Column(db.String(100), nullable=False)
    date          = db.Column(db.Date, nullable=False)
    is_full_day   = db.Column(db.Boolean, default=True)
    start_time    = db.Column(db.String(5), nullable=True)       # for partial-day holidays
    end_time      = db.Column(db.String(5), nullable=True)
    is_paid       = db.Column(db.Boolean, default=True)
    affects_forecast = db.Column(db.Boolean, default=True)       # adjust forecast volume
    volume_factor = db.Column(db.Float, default=0.0)             # 0 = closed, 0.5 = half volume
    year          = db.Column(db.Integer, nullable=False)
    is_recurring  = db.Column(db.Boolean, default=True)          # auto-create for next year
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id, "name": self.name,
            "date": self.date.isoformat() if self.date else "",
            "is_full_day": self.is_full_day,
            "start_time": self.start_time, "end_time": self.end_time,
            "is_paid": self.is_paid, "affects_forecast": self.affects_forecast,
            "volume_factor": self.volume_factor, "year": self.year,
            "is_recurring": self.is_recurring,
        }


# ═══════════════════════════════════════════════════════════════
# SKILL GROUPS — Multi-skill mapping with proficiency
# ═══════════════════════════════════════════════════════════════

class SkillGroup(db.Model):
    """Named skill groups for organizing LOBs/queues."""
    __tablename__ = "skill_groups"

    id          = db.Column(db.Integer, primary_key=True)
    name        = db.Column(db.String(80), unique=True, nullable=False)
    description = db.Column(db.String(255), default="")
    is_active   = db.Column(db.Boolean, default=True)
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)

    mappings = db.relationship("SkillMapping", backref="skill_group",
                               cascade="all, delete-orphan", lazy="joined")

    def to_dict(self):
        return {
            "id": self.id, "name": self.name,
            "description": self.description, "is_active": self.is_active,
            "mappings": [m.to_dict() for m in self.mappings],
        }


class SkillMapping(db.Model):
    """Maps an employee to a skill group with proficiency level and priority."""
    __tablename__ = "skill_mappings"

    id            = db.Column(db.Integer, primary_key=True)
    skill_group_id = db.Column(db.Integer, db.ForeignKey("skill_groups.id", ondelete="CASCADE"),
                               nullable=False, index=True)
    employee_id   = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=False, index=True)
    proficiency   = db.Column(db.Integer, default=3)          # 1-5 scale
    priority      = db.Column(db.Integer, default=1)          # routing priority (1 = primary)
    is_active     = db.Column(db.Boolean, default=True)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)

    employee = db.relationship("Employee", backref="skill_mappings")

    __table_args__ = (
        db.UniqueConstraint("skill_group_id", "employee_id", name="uq_skill_employee"),
    )

    def to_dict(self):
        emp = self.employee
        return {
            "id": self.id, "skill_group_id": self.skill_group_id,
            "employee_id": emp.employee_id if emp else "",
            "employee_name": emp.full_name if emp else "",
            "proficiency": self.proficiency, "priority": self.priority,
            "is_active": self.is_active,
        }


# ═══════════════════════════════════════════════════════════════
# ADHERENCE EXCEPTION CODES
# ═══════════════════════════════════════════════════════════════

class AdherenceException(db.Model):
    """Exception codes for adherence deviations (late, early out, approved absence, etc.)."""
    __tablename__ = "adherence_exceptions"

    id           = db.Column(db.Integer, primary_key=True)
    code         = db.Column(db.String(40), unique=True, nullable=False)
    label        = db.Column(db.String(80), nullable=False)
    color        = db.Column(db.String(20), default="#ef4444")
    is_excused   = db.Column(db.Boolean, default=False)       # excused = doesn't count against adherence
    category     = db.Column(db.String(30), default="other")  # late | early_out | absence | break_overrun | other
    is_default   = db.Column(db.Boolean, default=False)
    is_active    = db.Column(db.Boolean, default=True)
    sort_order   = db.Column(db.Integer, default=0)
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id, "code": self.code, "label": self.label,
            "color": self.color, "is_excused": self.is_excused,
            "category": self.category, "is_default": self.is_default,
            "is_active": self.is_active, "sort_order": self.sort_order,
        }


# ═══════════════════════════════════════════════════════════════
# NOTIFICATION / ALERT SETTINGS
# ═══════════════════════════════════════════════════════════════

class AlertConfig(db.Model):
    """Configurable alert thresholds and notification preferences."""
    __tablename__ = "alert_configs"

    id                = db.Column(db.Integer, primary_key=True)
    name              = db.Column(db.String(80), nullable=False)
    alert_type        = db.Column(db.String(40), nullable=False)        # sl_breach | understaffed | overstaffed | adherence | forecast_variance
    threshold_value   = db.Column(db.Float, nullable=False)             # e.g. 0.80 for SL, 10.0 for % variance
    threshold_operator = db.Column(db.String(10), default="lt")         # lt | gt | eq
    planning_unit_id  = db.Column(db.Integer, db.ForeignKey("planning_units.id"), nullable=True)  # None = all LOBs
    notify_email      = db.Column(db.Boolean, default=False)
    notify_in_app     = db.Column(db.Boolean, default=True)
    email_recipients  = db.Column(db.Text, default="")                  # comma-separated emails
    cooldown_minutes  = db.Column(db.Integer, default=30)               # min time between alerts
    is_active         = db.Column(db.Boolean, default=True)
    created_at        = db.Column(db.DateTime, default=datetime.utcnow)

    planning_unit = db.relationship("PlanningUnit", backref="alert_configs")

    def to_dict(self):
        pu = self.planning_unit
        return {
            "id": self.id, "name": self.name, "alert_type": self.alert_type,
            "threshold_value": self.threshold_value,
            "threshold_operator": self.threshold_operator,
            "lob_name": pu.name if pu else "All",
            "planning_unit_id": self.planning_unit_id,
            "notify_email": self.notify_email, "notify_in_app": self.notify_in_app,
            "email_recipients": self.email_recipients,
            "cooldown_minutes": self.cooldown_minutes, "is_active": self.is_active,
        }


# ═══════════════════════════════════════════════════════════════
# BRANDING / WHITE-LABEL SETTINGS (DB-backed)
# ═══════════════════════════════════════════════════════════════

class BrandSetting(db.Model):
    """Per-client branding overrides — stored in DB so admins can edit via UI."""
    __tablename__ = "brand_settings"

    id            = db.Column(db.Integer, primary_key=True)
    company_name  = db.Column(db.String(120), default="")
    tagline       = db.Column(db.String(255), default="")
    accent_color  = db.Column(db.String(20), default="#2563eb")
    contact_email = db.Column(db.String(255), default="")
    logo_url      = db.Column(db.String(500), default="")              # URL or data-URI
    favicon_url   = db.Column(db.String(500), default="")
    footer_text   = db.Column(db.String(255), default="")
    updated_at    = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id, "company_name": self.company_name,
            "tagline": self.tagline, "accent_color": self.accent_color,
            "contact_email": self.contact_email, "logo_url": self.logo_url,
            "favicon_url": self.favicon_url, "footer_text": self.footer_text,
        }


# ═══════════════════════════════════════════════════════════════
# EMPLOYEE AVAILABILITY
# ═══════════════════════════════════════════════════════════════

class EmployeeAvailability(db.Model):
    """Per-day availability window for an employee.
    One row per employee per day-of-week (0=Mon … 6=Sun)."""
    __tablename__ = "employee_availability"

    id             = db.Column(db.Integer, primary_key=True)
    employee_id    = db.Column(db.Integer, db.ForeignKey("employees.id", ondelete="CASCADE"),
                               nullable=False, index=True)
    day_of_week    = db.Column(db.Integer, nullable=False)   # 0=Mon, 1=Tue … 6=Sun
    is_available   = db.Column(db.Boolean, default=True)
    earliest_start = db.Column(db.Time, nullable=True)       # e.g. 08:00
    latest_start   = db.Column(db.Time, nullable=True)       # e.g. 10:00
    latest_end     = db.Column(db.Time, nullable=True)       # e.g. 22:00
    notes          = db.Column(db.String(255), default="")
    updated_at     = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    employee = db.relationship("Employee", backref="availability_entries")

    __table_args__ = (
        db.UniqueConstraint("employee_id", "day_of_week", name="uq_avail_emp_day"),
    )

    def to_dict(self):
        day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        return {
            "id": self.id,
            "employee_id": self.employee_id,
            "day_of_week": self.day_of_week,
            "day_name": day_names[self.day_of_week] if 0 <= self.day_of_week <= 6 else "",
            "is_available": self.is_available,
            "earliest_start": self.earliest_start.strftime("%H:%M") if self.earliest_start else "",
            "latest_start": self.latest_start.strftime("%H:%M") if self.latest_start else "",
            "latest_end": self.latest_end.strftime("%H:%M") if self.latest_end else "",
            "notes": self.notes or "",
        }


# ═══════════════════════════════════════════════════════════════
# REAL-TIME ACTUALS (ACD interval stats + agent status events)
# ═══════════════════════════════════════════════════════════════

class IntervalActual(db.Model):
    """Half-hour (or 15-min) call statistics from the phone system / ACD.
    One row per LOB per interval. Fed by CSV upload, Google Sheet tab
    'ACTUALS RAW', or an API connector."""
    __tablename__ = "interval_actuals"

    id               = db.Column(db.Integer, primary_key=True)
    planning_unit_id = db.Column(db.Integer, db.ForeignKey("planning_units.id"), nullable=False, index=True)
    timestamp        = db.Column(db.DateTime, nullable=False, index=True)   # interval start (local)
    offered          = db.Column(db.Integer, default=0)
    answered         = db.Column(db.Integer, default=0)
    answered_within  = db.Column(db.Integer, default=0)   # answered within SL threshold
    abandoned        = db.Column(db.Integer, default=0)
    rolled           = db.Column(db.Integer, default=0)   # rolled over / overflowed
    asa_secs         = db.Column(db.Float, nullable=True) # average speed of answer
    aht_secs         = db.Column(db.Float, nullable=True) # actual average handle time
    max_queued       = db.Column(db.Integer, nullable=True)
    source           = db.Column(db.String(30), default="upload")  # upload | sheet | api | demo
    data_source_id   = db.Column(db.Integer, db.ForeignKey("data_sources.id"), nullable=True, index=True)
    uploaded_at      = db.Column(db.DateTime, default=datetime.utcnow)

    planning_unit = db.relationship("PlanningUnit")
    data_source   = db.relationship("DataSource", backref="actuals")

    __table_args__ = (
        db.UniqueConstraint("planning_unit_id", "timestamp", name="uq_actual_unit_ts"),
    )

    def to_dict(self):
        return {
            "time": self.timestamp.strftime("%H:%M"),
            "timestamp": self.timestamp.strftime("%Y-%m-%d %H:%M"),
            "offered": self.offered or 0,
            "answered": self.answered or 0,
            "answered_within": self.answered_within or 0,
            "abandoned": self.abandoned or 0,
            "rolled": self.rolled or 0,
            "asa": self.asa_secs,
            "aht": self.aht_secs,
            "max_queued": self.max_queued,
        }


class LobMapping(db.Model):
    """Maps a source LOB/workload name to a canonical planning-unit name.
    E.g. 'SS Sales Combined' → 'SS Sales'.
    Replaces the hard-coded _LOB_TO_PU dict for generic client support."""
    __tablename__ = "lob_mappings"

    id               = db.Column(db.Integer, primary_key=True)
    source_name      = db.Column(db.String(200), unique=True, nullable=False, index=True)
    planning_unit_name = db.Column(db.String(200), nullable=False)
    created_at       = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<LobMapping {self.source_name!r} → {self.planning_unit_name!r}>"


class AgentStatusEvent(db.Model):
    """A period an agent spent in one ACD status (On Queue, Break, Lunch,
    Meeting, Offline, ...). Drives Agent Status, Absenteeism, Efficiency."""
    __tablename__ = "agent_status_events"

    id           = db.Column(db.Integer, primary_key=True)
    employee_id  = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=False, index=True)
    status       = db.Column(db.String(40), nullable=False)          # normalised label
    start_ts     = db.Column(db.DateTime, nullable=False, index=True)
    end_ts       = db.Column(db.DateTime, nullable=True)              # null = still in this status
    source       = db.Column(db.String(30), default="upload")
    uploaded_at  = db.Column(db.DateTime, default=datetime.utcnow)

    employee = db.relationship("Employee", backref=db.backref("status_events", lazy="dynamic"))

    __table_args__ = (
        db.Index("ix_agent_status_emp_start", "employee_id", "start_ts"),
    )

    def to_dict(self):
        emp = self.employee
        return {
            "employee": emp.full_name if emp else "",
            "employee_id": emp.employee_id if emp else "",
            "status": self.status,
            "start": self.start_ts.strftime("%Y-%m-%d %H:%M:%S"),
            "end": self.end_ts.strftime("%Y-%m-%d %H:%M:%S") if self.end_ts else None,
        }
