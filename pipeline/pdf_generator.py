"""
Vanilla Steel — Neutralised Inspection Certificate (EN 10204 3.1)
Template v2 — multi-page landscape A4, matching HTML template.

Sections (in order):
  Meta strip   — 5-col: VS cert no | mill cert no | date | standard | insp type
  Parties      — 3-col: Manufacturer | Consignee | Destination
  Refs + Spec  — 2-col: Order references KV | Product specification KV
  Items table  — per-coil: Item CoilNo Heat Serial Thick Width NetWt GrossWt Pcs
  Mechanical   — per-coil: CoilNo Dir Cond Re Rm A Ratio Bend Coat
  Chemical     — per-heat: Heat C Si Mn P S Al N Cu Cr Ni Nb Ti V Mo B
  Declaration  — 2-col: declaration+remarks+footnote | signer+verifyCode
"""
from io import BytesIO
import os, hashlib
from datetime import datetime

from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_RIGHT, TA_CENTER
from reportlab.platypus import (
    BaseDocTemplate, Frame, PageTemplate,
    Paragraph, Table, TableStyle, Spacer, KeepTogether,
)
from reportlab.pdfgen import canvas as pdfcanvas


# ── Palette ──────────────────────────────────────────────────────────────────
NAVY         = colors.HexColor("#000831")
TEXT_DARK    = colors.HexColor("#11151f")
TEXT_MED     = colors.HexColor("#4b515e")
TEXT_MED2    = colors.HexColor("#5b6170")
TEXT_MUTED2  = colors.HexColor("#6b7280")
TEXT_MUTED   = colors.HexColor("#7b8290")
LABEL_C      = colors.HexColor("#9aa0ac")
BORDER_LIGHT = colors.HexColor("#eef0f3")
BORDER_MED   = colors.HexColor("#e6e8ee")

# ── Page geometry ─────────────────────────────────────────────────────────────
PAGE_W, PAGE_H = landscape(A4)
ML = MR       = 14 * mm
MAST_H        = 20 * mm
FOOT_H        = 11 * mm
CONTENT_W     = PAGE_W - ML - MR
FRAME_Y       = FOOT_H
FRAME_H       = PAGE_H - MAST_H - FOOT_H

# Chemical elements — fixed order from template
CHEM_ELEMS = ["C","Si","Mn","P","S","Al","N","Cu","Cr","Ni","Nb","Ti","V","Mo","B"]


# ── Styles ────────────────────────────────────────────────────────────────────
def _s(name, **kw):
    b = dict(fontName="Helvetica", fontSize=8, leading=10,
             textColor=TEXT_DARK, spaceAfter=0, spaceBefore=0)
    b.update(kw)
    return ParagraphStyle(name, **b)

S_LABEL  = _s("lbl",  fontSize=6.75, textColor=LABEL_C,    leading=9)
S_META_V = _s("mtv",  fontSize=9.75, textColor=TEXT_DARK,  fontName="Helvetica-Bold", leading=12)
S_PTY_N  = _s("ptn",  fontSize=9.75, textColor=TEXT_DARK,  fontName="Helvetica-Bold", leading=12)
S_PTY_A  = _s("pta",  fontSize=8.6,  textColor=TEXT_MED,   leading=13)
S_SEC    = _s("sec",  fontSize=8.25, textColor=TEXT_DARK,  fontName="Helvetica-Bold", leading=10)
S_SEC_R  = _s("scr",  fontSize=7.5,  textColor=LABEL_C,    leading=9,  alignment=TA_RIGHT)
S_KV_K   = _s("kvk",  fontSize=8.6,  textColor=TEXT_MUTED2, leading=11)
S_KV_V   = _s("kvv",  fontSize=8.6,  textColor=TEXT_DARK,  fontName="Helvetica-Bold", leading=11, alignment=TA_RIGHT)
S_TH     = _s("thd",  fontSize=8.25, textColor=TEXT_MED2,  fontName="Helvetica-Bold", leading=10)
S_TH_C   = _s("thc",  fontSize=8.25, textColor=TEXT_MED2,  fontName="Helvetica-Bold", leading=10, alignment=TA_CENTER)
S_TH_R   = _s("thr",  fontSize=8.25, textColor=TEXT_MED2,  fontName="Helvetica-Bold", leading=10, alignment=TA_RIGHT)
S_TD_L   = _s("tdl",  fontSize=8.25, textColor=TEXT_DARK,  leading=10)
S_TD_LB  = _s("tdlb", fontSize=8.25, textColor=TEXT_DARK,  fontName="Helvetica-Bold", leading=10)
S_TD_R   = _s("tdr",  fontSize=8.25, textColor=TEXT_DARK,  leading=10, alignment=TA_RIGHT)
S_TD_RB  = _s("tdrb", fontSize=8.25, textColor=TEXT_DARK,  fontName="Helvetica-Bold", leading=10, alignment=TA_RIGHT)
S_TD_C   = _s("tdc",  fontSize=8.25, textColor=TEXT_MED,   leading=10, alignment=TA_CENTER)
S_TD_MUT = _s("tdm",  fontSize=8.25, textColor=TEXT_MUTED, leading=10, alignment=TA_RIGHT)
S_TOT_L  = _s("totl", fontSize=8.25, textColor=TEXT_DARK,  fontName="Helvetica-Bold", leading=10)
S_TOT_R  = _s("totr", fontSize=8.25, textColor=TEXT_DARK,  fontName="Helvetica-Bold", leading=10, alignment=TA_RIGHT)
S_DECL   = _s("dcl",  fontSize=8.25, textColor=TEXT_MED,   leading=13)
S_FNOTE  = _s("fno",  fontSize=7.5,  textColor=LABEL_C,    leading=9)
S_SIGNER = _s("sgn",  fontSize=9.5,  textColor=TEXT_DARK,  fontName="Helvetica-Bold", leading=12)
S_SIGROL = _s("sgr",  fontSize=7.8,  textColor=TEXT_MUTED, leading=10)
S_VCODE  = _s("vcd",  fontSize=7.5,  textColor=TEXT_MUTED2, leading=9)
S_PLACE  = _s("plc",  fontSize=7.5,  textColor=LABEL_C,    leading=10, alignment=TA_RIGHT)


# ── Numbered canvas ───────────────────────────────────────────────────────────
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
            self.drawRightString(PAGE_W - MR, FOOT_H * 0.35, f"Page {i} / {n}")
            pdfcanvas.Canvas.showPage(self)
        pdfcanvas.Canvas.save(self)


# ── Page decoration (header + footer, every page) ─────────────────────────────
def _draw_page(canvas, doc, logo_path, vs_cert_no, mill_cert_no):
    canvas.saveState()
    top = PAGE_H - 3 * mm

    # Logo
    if logo_path and os.path.exists(logo_path):
        try:
            canvas.drawImage(logo_path, ML, top - 9 * mm,
                             width=24 * mm, height=9 * mm,
                             preserveAspectRatio=True, mask="auto")
        except Exception:
            pass

    # Title (right side)
    canvas.setFont("Helvetica-Bold", 13)
    canvas.setFillColor(TEXT_DARK)
    canvas.drawRightString(PAGE_W - MR, top - 5 * mm, "Inspection Certificate 3.1")

    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(TEXT_MUTED2)
    canvas.drawRightString(PAGE_W - MR, top - 11 * mm,
                           f"EN 10204:2004  ·  Certificate {vs_cert_no}")

    # Masthead rule
    canvas.setStrokeColor(NAVY)
    canvas.setLineWidth(1.5)
    canvas.line(ML, PAGE_H - MAST_H, PAGE_W - MR, PAGE_H - MAST_H)

    # Footer rule
    canvas.setStrokeColor(BORDER_MED)
    canvas.setLineWidth(0.4)
    canvas.line(ML, FOOT_H, PAGE_W - MR, FOOT_H)

    # Footer text
    canvas.setFont("Helvetica", 6.5)
    canvas.setFillColor(LABEL_C)
    canvas.drawString(ML, FOOT_H * 0.35,
                      f"Certificate {vs_cert_no}  ·  Mill cert {mill_cert_no}")
    canvas.drawCentredString(PAGE_W / 2, FOOT_H * 0.35,
                             "VANILLA STEEL  ·  Schönhauser Allee 36, 10435 Berlin, Germany  ·  USt-IdNr DE332534899")

    canvas.restoreState()


# ── Helpers ───────────────────────────────────────────────────────────────────
def _fmt(v, dec=4):
    if v is None or (isinstance(v, str) and not v.strip()):
        return "–"
    try:
        return f"{float(v):.{dec}f}".rstrip("0").rstrip(".")
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
        dt = datetime.strptime(str(d).strip(), "%Y-%m-%d")
        return f"{dt.day} {dt.strftime('%b %Y')}"
    except Exception:
        return str(d)

def _blank(v, fb="–"):
    if v is None or (isinstance(v, str) and not v.strip()):
        return fb
    return str(v)

def _get_chem(chems, elem):
    for k in (elem, elem.lower(), elem.upper(), elem.capitalize()):
        val = chems.get(k)
        if val is not None:
            return _fmt(val)
    return "–"

def _p(text, sty=None):
    return Paragraph(str(text) if text is not None else "–", sty or S_TD_L)

def _lbl(text):
    return Paragraph(text.upper(), S_LABEL)


# ── Section header ────────────────────────────────────────────────────────────
def _sec_hdr(left_txt, right_txt="", width=None):
    w = width or CONTENT_W
    t = Table([[_p(left_txt, S_SEC), _p(right_txt, S_SEC_R)]],
              colWidths=[w * 0.65, w * 0.35])
    t.setStyle(TableStyle([
        ("LINEBELOW",     (0,0), (-1,-1), 1.5, NAVY),
        ("TOPPADDING",    (0,0), (-1,-1), 0),
        ("BOTTOMPADDING", (0,0), (-1,-1), 5),
        ("LEFTPADDING",   (0,0), (-1,-1), 0),
        ("RIGHTPADDING",  (0,0), (-1,-1), 0),
    ]))
    return t


# ── 1. Meta strip (5-col) ─────────────────────────────────────────────────────
def _meta_strip(vs_cert_no, mill_cert_no, issue_date, mat_standard, insp_type):
    cw = CONTENT_W / 5
    data = [
        [_lbl("Certificate No."), _lbl("Mill certificate No."), _lbl("Issue date"),
         _lbl("Material standard"), _lbl("Inspection type")],
        [_p(_blank(vs_cert_no),   S_META_V),
         _p(_blank(mill_cert_no), S_META_V),
         _p(_blank(issue_date),   S_META_V),
         _p(_blank(mat_standard), S_META_V),
         _p(_blank(insp_type),    S_META_V)],
    ]
    t = Table(data, colWidths=[cw] * 5)
    t.setStyle(TableStyle([
        ("TOPPADDING",    (0,0), (-1,-1), 0),
        ("BOTTOMPADDING", (0,0), (-1,-1), 2),
        ("LEFTPADDING",   (0,0), (-1,-1), 0),
        ("RIGHTPADDING",  (0,0), (-1,-1), 4),
        ("VALIGN",        (0,0), (-1,-1), "TOP"),
    ]))
    return t


# ── 2. Parties (3-col) ────────────────────────────────────────────────────────
def _parties(mfr_name, mfr_addr, csg_name, csg_addr, dst_name, dst_addr):
    cw = CONTENT_W / 3

    def col(label, name, addr):
        items = [_lbl(label), Spacer(1, 1.5*mm), _p(_blank(name), S_PTY_N)]
        if addr and str(addr).strip():
            items.append(_p(str(addr), S_PTY_A))
        return items

    t = Table([[
        col("Manufacturer / Mill", mfr_name, mfr_addr),
        col("Consignee", csg_name, csg_addr),
        col("Destination / Delivery point", dst_name, dst_addr),
    ]], colWidths=[cw] * 3)
    t.setStyle(TableStyle([
        ("VALIGN",        (0,0), (-1,-1), "TOP"),
        ("TOPPADDING",    (0,0), (-1,-1), 0),
        ("BOTTOMPADDING", (0,0), (-1,-1), 0),
        ("LEFTPADDING",   (0,0), (-1,-1), 0),
        ("RIGHTPADDING",  (0,0), (-1,-1), 8),
        ("LINEABOVE",     (0,0), (-1,0),  0.5, BORDER_MED),
    ]))
    return t


# ── 3. Refs + Spec (2-col KV) ─────────────────────────────────────────────────
def _kv_list(pairs, col_w):
    rows = [[_p(k, S_KV_K), _p(_blank(v), S_KV_V)] for k, v in pairs]
    t = Table(rows, colWidths=[col_w * 0.54, col_w * 0.46])
    style = [
        ("TOPPADDING",    (0,0), (-1,-1), 3),
        ("BOTTOMPADDING", (0,0), (-1,-1), 3),
        ("LEFTPADDING",   (0,0), (-1,-1), 0),
        ("RIGHTPADDING",  (0,0), (-1,-1), 0),
        ("VALIGN",        (0,0), (-1,-1), "MIDDLE"),
    ]
    for i in range(len(rows) - 1):
        style.append(("LINEBELOW", (0,i), (-1,i), 0.4, BORDER_MED))
    t.setStyle(TableStyle(style))
    return t


def _refs_spec(refs, spec):
    GAP = 9 * mm
    cw = (CONTENT_W - GAP) / 2   # each column's usable width

    left  = [_sec_hdr("Order references",     width=cw), Spacer(1, 2*mm), _kv_list(refs, cw)]
    right = [_sec_hdr("Product specification", width=cw), Spacer(1, 2*mm), _kv_list(spec, cw)]

    t = Table([[left, Spacer(GAP, 1), right]], colWidths=[cw, GAP, cw])
    t.setStyle(TableStyle([
        ("VALIGN",        (0,0), (-1,-1), "TOP"),
        ("TOPPADDING",    (0,0), (-1,-1), 0),
        ("BOTTOMPADDING", (0,0), (-1,-1), 0),
        ("LEFTPADDING",   (0,0), (-1,-1), 0),
        ("RIGHTPADDING",  (0,0), (-1,-1), 0),
    ]))
    return t


# ── 4. Items table ────────────────────────────────────────────────────────────
def _items_table(coils, total_weight, has_net=True, has_gross=True):
    # Proportional column widths summing to CONTENT_W
    fracs = [0.045, 0.17, 0.12, 0.10, 0.08, 0.08, 0.13, 0.13, 0.065]
    cw = [CONTENT_W * f for f in fracs]

    header = [
        _p("Item",          S_TH),   _p("Coil / Pack No.", S_TH),
        _p("Heat / Cast",   S_TH),   _p("Serial No.",      S_TH),
        _p("Thickness",     S_TH_R), _p("Width",           S_TH_R),
        _p("Net wt.",       S_TH_R), _p("Gross wt.",       S_TH_R),
        _p("Pcs",           S_TH_R),
    ]

    rows = [header]
    for i, c in enumerate(coils, 1):
        net_kg   = c.get("weight_kg")
        gross_kg = c.get("gross_weight_kg")
        rows.append([
            _p(str(i),                                               S_TD_L),
            _p(_blank(c.get("coil_no")),                             S_TD_LB),
            _p(_blank(c.get("cast_no")),                             S_TD_L),
            _p(_blank(c.get("serial_no") or c.get("serial", "")),    S_TD_C),
            _p(_blank(c.get("thickness_mm")),                        S_TD_R),
            _p(_blank(c.get("width_mm")),                            S_TD_R),
            _p(_fmtw(net_kg)   if net_kg   is not None else "–",     S_TD_R),
            _p(_fmtw(gross_kg) if gross_kg is not None else "–",     S_TD_MUT),
            _p(str(c.get("qty") or 1),                               S_TD_R),
        ])

    # Total row — spans cols 0-5, then net total, gross total, pcs count
    # If no net weights exist (gross-only cert like ArcelorMittal), put total in gross column
    if has_net:
        net_total_str   = _fmtw(total_weight)
        gross_total_str = ""
    else:
        net_total_str   = ""
        gross_total_str = _fmtw(total_weight)
    total_row = [
        _p(f"Total — {len(coils)} coils", S_TOT_L),
        "", "", "", "", "",
        _p(net_total_str,   S_TOT_R),
        _p(gross_total_str, S_TOT_R),
        _p(str(len(coils)), S_TOT_R),
    ]
    rows.append(total_row)

    t = Table(rows, colWidths=cw, repeatRows=1)
    t.setStyle(TableStyle([
        ("SPAN",          (0,-1), (5,-1)),
        ("LINEBELOW",     (0,0),  (-1,0),  1.5, NAVY),
        ("LINEABOVE",     (0,-1), (-1,-1), 1.5, NAVY),
        ("LINEBELOW",     (0,1),  (-1,-2), 0.5, BORDER_LIGHT),
        ("TOPPADDING",    (0,0),  (-1,-1), 6),
        ("BOTTOMPADDING", (0,0),  (-1,-1), 6),
        ("LEFTPADDING",   (0,0),  (-1,-1), 5),
        ("RIGHTPADDING",  (0,0),  (-1,-1), 5),
        ("FONTSIZE",      (0,0),  (-1,-1), 8.25),
        ("TEXTCOLOR",     (0,0),  (-1,0),  TEXT_MED2),
        ("TEXTCOLOR",     (0,1),  (0,-2),  TEXT_MUTED),
        ("ALIGN",         (4,0),  (-1,-1), "RIGHT"),
        ("ALIGN",         (3,1),  (3,-1),  "CENTER"),
        ("FONTNAME",      (0,-1), (-1,-1), "Helvetica-Bold"),
    ]))
    return t


# ── 5. Mechanical table (one row per coil) ────────────────────────────────────
def _mech_table(coils):
    has_mech = any(c.get("mechanical") for c in coils)
    if not has_mech:
        return None

    fracs = [0.18, 0.055, 0.075, 0.13, 0.13, 0.07, 0.10, 0.10, 0.16]
    cw = [CONTENT_W * f for f in fracs]

    header = [
        _p("Coil / Pack No.",     S_TH),
        _p("Dir.",                S_TH_C),
        _p("Cond.",               S_TH_C),
        _p("Yield Re/Rp0.2",      S_TH_R),
        _p("Tensile Rm",          S_TH_R),
        _p("A",                   S_TH_R),
        _p("Rp/Rm",               S_TH_R),
        _p("Bend test",           S_TH_C),
        _p("Coating top/btm",     S_TH_R),
    ]

    rows = [header]
    for c in coils:
        m = c.get("mechanical") or {}
        rp02 = m.get("rp02")
        rm   = m.get("rm")
        a    = m.get("a_pct")
        ratio = ""
        if rp02 and rm:
            try:
                ratio = f"{float(rp02)/float(rm):.2f}"
            except Exception:
                pass
        coil_id = _blank(c.get("coil_no") or c.get("cast_no"))
        rows.append([
            _p(coil_id,                              S_TD_LB),
            _p(m.get("dir", "L"),                   S_TD_C),
            _p(_blank(m.get("cond", "")),           S_TD_C),
            _p(str(rp02) if rp02 else "–",          S_TD_RB),
            _p(str(rm)   if rm   else "–",          S_TD_RB),
            _p(_fmt(a, 1) if a is not None else "–", S_TD_R),
            _p(ratio or "–",                        S_TD_MUT),
            _p(_blank(m.get("bend", "")),           S_TD_C),
            _p(_blank(c.get("coating", "") or c.get("coating_g_m2", "")), S_TD_MUT),
        ])

    t = Table(rows, colWidths=cw, repeatRows=1)
    t.setStyle(TableStyle([
        ("LINEBELOW",     (0,0),  (-1,0),  1.5, NAVY),
        ("LINEBELOW",     (0,1),  (-1,-1), 0.5, BORDER_LIGHT),
        ("TOPPADDING",    (0,0),  (-1,-1), 6),
        ("BOTTOMPADDING", (0,0),  (-1,-1), 6),
        ("LEFTPADDING",   (0,0),  (-1,-1), 5),
        ("RIGHTPADDING",  (0,0),  (-1,-1), 5),
        ("FONTSIZE",      (0,0),  (-1,-1), 8.25),
        ("TEXTCOLOR",     (0,0),  (-1,0),  TEXT_MED2),
    ]))
    return t


# ── 6. Chemical table (one row per unique heat) ───────────────────────────────
def _chem_table(coils):
    seen = {}
    for c in coils:
        heat = c.get("cast_no") or c.get("coil_no") or ""
        if heat and heat not in seen:
            seen[heat] = c.get("chemicals") or {}
    if not seen:
        return None

    present = [e for e in CHEM_ELEMS
               if any(_get_chem(ch, e) != "–" for ch in seen.values())]
    if not present:
        return None

    cw_heat = CONTENT_W * 0.12
    cw_elem = (CONTENT_W - cw_heat) / len(present)

    header = [_p("Heat / Cast", S_TH)] + [_p(e, S_TH_R) for e in present]
    rows = [header]
    for heat, chems in seen.items():
        rows.append([_p(heat, S_TD_LB)] + [_p(_get_chem(chems, e), S_TD_R) for e in present])

    t = Table(rows, colWidths=[cw_heat] + [cw_elem] * len(present), repeatRows=1)
    t.setStyle(TableStyle([
        ("LINEBELOW",     (0,0),  (-1,0),  1.5, NAVY),
        ("LINEBELOW",     (0,1),  (-1,-1), 0.5, BORDER_LIGHT),
        ("TOPPADDING",    (0,0),  (-1,-1), 5),
        ("BOTTOMPADDING", (0,0),  (-1,-1), 5),
        ("LEFTPADDING",   (0,0),  (-1,-1), 4),
        ("RIGHTPADDING",  (0,0),  (-1,-1), 4),
        ("FONTSIZE",      (0,0),  (-1,-1), 8.25),
        ("TEXTCOLOR",     (0,0),  (-1,0),  TEXT_MED2),
    ]))
    return t


# ── 7. Declaration block ──────────────────────────────────────────────────────
def _declaration(parsed_cert, mill_cert_no, vs_cert_no, issue_date):
    DECL_TEXT = (
        "We hereby certify that the material described above has been manufactured, "
        "tested and inspected in accordance with the order and the applicable standard, "
        "and that the results comply with the specification."
    )
    remarks  = _blank(parsed_cert.get("remarks", ""), "")
    mfr_name = _blank(parsed_cert.get("manufacturer_name", ""), "")
    footnote = f"Composition and results transcribed from mill certificate {mill_cert_no}"
    if mfr_name:
        footnote += f" ({mfr_name})"
    footnote += "."

    h = hashlib.md5(vs_cert_no.encode()).hexdigest().upper()
    verify_code = f"VS-{h[:4]}-{h[4:8]}"

    left_items = [_p(DECL_TEXT, S_DECL)]
    if remarks:
        left_items += [Spacer(1, 2*mm),
                       _p(f"<font color='#6b7280'>Remarks: </font>{remarks}", S_DECL)]
    left_items += [Spacer(1, 2*mm), _p(footnote, S_FNOTE)]

    right_items = [
        _p("Vanilla Steel GmbH", S_SIGNER),
        _p("Quality Assurance",  S_SIGROL),
        Spacer(1, 3*mm),
        _p("Verification code",  S_VCODE),
        _p(verify_code,          S_KV_V),
        Spacer(1, 3*mm),
        _p(f"Berlin, DE  ·  {issue_date}  ·  Valid without signature", S_PLACE),
    ]

    GAP = 10 * mm
    lw = (CONTENT_W - GAP) * (1.6 / 2.6)
    rw = (CONTENT_W - GAP) * (1.0 / 2.6)

    t = Table([[left_items, right_items]], colWidths=[lw + GAP, rw])
    t.setStyle(TableStyle([
        ("VALIGN",        (0,0), (-1,-1), "BOTTOM"),
        ("TOPPADDING",    (0,0), (-1,-1), 0),
        ("BOTTOMPADDING", (0,0), (-1,-1), 0),
        ("LEFTPADDING",   (0,0), (-1,-1), 0),
        ("RIGHTPADDING",  (0,0), (0,0),   GAP),
        ("RIGHTPADDING",  (1,0), (1,0),   0),
    ]))
    return t


# ── Main entry point ──────────────────────────────────────────────────────────
def generate_certificate(
    parsed_cert: dict,
    odoo_data:   dict,
    logo_path:   str | None = None,
) -> bytes:
    buf   = BytesIO()
    coils = parsed_cert.get("coils") or []

    # ── Data assembly ──────────────────────────────────────────────────────────
    mill_cert_no = _blank(parsed_cert.get("cert_number"), "–")
    vs_cert_no   = f"VS-{mill_cert_no}"
    cert_date    = parsed_cert.get("cert_date") or ""
    issue_date   = _fmt_date(cert_date)
    mat_standard = _blank(parsed_cert.get("standard"), "–")
    insp_type    = _blank(parsed_cert.get("insp_type"), "3.1 / EN 10204")

    grade   = parsed_cert.get("grade")   or ""
    quality = parsed_cert.get("quality") or ""
    grade_quality = " — ".join(x for x in [grade, quality] if x) or "–"

    mfr_name = parsed_cert.get("manufacturer_name")    or ""
    mfr_addr = parsed_cert.get("manufacturer_address") or ""

    csg_name = odoo_data.get("buyer_name",    "") or ""
    csg_addr = odoo_data.get("buyer_address", "") or ""
    dst_name = odoo_data.get("dest_name",     "") or ""
    dst_addr = odoo_data.get("dest_address",  "") or ""

    # Order references
    # VS reference = deal_reference from Odoo (VSO-XXXX)
    # VS SO No.    = SO number from Odoo (S01512)
    # Customer order No. = buyer's PO to VS (Odoo client_order_ref)
    so_number    = odoo_data.get("so_number") or ""
    vs_reference = odoo_data.get("vs_reference") or ""   # deal_reference (VSO-XXXX)
    refs = [
        ("VS reference",       vs_reference),
        ("Customer order No.", odoo_data.get("customer_po") or ""),
        ("VS SO No.",          so_number),
        ("Mill order No.",     parsed_cert.get("mill_order_no") or ""),
        ("Contract No.",       ""),
        ("Dispatch note",      parsed_cert.get("dispatch_note") or odoo_data.get("delivery_note") or ""),
        ("Transport / freight car", ""),
        ("Marking",            ""),
    ]

    # Product specification
    spec = [
        ("Product description",   parsed_cert.get("material_type") or ""),
        ("Grade / quality",       grade_quality),
        ("Coating",               parsed_cert.get("coating") or ""),
        ("Surface group / class", parsed_cert.get("surface_quality") or ""),
        ("Delivery condition",    parsed_cert.get("delivery_condition") or ""),
        ("Dimensional tolerance", parsed_cert.get("dimensional_tolerance") or ""),
        ("Country of origin",     parsed_cert.get("country_of_origin") or ""),
        ("Steelmaking process",   parsed_cert.get("steelmaking_process") or ""),
    ]

    # Prefer explicit total; otherwise sum net weights; fall back to gross weights
    _net_sum   = sum(float(c.get("weight_kg")       or 0) for c in coils)
    _gross_sum = sum(float(c.get("gross_weight_kg") or 0) for c in coils)
    total_weight = (
        parsed_cert.get("total_weight_kg")
        or (_net_sum   if _net_sum   > 0 else None)
        or (_gross_sum if _gross_sum > 0 else None)
    )
    # Determine whether we're showing net or gross in the total row
    _has_net   = any(c.get("weight_kg")       for c in coils)
    _has_gross = any(c.get("gross_weight_kg") for c in coils)

    # ── Story ──────────────────────────────────────────────────────────────────
    story = []

    # Page 1 fixed sections — kept together
    story.append(KeepTogether([
        _meta_strip(vs_cert_no, mill_cert_no, issue_date, mat_standard, insp_type),
        Spacer(1, 5*mm),
        _parties(mfr_name, mfr_addr, csg_name, csg_addr, dst_name, dst_addr),
        Spacer(1, 5*mm),
        _refs_spec(refs, spec),
    ]))
    story.append(Spacer(1, 5*mm))

    # Items
    story.append(_sec_hdr("Delivery items & dimensions", "Dimensions in mm · weights in kg"))
    story.append(_items_table(coils, total_weight, _has_net, _has_gross))
    story.append(Spacer(1, 5*mm))

    # Mechanical
    mech_t = _mech_table(coils)
    if mech_t:
        story.append(_sec_hdr("Mechanical properties",
                              "Yield / Tensile in N/mm²  ·  Elongation in %"))
        story.append(mech_t)
        story.append(Spacer(1, 5*mm))

    # Chemical
    chem_t = _chem_table(coils)
    if chem_t:
        story.append(_sec_hdr("Chemical composition", "Heat analysis · % by mass"))
        story.append(chem_t)
        story.append(Spacer(1, 5*mm))

    # Declaration
    story.append(_declaration(parsed_cert, mill_cert_no, vs_cert_no, issue_date))

    # ── Document ───────────────────────────────────────────────────────────────
    doc = BaseDocTemplate(
        buf,
        pagesize=(PAGE_W, PAGE_H),
        leftMargin=ML, rightMargin=MR,
        topMargin=MAST_H + 2*mm,
        bottomMargin=FOOT_H + 2*mm,
    )
    frame = Frame(ML, FRAME_Y, CONTENT_W, FRAME_H,
                  leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    doc.addPageTemplates([PageTemplate(
        id="main", frames=[frame],
        onPage=lambda c, d: _draw_page(c, d, logo_path, vs_cert_no, mill_cert_no),
    )])
    doc.build(story)
    return buf.getvalue()
