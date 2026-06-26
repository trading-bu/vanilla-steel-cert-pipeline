"""
ReportLab PDF generator for the Vanilla Steel neutralised certificate.
Follows neutralisation_Instructions_v2.md.

Input:
  parsed_cert  — output of cert_parser.parse_cert(), coils already AOO-filtered,
                 each coil has 'vs_article' key added by main.py
  odoo_data    — output of odoo_client.get_neutralisation_data()
  logo_path    — absolute path to NEW VS LOGO.jpg

Multi-coil: Section 2/3/4 produce one row per coil/cast.
ArcelorMittal chemistry: raw integers with ×10⁻³%/×10⁻²%/×10⁻⁴% headers.
SSAB / others: decimal % values.
"""
from io import BytesIO
import os
import re

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.platypus import (
    BaseDocTemplate, Frame, PageTemplate, Paragraph,
    Table, TableStyle, Spacer, KeepTogether,
)
from reportlab.pdfgen import canvas as pdfcanvas

# ─── Brand colours ────────────────────────────────────────────────────────────
NAVY    = colors.HexColor("#000831")
BLUE    = colors.HexColor("#0047FF")
VANILLA = colors.HexColor("#FFF7E6")
WHITE   = colors.white
LGREY   = colors.HexColor("#D8D8D8")

# ─── Page geometry ────────────────────────────────────────────────────────────
PAGE_W, PAGE_H = A4
ML = MR = 15 * mm
HEADER_H  = 53 * mm
FOOTER_H  = 20 * mm
CONTENT_W = PAGE_W - ML - MR
FRAME_Y   = FOOTER_H
FRAME_H   = PAGE_H - HEADER_H - FOOTER_H

# ─── Styles ───────────────────────────────────────────────────────────────────
_NORMAL  = ParagraphStyle("normal",  fontName="Helvetica",       fontSize=8,   leading=10)
_BOLD    = ParagraphStyle("bold",    fontName="Helvetica-Bold",  fontSize=8,   leading=10)
_SMALL   = ParagraphStyle("small",   fontName="Helvetica",       fontSize=7,   leading=9,
                           textColor=colors.HexColor("#333333"))
_WHITE_B = ParagraphStyle("white_b", fontName="Helvetica-Bold",  fontSize=8,   leading=10,
                           textColor=WHITE, alignment=TA_CENTER)
_WHITE_C = ParagraphStyle("white_c", fontName="Helvetica",       fontSize=7,   leading=9,
                           textColor=WHITE, alignment=TA_CENTER)
_CELL    = ParagraphStyle("cell",    fontName="Helvetica",       fontSize=7.5, leading=9.5)
_ITALIC  = ParagraphStyle("italic",  fontName="Helvetica-Oblique", fontSize=7.5, leading=9.5,
                           textColor=colors.HexColor("#444444"))


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
            self.setFont("Helvetica", 8)
            self.setFillColor(NAVY)
            x = ML + CONTENT_W * 0.5 + 30 * mm
            y = PAGE_H - HEADER_H + 6 * mm
            self.drawString(x, y, f"{i} of {total}")
            pdfcanvas.Canvas.showPage(self)
        pdfcanvas.Canvas.save(self)


# ─── Header / footer ──────────────────────────────────────────────────────────
def _draw_page(canvas, doc, cert_info: dict, logo_path: str | None):
    canvas.saveState()

    banner_h = 25 * mm
    banner_y = PAGE_H - banner_h
    left_w   = PAGE_W * 0.38

    # Banner panels
    canvas.setFillColor(WHITE)
    canvas.rect(0, banner_y, left_w, banner_h, fill=1, stroke=0)
    canvas.setFillColor(NAVY)
    canvas.rect(left_w, banner_y, PAGE_W - left_w, banner_h, fill=1, stroke=0)

    # Logo
    if logo_path and os.path.exists(logo_path):
        try:
            canvas.drawImage(logo_path, 5*mm, banner_y+2.5*mm,
                             width=20*mm, height=20*mm,
                             preserveAspectRatio=True, mask="auto")
        except Exception:
            pass

    # Company name & tagline
    canvas.setFillColor(NAVY)
    canvas.setFont("Helvetica-Bold", 15)
    canvas.drawString(28*mm, banner_y+14*mm, "Vanilla Steel")
    canvas.setFillColor(BLUE)
    canvas.setFont("Helvetica", 8)
    canvas.drawString(28*mm, banner_y+7*mm, "Steel, made simple.")

    # Title (right panel)
    canvas.setFillColor(WHITE)
    canvas.setFont("Helvetica-Bold", 14)
    canvas.drawRightString(PAGE_W-5*mm, banner_y+14*mm, "INSPECTION CERTIFICATE – Copy")
    canvas.setFont("Helvetica", 8)
    canvas.drawRightString(PAGE_W-5*mm, banner_y+6*mm, "EN 10204 – Type 3.1")

    # Rule
    canvas.setStrokeColor(BLUE)
    canvas.setLineWidth(1.5)
    canvas.line(0, banner_y, PAGE_W, banner_y)

    # Info box
    info_h = 28 * mm
    info_y = banner_y - info_h
    canvas.setFillColor(VANILLA)
    canvas.setStrokeColor(LGREY)
    canvas.setLineWidth(0.5)
    canvas.rect(ML, info_y, CONTENT_W, info_h, fill=1, stroke=1)

    mid = ML + CONTENT_W * 0.5

    def kv(x, y, key, value):
        canvas.setFont("Helvetica-Bold", 8)
        canvas.setFillColor(NAVY)
        canvas.drawString(x, y, key)
        canvas.setFont("Helvetica", 8)
        canvas.drawString(x + 35*mm, y, str(value))

    kv(ML+3*mm, info_y+20*mm, "Certificate Type",       cert_info.get("cert_type", "EN 10204 3.1"))
    kv(ML+3*mm, info_y+12*mm, "Country of Destination", cert_info.get("buyer_country", ""))
    kv(mid+3*mm, info_y+20*mm, "Issue Date",            cert_info.get("cert_date", ""))
    kv(mid+3*mm, info_y+12*mm, "Sales Order",           cert_info.get("so_number", ""))

    canvas.setFont("Helvetica-Bold", 8)
    canvas.setFillColor(NAVY)
    canvas.drawString(mid+3*mm, info_y+4*mm, "Page")
    # Actual number written by _NumberedCanvas.save()

    # Footer
    canvas.setStrokeColor(BLUE)
    canvas.setLineWidth(0.5)
    canvas.line(ML, FOOTER_H+10*mm, PAGE_W-MR, FOOTER_H+10*mm)
    canvas.setFont("Helvetica", 6.5)
    canvas.setFillColor(NAVY)
    canvas.drawString(ML, FOOTER_H+7*mm,
        "Vanilla Steel GmbH | Schönhauser Allee 36, 10435 Berlin, Germany")
    canvas.drawString(ML, FOOTER_H+3.5*mm,
        "VAT ID: DE332534899 | Tax No: 37/569/52330 | "
        "Registered Court: Charlottenburg District Court (Berlin) HRB 218619 B")
    canvas.drawString(ML, FOOTER_H+0.5*mm,
        "Managing Directors: Clifford Ondara, Simon Zühlke")
    canvas.setFillColor(BLUE)
    canvas.drawRightString(PAGE_W-MR, FOOTER_H+7*mm,  "support@vanillasteel.com")
    canvas.drawRightString(PAGE_W-MR, FOOTER_H+3.5*mm, "www.vanillasteel.com")

    canvas.restoreState()


# ─── Table helpers ─────────────────────────────────────────────────────────────
def _section_header(title: str) -> Table:
    t = Table([[Paragraph(f"<b>{title}</b>", _WHITE_B)]], colWidths=[CONTENT_W])
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0,0),(-1,-1), NAVY),
        ("TOPPADDING",    (0,0),(-1,-1), 5),
        ("BOTTOMPADDING", (0,0),(-1,-1), 5),
        ("LEFTPADDING",   (0,0),(-1,-1), 4),
    ]))
    return t


def _data_table(headers: list, rows: list, col_weights: list = None,
                tall_header: bool = False) -> Table:
    """
    Blue header row, alternating white/vanilla body rows.
    headers: list of str or Paragraph objects.
    tall_header: True when header cells contain two-line content (chemistry).
    """
    if col_weights is None:
        col_weights = [1.0] * len(headers)
    total_w    = sum(col_weights)
    col_widths = [CONTENT_W * w / total_w for w in col_weights]

    head_row  = []
    for h in headers:
        if isinstance(h, Paragraph):
            head_row.append(h)
        else:
            head_row.append(Paragraph(f"<b>{h}</b>", _WHITE_B))

    data_rows = []
    for row in rows:
        data_rows.append([
            Paragraph(str(c) if c is not None else "–", _CELL) for c in row
        ])

    table_data = [head_row] + data_rows
    t = Table(table_data, colWidths=col_widths, repeatRows=1)
    header_h = 12 if not tall_header else 22
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0,0),(-1,0),  BLUE),
        ("TEXTCOLOR",     (0,0),(-1,0),  WHITE),
        ("ROWBACKGROUNDS",(0,1),(-1,-1), [WHITE, VANILLA]),
        ("TOPPADDING",    (0,0),(-1,0),  4 if not tall_header else 5),
        ("BOTTOMPADDING", (0,0),(-1,0),  4 if not tall_header else 5),
        ("TOPPADDING",    (0,1),(-1,-1), 3),
        ("BOTTOMPADDING", (0,1),(-1,-1), 3),
        ("LEFTPADDING",   (0,0),(-1,-1), 3),
        ("GRID",          (0,0),(-1,-1), 0.3, LGREY),
        ("VALIGN",        (0,0),(-1,-1), "MIDDLE"),
    ]))
    return t


def _kv_grid(rows: list) -> Table:
    data = []
    for i in range(0, len(rows), 2):
        left  = rows[i]
        right = rows[i+1] if i+1 < len(rows) else ("", "")
        data.append([
            Paragraph(f"<b>{left[0]}</b>",  _NORMAL), Paragraph(str(left[1]),  _CELL),
            Paragraph(f"<b>{right[0]}</b>", _NORMAL), Paragraph(str(right[1]), _CELL),
        ])
    col = CONTENT_W / 4
    t = Table(data, colWidths=[col, col, col, col])
    t.setStyle(TableStyle([
        ("ROWBACKGROUNDS", (0,0),(-1,-1), [WHITE, VANILLA]),
        ("TOPPADDING",     (0,0),(-1,-1), 3),
        ("BOTTOMPADDING",  (0,0),(-1,-1), 3),
        ("LEFTPADDING",    (0,0),(-1,-1), 4),
        ("GRID",           (0,0),(-1,-1), 0.3, LGREY),
    ]))
    return t


# ─── Chemistry helpers ─────────────────────────────────────────────────────────
def _chem_header_para(elem: str, am_units: dict | None) -> Paragraph:
    """
    Two-line header cell: element name (bold) + unit.
    AM: ×10<super>-3</super>%  etc.
    Others: %
    """
    if am_units and elem in am_units:
        exp = am_units[elem]        # e.g. "-3", "-2", "-4"
        unit_line = f"×10<super>{exp}</super>%"
    else:
        unit_line = "%"

    xml = f"<b>{elem}</b><br/><font size='6'>{unit_line}</font>"
    return Paragraph(xml, _WHITE_C)


def _fmt_chem(val, is_integer: bool) -> str:
    """Format a chemistry value for display."""
    if val is None or (isinstance(val, str) and not val.strip()):
        return "–"
    try:
        if is_integer:
            return str(int(float(val)))
        # Decimal % — strip trailing zeros but keep enough precision
        f = float(val)
        if f == 0:
            return "0"
        # Show up to 4 sig figs
        s = f"{f:.4f}".rstrip("0").rstrip(".")
        return s
    except (ValueError, TypeError):
        return str(val)  # show raw value if unparseable


# ─── Main generator ───────────────────────────────────────────────────────────
def generate_certificate(
    parsed_cert: dict,
    odoo_data:   dict,
    logo_path:   str | None = None,
) -> bytes:
    """
    Build the neutralised VS inspection certificate PDF.

    parsed_cert: output of cert_parser.parse_cert() with:
                 - coils already filtered by AOO and enriched with 'vs_article'
                 - cert_date, cert_number, cert_type, grade, standard, etc. merged in
    odoo_data:  output of odoo_client.get_neutralisation_data()
    """
    buf = BytesIO()

    cert_info = {
        "cert_type":     parsed_cert.get("cert_type", "EN 10204 3.1"),
        "buyer_country": odoo_data.get("buyer_country", ""),
        "cert_date":     parsed_cert.get("cert_date", ""),
        "so_number":     odoo_data.get("so_number", ""),
    }

    doc = BaseDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=ML, rightMargin=MR,
        topMargin=HEADER_H, bottomMargin=FOOTER_H,
    )
    frame    = Frame(ML, FRAME_Y, CONTENT_W, FRAME_H, id="body")
    template = PageTemplate(
        id="cert",
        frames=[frame],
        onPage=lambda c, d: _draw_page(c, d, cert_info, logo_path),
    )
    doc.addPageTemplates([template])

    story = _build_story(parsed_cert, odoo_data)
    doc.build(story, canvasmaker=_NumberedCanvas)
    return buf.getvalue()


def _build_story(parsed_cert: dict, odoo_data: dict) -> list:
    story  = []
    coils  = parsed_cert.get("coils", [])
    is_int = parsed_cert.get("is_integer_chemistry", False)
    am_u   = parsed_cert.get("am_units") if is_int else None

    # ── SECTION 1: Product Details ────────────────────────────────────────────
    story.append(_section_header("1. PRODUCT DETAILS"))

    total_w = parsed_cert.get("total_weight_kg") or sum(
        c.get("weight_kg") or 0 for c in coils
    )
    wt_s = f"{int(total_w):,} kg" if total_w else "–"

    # Determine shared dimensions (may vary per coil for SSAB)
    thicknesses = list({c.get("thickness_mm", "") for c in coils if c.get("thickness_mm")})
    widths      = list({c.get("width_mm", "")     for c in coils if c.get("width_mm")})
    dim_s = ""
    if len(thicknesses) == 1 and len(widths) == 1:
        dim_s = f"{thicknesses[0]} mm × {widths[0]} mm"
    elif thicknesses or widths:
        dim_s = "Mixed — see Shipped Positions"

    grade_s   = parsed_cert.get("grade", "") or ""
    quality_s = parsed_cert.get("quality", "") or ""
    gq_s      = " / ".join(filter(None, [grade_s, quality_s])) or "–"

    kv_pairs = [
        ("Description of Goods",  parsed_cert.get("material_type", "–")),
        ("Type of Packages",       "Coil"),
        ("Standard",               parsed_cert.get("standard", "–")),
        ("Quantity",               f"{len(coils)} coil(s)"),
        ("Grade / Quality",        gq_s),
        ("Dimensions",             dim_s or "–"),
        ("Total Gross Weight",     wt_s),
        ("", ""), ("", ""), ("", ""),
    ]
    story.append(_kv_grid(kv_pairs))
    story.append(Spacer(1, 4*mm))

    # ── SECTION 2: Shipped Positions ──────────────────────────────────────────
    story.append(_section_header("2. SHIPPED POSITIONS"))

    # Decide which columns to include based on data available
    has_coil_no = any(c.get("coil_no") and c["coil_no"] != c.get("pack_nr") for c in coils)
    has_cast_no = any(c.get("cast_no") for c in coils)

    if has_coil_no and has_cast_no:
        s2_headers  = ["Item", "Pack Nr", "Coil No.", "Cast No.", "Grade",
                        "Width\n(mm)", "Thick.\n(mm)", "Qty", "Gross Wt.\n(kg)", "VS Article"]
        s2_weights  = [0.7, 1.8, 2.5, 1.8, 2.8, 1.2, 1.2, 0.7, 1.8, 2.0]
    elif has_cast_no:
        s2_headers  = ["Item", "Pack Nr", "Cast No.", "Grade",
                        "Width\n(mm)", "Thick.\n(mm)", "Qty", "Gross Wt.\n(kg)", "VS Article"]
        s2_weights  = [0.7, 2.0, 2.0, 2.8, 1.2, 1.2, 0.7, 1.8, 2.0]
    else:
        s2_headers  = ["Item", "Pack Nr / Heat No.", "Grade",
                        "Width\n(mm)", "Thick.\n(mm)", "Qty", "Gross Wt.\n(kg)", "VS Article"]
        s2_weights  = [0.7, 2.5, 2.8, 1.2, 1.2, 0.7, 1.8, 2.0]

    cert_grade = parsed_cert.get("grade", "–") or "–"
    s2_rows = []
    for i, coil in enumerate(coils, 1):
        wk = coil.get("weight_kg")
        wks = f"{int(wk):,}" if wk else "–"
        vs  = coil.get("vs_article", "–")
        coil_grade = coil.get("grade") or cert_grade or "–"

        if has_coil_no and has_cast_no:
            row = [i, coil.get("pack_nr","–"), coil.get("coil_no","–"),
                   coil.get("cast_no","–"), coil_grade,
                   coil.get("width_mm","–"), coil.get("thickness_mm","–"),
                   coil.get("qty", 1), wks, vs]
        elif has_cast_no:
            row = [i, coil.get("pack_nr","–"), coil.get("cast_no","–"), coil_grade,
                   coil.get("width_mm","–"), coil.get("thickness_mm","–"),
                   coil.get("qty", 1), wks, vs]
        else:
            row = [i, coil.get("pack_nr","–"), coil_grade,
                   coil.get("width_mm","–"), coil.get("thickness_mm","–"),
                   coil.get("qty", 1), wks, vs]
        s2_rows.append(row)

    # Totals row
    total_qty = sum(c.get("qty", 1) for c in coils)
    total_wt_str = f"{int(total_w):,}" if total_w else "–"
    totals_row = [""] * len(s2_headers)
    totals_row[0]  = f"Total: {len(coils)}"
    totals_row[-4] = total_qty   # Qty column
    totals_row[-3] = total_wt_str  # Gross Wt column
    totals_row[-2] = ""
    totals_row[-1] = ""
    s2_rows.append(totals_row)

    story.append(_data_table(s2_headers, s2_rows, s2_weights))

    # Dimension note
    if len(thicknesses) == 1 and len(widths) == 1:
        story.append(Paragraph(
            f"<i>Note: Width {widths[0]} mm × Thickness {thicknesses[0]} mm.</i>", _ITALIC
        ))
    story.append(Spacer(1, 4*mm))

    # ── SECTION 3: Chemical Composition ───────────────────────────────────────
    # Gather all elements that appear in at least one coil
    all_elems = []
    for coil in coils:
        chems = coil.get("chemicals") or {}
        for elem in chems:
            if elem not in all_elems and chems[elem] is not None:
                all_elems.append(elem)

    if all_elems:
        story.append(_section_header("3. CHEMICAL COMPOSITION"))

        # Column headers: Cast/Pack + each element (two-line for unit)
        id_col_header = Paragraph("<b>Cast No. /\nPack Nr</b>", _WHITE_C)
        chem_headers  = [id_col_header] + [_chem_header_para(e, am_u) for e in all_elems]
        # Weights: id column wider, element columns narrow
        chem_weights  = [2.5] + [max(0.8, 5.0/len(all_elems))] * len(all_elems)

        chem_rows = []
        for coil in coils:
            cast_id = coil.get("cast_no") or coil.get("pack_nr") or "–"
            chems   = coil.get("chemicals") or {}
            row     = [cast_id] + [_fmt_chem(chems.get(e), is_int) for e in all_elems]
            chem_rows.append(row)

        story.append(_data_table(chem_headers, chem_rows, chem_weights, tall_header=True))
        story.append(Spacer(1, 4*mm))

    # ── SECTION 4: Mechanical Test Results ────────────────────────────────────
    mech_coils = [c for c in coils if c.get("mechanical")]
    if mech_coils:
        story.append(_section_header("4. MECHANICAL TEST RESULTS – TENSILE TEST"))

        # Build decoder legend from actual values present
        cond_l = parsed_cert.get("cond_legend", {})
        dir_l  = parsed_cert.get("dir_legend",  {})
        spec   = parsed_cert.get("specimen_dims", "")

        note_parts = []
        if cond_l:
            note_parts.append("Specimen condition (Cond.): " +
                               " | ".join(f"{k} = {v}" for k, v in cond_l.items()))
        if dir_l:
            note_parts.append("Direction (Dir.): " +
                               " | ".join(f"{k} = {v}" for k, v in dir_l.items()))
        if spec:
            note_parts.append(f"Specimen dimensions: {spec}")

        if note_parts:
            story.append(Paragraph("<i>" + " | ".join(note_parts) + "</i>", _ITALIC))
            story.append(Spacer(1, 2*mm))

        # Columns
        m_headers = ["Item", "Pack Nr", "Cond.", "Dir.",
                     "Yield Strength\nRp0.2 (MPa)",
                     "Tensile Strength\nRm (MPa)",
                     "Elongation\nA (%)"]
        m_weights = [0.7, 2.0, 0.8, 0.7, 2.0, 2.0, 1.5]

        # Check if any coil has Rp0.2/Rm ratio
        has_ratio = any(
            c["mechanical"].get("rp02_rm") is not None
            for c in mech_coils
        )
        if has_ratio:
            m_headers.append("Rp0.2/Rm\n(%)")
            m_weights.append(1.5)

        m_rows = []
        for i, coil in enumerate(mech_coils, 1):
            m   = coil["mechanical"]
            row = [
                i,
                coil.get("pack_nr", "–"),
                m.get("cond", "–"),
                m.get("dir",  "–"),
                m.get("rp02") or "–",
                m.get("rm")   or "–",
                m.get("a_pct") if m.get("a_pct") is not None else "–",
            ]
            if has_ratio:
                row.append(m.get("rp02_rm") or "–")
            m_rows.append(row)

        story.append(_data_table(m_headers, m_rows, m_weights))
        story.append(Spacer(1, 4*mm))

    # ── SECTION 5: Remarks ────────────────────────────────────────────────────
    remarks = parsed_cert.get("remarks", [])
    story.append(_section_header("5. REMARKS & PRODUCTION NOTES"))
    # Use &#x2022; XML entity instead of the literal • character.
    # Helvetica at code-point 0x7F renders as (cid:127) in some PDF viewers;
    # the XML entity forces ReportLab to resolve the Unicode glyph correctly.
    # Also strip any (cid:N) artefacts from source PDF text extraction.
    def _render_remark(text: str) -> Paragraph:
        clean = re.sub(r'\(cid:\d+\)', '', text).strip()
        return Paragraph(f"&#x2022; {clean}", _CELL)

    if remarks:
        for r in remarks:
            if r.strip():
                story.append(_render_remark(r))
    else:
        story.append(_render_remark(
            "All technical data reproduced from the original manufacturer's "
            "inspection records without modification."
        ))
    story.append(Spacer(1, 4*mm))

    # ── SECTION 6: Certification ──────────────────────────────────────────────
    story.append(_section_header("6. CERTIFICATION"))
    story.append(Spacer(1, 2*mm))
    story.append(Paragraph(
        "This inspection certificate has been issued in accordance with EN 10204 Type 3.1. "
        "All test results stated herein are based on authenticated records from the original "
        "manufacturer's inspection data.",
        _NORMAL
    ))
    story.append(Spacer(1, 4*mm))

    validity = Table([["This certificate is valid without signature."]], colWidths=[CONTENT_W])
    validity.setStyle(TableStyle([
        ("BOX",           (0,0),(-1,-1), 1, BLUE),
        ("BACKGROUND",    (0,0),(-1,-1), VANILLA),
        ("ALIGN",         (0,0),(-1,-1), "CENTER"),
        ("TOPPADDING",    (0,0),(-1,-1), 6),
        ("BOTTOMPADDING", (0,0),(-1,-1), 6),
        ("FONTNAME",      (0,0),(-1,-1), "Helvetica-Oblique"),
        ("FONTSIZE",      (0,0),(-1,-1), 9),
    ]))
    story.append(validity)

    return story
