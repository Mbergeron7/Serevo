"""
scheduling/roster_engine.py — Roster Rotation generation engine
================================================================
Generates full weekly schedules for roster groups.

Algorithm (week-level planning):
  1. For each week in the date range:
     a. Assign closing shifts first — one per member per week (rotating fairly)
     b. Apply Sunday = non-closer rule if enabled
     c. Fill remaining days with configured day shift slots
  2. Handle dedicated weekend members (Saturday/Sunday)
  3. Write draft Schedule rows with source='roster'
  4. Update RosterRotationState counters for fairness
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
    from sqlalchemy.orm import joinedload
    group = db.session.query(RosterGroup).options(
        joinedload(RosterGroup.members).joinedload(RosterMember.employee),
        joinedload(RosterGroup.slots),
    ).get(group_id)
    if not group or not group.active:
        return {"error": "Group not found or inactive"}

    active_members = [m for m in group.members if m.active and not m.is_display_only]
    if not active_members:
        return {"error": "No active members in group"}

    # Reset one-shot debug flag each generation
    global _thu_debug_done
    _thu_debug_done = False

    # ── Diagnostic logging ──
    log.info("=== ROSTER ENGINE START: group=%s (%s), range=%s to %s ===",
             group.id, group.name, start_date, end_date)
    log.info("Group settings: closers_req_wd=%s, closers_req_we=%s, weekend_mode=%s, sunday_non_closer=%s, parity=%s",
             group.closers_required_weekday, group.closers_required_weekend,
             group.weekend_mode, group.sunday_non_closer, group.weekend_close_week_parity)
    log.info("Active members: %d", len(active_members))
    for m in active_members:
        emp_name = m.employee.first_name + " " + m.employee.last_name if m.employee else f"emp#{m.employee_id}"
        log.debug("  Member %s (%s): max_close/wk=%s, ded_closer=%s, ded_opener=%s, "
                  "excl_close=%s, excl_open=%s, excl_wknd=%s, accom_no_close=%s, "
                  "work_days=%s, excluded_closing_days=%s, static_days=%s, fill_in=%s",
                  m.id, emp_name, m.max_closings_per_week,
                  m.dedicated_closer, m.dedicated_opener,
                  m.exclude_from_closing, m.exclude_from_opening, m.exclude_from_saturday, m.exclude_from_sunday,
                  m.accommodation_no_close,
                  m.work_days, m.excluded_closing_days, m.static_days_json, m.is_fill_in)
        log.debug("    accom_start=%s, accom_end=%s", m.accommodation_start, m.accommodation_end)

    slots_by_day_type = _index_slots(group.slots)
    if not slots_by_day_type:
        return {"error": "No shift slots defined for this group"}

    # Log slot roles after normalisation
    for dt, slot_list in slots_by_day_type.items():
        for s in slot_list:
            log.debug("  Slot id=%s day_type=%s role='%s' label=%s", s.id, dt, s.role, s.label)

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

    # Weekend close week parity: "odd" or "even" ISO week numbers
    wk_parity = group.weekend_close_week_parity  # None = every weekend

    assignments = []

    # ── In-memory per-day-of-week and per-role counters ──
    # These track distribution WITHIN this generation run for variety
    closer_dow_counts = {m.id: [0]*7 for m in active_members}  # per day-of-week closer counts
    opener_dow_counts = {m.id: [0]*7 for m in active_members}  # per day-of-week opener counts
    role_counts = {m.id: {} for m in active_members}           # per role counts (for mid shifts)

    # ── Unified week-level pipeline ──
    # Always use week-level planning — when no closer slots exist,
    # Phases 1-2 simply produce no assignments and the remaining
    # phases (0, 3, 4, 5) handle all slot types with full fairness,
    # contract enforcement, and consistent_start support.
    weeks = _split_into_weeks(start_date, end_date)
    for week_start, week_end in weeks:
        week_days = _date_range(max(week_start, start_date), min(week_end, end_date))
        week_assignments = _process_week(
            week_days, group, active_members, slots_by_day_type,
            stat_dates, pto_dates, states, wk_parity,
            closer_dow_counts, opener_dow_counts, role_counts
        )
        assignments.extend(week_assignments)

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
    """Group slots by day_type → list of slots, sorted by sort_order.

    Normalises role to lowercase so all engine comparisons
    (e.g. ``s.role == "closer"``) work regardless of how the user
    typed the role in the UI.
    """
    out = {}
    for s in slots:
        # Normalise role to canonical form so engine comparisons work.
        # Users type free-text roles like "closer shift", "opener 1" etc.
        # The engine expects exact "closer" or "opener".
        if s.role:
            r = s.role.strip().lower()
            if "close" in r:
                r = "closer"
            elif "open" in r:
                r = "opener"
            s.role = r
        dt_key = (s.day_type or "").strip().lower()
        out.setdefault(dt_key, []).append(s)
    # Sort each day_type's slots by sort_order
    for dt in out:
        out[dt].sort(key=lambda s: (s.sort_order or 0, s.role or ""))
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
                saturday_count=getattr(prev, 'saturday_count', 0) or 0 if prev else 0,
                sunday_count=getattr(prev, 'sunday_count', 0) or 0 if prev else 0,
                stat_count=prev.stat_count if prev else 0,
                opener_count=getattr(prev, 'opener_count', 0) or 0 if prev else 0,
                day_shift_count=getattr(prev, 'day_shift_count', 0) or 0 if prev else 0,
                weekday_rotation_index=prev.weekday_rotation_index if prev else 0,
                role_counts_json=getattr(prev, 'role_counts_json', None) if prev else None,
                consecutive_days=getattr(prev, 'consecutive_days', 0) or 0 if prev else 0,
                last_work_date=getattr(prev, 'last_work_date', None) if prev else None,
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


def _split_into_weeks(start_date, end_date):
    """Split date range into (week_start_monday, week_end_sunday) tuples."""
    weeks = []
    current = start_date - timedelta(days=start_date.weekday())  # Monday
    while current <= end_date:
        week_end = current + timedelta(days=6)  # Sunday
        weeks.append((current, week_end))
        current += timedelta(days=7)
    return weeks


def _date_range(start, end):
    """Return list of dates from start to end inclusive."""
    days = []
    d = start
    while d <= end:
        days.append(d)
        d += timedelta(days=1)
    return days


# ── Week-level planning ────────────────────────────────────────

def _process_week(week_days, group, members, slots_by_day_type,
                  stat_dates, pto_dates, states, wk_parity=None,
                  closer_dow_counts=None, opener_dow_counts=None, role_counts=None):
    """Process one week: assign closings first, then day shifts, then Sunday rule.

    Returns list of assignment dicts for the whole week.
    """
    assignments = []
    # Track who's assigned to what each day this week
    # day_date → set of member_ids already assigned
    daily_assigned = {d: set() for d in week_days}
    # Track how many DAYS each member has been assigned this week (for contract enforcement)
    week_day_counts = {m.id: 0 for m in members}
    # Contract lookup: member_id → contracted_days (default 5)
    _contracted = {m.id: (m.contracted_days if m.contracted_days is not None else 5) for m in members}
    # Track who got a closing this week
    week_closings = {}  # member_id → count
    week_openings = {}  # member_id → count
    week_mids = {}      # member_id → count
    # Track which members closed on which day (for Sunday rule)
    closer_days = {}  # member_id → [dates]
    # Track first assigned shift_start per member for consistent_start enforcement
    consistent_start_times = {}  # member_id → shift_start (time obj)
    # Track assigned slot times per member per day for split shift gap checking
    daily_slots = {d: {} for d in week_days}  # day_date → {member_id: [(start, end), ...]}

    def _track_day(member_id, the_date, slot=None):
        """Increment week_day_counts when a member is first assigned on a new day, and mark them assigned."""
        if member_id not in daily_assigned[the_date]:
            week_day_counts[member_id] = week_day_counts.get(member_id, 0) + 1
            daily_assigned[the_date].add(member_id)
            _update_consecutive(member_id, the_date)
        if slot is not None:
            _record_consistent_start(member_id, slot)
            week_hours[member_id] = week_hours.get(member_id, 0) + _slot_hours(slot)
            # Record slot times for split shift gap checking
            if the_date not in daily_slots:
                daily_slots[the_date] = {}
            if member_id not in daily_slots[the_date]:
                daily_slots[the_date][member_id] = []
            daily_slots[the_date][member_id].append((slot.shift_start, slot.shift_end))

    def _split_shift_ok(member, day_date, slot):
        """Check if a member with allow_split_shift can take this slot given existing assignments."""
        if not member.allow_split_shift:
            return False
        existing = daily_slots.get(day_date, {}).get(member.id, [])
        if len(existing) >= 2:
            return False  # max 2 slots per day
        if not existing:
            return True  # first slot, always ok
        min_gap = member.split_shift_min_gap or 0
        for (ex_start, ex_end) in existing:
            if slot.shift_start and slot.shift_end and ex_start and ex_end:
                # Calculate gap in hours between existing slot and new slot
                from datetime import datetime, date as date_cls
                ref = date_cls(2000, 1, 1)
                new_start_dt = datetime.combine(ref, slot.shift_start)
                new_end_dt = datetime.combine(ref, slot.shift_end)
                ex_start_dt = datetime.combine(ref, ex_start)
                ex_end_dt = datetime.combine(ref, ex_end)
                # Gap: time between end of earlier shift and start of later shift
                if new_start_dt >= ex_end_dt:
                    gap_hours = (new_start_dt - ex_end_dt).total_seconds() / 3600
                elif ex_start_dt >= new_end_dt:
                    gap_hours = (ex_start_dt - new_end_dt).total_seconds() / 3600
                else:
                    return False  # overlapping
                if gap_hours < min_gap:
                    return False
        return True

    def _is_available_for_slot(member, day_date, slot=None):
        """Check if member can be assigned on this day — either not yet assigned, or split shift eligible."""
        if member.id not in daily_assigned[day_date]:
            return True
        if slot is not None and _split_shift_ok(member, day_date, slot):
            return True
        return False

    def _contract_deficit(member_id):
        """How many more days this member needs to hit their contract. Higher = more urgently needs shifts."""
        return _contracted.get(member_id, 5) - week_day_counts.get(member_id, 0)

    def _consistent_start_ok(member, slot):
        """Hard check: does this slot match the member's established start time?
        Returns True if member has no pref, no established time yet, or slot matches."""
        if not getattr(member, 'consistent_start', False):
            return True
        established = consistent_start_times.get(member.id)
        if established is None:
            return True  # first assignment, no preference yet
        return slot.shift_start == established

    def _apply_consistent_start_filter(pool, slot):
        """Hard-filter pool by consistent_start, but fall back to full pool
        if filtering would remove everyone."""
        filtered = [m for m in pool if _consistent_start_ok(m, slot)]
        return filtered if filtered else pool

    def _consistent_start_penalty(member, slot):
        """Soft tiebreaker for sorting — bonus for matching, penalty for mismatch.
        The hard filter above does most of the work; this handles fallback cases."""
        if not getattr(member, 'consistent_start', False):
            return 0
        established = consistent_start_times.get(member.id)
        if established is None:
            return 0
        if slot.shift_start == established:
            return -1
        return 50

    def _record_consistent_start(member_id, slot):
        """Record the first shift_start for a consistent_start member."""
        if member_id not in consistent_start_times:
            # Find the member object
            m_obj = next((m for m in members if m.id == member_id), None)
            if m_obj and getattr(m_obj, 'consistent_start', False) and slot.shift_start:
                consistent_start_times[member_id] = slot.shift_start

    # ── Hours tracking for min/max guardrails ──
    week_hours = {m.id: 0.0 for m in members}

    def _slot_hours(slot):
        """Calculate hours for a slot."""
        from datetime import datetime as _dt
        start_dt = _dt.combine(date.today(), slot.shift_start)
        end_dt = _dt.combine(date.today(), slot.shift_end)
        h = (end_dt - start_dt).total_seconds() / 3600
        return h + 24 if h < 0 else h

    def _hours_ok(member, slot):
        """Check if assigning this slot would exceed max_hours_per_week."""
        max_h = member.max_hours_per_week
        if max_h is None:
            return True
        return week_hours.get(member.id, 0) + _slot_hours(slot) <= max_h + 0.01

    def _hours_deficit(member):
        """How many more hours this member needs to hit min_hours. Higher = more urgently needs shifts."""
        min_h = member.min_hours_per_week
        if min_h is None:
            return 0
        return max(0, min_h - week_hours.get(member.id, 0))

    # ── Consecutive day tracking ──
    # Load from state, update as we assign
    consecutive_tracker = {}  # member_id → current streak count
    last_work_tracker = {}    # member_id → last work date
    for m in members:
        st = states[m.id]
        consecutive_tracker[m.id] = getattr(st, 'consecutive_days', 0) or 0
        consecutive_tracker[m.id] = consecutive_tracker[m.id]
        last_work_tracker[m.id] = getattr(st, 'last_work_date', None)

    def _consecutive_ok(member, day_date):
        """Check if member can work this day without exceeding max_consecutive_days."""
        max_c = member.max_consecutive_days
        if max_c is None:
            return True
        mid = member.id
        last_d = last_work_tracker.get(mid)
        streak = consecutive_tracker.get(mid, 0)
        if last_d and (day_date - last_d).days == 1:
            return streak < max_c
        return True  # gap resets streak

    def _update_consecutive(member_id, day_date):
        """Update consecutive day tracker after assignment."""
        last_d = last_work_tracker.get(member_id)
        if last_d and (day_date - last_d).days == 1:
            consecutive_tracker[member_id] = consecutive_tracker.get(member_id, 0) + 1
        elif last_d and (day_date - last_d).days == 0:
            pass  # same day, no change
        else:
            consecutive_tracker[member_id] = 1
        last_work_tracker[member_id] = day_date

    def _preferred_off_penalty(member, day_date):
        """Return soft penalty if this day is a preferred day off."""
        pref = _parse_json(member.preferred_days_off) if hasattr(member, 'preferred_days_off') else None
        if not pref:
            return 0
        dow = day_date.weekday()
        if DOW_ABBR[dow] in pref or DOW_FULL[dow] in pref:
            return 25  # soft penalty — prefer someone else but don't block
        return 0

    def _pairing_score(member, day_date, daily_assigned_set):
        """Return bonus/penalty based on shift pairing/avoidance rules."""
        score = 0
        pair_id = getattr(member, 'pair_with_member_id', None)
        avoid_id = getattr(member, 'avoid_member_id', None)
        if pair_id and pair_id in daily_assigned_set:
            score -= 10  # bonus — paired member already working this day
        if avoid_id and avoid_id in daily_assigned_set:
            score += 30  # penalty — avoid member already working this day
        return score

    # Wrap _track_day to also update hours and consecutive tracking
    _orig_track_day = None  # will be set below

    # ── Determine if this is a weekend-close week for this group ──
    # Use the ISO week number of the first day in this week slice
    iso_week = week_days[0].isocalendar()[1]
    is_close_weekend = True
    if wk_parity:
        week_is_odd = (iso_week % 2 == 1)
        is_close_weekend = (wk_parity == "odd" and week_is_odd) or \
                           (wk_parity == "even" and not week_is_odd)

    # ── When parity is set, handle weekend days as single-role days ──
    # On close-week: only assign closer (1 person). Off-week: only opener (1 person).
    # This prevents 2 people from the same group on the same weekend day.
    parity_weekend_days = set()  # dates handled by parity logic (skip in later phases)
    if wk_parity:
        for day_date in week_days:
            day_type = _detect_day_type(day_date, stat_dates)
            if day_type not in ("saturday", "sunday"):
                continue
            parity_weekend_days.add(day_date)
            applicable_slots = _get_applicable_slots(slots_by_day_type, day_type)

            # Pick the right role: closer on close-week, opener on off-week
            if is_close_weekend:
                target_slots = [s for s in applicable_slots if s.role == "closer"]
            else:
                target_slots = [s for s in applicable_slots if s.role == "opener"]

            if not target_slots:
                continue
            slot = target_slots[0]

            eligible = _get_eligible_members(members, day_date, slot, pto_dates, states, group)
            # Prefer dedicated weekend primary
            dedicated = [m for m in eligible
                        if _is_weekend_primary_for(m, day_date, group)
                        and m.id not in daily_assigned[day_date]]
            pool = dedicated if dedicated else [
                m for m in eligible if m.id not in daily_assigned[day_date]
            ]
            if not pool:
                continue

            # Sort pool by day-specific count so Sat/Sun rotate independently
            _dow = day_date.weekday()
            pool.sort(key=lambda m: (
                (states[m.id].saturday_count or 0) if _dow == 5 else (states[m.id].sunday_count or 0),
                states[m.id].weekend_count,
                getattr(m, 'rotation_order', 0) or 0
            ))
            m = pool[0]
            reason = "parity_weekend"
            assignments.append(_make_assignment(m, slot, day_date, day_type, reason))
            _track_day(m.id, day_date, slot)
            _update_state_for_assignment(states[m.id], day_date, slot)
            if slot.role == "closer":
                week_closings[m.id] = week_closings.get(m.id, 0) + 1
                closer_days.setdefault(m.id, []).append(day_date)
                if closer_dow_counts is not None:
                    closer_dow_counts[m.id][day_date.weekday()] += 1

    # ── Phase 0: Pre-assign accommodated members with limited slot options ──
    # Members whose accommodation window restricts them to a single eligible
    # slot must be placed first, otherwise other phases fill that slot and
    # the accommodated member gets nothing.
    for day_date in week_days:
        if day_date in parity_weekend_days:
            continue
        day_type = _detect_day_type(day_date, stat_dates)
        applicable_slots = _get_applicable_slots(slots_by_day_type, day_type)
        if not applicable_slots:
            continue
        for m in members:
            if m.id in daily_assigned[day_date]:
                continue
            if not (m.accommodation_start or m.accommodation_end):
                continue
            if m.dedicated_closer or m.dedicated_opener:
                continue
            # Find which slots this member is eligible for
            eligible_slots = [
                s for s in applicable_slots
                if m in _get_eligible_members([m], day_date, s, pto_dates, states, group)
            ]
            if day_date.weekday() == 3:  # Thursday Phase 0 debug
                log.debug("  Phase0 THU %s: m%d eligible_slots=%s (of %d applicable)",
                         day_date, m.id, [s.role for s in eligible_slots], len(applicable_slots))
            if len(eligible_slots) == 1:
                slot = eligible_slots[0]
                log.debug("  Phase0 ASSIGN m%d → %s on %s", m.id, slot.role, day_date)
                assignments.append(_make_assignment(m, slot, day_date, day_type, "accommodation"))
                _track_day(m.id, day_date, slot)
                _update_state_for_assignment(states[m.id], day_date, slot)
                if slot.role == "closer":
                    week_closings[m.id] = week_closings.get(m.id, 0) + 1
                    closer_days.setdefault(m.id, []).append(day_date)
                    if closer_dow_counts is not None:
                        closer_dow_counts[m.id][day_date.weekday()] += 1

    # ── Phase 1: Assign closing slots across the week ──────────
    for day_date in week_days:
        if day_date in parity_weekend_days:
            continue  # already handled above
        day_type = _detect_day_type(day_date, stat_dates)
        applicable_slots = _get_applicable_slots(slots_by_day_type, day_type)
        closer_slots = [s for s in applicable_slots if s.role == "closer"]

        for slot in closer_slots:
            needed = _slots_needed(slot, group, day_type)

            # Static/dedicated assignments first
            eligible = _get_eligible_members(members, day_date, slot, pto_dates, states, group)
            if day_date.weekday() == 3:  # Thursday debug
                log.debug("  Phase1 THU %s: eligible_ids=%s, daily_assigned=%s",
                         day_date, [m.id for m in eligible], daily_assigned[day_date])
            static_picks, rotation_pool = _split_static_rotation(
                eligible, day_date, slot, group, daily_assigned[day_date]
            )

            for m in static_picks[:needed]:
                assignments.append(_make_assignment(m, slot, day_date, day_type, "static"))
                _track_day(m.id, day_date, slot)
                _update_state_for_assignment(states[m.id], day_date, slot)
                week_closings[m.id] = week_closings.get(m.id, 0) + 1
                closer_days.setdefault(m.id, []).append(day_date)
                if closer_dow_counts is not None:
                    closer_dow_counts[m.id][day_date.weekday()] += 1

            remaining = needed - len(static_picks[:needed])
            if remaining <= 0:
                continue

            # Dedicated closers get priority — assign them first
            dedicated_pool = [m for m in rotation_pool
                              if m.dedicated_closer and m.id not in daily_assigned[day_date]]
            for m in dedicated_pool[:remaining]:
                assignments.append(_make_assignment(m, slot, day_date, day_type, "dedicated_closer"))
                _track_day(m.id, day_date, slot)
                _update_state_for_assignment(states[m.id], day_date, slot)
                week_closings[m.id] = week_closings.get(m.id, 0) + 1
                closer_days.setdefault(m.id, []).append(day_date)
                if closer_dow_counts is not None:
                    closer_dow_counts[m.id][day_date.weekday()] += 1
            remaining -= len(dedicated_pool[:remaining])
            if remaining <= 0:
                continue

            # Rotation assignment respecting max_closings_per_week
            log.debug("  Phase1 closer rotation on %s: dedicated_pool=%d, rotation_pool=%d, remaining=%d",
                     day_date, len(dedicated_pool), len(rotation_pool), remaining)
            scored = []
            for m in rotation_pool:
                if m.id in daily_assigned[day_date]:
                    continue
                if m.dedicated_closer:
                    continue  # already assigned above
                # Hard cap: skip members who already hit their weekly closing limit
                max_pw = m.max_closings_per_week or 1
                if week_closings.get(m.id, 0) >= max_pw:
                    continue
                # Enhancement: max consecutive days
                if not _consecutive_ok(m, day_date):
                    continue
                # Enhancement: max hours guardrail
                if not _hours_ok(m, slot):
                    continue
                # Enhancement: consistent start — hard filter
                if not _consistent_start_ok(m, slot):
                    continue
                dow = day_date.weekday()
                st = states[m.id]
                s = _fairness_score(m, st, day_date, slot, week_closings)
                # Enhancement: preferred days off + pairing
                s += _preferred_off_penalty(m, day_date)
                s += _pairing_score(m, day_date, daily_assigned[day_date])
                dow_cnt = closer_dow_counts[m.id][dow] if closer_dow_counts else 0
                deficit = -_contract_deficit(m.id)
                # Raw close count for primary fairness — rotation_order
                # breaks ties between members with equal counts so week 1
                # goes to rotation_order 1, week 2 to rotation_order 2, etc.
                if dow < 5:
                    raw_count = st.weekday_close_count or 0
                elif dow == 5:
                    raw_count = st.saturday_count or 0
                else:
                    raw_count = st.sunday_count or 0
                rot_ord = getattr(m, 'rotation_order', 0) or 0
                scored.append((deficit, dow_cnt, raw_count, rot_ord, s, m))
            scored.sort(key=lambda x: (x[0], x[1], x[2], x[3], x[4]))
            log.debug("  Phase1 scored candidates on %s: %s", day_date,
                     [(getattr(x[5], 'id', '?'), x[0], x[1], x[2], x[3], x[4]) for x in scored[:5]])

            for _, _, _, _, _, m in scored[:remaining]:
                emp_name = m.employee.first_name + " " + m.employee.last_name if m.employee else f"emp#{m.employee_id}"
                log.debug("  Phase1 ASSIGN closer: %s (id=%s) on %s (max_close/wk=%s, current_wk_closings=%s)",
                         emp_name, m.id, day_date, m.max_closings_per_week, week_closings.get(m.id, 0))
                assignments.append(_make_assignment(m, slot, day_date, day_type, "rotation"))
                _track_day(m.id, day_date, slot)
                _update_state_for_assignment(states[m.id], day_date, slot)
                week_closings[m.id] = week_closings.get(m.id, 0) + 1
                closer_days.setdefault(m.id, []).append(day_date)
                if closer_dow_counts is not None:
                    closer_dow_counts[m.id][day_date.weekday()] += 1

    # ── Phase 2: Sunday = non-closer rule ──────────────────────
    # If enabled, assign Sunday opener/day slots to members who didn't close this week
    sunday_dates = [d for d in week_days if d.weekday() == 6 and d not in parity_weekend_days]
    if group.sunday_non_closer and sunday_dates:
        closers_this_week = set(week_closings.keys())
        non_closers = [m for m in members if m.id not in closers_this_week]

        for sun_date in sunday_dates:
            day_type = _detect_day_type(sun_date, stat_dates)
            applicable_slots = _get_applicable_slots(slots_by_day_type, day_type)
            non_closer_slots = [s for s in applicable_slots if s.role != "closer"]

            for slot in non_closer_slots:
                needed = _slots_needed(slot, group, day_type)
                eligible = _get_eligible_members(
                    non_closers, sun_date, slot, pto_dates, states, group)
                eligible = [m for m in eligible
                           if m.id not in daily_assigned[sun_date]]

                # Dedicated weekend members first
                dedicated = [m for m in eligible
                            if _is_weekend_primary_for(m, sun_date, group)]
                pool = dedicated if dedicated else eligible

                # Sort by sunday_count for fair rotation
                pool.sort(key=lambda m: (
                    (states[m.id].sunday_count or 0),
                    states[m.id].weekend_count,
                    getattr(m, 'rotation_order', 0) or 0
                ))

                for m in pool[:needed]:
                    assignments.append(_make_assignment(m, slot, sun_date, day_type, "sunday_rule"))
                    _track_day(m.id, sun_date, slot)
                    _update_state_for_assignment(states[m.id], sun_date, slot)

                remaining = needed - len(pool[:needed])
                if remaining > 0:
                    rest = [m for m in eligible if m not in pool[:needed]]
                    rest.sort(key=lambda m: (
                        (states[m.id].sunday_count or 0),
                        states[m.id].weekend_count,
                        getattr(m, 'rotation_order', 0) or 0
                    ))
                    for m in rest[:remaining]:
                        assignments.append(_make_assignment(m, slot, sun_date, day_type, "sunday_rule"))
                        _track_day(m.id, sun_date, slot)
                        _update_state_for_assignment(states[m.id], sun_date, slot)

    # ── Phase 3: Assign opener slots ───────────────────────────
    for day_date in week_days:
        if day_date in parity_weekend_days:
            continue  # already handled by parity logic
        day_type = _detect_day_type(day_date, stat_dates)
        applicable_slots = _get_applicable_slots(slots_by_day_type, day_type)
        opener_slots = [s for s in applicable_slots if s.role == "opener"]

        for slot in opener_slots:
            needed = _slots_needed(slot, group, day_type)
            eligible = _get_eligible_members(members, day_date, slot, pto_dates, states, group)

            # Static/dedicated first
            static_picks, rotation_pool = _split_static_rotation(
                eligible, day_date, slot, group, daily_assigned[day_date]
            )

            for m in static_picks[:needed]:
                assignments.append(_make_assignment(m, slot, day_date, day_type, "static"))
                _track_day(m.id, day_date, slot)
                _update_state_for_assignment(states[m.id], day_date, slot)

            remaining = needed - len(static_picks[:needed])
            if remaining <= 0:
                continue

            pool = [m for m in rotation_pool if m.id not in daily_assigned[day_date]]

            # Dedicated openers get priority
            dedicated_pool = [m for m in pool if m.dedicated_opener]
            for m in dedicated_pool[:remaining]:
                assignments.append(_make_assignment(m, slot, day_date, day_type, "dedicated_opener"))
                _track_day(m.id, day_date, slot)
                _update_state_for_assignment(states[m.id], day_date, slot)
                week_openings[m.id] = week_openings.get(m.id, 0) + 1
                if opener_dow_counts:
                    opener_dow_counts[m.id][day_date.weekday()] += 1
            remaining -= len(dedicated_pool[:remaining])
            if remaining <= 0:
                continue

            pool = [m for m in pool if not m.dedicated_opener]
            # Enhancement: filter by consecutive days, hours, and max openings cap
            pool = [m for m in pool if _consecutive_ok(m, day_date) and _hours_ok(m, slot)]
            pool = [m for m in pool if m.max_openings_per_week is None or week_openings.get(m.id, 0) < m.max_openings_per_week]
            # Enhancement: consistent start — hard filter with fallback
            pool = _apply_consistent_start_filter(pool, slot)
            # Sort by opener_count for fair rotation across all day types
            _dow = day_date.weekday()
            if day_type in ("saturday", "sunday"):
                pool.sort(key=lambda m: (
                    -_contract_deficit(m.id),
                    _preferred_off_penalty(m, day_date),
                    _pairing_score(m, day_date, daily_assigned[day_date]),
                    (states[m.id].saturday_count or 0) if day_type == "saturday" else (states[m.id].sunday_count or 0),
                    states[m.id].weekend_count,
                    getattr(m, 'rotation_order', 0) or 0
                ))
            else:
                # Primary: contract deficit (members further below contract picked first)
                # Then: consistent_start preference, day-of-week opener count, global opener count
                pool.sort(key=lambda m: (
                    -_contract_deficit(m.id),
                    _consistent_start_penalty(m, slot),
                    _preferred_off_penalty(m, day_date),
                    _pairing_score(m, day_date, daily_assigned[day_date]),
                    opener_dow_counts[m.id][_dow] if opener_dow_counts else 0,
                    (states[m.id].opener_count or 0),
                    getattr(m, 'rotation_order', 0) or 0
                ))
            for m in pool[:remaining]:
                assignments.append(_make_assignment(m, slot, day_date, day_type, "rotation"))
                _track_day(m.id, day_date, slot)
                _update_state_for_assignment(states[m.id], day_date, slot)
                week_openings[m.id] = week_openings.get(m.id, 0) + 1
                if opener_dow_counts:
                    opener_dow_counts[m.id][_dow] += 1

    # ── Phase 4: Assign day shift slots (everything else) ──────
    for day_date in week_days:
        if day_date in parity_weekend_days:
            continue  # parity weekends = single person, no day shifts from this group
        day_type = _detect_day_type(day_date, stat_dates)
        applicable_slots = _get_applicable_slots(slots_by_day_type, day_type)
        day_slots = [s for s in applicable_slots
                     if s.role not in ("closer", "opener")]

        for slot in day_slots:
            needed = _slots_needed(slot, group, day_type)
            eligible = _get_eligible_members(members, day_date, slot, pto_dates, states, group)

            # For day shifts, skip members already assigned today (unless split shift eligible)
            available = [m for m in eligible if _is_available_for_slot(m, day_date, slot)]

            # Static/dedicated first
            static_picks = []
            rotation_pool = []
            for m in available:
                if _is_static_assignment(m, day_date, slot.role):
                    static_picks.append(m)
                elif _is_weekend_primary_for(m, day_date, group) and day_type in ("saturday", "sunday"):
                    static_picks.append(m)
                else:
                    rotation_pool.append(m)

            for m in static_picks[:needed]:
                assignments.append(_make_assignment(m, slot, day_date, day_type, "static"))
                _track_day(m.id, day_date, slot)
                _update_state_for_assignment(states[m.id], day_date, slot)

            remaining = needed - len(static_picks[:needed])
            if remaining <= 0:
                continue

            # Enhancement: filter by consecutive days, hours, and max mids cap
            rotation_pool = [m for m in rotation_pool if _consecutive_ok(m, day_date) and _hours_ok(m, slot)]
            rotation_pool = [m for m in rotation_pool if m.max_mids_per_week is None or week_mids.get(m.id, 0) < m.max_mids_per_week]
            # Enhancement: consistent start — hard filter with fallback
            rotation_pool = _apply_consistent_start_filter(rotation_pool, slot)

            # For day shifts, prefer members who have the fewest total assignments
            # On weekends, also factor in day-specific count for fair rotation
            _slot_role = slot.label or slot.role
            if day_type in ("saturday", "sunday"):
                rotation_pool.sort(key=lambda m: (
                    -_contract_deficit(m.id),
                    _preferred_off_penalty(m, day_date),
                    _pairing_score(m, day_date, daily_assigned[day_date]),
                    (states[m.id].saturday_count or 0) if day_type == "saturday" else (states[m.id].sunday_count or 0),
                    states[m.id].weekend_count,
                    sum(1 for d, ids in daily_assigned.items() if m.id in ids),
                    getattr(m, 'rotation_order', 0) or 0
                ))
            else:
                # Primary: contract deficit (members below contract get priority)
                # Then: consistent_start preference, role-specific count, global day_shift_count, weekly assignments
                rotation_pool.sort(key=lambda m: (
                    -_contract_deficit(m.id),
                    _consistent_start_penalty(m, slot),
                    _preferred_off_penalty(m, day_date),
                    _pairing_score(m, day_date, daily_assigned[day_date]),
                    role_counts.get(m.id, {}).get(_slot_role, 0) if role_counts else 0,
                    (states[m.id].day_shift_count or 0),
                    sum(1 for d, ids in daily_assigned.items() if m.id in ids),
                    getattr(m, 'rotation_order', 0) or 0
                ))

            for m in rotation_pool[:remaining]:
                assignments.append(_make_assignment(m, slot, day_date, day_type, "rotation"))
                _track_day(m.id, day_date, slot)
                _update_state_for_assignment(states[m.id], day_date, slot)
                week_mids[m.id] = week_mids.get(m.id, 0) + 1
                if role_counts is not None:
                    role_counts.setdefault(m.id, {})[_slot_role] = role_counts.get(m.id, {}).get(_slot_role, 0) + 1

    # ── Phase 5: Contract backfill ───────────────────────────────
    # Members still below their contracted days get doubled-up on
    # weekday non-closer slots (opener / mid shifts).  Weekend and
    # closer slots are never doubled.
    for day_date in week_days:
        day_type = _detect_day_type(day_date, stat_dates)
        if day_type in ("saturday", "sunday"):
            continue  # no doubling on weekends

        # Find members not yet assigned today who still need more days
        # Use _get_eligible_members with a generic "day" role so all
        # exclusion flags, work_days, accommodations etc. are respected.
        # Skip dedicated closer/opener — they only get their dedicated role.
        applicable_slots_p5 = _get_applicable_slots(slots_by_day_type, day_type)
        # Find a non-closer slot to use for eligibility checks
        backfill_slot = None
        for _bs in applicable_slots_p5:
            if _bs.role not in ("closer",):
                backfill_slot = _bs
                break
        if not backfill_slot:
            continue
        unassigned_today = [
            m for m in _get_eligible_members(
                members, day_date, backfill_slot, pto_dates, states, group)
            if m.id not in daily_assigned[day_date]
            and _contract_deficit(m.id) > 0
            and not m.dedicated_closer
            and not m.dedicated_opener
            and _consecutive_ok(m, day_date)
            and _hours_ok(m, backfill_slot)
        ]
        if not unassigned_today:
            continue

        # Sort: biggest deficit first, then fewest total assignments
        unassigned_today.sort(key=lambda m: (
            -_contract_deficit(m.id),
            sum(1 for d, ids in daily_assigned.items() if m.id in ids),
            getattr(m, 'rotation_order', 0) or 0
        ))

        # Pick a non-closer slot to double up on
        applicable_slots = _get_applicable_slots(slots_by_day_type, day_type)
        backfill_slots = [s for s in applicable_slots if s.role != "closer"]
        if not backfill_slots:
            continue

        for m in unassigned_today:
            if _contract_deficit(m.id) <= 0:
                break
            # Pick the slot this member has done least (for variety),
            # hard-filter by consistent start, fallback to all if none match
            m_slots = _apply_consistent_start_filter(backfill_slots, backfill_slots[0])
            m_slots = [s for s in backfill_slots if _consistent_start_ok(m, s)] or backfill_slots
            best_slot = min(m_slots, key=lambda s: (
                _consistent_start_penalty(m, s),
                role_counts.get(m.id, {}).get(s.label or s.role, 0) if role_counts else 0,
                s.sort_order or 0
            ))
            assignments.append(_make_assignment(m, best_slot, day_date, day_type, "backfill"))
            _track_day(m.id, day_date, best_slot)
            _update_state_for_assignment(states[m.id], day_date, best_slot)
            if role_counts is not None:
                _slot_role = best_slot.label or best_slot.role
                role_counts.setdefault(m.id, {})[_slot_role] = role_counts.get(m.id, {}).get(_slot_role, 0) + 1

    return assignments


# ── Slot helpers ───────────────────────────────────────────────

def _get_applicable_slots(slots_by_day_type, day_type):
    """Get slots for this day type, with fallbacks."""
    applicable = slots_by_day_type.get(day_type, [])
    if not applicable and day_type == "stat":
        applicable = slots_by_day_type.get("weekday", [])
    return applicable


def _slots_needed(slot, group, day_type):
    """How many of this slot are needed."""
    if slot.role == "closer":
        return (group.closers_required_weekday
                if day_type == "weekday"
                else group.closers_required_weekend)
    # For opener and day shifts, one per slot definition
    return 1


def _make_assignment(member, slot, day_date, day_type, reason):
    """Create an assignment dict."""
    return {
        "member": member,
        "slot": slot,
        "date": day_date,
        "day_type": day_type,
        "reason": reason,
    }


def _split_static_rotation(eligible, day_date, slot, group, assigned_ids):
    """Split eligible members into static picks and rotation pool."""
    static_picks = []
    rotation_pool = []
    for m in eligible:
        if m.id in assigned_ids:
            continue
        if _is_static_assignment(m, day_date, slot.role):
            static_picks.append(m)
        elif _is_weekend_primary_for(m, day_date, group):
            static_picks.append(m)
        else:
            rotation_pool.append(m)

    # When a dedicated weekend primary exists but is absent, restrict to fill-in pool
    day_type = DOW_FULL[day_date.weekday()]
    if (day_type in ("saturday", "sunday")
            and not static_picks):
        has_primaries = any(
            _is_weekend_primary_for(m, day_date, group)
            for m in eligible if m.active if m.id not in assigned_ids
        )
        if has_primaries:
            fill_in_pool = [m for m in rotation_pool if m.is_fill_in]
            if fill_in_pool:
                rotation_pool = fill_in_pool

    return static_picks, rotation_pool


def _is_available(member, day_date, pto_dates):
    """Check if member is available (not on PTO, works this day)."""
    emp_pto = pto_dates.get(member.employee_id, set())
    if day_date in emp_pto:
        return False
    work_days = _parse_json(member.work_days)
    if work_days is not None:
        dow = day_date.weekday()
        if DOW_ABBR[dow] not in work_days and DOW_FULL[dow] not in work_days:
            return False
    return True


_thu_debug_done = False  # module-level flag for one-shot Thursday debug

def _get_eligible_members(members, day_date, slot, pto_dates, states, group):
    """Filter members eligible for a specific slot on a specific day."""
    global _thu_debug_done
    dow = day_date.weekday()
    day_abbr = DOW_ABBR[dow]
    day_full = DOW_FULL[dow]
    role = slot.role

    # One-shot detailed debug for first Thursday closer (full member list only, skip Phase 0 single-member calls)
    trace = (dow == 3 and role == "closer" and not _thu_debug_done and len(members) > 1)
    if trace:
        _thu_debug_done = True
        log.debug("  THU-TRACE %s: day_abbr=%s day_full=%s role=%s slot_start=%s slot_end=%s members=%d",
                 day_date, day_abbr, day_full, role, slot.shift_start, slot.shift_end, len(members))

    eligible = []
    for m in members:
        if m.is_display_only:
            if trace:
                log.debug("    m%d SKIP display_only", m.id)
            continue

        # ── Role exclusion flags ──
        if role == "closer" and (m.exclude_from_closing or m.dedicated_opener):
            if trace:
                log.debug("    m%d SKIP role_excl close=%s ded_opener=%s", m.id, m.exclude_from_closing, m.dedicated_opener)
            continue
        if role == "opener" and (m.exclude_from_opening or m.dedicated_closer):
            continue
        # Dedicated closer/opener shouldn't get mid-shift roles on weekdays,
        # but they CAN work weekend slots if they are weekend primaries
        if role not in ("closer", "opener"):
            if m.dedicated_closer or m.dedicated_opener:
                if day_full not in ("saturday", "sunday"):
                    continue
                # On weekends, only allow if they are a weekend primary for this day
                if not _is_weekend_primary_for(m, day_date, group):
                    continue

        # ── Saturday / Sunday exclusion ──
        if day_full == "saturday" and m.exclude_from_saturday:
            continue
        if day_full == "sunday" and m.exclude_from_sunday:
            continue

        # Check work_days
        work_days = _parse_json(m.work_days)
        if work_days is not None:
            if day_abbr not in work_days and day_full not in work_days:
                if trace:
                    log.debug("    m%d SKIP work_days=%s abbr=%s full=%s", m.id, work_days, day_abbr, day_full)
                continue

        # Check PTO
        emp_pto = pto_dates.get(m.employee_id, set())
        if day_date in emp_pto:
            if trace:
                log.debug("    m%d SKIP pto", m.id)
            continue

        # For closer slots: check accommodation_no_close
        if role == "closer" and m.accommodation_no_close:
            if trace:
                log.debug("    m%d SKIP accom_no_close", m.id)
            continue

        # For closer slots: check excluded_closing_days
        if role == "closer":
            excluded = _parse_json(m.excluded_closing_days)
            if excluded and (day_abbr in excluded or day_full in excluded):
                if trace:
                    log.debug("    m%d SKIP excl_closing raw=%r parsed=%s abbr=%s full=%s",
                             m.id, m.excluded_closing_days, excluded, day_abbr, day_full)
                continue

        # For opener slots: check excluded_opening_days
        if role == "opener":
            excluded_open = _parse_json(m.excluded_opening_days)
            if excluded_open and (day_abbr in excluded_open or day_full in excluded_open):
                if trace:
                    log.debug("    m%d SKIP excl_opening raw=%r parsed=%s abbr=%s full=%s",
                             m.id, m.excluded_opening_days, excluded_open, day_abbr, day_full)
                continue

        # Check static_days_json — if member has a static assignment for a
        # DIFFERENT role on this day, skip them for this slot
        static = _parse_json(m.static_days_json)
        if static and day_full in static:
            static_role = static[day_full]
            if static_role != role:
                if trace:
                    log.debug("    m%d SKIP static_days=%s", m.id, static)
                continue

        # For accommodation: check if shift times fit within accommodated window
        if m.accommodation_start and slot.shift_start:
            if slot.shift_start < m.accommodation_start:
                if trace:
                    log.debug("    m%d SKIP accom_start m=%s slot=%s", m.id, m.accommodation_start, slot.shift_start)
                continue
        if m.accommodation_end and slot.shift_end:
            if slot.shift_end > m.accommodation_end:
                if trace:
                    log.debug("    m%d SKIP accom_end m=%s slot=%s", m.id, m.accommodation_end, slot.shift_end)
                continue

        if trace:
            log.debug("    m%d PASS", m.id)
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
    """Check if member is a dedicated weekend worker for this day.

    Honoured whenever the member's dedicated checkboxes are set, regardless
    of the group's weekend_mode — a member flagged Dedicated Saturday should
    always be preferred for Saturday even if the group is in 'rotate' mode.
    """
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

    dow = day_date.weekday()
    if role == "closer":
        if dow < 5:
            # Use weekday-only close count so members excluded from
            # weekend closings (e.g. accommodation) still get their
            # fair share of weekday closing shifts.
            count = (state.weekday_close_count or 0)
        elif dow == 5:
            count = (state.saturday_count or 0)
        else:
            count = (state.sunday_count or 0)
    elif dow == 5:
        count = (state.saturday_count or 0)
    elif dow == 6:
        count = (state.sunday_count or 0)
    else:
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
        distance = (actual_weekday - target_weekday) % 5
        if distance > 0:
            score += distance * 5

    # Max-per-week cap check (dedicated closers bypass the cap)
    if role == "closer" and not member.dedicated_closer:
        max_pw = member.max_closings_per_week or 1
        if week_closings.get(member.id, 0) >= max_pw:
            score += 10000  # effectively skip

    return score


def _update_state_for_assignment(state, day_date, slot):
    """Update fairness counters after assigning a slot."""
    # Update consecutive day tracking
    last_d = getattr(state, 'last_work_date', None)
    if last_d and (day_date - last_d).days == 1:
        state.consecutive_days = (getattr(state, 'consecutive_days', 0) or 0) + 1
    elif last_d and (day_date - last_d).days == 0:
        pass  # same day
    else:
        state.consecutive_days = 1
    state.last_work_date = day_date

    # Update per-role counts for role rotation tracking
    role_key = slot.label or slot.role
    try:
        rc = json.loads(state.role_counts_json) if getattr(state, 'role_counts_json', None) else {}
    except (json.JSONDecodeError, TypeError):
        rc = {}
    rc[role_key] = rc.get(role_key, 0) + 1
    state.role_counts_json = json.dumps(rc)

    dow = day_date.weekday()
    if slot.role == "closer":
        if dow < 5:  # weekday
            state.weekday_close_count += 1
            state.weekday_rotation_index = (dow + 1) % 5
        elif dow in (5, 6):  # weekend
            state.weekend_count += 1
            if dow == 5:
                state.saturday_count = (state.saturday_count or 0) + 1
            else:
                state.sunday_count = (state.sunday_count or 0) + 1
            state.last_weekend_date = day_date
        state.last_close_date = day_date
        state.last_close_weekday = dow
    elif slot.role == "opener":
        state.opener_count = (state.opener_count or 0) + 1
        if dow in (5, 6):
            state.weekend_count += 1
            if dow == 5:
                state.saturday_count = (state.saturday_count or 0) + 1
            else:
                state.sunday_count = (state.sunday_count or 0) + 1
            state.last_weekend_date = day_date
    else:  # mid shift / day shift
        state.day_shift_count = (state.day_shift_count or 0) + 1
        if dow in (5, 6):
            state.weekend_count += 1
            if dow == 5:
                state.saturday_count = (state.saturday_count or 0) + 1
            else:
                state.sunday_count = (state.sunday_count or 0) + 1
            state.last_weekend_date = day_date


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

        # Use label if available, otherwise role
        shift_type = slot.label or slot.role

        row = Schedule(
            employee_id=member.employee_id,
            planning_unit_id=group.planning_unit_id,
            schedule_date=a["date"],
            shift_start=slot.shift_start,
            shift_end=slot.shift_end,
            shift_type=shift_type,
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
    slot = a["slot"]
    return {
        "date": a["date"].isoformat(),
        "employee_name": emp.full_name if emp else "",
        "employee_id": emp.employee_id if emp else "",
        "role": slot.label or slot.role,
        "day_type": a["day_type"],
        "shift_start": slot.shift_start.strftime("%H:%M"),
        "shift_end": slot.shift_end.strftime("%H:%M"),
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
