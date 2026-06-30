"""
Vanilla Steel — Neutralised Inspection Certificate (EN 10204 3.1)
Direction A — Light layout.

Design reference:
  - White background, navy #000831 accent lines
  - Masthead: logo + address left / title right / 2pt navy rule below
  - Meta strip: 4 columns (cert no, date, standard, place)
  - Middle: 3 columns (Consignee | Order references | Product)
  - Chemical composition table (elements as columns, heats as rows)
  - Bottom: 2 columns (Mechanical properties | Declaration + signature)

Multi-coil handling:
  - If >1 coil: adds a Delivery Details table between Middle and Chemical sections
  - Chemistry and Mechanical tables always show all rows
"""
from io import BytesIO
import os

from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_RIGHT, TA_CENTER
from reportlab.platypus import (
    BaseDocTemplate, Frame, PageTemplate,
    Paragraph, Table, TableStyle, Spacer, HRFlowable, KeepTogether,
)
from reportlab.pdfgen import canvas as pdfcanvas


# ── Palette (Direction A) ─────────────────────────────────────────────────────
NAVY         = colors.HexColor("#000831")
WHITE        = colors.white
TEXT_DARK    = colors.HexColor("#11151f")
TEXT_MED     = colors.HexColor("#4b515e")
TEXT_MUTED   = colors.HexColor("#7b8290")
LABEL_C      = colors.HexColor("#9aa0ac")
BORDER_LIGHT = colors.HexColor("#eef0f3")
BORDER_MED   = colors.HexColor("#e6e8ee")


# ── Page geometry ─────────────────────────────────────────────────────────────
PAGE_W, PAGE_H = landscape(A4)
ML = MR = 14 * mm
HEADER_H = 22 * mm
FOOTER_H =  8 * mm
CONTENT_W = PAGE_W - ML - MR
FRAME_Y = FOOTER_H + 3 * mm
FRAME_H = PAGE_H - HEADER_H - FOOTER_H - 5 * mm


# ── Text styles ───────────────────────────────────────────────────────────────
def _s(name, **kw):
    base = dict(fontName="Helvetica", fontSize=8, leading=10,
                textColor=TEXT_DARK, spaceAfter=0, spaceBefore=0)
    base.update(kw)
    return ParagraphStyle(name, **base)

S_LABEL  = _s("lbl",   fontSize=6.5,  textColor=LABEL_C,   leading=9)
S_VAL    = _s("val",   fontSize=10.5, textColor=TEXT_DARK,  fontName="Helvetica-Bold", leading=13)
S_BODY   = _s("body",  fontSize=8.5,  textColor=TEXT_MED,   leading=12)
S_BODYB  = _s("bodb",  fontSize=8.5,  textColor=TEXT_DARK,  fontName="Helvetica-Bold", leading=12)
S_GRADE  = _s("grd",   fontSize=12,   textColor=TEXT_DARK,  fontName="Helvetica-Bold", leading=15)
S_MUTED  = _s("mut",   fontSize=8,    textColor=TEXT_MUTED, leading=11)
S_DECL   = _s("dcl",   fontSize=8.5,  textColor=TEXT_MED,   leading=13)
S_SIGNAM = _s("sgn",   fontSize=9.5,  textColor=TEXT_DARK,  fontName="Helvetica-Bold", leading=12)
S_SIGROL = _s("sgr",   fontSize=8,    textColor=TEXT_MUTED, leading=10)
S_NOTE   = _s("nte",   fontSize=7,    textColor=LABEL_C,    leading=9,  alignment=TA_RIGHT)
S_THDR   = _s("thd",   fontSize=8,    textColor=TEXT_MED,   fontName="Helvetica-Bold",
               leading=10, alignment=TA_CENTER)
S_TCEN   = _s("tcn",   fontSize=8,    textColor=TEXT_DARK,  fontName="Helvetica-Bold",
               leading=10, alignment=TA_CENTER)
S_TCENR  = _s("tcr",   fontSize=8,    textColor=TEXT_DARK,  leading=10, alignment=TA_CENTER)
S_TRIGHT = _s("trg",   fontSize=8,    textColor=TEXT_DARK,  fontName="Helvetica-Bold",
               leading=10, alignment=TA_RIGHT)
S_TRIGHTM= _s("trm",   fontSize=8,    textColor=TEXT_MUTED, leading=10, alignment=TA_RIGHT)
S_TLEFT  = _s("tll",   fontSize=8,    textColor=TEXT_DARK,  leading=10)
S_TLEFTB = _s("tlb",   fontSize=8,    textColor=TEXT_DARK,  fontName="Helvetica-Bold", leading=10)
S_SECR   = _s("scr",   fontSize=7.5,  textColor=LABEL_C,    leading=9,  alignment=TA_RIGHT)


# ── Two-pass canvas (Page X / Y) ─────────────────────────────────────────────
class _NumberedCanvas(pdfcanvas.Canvas):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._pages = []

    def showPage(self):
        self._pages.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        n = len(self._pages)
        for i, s in enumerate(self._pages, 1):
            self.__dict__.update(s)
            self.setFont("Helvetica", 7)
            self.setFillColor(LABEL_C)
            self.drawRightString(PAGE_W - MR, FOOTER_H - 2*mm, f"Page {i} / {n}")
            pdfcanvas.Canvas.showPage(self)
        pdfcanvas.Canvas.save(self)


# ── Canvas: header + footer (drawn on every page) ─────────────────────────────
def _draw_page(canvas, doc, logo_path):
    canvas.saveState()

    top = PAGE_H - 4 * mm

    # Logo (top-left)
    if logo_path and os.path.exists(logo_path):
        try:
            canvas.drawImage(logo_path, ML, top - 9*mm,
                             width=24*mm, height=9*mm,
                             preserveAspectRatio=True, mask="auto")
        except Exception:
            pass

    # Company address (below logo)
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(TEXT_MUTED)
    canvas.drawString(ML, top - 14*mm,
                      "Schönhauser Allee 36 · 10435 Berlin · Germany  "
                      "·  USt-IdNr DE332534899  ·  support@vanillasteel.com")

    # Certificate title (right-aligned)
    canvas.setFont("Helvetica-Bold", 13)
    canvas.setFillColor(TEXT_DARK)
    canvas.drawRightString(PAGE_W - MR, top - 6*mm, "Inspection Certificate 3.1")

    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(LABEL_C)
    canvas.drawRightString(PAGE_W - MR, top - 12*mm, "According to EN 10204:2004")

    # 2pt navy rule below masthead
    canvas.setStrokeColor(NAVY)
    canvas.setLineWidth(1.5)
    canvas.line(ML, PAGE_H - HEADER_H, PAGE_W - MR, PAGE_H - HEADER_H)

    # Footer divider
    canvas.setStrokeColor(BORDER_MED)
    canvas.setLineWidth(0.4)
    canvas.line(ML, FOOTER_H, PAGE_W - MR, FOOTER_H)

    # Footer text (left)
    canvas.setFont("Helvetica", 6.5)
    canvas.setFillColor(LABEL_C)
    canvas.drawString(ML, FOOTER_H - 2.5*mm,
                      "Vanilla Steel GmbH  ·  HRB 218619 B  ·  "
                      "Managing Directors: Clifford Ondara, Simon Zühlke  ·  "
                      "Electronically generated · valid without signature")

    canvas.restoreState()


# ── Helpers ───────────────────────────────────────────────────────────────────
def _fmt(v):
    if v is None or (isinstance(v, str) and not v.strip()):
        return "–"
    try:
        f = float(v)
        return f"{f:.4f}".rstrip("0").rstrip(".")
    except (ValueError, TypeError):
        return str(v)

def _fmtw(v):
    if v is None:
        return "–"
    try:
        return f"{int(float(v)):,}"
    except (ValueError, TypeError):
        return str(v)

def _fmt_date(d):
    if not d:
        return "–"
    try:
        from datetime import datetime
        dt = datetime.strptime(str(d).strip(), "%Y-%m-%d")
        # day without leading zero, cross-platform
        day = str(dt.day)
        return f"{day} {dt.strftime('%b %Y')}"
    except Exception:
        return str(d)

def _get_chem(chems, elem):
    for key in (elem, elem.lower(), elem.upper(), elem.capitalize()):
        val = chems.get(key)
        if val is not None:
            return _fmt(val)
    return "–"

def _lbl(text):
    return Paragraph(text.upper(), S_LABEL)

def _val(text):
    return Paragraph(str(text) if text else "–", S_VAL)

def _p(text, sty=None):
    return Paragraph(str(text) if text is not None else "–", sty or S_BODY)


# ── Section builders ──────────────────────────────────────────────────────────

def _meta_strip(cert_no, cert_date, standard):
    cw = CONTENT_W / 4
    data = [
        [_lbl("Certificate No."), _lbl("Issue date"),
         _lbl("Material standard"), _lbl("Place of issue")],
        [_val(cert_no or "–"), _val(_fmt_date(cert_date)),
         _val(standard or "–"), _val("Berlin, DE")],
    ]
    t = Table(data, colWidths=[cw] * 4)
    t.setStyle(TableStyle([
        ("TOPPADDING",    (0,0), (-1,-1), 0),
        ("BOTTOMPADDING", (0,0), (-1,-1), 2),
        ("LEFTPADDING",   (0,0), (-1,-1), 0),
        ("RIGHTPADDING",  (0,0), (-1,-1), 4),
        ("VALIGN",        (0,0), (-1,-1), "TOP"),
    ]))
    return t


def _order_refs_cell(so, vs_ref, customer_po, delivery_note):
    pairs = [
        ("Sales order",   so or "–"),
        ("VS reference",  vs_ref or "–"),
        ("Customer PO",   customer_po or "–"),
        ("Delivery note", delivery_note or "–"),
    ]
    rows = [[_p(k, S_BODY), _p(v, S_BODYB)] for k, v in pairs]
    t = Table(rows, colWidths=[30*mm, 30*mm])
    t.setStyle(TableStyle([
        ("TOPPADDING",    (0,0), (-1,-1), 1.5),
        ("BOTTOMPADDING", (0,0), (-1,-1), 1.5),
        ("LEFTPADDING",   (0,0), (-1,-1), 0),
        ("RIGHTPADDING",  (0,0), (-1,-1), 0),
        ("ALIGN",         (1,0), (1,-1), "RIGHT"),
    ]))
    return t


def _product_single(coil, grade, quality):
    """Product detail grid for a single-coil cert."""
    grade_str = (f"{grade} — {quality}" if quality else grade) or "–"
    thickness = str(coil.get("thickness_mm") or "–")
    width     = str(coil.get("width_mm")     or "–")
    dims = (f"{thickness} × {width} mm"
            if thickness != "–" and width != "–" else "–")
    weight = (_fmtw(coil.get("weight_kg")) + " kg"
              if coil.get("weight_kg") else "–")
    cast = coil.get("cast_no") or coil.get("coil_no") or "–"
    qty  = str(coil.get("qty") or 1)

    pairs = [
        ("Heat / Cast No.", cast), ("Dimensions", dims),
        ("Net weight", weight),    ("Pieces", qty),
    ]
    rows = [[_p(k, S_MUTED), _p(v, S_BODYB)] for k, v in pairs]
    t = Table(rows, colWidths=[28*mm, 28*mm])
    t.setStyle(TableStyle([
        ("TOPPADDING",    (0,0), (-1,-1), 2),
        ("BOTTOMPADDING", (0,0), (-1,-1), 2),
        ("LEFTPADDING",   (0,0), (-1,-1), 0),
        ("RIGHTPADDING",  (0,0), (-1,-1), 0),
        ("ALIGN",         (1,0), (1,-1), "RIGHT"),
    ]))
    return [
        _p(grade_str, S_GRADE),
        Spacer(1, 3*mm),
        t,
    ]


def _product_multi(coils, grade, quality, parsed_cert):
    """Product summary cell for multi-coil cert (detail goes in delivery table)."""
    grade_str = (f"{grade} — {quality}" if quality else grade) or "–"
    total_wt = parsed_cert.get("total_weight_kg") or sum(
        float(c.get("weight_kg") or 0) for c in coils) or None
    wt_s = _fmtw(total_wt) + " kg" if total_wt else "–"
    return [
        _p(grade_str, S_GRADE),
        Spacer(1, 3*mm),
        _p(f"{len(coils)} coils  ·  {wt_s} total", S_BODY),
    ]


def _middle_section(odoo_data, coils, grade, quality, parsed_cert):
    """3-column table: Consignee | Order references | Product."""
    buyer      = odoo_data.get("buyer_name",    "") or "–"
    buyer_addr = odoo_data.get("buyer_address", "") or ""
    so         = odoo_data.get("so_number",     "") or "–"
    vs_ref     = odoo_data.get("vs_reference",  "") or ""
    cust_po    = (odoo_data.get("customer_po",  "")
                  or parsed_cert.get("po_number", "") or "")
    del_note   = odoo_data.get("delivery_note", "") or ""

    single = (len(coils) == 1)
    coil0  = coils[0] if coils else {}

    # Column contents (lists of flowables)
    col1 = [
        _lbl("Consignee"),
        Spacer(1, 1.5*mm),
        _p(buyer, S_BODYB),
    ]
    if buyer_addr:
        col1.append(_p(buyer_addr, S_BODY))

    col2 = [
        _lbl("Order references"),
        Spacer(1, 1.5*mm),
        _order_refs_cell(so, vs_ref, cust_po, del_note),
    ]

    col3 = [_lbl("Product"), Spacer(1, 1.5*mm)]
    if single:
        col3 += _product_single(coil0, grade, quality)
    else:
        col3 += _product_multi(coils, grade, quality, parsed_cert)

    cw = [CONTENT_W * f for f in [0.30, 0.30, 0.40]]
    data = [[col1, col2, col3]]
    t = Table(data, colWidths=cw)
    t.setStyle(TableStyle([
        ("VALIGN",        (0,0), (-1,-1), "TOP"),
        ("TOPPADDING",    (0,0), (-1,-1), 0),
        ("BOTTOMPADDING", (0,0), (-1,-1), 0),
        ("LEFTPADDING",   (0,0), (-1,-1), 0),
        ("RIGHTPADDING",  (0,0), (-1,-1), 8),
        ("LINEABOVE",     (0,0), (-1,0),  0.5, BORDER_MED),
    ]))
    return t


def _delivery_table(coils):
    """Shown only for multi-coil certs, between middle section and chemistry."""
    has_coil = any(c.get("coil_no") for c in coils)
    has_cast = any(c.get("cast_no") for c in coils)

    if has_coil and has_cast:
        headers = ["#", "Heat / Cast No.", "Coil No.", "Thickness mm", "Width mm", "Net Weight kg"]
        weights = [0.05, 0.22, 0.22, 0.17, 0.17, 0.17]
    elif has_cast:
        headers = ["#", "Heat / Cast No.", "Thickness mm", "Width mm", "Net Weight kg"]
        weights = [0.05, 0.30, 0.22, 0.22, 0.21]
    else:
        headers = ["#", "Pack / Coil No.", "Thickness mm", "Width mm", "Net Weight kg"]
        weights = [0.05, 0.30, 0.22, 0.22, 0.21]

    cw = [CONTENT_W * w for w in weights]

    head_row = [_p(h, S_THDR) for h in headers]
    rows = [head_row]
    for i, coil in enumerate(coils, 1):
        wt = _fmtw(coil.get("weight_kg"))
        if has_coil and has_cast:
            row = [i, coil.get("cast_no") or "–", coil.get("coil_no") or "–",
                   coil.get("thickness_mm") or "–", coil.get("width_mm") or "–", wt]
        elif has_cast:
            row = [i, coil.get("cast_no") or "–",
                   coil.get("thickness_mm") or "–", coil.get("width_mm") or "–", wt]
        else:
            row = [i, coil.get("coil_no") or coil.get("pack_nr") or "–",
                   coil.get("thickness_mm") or "–", coil.get("width_mm") or "–", wt]
        rows.append([_p(str(c), S_TCENR if idx > 0 else S_TCENR)
                     for idx, c in enumerate(row)])

    t = Table(rows, colWidths=cw, repeatRows=1)
    t.setStyle(TableStyle([
        ("LINEBELOW",     (0,0),  (-1,0),  1.5, NAVY),
        ("LINEBELOW",     (0,1),  (-1,-1), 0.5, BORDER_LIGHT),
        ("TOPPADDING",    (0,0),  (-1,-1), 5),
        ("BOTTOMPADDING", (0,0),  (-1,-1), 5),
        ("LEFTPADDING",   (0,0),  (-1,-1), 3),
        ("RIGHTPADDING",  (0,0),  (-1,-1), 3),
        ("ALIGN",         (0,0),  (-1,-1), "CENTER"),
        ("FONTNAME",      (0,1),  (-1,-1), "Helvetica"),
        ("FONTSIZE",      (0,0),  (-1,-1), 8),
        ("TEXTCOLOR",     (0,0),  (-1,-1), TEXT_DARK),
    ]))
    return t


def _chem_table(coils):
    ELEMS = ["C","Si","Mn","P","S","Al","Nb","Ti","V","Cu","Cr","Ni","Mo"]
    present = [e for e in ELEMS
               if any(_get_chem(c.get("chemicals") or {}, e) != "\u2013" for c in coils)]
    if not present:
        return None
    cw_heat = 26 * mm
    cw_elem = (CONTENT_W - cw_heat) / len(present)
    head = [_p("Heat No.", S_THDR)] + [_p(e, S_THDR) for e in present]
    rows = [head]
    seen_heats = {}
    for coil in coils:
        heat = coil.get("cast_no") or coil.get("coil_no") or "\u2013"
        if heat in seen_heats:
            continue
        seen_heats[heat] = True
        chems = coil.get("chemicals") or {}
        row = [_p(heat, S_TCENR)] + [_p(_get_chem(chems, e), S_TCENR) for e in present]
        rows.append(row)
    t = Table(rows, colWidths=[cw_heat] + [cw_elem] * len(present))
    t.setStyle(TableStyle([
        ("LINEBELOW",     (0,0),  (-1,0),  1.5, NAVY),
        ("LINEBELOW",     (0,1),  (-1,-1), 0.5, BORDER_LIGHT),
        ("TOPPADDING",    (0,0),  (-1,-1), 6),
        ("BOTTOMPADDING", (0,0),  (-1,-1), 6),
        ("LEFTPADDING",   (0,0),  (-1,-1), 3),
        ("RIGHTPADDING",  (0,0),  (-1,-1), 3),
        ("ALIGN",         (0,0),  (-1,-1), "CENTER"),
        ("FONTNAME",      (0,0),  (-1,0),  "Helvetica-Bold"),
        ("FONTNAME",      (0,1),  (-1,-1), "Helvetica"),
        ("FONTNAME",      (0,1),  (0,-1),  "Helvetica-Bold"),
        ("FONTSIZE",      (0,0),  (-1,-1), 8),
        ("TEXTCOLOR",     (0,0),  (-1,0),  TEXT_MED),
        ("TEXTCOLOR",     (0,1),  (-1,-1), TEXT_DARK),
    ]))
    return t


def _mech_table(coils):
    has_mech = any(c.get("mechanical") for c in coils)
    if not has_mech:
        return None
    cw = [CONTENT_W * 0.56 * f for f in [0.50, 0.18, 0.20, 0.12]]
    head = [_p(h, S_THDR) for h in ["Property", "Result", "Specified", "Unit"]]
    rows = [head]
    seen = {}
    for coil in coils:
        m = coil.get("mechanical") or {}
        if not m:
            continue
        key = (coil.get("cast_no") or "", m.get("rp02"), m.get("rm"), m.get("a_pct"))
        if key in seen:
            continue
        seen[key] = True
        rp02 = m.get("rp02")
        rm   = m.get("rm")
        a    = m.get("a_pct")
        a_s  = (f"{float(a):.1f}" if isinstance(a, (int, float)) else str(a)) if a else "\u2013"
        rows += [
            [_p("Yield strength Rp0.2", S_TLEFT), _p(str(rp02) if rp02 else "\u2013", S_TRIGHT),
             _p("", S_TRIGHTM), _p("N/mm\u00b2", S_TRIGHTM)],
            [_p("Tensile strength Rm",  S_TLEFT), _p(str(rm)   if rm   else "\u2013", S_TRIGHT),
             _p("", S_TRIGHTM), _p("N/mm\u00b2", S_TRIGHTM)],
            [_p("Elongation A80",       S_TLEFT), _p(a_s, S_TRIGHT),
             _p("", S_TRIGHTM), _p("%", S_TRIGHTM)],
        ]
    t = Table(rows, colWidths=cw)
    t.setStyle(TableStyle([
        ("LINEBELOW",     (0,0),  (-1,0),  1.5, NAVY),
        ("LINEBELOW",     (0,1),  (-1,-1), 0.5, BORDER_LIGHT),
        ("TOPPADDING",    (0,0),  (-1,-1), 7),
        ("BOTTOMPADDING", (0,0),  (-1,-1), 7),
        ("LEFTPADDING",   (0,0),  (-1,-1), 3),
        ("RIGHTPADDING",  (0,0),  (-1,-1), 3),
        ("FONTNAME",      (0,0),  (-1,0),  "Helvetica-Bold"),
        ("FONTNAME",      (0,1),  (-1,-1), "Helvetica"),
        ("FONTNAME",      (1,1),  (1,-1),  "Helvetica-Bold"),
        ("FONTSIZE",      (0,0),  (-1,-1), 8),
        ("TEXTCOLOR",     (0,0),  (-1,0),  TEXT_MED),
        ("TEXTCOLOR",     (2,1),  (3,-1),  TEXT_MUTED),
    ]))
    return t


def _bottom_section(mech_t):
    decl = (
        "We hereby certify that the material described above has been tested "
        "and inspected in accordance with the order and the applicable standard, "
        "and that the results comply with the specification."
    )
    left_cell  = [mech_t] if mech_t else [_p("No mechanical data.", S_MUTED)]
    right_cell = [
        _p(decl, S_DECL),
        Spacer(1, 6*mm),
        _p("Vanilla Steel GmbH", S_SIGNAM),
        _p("Quality Assurance", S_SIGROL),
        Spacer(1, 4*mm),
        _p("Electronically generated \u00b7 valid without signature", S_NOTE),
    ]
    cw = [CONTENT_W * 0.56, CONTENT_W * 0.44]
    t = Table([[left_cell, right_cell]], colWidths=cw)
    t.setStyle(TableStyle([
        ("VALIGN",        (0,0), (-1,-1), "BOTTOM"),
        ("TOPPADDING",    (0,0), (-1,-1), 0),
        ("BOTTOMPADDING", (0,0), (-1,-1), 0),
        ("LEFTPADDING",   (0,0), (-1,-1), 0),
        ("RIGHTPADDING",  (0,0), (-1,-1), 0),
        ("LEFTPADDING",   (1,0), (1,0),   10),
    ]))
    return t


def _sec_hdr(left, right=""):
    data = [[_p(f"<b>{left}</b>", S_TLEFTB), _p(right, S_SECR)]]
    t = Table(data, colWidths=[CONTENT_W * 0.6, CONTENT_W * 0.4])
    t.setStyle(TableStyle([
        ("TOPPADDING",    (0,0), (-1,-1), 0),
        ("BOTTOMPADDING", (0,0), (-1,-1), 3),
        ("LEFTPADDING",   (0,0), (-1,-1), 0),
        ("RIGHTPADDING",  (0,0), (-1,-1), 0),
    ]))
    return t


def generate_certificate(
    parsed_cert: dict,
    odoo_data:   dict,
    logo_path:   str | None = None,
) -> bytes:
    buf   = BytesIO()
    coils = parsed_cert.get("coils") or []
    multi = len(coils) > 1
    grade    = parsed_cert.get("grade",       "") or ""
    quality  = parsed_cert.get("quality",     "") or ""
    cert_no  = parsed_cert.get("cert_number", "") or "\u2013"
    cert_date= parsed_cert.get("cert_date",   "") or ""
    standard = parsed_cert.get("standard",    "") or ""

    story = []
    story.append(_meta_strip(cert_no, cert_date, standard))
    story.append(Spacer(1, 5*mm))
    story.append(_middle_section(odoo_data, coils, grade, quality, parsed_cert))
    story.append(Spacer(1, 5*mm))
    if multi:
        story.append(_sec_hdr("Delivery details"))
        story.append(_delivery_table(coils))
        story.append(Spacer(1, 5*mm))
    chem_t = _chem_table(coils)
    if chem_t:
        story.append(_sec_hdr("Chemical composition", "Heat analysis \u00b7 % by mass"))
        story.append(chem_t)
        story.append(Spacer(1, 5*mm))
    mech_t = _mech_table(coils)
    if mech_t:
        story.append(_sec_hdr("Mechanical properties"))
    story.append(_bottom_section(mech_t))

    doc = BaseDocTemplate(
        buf,
        pagesize=(PAGE_W, PAGE_H),
        leftMargin=ML, rightMargin=MR,
        topMargin=HEADER_H + 2*mm,
        bottomMargin=FOOTER_H + 3*mm,
    )
    frame = Frame(ML, FRAME_Y, CONTENT_W, FRAME_H,
                  leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    doc.addPageTemplates([PageTemplate(
        id="main", frames=[frame],
        onPage=lambda c, d: _draw_page(c, d, logo_path),
    )])
    doc.build(story, canvasmaker=_NumberedCanvas)
    return buf.getvalue()
