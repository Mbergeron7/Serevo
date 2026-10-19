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

    # Determine if we have closer slots (enables week-level planning)
    has_closers = any(
        s.role == "closer"
        for slots in slots_by_day_type.values()
        for s in slots
    )

    # Weekend close week parity: "odd" or "even" ISO week numbers
    wk_parity = group.weekend_close_week_parity  # None = every weekend

    assignments = []

    # ── In-memory per-day-of-week and per-role counters ──
    # These track distribution WITHIN this generation run for variety
    closer_dow_counts = {m.id: [0]*7 for m in active_members}  # per day-of-week closer counts
    opener_dow_counts = {m.id: [0]*7 for m in active_members}  # per day-of-week opener counts
    role_counts = {m.id: {} for m in active_members}           # per role counts (for mid shifts)

    if has_closers:
        # ── Week-level planning mode ──
        # Process week by week to enforce one-closing-per-week and Sunday rule
        weeks = _split_into_weeks(start_date, end_date)
        for week_start, week_end in weeks:
            week_days = _date_range(max(week_start, start_date), min(week_end, end_date))
            week_assignments = _process_week(
                week_days, group, active_members, slots_by_day_type,
                stat_dates, pto_dates, states, wk_parity,
                closer_dow_counts, opener_dow_counts, role_counts
            )
            assignments.extend(week_assignments)
    else:
        # ── Simple day-by-day mode (no closers, just day shifts) ──
        current = start_date
        while current <= end_date:
            day_assignments = _process_day_simple(
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
    """Group slots by day_type → list of slots, sorted by sort_order."""
    out = {}
    for s in slots:
        out.setdefault(s.day_type, []).append(s)
    # Sort each day_type's slots by sort_order
    for dt in out:
        out[dt].sort(key=lambda s: (s.sort_order or 0, s.role))
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
    # Track who got a closing this week
    week_closings = {}  # member_id → count
    # Track which members closed on which day (for Sunday rule)
    closer_days = {}  # member_id → [dates]

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
            daily_assigned[day_date].add(m.id)
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
            static_picks, rotation_pool = _split_static_rotation(
                eligible, day_date, slot, group, daily_assigned[day_date]
            )

            for m in static_picks[:needed]:
                assignments.append(_make_assignment(m, slot, day_date, day_type, "static"))
                daily_assigned[day_date].add(m.id)
                _update_state_for_assignment(states[m.id], day_date, slot)
                week_closings[m.id] = week_closings.get(m.id, 0) + 1
                closer_days.setdefault(m.id, []).append(day_date)
                if closer_dow_counts is not None:
                    closer_dow_counts[m.id][day_date.weekday()] += 1

            remaining = needed - len(static_picks[:needed])
            if remaining <= 0:
                continue

            # Rotation assignment respecting max_closings_per_week
            scored = []
            for m in rotation_pool:
                if m.id in daily_assigned[day_date]:
                    continue
                # Hard cap: skip members who already hit their weekly closing limit
                max_pw = m.max_closings_per_week or 1
                if week_closings.get(m.id, 0) >= max_pw:
                    continue
                dow = day_date.weekday()
                s = _fairness_score(m, states[m.id], day_date, slot, week_closings)
                dow_cnt = closer_dow_counts[m.id][dow] if closer_dow_counts else 0
                scored.append((dow_cnt, s, m))
            scored.sort(key=lambda x: (x[0], x[1], getattr(x[2], 'rotation_order', 0) or 0))

            for _, _, m in scored[:remaining]:
                assignments.append(_make_assignment(m, slot, day_date, day_type, "rotation"))
                daily_assigned[day_date].add(m.id)
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
                eligible = [m for m in non_closers
                           if m.id not in daily_assigned[sun_date]
                           and _is_available(m, sun_date, pto_dates)]

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
                    daily_assigned[sun_date].add(m.id)
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
                        daily_assigned[sun_date].add(m.id)
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
                daily_assigned[day_date].add(m.id)
                _update_state_for_assignment(states[m.id], day_date, slot)

            remaining = needed - len(static_picks[:needed])
            if remaining <= 0:
                continue

            pool = [m for m in rotation_pool if m.id not in daily_assigned[day_date]]
            # Sort by opener_count for fair rotation across all day types
            _dow = day_date.weekday()
            if day_type in ("saturday", "sunday"):
                pool.sort(key=lambda m: (
                    (states[m.id].saturday_count or 0) if day_type == "saturday" else (states[m.id].sunday_count or 0),
                    states[m.id].weekend_count,
                    getattr(m, 'rotation_order', 0) or 0
                ))
            else:
                # Primary: how many times this person opened on THIS weekday
                # Secondary: global opener count for overall balance
                pool.sort(key=lambda m: (
                    opener_dow_counts[m.id][_dow] if opener_dow_counts else 0,
                    (states[m.id].opener_count or 0),
                    getattr(m, 'rotation_order', 0) or 0
                ))
            for m in pool[:remaining]:
                assignments.append(_make_assignment(m, slot, day_date, day_type, "rotation"))
                daily_assigned[day_date].add(m.id)
                _update_state_for_assignment(states[m.id], day_date, slot)
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

            # For day shifts, skip members already assigned today (they have closer/opener)
            available = [m for m in eligible if m.id not in daily_assigned[day_date]]

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
                daily_assigned[day_date].add(m.id)
                _update_state_for_assignment(states[m.id], day_date, slot)

            remaining = needed - len(static_picks[:needed])
            if remaining <= 0:
                continue

            # For day shifts, prefer members who have the fewest total assignments
            # On weekends, also factor in day-specific count for fair rotation
            _slot_role = slot.label or slot.role
            if day_type in ("saturday", "sunday"):
                rotation_pool.sort(key=lambda m: (
                    (states[m.id].saturday_count or 0) if day_type == "saturday" else (states[m.id].sunday_count or 0),
                    states[m.id].weekend_count,
                    sum(1 for d, ids in daily_assigned.items() if m.id in ids),
                    getattr(m, 'rotation_order', 0) or 0
                ))
            else:
                # Primary: how many times this person got THIS specific role
                # Secondary: global day_shift_count, then weekly assignments
                rotation_pool.sort(key=lambda m: (
                    role_counts.get(m.id, {}).get(_slot_role, 0) if role_counts else 0,
                    (states[m.id].day_shift_count or 0),
                    sum(1 for d, ids in daily_assigned.items() if m.id in ids),
                    getattr(m, 'rotation_order', 0) or 0
                ))

            for m in rotation_pool[:remaining]:
                assignments.append(_make_assignment(m, slot, day_date, day_type, "rotation"))
                daily_assigned[day_date].add(m.id)
                _update_state_for_assignment(states[m.id], day_date, slot)
                if role_counts is not None:
                    role_counts.setdefault(m.id, {})[_slot_role] = role_counts.get(m.id, {}).get(_slot_role, 0) + 1

    return assignments


def _process_day_simple(day_date, group, members, slots_by_day_type,
                        stat_dates, pto_dates, states):
    """Process one day in simple mode (no closers — just assign all slots)."""
    day_type = _detect_day_type(day_date, stat_dates)
    applicable_slots = _get_applicable_slots(slots_by_day_type, day_type)
    if not applicable_slots:
        return []

    assignments = []
    assigned_ids = set()

    for slot in applicable_slots:
        needed = _slots_needed(slot, group, day_type)
        eligible = _get_eligible_members(members, day_date, slot, pto_dates, states, group)

        available = [m for m in eligible if m.id not in assigned_ids]
        # Static first
        static_picks = [m for m in available
                       if _is_static_assignment(m, day_date, slot.role)
                       or (_is_weekend_primary_for(m, day_date, group)
                           and day_type in ("saturday", "sunday"))]

        for m in static_picks[:needed]:
            assignments.append(_make_assignment(m, slot, day_date, day_type, "static"))
            assigned_ids.add(m.id)

        remaining = needed - len(static_picks[:needed])
        if remaining <= 0:
            continue

        pool = [m for m in available if m not in static_picks]
        pool.sort(key=lambda m: getattr(m, 'rotation_order', 0) or 0)
        for m in pool[:remaining]:
            assignments.append(_make_assignment(m, slot, day_date, day_type, "rotation"))
            assigned_ids.add(m.id)
            if day_type in ("saturday", "sunday"):
                _update_state_for_assignment(states[m.id], day_date, slot)

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


def _get_eligible_members(members, day_date, slot, pto_dates, states, group):
    """Filter members eligible for a specific slot on a specific day."""
    dow = day_date.weekday()
    day_abbr = DOW_ABBR[dow]
    day_full = DOW_FULL[dow]
    role = slot.role

    eligible = []
    for m in members:
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
            # Use TOTAL closings (weekday + weekend) so members excluded
            # from weekend closings get prioritised on weekdays to keep
            # overall closing counts fair across the team.
            total_closings = (state.weekday_close_count or 0) + \
                             (state.saturday_count or 0) + \
                             (state.sunday_count or 0)
            count = total_closings
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

    # Max-per-week cap check
    max_pw = member.max_closings_per_week or 1
    if role == "closer" and week_closings.get(member.id, 0) >= max_pw:
        score += 10000  # effectively skip

    return score


def _update_state_for_assignment(state, day_date, slot):
    """Update fairness counters after assigning a slot."""
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
