"""Seed CallPotential activity SID -> SegmentCode + ExternalStatusMapping.

Revision ID: u5v6w7x8y9z0
Revises: t4u5v6w7x8y9
Create Date: 2026-10-07
"""
from alembic import op
import sqlalchemy as sa
from datetime import datetime, timezone

revision = "u5v6w7x8y9z0"
down_revision = "t4u5v6w7x8y9"
branch_labels = None
depends_on = None

# CP activity SID -> (label, is_productive, is_paid, activity_type, color)
_SEGMENT_DEFS = {
    "Ready":                 (True,  True,  "presence", "#22c55e"),
    "Offline":               (False, False, "absence",  "#6b7280"),
    "On-call":               (True,  True,  "presence", "#3b82f6"),
    "Cool-Down":             (True,  True,  "presence", "#60a5fa"),
    "Unavailable":           (False, True,  "presence", "#ef4444"),
    "No-Answer":             (False, True,  "presence", "#f97316"),
    "Rejected":              (False, True,  "presence", "#dc2626"),
    "On Break":              (False, True,  "break",    "#f59e0b"),
    "Lunch":                 (False, True,  "break",    "#eab308"),
    "ooq Client Account Work": (True, True, "presence", "#8b5cf6"),
    "No-Mic":                (False, True,  "presence", "#fb923c"),
    "Web Leads":             (True,  True,  "presence", "#14b8a6"),
    "Long Distance":         (True,  True,  "presence", "#06b6d4"),
    "eChat":                 (True,  True,  "presence", "#0ea5e9"),
    "Leader on Duty":        (True,  True,  "presence", "#a855f7"),
    "ooq Meeting":           (True,  True,  "meeting",  "#6366f1"),
    "ooq Training":          (True,  True,  "meeting",  "#818cf8"),
    "ooq Coaching":          (True,  True,  "meeting",  "#7c3aed"),
    "ooq After Shift":       (False, False, "absence",  "#9ca3af"),
    "ooq System Issue":      (False, True,  "presence", "#ef4444"),
    "ooq Personal":          (False, True,  "absence",  "#d946ef"),
    "Cascade":               (False, True,  "presence", "#78716c"),
}

# CP SID -> label
_CP_STATUS_MAP = {
    "WA9c4e93d1de9b472ebb7e3b89426df574":           "Ready",
    "WAd1f6c9952f3d04482bb9b6b28dd9819e":           "Offline",
    "WA9a7153cbe2257eb452ff60067002087b":           "On-call",
    "WA55e3a3eb3b9df10c69b902682ee87fbf":           "Cool-Down",
    "WA3f5159a6c417b72f73c8128f5d3cc0ed":           "Unavailable",
    "WA4090114d9b863e27e396b747e7071d7b":           "No-Answer",
    "WAa8d8e71d8d79415ba585a53ca493521f":           "Rejected",
    "WA960ed92496da0b023b5e69e4f5c783a2":           "On Break",
    "WAa513fc91db454de83aefda3f5b689c5e":           "Lunch",
    "WA009e46be32c8efb7132cfda1b5a42ea8":           "ooq Client Account Work",
    "WA641c3fceafe43e72da443995033777a0":           "No-Mic",
    "WA17446c1b845fe8159134a9e2da69dc8c":           "Web Leads",
    "WA84686f853061b6271f6a43dd6a3c6384":           "Long Distance",
    "WA0d324b251da94519738a9ee65fb89152":           "eChat",
    "WA8cb16293fa633f5b155a5579c4199992":           "Leader on Duty",
    "WAea2d79d49903a266c61e67a346ecf926":           "ooq Meeting",
    "WA61f0205ad33fc03a62d1b21c6edd4cf5":           "ooq Training",
    "WA1eb894d4d481b80387166e248bea38c4":           "ooq Coaching",
    "WAaaf8f99bad17314f6908a2b20faf208a":           "ooq After Shift",
    "WAcafc4a5f5503c8fbab4e19697b81edb3":           "ooq System Issue",
    "WA2a678c82745f6019e3b4fdc994af7f18":           "ooq Personal",
    "WA3f5159a6c417b72f73c8128f5d3cc0ed Duplicate": "Cascade",
}


def upgrade():
    conn = op.get_bind()
    now = datetime.now(timezone.utc)

    # Idempotent: skip if segment_codes already has rows
    existing = conn.execute(sa.text("SELECT COUNT(*) FROM segment_codes")).scalar()
    if existing > 0:
        return

    # 1. Insert SegmentCodes
    for sort_i, (label, (productive, paid, atype, color)) in enumerate(_SEGMENT_DEFS.items()):
        code = label.lower().replace(" ", "_").replace("-", "_")
        conn.execute(sa.text(
            "INSERT INTO segment_codes (code, label, color, is_productive, is_paid, is_default, sort_order, is_active, activity_type, created_at) "
            "VALUES (:code, :label, :color, :prod, :paid, true, :sort, true, :atype, :now)"
        ), {"code": code, "label": label, "color": color, "prod": productive,
            "paid": paid, "sort": sort_i, "atype": atype, "now": now})

    # 2. Insert ExternalStatusMappings linking CP SIDs to SegmentCodes
    for sid, label in _CP_STATUS_MAP.items():
        code = label.lower().replace(" ", "_").replace("-", "_")
        seg_id = conn.execute(
            sa.text("SELECT id FROM segment_codes WHERE code = :code"), {"code": code}
        ).scalar()
        if seg_id:
            conn.execute(sa.text(
                "INSERT INTO external_status_mappings (segment_code_id, external_status, description, created_at) "
                "VALUES (:seg_id, :sid, :desc, :now)"
            ), {"seg_id": seg_id, "sid": sid, "desc": f"CallPotential SID for {label}", "now": now})


def downgrade():
    op.execute("DELETE FROM external_status_mappings")
    op.execute("DELETE FROM segment_codes WHERE is_default = true")
