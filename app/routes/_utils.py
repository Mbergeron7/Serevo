"""
Shared helpers for route blueprints.
"""

import logging

log = logging.getLogger(__name__)


def get_sheet():
    """Return the capacity Google Sheet object, or None."""
    try:
        from app.data_source import _open_capacity_sheet
        sheet, err = _open_capacity_sheet()
        if err:
            log.warning("Sheet open error: %s", err)
            return None
        return sheet
    except Exception as e:
        log.warning("Sheet import error: %s", e)
        return None
