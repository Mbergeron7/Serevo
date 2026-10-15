"""
scheduling/roster_engine.py — Roster Rotation generation engine
================================================================
Generates fair role assignments (opener/closer) for roster groups.
The algorithm:
  1. Detect day type (stat → weekend → weekday)
  2. Filter eligible members per slot
  3. Lock static assignments first
  4. Rotate remaining slots using fairness scoring
  5. Write draft Schedule rows with source='roster'
  6. Update RosterRotationState counters
"""

import json
import logging
from datetime import date, datetime, timedelta

from app.models import (
    db, RosterGroup, RosterMember, RosterSlot,
    RosterRotationState, Schedule, Holiday, Employee,
)

log = logging.getLogger("serevo.roster_engine")

# Day-of-week abbreviations used in work_days JSON
DOW_ABBR = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
DOW_FULL = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def generate_roster(group_id, start_date, end_date):
    """Generate draft roster assignments for a group over a date range.

    Returns dict with 'assignments' list and 'summary' stats.
    """
    group = RosterGroup.query.get(group_id)
    if not group or not group.active:
        return {"error": "Group not found or inactive"}

    active_members = [m for m in group.members if m.active and not m.is_display_only]
    if not active_members:
        return {"error": "No active members in group"}

    slots_by_day_type = _index_slots(group.slots)
    if not slots_by_day_type:
        return {"error": "No shift slots defined for this group"}

    # Load holiday dates for the planning unit's date range
    stat_dates = _get_stat_dates(start_date, end_date)

    # Load existing PTO for all members in the date range
    member_employee_ids = {m.id: m.employee_id for m in active_members}
    pto_dates = _get_pto_dates(list(member_employee_ids.values()), start_date, end_date)

    # Get or create rotation state for each member (period = Jan 1 of start year)
    period_start = date(start_date.year, 1, 1)
    states = _get_or_create_states(active_members, period_start)

    # Clear any existing draft roster schedules for this group's members in date range
    _clear_draft_rosters(group, start_date, end_date)

    assignments = []
    current = start_date
    while current <= end_date:
        day_assignments = _process_day(
            current, group, active_members, slots_by_day_type,
            stat_dates, pto_dates, states
        )
        assignments.extend(day_assignments)
        current += timedelta(days=1)

    # Persist updated states
    for state in states.values():
        db.session.add(state)

    # Write Schedule rows
    schedule_rows = _write_schedule_rows(assignments, group)

    db.session.commit()

    return {
        "assignments": [_assignment_to_dict(a) for a in assignments],
        "count": len(assignments),
        "schedule_rows": len(schedule_rows),
    }


def publish_roster(group_id, start_date, end_date):
    """Publish draft roster schedules (change status from 'draft' to 'scheduled')."""
    group = RosterGroup.query.get(group_id)
    if not group:
        return {"error": "Group not found"}

    emp_ids = [m.employee_id for m in group.members if m.active]
    rows = Schedule.query.filter(
        Schedule.employee_id.in_(emp_ids),
        Schedule.schedule_date >= start_date,
        Schedule.schedule_date <= end_date,
        Schedule.source == "roster",
        Schedule.status == "draft",
    ).all()

    for row in rows:
        row.status = "scheduled"

    db.session.commit()
    return {"published": len(rows)}


def clear_roster_drafts(group_id, start_date, end_date):
    """Remove unpublished draft roster schedules."""
    group = RosterGroup.query.get(group_id)
    if not group:
        return {"error": "Group not found"}

    _clear_draft_rosters(group, start_date, end_date)
    db.session.commit()
    return {"ok": True}


def get_roster_calendar(group_id, start_date, end_date):
    """Return roster schedule rows for calendar display."""
    group = RosterGroup.query.get(group_id)
    if not group:
        return {"error": "Group not found"}

    emp_ids = [m.employee_id for m in group.members if m.active]
    rows = Schedule.query.filter(
        Schedule.employee_id.in_(emp_ids),
        Schedule.schedule_date >= start_date,
        Schedule.schedule_date <= end_date,
        Schedule.source == "roster",
    ).order_by(Schedule.schedule_date, Schedule.shift_start).all()

    return {"rows": [r.to_dict() for r in rows]}


# ── Internal helpers ────────────────────────────────────────────

def _index_slots(slots):
    """Group slots by day_type → list of slots."""
    out = {}
    for s in slots:
        out.setdefault(s.day_type, []).append(s)
    return out


def _get_stat_dates(start, end):
    """Return set of dates that are statutory holidays in the range."""
    holidays = Holiday.query.filter(
        Holiday.date >= start,
        Holiday.date <= end,
    ).all()
    return {h.date for h in holidays}


def _get_pto_dates(employee_ids, start, end):
    """Return dict of employee_id → set of dates with approved PTO."""
    if not employee_ids:
        return {}
    pto_rows = Schedule.query.filter(
        Schedule.employee_id.in_(employee_ids),
        Schedule.schedule_date >= start,
        Schedule.schedule_date <= end,
        Schedule.status == "pto",
    ).all()
    out = {}
    for row in pto_rows:
        out.setdefault(row.employee_id, set()).add(row.schedule_date)
    return out


def _get_or_create_states(members, period_start):
    """Load or create RosterRotationState for each member.

    When no state exists for the requested period, the most recent
    previous state is carried forward so the rotation picks up where
    it left off instead of resetting to zero.

    Returns dict member_id → state.
    """
    states = {}
    for m in members:
        state = RosterRotationState.query.filter_by(
            roster_member_id=m.id,
            period_start=period_start,
        ).first()
        if not state:
            # Look for the most recent previous state to carry forward
            prev = (RosterRotationState.query
                    .filter_by(roster_member_id=m.id)
                    .filter(RosterRotationState.period_start < period_start)
                    .order_by(RosterRotationState.period_start.desc())
                    .first())
            state = RosterRotationState(
                roster_member_id=m.id,
                period_start=period_start,
                weekday_close_count=prev.weekday_close_count if prev else 0,
                weekend_count=prev.weekend_count if prev else 0,
                stat_count=prev.stat_count if prev else 0,
                weekday_rotation_index=prev.weekday_rotation_index if prev else 0,
                last_close_weekday=prev.last_close_weekday if prev else None,
                last_close_date=prev.last_close_date if prev else None,
                last_weekend_date=prev.last_weekend_date if prev else None,
            )
            db.session.add(state)
            db.session.flush()
        states[m.id] = state
    return states


def _clear_draft_rosters(group, start_date, end_date):
    """Delete draft roster Schedule rows for this group's members."""
    emp_ids = [m.employee_id for m in group.members if m.active]
    if not emp_ids:
        return
    Schedule.query.filter(
        Schedule.employee_id.in_(emp_ids),
        Schedule.schedule_date >= start_date,
        Schedule.schedule_date <= end_date,
        Schedule.source == "roster",
        Schedule.status == "draft",
    ).delete(synchronize_session="fetch")


def _detect_day_type(dt, stat_dates):
    """Determine day type: 'stat', 'saturday', 'sunday', or 'weekday'."""
    if dt in stat_dates:
        return "stat"
    dow = dt.weekday()  # 0=Mon, 6=Sun
    if dow == 5:
        return "saturday"
    if dow == 6:
        return "sunday"
    return "weekday"


def _get_eligible_members(members, day_date, slot, pto_dates, states, group):
    """Filter members eligible for a specific slot on a specific day."""
    dow = day_date.weekday()
    day_abbr = DOW_ABBR[dow]
    day_full = DOW_FULL[dow]
    role = slot.role  # "opener" or "closer"

    eligible = []
    for m in members:
        # Skip display-only
        if m.is_display_only:
            continue

        # Check work_days
        work_days = _parse_json(m.work_days)
        if work_days is not None:
            if day_abbr not in work_days and day_full not in work_days:
                continue

        # Check PTO
        emp_pto = pto_dates.get(m.employee_id, set())
        if day_date in emp_pto:
            continue

        # For closer slots: check accommodation_no_close
        if role == "closer" and m.accommodation_no_close:
            continue

        # For closer slots: check excluded_closing_days
        if role == "closer":
            excluded = _parse_json(m.excluded_closing_days)
            if excluded and (day_abbr in excluded or day_full in excluded):
                continue

        # Check static_days_json — if member has a static assignment for a
        # DIFFERENT role on this day, skip them for this slot
        static = _parse_json(m.static_days_json)
        if static and day_full in static:
            static_role = static[day_full]
            if static_role != role:
                continue

        # For accommodation: check if shift times fit
        if role == "closer" and m.accommodation_end:
            if slot.shift_end and slot.shift_end > m.accommodation_end:
                continue
        if role == "opener" and m.accommodation_start:
            if slot.shift_start and slot.shift_start < m.accommodation_start:
                continue

        eligible.append(m)

    return eligible


def _is_static_assignment(member, day_date, role):
    """Check if member has a static (fixed) assignment for this day+role."""
    dow = day_date.weekday()
    day_full = DOW_FULL[dow]
    static = _parse_json(member.static_days_json)
    if static and day_full in static:
        return static[day_full] == role
    return False


def _is_weekend_primary_for(member, day_date, group):
    """Check if member is a dedicated weekend worker for this day."""
    if group.weekend_mode != "dedicated":
        return False
    if not member.is_weekend_primary:
        return False
    day_full = DOW_FULL[day_date.weekday()]
    primary_days = _parse_json(member.weekend_primary_days)
    if primary_days:
        return day_full in primary_days
    # If no specific days set, they cover all weekend days
    return day_full in ("saturday", "sunday")


def _fairness_score(member, state, day_date, slot, week_closings):
    """Compute priority score for rotation assignment. Lower = picked first."""
    role = slot.role

    if role == "closer":
        count = state.weekday_close_count if day_date.weekday() < 5 else state.weekend_count
    else:
        # For openers, use a simple counter (less critical for fairness)
        count = 0

    score = count * 10

    # Anti-clustering: penalize if closed recently
    if state.last_close_date:
        days_since = (day_date - state.last_close_date).days
        if days_since <= 2:
            score += 100  # heavy penalty for back-to-back
        else:
            score -= days_since  # longer gap = lower score (better)

    # Weekday advancement: prefer next weekday in cycle
    if role == "closer" and day_date.weekday() < 5:
        target_weekday = state.weekday_rotation_index
        actual_weekday = day_date.weekday()
        # Distance in the Mon→Fri cycle
        distance = (actual_weekday - target_weekday) % 5
        if distance > 0:
            score += distance * 5

    # Max-per-week cap check
    max_pw = member.max_closings_per_week or 1
    if role == "closer" and week_closings.get(member.id, 0) >= max_pw:
        score += 10000  # effectively skip

    return score


def _process_day(day_date, group, members, slots_by_day_type,
                 stat_dates, pto_dates, states):
    """Process one day: assign roles and return assignment dicts."""
    day_type = _detect_day_type(day_date, stat_dates)

    # Find applicable slots
    applicable_slots = slots_by_day_type.get(day_type, [])
    if not applicable_slots:
        # Fall back: weekday slots for stat days if no stat-specific slots
        if day_type == "stat":
            applicable_slots = slots_by_day_type.get("weekday", [])
        if not applicable_slots:
            return []

    assignments = []
    assigned_member_ids = set()  # track who's already assigned today

    # Track closings this week for max-per-week cap
    week_start = day_date - timedelta(days=day_date.weekday())
    week_closings = _count_week_closings(members, week_start, day_date, states)

    for slot in applicable_slots:
        # How many needed for this slot
        if slot.role == "closer":
            needed = (group.closers_required_weekday
                      if day_type == "weekday"
                      else group.closers_required_weekend)
        else:
            needed = 1  # typically 1 opener

        eligible = _get_eligible_members(
            members, day_date, slot, pto_dates, states, group
        )

        # Step 3: Static assignments first
        static_picks = []
        for m in eligible:
            if m.id in assigned_member_ids:
                continue
            if _is_static_assignment(m, day_date, slot.role):
                static_picks.append(m)
            elif _is_weekend_primary_for(m, day_date, group) and slot.role == "closer":
                static_picks.append(m)

        for m in static_picks[:needed]:
            assignments.append({
                "member": m,
                "slot": slot,
                "date": day_date,
                "day_type": day_type,
                "reason": "static",
            })
            assigned_member_ids.add(m.id)
            _update_state_for_assignment(states[m.id], day_date, slot)
            if slot.role == "closer":
                week_closings[m.id] = week_closings.get(m.id, 0) + 1

        remaining = needed - len(static_picks[:needed])
        if remaining <= 0:
            continue

        # Step 4: Rotation assignment for remaining slots
        rotation_pool = [m for m in eligible
                         if m.id not in assigned_member_ids
                         and m not in static_picks]

        # Sort by fairness score
        scored = []
        for m in rotation_pool:
            s = _fairness_score(m, states[m.id], day_date, slot, week_closings)
            scored.append((s, m))
        # Sort by fairness score, then rotation_order as tiebreaker
        scored.sort(key=lambda x: (x[0], getattr(x[1], 'rotation_order', 0) or 0))

        for _, m in scored[:remaining]:
            assignments.append({
                "member": m,
                "slot": slot,
                "date": day_date,
                "day_type": day_type,
                "reason": "rotation",
            })
            assigned_member_ids.add(m.id)
            _update_state_for_assignment(states[m.id], day_date, slot)
            if slot.role == "closer":
                week_closings[m.id] = week_closings.get(m.id, 0) + 1

    return assignments


def _update_state_for_assignment(state, day_date, slot):
    """Update fairness counters after assigning a slot."""
    if slot.role == "closer":
        dow = day_date.weekday()
        if dow < 5:  # weekday
            state.weekday_close_count += 1
            state.weekday_rotation_index = (dow + 1) % 5
        elif dow in (5, 6):  # weekend
            state.weekend_count += 1
            state.last_weekend_date = day_date
        state.last_close_date = day_date
        state.last_close_weekday = dow


def _count_week_closings(members, week_start, current_date, states):
    """Approximate weekly closings from generated assignments so far.
    For the first generation this is tracked in-memory via the generation loop.
    Returns a dict member_id → count.
    """
    # This is populated incrementally during _process_day, start at 0
    return {}


def _write_schedule_rows(assignments, group):
    """Create Schedule rows for all assignments."""
    rows = []
    for a in assignments:
        member = a["member"]
        slot = a["slot"]

        # Calculate hours
        start_dt = datetime.combine(a["date"], slot.shift_start)
        end_dt = datetime.combine(a["date"], slot.shift_end)
        hours = (end_dt - start_dt).total_seconds() / 3600
        if hours < 0:
            hours += 24  # overnight shift

        row = Schedule(
            employee_id=member.employee_id,
            planning_unit_id=group.planning_unit_id,
            schedule_date=a["date"],
            shift_start=slot.shift_start,
            shift_end=slot.shift_end,
            shift_type=slot.role,      # "opener" or "closer"
            hours=round(hours, 2),
            status="draft",
            source="roster",
        )
        db.session.add(row)
        rows.append(row)

    return rows


def _assignment_to_dict(a):
    """Convert assignment dict to JSON-serializable form."""
    emp = a["member"].employee
    return {
        "date": a["date"].isoformat(),
        "employee_name": emp.full_name if emp else "",
        "employee_id": emp.employee_id if emp else "",
        "role": a["slot"].role,
        "day_type": a["day_type"],
        "shift_start": a["slot"].shift_start.strftime("%H:%M"),
        "shift_end": a["slot"].shift_end.strftime("%H:%M"),
        "reason": a["reason"],
    }


def _parse_json(text):
    """Safely parse JSON text, return None on failure."""
    if not text:
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
