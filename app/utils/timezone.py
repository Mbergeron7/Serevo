"""Centralised timezone helpers.

Every module that needs the application or ACD-feed timezone should call
:func:`get_app_timezone` or :func:`get_acd_timezone` instead of
hard-coding a timezone string.
"""

import time as _time
from zoneinfo import ZoneInfo

from app.constants import DEFAULT_TIMEZONE, DEFAULT_ACD_TIMEZONE

# ── Simple TTL cache (avoids hitting the DB every call) ──────────
_cache: dict = {}
_CACHE_TTL = 300  # seconds


def _get_cached_setting(key: str, default: str) -> str:
    """Return an AppSetting value with a 5-minute TTL cache."""
    now = _time.time()
    entry = _cache.get(key)
    if entry and now - entry["ts"] < _CACHE_TTL:
        return entry["val"]

    try:
        from app.models import AppSetting
        val = AppSetting.get(key, default)
    except Exception:
        val = default

    _cache[key] = {"val": val, "ts": now}
    return val


def get_app_timezone_name() -> str:
    """Return the IANA timezone string for the application (e.g. 'America/Toronto')."""
    return _get_cached_setting("timezone", DEFAULT_TIMEZONE)


def get_app_timezone() -> ZoneInfo:
    """Return a :class:`ZoneInfo` for the application timezone."""
    return ZoneInfo(get_app_timezone_name())


def get_acd_timezone_name() -> str:
    """Return the IANA timezone string for the ACD / dialer feed."""
    return _get_cached_setting("acd_timezone", DEFAULT_ACD_TIMEZONE)


def get_acd_timezone() -> ZoneInfo:
    """Return a :class:`ZoneInfo` for the ACD feed timezone."""
    return ZoneInfo(get_acd_timezone_name())


def invalidate_cache():
    """Clear the timezone cache (call after settings change)."""
    _cache.clear()
