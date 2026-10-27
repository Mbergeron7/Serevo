"""Application-wide constants for Serevo WFM."""

# ── LOB (Line of Business) sentinel ──────────────────────────────
# Templates send "All" as the dropdown value; Python normalises it
# to LOB_ALL (empty string) meaning "no filter / all LOBs".
LOB_ALL = ""
LOB_ALL_LABEL = "All"          # what the templates send


# ── Default timezone ─────────────────────────────────────────────
DEFAULT_TIMEZONE = "America/Toronto"
DEFAULT_ACD_TIMEZONE = "America/Chicago"
