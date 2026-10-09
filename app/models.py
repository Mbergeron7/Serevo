"""
Serevo — SQLAlchemy models
============================
All database tables for the production platform.
"""

import json
from datetime import datetime, date, time, timezone


def _utcnow():
    return datetime.now(timezone.utc)
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
    role          = db.Column(db.String(20), nullable=False, default="supervisor")  # supervisor | admin | agent
    is_demo       = db.Column(db.Boolean, default=False)
    is_active     = db.Column(db.Boolean, default=True)
    oauth_provider = db.Column(db.String(30), nullable=True)   # "google", "microsoft", etc.
    oauth_id       = db.Column(db.String(255), nullable=True)  # provider's unique user ID
    employee_id   = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=True)
    wfm_access    = db.Column(db.Boolean, default=False)  # grants access to WFM ticketing tool
    created_at    = db.Column(db.DateTime, default=_utcnow)
    updated_at    = db.Column(db.DateTime, default=_utcnow, onupdate=_utcnow)

    employee      = db.relationship("Employee", backref="user_account", foreign_keys=[employee_id])

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
    created_at = db.Column(db.DateTime, default=_utcnow)

    description  = db.Column(db.String(255), nullable=True)
    timezone     = db.Column(db.String(60), default="America/New_York")

    employees    = db.relationship("Employee", backref="planning_unit", lazy="dynamic")
    requirements = db.relationship("RequirementInterval", backref="planning_unit", lazy="dynamic")
    forecasts    = db.relationship("ForecastInterval", backref="planning_unit", lazy="dynamic")

    def __repr__(self):
        return f"<PlanningUnit {self.name}>"

    def to_dict(self):
        return {
            "id": self.id, "name": self.name, "is_active": self.is_active,
            "description": self.description or "",
            "timezone": self.timezone or "America/New_York",
            "business_hours": [bh.to_dict() for bh in self.business_hours],
            "assigned_activities": [pa.to_dict() for pa in self.assigned_activities],
            "parameters": [p.to_dict() for p in self.unit_parameters],
            "call_routes": [cr.to_dict() for cr in self.call_routes],
        }


class CallRoute(db.Model):
    """Maps a phone system call route/queue to a planning unit.
    E.g. route_id='719' → name='SS Sales EN' → planning_unit 'Sales'.
    Used to translate incoming real-time data (which only has route IDs)."""
    __tablename__ = "call_routes"

    id               = db.Column(db.Integer, primary_key=True)
    planning_unit_id = db.Column(db.Integer, db.ForeignKey("planning_units.id"), nullable=False)
    route_id         = db.Column(db.String(50), nullable=False, index=True)    # e.g. "719"
    name             = db.Column(db.String(200), nullable=False)               # e.g. "SS Sales EN"
    is_active        = db.Column(db.Boolean, default=True)
    created_at       = db.Column(db.DateTime, default=_utcnow)

    planning_unit = db.relationship("PlanningUnit", backref=db.backref("call_routes", lazy="select", cascade="all, delete-orphan"))

    __table_args__ = (
        db.UniqueConstraint("planning_unit_id", "route_id", name="uq_call_route_pu"),
    )

    def to_dict(self):
        return {
            "id": self.id, "route_id": self.route_id,
            "name": self.name, "is_active": self.is_active,
        }


class PlanningUnitBusinessHours(db.Model):
    """Business hours per day type for a planning unit.
    E.g. Monday: 08:00-22:00, valid from 2026-01-01."""
    __tablename__ = "planning_unit_business_hours"

    id               = db.Column(db.Integer, primary_key=True)
    planning_unit_id = db.Column(db.Integer, db.ForeignKey("planning_units.id"), nullable=False)
    day_type         = db.Column(db.String(20), nullable=False)           # monday|tuesday|...|sunday|holiday
    open_time        = db.Column(db.String(5), nullable=False)            # "08:00"
    close_time       = db.Column(db.String(5), nullable=False)            # "22:00"
    valid_from       = db.Column(db.Date, nullable=True)
    valid_to         = db.Column(db.Date, nullable=True)
    created_at       = db.Column(db.DateTime, default=_utcnow)

    planning_unit = db.relationship("PlanningUnit", backref=db.backref("business_hours", lazy="select", cascade="all, delete-orphan"))

    def to_dict(self):
        return {
            "id": self.id, "day_type": self.day_type,
            "open_time": self.open_time, "close_time": self.close_time,
            "valid_from": self.valid_from.isoformat() if self.valid_from else None,
            "valid_to": self.valid_to.isoformat() if self.valid_to else None,
        }


class PlanningUnitActivity(db.Model):
    """Activities assigned to a planning unit with time windows and validity dates.
    E.g. 'Break' activity available 10:00-14:00, valid from 2026-01-01."""
    __tablename__ = "planning_unit_activities"

    id               = db.Column(db.Integer, primary_key=True)
    planning_unit_id = db.Column(db.Integer, db.ForeignKey("planning_units.id"), nullable=False)
    segment_code_id  = db.Column(db.Integer, db.ForeignKey("segment_codes.id"), nullable=False)
    window_start     = db.Column(db.String(5), nullable=True)             # "10:00" — time window start
    window_end       = db.Column(db.String(5), nullable=True)             # "14:00" — time window end
    valid_from       = db.Column(db.Date, nullable=True)
    valid_to         = db.Column(db.Date, nullable=True)
    created_at       = db.Column(db.DateTime, default=_utcnow)

    planning_unit = db.relationship("PlanningUnit", backref=db.backref("assigned_activities", lazy="select", cascade="all, delete-orphan"))
    segment_code  = db.relationship("SegmentCode")

    def to_dict(self):
        return {
            "id": self.id, "segment_code_id": self.segment_code_id,
            "activity_name": self.segment_code.label if self.segment_code else "",
            "window_start": self.window_start, "window_end": self.window_end,
            "valid_from": self.valid_from.isoformat() if self.valid_from else None,
            "valid_to": self.valid_to.isoformat() if self.valid_to else None,
        }


class PlanningUnitParameter(db.Model):
    """User-defined parameters for a planning unit (e.g. AHT targets, shrinkage).
    Name + lower/upper limit range."""
    __tablename__ = "planning_unit_parameters"

    id               = db.Column(db.Integer, primary_key=True)
    planning_unit_id = db.Column(db.Integer, db.ForeignKey("planning_units.id"), nullable=False)
    name             = db.Column(db.String(120), nullable=False)
    lower_limit      = db.Column(db.Float, nullable=True)
    upper_limit      = db.Column(db.Float, nullable=True)
    created_at       = db.Column(db.DateTime, default=_utcnow)

    planning_unit = db.relationship("PlanningUnit", backref=db.backref("unit_parameters", lazy="select", cascade="all, delete-orphan"))

    def to_dict(self):
        return {
            "id": self.id, "name": self.name,
            "lower_limit": self.lower_limit, "upper_limit": self.upper_limit,
        }


# ═══════════════════════════════════════════════════════════════
# EMPLOYEES
# ═══════════════════════════════════════════════════════════════

class Employee(db.Model):
    __tablename__ = "employees"

    id               = db.Column(db.Integer, primary_key=True)
    employee_id      = db.Column(db.String(30), unique=True, nullable=False, index=True)
    external_id_1    = db.Column(db.String(50), nullable=True, index=True)   # e.g. IntelYStorageVault ID
    external_id_2    = db.Column(db.String(50), nullable=True, index=True)   # e.g. CallPotential ID
    first_name       = db.Column(db.String(80), nullable=False)
    last_name        = db.Column(db.String(80), nullable=False)
    status           = db.Column(db.String(20), nullable=False, default="Active", index=True)
    planning_unit_id = db.Column(db.Integer, db.ForeignKey("planning_units.id"), nullable=True, index=True)
    all_skills       = db.Column(db.Text, default="")
    skill_start      = db.Column(db.Date, nullable=True)
    skill_end        = db.Column(db.Date, nullable=True)
    end_date         = db.Column(db.Date, nullable=True)
    languages        = db.Column(db.String(100), default="English")
    contract_type    = db.Column(db.String(100), default="Full-Time")    # Full-Time | Part-Time
    contract_id      = db.Column(db.Integer, db.ForeignKey("contracts.id"), nullable=True)
    weekly_hours     = db.Column(db.Float, default=42.5)
    days_per_week    = db.Column(db.Integer, default=5)
    hours_per_day    = db.Column(db.Float, default=8.5)
    timezone         = db.Column(db.String(60), default="America/New_York")
    email            = db.Column(db.String(255), nullable=True, index=True)
    team_lead        = db.Column(db.String(100), default="")
    schedule_excluded = db.Column(db.Boolean, default=False, server_default="false")
    manually_edited  = db.Column(db.Boolean, default=False, server_default="false")
    created_at       = db.Column(db.DateTime, default=_utcnow)
    updated_at       = db.Column(db.DateTime, default=_utcnow, onupdate=_utcnow)

    contract       = db.relationship("Contract", backref="employees")
    accommodations = db.relationship("Accommodation", backref="employee", lazy="dynamic",
                                     cascade="all, delete-orphan")
    pto_entries    = db.relationship("PTOEntry", backref="employee", lazy="dynamic",
                                    cascade="all, delete-orphan")

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
            "external_id_1": self.external_id_1 or "",
            "external_id_2": self.external_id_2 or "",
            "Latest Skill Name": pu.name if pu else "",
            "Latest Skill Start": self.skill_start.isoformat() if self.skill_start else "",
            "Latest Skill End": self.skill_end.isoformat() if self.skill_end else "",
            "All Skills": self.all_skills or "",
            "Languages": self.languages or "English",
            "Contract Type": self.contract_type or "Full-Time",
            "contract_id": self.contract_id,
            "Weekly Hours": self.weekly_hours or 40.0,
            "Days Per Week": self.days_per_week or 5,
            "Hours Per Day": self.hours_per_day or 8.0,
            "Timezone": self.timezone or "America/New_York",
            "Schedule Excluded": self.schedule_excluded or False,
            "End Date": self.end_date.isoformat() if self.end_date else "",
            "Team Lead": self.team_lead or "",
            "Email": self.email or "",
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
    updated_at  = db.Column(db.DateTime, default=_utcnow, onupdate=_utcnow)

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

    id               = db.Column(db.Integer, primary_key=True)
    employee_id      = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=False, index=True)
    start_date       = db.Column(db.Date, nullable=False, index=True)
    end_date         = db.Column(db.Date, nullable=False, index=True)
    pto_type         = db.Column(db.String(20), default="full")   # full | partial
    time_off_type_id = db.Column(db.Integer, db.ForeignKey("time_off_types.id"), nullable=True)
    approval_status  = db.Column(db.String(20), default="approved")  # pending | approved | denied
    requested_by     = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    reviewed_by      = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    reviewed_at      = db.Column(db.DateTime, nullable=True)
    note             = db.Column(db.Text, default="")
    updated_at       = db.Column(db.DateTime, default=_utcnow, onupdate=_utcnow)

    time_off_type    = db.relationship("TimeOffType", backref="pto_entries")
    requester        = db.relationship("User", foreign_keys=[requested_by])
    reviewer         = db.relationship("User", foreign_keys=[reviewed_by])

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
    uploaded_at      = db.Column(db.DateTime, default=_utcnow)

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
    uploaded_at      = db.Column(db.DateTime, default=_utcnow)

    __table_args__ = (
        db.Index("ix_req_unit_ts", "planning_unit_id", "timestamp"),
    )

    def to_legacy_dict(self):
        return {
            "time": self.timestamp.strftime("%Y-%m-%d %H:%M"),
            "agents_required": self.agents_required,
        }


# ═══════════════════════════════════════════════════════════════
# FORECAST SCENARIOS
# ═══════════════════════════════════════════════════════════════

class ForecastScenario(db.Model):
    """A named forecast scenario (Regular, Operational, Strategic).

    Each scenario stores a snapshot of forecast settings and results
    so users can compare different approaches and push the best one
    to staffing requirements.
    """
    __tablename__ = "forecast_scenarios"

    id               = db.Column(db.Integer, primary_key=True)
    planning_unit_id = db.Column(db.Integer, db.ForeignKey("planning_units.id"), nullable=False, index=True)
    name             = db.Column(db.String(60), nullable=False)            # "Regular", "Operational", "Strategic"
    scenario_type    = db.Column(db.String(30), nullable=False, default="regular")  # regular | operational | strategic
    method           = db.Column(db.String(40), default="weighted")        # forecast method used
    interval_minutes = db.Column(db.Integer, default=30)                   # 15 or 30
    historical_days  = db.Column(db.Integer, default=90)
    forecast_days    = db.Column(db.Integer, default=90)
    service_level    = db.Column(db.Float, default=0.80)
    target_asa       = db.Column(db.Float, default=30)
    shrinkage        = db.Column(db.Float, default=0.30)
    is_active        = db.Column(db.Boolean, default=False)                # pushed to requirements?
    year             = db.Column(db.Integer, nullable=False)
    notes            = db.Column(db.Text, default="")
    created_at       = db.Column(db.DateTime, default=_utcnow)
    updated_at       = db.Column(db.DateTime, default=_utcnow, onupdate=_utcnow)

    planning_unit = db.relationship("PlanningUnit", backref=db.backref("forecast_scenarios", lazy="dynamic"))

    __table_args__ = (
        db.Index("ix_scenario_unit_year", "planning_unit_id", "year"),
    )

    def to_dict(self):
        pu = self.planning_unit
        return {
            "id": self.id,
            "planning_unit_id": self.planning_unit_id,
            "lob_name": pu.name if pu else "",
            "name": self.name,
            "scenario_type": self.scenario_type,
            "method": self.method,
            "interval_minutes": self.interval_minutes,
            "historical_days": self.historical_days,
            "forecast_days": self.forecast_days,
            "service_level": self.service_level,
            "target_asa": self.target_asa,
            "shrinkage": self.shrinkage,
            "is_active": self.is_active,
            "year": self.year,
            "notes": self.notes,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


# ═══════════════════════════════════════════════════════════════
# SCHEDULES
# ═══════════════════════════════════════════════════════════════

class Schedule(db.Model):
    __tablename__ = "schedules"

    id               = db.Column(db.Integer, primary_key=True)
    employee_id      = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=False, index=True)
    planning_unit_id = db.Column(db.Integer, db.ForeignKey("planning_units.id"), nullable=True)
    schedule_date    = db.Column(db.Date, nullable=False, index=True)
    shift_start      = db.Column(db.Time, nullable=True)
    shift_end        = db.Column(db.Time, nullable=True)
    shift_type       = db.Column(db.String(10), default="full")      # full | half
    hours            = db.Column(db.Float, default=0)
    status           = db.Column(db.String(20), default="scheduled")  # scheduled | off | pto
    created_at       = db.Column(db.DateTime, default=_utcnow)
    updated_at       = db.Column(db.DateTime, default=_utcnow, onupdate=_utcnow)

    employee = db.relationship("Employee", backref=db.backref("schedules", cascade="all, delete-orphan"))
    planning_unit = db.relationship("PlanningUnit", backref="schedules")
    segments = db.relationship("ShiftSegment", backref="schedule",
                               cascade="all, delete-orphan",
                               order_by="ShiftSegment.sort_order",
                               lazy="joined")

    __table_args__ = (
        db.Index("ix_sched_date_unit", "schedule_date", "planning_unit_id"),
    )

    def to_dict(self):
        emp = self.employee
        pu = self.planning_unit
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
            "team_lead": emp.team_lead if emp and emp.team_lead else "",
            "lob": pu.name if pu else "",
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
    created_at    = db.Column(db.DateTime, default=_utcnow)

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
    created_at    = db.Column(db.DateTime, default=_utcnow)
    updated_at    = db.Column(db.DateTime, default=_utcnow, onupdate=_utcnow)

    def __repr__(self):
        return f"<APIConnection {self.name} ({self.provider})>"


class DataFeed(db.Model):
    """A configured data feed — a Google Sheet or API endpoint that provides
    call volume or agent status data on a recurring sync schedule.
    Each client/LOB can have its own feeds."""
    __tablename__ = "data_feeds"

    id            = db.Column(db.Integer, primary_key=True)
    name          = db.Column(db.String(200), nullable=False)          # e.g. "Acme Corp - Call Volume"
    feed_type     = db.Column(db.String(30), nullable=False)           # call_volume | agent_status
    source_type   = db.Column(db.String(30), nullable=False, default="google_sheet")  # google_sheet | api
    sheet_key     = db.Column(db.String(200), default="")              # Google Sheet key
    sheet_tab     = db.Column(db.String(100), default="")              # specific tab name (optional)
    data_format   = db.Column(db.String(20), default="interval")       # interval | event
    interval_minutes = db.Column(db.Integer, default=15)               # aggregation bucket size for event format
    source_timezone  = db.Column(db.String(60), default="")            # IANA tz of source data, e.g. "US/Central"
    service_account_json = db.Column(db.Text, default="")              # per-feed Google SA creds (JSON)
    api_url       = db.Column(db.String(500), default="")              # for API sources
    api_headers   = db.Column(db.Text, default="")                     # JSON headers for API
    column_mapping = db.Column(db.Text, default="")                    # JSON: {"canonical_field": "sheet_column_name", ...}
    is_active     = db.Column(db.Boolean, default=True)
    last_sync_at  = db.Column(db.DateTime, nullable=True)
    last_status   = db.Column(db.String(20), default="new")            # new | ok | error
    last_error    = db.Column(db.Text, default="")
    last_upserted = db.Column(db.Integer, default=0)
    last_skipped  = db.Column(db.Integer, default=0)
    created_at    = db.Column(db.DateTime, default=_utcnow)
    updated_at    = db.Column(db.DateTime, default=_utcnow, onupdate=_utcnow)

    def __repr__(self):
        return f"<DataFeed {self.name} ({self.feed_type}/{self.source_type})>"

    def to_dict(self):
        return {
            "id": self.id, "name": self.name,
            "feed_type": self.feed_type, "source_type": self.source_type,
            "sheet_key": self.sheet_key, "sheet_tab": self.sheet_tab,
            "data_format": self.data_format or "interval",
            "interval_minutes": self.interval_minutes or 15,
            "source_timezone": self.source_timezone or "",
            "has_service_account": bool(self.service_account_json),
            "api_url": self.api_url, "api_headers": self.api_headers or "",
            "column_mapping": json.loads(self.column_mapping) if self.column_mapping else {},
            "is_active": self.is_active,
            "last_sync_at": self.last_sync_at.isoformat() if self.last_sync_at else None,
            "last_status": self.last_status,
            "last_error": self.last_error,
            "last_upserted": self.last_upserted,
            "last_skipped": self.last_skipped,
        }


# ═══════════════════════════════════════════════════════════════
# SCHEMA MAPPINGS (saved column-mapping profiles for data imports)
# ═══════════════════════════════════════════════════════════════

class SchemaMapping(db.Model):
    """Saved column-mapping profile for data imports.

    Stores a named mapping from client-specific column headers to our
    internal field names, per upload type.  Lets different clients use
    different column names and reuse their mapping across imports.
    """
    __tablename__ = "schema_mappings"

    id          = db.Column(db.Integer, primary_key=True)
    name        = db.Column(db.String(120), nullable=False)
    upload_type = db.Column(db.String(30), nullable=False)
    mapping     = db.Column(db.Text, nullable=False, default="{}")  # JSON: {"their_header": "our_field", …}
    created_by  = db.Column(db.String(255), default="")
    created_at  = db.Column(db.DateTime, default=_utcnow)
    updated_at  = db.Column(db.DateTime, default=_utcnow, onupdate=_utcnow)

    def get_mapping(self):
        import json
        try:
            return json.loads(self.mapping or "{}")
        except (json.JSONDecodeError, TypeError):
            return {}

    def set_mapping(self, d):
        import json
        self.mapping = json.dumps(d)

    def __repr__(self):
        return f"<SchemaMapping {self.name} ({self.upload_type})>"


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
    created_at   = db.Column(db.DateTime, default=_utcnow)

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

    created_at        = db.Column(db.DateTime, default=_utcnow)
    updated_at        = db.Column(db.DateTime, default=_utcnow, onupdate=_utcnow)

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
    # ── Activity type (PeopleWare categories) ────────────────
    # Controls which features are available on this activity
    activity_type = db.Column(db.String(20), default="presence")          # presence | break | absence | meeting | vacation
    # ── Activity category ────────────────────────────────────
    activity_category = db.Column(db.String(20), default="status")        # status | lob
    # ── Naming / identification ──────────────────────────────
    official_name = db.Column(db.String(120), nullable=True)              # official/long name
    abbreviation  = db.Column(db.String(20), nullable=True)               # short abbreviation
    shortcut      = db.Column(db.String(10), nullable=True)               # keyboard shortcut key
    # ── External ID mapping ──────────────────────────────────
    external_ids = db.Column(db.Text, nullable=True)                      # JSON array e.g. ["1003", "AUX_BREAK"]
    # ── Multi-activity support ───────────────────────────────
    parent_id    = db.Column(db.Integer, db.ForeignKey("segment_codes.id"), nullable=True)
    is_multi_activity = db.Column(db.Boolean, default=False)              # True = parent with subactivities
    # ── Scheduling behavior flags ────────────────────────────
    is_replaceable       = db.Column(db.Boolean, default=True)            # can be replaced by another activity
    is_plannable         = db.Column(db.Boolean, default=True)            # can be placed in schedules
    importance           = db.Column(db.Integer, default=50)              # 0-100, higher = more important
    priority             = db.Column(db.Integer, default=50)              # 0-100, scheduling priority
    comply_rest_period   = db.Column(db.Boolean, default=True)            # must respect rest period rules
    allow_overstaffing_zero = db.Column(db.Boolean, default=False)        # allow overstaffing when requirement=0
    is_requestable       = db.Column(db.Boolean, default=False)           # agents can request in self-service
    is_exchangeable      = db.Column(db.Boolean, default=False)           # can be swapped in shift exchange
    allow_full_day       = db.Column(db.Boolean, default=False)           # can span full day
    special_handling     = db.Column(db.Boolean, default=False)           # special handling in optimized scheduling
    can_be_day_status    = db.Column(db.Boolean, default=False)           # can serve as day-level status
    # ── Placement rules ──────────────────────────────────────
    offset_mins       = db.Column(db.Integer, nullable=True)
    duration_mins     = db.Column(db.Integer, nullable=True)
    is_flexible       = db.Column(db.Boolean, default=False)
    window_start_mins = db.Column(db.Integer, nullable=True)
    window_end_mins   = db.Column(db.Integer, nullable=True)
    created_at   = db.Column(db.DateTime, default=_utcnow)

    # Self-referential relationship for multi-activity parent/child
    parent = db.relationship("SegmentCode", remote_side=[id], backref=db.backref("subactivities", lazy="dynamic"))

    def get_external_ids(self):
        """Return external IDs as a Python list."""
        if not self.external_ids:
            return []
        try:
            import json
            return json.loads(self.external_ids)
        except (json.JSONDecodeError, TypeError):
            return []

    def set_external_ids(self, ids_list):
        """Set external IDs from a Python list."""
        import json
        self.external_ids = json.dumps(ids_list) if ids_list else None

    def to_dict(self):
        return {
            "id": self.id, "code": self.code, "label": self.label,
            "color": self.color, "is_productive": self.is_productive,
            "is_paid": self.is_paid, "is_default": self.is_default,
            "sort_order": self.sort_order, "is_active": self.is_active,
            "activity_type": self.activity_type or "presence",
            "activity_category": self.activity_category or "status",
            "official_name": self.official_name or "",
            "abbreviation": self.abbreviation or "",
            "shortcut": self.shortcut or "",
            "external_ids": self.get_external_ids(),
            "parent_id": self.parent_id,
            "is_multi_activity": self.is_multi_activity,
            "is_replaceable": self.is_replaceable if self.is_replaceable is not None else True,
            "is_plannable": self.is_plannable if self.is_plannable is not None else True,
            "importance": self.importance or 50,
            "priority": self.priority or 50,
            "comply_rest_period": self.comply_rest_period if self.comply_rest_period is not None else True,
            "allow_overstaffing_zero": self.allow_overstaffing_zero or False,
            "is_requestable": self.is_requestable or False,
            "is_exchangeable": self.is_exchangeable or False,
            "allow_full_day": self.allow_full_day or False,
            "special_handling": self.special_handling or False,
            "can_be_day_status": self.can_be_day_status or False,
            "offset_mins": self.offset_mins,
            "duration_mins": self.duration_mins,
            "is_flexible": self.is_flexible,
            "window_start_mins": self.window_start_mins,
            "window_end_mins": self.window_end_mins,
            "skills": [s.to_dict() for s in self.activity_skills],
            "external_statuses": [m.to_dict() for m in self.external_status_mappings],
        }


class ActivitySkill(db.Model):
    """Skills linked to an activity with weighting.
    E.g. 'SS Sales Combined' weight 100 on the SS Sales activity."""
    __tablename__ = "activity_skills"

    id              = db.Column(db.Integer, primary_key=True)
    segment_code_id = db.Column(db.Integer, db.ForeignKey("segment_codes.id"), nullable=False)
    name            = db.Column(db.String(120), nullable=False)
    weighting       = db.Column(db.Integer, default=100)               # 0-100 skill weight
    created_at      = db.Column(db.DateTime, default=_utcnow)

    segment_code = db.relationship("SegmentCode", backref=db.backref("activity_skills", lazy="select", cascade="all, delete-orphan"))

    def to_dict(self):
        return {"id": self.id, "name": self.name, "weighting": self.weighting}


class ExternalStatusMapping(db.Model):
    """Maps external ACD agent statuses to internal activities for adherence tracking.
    E.g. ACD status code '205' → 'Break' activity."""
    __tablename__ = "external_status_mappings"

    id              = db.Column(db.Integer, primary_key=True)
    segment_code_id = db.Column(db.Integer, db.ForeignKey("segment_codes.id"), nullable=False)
    external_status = db.Column(db.String(100), nullable=False)        # ACD status code/name
    description     = db.Column(db.String(200), nullable=True)         # human-readable description
    created_at      = db.Column(db.DateTime, default=_utcnow)

    segment_code = db.relationship("SegmentCode", backref=db.backref("external_status_mappings", lazy="select", cascade="all, delete-orphan"))

    def to_dict(self):
        return {"id": self.id, "external_status": self.external_status, "description": self.description or ""}


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
    created_at  = db.Column(db.DateTime, default=_utcnow)

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
    created_at  = db.Column(db.DateTime, default=_utcnow)

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
    created_at   = db.Column(db.DateTime, default=_utcnow)

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
    created_at       = db.Column(db.DateTime, default=_utcnow)

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
    updated_at            = db.Column(db.DateTime, default=_utcnow, onupdate=_utcnow)

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
    created_at    = db.Column(db.DateTime, default=_utcnow)

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
    created_at        = db.Column(db.DateTime, default=_utcnow)

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
    created_at            = db.Column(db.DateTime, default=_utcnow)

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
    date          = db.Column(db.Date, nullable=False, index=True)
    is_full_day   = db.Column(db.Boolean, default=True)
    start_time    = db.Column(db.String(5), nullable=True)       # for partial-day holidays
    end_time      = db.Column(db.String(5), nullable=True)
    is_paid       = db.Column(db.Boolean, default=True)
    affects_forecast = db.Column(db.Boolean, default=True)       # adjust forecast volume
    volume_factor = db.Column(db.Float, default=0.0)             # 0 = closed, 0.5 = half volume
    year          = db.Column(db.Integer, nullable=False, index=True)
    is_recurring  = db.Column(db.Boolean, default=True)          # auto-create for next year
    created_at    = db.Column(db.DateTime, default=_utcnow)

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
    created_at  = db.Column(db.DateTime, default=_utcnow)

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
    created_at    = db.Column(db.DateTime, default=_utcnow)

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
    created_at   = db.Column(db.DateTime, default=_utcnow)

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
    created_at        = db.Column(db.DateTime, default=_utcnow)

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
    updated_at    = db.Column(db.DateTime, default=_utcnow, onupdate=_utcnow)

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
    updated_at     = db.Column(db.DateTime, default=_utcnow, onupdate=_utcnow)

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
# EMPLOYEE ↔ PLANNING UNIT (many-to-many with priority & validity)
# ═══════════════════════════════════════════════════════════════

class EmployeePlanningUnit(db.Model):
    """Assigns an employee to a planning unit with a priority and
    optional validity period. Priority 1 = primary unit used by
    schedule optimization. Lower-priority units allow manual scheduling."""
    __tablename__ = "employee_planning_units"

    id               = db.Column(db.Integer, primary_key=True)
    employee_id      = db.Column(db.Integer, db.ForeignKey("employees.id", ondelete="CASCADE"),
                                 nullable=False, index=True)
    planning_unit_id = db.Column(db.Integer, db.ForeignKey("planning_units.id", ondelete="CASCADE"),
                                 nullable=False, index=True)
    priority         = db.Column(db.Integer, default=1)          # 1 = primary
    valid_from       = db.Column(db.Date, nullable=True)
    valid_to         = db.Column(db.Date, nullable=True)
    created_at       = db.Column(db.DateTime, default=_utcnow)

    employee      = db.relationship("Employee", backref="planning_unit_assignments")
    planning_unit = db.relationship("PlanningUnit", backref="employee_pu_assignments")

    __table_args__ = (
        db.UniqueConstraint("employee_id", "planning_unit_id", name="uq_emp_pu"),
    )

    def to_dict(self):
        pu = self.planning_unit
        return {
            "id": self.id,
            "employee_id": self.employee_id,
            "planning_unit_id": self.planning_unit_id,
            "planning_unit_name": pu.name if pu else "",
            "priority": self.priority,
            "valid_from": self.valid_from.isoformat() if self.valid_from else "",
            "valid_to": self.valid_to.isoformat() if self.valid_to else "",
        }


# ═══════════════════════════════════════════════════════════════
# EMPLOYEE ↔ WORK TIME PATTERN MODEL (with reference date)
# ═══════════════════════════════════════════════════════════════

class EmployeeWorkTimePattern(db.Model):
    """Assigns a work time pattern model to an employee with a reference
    date (when the rotation cycle starts) and optional validity period."""
    __tablename__ = "employee_work_time_patterns"

    id                       = db.Column(db.Integer, primary_key=True)
    employee_id              = db.Column(db.Integer, db.ForeignKey("employees.id", ondelete="CASCADE"),
                                         nullable=False, index=True)
    work_time_pattern_model_id = db.Column(db.Integer, db.ForeignKey("work_time_pattern_models.id", ondelete="CASCADE"),
                                            nullable=False, index=True)
    reference_date           = db.Column(db.Date, nullable=True)     # cycle start date
    valid_from               = db.Column(db.Date, nullable=True)
    valid_to                 = db.Column(db.Date, nullable=True)
    created_at               = db.Column(db.DateTime, default=_utcnow)

    employee               = db.relationship("Employee", backref="work_time_pattern_assignments")
    work_time_pattern_model = db.relationship("WorkTimePatternModel", backref="employee_wtp_assignments")

    __table_args__ = (
        db.UniqueConstraint("employee_id", "work_time_pattern_model_id", name="uq_emp_wtpm"),
    )

    def to_dict(self):
        wtp = self.work_time_pattern_model
        return {
            "id": self.id,
            "employee_id": self.employee_id,
            "work_time_pattern_model_id": self.work_time_pattern_model_id,
            "work_time_pattern_model_name": wtp.name if wtp else "",
            "reference_date": self.reference_date.isoformat() if self.reference_date else "",
            "valid_from": self.valid_from.isoformat() if self.valid_from else "",
            "valid_to": self.valid_to.isoformat() if self.valid_to else "",
        }


# ═══════════════════════════════════════════════════════════════
# EMPLOYEE ↔ CONTRACT (with validity — replaces single FK)
# ═══════════════════════════════════════════════════════════════

class EmployeeContract(db.Model):
    """Assigns a contract to an employee with validity period.
    Allows tracking contract history (past, current, future)."""
    __tablename__ = "employee_contracts"

    id           = db.Column(db.Integer, primary_key=True)
    employee_id  = db.Column(db.Integer, db.ForeignKey("employees.id", ondelete="CASCADE"),
                             nullable=False, index=True)
    contract_id  = db.Column(db.Integer, db.ForeignKey("contracts.id", ondelete="CASCADE"),
                             nullable=False, index=True)
    valid_from   = db.Column(db.Date, nullable=True)
    valid_to     = db.Column(db.Date, nullable=True)
    created_at   = db.Column(db.DateTime, default=_utcnow)

    employee = db.relationship("Employee", backref="contract_assignments")
    contract = db.relationship("Contract", backref="employee_contract_assignments")

    def to_dict(self):
        c = self.contract
        return {
            "id": self.id,
            "employee_id": self.employee_id,
            "contract_id": self.contract_id,
            "contract_name": c.name if c else "",
            "contract_type": c.contract_type if c else "",
            "weekly_hours": c.weekly_hours if c else None,
            "valid_from": self.valid_from.isoformat() if self.valid_from else "",
            "valid_to": self.valid_to.isoformat() if self.valid_to else "",
        }


# ═══════════════════════════════════════════════════════════════
# SELECTIONS — Custom grouping of employees
# ═══════════════════════════════════════════════════════════════

class Selection(db.Model):
    """Named group of employees for filtering/reporting purposes.
    E.g. 'New Hires Q4', 'Night Shift Team', 'Training Group A'."""
    __tablename__ = "selections"

    id          = db.Column(db.Integer, primary_key=True)
    name        = db.Column(db.String(100), unique=True, nullable=False)
    description = db.Column(db.String(255), default="")
    is_active   = db.Column(db.Boolean, default=True)
    created_at  = db.Column(db.DateTime, default=_utcnow)

    members = db.relationship("SelectionMember", backref="selection",
                               cascade="all, delete-orphan", lazy="joined")

    def to_dict(self):
        return {
            "id": self.id, "name": self.name,
            "description": self.description,
            "is_active": self.is_active,
            "member_count": len(self.members),
        }


class SelectionMember(db.Model):
    """Links an employee to a selection."""
    __tablename__ = "selection_members"

    id           = db.Column(db.Integer, primary_key=True)
    selection_id = db.Column(db.Integer, db.ForeignKey("selections.id", ondelete="CASCADE"),
                             nullable=False, index=True)
    employee_id  = db.Column(db.Integer, db.ForeignKey("employees.id", ondelete="CASCADE"),
                             nullable=False, index=True)
    created_at   = db.Column(db.DateTime, default=_utcnow)

    employee = db.relationship("Employee", backref="selection_memberships")

    __table_args__ = (
        db.UniqueConstraint("selection_id", "employee_id", name="uq_sel_emp"),
    )

    def to_dict(self):
        emp = self.employee
        return {
            "id": self.id,
            "selection_id": self.selection_id,
            "employee_id": self.employee_id,
            "employee_name": emp.full_name if emp else "",
            "employee_eid": emp.employee_id if emp else "",
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
    uploaded_at      = db.Column(db.DateTime, default=_utcnow)

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
    created_at       = db.Column(db.DateTime, default=_utcnow)

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
    uploaded_at  = db.Column(db.DateTime, default=_utcnow)

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


# ═══════════════════════════════════════════════════════════════
# ACTIVITIES — What agents do (calls, email, break, training…)
# ═══════════════════════════════════════════════════════════════

class Activity(db.Model):
    """Schedulable activities — the building blocks of day models.
    Maps to WFM platform 'Activities' concept. Each activity has a type
    (presence = on-phone/productive, absence = break/lunch, meeting, training)
    and a linked segment code for color/labeling in the schedule view."""
    __tablename__ = "activities"

    id            = db.Column(db.Integer, primary_key=True)
    name          = db.Column(db.String(100), unique=True, nullable=False)
    short_name    = db.Column(db.String(20), default="")           # abbrev for compact views
    activity_type = db.Column(db.String(30), default="presence")   # presence | absence | meeting | training | other
    is_paid       = db.Column(db.Boolean, default=True)
    is_productive = db.Column(db.Boolean, default=True)            # counts toward productive time
    duration_mins = db.Column(db.Integer, nullable=True)           # default duration (NULL = variable)
    color         = db.Column(db.String(20), default="#6b7280")
    segment_code_id = db.Column(db.Integer, db.ForeignKey("segment_codes.id"), nullable=True)
    is_active     = db.Column(db.Boolean, default=True)
    sort_order    = db.Column(db.Integer, default=0)
    # ── Activity category ────────────────────────────────────
    activity_category = db.Column(db.String(20), default="status")  # status | lob
    # ── External ID mapping ──────────────────────────────────
    external_ids  = db.Column(db.Text, nullable=True)              # JSON array of external system IDs
    # ── Multi-activity support ───────────────────────────────
    parent_id     = db.Column(db.Integer, db.ForeignKey("activities.id"), nullable=True)
    is_multi_activity = db.Column(db.Boolean, default=False)       # True = parent with subactivities
    created_at    = db.Column(db.DateTime, default=_utcnow)

    segment_code = db.relationship("SegmentCode", backref="activities")
    parent = db.relationship("Activity", remote_side=[id], backref=db.backref("subactivities", lazy="dynamic"))

    def get_external_ids(self):
        if not self.external_ids:
            return []
        try:
            import json
            return json.loads(self.external_ids)
        except (json.JSONDecodeError, TypeError):
            return []

    def set_external_ids(self, ids_list):
        import json
        self.external_ids = json.dumps(ids_list) if ids_list else None

    def to_dict(self):
        sc = self.segment_code
        return {
            "id": self.id, "name": self.name, "short_name": self.short_name,
            "activity_type": self.activity_type, "is_paid": self.is_paid,
            "is_productive": self.is_productive, "duration_mins": self.duration_mins,
            "color": self.color, "segment_code_id": self.segment_code_id,
            "segment_code": sc.label if sc else "",
            "is_active": self.is_active, "sort_order": self.sort_order,
            "activity_category": self.activity_category or "status",
            "external_ids": self.get_external_ids(),
            "parent_id": self.parent_id,
            "is_multi_activity": self.is_multi_activity,
        }


# ═══════════════════════════════════════════════════════════════
# CONTRACTS — Employment contract templates
# ═══════════════════════════════════════════════════════════════

class Contract(db.Model):
    """Contract templates defining work hours, break rules, and scheduling
    constraints. Employees reference a contract instead of having hours
    scattered across their profile. Maps to WFM platform 'Contracts'.
    Enhanced to match PeopleWare contract depth: General, Work Time, Scheduling."""
    __tablename__ = "contracts"

    id                  = db.Column(db.Integer, primary_key=True)
    name                = db.Column(db.String(100), unique=True, nullable=False)
    abbreviation        = db.Column(db.String(50), nullable=True)
    color               = db.Column(db.String(7), default="#000000")
    contract_type       = db.Column(db.String(30), default="full_time")  # full_time | part_time | casual | temp
    days_per_week       = db.Column(db.Integer, default=5)
    workdays_calculation = db.Column(db.String(20), default="flexible")  # flexible | fixed

    # ── Work Time Guidelines ──────────────────────────────────
    daily_hours_min     = db.Column(db.Float, nullable=True)             # HH:MM stored as hours
    daily_hours_target  = db.Column(db.Float, nullable=True)
    daily_hours_max     = db.Column(db.Float, nullable=True)
    weekly_hours_min    = db.Column(db.Float, nullable=True)
    weekly_hours_target = db.Column(db.Float, default=40.0)
    weekly_hours        = db.Column(db.Float, default=40.0)              # weekly_hours_max (kept name for compat)
    monthly_hours_max   = db.Column(db.Float, nullable=True)

    # ── Work Hours Per Day (optional per-day overrides, HH:MM as string) ──
    work_hours_mon      = db.Column(db.String(5), nullable=True)
    work_hours_tue      = db.Column(db.String(5), nullable=True)
    work_hours_wed      = db.Column(db.String(5), nullable=True)
    work_hours_thu      = db.Column(db.String(5), nullable=True)
    work_hours_fri      = db.Column(db.String(5), nullable=True)
    work_hours_sat      = db.Column(db.String(5), nullable=True)
    work_hours_sun      = db.Column(db.String(5), nullable=True)

    # ── AutoScheduler Parameters ──────────────────────────────
    use_target_work_times     = db.Column(db.Boolean, default=False)
    schedule_after_day_off    = db.Column(db.Boolean, default=False)
    min_days_off_per_week     = db.Column(db.Integer, nullable=True)
    min_consec_days_off_week  = db.Column(db.Integer, nullable=True)
    max_consecutive_days_off  = db.Column(db.Integer, nullable=True)
    max_consecutive_days      = db.Column(db.Integer, default=7)
    min_days_per_week         = db.Column(db.Integer, nullable=True)
    max_days_per_week         = db.Column(db.Integer, default=6)
    weeks_max_1_sat           = db.Column(db.Integer, nullable=True)
    min_rest_hours            = db.Column(db.Float, default=10.0)        # between shifts

    # ── Scheduling Parameters (numbered rules from PeopleWare) ─
    max_saturdays_per_month   = db.Column(db.Integer, nullable=True)
    max_sundays_per_month     = db.Column(db.Integer, nullable=True)     # (kept for legacy)
    min_net_work_hours_day    = db.Column(db.String(5), nullable=True)   # HH:MM
    max_net_work_hours_day    = db.Column(db.String(5), nullable=True)
    rest_between_workdays     = db.Column(db.String(5), nullable=True)   # HH:MM e.g. "10:00"
    max_activity_duration     = db.Column(db.String(5), nullable=True)   # HH:MM e.g. "12:00"
    exclude_illness           = db.Column(db.Boolean, default=False)
    exclude_vacation          = db.Column(db.Boolean, default=False)
    min_gap_between_activities = db.Column(db.String(5), nullable=True)  # HH:MM e.g. "00:30"
    max_gap_between_activities = db.Column(db.String(5), nullable=True)  # HH:MM e.g. "01:15"
    max_shifts_per_day        = db.Column(db.Integer, nullable=True)     # e.g. 2
    max_work_hours_per_day_flag = db.Column(db.Boolean, default=False)
    max_work_hours_include_activities = db.Column(db.Boolean, default=False)
    max_work_hours_include_day_models = db.Column(db.Boolean, default=False)
    min_weekends_off_month    = db.Column(db.Integer, nullable=True)
    max_working_days_per_week = db.Column(db.Integer, nullable=True)
    max_consecutive_working_days = db.Column(db.Integer, nullable=True)
    exclude_illness_consec    = db.Column(db.Boolean, default=False)
    exclude_vacation_consec   = db.Column(db.Boolean, default=False)
    min_consec_days_off_week_sched = db.Column(db.Integer, nullable=True)
    max_work_hours_24h        = db.Column(db.String(5), nullable=True)   # HH:MM
    min_days_off_sat_work     = db.Column(db.Integer, nullable=True)
    min_days_off_sun_work     = db.Column(db.Integer, nullable=True)
    overtime_threshold_consec = db.Column(db.String(5), nullable=True)   # HH:MM
    overtime_num_weeks        = db.Column(db.Integer, nullable=True)
    no_schedule_on_holidays   = db.Column(db.Boolean, default=False)
    max_night_shifts_week     = db.Column(db.Integer, nullable=True)
    max_night_shifts_month    = db.Column(db.Integer, nullable=True)
    max_consec_night_shifts   = db.Column(db.Integer, nullable=True)
    weekly_rest_no_full_day   = db.Column(db.String(5), nullable=True)   # HH:MM
    weekly_rest_full_day      = db.Column(db.String(5), nullable=True)   # HH:MM
    avoid_overlap_sun_rule    = db.Column(db.Boolean, default=False)
    max_activity_duration_2   = db.Column(db.String(5), nullable=True)   # excl. absence type
    max_sun_holidays_month    = db.Column(db.Integer, nullable=True)
    comp_eligibility_weekend  = db.Column(db.Integer, nullable=True)     # days
    rest_after_holiday_no_full = db.Column(db.String(5), nullable=True)
    rest_after_holiday_full   = db.Column(db.String(5), nullable=True)
    max_sundays_in_row        = db.Column(db.Integer, nullable=True)
    max_weekends_in_row       = db.Column(db.Integer, nullable=True)
    max_day_models_24h        = db.Column(db.Integer, nullable=True)
    max_work_time_deviation   = db.Column(db.String(5), nullable=True)

    # ── Legacy / general ──────────────────────────────────────
    break_duration_mins = db.Column(db.Integer, default=30)
    break_after_hours   = db.Column(db.Float, default=4.0)
    lunch_duration_mins = db.Column(db.Integer, default=30)
    overtime_eligible   = db.Column(db.Boolean, default=True)
    schedule_rule_id    = db.Column(db.Integer, db.ForeignKey("schedule_rules.id"), nullable=True)
    is_active           = db.Column(db.Boolean, default=True)
    created_at          = db.Column(db.DateTime, default=_utcnow)

    schedule_rule = db.relationship("ScheduleRule", backref="contracts")

    def to_dict(self):
        sr = self.schedule_rule
        d = {c.name: getattr(self, c.name) for c in self.__table__.columns}
        d["schedule_rule"] = sr.name if sr else ""
        return d


# ═══════════════════════════════════════════════════════════════
# DAY MODELS — Shift structures for a given day type
# ═══════════════════════════════════════════════════════════════

class DayModel(db.Model):
    """A day model defines a specific shift shape for a day — start/end time,
    activities placed within it. WFM platform equivalent: 'Day models'.
    A day model references a shift template for its time window and adds
    activity placements (breaks, lunches, meetings) at specific offsets."""
    __tablename__ = "day_models"

    id               = db.Column(db.Integer, primary_key=True)
    name             = db.Column(db.String(100), unique=True, nullable=False)
    abbreviation     = db.Column(db.String(20), nullable=True)       # e.g. "FT 10.5"
    shift_template_id = db.Column(db.Integer, db.ForeignKey("shift_templates.id"), nullable=True)
    start_time       = db.Column(db.String(5), nullable=False, default="08:00")
    end_time         = db.Column(db.String(5), nullable=False, default="16:30")
    paid_hours       = db.Column(db.Float, default=8.0)
    total_hours      = db.Column(db.Float, default=8.5)              # total duration incl. unpaid break
    model_type       = db.Column(db.String(20), default="Fixed")     # Fixed | Flexible
    color            = db.Column(db.String(7), default="#4472C4")    # hex color for UI
    # JSON array: [{activity_id, offset_mins, duration_mins, is_flexible, window_start, window_end}]
    activities_json  = db.Column(db.Text, default="[]")
    day_type         = db.Column(db.String(20), default="any")   # weekday | saturday | sunday | holiday | any
    planning_unit_id = db.Column(db.Integer, db.ForeignKey("planning_units.id"), nullable=True)
    is_active        = db.Column(db.Boolean, default=True)
    sort_order       = db.Column(db.Integer, default=0)
    created_at       = db.Column(db.DateTime, default=_utcnow)

    shift_template = db.relationship("ShiftTemplate", backref="day_models")
    planning_unit  = db.relationship("PlanningUnit", backref="day_models")

    def to_dict(self):
        import json as _json
        st = self.shift_template
        pu = self.planning_unit
        return {
            "id": self.id, "name": self.name,
            "abbreviation": self.abbreviation or "",
            "shift_template_id": self.shift_template_id,
            "shift_template": st.name if st else "",
            "start_time": self.start_time, "end_time": self.end_time,
            "paid_hours": self.paid_hours,
            "total_hours": self.total_hours or self.paid_hours,
            "model_type": self.model_type or "Fixed",
            "color": self.color or "#4472C4",
            "activities": _json.loads(self.activities_json) if self.activities_json else [],
            "day_type": self.day_type,
            "planning_unit_id": self.planning_unit_id,
            "planning_unit": pu.name if pu else "All",
            "is_active": self.is_active, "sort_order": self.sort_order,
        }


# ═══════════════════════════════════════════════════════════════
# WEEK TIME PATTERNS — Group day models for quartile/role scheduling
# ═══════════════════════════════════════════════════════════════

class WeekTimePattern(db.Model):
    """A week time pattern groups eligible day models for a specific
    role or quartile. WFM platform equivalent: 'Week time patterns'.
    Can be used as day-of-week mapping OR as a pool of eligible shifts."""
    __tablename__ = "week_time_patterns"

    id                  = db.Column(db.Integer, primary_key=True)
    name                = db.Column(db.String(100), unique=True, nullable=False)
    abbreviation        = db.Column(db.String(20), nullable=True)       # e.g. "SS Q1"
    # JSON: {mon: day_model_id, tue: day_model_id, ..., sun: day_model_id}
    # null for a day = day off — used for day-of-week mapping mode
    days_json           = db.Column(db.Text, nullable=False, default="{}")
    total_hours         = db.Column(db.Float, default=40.0)
    max_exception_days  = db.Column(db.Integer, default=0)              # max exception days per week
    planning_unit_id    = db.Column(db.Integer, db.ForeignKey("planning_units.id"), nullable=True)
    is_active           = db.Column(db.Boolean, default=True)
    created_at          = db.Column(db.DateTime, default=_utcnow)

    planning_unit  = db.relationship("PlanningUnit", backref="week_time_patterns")
    assigned_day_models = db.relationship("WeekTimePatternDayModel", backref="week_time_pattern",
                                          cascade="all, delete-orphan",
                                          order_by="WeekTimePatternDayModel.position")

    def to_dict(self):
        import json as _json
        pu = self.planning_unit
        return {
            "id": self.id, "name": self.name,
            "abbreviation": self.abbreviation or "",
            "days": _json.loads(self.days_json) if self.days_json else {},
            "total_hours": self.total_hours,
            "max_exception_days": self.max_exception_days,
            "planning_unit_id": self.planning_unit_id,
            "planning_unit": pu.name if pu else "All",
            "assigned_day_models": [adm.to_dict() for adm in self.assigned_day_models],
            "is_active": self.is_active,
        }


class WeekTimePatternDayModel(db.Model):
    """Junction table linking day models to a week time pattern with position."""
    __tablename__ = "week_time_pattern_day_models"

    id                    = db.Column(db.Integer, primary_key=True)
    week_time_pattern_id  = db.Column(db.Integer, db.ForeignKey("week_time_patterns.id", ondelete="CASCADE"),
                                       nullable=False, index=True)
    day_model_id          = db.Column(db.Integer, db.ForeignKey("day_models.id", ondelete="CASCADE"),
                                       nullable=False, index=True)
    position              = db.Column(db.Integer, default=0)

    day_model = db.relationship("DayModel")

    def to_dict(self):
        dm = self.day_model
        return {
            "id": self.id,
            "day_model_id": self.day_model_id,
            "day_model_name": dm.name if dm else "",
            "start_time": dm.start_time if dm else "",
            "end_time": dm.end_time if dm else "",
            "color": (dm.color or "#4472C4") if dm else "#4472C4",
            "position": self.position,
        }


# ═══════════════════════════════════════════════════════════════
# WORK TIME PATTERN MODELS — Combine week patterns for scheduling
# ═══════════════════════════════════════════════════════════════

class WorkTimePatternModel(db.Model):
    """Groups one or more week time patterns into a schedulable model.
    WFM platform equivalent: 'Work time pattern models'. Assigned to a
    planning unit to tell the scheduler which shift shapes to use."""
    __tablename__ = "work_time_pattern_models"

    id                = db.Column(db.Integer, primary_key=True)
    name              = db.Column(db.String(100), unique=True, nullable=False)
    abbreviation      = db.Column(db.String(20), nullable=True)       # e.g. "SS"
    model_type        = db.Column(db.String(20), default="Fixed")     # Fixed | Flexible
    description       = db.Column(db.Text, nullable=True)
    # JSON array: [week_time_pattern_id, ...] — ordered list of week patterns
    # If multiple, they cycle (week 1 uses pattern[0], week 2 uses pattern[1], etc.)
    patterns_json     = db.Column(db.Text, nullable=False, default="[]")
    exception_wtp_id  = db.Column(db.Integer, db.ForeignKey("week_time_patterns.id"), nullable=True)
    planning_unit_id  = db.Column(db.Integer, db.ForeignKey("planning_units.id"), nullable=True)
    is_active         = db.Column(db.Boolean, default=True)
    created_at        = db.Column(db.DateTime, default=_utcnow)

    planning_unit     = db.relationship("PlanningUnit", backref="work_time_pattern_models")
    exception_wtp     = db.relationship("WeekTimePattern", foreign_keys=[exception_wtp_id])
    assigned_patterns = db.relationship("WorkTimePatternModelPattern", backref="work_time_pattern_model",
                                         cascade="all, delete-orphan",
                                         order_by="WorkTimePatternModelPattern.position")

    def to_dict(self):
        import json as _json
        pu = self.planning_unit
        ewtp = self.exception_wtp
        return {
            "id": self.id, "name": self.name,
            "abbreviation": self.abbreviation or "",
            "model_type": self.model_type or "Fixed",
            "description": self.description or "",
            "patterns": _json.loads(self.patterns_json) if self.patterns_json else [],
            "exception_wtp_id": self.exception_wtp_id,
            "exception_wtp_name": ewtp.name if ewtp else "",
            "planning_unit_id": self.planning_unit_id,
            "planning_unit": pu.name if pu else "All",
            "assigned_patterns": [ap.to_dict() for ap in self.assigned_patterns],
            "is_active": self.is_active,
        }


class WorkTimePatternModelPattern(db.Model):
    """Junction table linking week time patterns to a work time pattern model with position."""
    __tablename__ = "work_time_pattern_model_patterns"

    id                          = db.Column(db.Integer, primary_key=True)
    work_time_pattern_model_id  = db.Column(db.Integer, db.ForeignKey("work_time_pattern_models.id", ondelete="CASCADE"),
                                             nullable=False, index=True)
    week_time_pattern_id        = db.Column(db.Integer, db.ForeignKey("week_time_patterns.id", ondelete="CASCADE"),
                                             nullable=False, index=True)
    position                    = db.Column(db.Integer, default=0)

    week_time_pattern = db.relationship("WeekTimePattern")

    def to_dict(self):
        wtp = self.week_time_pattern
        return {
            "id": self.id,
            "week_time_pattern_id": self.week_time_pattern_id,
            "week_time_pattern_name": wtp.name if wtp else "",
            "position": self.position,
        }


# ═══════════════════════════════════════════════════════════════
# EMPLOYEE QUARTILE ASSIGNMENT
# ═══════════════════════════════════════════════════════════════

class EmployeeQuartile(db.Model):
    """Assigns a performance quartile (Q1-Q4) to an employee per LOB/planning unit.
    Q1 = top performers (prime shifts), Q4 = lowest (remaining shifts)."""
    __tablename__ = "employee_quartiles"

    id               = db.Column(db.Integer, primary_key=True)
    employee_id      = db.Column(db.Integer, db.ForeignKey("employees.id", ondelete="CASCADE"),
                                  nullable=False, index=True)
    planning_unit_id = db.Column(db.Integer, db.ForeignKey("planning_units.id"), nullable=True)
    quartile         = db.Column(db.Integer, nullable=False, default=4)  # 1-4
    effective_date   = db.Column(db.Date, nullable=True)
    notes            = db.Column(db.Text, nullable=True)
    created_at       = db.Column(db.DateTime, default=_utcnow)
    updated_at       = db.Column(db.DateTime, default=_utcnow, onupdate=_utcnow)

    employee      = db.relationship("Employee", backref="quartile_assignments")
    planning_unit = db.relationship("PlanningUnit", backref="employee_quartiles")

    __table_args__ = (
        db.UniqueConstraint("employee_id", "planning_unit_id", name="uq_emp_quartile_pu"),
    )

    def to_dict(self):
        emp = self.employee
        pu = self.planning_unit
        return {
            "id": self.id,
            "employee_id": self.employee_id,
            "employee_name": emp.full_name if emp else "",
            "employee_ext_id": emp.employee_id if emp else "",
            "planning_unit_id": self.planning_unit_id,
            "planning_unit": pu.name if pu else "All",
            "quartile": self.quartile,
            "effective_date": self.effective_date.isoformat() if self.effective_date else "",
            "notes": self.notes or "",
        }


# ═══════════════════════════════════════════════════════════════
# SHIFT SEQUENCES
# ═══════════════════════════════════════════════════════════════

class ShiftSequence(db.Model):
    """A repeating pattern of day models spanning one or more weeks."""
    __tablename__ = "shift_sequences"

    id          = db.Column(db.Integer, primary_key=True)
    name        = db.Column(db.String(120), unique=True, nullable=False)
    cycle_weeks = db.Column(db.Integer, default=1)
    is_active   = db.Column(db.Boolean, default=True)
    created_at  = db.Column(db.DateTime, default=_utcnow)

    rows = db.relationship("ShiftSequenceRow", backref="shift_sequence",
                           cascade="all, delete-orphan", lazy="joined",
                           order_by="ShiftSequenceRow.position")

    def to_dict(self):
        return {
            "id": self.id, "name": self.name,
            "cycle_weeks": self.cycle_weeks,
            "is_active": self.is_active,
            "row_count": len(self.rows),
        }


class ShiftSequenceRow(db.Model):
    """One row within a shift sequence — a named pattern line
    containing day-model assignments per day per week (stored as JSON)."""
    __tablename__ = "shift_sequence_rows"

    id                = db.Column(db.Integer, primary_key=True)
    shift_sequence_id = db.Column(db.Integer,
                                  db.ForeignKey("shift_sequences.id", ondelete="CASCADE"),
                                  nullable=False, index=True)
    name              = db.Column(db.String(80), default="")
    position          = db.Column(db.Integer, default=0)
    pattern_json      = db.Column(db.Text, default="{}")
    created_at        = db.Column(db.DateTime, default=_utcnow)

    def to_dict(self):
        import json as _json
        return {
            "id": self.id,
            "name": self.name,
            "position": self.position,
            "pattern": _json.loads(self.pattern_json) if self.pattern_json else {},
        }


# ═══════════════════════════════════════════════════════════════
# PLANNING CALENDARS & DAY TYPES
# ═══════════════════════════════════════════════════════════════

class DayType(db.Model):
    """Reusable label for special days — holidays, campaigns, etc."""
    __tablename__ = "day_types"

    id         = db.Column(db.Integer, primary_key=True)
    name       = db.Column(db.String(80), unique=True, nullable=False)
    color      = db.Column(db.String(10), default="#ef4444")
    is_holiday = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=_utcnow)

    def to_dict(self):
        return {
            "id": self.id, "name": self.name,
            "color": self.color, "is_holiday": self.is_holiday,
        }


class PlanningCalendar(db.Model):
    """A calendar of special days assigned to planning units."""
    __tablename__ = "planning_calendars"

    id         = db.Column(db.Integer, primary_key=True)
    name       = db.Column(db.String(120), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=_utcnow)

    entries = db.relationship("CalendarEntry", backref="calendar",
                              cascade="all, delete-orphan", lazy="dynamic",
                              order_by="CalendarEntry.date")

    def to_dict(self):
        return {
            "id": self.id, "name": self.name,
            "entry_count": self.entries.count(),
        }


class CalendarEntry(db.Model):
    """One date in a planning calendar marked with a day type."""
    __tablename__ = "calendar_entries"

    id           = db.Column(db.Integer, primary_key=True)
    calendar_id  = db.Column(db.Integer,
                             db.ForeignKey("planning_calendars.id", ondelete="CASCADE"),
                             nullable=False, index=True)
    day_type_id  = db.Column(db.Integer,
                             db.ForeignKey("day_types.id", ondelete="CASCADE"),
                             nullable=False, index=True)
    date         = db.Column(db.Date, nullable=False)
    created_at   = db.Column(db.DateTime, default=_utcnow)

    day_type = db.relationship("DayType")

    __table_args__ = (
        db.UniqueConstraint("calendar_id", "date", name="uq_cal_date"),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "calendar_id": self.calendar_id,
            "day_type_id": self.day_type_id,
            "date": self.date.isoformat() if self.date else "",
        }


# ═══════════════════════════════════════════════════════════════
# EMPLOYEE ↔ SHIFT SEQUENCE (with reference date & validity)
# ═══════════════════════════════════════════════════════════════

class EmployeeShiftSequence(db.Model):
    """Assigns a shift sequence to an employee with a reference date
    (when the rotation cycle starts) and optional validity period."""
    __tablename__ = "employee_shift_sequences"

    id                = db.Column(db.Integer, primary_key=True)
    employee_id       = db.Column(db.Integer, db.ForeignKey("employees.id", ondelete="CASCADE"),
                                  nullable=False, index=True)
    shift_sequence_id = db.Column(db.Integer, db.ForeignKey("shift_sequences.id", ondelete="CASCADE"),
                                  nullable=False, index=True)
    row_index         = db.Column(db.Integer, default=0)          # which row in the sequence
    reference_date    = db.Column(db.Date, nullable=True)         # cycle start date
    valid_from        = db.Column(db.Date, nullable=True)
    valid_to          = db.Column(db.Date, nullable=True)
    created_at        = db.Column(db.DateTime, default=_utcnow)

    employee       = db.relationship("Employee", backref="shift_sequence_assignments")
    shift_sequence = db.relationship("ShiftSequence", backref="employee_ss_assignments")

    __table_args__ = (
        db.UniqueConstraint("employee_id", "shift_sequence_id", name="uq_emp_ss"),
    )

    @property
    def row(self):
        """Return the ShiftSequenceRow matching this assignment's row_index."""
        return ShiftSequenceRow.query.filter_by(
            shift_sequence_id=self.shift_sequence_id,
            position=self.row_index or 0
        ).first()

    def to_dict(self):
        ss = self.shift_sequence
        return {
            "id": self.id,
            "employee_id": self.employee_id,
            "shift_sequence_id": self.shift_sequence_id,
            "shift_sequence_name": ss.name if ss else "",
            "row_index": self.row_index,
            "reference_date": self.reference_date.isoformat() if self.reference_date else "",
            "valid_from": self.valid_from.isoformat() if self.valid_from else "",
            "valid_to": self.valid_to.isoformat() if self.valid_to else "",
        }


# ═══════════════════════════════════════════════════════════════
# COACHING SESSIONS
# ═══════════════════════════════════════════════════════════════

class CoachingSession(db.Model):
    __tablename__ = "coaching_sessions"

    id            = db.Column(db.Integer, primary_key=True)
    employee_id   = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=False, index=True)
    coach_name    = db.Column(db.String(200), nullable=False)
    session_date  = db.Column(db.Date, nullable=False)
    session_time  = db.Column(db.String(5))   # HH:MM
    duration_mins = db.Column(db.Integer, default=30)
    topic         = db.Column(db.String(200))
    category      = db.Column(db.String(50), default="general")  # quality, adherence, performance, general
    quality_score = db.Column(db.Float)       # related quality score if applicable
    notes         = db.Column(db.Text)
    outcome       = db.Column(db.String(100)) # completed, rescheduled, cancelled, no-show
    follow_up     = db.Column(db.Text)
    created_at    = db.Column(db.DateTime, default=db.func.now())

    employee = db.relationship("Employee", backref="coaching_sessions")

    def to_dict(self):
        emp = self.employee
        return {
            "id": self.id,
            "employee_id": self.employee_id,
            "employee_name": f"{emp.first_name} {emp.last_name}" if emp else "",
            "coach_name": self.coach_name,
            "session_date": self.session_date.isoformat() if self.session_date else "",
            "session_time": self.session_time or "",
            "duration_mins": self.duration_mins,
            "topic": self.topic or "",
            "category": self.category or "general",
            "quality_score": self.quality_score,
            "notes": self.notes or "",
            "outcome": self.outcome or "",
            "follow_up": self.follow_up or "",
        }


# ═══════════════════════════════════════════════════════════════
# QUALITY EVALUATIONS
# ═══════════════════════════════════════════════════════════════

class QualityEvaluation(db.Model):
    __tablename__ = "quality_evaluations"

    id            = db.Column(db.Integer, primary_key=True)
    employee_id   = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=False, index=True)
    evaluator     = db.Column(db.String(200), nullable=False)
    eval_date     = db.Column(db.Date, nullable=False)
    interaction_id = db.Column(db.String(100))    # external interaction/call ID
    channel       = db.Column(db.String(50), default="voice")  # voice, chat, email
    lob           = db.Column(db.String(100))
    overall_score = db.Column(db.Float, nullable=False)
    # Sub-scores
    greeting_score     = db.Column(db.Float)
    knowledge_score    = db.Column(db.Float)
    process_score      = db.Column(db.Float)
    communication_score = db.Column(db.Float)
    resolution_score   = db.Column(db.Float)
    compliance_score   = db.Column(db.Float)
    # Metadata
    call_duration_secs = db.Column(db.Integer)
    disposition   = db.Column(db.String(100))    # resolved, escalated, callback, transfer
    notes         = db.Column(db.Text)
    critical_fail = db.Column(db.Boolean, default=False)
    created_at    = db.Column(db.DateTime, default=db.func.now())

    employee = db.relationship("Employee", backref="quality_evaluations")

    def to_dict(self):
        emp = self.employee
        return {
            "id": self.id,
            "employee_id": self.employee_id,
            "employee_name": f"{emp.first_name} {emp.last_name}" if emp else "",
            "evaluator": self.evaluator,
            "eval_date": self.eval_date.isoformat() if self.eval_date else "",
            "interaction_id": self.interaction_id or "",
            "channel": self.channel or "voice",
            "lob": self.lob or "",
            "overall_score": self.overall_score,
            "greeting_score": self.greeting_score,
            "knowledge_score": self.knowledge_score,
            "process_score": self.process_score,
            "communication_score": self.communication_score,
            "resolution_score": self.resolution_score,
            "compliance_score": self.compliance_score,
            "call_duration_secs": self.call_duration_secs,
            "disposition": self.disposition or "",
            "notes": self.notes or "",
            "critical_fail": self.critical_fail or False,
        }


class AnalyticsIntegration(db.Model):
    """Tracks external QM/analytics platform webhook configurations."""
    __tablename__ = "analytics_integrations"

    id           = db.Column(db.Integer, primary_key=True)
    name         = db.Column(db.String(200), nullable=False)        # e.g. "NICE CXone", "Calabrio"
    platform     = db.Column(db.String(100), nullable=False)        # nice, calabrio, custom
    webhook_key  = db.Column(db.String(100), nullable=False, unique=True)  # API key for auth
    is_active    = db.Column(db.Boolean, default=True)
    field_map    = db.Column(db.Text)    # JSON mapping of external fields → internal fields
    last_received = db.Column(db.DateTime)
    events_count = db.Column(db.Integer, default=0)
    created_at   = db.Column(db.DateTime, default=db.func.now())

    def to_dict(self):
        import json as _json
        return {
            "id": self.id,
            "name": self.name,
            "platform": self.platform,
            "webhook_key": self.webhook_key,
            "is_active": self.is_active,
            "field_map": _json.loads(self.field_map) if self.field_map else {},
            "last_received": self.last_received.isoformat() if self.last_received else None,
            "events_count": self.events_count,
        }


# ═══════════════════════════════════════════════════════════════
# SHIFT POSTS (Open-shift bidding — Phase 6.3)
# ═══════════════════════════════════════════════════════════════

class ShiftPost(db.Model):
    """An open shift posted for agents to bid on."""
    __tablename__ = "shift_posts"

    id              = db.Column(db.Integer, primary_key=True)
    schedule_date   = db.Column(db.Date, nullable=False)
    shift_start     = db.Column(db.Time, nullable=False)
    shift_end       = db.Column(db.Time, nullable=False)
    hours           = db.Column(db.Float, default=0)
    planning_unit_id = db.Column(db.Integer, db.ForeignKey("planning_units.id"), nullable=True)
    required_skills = db.Column(db.Text, default="")          # comma-separated
    status          = db.Column(db.String(20), default="open") # open | assigned | cancelled
    assigned_to     = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=True)
    posted_by       = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    notes           = db.Column(db.Text, default="")
    created_at      = db.Column(db.DateTime, default=_utcnow)

    planning_unit   = db.relationship("PlanningUnit")
    assigned_emp    = db.relationship("Employee", foreign_keys=[assigned_to])

    def to_dict(self):
        pu = self.planning_unit
        emp = self.assigned_emp
        return {
            "id": self.id,
            "date": self.schedule_date.isoformat(),
            "start": self.shift_start.strftime("%H:%M"),
            "end": self.shift_end.strftime("%H:%M"),
            "hours": self.hours,
            "lob": pu.name if pu else "",
            "required_skills": self.required_skills or "",
            "status": self.status,
            "assigned_to": emp.full_name if emp else None,
            "notes": self.notes or "",
            "created_at": self.created_at.isoformat() if self.created_at else "",
        }


class ShiftBid(db.Model):
    """An agent's bid on an open shift."""
    __tablename__ = "shift_bids"

    id           = db.Column(db.Integer, primary_key=True)
    shift_post_id = db.Column(db.Integer, db.ForeignKey("shift_posts.id", ondelete="CASCADE"), nullable=False)
    employee_id  = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=False)
    preference   = db.Column(db.Integer, default=1)            # 1=high, 2=med, 3=low
    status       = db.Column(db.String(20), default="pending")  # pending | accepted | declined
    created_at   = db.Column(db.DateTime, default=_utcnow)

    shift_post   = db.relationship("ShiftPost", backref="bids")
    employee     = db.relationship("Employee")

    __table_args__ = (
        db.UniqueConstraint("shift_post_id", "employee_id", name="uq_bid_post_emp"),
    )


# ═══════════════════════════════════════════════════════════════
# SHIFT SWAP REQUESTS (Phase 6.4)
# ═══════════════════════════════════════════════════════════════

class ShiftSwapRequest(db.Model):
    """Agent-to-agent shift swap with manager approval."""
    __tablename__ = "shift_swap_requests"

    id               = db.Column(db.Integer, primary_key=True)
    requester_id     = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=False)
    requester_schedule_id = db.Column(db.Integer, db.ForeignKey("schedules.id", ondelete="CASCADE"), nullable=False)
    target_id        = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=True)  # null = open offer
    target_schedule_id = db.Column(db.Integer, db.ForeignKey("schedules.id", ondelete="SET NULL"), nullable=True)
    status           = db.Column(db.String(20), default="pending")  # pending | accepted | approved | declined | cancelled
    reason           = db.Column(db.Text, default="")
    reviewed_by      = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at       = db.Column(db.DateTime, default=_utcnow)
    updated_at       = db.Column(db.DateTime, default=_utcnow, onupdate=_utcnow)

    requester        = db.relationship("Employee", foreign_keys=[requester_id])
    target           = db.relationship("Employee", foreign_keys=[target_id])
    requester_sched  = db.relationship("Schedule", foreign_keys=[requester_schedule_id])
    target_sched     = db.relationship("Schedule", foreign_keys=[target_schedule_id])

    def to_dict(self):
        rs = self.requester_sched
        ts = self.target_sched
        return {
            "id": self.id,
            "requester": self.requester.full_name if self.requester else "",
            "requester_date": rs.schedule_date.isoformat() if rs else "",
            "requester_shift": f"{rs.shift_start.strftime('%H:%M')}-{rs.shift_end.strftime('%H:%M')}" if rs and rs.shift_start else "",
            "target": self.target.full_name if self.target else "Open",
            "target_date": ts.schedule_date.isoformat() if ts else "",
            "target_shift": f"{ts.shift_start.strftime('%H:%M')}-{ts.shift_end.strftime('%H:%M')}" if ts and ts.shift_start else "",
            "status": self.status,
            "reason": self.reason or "",
            "created_at": self.created_at.isoformat() if self.created_at else "",
        }


# ═══════════════════════════════════════════════════════════════
# VTO / OT SIGN-UP BOARD (Phase 6.5)
# ═══════════════════════════════════════════════════════════════

class VTOOTPost(db.Model):
    """A voluntary time-off or overtime opportunity."""
    __tablename__ = "vto_ot_posts"

    id              = db.Column(db.Integer, primary_key=True)
    post_type       = db.Column(db.String(10), nullable=False)  # vto | ot
    schedule_date   = db.Column(db.Date, nullable=False)
    start_time      = db.Column(db.Time, nullable=True)
    end_time        = db.Column(db.Time, nullable=True)
    hours           = db.Column(db.Float, default=0)
    slots           = db.Column(db.Integer, default=1)
    slots_filled    = db.Column(db.Integer, default=0)
    planning_unit_id = db.Column(db.Integer, db.ForeignKey("planning_units.id"), nullable=True)
    status          = db.Column(db.String(20), default="open")  # open | filled | cancelled
    posted_by       = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    notes           = db.Column(db.Text, default="")
    created_at      = db.Column(db.DateTime, default=_utcnow)

    planning_unit   = db.relationship("PlanningUnit")

    def to_dict(self):
        pu = self.planning_unit
        return {
            "id": self.id,
            "type": self.post_type,
            "date": self.schedule_date.isoformat(),
            "start": self.start_time.strftime("%H:%M") if self.start_time else "",
            "end": self.end_time.strftime("%H:%M") if self.end_time else "",
            "hours": self.hours,
            "slots": self.slots,
            "slots_filled": self.slots_filled,
            "lob": pu.name if pu else "",
            "status": self.status,
            "notes": self.notes or "",
        }


class VTOOTSignup(db.Model):
    """An agent's sign-up for a VTO/OT opportunity."""
    __tablename__ = "vto_ot_signups"

    id           = db.Column(db.Integer, primary_key=True)
    post_id      = db.Column(db.Integer, db.ForeignKey("vto_ot_posts.id", ondelete="CASCADE"), nullable=False)
    employee_id  = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=False)
    status       = db.Column(db.String(20), default="confirmed")  # confirmed | cancelled
    created_at   = db.Column(db.DateTime, default=_utcnow)

    post         = db.relationship("VTOOTPost", backref="signups")
    employee     = db.relationship("Employee")

    __table_args__ = (
        db.UniqueConstraint("post_id", "employee_id", name="uq_vto_post_emp"),
    )


# ═══════════════════════════════════════════════════════════════
# NOTIFICATIONS
# ═══════════════════════════════════════════════════════════════

class Notification(db.Model):
    __tablename__ = "notifications"

    id          = db.Column(db.Integer, primary_key=True)
    user_id     = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    category    = db.Column(db.String(40), nullable=False, default="info")
    # Categories: schedule, pto, swap, quality, system, alert
    title       = db.Column(db.String(200), nullable=False)
    message     = db.Column(db.Text, nullable=True)
    link        = db.Column(db.String(500), nullable=True)   # optional deep-link
    is_read     = db.Column(db.Boolean, default=False)
    created_at  = db.Column(db.DateTime, default=_utcnow)

    user        = db.relationship("User", backref=db.backref("notifications", lazy="dynamic"))

    def to_dict(self):
        return {
            "id": self.id,
            "category": self.category,
            "title": self.title,
            "message": self.message,
            "link": self.link,
            "is_read": self.is_read,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# ── Audit Log ─────────────────────────────────────────────────

class AuditLog(db.Model):
    __tablename__ = "audit_log"

    id          = db.Column(db.Integer, primary_key=True)
    user_id     = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    action      = db.Column(db.String(80), nullable=False)
    # Actions: schedule_generated, schedule_published, pto_approved, pto_denied,
    #          employee_added, employee_updated, settings_changed, data_imported, etc.
    detail      = db.Column(db.Text, nullable=True)
    entity_type = db.Column(db.String(40), nullable=True)    # schedule, employee, pto, etc.
    entity_id   = db.Column(db.Integer, nullable=True)
    created_at  = db.Column(db.DateTime, default=_utcnow)

    user        = db.relationship("User", backref="audit_logs")

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "user_name": self.user.display_name if self.user else "System",
            "action": self.action,
            "detail": self.detail,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class TimeClock(db.Model):
    """Tracks employee clock-in / clock-out punches."""
    __tablename__ = "time_clock"

    id            = db.Column(db.Integer, primary_key=True)
    employee_id   = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=False, index=True)
    clock_in      = db.Column(db.DateTime, nullable=False)
    clock_out     = db.Column(db.DateTime, nullable=True)
    clock_in_note = db.Column(db.String(200), nullable=True)
    clock_out_note = db.Column(db.String(200), nullable=True)
    status        = db.Column(db.String(20), default="active")  # active, completed, edited
    total_hours   = db.Column(db.Float, nullable=True)
    date          = db.Column(db.Date, nullable=False, index=True)
    created_at    = db.Column(db.DateTime, default=_utcnow)
    edited_by     = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    edited_at     = db.Column(db.DateTime, nullable=True)

    employee = db.relationship("Employee", backref="time_punches")
    editor   = db.relationship("User", foreign_keys=[edited_by])


class Announcement(db.Model):
    """Team announcements posted by supervisors/admins, visible to agents."""
    __tablename__ = "announcements"

    id         = db.Column(db.Integer, primary_key=True)
    title      = db.Column(db.String(200), nullable=False)
    body       = db.Column(db.Text, nullable=True)
    category   = db.Column(db.String(40), default="general")  # general, urgent, schedule, policy
    is_pinned  = db.Column(db.Boolean, default=False)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=_utcnow)
    expires_at = db.Column(db.DateTime, nullable=True)

    author = db.relationship("User", foreign_keys=[created_by])


class ShiftNote(db.Model):
    """Supervisor shift handoff notes — passed between shifts."""
    __tablename__ = "shift_notes"

    id          = db.Column(db.Integer, primary_key=True)
    note_date   = db.Column(db.Date, nullable=False, index=True)
    shift_label = db.Column(db.String(40), default="")          # e.g. "AM", "PM", "Night"
    body        = db.Column(db.Text, nullable=False)
    category    = db.Column(db.String(40), default="general")   # general, staffing, escalation, system
    is_resolved = db.Column(db.Boolean, default=False)
    created_by  = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at  = db.Column(db.DateTime, default=_utcnow)

    author = db.relationship("User", foreign_keys=[created_by])


class EmployeeDocument(db.Model):
    """Files attached to an employee record (certs, write-ups, onboarding, etc.)."""
    __tablename__ = "employee_documents"

    id           = db.Column(db.Integer, primary_key=True)
    employee_id  = db.Column(db.Integer, db.ForeignKey("employees.id", ondelete="CASCADE"),
                             nullable=False, index=True)
    title        = db.Column(db.String(200), nullable=False)
    category     = db.Column(db.String(60), default="general")  # general, onboarding, certification, performance, policy
    filename     = db.Column(db.String(255), nullable=False)
    mime_type    = db.Column(db.String(100), default="application/octet-stream")
    file_size    = db.Column(db.Integer, default=0)             # bytes
    file_data    = db.Column(db.LargeBinary, nullable=False)
    notes        = db.Column(db.Text, default="")
    uploaded_by  = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at   = db.Column(db.DateTime, default=_utcnow)

    employee = db.relationship("Employee", backref="documents")
    uploader = db.relationship("User", foreign_keys=[uploaded_by])


class TrainingModule(db.Model):
    """A training course or module that can be assigned to employees."""
    __tablename__ = "training_modules"

    id           = db.Column(db.Integer, primary_key=True)
    title        = db.Column(db.String(200), nullable=False)
    description  = db.Column(db.Text, default="")
    category     = db.Column(db.String(60), default="general")  # general, compliance, safety, product, soft-skills
    duration_mins = db.Column(db.Integer, default=60)
    is_required  = db.Column(db.Boolean, default=False)         # mandatory for all employees?
    is_active    = db.Column(db.Boolean, default=True)
    created_by   = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at   = db.Column(db.DateTime, default=_utcnow)

    creator = db.relationship("User", foreign_keys=[created_by])


class TrainingAssignment(db.Model):
    """Assigns a training module to an employee with completion tracking."""
    __tablename__ = "training_assignments"

    id           = db.Column(db.Integer, primary_key=True)
    module_id    = db.Column(db.Integer, db.ForeignKey("training_modules.id", ondelete="CASCADE"),
                             nullable=False, index=True)
    employee_id  = db.Column(db.Integer, db.ForeignKey("employees.id", ondelete="CASCADE"),
                             nullable=False, index=True)
    status       = db.Column(db.String(30), default="assigned")  # assigned, in_progress, completed, overdue
    due_date     = db.Column(db.Date, nullable=True)
    completed_at = db.Column(db.DateTime, nullable=True)
    score        = db.Column(db.Float, nullable=True)            # optional quiz/test score
    notes        = db.Column(db.Text, default="")
    assigned_by  = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    assigned_at  = db.Column(db.DateTime, default=_utcnow)

    module   = db.relationship("TrainingModule", backref="assignments")
    employee = db.relationship("Employee", backref="training_assignments")
    assigner = db.relationship("User", foreign_keys=[assigned_by])

    __table_args__ = (
        db.UniqueConstraint("module_id", "employee_id", name="uq_training_mod_emp"),
    )


# ── Support Tickets ──────────────────────────────────────────

class SupportTicket(db.Model):
    """Support tickets submitted by any user for issue tracking."""
    __tablename__ = "support_tickets"

    id          = db.Column(db.Integer, primary_key=True)
    subject     = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=False)
    category    = db.Column(db.String(60), default="general")  # general, bug, feature, access, data
    priority    = db.Column(db.String(20), default="medium")   # low, medium, high, urgent
    status      = db.Column(db.String(20), default="open")     # open, in_progress, resolved, closed
    submitted_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    assigned_to  = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    resolution   = db.Column(db.Text, nullable=True)
    created_at   = db.Column(db.DateTime, default=_utcnow)
    updated_at   = db.Column(db.DateTime, default=_utcnow, onupdate=_utcnow)

    submitter = db.relationship("User", foreign_keys=[submitted_by], backref="submitted_tickets")
    assignee  = db.relationship("User", foreign_keys=[assigned_to], backref="assigned_tickets")

    def to_dict(self):
        return {
            "id": self.id,
            "subject": self.subject,
            "description": self.description,
            "category": self.category,
            "priority": self.priority,
            "status": self.status,
            "submitted_by": self.submitted_by,
            "submitter_name": self.submitter.display_name if self.submitter else "Unknown",
            "assigned_to": self.assigned_to,
            "assignee_name": self.assignee.display_name if self.assignee else None,
            "resolution": self.resolution,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class TicketComment(db.Model):
    """Comments on support tickets for back-and-forth communication."""
    __tablename__ = "ticket_comments"

    id         = db.Column(db.Integer, primary_key=True)
    ticket_id  = db.Column(db.Integer, db.ForeignKey("support_tickets.id", ondelete="CASCADE"),
                           nullable=False, index=True)
    user_id    = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    body       = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=_utcnow)

    ticket = db.relationship("SupportTicket", backref=db.backref("comments", order_by="TicketComment.created_at"))
    user   = db.relationship("User", backref="ticket_comments")

    def to_dict(self):
        return {
            "id": self.id,
            "ticket_id": self.ticket_id,
            "user_id": self.user_id,
            "user_name": self.user.display_name if self.user else "Unknown",
            "body": self.body,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# ═══════════════════════════════════════════════════════════════
# WFM TICKETS — client-facing ticketing for WFM requests
# ═══════════════════════════════════════════════════════════════

class WfmTicket(db.Model):
    """WFM ticket submitted by team leads for schedule changes, OT, etc."""
    __tablename__ = "wfm_tickets"

    id           = db.Column(db.Integer, primary_key=True)
    subject      = db.Column(db.String(255), nullable=False)
    description  = db.Column(db.Text, nullable=False)
    category     = db.Column(db.String(50), nullable=False, default="general")
    # Categories: schedule_change, overtime, time_off_exception, shift_swap,
    #             headcount, forecast_adjustment, general
    priority     = db.Column(db.String(20), nullable=False, default="medium")
    status       = db.Column(db.String(20), nullable=False, default="open")
    # Status: open, in_progress, resolved, closed
    submitted_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    assigned_to  = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    affected_agents = db.Column(db.Text, nullable=True)  # comma-separated agent names or IDs
    affected_date   = db.Column(db.Date, nullable=True)   # date the request applies to
    resolution   = db.Column(db.Text, nullable=True)
    internal_note = db.Column(db.Text, nullable=True)     # WFM-only internal notes
    closed_at    = db.Column(db.DateTime, nullable=True)
    closed_by    = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    is_archived  = db.Column(db.Boolean, default=False, nullable=False)
    is_deleted   = db.Column(db.Boolean, default=False, nullable=False)
    created_at   = db.Column(db.DateTime, default=_utcnow)
    updated_at   = db.Column(db.DateTime, default=_utcnow, onupdate=_utcnow)

    submitter  = db.relationship("User", foreign_keys=[submitted_by], backref="wfm_tickets_submitted")
    assignee   = db.relationship("User", foreign_keys=[assigned_to], backref="wfm_tickets_assigned")
    closer     = db.relationship("User", foreign_keys=[closed_by])

    def to_dict(self, strip_internal=False):
        d = {
            "id": self.id,
            "subject": self.subject,
            "description": self.description,
            "category": self.category,
            "priority": self.priority,
            "status": self.status,
            "submitted_by": self.submitted_by,
            "submitter_name": self.submitter.display_name if self.submitter else "Unknown",
            "assigned_to": self.assigned_to,
            "assignee_name": self.assignee.display_name if self.assignee else None,
            "affected_agents": self.affected_agents,
            "affected_date": self.affected_date.isoformat() if self.affected_date else None,
            "resolution": self.resolution,
            "internal_note": self.internal_note,
            "closed_at": self.closed_at.isoformat() if self.closed_at else None,
            "closed_by_name": self.closer.display_name if self.closer else None,
            "is_archived": self.is_archived,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
        if strip_internal:
            d.pop("internal_note", None)
        return d


class WfmTicketComment(db.Model):
    """Comments on WFM tickets."""
    __tablename__ = "wfm_ticket_comments"

    id         = db.Column(db.Integer, primary_key=True)
    ticket_id  = db.Column(db.Integer, db.ForeignKey("wfm_tickets.id", ondelete="CASCADE"),
                           nullable=False, index=True)
    user_id    = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    body       = db.Column(db.Text, nullable=False)
    is_internal = db.Column(db.Boolean, default=False)  # internal WFM-only comments
    created_at = db.Column(db.DateTime, default=_utcnow)

    ticket = db.relationship("WfmTicket", backref=db.backref("comments", order_by="WfmTicketComment.created_at"))
    user   = db.relationship("User", backref="wfm_ticket_comments")

    def to_dict(self):
        return {
            "id": self.id,
            "ticket_id": self.ticket_id,
            "user_id": self.user_id,
            "user_name": self.user.display_name if self.user else "Unknown",
            "body": self.body,
            "is_internal": self.is_internal,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class WfmTicketHistory(db.Model):
    """Audit log of changes to WFM tickets."""
    __tablename__ = "wfm_ticket_history"

    id         = db.Column(db.Integer, primary_key=True)
    ticket_id  = db.Column(db.Integer, db.ForeignKey("wfm_tickets.id", ondelete="CASCADE"),
                           nullable=False, index=True)
    user_id    = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    action     = db.Column(db.String(50), nullable=False)  # created, status_change, assigned, archived, restored, etc.
    field      = db.Column(db.String(50), nullable=True)
    old_value  = db.Column(db.Text, nullable=True)
    new_value  = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=_utcnow)

    ticket = db.relationship("WfmTicket", backref=db.backref("history", order_by="WfmTicketHistory.created_at.desc()"))
    user   = db.relationship("User")

    def to_dict(self):
        return {
            "id": self.id,
            "ticket_id": self.ticket_id,
            "user_name": self.user.display_name if self.user else "Unknown",
            "action": self.action,
            "field": self.field,
            "old_value": self.old_value,
            "new_value": self.new_value,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
