"""
ReportLab PDF generator for the Vanilla Steel neutralised certificate.
Follows neutralisation_Instructions_v2.md exactly.

Brand colours:  Navy #000831  |  Blue #0047FF  |  Vanilla #FFF7E6
"""
from io import BytesIO
import os
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (
    BaseDocTemplate, Frame, PageTemplate, Paragraph,
    Table, TableStyle, Spacer, KeepTogether, HRFlowable
)
from reportlab.pdfgen import canvas as pdfcanvas

# ─── Brand colours ────────────────────────────────────────────────────────────
NAVY    = colors.HexColor("#000831")
BLUE    = colors.HexColor("#0047FF")
VANILLA = colors.HexColor("#FFF7E6")
WHITE   = colors.white
LGREY   = colors.HexColor("#D8D8D8")
ALT_ROW = colors.HexColor("#FFF7E6")   # vanilla alternating rows

# ─── Page geometry ────────────────────────────────────────────────────────────
PAGE_W, PAGE_H = A4          # 595.28 × 841.89 pt
ML = MR = 15 * mm           # left / right margins
HEADER_H  = 53 * mm         # space reserved at the top (banner + info box)
FOOTER_H  = 20 * mm         # space reserved at the bottom
CONTENT_W = PAGE_W - ML - MR
FRAME_Y   = FOOTER_H
FRAME_H   = PAGE_H - HEADER_H - FOOTER_H

# ─── Paragraph styles ─────────────────────────────────────────────────────────
_NORMAL   = ParagraphStyle("normal",   fontName="Helvetica",      fontSize=8,  leading=10)
_BOLD     = ParagraphStyle("bold",     fontName="Helvetica-Bold",  fontSize=8,  leading=10)
_SMALL    = ParagraphStyle("small",    fontName="Helvetica",       fontSize=7,  leading=9,  textColor=colors.HexColor("#333333"))
_WHITE_B  = ParagraphStyle("white_b",  fontName="Helvetica-Bold",  fontSize=8,  leading=10, textColor=WHITE, alignment=TA_CENTER)
_WHITE_C  = ParagraphStyle("white_c",  fontName="Helvetica",       fontSize=7,  leading=9,  textColor=WHITE, alignment=TA_CENTER)
_CELL     = ParagraphStyle("cell",     fontName="Helvetica",       fontSize=7.5,leading=9.5)
_ITALIC   = ParagraphStyle("italic",   fontName="Helvetica-Oblique",fontSize=7.5,leading=9.5,textColor=colors.HexColor("#444444"))


# ─── Two-pass canvas (Page X of Y) ────────────────────────────────────────────
class _NumberedCanvas(pdfcanvas.Canvas):
    """Deferred page-number rendering so 'Page X of Y' is always correct."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._page_data = []   # stores (page_num, callback_args) per page

    def showPage(self):
        self._page_data.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._page_data)
        for i, state in enumerate(self._page_data, 1):
            self.__dict__.update(state)
            # Write "X of Y" into the info box area
            self.setFont("Helvetica", 8)
            self.setFillColor(NAVY)
            # Position: right column of info box, "Page" row
            x = ML + CONTENT_W * 0.5 + 30 * mm
            y = PAGE_H - HEADER_H + 6 * mm
            self.drawString(x, y, f"{i} of {total}")
            pdfcanvas.Canvas.showPage(self)
        pdfcanvas.Canvas.save(self)


# ─── Header / footer (drawn on every page via PageTemplate) ───────────────────
def _draw_page(canvas, doc, cert_info: dict, logo_path: str | None):
    canvas.saveState()

    banner_h = 25 * mm
    banner_y = PAGE_H - banner_h
    left_w   = PAGE_W * 0.38

    # ── Zone 1: two-panel banner ──────────────────────────────────────────────
    canvas.setFillColor(WHITE)
    canvas.rect(0, banner_y, left_w, banner_h, fill=1, stroke=0)

    canvas.setFillColor(NAVY)
    canvas.rect(left_w, banner_y, PAGE_W - left_w, banner_h, fill=1, stroke=0)

    # Logo
    if logo_path and os.path.exists(logo_path):
        try:
            canvas.drawImage(
                logo_path,
                5 * mm, banner_y + 2.5 * mm,
                width=20 * mm, height=20 * mm,
                preserveAspectRatio=True, mask="auto"
            )
        except Exception:
            pass  # Logo missing — continue without it

    # Company name + tagline
    canvas.setFillColor(NAVY)
    canvas.setFont("Helvetica-Bold", 15)
    canvas.drawString(28 * mm, banner_y + 14 * mm, "Vanilla Steel")
    canvas.setFillColor(BLUE)
    canvas.setFont("Helvetica", 8)
    canvas.drawString(28 * mm, banner_y + 7 * mm, "Steel, made simple.")

    # Title block (right panel)
    canvas.setFillColor(WHITE)
    canvas.setFont("Helvetica-Bold", 14)
    canvas.drawRightString(PAGE_W - 5 * mm, banner_y + 14 * mm, "INSPECTION CERTIFICATE – Copy")
    canvas.setFont("Helvetica", 8)
    canvas.drawRightString(PAGE_W - 5 * mm, banner_y + 6 * mm, "EN 10204 – Type 3.1")

    # Blue rule between banner and info box
    canvas.setStrokeColor(BLUE)
    canvas.setLineWidth(1.5)
    canvas.line(0, banner_y, PAGE_W, banner_y)

    # ── Zone 2: info box ──────────────────────────────────────────────────────
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
        canvas.drawString(x + 35 * mm, y, str(value))

    kv(ML + 3 * mm,  info_y + 20 * mm, "Certificate Type",      cert_info.get("cert_type", "EN 10204 3.1"))
    kv(ML + 3 * mm,  info_y + 12 * mm, "Country of Destination", cert_info.get("buyer_country", ""))

    kv(mid + 3 * mm, info_y + 20 * mm, "Issue Date",   cert_info.get("cert_date", ""))
    kv(mid + 3 * mm, info_y + 12 * mm, "Sales Order",  cert_info.get("so_number", ""))

    # "Page X of Y" is filled in by _NumberedCanvas.save()
    canvas.setFont("Helvetica-Bold", 8)
    canvas.setFillColor(NAVY)
    canvas.drawString(mid + 3 * mm, info_y + 6 * mm - 1 * mm, "Page")
    # Actual page number written by _NumberedCanvas

    # ── Zone 3: footer ────────────────────────────────────────────────────────
    canvas.setStrokeColor(BLUE)
    canvas.setLineWidth(0.5)
    canvas.line(ML, FOOTER_H + 10 * mm, PAGE_W - MR, FOOTER_H + 10 * mm)

    canvas.setFont("Helvetica", 6.5)
    canvas.setFillColor(NAVY)
    canvas.drawString(ML, FOOTER_H + 7 * mm,
        "Vanilla Steel GmbH | Schönhauser Allee 36, 10435 Berlin, Germany")
    canvas.drawString(ML, FOOTER_H + 3.5 * mm,
        "VAT ID: DE332534899 | Tax No: 37/569/52330 | Registered Court: "
        "Charlottenburg District Court (Berlin) HRB 218619 B")
    canvas.drawString(ML, FOOTER_H + 0.5 * mm,
        "Managing Directors: Clifford Ondara, Simon Zühlke")

    canvas.setFillColor(BLUE)
    canvas.drawRightString(PAGE_W - MR, FOOTER_H + 7 * mm,  "support@vanillasteel.com")
    canvas.drawRightString(PAGE_W - MR, FOOTER_H + 3.5 * mm, "www.vanillasteel.com")

    canvas.restoreState()


# ─── Section helpers ──────────────────────────────────────────────────────────
def _section_header(title: str) -> Table:
    """Navy background, white bold text section header row."""
    t = Table([[Paragraph(f"<b>{title}</b>", _WHITE_B)]], colWidths=[CONTENT_W])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), NAVY),
        ("TOPPADDING",    (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING",   (0, 0), (-1, -1), 4),
    ]))
    return t


def _kv_grid(rows: list[tuple]) -> Table:
    """Two-column key-value grid for Product Details section."""
    data = []
    for i in range(0, len(rows), 2):
        left  = rows[i]
        right = rows[i + 1] if i + 1 < len(rows) else ("", "")
        data.append([
            Paragraph(f"<b>{left[0]}</b>",  _NORMAL), Paragraph(str(left[1]),  _CELL),
            Paragraph(f"<b>{right[0]}</b>", _NORMAL), Paragraph(str(right[1]), _CELL),
        ])
    col = CONTENT_W / 4
    t = Table(data, colWidths=[col, col, col, col])
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), WHITE),
        ("ROWBACKGROUNDS",(0, 0), (-1, -1), [WHITE, VANILLA]),
        ("TOPPADDING",    (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING",   (0, 0), (-1, -1), 4),
        ("GRID",          (0, 0), (-1, -1), 0.3, LGREY),
    ]))
    return t


def _data_table(headers: list, rows: list, col_weights: list = None) -> Table:
    """
    Standard VS-branded data table.
    - Blue header row with white bold text
    - Alternating white / vanilla rows
    """
    if col_weights is None:
        col_weights = [1.0] * len(headers)
    total_w = sum(col_weights)
    col_widths = [CONTENT_W * w / total_w for w in col_weights]

    head_row = [Paragraph(f"<b>{h}</b>", _WHITE_B) for h in headers]
    data_rows = []
    for i, row in enumerate(rows):
        bg = WHITE if i % 2 == 0 else VANILLA
        data_rows.append([Paragraph(str(c) if c is not None else "–", _CELL) for c in row])

    table_data = [head_row] + data_rows
    t = Table(table_data, colWidths=col_widths, repeatRows=1)
    style = [
        ("BACKGROUND",    (0, 0), (-1, 0),   BLUE),
        ("TEXTCOLOR",     (0, 0), (-1, 0),   WHITE),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1),  [WHITE, VANILLA]),
        ("TOPPADDING",    (0, 0), (-1, -1),  3),
        ("BOTTOMPADDING", (0, 0), (-1, -1),  3),
        ("LEFTPADDING",   (0, 0), (-1, -1),  3),
        ("GRID",          (0, 0), (-1, -1),  0.3, LGREY),
        ("VALIGN",        (0, 0), (-1, -1),  "MIDDLE"),
    ]
    t.setStyle(TableStyle(style))
    return t


# ─── Main generator ───────────────────────────────────────────────────────────
def generate_certificate(
    cert_data: dict,
    odoo_data: dict,
    logo_path: str | None = None,
) -> bytes:
    """
    Builds the neutralised VS inspection certificate PDF.

    Args:
        cert_data:  Output of docsumo_client.get_cert_data()
        odoo_data:  Output of odoo_client.get_neutralisation_data()
        logo_path:  Absolute path to NEW VS LOGO.jpg (or None)

    Returns:
        PDF bytes ready to save/upload.
    """
    buf = BytesIO()

    cert_info = {
        "cert_type":     cert_data.get("cert_type", "EN 10204 3.1"),
        "buyer_country": odoo_data.get("buyer_country", ""),
        "cert_date":     cert_data.get("cert_date", ""),
        "so_number":     odoo_data.get("so_number", ""),
    }

    doc = BaseDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=ML, rightMargin=MR,
        topMargin=HEADER_H, bottomMargin=FOOTER_H,
    )

    frame = Frame(ML, FRAME_Y, CONTENT_W, FRAME_H, id="body")
    template = PageTemplate(
        id="cert",
        frames=[frame],
        onPage=lambda c, d: _draw_page(c, d, cert_info, logo_path),
    )
    doc.addPageTemplates([template])

    story = _build_story(cert_data, odoo_data)
    doc.build(story, canvasmaker=_NumberedCanvas)

    return buf.getvalue()


def _build_story(cert_data: dict, odoo_data: dict) -> list:
    story = []
    vs_articles = odoo_data.get("vs_articles", [])

    # ── SECTION 1: Product Details ────────────────────────────────────────────
    story.append(_section_header("1. PRODUCT DETAILS"))

    dims = cert_data.get("dimensions", "")
    wt   = cert_data.get("weight_kg")
    wt_s = f"{wt:,.0f} kg" if wt else "–"

    kv_pairs = [
        ("Description of Goods",  cert_data.get("material_type", "–")),
        ("Type of Packages",       "Coil" if "coil" in cert_data.get("material_type","").lower() else "Bar"),
        ("Standard",               cert_data.get("cert_type", "–")),
        ("Quantity",               f"{len(vs_articles)} item(s)"),
        ("Grade / Quality",        cert_data.get("grade", "–")),
        ("Coating Weight",         "–"),
        ("Surface Treatment",      "–"),
        ("Dimensional Standard",   "–"),
        ("Dimensions",             dims),
        ("Steelmaking Process",    "–"),
        ("Total Gross Weight",     wt_s),
        ("", ""),
    ]
    story.append(_kv_grid(kv_pairs))
    story.append(Spacer(1, 4 * mm))

    # ── SECTION 2: Shipped Positions ──────────────────────────────────────────
    story.append(_section_header("2. SHIPPED POSITIONS"))

    headers = ["Item", "Pack Nr / Heat No.", "Grade", "Width\n(mm)", "Thick.\n(mm)", "Qty", "Gross Wt.\n(kg)", "VS Article"]
    weights = [0.8, 2.5, 3.0, 1.3, 1.3, 0.8, 1.8, 2.2]

    heat = cert_data.get("heat_number") or "–"

    # Parse dimensions: "300.00 mm x 20.00 mm"
    width_s, thick_s = "–", "–"
    dim_raw = cert_data.get("dimensions", "")
    if "x" in dim_raw.lower():
        parts = dim_raw.lower().replace("mm","").split("x")
        try:
            w, t = [p.strip() for p in parts]
            width_s = w
            thick_s = t
        except ValueError:
            pass

    rows = []
    for i, art in enumerate(vs_articles, 1):
        rows.append([
            i,
            heat,
            cert_data.get("grade", "–"),
            width_s,
            thick_s,
            1,
            wt_s if i == 1 else "–",
            art.get("vs_article", "–"),
        ])

    if not rows:
        # Fallback: one row with available data
        rows = [[1, heat, cert_data.get("grade","–"), width_s, thick_s, 1, wt_s, "–"]]

    story.append(_data_table(headers, rows, weights))

    # Dimension note
    if width_s != "–" and thick_s != "–":
        note = f"Note: Width {width_s} mm × Thickness {thick_s} mm."
        story.append(Paragraph(note, _ITALIC))
    story.append(Spacer(1, 4 * mm))

    # ── SECTION 3: Chemical Composition ───────────────────────────────────────
    chems = {k: v for k, v in cert_data.get("chemicals", {}).items() if v is not None}
    if chems:
        story.append(_section_header("3. CHEMICAL COMPOSITION"))

        # All Ori Martin / Wolter-Stahl values are already in % decimal format
        chem_headers = ["Pack Nr / Heat No."] + [f"<b>{el}</b><br/>%" for el in chems]
        chem_weights = [2.5] + [1.0] * len(chems)
        chem_row     = [heat] + [f"{v:.4f}".rstrip("0").rstrip(".") for v in chems.values()]

        story.append(_data_table(chem_headers, [chem_row], chem_weights))
        story.append(Spacer(1, 4 * mm))

    # ── SECTION 4: Mechanical Test Results ────────────────────────────────────
    mech = cert_data.get("mechanical", {})
    if any(v is not None for v in mech.values()):
        story.append(_section_header("4. MECHANICAL TEST RESULTS – TENSILE TEST"))

        note = ("Specimen condition (Cond.): F = Non-Aged | "
                "Direction (Dir.): L = Longitudinal (0°)")
        story.append(Paragraph(note, _ITALIC))
        story.append(Spacer(1, 2 * mm))

        m_headers = ["Item", "Pack Nr / Heat No.", "Cond.", "Dir.",
                     "Yield Strength\nRp0.2 (MPa)", "Tensile Strength\nRm (MPa)",
                     "Elongation\nA (%)"]
        m_weights  = [0.7, 2.2, 0.8, 0.7, 2.0, 2.0, 1.5]

        m_row = [
            1,
            heat,
            "F",
            "L",
            mech.get("reh") or "–",
            mech.get("rm")  or "–",
            mech.get("a80") or "–",
        ]
        story.append(_data_table(m_headers, [m_row], m_weights))
        story.append(Spacer(1, 4 * mm))

    # ── SECTION 5: Remarks ────────────────────────────────────────────────────
    story.append(_section_header("5. REMARKS & PRODUCTION NOTES"))
    story.append(Paragraph(
        "• All technical data reproduced from the original manufacturer’s "
        "inspection records without modification.",
        _CELL
    ))
    story.append(Spacer(1, 4 * mm))

    # ── SECTION 6: Certification ──────────────────────────────────────────────
    story.append(_section_header("6. CERTIFICATION"))
    story.append(Spacer(1, 2 * mm))
    story.append(Paragraph(
        "This inspection certificate has been issued in accordance with EN 10204 Type 3.1. "
        "All test results stated herein are based on authenticated records from the original "
        "manufacturer’s inspection data.",
        _NORMAL
    ))
    story.append(Spacer(1, 4 * mm))

    # "Valid without signature" box
    validity_data = [["This certificate is valid without signature."]]
    validity_t = Table(validity_data, colWidths=[CONTENT_W])
    validity_t.setStyle(TableStyle([
        ("BOX",           (0, 0), (-1, -1), 1, BLUE),
        ("BACKGROUND",    (0, 0), (-1, -1), VANILLA),
        ("ALIGN",         (0, 0), (-1, -1), "CENTER"),
        ("TOPPADDING",    (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("FONTNAME",      (0, 0), (-1, -1), "Helvetica-Oblique"),
        ("FONTSIZE",      (0, 0), (-1, -1), 9),
    ]))
    story.append(validity_t)

    return story
