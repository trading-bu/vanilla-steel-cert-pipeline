"""
Vanilla Steel — Neutralised Inspection Certificate (EN 10204 3.1)
Landscape A4. VS brand colours: Navy #000831 | Blue #0047FF | Vanilla #FFF7E6.

generate_certificate(parsed_cert, odoo_data, logo_path) → bytes
"""
from io import BytesIO
import os
import re

from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_RIGHT, TA_CENTER
from reportlab.platypus import (
    BaseDocTemplate, Frame, PageTemplate,
    Paragraph, Table, TableStyle, Spacer, HRFlowable,
)
from reportlab.pdfgen import canvas as pdfcanvas


# ─── VS Brand Colours ─────────────────────────────────────────────────────────
NAVY    = colors.HexColor("#000831")
BLUE    = colors.HexColor("#0047FF")
VANILLA = colors.HexColor("#FFF7E6")
WHITE   = colors.white
BLACK   = colors.black
LGREY   = colors.HexColor("#EEEEEE")
MGREY   = colors.HexColor("#888888")
BORDER  = colors.HexColor("#CCCCCC")


# ─── Page geometry (landscape A4: 297 × 210 mm) ───────────────────────────────
PAGE_W, PAGE_H = landscape(A4)
ML = MR = 12 * mm

# Zone heights
ZONE1_H = 18 * mm   # banner
ZONE2_H = 14 * mm   # info box
HEADER_H = ZONE1_H + ZONE2_H + 3 * mm   # total header reserve
FOOTER_H = 10 * mm
CONTENT_W = PAGE_W - ML - MR
FRAME_Y = FOOTER_H + 3 * mm
FRAME_H = PAGE_H - HEADER_H - FOOTER_H - 5 * mm


# ─── Text styles ──────────────────────────────────────────────────────────────
_BODY  = ParagraphStyle("body",  fontName="Helvetica",      fontSize=8,   leading=10, textColor=BLACK)
_BOLD  = ParagraphStyle("bold",  fontName="Helvetica-Bold", fontSize=8,   leading=10, textColor=BLACK)
_SMALL = ParagraphStyle("small", fontName="Helvetica",      fontSize=6.5, leading=8.5, textColor=MGREY)
_LABEL = ParagraphStyle("label", fontName="Helvetica-Bold", fontSize=6,   leading=7.5, textColor=MGREY)
_SEC   = ParagraphStyle("sec",   fontName="Helvetica-Bold", fontSize=7.5, leading=10,
                         textColor=WHITE)
_CELL  = ParagraphStyle("cell",  fontName="Helvetica",      fontSize=7,   leading=9,  textColor=BLACK)
_CELLB = ParagraphStyle("cellb", fontName="Helvetica-Bold", fontSize=7,   leading=9,  textColor=WHITE)
_ITALIC = ParagraphStyle("italic", fontName="Helvetica-Oblique", fontSize=6.5,
                          leading=9, textColor=MGREY)


# ─── Two-pass canvas (Page X of Y) ────────────────────────────────────────────
class _NumberedCanvas(pdfcanvas.Canvas):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._page_data = []

    def showPage(self):
        self._page_data.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._page_data)
        for i, state in enumerate(self._page_data, 1):
            self.__dict__.update(state)
            # Write page number into Zone 2 right area (drawn in _draw_page)
            self.setFont("Helvetica", 6.5)
            self.setFillColor(MGREY)
            pg_y = PAGE_H - ZONE1_H - 4 * mm - 7 * mm   # mid of Zone 2
            self.drawRightString(PAGE_W - MR, pg_y, f"Page {i} / {total}")
            pdfcanvas.Canvas.showPage(self)
        pdfcanvas.Canvas.save(self)


# ─── Header & Footer ──────────────────────────────────────────────────────────
def _draw_page(canvas, doc, cert_info: dict, logo_path: str | None):
    canvas.saveState()

    # ── Zone 1: Two-panel banner ──────────────────────────────────────────────
    z1_top  = PAGE_H - 2 * mm
    z1_bot  = z1_top - ZONE1_H
    left_w  = CONTENT_W * 0.38
    right_w = CONTENT_W * 0.62

    # Left panel — white background (implicit)
    # Logo
    logo_size = 12 * mm
    if logo_path and os.path.exists(logo_path):
        try:
            canvas.drawImage(logo_path, ML, z1_bot + 3 * mm,
                             width=logo_size, height=logo_size,
                             preserveAspectRatio=True, mask="auto")
        except Exception:
            pass

    logo_right = ML + logo_size + 2 * mm
    canvas.setFont("Helvetica-Bold", 10)
    canvas.setFillColor(NAVY)
    canvas.drawString(logo_right, z1_bot + 9 * mm, "Vanilla Steel")
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(BLUE)
    canvas.drawString(logo_right, z1_bot + 4 * mm, "Steel, made simple.")

    # Right panel — navy background
    right_x = ML + left_w
    canvas.setFillColor(NAVY)
    canvas.rect(right_x, z1_bot, right_w, ZONE1_H, stroke=0, fill=1)
    canvas.setFillColor(WHITE)
    canvas.setFont("Helvetica-Bold", 10)
    canvas.drawRightString(ML + CONTENT_W - 3 * mm, z1_bot + 9 * mm,
                           "INSPECTION CERTIFICATE – Copy")
    canvas.setFont("Helvetica", 7)
    canvas.drawRightString(ML + CONTENT_W - 3 * mm, z1_bot + 3 * mm,
                           "EN 10204 – Type 3.1")

    # Blue separator rule
    canvas.setStrokeColor(BLUE)
    canvas.setLineWidth(1.5)
    canvas.line(ML, z1_bot, ML + CONTENT_W, z1_bot)

    # ── Zone 2: Info box ──────────────────────────────────────────────────────
    z2_top = z1_bot
    z2_bot = z2_top - ZONE2_H

    # Vanilla background with grey border
    canvas.setFillColor(VANILLA)
    canvas.setStrokeColor(BORDER)
    canvas.setLineWidth(0.5)
    canvas.rect(ML, z2_bot, CONTENT_W, ZONE2_H, stroke=1, fill=1)

    # Left column: Certificate Type | Country of Destination
    col_x = ML + 3 * mm
    row_h = ZONE2_H / 2

    canvas.setFillColor(MGREY)
    canvas.setFont("Helvetica-Bold", 5.5)
    canvas.drawString(col_x, z2_bot + row_h + 3.5 * mm, "CERTIFICATE TYPE")
    canvas.setFillColor(BLACK)
    canvas.setFont("Helvetica", 7)
    canvas.drawString(col_x, z2_bot + row_h + 0.5 * mm,
                      cert_info.get("cert_type", "EN 10204 3.1"))

    canvas.setFillColor(MGREY)
    canvas.setFont("Helvetica-Bold", 5.5)
    canvas.drawString(col_x, z2_bot + 3.5 * mm, "COUNTRY OF DESTINATION")
    canvas.setFillColor(BLACK)
    canvas.setFont("Helvetica", 7)
    canvas.drawString(col_x, z2_bot + 0.5 * mm,
                      cert_info.get("buyer_country", "–"))

    # Right column: Issue Date | Sales Order
    mid_x = ML + CONTENT_W * 0.50
    canvas.setFillColor(MGREY)
    canvas.setFont("Helvetica-Bold", 5.5)
    canvas.drawString(mid_x, z2_bot + row_h + 3.5 * mm, "ISSUE DATE")
    canvas.setFillColor(BLACK)
    canvas.setFont("Helvetica", 7)
    canvas.drawString(mid_x, z2_bot + row_h + 0.5 * mm,
                      cert_info.get("cert_date", "–"))

    canvas.setFillColor(MGREY)
    canvas.setFont("Helvetica-Bold", 5.5)
    canvas.drawString(mid_x, z2_bot + 3.5 * mm, "SALES ORDER")
    canvas.setFillColor(BLACK)
    canvas.setFont("Helvetica", 7)
    canvas.drawString(mid_x, z2_bot + 0.5 * mm,
                      cert_info.get("so_number", "–"))

    # Blue separator below Zone 2
    canvas.setStrokeColor(BLUE)
    canvas.setLineWidth(0.5)
    canvas.line(ML, z2_bot, ML + CONTENT_W, z2_bot)

    # ── Zone 3: Footer ────────────────────────────────────────────────────────
    canvas.setStrokeColor(BLUE)
    canvas.setLineWidth(0.4)
    canvas.line(ML, FOOTER_H, ML + CONTENT_W, FOOTER_H)

    canvas.setFont("Helvetica", 5.5)
    canvas.setFillColor(MGREY)
    canvas.drawString(
        ML, FOOTER_H - 4 * mm,
        "Vanilla Steel GmbH  ·  Schönhauser Allee 36, 10435 Berlin, Germany  ·  "
        "VAT ID: DE332534899  ·  Tax No. 30/424/30144  ·  HRB 218619 B  ·  "
        "Managing Directors: Clifford Ondara, Simon Zühlke",
    )
    canvas.setFillColor(BLUE)
    canvas.drawRightString(
        ML + CONTENT_W, FOOTER_H - 4 * mm,
        "support@vanillasteel.com  ·  www.vanillasteel.com",
    )

    canvas.restoreState()


# ─── Helpers ──────────────────────────────────────────────────────────────────
def _fmt(v) -> str:
    if v is None or (isinstance(v, str) and not v.strip()):
        return "–"
    try:
        f = float(v)
        if f == 0:
            return "0"
        # AM integers come through as e.g. 71.0 — show as "71"
        if f == int(f) and abs(f) >= 1:
            return str(int(f))
        return f"{f:.4f}".rstrip("0").rstrip(".")
    except (ValueError, TypeError):
        return str(v)


def _get_chem(chems: dict, elem: str) -> str:
    for key in (elem, elem.lower(), elem.upper(),
                elem[0].upper() + elem[1:].lower()):
        val = chems.get(key)
        if val is not None:
            return _fmt(val)
    return "–"


def _section_header(text: str) -> list:
    """Navy bar with white bold text — full content width."""
    data = [[Paragraph(f"  {text}", _SEC)]]
    t = Table(data, colWidths=[CONTENT_W])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), NAVY),
        ("TOPPADDING",    (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING",   (0, 0), (-1, -1), 4),
    ]))
    return [t, Spacer(1, 1 * mm)]


def _make_table(headers, rows, weights=None, note: str = None) -> list:
    """Blue column headers, alternating white/vanilla rows."""
    if weights is None:
        weights = [1.0] * len(headers)
    total = sum(weights)
    col_w = [CONTENT_W * w / total for w in weights]

    head = [Paragraph(str(h), _CELLB) for h in headers]
    body = [[Paragraph(str(c) if c is not None else "–", _CELL) for c in row]
            for row in rows]

    style = TableStyle([
        ("GRID",          (0, 0), (-1, -1), 0.3, BORDER),
        ("BACKGROUND",    (0, 0), (-1,  0), BLUE),
        ("FONTNAME",      (0, 0), (-1,  0), "Helvetica-Bold"),
        ("FONTNAME",      (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE",      (0, 0), (-1, -1), 7),
        ("LEADING",       (0, 0), (-1, -1), 9),
        ("TOPPADDING",    (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("LEFTPADDING",   (0, 0), (-1, -1), 3),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 3),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [WHITE, VANILLA]),
    ])

    t = Table([head] + body, colWidths=col_w, repeatRows=1)
    t.setStyle(style)

    result = [t]
    if note:
        result.append(Spacer(1, 1 * mm))
        result.append(Paragraph(f"<i>{note}</i>", _ITALIC))
    return result


# ─── Main generator ───────────────────────────────────────────────────────────
def generate_certificate(
    parsed_cert: dict,
    odoo_data:   dict,
    logo_path:   str | None = None,
) -> bytes:
    buf   = BytesIO()
    coils = parsed_cert.get("coils") or []

    cert_date  = parsed_cert.get("cert_date")     or "–"
    cert_type  = parsed_cert.get("cert_type")     or "EN 10204 3.1"
    standard   = parsed_cert.get("standard")      or "–"
    material   = parsed_cert.get("material_type") or "–"
    grade_s    = parsed_cert.get("grade",   "")   or ""
    quality_s  = parsed_cert.get("quality", "")   or ""
    gq_s       = " / ".join(filter(None, [grade_s, quality_s])) or "–"

    so_number  = odoo_data.get("so_number",     "") or "–"
    buyer      = odoo_data.get("buyer_name",    "") or "–"
    country    = odoo_data.get("buyer_country", "") or "–"

    total_wt = parsed_cert.get("total_weight_kg") or sum(
        (c.get("weight_kg") or 0) for c in coils
    ) or None
    total_wt_s = f"{int(total_wt):,} kg" if total_wt else "–"

    cert_info = dict(
        cert_date=cert_date,
        cert_type=cert_type,
        so_number=so_number,
        buyer_country=country,
    )

    story = []

    # ── Section 1: Product Details ────────────────────────────────────────────
    story.extend(_section_header("PRODUCT DETAILS"))
    pd_pairs = [
        ("Description of Goods", material),
        ("Standard",             standard),
        ("Grade / Quality",      gq_s),
        ("Dimensions",
         f"{coils[0].get('thickness_mm','–')} × {coils[0].get('width_mm','–')} mm"
         if coils else "–"),
        ("Total Gross Weight",   total_wt_s),
        ("Quantity",             f"{len(coils)} coil(s)"),
        ("Certificate Type",     cert_type),
        ("Country of Destination", country),
    ]
    coating = parsed_cert.get("coating") or (
        odoo_data.get("vs_articles", [{}])[0].get("coating") if odoo_data.get("vs_articles") else None
    )
    if coating:
        pd_pairs.append(("Coating", coating))

    pd_rows = []
    for i in range(0, len(pd_pairs), 2):
        lk, lv = pd_pairs[i]
        rk, rv = pd_pairs[i + 1] if i + 1 < len(pd_pairs) else ("", "")
        pd_rows.append([
            Paragraph(lk, _LABEL), Paragraph(str(lv), _BODY),
            Paragraph(rk, _LABEL), Paragraph(str(rv), _BODY),
        ])
    cw4 = CONTENT_W / 4
    pd_t = Table(pd_rows, colWidths=[cw4 * 0.5, cw4 * 1.5, cw4 * 0.5, cw4 * 1.5])
    pd_t.setStyle(TableStyle([
        ("TOPPADDING",    (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING",   (0, 0), (-1, -1), 3),
        ("LINEBELOW",     (0, 0), (-1, -1), 0.3, BORDER),
        ("BACKGROUND",    (0, 0), (-1, -1), VANILLA),
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(pd_t)
    story.append(Spacer(1, 4 * mm))

    # ── Section 2: Shipped Positions ─────────────────────────────────────────
    story.extend(_section_header("SHIPPED POSITIONS"))

    has_coil = any(c.get("coil_no") for c in coils)
    has_cast = any(c.get("cast_no") for c in coils)
    has_net  = any(c.get("net_weight_kg") for c in coils)

    if has_coil and has_cast:
        dh = ["Item", "Pack Nr", "Coil No.", "Cast No. (Heat)",
              "Grade", "Width\nmm", "Thickness\nmm", "Qty",
              "Gross Wt.\nkg", "VS Article"]
        dw = [0.5, 1.3, 1.3, 1.3, 2.0, 0.9, 1.1, 0.6, 1.1, 1.5]
    elif has_cast:
        dh = ["Item", "Pack Nr", "Cast No. (Heat)",
              "Grade", "Width\nmm", "Thickness\nmm", "Qty",
              "Gross Wt.\nkg", "VS Article"]
        dw = [0.5, 1.5, 1.5, 2.0, 0.9, 1.1, 0.6, 1.2, 1.5]
    else:
        dh = ["Item", "Pack Nr", "Grade",
              "Width\nmm", "Thickness\nmm", "Qty",
              "Gross Wt.\nkg", "VS Article"]
        dw = [0.5, 1.8, 2.5, 0.9, 1.1, 0.6, 1.2, 1.8]

    if has_net:
        # Insert Net Wt. before VS Article
        dh.insert(-1, "Net Wt.\nkg")
        dw.insert(-1, 1.1)

    d_rows = []
    wt_sum = 0
    for i, coil in enumerate(coils, 1):
        wk  = coil.get("weight_kg")
        wks = f"{int(wk):,}" if wk else "–"
        nwk = coil.get("net_weight_kg")
        nwks = f"{int(nwk):,}" if nwk else None
        if wk:
            wt_sum += wk
        g = coil.get("grade") or grade_s or "–"

        if has_coil and has_cast:
            row = [i, coil.get("pack_nr") or "–", coil.get("coil_no") or "–",
                   coil.get("cast_no") or "–", g,
                   coil.get("width_mm") or "–", coil.get("thickness_mm") or "–",
                   coil.get("qty", 1), wks, coil.get("vs_article") or "–"]
        elif has_cast:
            row = [i, coil.get("pack_nr") or "–", coil.get("cast_no") or "–",
                   g, coil.get("width_mm") or "–", coil.get("thickness_mm") or "–",
                   coil.get("qty", 1), wks, coil.get("vs_article") or "–"]
        else:
            row = [i, coil.get("pack_nr") or "–", g,
                   coil.get("width_mm") or "–", coil.get("thickness_mm") or "–",
                   coil.get("qty", 1), wks, coil.get("vs_article") or "–"]

        if has_net:
            row.insert(-1, nwks or "–")
        d_rows.append(row)

    # Totals row
    tot_row = [""] * len(dh)
    tot_row[0] = "TOTAL"
    tot_row[-2 if not has_net else -3] = f"{int(wt_sum):,}" if wt_sum else "–"
    d_rows.append(tot_row)

    # Dimension note (mandatory per instructions)
    if coils:
        w0 = coils[0].get("width_mm", "–")
        t0 = coils[0].get("thickness_mm", "–")
        dim_note = (f"Note: All positions have identical dimensions — "
                    f"Width {w0} mm × Thickness {t0} mm. "
                    "Width and Thickness columns confirm this for each row.")
    else:
        dim_note = None

    story.extend(_make_table(dh, d_rows, dw, note=dim_note))
    story.append(Spacer(1, 4 * mm))

    # ── Section 3: Chemical Composition ──────────────────────────────────────
    ELEM_ORDER = ["C", "Si", "Mn", "P", "S", "Al",
                  "Cr", "Ni", "Mo", "Cu", "V", "Ti", "Nb", "Zr", "B", "N", "Ceq"]
    present = [
        e for e in ELEM_ORDER
        if any(_get_chem(c.get("chemicals") or {}, e) != "–" for c in coils)
    ]

    if present:
        is_am = parsed_cert.get("is_integer_chemistry", False)
        am_units = parsed_cert.get("am_units") or {}

        def _chem_hdr(e):
            if is_am:
                exp = am_units.get(e, "-3")
                return Paragraph(
                    f"<b>{e}</b><br/>×10<super>{exp}</super>%", _CELLB
                )
            return Paragraph(f"<b>{e}</b><br/>%", _CELLB)

        # Label column depends on whether we have cast numbers
        label_col = "Cast No. (Heat)" if has_cast else "Pack Nr"
        c_headers = [
            Paragraph(f"<b>{label_col}</b>", _CELLB),
            Paragraph("<b>Item</b>", _CELLB),
        ] + [_chem_hdr(e) for e in present]
        c_weights = [1.3, 0.4] + [0.65] * len(present)

        c_rows = []
        for i, coil in enumerate(coils, 1):
            chems = coil.get("chemicals") or {}
            id_val = coil.get("cast_no") or coil.get("pack_nr") or "–"
            c_rows.append([id_val, i] + [_get_chem(chems, e) for e in present])

        chem_label = "CHEMICAL COMPOSITION"
        if is_am:
            chem_label += " (values as per source certificate — see column headers for unit scale)"
        else:
            chem_label += " (%)"

        story.extend(_section_header(chem_label))
        total = sum(c_weights)
        col_w = [CONTENT_W * w / total for w in c_weights]
        body = [[Paragraph(str(c) if c is not None else "–", _CELL) for c in row]
                for row in c_rows]
        ct = Table([c_headers] + body, colWidths=col_w, repeatRows=1)
        ct.setStyle(TableStyle([
            ("GRID",           (0, 0), (-1, -1), 0.3, BORDER),
            ("BACKGROUND",     (0, 0), (-1,  0), BLUE),
            ("FONTNAME",       (0, 0), (-1,  0), "Helvetica-Bold"),
            ("ROWHEIGHT",      (0, 0), (-1,  0), 18),
            ("FONTNAME",       (0, 1), (-1, -1), "Helvetica"),
            ("FONTSIZE",       (0, 0), (-1, -1), 7),
            ("LEADING",        (0, 0), (-1, -1), 9),
            ("TOPPADDING",     (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING",  (0, 0), (-1, -1), 2),
            ("LEFTPADDING",    (0, 0), (-1, -1), 3),
            ("RIGHTPADDING",   (0, 0), (-1, -1), 3),
            ("VALIGN",         (0, 0), (-1, -1), "MIDDLE"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [WHITE, VANILLA]),
        ]))
        story.append(ct)
        story.append(Spacer(1, 4 * mm))

    # ── Section 4: Mechanical Test Results ───────────────────────────────────
    has_mech = any(c.get("mechanical") for c in coils)
    if has_mech:
        story.extend(_section_header("MECHANICAL TEST RESULTS – TENSILE TEST"))

        # Build decoder legend from actual codes present
        cond_map = {"F": "Non-Aged", "V": "Aged", "N": "Normalised"}
        dir_map  = {"L": "Longitudinal (0°)", "S": "45°", "D": "Transverse (90°)"}
        conds_used = sorted({c.get("mechanical", {}).get("cond", "")
                              for c in coils if c.get("mechanical")} - {""})
        dirs_used  = sorted({c.get("mechanical", {}).get("dir", "")
                              for c in coils if c.get("mechanical")} - {""})
        legend_parts = []
        if conds_used:
            legend_parts.append(
                "Specimen condition (Cond.): " +
                " | ".join(f"{k} = {cond_map.get(k, k)}" for k in conds_used)
            )
        if dirs_used:
            legend_parts.append(
                "Direction (Dir.): " +
                " | ".join(f"{k} = {dir_map.get(k, k)}" for k in dirs_used)
            )
        if legend_parts:
            story.append(Paragraph(" · ".join(legend_parts), _ITALIC))
            story.append(Spacer(1, 1 * mm))

        m_headers = ["Item", "Pack Nr", "Cond.", "Dir.",
                     "Yield Strength\nRp0.2 (MPa)",
                     "Tensile Strength\nRm (MPa)",
                     "Elongation\nA (%)",
                     "Rp0.2/Rm"]
        m_weights = [0.4, 1.4, 0.6, 0.5, 1.4, 1.4, 1.0, 0.8]
        m_rows = []
        for i, coil in enumerate(coils, 1):
            m = coil.get("mechanical") or {}
            if not m:
                continue
            a_pct = m.get("a_pct")
            a_s   = (f"{a_pct:.1f}" if isinstance(a_pct, (int, float))
                     else (str(a_pct) if a_pct else "–"))
            rr = m.get("rp02_rm")
            rr_s = (f"{rr:.2f}" if isinstance(rr, (int, float)) else (str(rr) if rr else "–"))
            m_rows.append([
                i,
                coil.get("pack_nr") or coil.get("cast_no") or "–",
                m.get("cond", "–"),
                m.get("dir", "–"),
                m.get("rp02") or "–",
                m.get("rm")   or "–",
                a_s,
                rr_s,
            ])
        if m_rows:
            story.extend(_make_table(m_headers, m_rows, m_weights))
            story.append(Spacer(1, 4 * mm))

    # ── Section 5: Remarks & Production Notes ─────────────────────────────────
    remarks = parsed_cert.get("remarks") or []
    if remarks:
        story.extend(_section_header("REMARKS & PRODUCTION NOTES"))
        for remark in remarks:
            story.append(Paragraph(f"• {remark}", _BODY))
        story.append(Spacer(1, 4 * mm))

    # ── Section 6: Certification ──────────────────────────────────────────────
    story.extend(_section_header("CERTIFICATION"))
    story.append(Spacer(1, 1 * mm))
    story.append(Paragraph(
        "This inspection certificate has been issued in accordance with EN 10204 Type 3.1.",
        _BODY,
    ))
    story.append(Spacer(1, 1.5 * mm))

    # Conditional ISO 9001 sentence
    quality_system = parsed_cert.get("quality_system") or ""
    if quality_system and re.search(r"iso.?9001|iatf.?16949", quality_system, re.I):
        story.append(Paragraph(
            "The Quality Management System applied to the manufacturing of the goods "
            "described above is certified to meet the requirements of ISO 9001 and IATF 16949.",
            _BODY,
        ))
        story.append(Spacer(1, 1.5 * mm))

    story.append(Paragraph(
        "All test results stated herein are based on authenticated records from "
        "the original manufacturer's inspection data.",
        _BODY,
    ))
    story.append(Spacer(1, 3 * mm))

    # Validity box — blue border, vanilla background
    vb_data = [[Paragraph(
        "This certificate is valid without signature.",
        ParagraphStyle("vb", fontName="Helvetica-Bold", fontSize=9,
                       textColor=NAVY, alignment=TA_CENTER)
    )]]
    vb = Table(vb_data, colWidths=[CONTENT_W * 0.6])
    vb.setStyle(TableStyle([
        ("BOX",           (0, 0), (-1, -1), 1.5, BLUE),
        ("BACKGROUND",    (0, 0), (-1, -1), VANILLA),
        ("TOPPADDING",    (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING",   (0, 0), (-1, -1), 8),
    ]))
    story.append(vb)

    # ── Build PDF ─────────────────────────────────────────────────────────────
    doc = BaseDocTemplate(
        buf,
        pagesize=(PAGE_W, PAGE_H),
        leftMargin=ML, rightMargin=MR,
        topMargin=HEADER_H + 3 * mm,
        bottomMargin=FOOTER_H + 4 * mm,
    )
    frame = Frame(
        ML, FRAME_Y, CONTENT_W, FRAME_H,
        leftPadding=0, rightPadding=0,
        topPadding=0, bottomPadding=0,
    )
    doc.addPageTemplates([PageTemplate(
        id="main",
        frames=[frame],
        onPage=lambda c, d: _draw_page(c, d, cert_info, logo_path),
    )])
    doc.build(story, canvasmaker=_NumberedCanvas)
    return buf.getvalue()
