
"""
capacity_export.py
------------------
Excel export helpers for the three capacity planning views.
Uses openpyxl with full formatting — no post-processing needed.
"""

from openpyxl import Workbook
from openpyxl.styles import (Font, PatternFill, Alignment, Border, Side,
                              GradientFill)
from openpyxl.styles.differential import DifferentialStyle
from openpyxl.formatting.rule import ColorScaleRule, CellIsRule, Rule
from openpyxl.utils import get_column_letter
import io, datetime

# ── Brand colours ────────────────────────────────────────────────
PURPLE_DARK  = "4A148C"
PURPLE_MID   = "6A1B9A"
PURPLE_LIGHT = "E8D5FF"
PURPLE_PALE  = "F3E9FF"
WHITE        = "FFFFFF"
GREY_LIGHT   = "F8F9FA"
GREY_MID     = "E9ECEF"
GREEN_BG     = "D1E7DD"
GREEN_FG     = "0A3622"
RED_BG       = "F8D7DA"
RED_FG       = "842029"
AMBER_BG     = "FFF3CD"
AMBER_FG     = "856404"

def _hdr_font(sz=10):
    return Font(name="Calibri", bold=True, color=WHITE, size=sz)

def _body_font(sz=10, bold=False):
    return Font(name="Calibri", size=sz, bold=bold)

def _fill(hex_color):
    return PatternFill("solid", fgColor=hex_color)

def _border():
    s = Side(style="thin", color="CCCCCC")
    return Border(left=s, right=s, top=s, bottom=s)

def _center():
    return Alignment(horizontal="center", vertical="center", wrap_text=False)

def _left():
    return Alignment(horizontal="left", vertical="center")

def _apply_cf_gap(ws, col_letter, first_row, last_row):
    """Green if >0, red if <0 for gap/req-vs-actual columns."""
    rng = f"{col_letter}{first_row}:{col_letter}{last_row}"
    ws.conditional_formatting.add(rng, CellIsRule(
        operator="greaterThan", formula=["0"],
        fill=_fill(GREEN_BG), font=Font(color=GREEN_FG, bold=True, name="Calibri")))
    ws.conditional_formatting.add(rng, CellIsRule(
        operator="lessThan", formula=["0"],
        fill=_fill(RED_BG), font=Font(color=RED_FG, bold=True, name="Calibri")))


# ================================================================
#  CAPACITY PLAN EXPORT  —  one sheet per LOB
# ================================================================

PLAN_METRICS = [
    ("Forecasted Calls Offered", "forecasted_calls", "#,##0"),
    ("Forecast Calls Answered",  "calls_answered",   "#,##0"),
    ("AHT (seconds)",            "aht",              "0"),
    ("Occupancy",                "occupancy",        "0%"),
    None,  # divider
    ("PSIH without Shrinkage",   "psih_no_shrink",   "#,##0.0"),
    ("PSIH with Shrinkage",      "psih_with_shrink", "#,##0.0"),
    None,
    ("FTE Required",             "fte_required",     "0.0"),
    ("Actual Head Count",        "actual_hc",        "0"),
    ("  Combined HC",            "hc_combined",      "0"),
    ("  English HC",             "hc_en",            "0"),
    ("  French HC",              "hc_fr",            "0"),
    ("Required vs Actual",       "req_vs_actual",    "+0.0;-0.0;0.0"),
    None,
    ("Shrinkage %",              "shrinkage",        "0%"),
    ("Peak Agents Required",     "peak_agents",      "0.00"),
    ("Avg Agents Required",      "avg_agents",       "0.00"),
]

def export_capacity_plan(plan: dict, all_months: list) -> bytes:
    wb = Workbook()
    wb.remove(wb.active)  # remove default sheet

    month_labels = []
    for m in all_months:
        try:
            month_labels.append(datetime.datetime.strptime(m, "%Y-%m").strftime("%b %Y"))
        except Exception:
            month_labels.append(m)

    for lob_idx, (lob, months_data) in enumerate(sorted(plan.items())):
        safe_name = lob[:31].replace("/", "-").replace("\\", "-").replace("?","").replace("*","").replace("[","").replace("]","").replace(":","")
        ws = wb.create_sheet(title=safe_name)
        ws.freeze_panes = "B3"

        # Tab colour cycling between two purples
        ws.sheet_properties.tabColor = PURPLE_MID if lob_idx % 2 == 0 else "7B1FA2"

        # ── Column widths ──────────────────────────────────────────
        ws.column_dimensions["A"].width = 28
        for i in range(len(all_months)):
            ws.column_dimensions[get_column_letter(i + 2)].width = 12

        # ── Row 1: LOB header banner ───────────────────────────────
        ws.row_dimensions[1].height = 24
        c = ws.cell(1, 1, lob)
        c.font       = Font(name="Calibri", bold=True, size=13, color=WHITE)
        c.fill       = _fill(PURPLE_DARK)
        c.alignment  = _left()
        for i in range(len(all_months)):
            ws.cell(1, i + 2).fill = _fill(PURPLE_DARK)

        # ── Row 2: month headers ───────────────────────────────────
        ws.row_dimensions[2].height = 18
        ws.cell(2, 1, "Metric").font      = _hdr_font()
        ws.cell(2, 1).fill               = _fill(PURPLE_MID)
        ws.cell(2, 1).alignment          = _left()
        for i, label in enumerate(month_labels):
            c = ws.cell(2, i + 2, label)
            c.font      = _hdr_font()
            c.fill      = _fill(PURPLE_MID)
            c.alignment = _center()

        # ── Data rows ──────────────────────────────────────────────
        row = 3
        req_vs_actual_rows = []
        fte_rows           = []

        for metric in PLAN_METRICS:
            if metric is None:
                # divider
                ws.row_dimensions[row].height = 4
                for col in range(1, len(all_months) + 2):
                    ws.cell(row, col).fill = _fill("E9D8F7")
                row += 1
                continue

            label, key, fmt = metric
            is_highlight = key in ("fte_required", "actual_hc", "req_vs_actual")
            bg = PURPLE_PALE if is_highlight else (GREY_LIGHT if row % 2 == 0 else WHITE)

            ws.row_dimensions[row].height = 16
            lc = ws.cell(row, 1, label)
            lc.font      = Font(name="Calibri", size=10,
                                bold=is_highlight, color="333333")
            lc.fill      = _fill("F0E6FF" if is_highlight else "FAFAFA")
            lc.alignment = _left()
            lc.border    = _border()

            for i, m in enumerate(all_months):
                d   = months_data.get(m)
                val = None
                if d:
                    raw = d.get(key)
                    if raw is not None:
                        try: val = float(raw)
                        except Exception: val = raw
                dc = ws.cell(row, i + 2, val if val is not None else "—")
                dc.number_format = fmt
                dc.alignment     = _center()
                dc.fill          = _fill("F0E6FF" if is_highlight else bg)
                dc.font          = Font(name="Calibri", size=10,
                                        bold=is_highlight)
                dc.border        = _border()

            if key == "req_vs_actual": req_vs_actual_rows.append(row)
            if key == "fte_required":  fte_rows.append(row)
            row += 1

        # ── Conditional formatting ─────────────────────────────────
        for r in req_vs_actual_rows:
            rng = f"B{r}:{get_column_letter(len(all_months)+1)}{r}"
            ws.conditional_formatting.add(rng, CellIsRule(
                operator="greaterThan", formula=["0"],
                fill=_fill(GREEN_BG),
                font=Font(color=GREEN_FG, bold=True, name="Calibri")))
            ws.conditional_formatting.add(rng, CellIsRule(
                operator="lessThan", formula=["0"],
                fill=_fill(RED_BG),
                font=Font(color=RED_FG, bold=True, name="Calibri")))

        for r in fte_rows:
            rng = f"B{r}:{get_column_letter(len(all_months)+1)}{r}"
            ws.conditional_formatting.add(rng, ColorScaleRule(
                start_type="min", start_color="63BE7B",
                mid_type="percentile", mid_value=50, mid_color="FFEB84",
                end_type="max", end_color="F8696B"))

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.read()


# ================================================================
#  CAPACITY SUMMARY EXPORT  —  single formatted table
# ================================================================

SUMMARY_COLS = [
    # Order matches the on-screen summary table exactly
    ("LOB",             "lob",           30, "left",   None),
    ("Month",           "date_range",    18, "center", None),
    ("Total Calls",     "total_calls",   14, "center", "#,##0"),
    ("Actual HC",       "active_hc",     12, "center", "0"),
    ("Combined HC",     "hc_combined",   12, "center", "0"),
    ("English HC",      "hc_en",         11, "center", "0"),
    ("French HC",       "hc_fr",         11, "center", "0"),
    ("FTE Required",    "fte_required",  14, "center", "0.0"),
    ("Gap (HC−FTE)",    "gap",           14, "center", "+0.0;-0.0;0.0"),
    ("AHT (s)",         "aht",           10, "center", "0"),
    ("Peak Agents",     "peak_agents",   13, "center", "0.00"),
    ("Avg Agents",      "avg_agents",    13, "center", "0.00"),
]

def export_capacity_summary(summary_rows: list) -> bytes:
    wb  = Workbook()
    ws  = wb.active
    ws.title = "Capacity Summary"
    ws.sheet_properties.tabColor = PURPLE_MID
    ws.freeze_panes = "A3"

    # ── Row 1: title ───────────────────────────────────────────
    ws.row_dimensions[1].height = 26
    c = ws.cell(1, 1, "Capacity Summary  —  StorageVault Canada")
    c.font      = Font(name="Calibri", bold=True, size=14, color=WHITE)
    c.fill      = _fill(PURPLE_DARK)
    c.alignment = _left()
    ws.merge_cells(f"A1:{get_column_letter(len(SUMMARY_COLS))}1")

    # ── Row 2: headers ─────────────────────────────────────────
    ws.row_dimensions[2].height = 18
    for ci, (label, _, width, align, _fmt) in enumerate(SUMMARY_COLS, 1):
        ws.column_dimensions[get_column_letter(ci)].width = width
        c = ws.cell(2, ci, label)
        c.font      = _hdr_font()
        c.fill      = _fill(PURPLE_MID)
        c.alignment = _center() if align == "center" else _left()
        c.border    = _border()

    # ── Data rows ──────────────────────────────────────────────
    # Colour helpers for FTE and Gap cells
    FTE_LOW  = "C6EFCE"; FTE_MID  = "FFEB84"; FTE_HIGH = "F8696B"
    GAP_POS  = "D1E7DD"; GAP_NEG  = "F8D7DA"
    # Compute FTE min/max for heatmap thresholds
    fte_vals = [r.get("fte_required", 0) for r in summary_rows if r.get("fte_required")]
    fte_min  = min(fte_vals) if fte_vals else 0
    fte_max  = max(fte_vals) if fte_vals else 1
    fte_rng  = (fte_max - fte_min) or 1

    prev_lob = None
    lob_shade = False
    for ri, row_data in enumerate(summary_rows, 3):
        # Alternate shading by LOB group (not by row) so grouped months are clear
        if row_data.get("lob") != prev_lob:
            lob_shade = not lob_shade
            prev_lob = row_data.get("lob")
        bg = GREY_LIGHT if lob_shade else WHITE
        ws.row_dimensions[ri].height = 17
        for ci, (label, key, width, align, fmt) in enumerate(SUMMARY_COLS, 1):
            val = row_data.get(key, "")
            c   = ws.cell(ri, ci, val)
            c.font      = _body_font(bold=(key == "lob"))
            c.alignment = _center() if align == "center" else _left()
            c.border    = _border()
            if fmt:
                c.number_format = fmt
            # FTE Required — heatmap colours
            if key == "fte_required" and val != "":
                pct = (val - fte_min) / fte_rng
                fill_hex = FTE_LOW if pct < 0.33 else (FTE_MID if pct < 0.66 else FTE_HIGH)
                c.fill = _fill(fill_hex)
                txt_col = "1B5E20" if pct < 0.33 else ("333300" if pct < 0.66 else "FFFFFF")
                c.font = Font(name="Calibri", bold=True, size=10, color=txt_col)
            # Gap — green/red colour coding
            elif key == "gap" and val != "":
                if val > 0:
                    c.fill = _fill(GAP_POS)
                    c.font = Font(name="Calibri", bold=True, size=10, color="0A3622")
                elif val < 0:
                    c.fill = _fill(GAP_NEG)
                    c.font = Font(name="Calibri", bold=True, size=10, color="842029")
                else:
                    c.fill = _fill(bg)
            else:
                c.fill = _fill(bg)

    # ── Totals row ─────────────────────────────────────────
    if summary_rows:
        totals_row = len(summary_rows) + 3
        ws.row_dimensions[totals_row].height = 18
        for ci, (label, key, width, align, fmt) in enumerate(SUMMARY_COLS, 1):
            if ci == 1:
                c = ws.cell(totals_row, ci, "TOTAL")
                c.font      = Font(name="Calibri", bold=True, size=10, color=WHITE)
                c.fill      = _fill(PURPLE_DARK)
                c.alignment = _left()
                c.border    = _border()
            elif key in ("fte_required", "active_hc", "gap", "total_calls",
                         "hc_combined", "hc_en", "hc_fr"):
                total = sum(r.get(key, 0) or 0 for r in summary_rows)
                c = ws.cell(totals_row, ci, round(total, 1))
                c.font         = Font(name="Calibri", bold=True, size=10, color=WHITE)
                c.fill         = _fill(PURPLE_DARK)
                c.alignment    = _center()
                c.border       = _border()
                if fmt: c.number_format = fmt
            else:
                c = ws.cell(totals_row, ci, "")
                c.fill   = _fill(PURPLE_DARK)
                c.border = _border()

    # ── Conditional formatting handled per-cell above ──────────
    if False and len(summary_rows) > 0:  # disabled — colours applied in data loop
        col_l  = get_column_letter(gap_col)
        last_r = len(summary_rows) + 2
        _apply_cf_gap(ws, col_l, 3, last_r)

    # ── FTE colour scale ───────────────────────────────────────
    fte_col = next((i+1 for i,(l,k,*_) in enumerate(SUMMARY_COLS) if k=="fte_required"), None)
    if fte_col and len(summary_rows) > 0:
        rng = f"{get_column_letter(fte_col)}3:{get_column_letter(fte_col)}{len(summary_rows)+2}"
        ws.conditional_formatting.add(rng, ColorScaleRule(
            start_type="min", start_color="63BE7B",
            mid_type="percentile", mid_value=50, mid_color="FFEB84",
            end_type="max", end_color="F8696B"))

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.read()


# ================================================================
#  HEADCOUNT EXPORT  —  single formatted table
# ================================================================

def export_headcount(hc_rows: list) -> bytes:
    wb  = Workbook()
    ws  = wb.active
    ws.title = "Headcount by Planning Unit"
    ws.sheet_properties.tabColor = PURPLE_MID
    ws.freeze_panes = "A3"

    cols = [
        ("Planning Unit", "planning_unit", 28, "left"),
        ("Active",        "active",        12, "center"),
        ("On Leave (LOA)","on_leave",      16, "center"),
        ("LOA %",         "loa_pct",       10, "center"),
        ("Inactive",      "inactive",      12, "center"),
        ("Total",         "total",         10, "center"),
        ("Active %",      None,            10, "center"),
    ]

    # ── Row 1: title ───────────────────────────────────────────
    ws.row_dimensions[1].height = 26
    c = ws.cell(1, 1, "Headcount by Planning Unit  —  StorageVault Canada")
    c.font      = Font(name="Calibri", bold=True, size=14, color=WHITE)
    c.fill      = _fill(PURPLE_DARK)
    c.alignment = _left()
    ws.merge_cells(f"A1:{get_column_letter(len(cols))}1")

    # ── Row 2: headers ─────────────────────────────────────────
    ws.row_dimensions[2].height = 18
    for ci, (label, _, width, align) in enumerate(cols, 1):
        ws.column_dimensions[get_column_letter(ci)].width = width
        c = ws.cell(2, ci, label)
        c.font      = _hdr_font()
        c.fill      = _fill(PURPLE_MID)
        c.alignment = _center() if align == "center" else _left()
        c.border    = _border()

    # ── Data rows ──────────────────────────────────────────────
    for ri, row_data in enumerate(hc_rows, 3):
        bg = GREY_LIGHT if ri % 2 == 0 else WHITE
        ws.row_dimensions[ri].height = 17
        active = row_data.get("active", 0)
        total  = row_data.get("total", 0)
        act_pct = round(active / total * 100, 1) if total > 0 else 0

        values = [
            row_data.get("planning_unit", ""),
            active,
            row_data.get("on_leave", 0),
            row_data.get("loa_pct", 0) / 100,
            row_data.get("inactive", 0),
            total,
            act_pct / 100,
        ]
        fmts = [None, "0", "0", "0.0%", "0", "0", "0.0%"]

        for ci, (val, fmt) in enumerate(zip(values, fmts), 1):
            c = ws.cell(ri, ci, val)
            c.font      = _body_font(bold=(ci == 1))
            c.fill      = _fill(bg)
            c.alignment = _left() if ci == 1 else _center()
            c.border    = _border()
            if fmt:
                c.number_format = fmt

    # ── Totals row ─────────────────────────────────────────────
    if hc_rows:
        tr = len(hc_rows) + 3
        ws.row_dimensions[tr].height = 18
        ta  = sum(r.get("active",   0) for r in hc_rows)
        tl  = sum(r.get("on_leave", 0) for r in hc_rows)
        ti  = sum(r.get("inactive", 0) for r in hc_rows)
        tt  = sum(r.get("total",    0) for r in hc_rows)
        loa_pct = round(tl / tt * 100, 1) if tt > 0 else 0
        act_pct = round(ta / tt * 100, 1) if tt > 0 else 0

        totals = ["TOTAL", ta, tl, loa_pct/100, ti, tt, act_pct/100]
        fmts   = [None, "0", "0", "0.0%", "0", "0", "0.0%"]
        for ci, (val, fmt) in enumerate(zip(totals, fmts), 1):
            c = ws.cell(tr, ci, val)
            c.font      = Font(name="Calibri", bold=True, size=10, color=WHITE)
            c.fill      = _fill(PURPLE_DARK)
            c.alignment = _left() if ci == 1 else _center()
            c.border    = _border()
            if fmt:
                c.number_format = fmt

    # ── Conditional formatting ─────────────────────────────────
    last_data = len(hc_rows) + 2
    if hc_rows:
        # LOA % — amber > 10%, red > 20%
        loa_col = "D"
        ws.conditional_formatting.add(f"{loa_col}3:{loa_col}{last_data}",
            CellIsRule(operator="greaterThan", formula=["0.2"],
                fill=_fill(RED_BG),
                font=Font(color=RED_FG, bold=True, name="Calibri")))
        ws.conditional_formatting.add(f"{loa_col}3:{loa_col}{last_data}",
            CellIsRule(operator="greaterThan", formula=["0.1"],
                fill=_fill(AMBER_BG),
                font=Font(color=AMBER_FG, bold=True, name="Calibri")))

        # Active % — colour scale green→red
        act_col = get_column_letter(7)
        ws.conditional_formatting.add(f"{act_col}3:{act_col}{last_data}",
            ColorScaleRule(
                start_type="min", start_color="F8696B",
                mid_type="num", mid_value=0.85, mid_color="FFEB84",
                end_type="max", end_color="63BE7B"))

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.read()
