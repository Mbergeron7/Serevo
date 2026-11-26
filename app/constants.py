"""Application-wide constants for Serevo WFM."""

# ── LOB (Line of Business) sentinel ──────────────────────────────
# Templates send "All" as the dropdown value; Python normalises it
# to LOB_ALL (empty string) meaning "no filter / all LOBs".
LOB_ALL = ""
LOB_ALL_LABEL = "All"          # what the templates send


# ── Default timezone ─────────────────────────────────────────────
DEFAULT_TIMEZONE = "America/Toronto"
DEFAULT_ACD_TIMEZONE = "America/Chicago"


def to_local(dt, tz_name=None):
    """Convert a datetime to the local timezone for display.

    Handles both aware (UTC) and naive (assumed UTC) datetimes.
    Returns a naive datetime in the local timezone, or the original
    value unchanged if conversion fails or dt is None.
    """
    if dt is None:
        return None
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(tz_name or DEFAULT_TIMEZONE)
        from datetime import timezone
        if dt.tzinfo is None:
            # Naive datetime — assume UTC
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(tz).replace(tzinfo=None)
    except Exception:
        return dt
