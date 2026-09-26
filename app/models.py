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
            "End Date": self.end_date.isoformat() if self.end_date else "",
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
