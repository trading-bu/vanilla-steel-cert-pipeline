"""
Vanilla Steel — Neutralised Inspection Certificate (EN 10204 3.1)
Generator v3 — compliant with neutralisation_Instructions_v2.md (v6 design / v7 language rule).

Structure (sections render ONLY if source data exists; numbering is dynamic):
  Header  (every page)  — VS logo left · "INSPECTION CERTIFICATE — Copy / EN 10204 – Type 3.1" right
  Info box (page 1)     — Certificate Type · Country of Destination · Issue Date · Sales Order · Material Standard
  1  Product Details            (KV grid, source data only)
  2  Shipped Positions          (Item · Pack Nr · Coil No. · Cast No. · Grade · Width · Thickness ·
                                 Qty · Gross Wt. · [Net Wt. if in source] · VS Article · totals row)
  3  Chemical Composition       (dynamic element set, verbatim values, units in column headers)
  4  Mechanical Test Results    (dynamic columns, verbatim yield label, decoder legend note)
  5  Coating & Surface Tests    (from surface_tests)
  6  Remarks & Production Notes (verbatim bullets)
  —  extra_tests                (one generic section each — zero data loss)
  N  Certification              (fixed sentences · conditional ISO/IATF line · validity box ·
                                 NO signature block, NO verification code)
  Footer (every page)   — full VS legal line · Page X of Y

Neutralisation guarantees:
  · No manufacturer name/address, no mill cert number, no mill/contract/dispatch references anywhere.
  · Only Odoo additions: VS Article (per row) and Sales Order (info box).
  · Values rendered verbatim (strings pass through untouched — trailing zeros survive).
  · Nothing invented: absent fields are dashes or the whole row/section is omitted.

Language (v7): pass language="de" ONLY when source cert is German AND buyer country is Germany.

Fonts: tries Hanken Grotesk TTFs from `font_dir` (HankenGrotesk-Regular.ttf / -Bold.ttf,
extracted per §1.1 of the spec); falls back to Helvetica.
"""
from io import BytesIO
import base64
import os
import re
from datetime import datetime
from xml.sax.saxutils import escape as _xml_escape

from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_RIGHT, TA_CENTER
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (
    BaseDocTemplate, Frame, PageTemplate,
    Paragraph, Table, TableStyle, Spacer, KeepTogether, CondPageBreak,
)
from reportlab.pdfgen import canvas as pdfcanvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# ── Embedded VS logo (masthead, JPEG 364×84) ─────────────────────────────────
_LOGO_B64 = (
    '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'
)

# ── Palette (v6: navy rules only, no fills) ──────────────────────────────────
NAVY         = colors.HexColor("#000831")
TEXT_DARK    = colors.HexColor("#11151F")
TEXT_MED     = colors.HexColor("#4B515E")
TEXT_MED2    = colors.HexColor("#5B6170")
TEXT_MUTED2  = colors.HexColor("#6B7280")
TEXT_MUTED   = colors.HexColor("#7B8290")
LABEL_C      = colors.HexColor("#9AA0AC")
BORDER_LIGHT = colors.HexColor("#EEF0F3")
BORDER_MED   = colors.HexColor("#E6E8EE")

# ── Page geometry ────────────────────────────────────────────────────────────
PAGE_W, PAGE_H = landscape(A4)
ML = MR       = 14 * mm
MAST_H        = 20 * mm
FOOT_H        = 12 * mm
CONTENT_W     = PAGE_W - ML - MR
FRAME_Y       = FOOT_H + 2 * mm
FRAME_H       = PAGE_H - MAST_H - FOOT_H - 4 * mm

FOOTER_LEGAL = (
    "Vanilla Steel GmbH · Schönhauser Allee 36, 10435 Berlin, Germany · "
    "VAT DE332534899 · HRB 218619 B (Amtsgericht Charlottenburg) · "
    "Managing Directors: Clifford Ondara, Simon Zühlke · "
    "support@vanillasteel.com · www.vanillasteel.com"
)

# ── Fixed sentences (EN / DE per v7 language rule) ───────────────────────────
STRINGS = {
    "en": {
        "title":        "INSPECTION CERTIFICATE — Copy",
        "subtitle":     "EN 10204 – Type 3.1",
        "cert_type":    "Certificate Type",
        "dest":         "Country of Destination",
        "issue":        "Issue Date",
        "so":           "Sales Order",
        "standard":     "Material Standard",
        "s_product":    "Product Details",
        "s_positions":  "Shipped Positions",
        "s_chem":       "Chemical Composition",
        "s_mech":       "Mechanical Test Results – Tensile Test",
        "s_surface":    "Coating & Surface Test Results",
        "s_remarks":    "Remarks & Production Notes",
        "s_cert":       "Certification",
        "total":        "Total",
        "coil":         "coil",
        "coils":        "coils",
        "page":         "Page {i} of {n}",
        "cert_open": "This inspection certificate has been issued in accordance with EN 10204 Type 3.1.",
        "cert_close": ("All test results stated herein are based on authenticated "
                       "records from the original manufacturer's inspection data."),
        "iso":  ("The Quality Management System applied to the manufacturing of the goods "
                 "described above is certified to meet the requirements of ISO 9001 and "
                 "IATF 16949."),
        "valid": "This certificate is valid without signature.",
        "chem_rowlbl_heat": "Heat / Cast",
        "chem_min": "Specified min.",
        "chem_max_row": "Specified max.",
        "dims_note": ("All positions share the same nominal cross-section "
                      "(thickness × width) as stated in Product Details."),
        "legend": ("Specimen condition (Cond.): F = Non-Aged, V = Aged, N = Normalised  |  "
                   "Direction (Dir.): L = Longitudinal (0°), S = 45°, D = Transverse (90°)"),
        "chem_meas": "Heat analysis (meas)",
        "chem_max":  "Product norm limits (max)",
        "chem_units_hdr": "Units as per column headers",
        "pct_mass": "% by mass",
        "weights_kg": "Weights in kg",
        "dims_mm": "Dimensions in mm · weights in kg",
    },
    "de": {
        "title":        "ABNAHMEPRÜFZEUGNIS — Kopie",
        "subtitle":     "EN 10204 – Typ 3.1",
        "cert_type":    "Zeugnisart",
        "dest":         "Bestimmungsland",
        "issue":        "Ausstellungsdatum",
        "so":           "Auftragsnummer",
        "standard":     "Werkstoffnorm",
        "s_product":    "Produktangaben",
        "s_positions":  "Gelieferte Positionen",
        "s_chem":       "Chemische Zusammensetzung",
        "s_mech":       "Mechanische Prüfergebnisse – Zugversuch",
        "s_surface":    "Überzugs- & Oberflächenprüfungen",
        "s_remarks":    "Bemerkungen & Fertigungshinweise",
        "s_cert":       "Bescheinigung",
        "total":        "Summe",
        "coil":         "Coil",
        "coils":        "Coils",
        "page":         "Seite {i} von {n}",
        "cert_open": "Diese Prüfbescheinigung wurde gemäß EN 10204 Typ 3.1 ausgestellt.",
        "cert_close": ("Alle hier aufgeführten Prüfergebnisse beruhen auf beglaubigten "
                       "Aufzeichnungen der Prüfdaten des ursprünglichen Herstellers."),
        "iso":  ("Das bei der Herstellung der oben beschriebenen Erzeugnisse angewandte "
                 "Qualitätsmanagementsystem ist nach ISO 9001 und IATF 16949 zertifiziert."),
        "valid": "Dieses Zeugnis ist ohne Unterschrift gültig.",
        "chem_rowlbl_heat": "Schmelze / Charge",
        "chem_min": "Grenzwert min.",
        "chem_max_row": "Grenzwert max.",
        "dims_note": ("Alle Positionen haben den gleichen Nennquerschnitt "
                      "(Dicke × Breite) wie unter Produktangaben angegeben."),
        "legend": ("Probenzustand (Cond.): F = nicht gealtert, V = gealtert, N = normalisiert  |  "
                   "Richtung (Dir.): L = längs (0°), S = 45°, D = quer (90°)"),
        "chem_meas": "Schmelzenanalyse (gem.)",
        "chem_max":  "Normgrenzwerte (max)",
        "chem_units_hdr": "Einheiten gemäß Spaltenüberschrift",
        "pct_mass": "% Massenanteil",
        "weights_kg": "Gewichte in kg",
        "dims_mm": "Abmessungen in mm · Gewichte in kg",
    },
}

# ── Fonts ────────────────────────────────────────────────────────────────────
FONT_R = "Helvetica"
FONT_B = "Helvetica-Bold"

def _register_fonts(font_dir: str | None) -> None:
    """Register Hanken Grotesk if TTFs are available; otherwise keep Helvetica."""
    global FONT_R, FONT_B
    candidates = [font_dir] if font_dir else []
    candidates += [os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts"),
                   "fonts"]
    for d in candidates:
        if not d:
            continue
        reg  = os.path.join(d, "HankenGrotesk-Regular.ttf")
        bold = os.path.join(d, "HankenGrotesk-Bold.ttf")
        if os.path.exists(reg) and os.path.exists(bold):
            try:
                pdfmetrics.registerFont(TTFont("HankenGrotesk", reg))
                pdfmetrics.registerFont(TTFont("HankenGrotesk-Bold", bold))
                FONT_R, FONT_B = "HankenGrotesk", "HankenGrotesk-Bold"
                return
            except Exception:
                pass  # fall through to Helvetica


# ── Styles (built after font registration) ───────────────────────────────────
S: dict = {}

# Render-time locale (set per call in generate_certificate)
_LANG = "en"
_NUMFMT = ""

def _build_styles() -> None:
    def _s(name, **kw):
        b = dict(fontName=FONT_R, fontSize=8.25, leading=10.5,
                 textColor=TEXT_DARK, spaceAfter=0, spaceBefore=0)
        b.update(kw)
        return ParagraphStyle(name, **b)

    S.clear()
    S.update(
        LABEL   =_s("lbl",  fontSize=6.75, textColor=LABEL_C,   leading=9),
        META_V  =_s("mtv",  fontSize=9.5,  fontName=FONT_B,     leading=12),
        SEC     =_s("sec",  fontSize=9.5,  fontName=FONT_B,     leading=12),
        SEC_R   =_s("scr",  fontSize=7.75, textColor=LABEL_C,   leading=10, alignment=TA_RIGHT),
        KV_K    =_s("kvk",  fontSize=8.5,  textColor=TEXT_MUTED2, leading=11),
        KV_V    =_s("kvv",  fontSize=8.5,  fontName=FONT_B,     leading=11),
        TH      =_s("thd",  fontSize=8,    textColor=TEXT_MED2, fontName=FONT_B, leading=10),
        TH_C    =_s("thc",  fontSize=8,    textColor=TEXT_MED2, fontName=FONT_B, leading=10, alignment=TA_CENTER),
        TH_R    =_s("thr",  fontSize=8,    textColor=TEXT_MED2, fontName=FONT_B, leading=10, alignment=TA_RIGHT),
        TD_L    =_s("tdl",  fontSize=8.25, leading=10),
        TD_LB   =_s("tdlb", fontSize=8.25, fontName=FONT_B,     leading=10),
        TD_R    =_s("tdr",  fontSize=8.25, leading=10, alignment=TA_RIGHT),
        TD_RB   =_s("tdrb", fontSize=8.25, fontName=FONT_B,     leading=10, alignment=TA_RIGHT),
        TD_C    =_s("tdc",  fontSize=8.25, textColor=TEXT_MED,  leading=10, alignment=TA_CENTER),
        TOT_L   =_s("totl", fontSize=8.25, fontName=FONT_B,     leading=10),
        TOT_R   =_s("totr", fontSize=8.25, fontName=FONT_B,     leading=10, alignment=TA_RIGHT),
        NOTE    =_s("nte",  fontSize=7.5,  textColor=TEXT_MUTED2, leading=10),
        REMARK  =_s("rmk",  fontSize=8.5,  textColor=TEXT_MED,  leading=13),
        DECL    =_s("dcl",  fontSize=8.5,  textColor=TEXT_MED,  leading=13),
        VALID   =_s("vld",  fontSize=9,    fontName=FONT_B,     leading=12, alignment=TA_CENTER),
    )


# ── Numbered canvas (accurate Page X of Y in one build) ──────────────────────
class _NumberedCanvas(pdfcanvas.Canvas):
    page_tpl = "Page {i} of {n}"

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._saved = []

    def showPage(self):
        self._saved.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        n = len(self._saved)
        for i, st in enumerate(self._saved, 1):
            self.__dict__.update(st)
            self.setFont(FONT_R, 7)
            self.setFillColor(LABEL_C)
            self.drawRightString(PAGE_W - MR, FOOT_H * 0.45,
                                 self.page_tpl.format(i=i, n=n))
            pdfcanvas.Canvas.showPage(self)
        pdfcanvas.Canvas.save(self)


# ── Page decoration ──────────────────────────────────────────────────────────
_logo_reader = None

def _get_logo() -> ImageReader | None:
    global _logo_reader
    if _logo_reader is None:
        try:
            _logo_reader = ImageReader(BytesIO(base64.b64decode(_LOGO_B64)))
        except Exception:
            _logo_reader = False
    return _logo_reader or None

def _draw_page(canvas, doc, L):
    canvas.saveState()
    top = PAGE_H - 4 * mm

    logo = _get_logo()
    if logo is not None:
        iw, ih = logo.getSize()               # 364 × 84
        h = 7.5 * mm
        w = h * iw / ih
        canvas.drawImage(logo, ML, top - h - 1.5 * mm, width=w, height=h,
                         preserveAspectRatio=True)
    else:  # never silent — visible fallback wordmark
        canvas.setFont(FONT_B, 12)
        canvas.setFillColor(NAVY)
        canvas.drawString(ML, top - 8 * mm, "VANILLA STEEL")

    canvas.setFont(FONT_B, 12.5)
    canvas.setFillColor(TEXT_DARK)
    canvas.drawRightString(PAGE_W - MR, top - 5 * mm, L["title"])
    canvas.setFont(FONT_R, 8)
    canvas.setFillColor(TEXT_MUTED2)
    canvas.drawRightString(PAGE_W - MR, top - 10 * mm, L["subtitle"])

    canvas.setStrokeColor(NAVY)
    canvas.setLineWidth(2)
    canvas.line(ML, PAGE_H - MAST_H, PAGE_W - MR, PAGE_H - MAST_H)

    canvas.setStrokeColor(BORDER_MED)
    canvas.setLineWidth(0.4)
    canvas.line(ML, FOOT_H, PAGE_W - MR, FOOT_H)

    canvas.setFont(FONT_R, 6.3)
    canvas.setFillColor(LABEL_C)
    canvas.drawString(ML, FOOT_H * 0.45, FOOTER_LEGAL)
    canvas.restoreState()


# ── Helpers ──────────────────────────────────────────────────────────────────
def _esc(v) -> str:
    return _xml_escape(str(v))

def _blank(v, fb="–") -> str:
    if v is None or (isinstance(v, str) and not v.strip()):
        return fb
    return str(v)

def _has(v) -> bool:
    return not (v is None or (isinstance(v, str) and not v.strip())
                or (isinstance(v, (list, dict)) and not v))

def _to_float(v) -> float:
    try:
        return float(str(v).replace(",", ".")) if _has(v) else 0.0
    except (TypeError, ValueError):
        return 0.0

def _fmt_int_kg(v) -> str:
    """Group thousands for weight totals only; per-row values stay verbatim.
    Locale-aware: English '2,170' → German '2.170'."""
    try:
        f = _to_float(v)
        s = f"{f:,.0f}" if f == int(f) else f"{f:,.1f}"
        if _LANG == "de":  # swap , and . for German number format
            s = s.replace(",", "\x00").replace(".", ",").replace("\x00", ".")
        return s
    except Exception:
        return _blank(v)

def _fmt_date(d) -> str:
    if not _has(d):
        return "–"
    try:
        dt = datetime.strptime(str(d).strip(), "%Y-%m-%d")
        return dt.strftime("%d.%m.%Y")
    except Exception:
        return str(d)

def _p(text, sty=None, esc=True):
    if text is None:
        text = "–"
    return Paragraph(_esc(text) if esc else str(text), sty or S["TD_L"])

_SCALE_RE = re.compile(r"[x×]\s*10\s*(?:\^|-|⁻)?\s*(-?\d+)", re.IGNORECASE)

# ── Identifier join + VSI resolution ─────────────────────────────────────────
_ID_STRIP_RE = re.compile(r"[\s\-_./]+")

def _norm_id(v) -> str:
    if v is None or v is False:
        return ""
    s = _ID_STRIP_RE.sub("", str(v)).upper()
    return (s.lstrip("0") or ("0" if s else ""))

def _resolve_vsi(coil: dict, vs_articles: dict) -> str:
    """Prefer the authoritative match set by the API join; else look the coil's
    own identifiers up in the (normalised-keyed) map; else single-article
    wildcard; else en-dash. Never silently borrows another row's VSI."""
    mv = str(coil.get("_matched_vsi") or "").strip()
    if mv and mv != "–":
        return mv
    if isinstance(vs_articles, dict):
        for f in ("coil_no", "cast_no", "pack_no", "serial"):
            raw = str(coil.get(f) or "").strip()
            if raw and raw in vs_articles:
                return vs_articles[raw]
            k = _norm_id(coil.get(f))
            if k and k in vs_articles:
                return vs_articles[k]
        if vs_articles.get("*"):
            return vs_articles["*"]
    return "–"

# ── Number localisation (comma for de, dot for en) ───────────────────────────
def _localise_num(v, number_format: str, language: str) -> str:
    """Re-emit a verbatim numeric string in the target locale WITHOUT changing
    any significant digit or trailing zero. Non-numeric text passes through."""
    if v is None:
        return "–"
    s = str(v).strip()
    if not s:
        return "–"
    # split a possible leading symbol (<, ≤, >, ≥) and trailing text
    m = re.match(r"^\s*([<≤>≥]?\s*)(-?[\d.,]+)(.*)$", s)
    if not m:
        return s
    pre, num, post = m.group(1), m.group(2), m.group(3)
    # interpret the number using the SOURCE convention, decimal part only
    if number_format == "comma":
        intpart, _, dec = num.replace(".", "").partition(",")
    elif number_format == "dot":
        intpart, _, dec = num.replace(",", "").partition(".")
    else:  # unknown: infer from the last separator present
        if "," in num and "." in num:
            last = max(num.rfind(","), num.rfind("."))
            dec = num[last + 1:]
            intpart = re.sub(r"[.,]", "", num[:last])
        elif "," in num:
            intpart, _, dec = num.partition(",")
        else:
            intpart, _, dec = num.partition(".")
    sep = "," if language == "de" else "."
    out = intpart + (sep + dec if dec != "" else "")
    return (pre.replace(" ", "") + out + post)

# ── Certificate-type display normaliser (never render raw supplier German) ───
def _norm_cert_type(v) -> str:
    s = str(v or "")
    if "2.2" in s:
        return "EN 10204 – Type 2.2"
    if "3.2" in s:
        return "EN 10204 – Type 3.2"
    if "3.1" in s:
        return "EN 10204 – Type 3.1"
    return s.strip()

# ── Neutralisation backstop: drop any identity that slipped past extraction ──
_ADMIN_DENY_RE = re.compile(
    r"(kunden[\-\s]?nummer|customer\s*(no|number)|abnahmebeauftragt|inspector|"
    r"lieferschein|delivery\s*note|dispatch|auftrags?[\-\s]?nr|order\s*no|"
    r"bestell|material[\-\s]?nummer|artikel[\-\s]?nr|besteller|"
    r"ust[\-\s]?id|vat|steuer|iban|swift|bic|hrb|hrg|registergericht|"
    r"gesch[aä]ftsf[uü]hrer|managing\s*director|bank)",
    re.IGNORECASE,
)

def _is_admin_leak(text: str) -> bool:
    return bool(_ADMIN_DENY_RE.search(str(text or "")))

def _unit_markup(header: str, elem: str) -> str:
    """'C x10-3 %' → 'C<br/>×10<super>-3</super> %' (escaped, safe)."""
    rest = header
    for pref in (elem, elem.upper(), elem.lower()):
        if rest.startswith(pref):
            rest = rest[len(pref):].strip()
            break
    if not rest:
        return f"<b>{_esc(elem)}</b>"
    m = _SCALE_RE.search(rest)
    if m:
        exp = m.group(1).lstrip("-")
        tail = rest[m.end():].strip()
        unit = f"×10<super>-{exp}</super>"
        if tail:
            unit += f" {_esc(tail)}"
        return f"<b>{_esc(elem)}</b><br/><font size=6.5>{unit}</font>"
    return f"<b>{_esc(elem)}</b><br/><font size=6.5>{_esc(rest)}</font>"


# ── Section header (v6: title + 1.5px navy underline, right-aligned unit note) ─
def _sec_hdr(num, title, right_txt="", width=None):
    w = width or CONTENT_W
    label = f"{num}  {title}" if num else title
    t = Table([[_p(label, S["SEC"]), _p(right_txt, S["SEC_R"])]],
              colWidths=[w * 0.62, w * 0.38])
    t.setStyle(TableStyle([
        ("LINEBELOW",     (0, 0), (-1, -1), 1.5, NAVY),
        ("TOPPADDING",    (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING",   (0, 0), (-1, -1), 0),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 0),
        ("VALIGN",        (0, 0), (-1, -1), "BOTTOM"),
    ]))
    return t


# ── Info box (page 1): Cert Type · Destination · Issue Date · SO · Standard ──
def _info_box(L, cert_type, dest_country, issue_date, so_number, standard):
    fields = [
        (L["cert_type"], cert_type),
        (L["dest"],      dest_country),
        (L["issue"],     issue_date),
        (L["so"],        so_number),      # always present, always filled
        (L["standard"],  standard),
    ]
    cw = CONTENT_W / 5
    t = Table(
        [[_p(k.upper(), S["LABEL"]) for k, _ in fields],
         [_p(_blank(v), S["META_V"]) for _, v in fields]],
        colWidths=[cw] * 5,
    )
    t.setStyle(TableStyle([
        ("TOPPADDING",    (0, 0), (-1, 0),  0),
        ("TOPPADDING",    (0, 1), (-1, 1),  1),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("LEFTPADDING",   (0, 0), (-1, -1), 0),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 6),
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW",     (0, -1), (-1, -1), 0.5, BORDER_MED),
    ]))
    return t


# ── Section: Product Details (KV grid, only rows with source data) ───────────
def _product_details(pairs):
    pairs = [(k, v) for k, v in pairs if _has(v)]
    if not pairs:
        return None
    half = (len(pairs) + 1) // 2
    cols = [pairs[:half], pairs[half:]]
    GAP = 9 * mm
    cw = (CONTENT_W - GAP) / 2

    def kv(items, col_w):
        rows = [[_p(k, S["KV_K"]), _p(_blank(v), S["KV_V"])] for k, v in items]
        t = Table(rows, colWidths=[col_w * 0.42, col_w * 0.58])
        sty = [
            ("TOPPADDING",    (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LEFTPADDING",   (0, 0), (-1, -1), 0),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 0),
            ("VALIGN",        (0, 0), (-1, -1), "TOP"),
        ]
        for i in range(len(rows) - 1):
            sty.append(("LINEBELOW", (0, i), (-1, i), 0.4, BORDER_LIGHT))
        t.setStyle(TableStyle(sty))
        return t

    t = Table([[kv(cols[0], cw), Spacer(GAP, 1), kv(cols[1], cw) if cols[1] else ""]],
              colWidths=[cw, GAP, cw])
    t.setStyle(TableStyle([
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING",    (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ("LEFTPADDING",   (0, 0), (-1, -1), 0),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 0),
    ]))
    return t


# ── Section: Shipped Positions ────────────────────────────────────────────────
def _positions_table(L, coils, vs_articles, show_net, show_pack, show_serial,
                     extra_cols):
    # column plan: (key, header, weight, style_head, style_cell)
    plan = [("item", "Item", 0.7, S["TH"], S["TD_L"])]
    if show_pack:
        plan.append(("pack",   "Pack Nr",         1.2, S["TH"],   S["TD_L"]))
    plan.append(("coil",       "Coil No.",        1.7, S["TH"],   S["TD_LB"]))
    plan.append(("cast",       "Cast No. (Heat)", 1.5, S["TH"],   S["TD_L"]))
    if show_serial:
        plan.append(("serial", "Serial",          0.9, S["TH_C"], S["TD_C"]))
    plan.append(("grade",      "Grade",           2.7, S["TH"],   S["TD_L"]))
    plan.append(("width",      "Width (mm)",      1.0, S["TH_R"], S["TD_R"]))
    plan.append(("thick",      "Thickness (mm)",  1.2, S["TH_R"], S["TD_R"]))
    plan.append(("qty",        "Qty",             0.55, S["TH_R"], S["TD_R"]))
    if show_net:
        plan.append(("net",    "Net Wt. (kg)",    1.25, S["TH_R"], S["TD_R"]))
    plan.append(("gross",      "Gross Wt. (kg)",  1.3, S["TH_R"], S["TD_RB"]))
    for ec in extra_cols:
        plan.append((("x", ec), ec,               1.2, S["TH"],   S["TD_L"]))
    plan.append(("vsa",        "VS Article",      1.7, S["TH"],   S["TD_LB"]))

    tw = sum(p[2] for p in plan)
    cw = [CONTENT_W * p[2] / tw for p in plan]

    def cell(key, c, i):
        if key == "item":   return _p(str(i), S["TD_L"])
        if key == "pack":   return _p(_blank(c.get("pack_no")), S["TD_L"])
        if key == "coil":   return _p(_blank(c.get("coil_no")), S["TD_LB"])
        if key == "cast":   return _p(_blank(c.get("cast_no")), S["TD_L"])
        if key == "serial": return _p(_blank(c.get("serial") or c.get("serial_no")), S["TD_C"])
        if key == "grade":  return _p(_blank(c.get("grade")), S["TD_L"])
        if key == "width":  return _p(_blank(c.get("width_mm")), S["TD_R"])
        if key == "thick":  return _p(_blank(c.get("thickness_mm")), S["TD_R"])
        if key == "qty":    return _p(_blank(c.get("qty"), "–"), S["TD_R"])
        if key == "net":    return _p(_blank(c.get("weight_kg")), S["TD_R"])
        if key == "gross":  return _p(_blank(c.get("gross_weight_kg")), S["TD_RB"])
        if key == "vsa":
            va = _resolve_vsi(c, vs_articles)
            return _p(va, S["TD_LB"])
        if isinstance(key, tuple):  # extra column
            return _p(_blank((c.get("extra") or {}).get(key[1])), S["TD_L"])
        return _p("–")

    rows = [[_p(p[1], p[3], esc=True) for p in plan]]
    for i, c in enumerate(coils, 1):
        rows.append([cell(p[0], c, i) for p in plan])

    # totals
    net_tot   = sum(_to_float(c.get("weight_kg"))       for c in coils)
    gross_tot = sum(_to_float(c.get("gross_weight_kg")) for c in coils)
    qty_tot   = sum(int(_to_float(c.get("qty") or 0))   for c in coils)
    total_row = []
    for p in plan:
        k = p[0]
        if k == "item":
            unit = L['coil'] if len(coils) == 1 else L['coils']
            total_row.append(_p(f"{L['total']} — {len(coils)} {unit}", S["TOT_L"]))
        elif k == "qty":
            total_row.append(_p(str(qty_tot) if qty_tot else "–", S["TOT_R"]))
        elif k == "net":
            total_row.append(_p(_fmt_int_kg(net_tot) if net_tot else "", S["TOT_R"]))
        elif k == "gross":
            total_row.append(_p(_fmt_int_kg(gross_tot) if gross_tot else "", S["TOT_R"]))
        else:
            total_row.append("")
    rows.append(total_row)

    # span "Total" across leading identity columns (up to grade)
    span_end = next(i for i, p in enumerate(plan) if p[0] == "grade")
    t = Table(rows, colWidths=cw, repeatRows=1)
    t.setStyle(TableStyle([
        ("SPAN",          (0, -1), (span_end, -1)),
        ("LINEBELOW",     (0, 0),  (-1, 0),  1.5, NAVY),
        ("LINEABOVE",     (0, -1), (-1, -1), 1.5, NAVY),
        ("LINEBELOW",     (0, 1),  (-1, -2), 0.5, BORDER_LIGHT),
        ("TOPPADDING",    (0, 0),  (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0),  (-1, -1), 5),
        ("LEFTPADDING",   (0, 0),  (-1, -1), 4),
        ("RIGHTPADDING",  (0, 0),  (-1, -1), 4),
        ("VALIGN",        (0, 0),  (-1, -1), "MIDDLE"),
    ]))
    return t


# ── Section: Chemical Composition ─────────────────────────────────────────────
def _coil_chem(c: dict):
    """Return (measured{elem:str}, limits{elem:{'min','max'}}) for one coil,
    tolerant of both the new shape (chemicals=str, chemical_limits=dict) and the
    old nested shape (chemicals[elem]={'ist','min','max'})."""
    measured, limits = {}, {}
    chem = c.get("chemicals") or {}
    for e, v in chem.items():
        if isinstance(v, dict):  # legacy nested — split it
            meas = v.get("ist") or v.get("meas") or v.get("measured") or v.get("value")
            if _has(meas):
                measured[e] = meas
            lo, hi = v.get("min"), v.get("max")
            if _has(lo) or _has(hi):
                limits[e] = {"min": lo or "", "max": hi or ""}
        elif _has(v):
            measured[e] = v
    for e, lim in (c.get("chemical_limits") or {}).items():
        if isinstance(lim, dict) and (_has(lim.get("min")) or _has(lim.get("max"))):
            limits.setdefault(e, {"min": lim.get("min") or "", "max": lim.get("max") or ""})
    return measured, limits


def _chem_table(coils, chem_headers, L, number_format="", language="en"):
    # one measured row per unique heat; plus merged spec min/max rows if present
    seen: dict = {}
    limits_by_heat: dict = {}
    for c in coils:
        heat = str(c.get("cast_no") or c.get("coil_no") or "")
        meas, lim = _coil_chem(c)
        if heat and heat not in seen and (meas or lim):
            seen[heat] = meas
            limits_by_heat[heat] = lim
    if not seen and not any(limits_by_heat.values()):
        return None, []

    # dynamic element order: header order first, then first-seen measured, then limits
    order: list = []
    def _add(e):
        if e not in order:
            order.append(e)
    if chem_headers:
        for e in chem_headers:
            if any(_has(m.get(e)) for m in seen.values()) or \
               any(e in lm for lm in limits_by_heat.values()):
                _add(e)
    for m in seen.values():
        for e in m:
            _add(e)
    for lm in limits_by_heat.values():
        for e in lm:
            _add(e)
    if not order:
        return None, []

    # merged spec limits across heats (first non-empty per element)
    merged_min, merged_max = {}, {}
    for lm in limits_by_heat.values():
        for e, b in lm.items():
            if e not in merged_min and _has(b.get("min")):
                merged_min[e] = b["min"]
            if e not in merged_max and _has(b.get("max")):
                merged_max[e] = b["max"]

    heads = []
    for e in order:
        hdr = (chem_headers or {}).get(e)
        if hdr:
            heads.append(_p(_unit_markup(str(hdr), e), S["TH_R"], esc=False))
        else:
            heads.append(_p(f"<b>{_esc(e)}</b><br/><font size=6.5>%</font>", S["TH_R"], esc=False))

    cw_heat = CONTENT_W * max(0.10, min(0.16, 1.9 / (len(order) + 2)))
    cw_elem = (CONTENT_W - cw_heat) / len(order)

    def _cell(v):
        return _p(_localise_num(v, number_format, language) if _has(v) else "–", S["TD_R"])

    rows = [[_p(L.get("chem_rowlbl_heat", "Heat / Cast"), S["TH"])] + heads]
    for heat, chems in seen.items():
        rows.append([_p(heat, S["TD_LB"])] + [_cell(chems.get(e)) for e in order])
    if merged_min:
        rows.append([_p(L.get("chem_min", "Specified min."), S["TD_L"])]
                    + [_cell(merged_min.get(e)) for e in order])
    if merged_max:
        rows.append([_p(L.get("chem_max_row", "Specified max."), S["TD_L"])]
                    + [_cell(merged_max.get(e)) for e in order])

    t = Table(rows, colWidths=[cw_heat] + [cw_elem] * len(order), repeatRows=1)
    t.setStyle(TableStyle([
        ("LINEBELOW",     (0, 0), (-1, 0),  1.5, NAVY),
        ("LINEBELOW",     (0, 1), (-1, -1), 0.5, BORDER_LIGHT),
        ("TOPPADDING",    (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING",   (0, 0), (-1, -1), 3),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 3),
        ("VALIGN",        (0, 0), (-1, -1), "BOTTOM"),
    ]))
    return t, order


# ── Section: Mechanical Test Results ──────────────────────────────────────────
def _mech_rows(coils):
    """Flatten (coil, mech-entry) pairs; supports dict (old) and list (new)."""
    out = []
    for c in coils:
        m = c.get("mechanical")
        if isinstance(m, dict) and m:
            out.append((c, m))
        elif isinstance(m, list):
            for e in m:
                if isinstance(e, dict) and e:
                    out.append((c, e))
    return out

def _mech_table(L, coils, remarks, number_format="", language="en"):
    pairs = _mech_rows(coils)
    if not pairs:
        return None, None

    def loc(v):
        return _localise_num(v, number_format, language) if _has(v) else "–"

    def yl(m):  # yield label verbatim from source
        return str(m.get("yield_label") or "").strip()

    labels = {yl(m) for _, m in pairs if yl(m)}
    yield_hdr = " / ".join(sorted(labels)) if labels else "Yield"
    gauges = {str(m.get("a_gauge") or "").strip() for _, m in pairs if _has(m.get("a_gauge"))}
    a_hdr = " / ".join(sorted(gauges)) if gauges else "A"

    have_dir  = any(_has(m.get("dir"))  for _, m in pairs)
    have_cond = any(_has(m.get("cond")) for _, m in pairs)
    have_bend = any(_has(m.get("bend")) for _, m in pairs)
    have_r    = any(_has(m.get("r_value")) for _, m in pairs)
    have_n    = any(_has(m.get("n_value")) for _, m in pairs)
    extra_keys: list = []
    for _, m in pairs:
        for k in (m.get("other") or {}):
            if k not in extra_keys and _has((m.get("other") or {}).get(k)):
                extra_keys.append(k)

    UNIT = "(N/mm<super>2</super>)"
    plan = [("coil", "Coil / Pack No.", 1.9, S["TH"], S["TD_LB"], True)]
    if have_cond:
        plan.append(("cond", "Cond.", 0.7, S["TH_C"], S["TD_C"], True))
    if have_dir:
        plan.append(("dir", "Dir.", 0.7, S["TH_C"], S["TD_C"], True))
    plan.append(("yield", f"{yield_hdr} {UNIT}", 1.5, S["TH_R"], S["TD_RB"], False))
    plan.append(("rm",    f"Rm {UNIT}",          1.3, S["TH_R"], S["TD_RB"], False))
    plan.append(("a",     f"{a_hdr} (%)",        1.0, S["TH_R"], S["TD_R"],  True))
    if have_r:
        plan.append(("r", "r-value", 1.0, S["TH_R"], S["TD_R"], True))
    if have_n:
        plan.append(("n", "n-value", 1.0, S["TH_R"], S["TD_R"], True))
    if have_bend:
        plan.append(("bend", "Bend test", 1.0, S["TH_C"], S["TD_C"], True))
    for k in extra_keys:
        plan.append((("x", k), k, 1.1, S["TH_R"], S["TD_R"], True))

    tw = sum(p[2] for p in plan)
    cw = [CONTENT_W * p[2] / tw for p in plan]

    # header row (superscript markup where needed → esc=False)
    rows = [[_p(p[1], p[3], esc=p[5]) for p in plan]]
    for c, m in pairs:
        r = []
        for p in plan:
            k = p[0]
            if k == "coil":
                r.append(_p(_blank(c.get("coil_no") or c.get("cast_no")), S["TD_LB"]))
            elif k == "cond":
                r.append(_p(_blank(m.get("cond")), S["TD_C"]))
            elif k == "dir":
                r.append(_p(_blank(m.get("dir")), S["TD_C"]))
            elif k == "yield":
                r.append(_p(loc(m.get("yield_value") or m.get("rp02")), S["TD_RB"]))
            elif k == "rm":
                r.append(_p(loc(m.get("rm")), S["TD_RB"]))
            elif k == "a":
                r.append(_p(loc(m.get("a_pct")), S["TD_R"]))
            elif k == "r":
                r.append(_p(loc(m.get("r_value")), S["TD_R"]))
            elif k == "n":
                r.append(_p(loc(m.get("n_value")), S["TD_R"]))
            elif k == "bend":
                r.append(_p(_blank(m.get("bend")), S["TD_C"]))
            elif isinstance(k, tuple):
                r.append(_p(loc((m.get("other") or {}).get(k[1])), S["TD_R"]))
        rows.append(r)

    t = Table(rows, colWidths=cw, repeatRows=1)
    t.setStyle(TableStyle([
        ("LINEBELOW",     (0, 0), (-1, 0),  1.5, NAVY),
        ("LINEBELOW",     (0, 1), (-1, -1), 0.5, BORDER_LIGHT),
        ("TOPPADDING",    (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING",   (0, 0), (-1, -1), 4),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 4),
        ("VALIGN",        (0, 0), (-1, -1), "BOTTOM"),
    ]))

    # ── Dynamic decoder legend: only codes actually present ──
    dir_map = {"L": "Longitudinal", "T": "Transverse", "Q": "Transverse (quer)",
               "QUER": "Transverse (quer)", "S": "45°", "D": "Transverse (90°)",
               "0°": "Longitudinal (0°)", "90°": "Transverse (90°)", "45°": "45°"}
    cond_map = {"F": "Non-Aged", "V": "Aged", "N": "Normalised"}
    dirs_present  = {str(m.get("dir")).strip().upper()  for _, m in pairs if _has(m.get("dir"))}
    conds_present = {str(m.get("cond")).strip().upper() for _, m in pairs if _has(m.get("cond"))}
    parts = []
    if conds_present:
        cs = [f"{k} = {cond_map.get(k, k)}" for k in sorted(conds_present)]
        parts.append("Condition (Cond.): " + ", ".join(cs))
    if dirs_present:
        ds = [f"{k} = {dir_map.get(k, k.title())}" for k in sorted(dirs_present)]
        parts.append("Direction (Dir.): " + ", ".join(ds))

    # ── Specification limits note (merged, so we don't repeat per coil) ──
    lim_labels = {
        "yield_min": f"{yield_hdr} min", "yield_max": f"{yield_hdr} max",
        "rm_min": "Rm min", "rm_max": "Rm max",
        "a_min": f"{a_hdr} min", "a_max": f"{a_hdr} max",
        "r_min": "r min", "r_max": "r max", "n_min": "n min", "n_max": "n max",
    }
    merged_lim: dict = {}
    for _, m in pairs:
        for k, v in (m.get("limits") or {}).items():
            if _has(v) and k not in merged_lim:
                merged_lim[k] = v
    if merged_lim:
        segs = [f"{lim_labels.get(k, k)} {loc(v)}" for k, v in merged_lim.items()]
        parts.append("Specified limits — " + "; ".join(segs))

    # specimen dimensions from remarks, if present
    for rr in remarks or []:
        if re.search(r"L\s*[C0]\s*/\s*L0\s*/\s*B0|LC/L0/B0", str(rr), re.IGNORECASE):
            parts.append(str(rr).strip())
            break

    legend_txt = "  |  ".join(parts) if parts else ""
    return t, (_p(legend_txt, S["NOTE"]) if legend_txt else Spacer(1, 0.1))


# ── Section: Coating & Surface Tests ──────────────────────────────────────────
def _surface_table(surface_tests):
    rows_src = [s for s in (surface_tests or []) if isinstance(s, dict)]
    if not rows_src:
        return None
    have_coil = any(_has(s.get("coil_no")) for s in rows_src)
    have_side = any(_has(s.get("side"))    for s in rows_src)
    have_std  = any(_has(s.get("standard")) for s in rows_src)
    have_res  = any(_has(s.get("result"))  for s in rows_src)

    plan = [("test", "Test", 2.2, S["TH"], S["TD_LB"])]
    if have_std:
        plan.append(("std", "Standard", 1.4, S["TH"], S["TD_L"]))
    if have_coil:
        plan.append(("coil", "Coil / Pack No.", 1.6, S["TH"], S["TD_L"]))
    if have_side:
        plan.append(("side", "Side", 0.8, S["TH_C"], S["TD_C"]))
    plan.append(("vals", "Values", 3.0, S["TH"], S["TD_L"]))
    plan.append(("unit", "Unit", 0.9, S["TH"], S["TD_L"]))
    if have_res:
        plan.append(("res", "Result", 1.2, S["TH"], S["TD_L"]))

    tw = sum(p[2] for p in plan)
    cw = [CONTENT_W * p[2] / tw for p in plan]
    rows = [[_p(p[1], p[3]) for p in plan]]
    for s in rows_src:
        vals = s.get("values")
        vals_s = "  ·  ".join(str(v) for v in vals) if isinstance(vals, list) else _blank(vals)
        r = []
        for p in plan:
            k = p[0]
            if   k == "test": r.append(_p(_blank(s.get("test_name")), S["TD_LB"]))
            elif k == "std":  r.append(_p(_blank(s.get("standard")),  S["TD_L"]))
            elif k == "coil": r.append(_p(_blank(s.get("coil_no")),   S["TD_L"]))
            elif k == "side": r.append(_p(_blank(s.get("side")),      S["TD_C"]))
            elif k == "vals": r.append(_p(vals_s or "–",              S["TD_L"]))
            elif k == "unit": r.append(_p(_blank(s.get("unit")),      S["TD_L"]))
            elif k == "res":  r.append(_p(_blank(s.get("result")),    S["TD_L"]))
        rows.append(r)

    t = Table(rows, colWidths=cw, repeatRows=1)
    t.setStyle(TableStyle([
        ("LINEBELOW",     (0, 0), (-1, 0),  1.5, NAVY),
        ("LINEBELOW",     (0, 1), (-1, -1), 0.5, BORDER_LIGHT),
        ("TOPPADDING",    (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING",   (0, 0), (-1, -1), 4),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 4),
        ("VALIGN",        (0, 0), (-1, -1), "BOTTOM"),
    ]))
    return t


# ── Section: extra_tests (generic, zero data loss) ────────────────────────────
def _extra_test_flowables(entry):
    out = []
    data = entry.get("data") or {}
    if data:
        rows = [[_p(_blank(k), S["KV_K"]), _p(_blank(v), S["KV_V"])]
                for k, v in data.items()]
        t = Table(rows, colWidths=[CONTENT_W * 0.3, CONTENT_W * 0.7])
        sty = [("TOPPADDING", (0, 0), (-1, -1), 3),
               ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
               ("LEFTPADDING", (0, 0), (-1, -1), 0),
               ("RIGHTPADDING", (0, 0), (-1, -1), 0)]
        for i in range(len(rows) - 1):
            sty.append(("LINEBELOW", (0, i), (-1, i), 0.4, BORDER_LIGHT))
        t.setStyle(TableStyle(sty))
        out.append(t)
    if _has(entry.get("text")):
        out.append(_p(entry["text"], S["REMARK"]))
    return out


# ── Section: Remarks & Certification ──────────────────────────────────────────
def _remarks_flowables(remarks):
    out = []
    for r in remarks:
        if _has(r):
            out.append(_p(f"•  {_esc(str(r).strip())}", S["REMARK"], esc=False))
            out.append(Spacer(1, 1.2 * mm))
    return out

def _certification_flowables(L, include_iso):
    out = [_p(L["cert_open"], S["DECL"])]
    if include_iso:
        out += [Spacer(1, 2 * mm), _p(L["iso"], S["DECL"])]
    out += [Spacer(1, 2 * mm), _p(L["cert_close"], S["DECL"])]
    out.append(Spacer(1, 4 * mm))
    box = Table([[_p(L["valid"], S["VALID"])]], colWidths=[CONTENT_W * 0.45])
    box.setStyle(TableStyle([
        ("BOX",           (0, 0), (-1, -1), 1.2, NAVY),
        ("TOPPADDING",    (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING",   (0, 0), (-1, -1), 10),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 10),
    ]))
    wrap = Table([[box]], colWidths=[CONTENT_W])
    wrap.setStyle(TableStyle([
        ("ALIGN",         (0, 0), (-1, -1), "CENTER"),
        ("TOPPADDING",    (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ("LEFTPADDING",   (0, 0), (-1, -1), 0),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 0),
    ]))
    out.append(wrap)
    return out


# ── Main entry point ──────────────────────────────────────────────────────────
def generate_certificate(
    parsed_cert: dict,
    odoo_data:   dict,
    language:    str = "en",     # "de" ONLY if source cert German AND buyer in Germany (v7)
    font_dir:    str | None = None,
) -> bytes:
    """
    parsed_cert : extraction JSON (v2 schema; v1 fields tolerated)
    odoo_data   : {
        "so_number":   "S01512",                      # REQUIRED — header info box
        "buyer_country": "France",                    # Country of Destination
        "vs_articles": {"<coil_no>": "VSI-XXXXXXXX",  # per-coil mapping, or
                        "*": "VSI-XXXXXXXX"}          # one article for all rows
    }
    Coil scope is decided by the caller — this renderer draws every coil
    it is given, without any filtering.
    """
    _register_fonts(font_dir)
    _build_styles()
    L = STRINGS.get(language, STRINGS["en"])
    _NumberedCanvas.page_tpl = L["page"]

    global _LANG, _NUMFMT
    _LANG = language
    _NUMFMT = str(parsed_cert.get("number_format") or "")

    coils       = parsed_cert.get("coils") or []
    remarks_raw = parsed_cert.get("remarks")
    remarks     = ([str(r) for r in remarks_raw if _has(r)]
                   if isinstance(remarks_raw, list)
                   else ([str(remarks_raw)] if _has(remarks_raw) else []))
    # Neutralisation backstop: drop any remark that reads as administrative /
    # identity data (should already be in source_admin, but never leak it here).
    remarks = [r for r in remarks if not _is_admin_leak(r)]
    vs_articles = dict(odoo_data.get("vs_articles") or {})
    if not vs_articles and _has(odoo_data.get("vs_article")):
        vs_articles = {"*": str(odoo_data["vs_article"])}

    so_number    = _blank(odoo_data.get("so_number"), "")
    dest_country = _blank(odoo_data.get("buyer_country"), "–")
    cert_type    = _norm_cert_type(parsed_cert.get("cert_type") or parsed_cert.get("insp_type"))
    issue_date   = _fmt_date(parsed_cert.get("cert_date"))
    standard     = _blank(parsed_cert.get("standard"))
    if not so_number:
        raise ValueError("odoo_data['so_number'] is required — the Sales Order "
                         "field must never be blank on a neutralised certificate.")

    # ── Product Details (source data only; rows without data are dropped) ──
    grade_full = parsed_cert.get("grade_full") or parsed_cert.get("grade") or ""
    dims = ""
    thicks = {str(c.get("thickness_mm")) for c in coils if _has(c.get("thickness_mm"))}
    widths = {str(c.get("width_mm"))     for c in coils if _has(c.get("width_mm"))}
    uniform_dims = len(thicks) == 1 and len(widths) == 1
    if uniform_dims:
        dims = f"{next(iter(thicks))} × {next(iter(widths))} mm"
    gross_tot = sum(_to_float(c.get("gross_weight_kg")) for c in coils)
    net_tot   = sum(_to_float(c.get("weight_kg")) for c in coils)
    total_gross = (parsed_cert.get("total_gross_weight_kg")
                   or (_fmt_int_kg(gross_tot) + " kg" if gross_tot else ""))
    if isinstance(total_gross, str) and total_gross and not total_gross.endswith("kg"):
        total_gross = f"{total_gross} kg"
    total_net = (parsed_cert.get("total_net_weight_kg")
                 or (_fmt_int_kg(net_tot) + " kg" if net_tot else ""))
    if isinstance(total_net, str) and total_net and not total_net.endswith("kg"):
        total_net = f"{total_net} kg"

    dim_std = parsed_cert.get("dimensional_standard") or ""
    if _has(parsed_cert.get("tolerance_note")):
        dim_std = f"{dim_std} — {parsed_cert['tolerance_note']}".strip(" —")

    product_pairs = [
        ("Description of Goods",  parsed_cert.get("material_type")),
        ("Standard",              parsed_cert.get("standard")),
        ("Grade / Quality",       grade_full),
        ("Coating",               parsed_cert.get("coating")),
        ("Surface Treatment",     parsed_cert.get("surface_quality")),
        ("Delivery Condition",    parsed_cert.get("delivery_condition")),
        ("Dimensions",            dims),
        ("Quantity",              (f"{len(coils)} "
                                    f"{L['coil'] if len(coils)==1 else L['coils']}")
                                   if coils else ""),
        ("Total Net Weight",      total_net),
        ("Total Gross Weight",    total_gross),
        ("Dimensional Standard",  dim_std),
        ("Steelmaking Process",   parsed_cert.get("steelmaking_process")),
        ("Country of Origin",     parsed_cert.get("country_of_origin")),
        ("Melted and Poured",     parsed_cert.get("melted_and_poured")),
    ]

    # ── Assemble story with dynamic section numbering ──
    story = [
        _info_box(L, cert_type, dest_country, issue_date, so_number, standard),
        Spacer(1, 6 * mm),
    ]
    n = 0

    pd = _product_details(product_pairs)
    if pd:
        n += 1
        story += [KeepTogether([_sec_hdr(n, L["s_product"]), pd]), Spacer(1, 6 * mm)]

    if coils:
        n += 1
        show_net    = any(_has(c.get("weight_kg")) for c in coils)
        show_pack   = any(_has(c.get("pack_no")) for c in coils)
        show_serial = any(_has(c.get("serial") or c.get("serial_no")) for c in coils)
        extra_cols: list = []
        for c in coils:
            for k, v in (c.get("extra") or {}).items():
                # backstop: never surface an administrative/identity column
                if k not in extra_cols and _has(v) and not _is_admin_leak(k):
                    extra_cols.append(k)
        # per-row grade fallback: document-level grade if rows carry none
        for c in coils:
            if not _has(c.get("grade")) and _has(grade_full):
                c["grade"] = grade_full
        note = L["dims_note"] if uniform_dims and len(coils) > 1 else ""
        story.append(CondPageBreak(40 * mm))
        story.append(_sec_hdr(n, L["s_positions"], L["dims_mm"]))
        story.append(_positions_table(L, coils, vs_articles, show_net,
                                      show_pack, show_serial, extra_cols))
        if note:
            story += [Spacer(1, 1.5 * mm), _p(note, S["NOTE"])]
        story.append(Spacer(1, 6 * mm))

    chem_headers = parsed_cert.get("chem_headers") or {}
    chem_t, chem_elems = _chem_table(coils, chem_headers, L, _NUMFMT, _LANG)
    if chem_t:
        n += 1
        cvt = (parsed_cert.get("chem_value_type") or
               ("norm_max" if parsed_cert.get("chem_norm_only") else "measured"))
        if any(_SCALE_RE.search(str(h)) for h in chem_headers.values()):
            unit_note = L["chem_units_hdr"]
        else:
            unit_note = (f"{L['chem_max']}" if cvt == "norm_max"
                         else f"{L['chem_meas']} · {L['pct_mass']}")
        story += [CondPageBreak(40 * mm),
                  _sec_hdr(n, L["s_chem"], unit_note), chem_t,
                  Spacer(1, 6 * mm)]

    mech_t, legend_p = _mech_table(L, coils, remarks, _NUMFMT, _LANG)
    if mech_t:
        n += 1
        story += [CondPageBreak(45 * mm),
                  _sec_hdr(n, L["s_mech"]), legend_p,
                  Spacer(1, 2 * mm), mech_t,
                  Spacer(1, 6 * mm)]

    surf_t = _surface_table(parsed_cert.get("surface_tests"))
    if surf_t:
        n += 1
        story += [CondPageBreak(40 * mm),
                  _sec_hdr(n, L["s_surface"]), surf_t,
                  Spacer(1, 6 * mm)]

    for entry in parsed_cert.get("extra_tests") or []:
        if not isinstance(entry, dict):
            continue
        flws = _extra_test_flowables(entry)
        if flws:
            n += 1
            title = _blank(entry.get("section_name"), "Additional Test Results")
            story += [KeepTogether([_sec_hdr(n, title)] + flws), Spacer(1, 6 * mm)]

    # adhesion statement travels with remarks if not already there
    adh = parsed_cert.get("adhesion_test_text")
    if _has(adh) and not any(str(adh).strip() in r for r in remarks):
        remarks.append(str(adh).strip())

    if remarks:
        n += 1
        story += [KeepTogether([_sec_hdr(n, L["s_remarks"])] +
                               _remarks_flowables(remarks)), Spacer(1, 6 * mm)]

    # Certification — conditional ISO sentence, validity box, NO signature block
    n += 1
    include_iso = bool(parsed_cert.get("references_iso9001")
                       and parsed_cert.get("references_iatf16949"))
    if _has(parsed_cert.get("quality_system_text")):
        include_iso = include_iso or (
            "9001" in str(parsed_cert["quality_system_text"])
            and "16949" in str(parsed_cert["quality_system_text"]))
    story.append(KeepTogether([_sec_hdr(n, L["s_cert"])]
                              + _certification_flowables(L, include_iso)))

    # ── Build ──
    buf = BytesIO()
    doc = BaseDocTemplate(
        buf, pagesize=(PAGE_W, PAGE_H),
        leftMargin=ML, rightMargin=MR,
        topMargin=MAST_H + 3 * mm, bottomMargin=FRAME_Y,
    )
    frame = Frame(ML, FRAME_Y, CONTENT_W, FRAME_H,
                  leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    doc.addPageTemplates([PageTemplate(
        id="main", frames=[frame],
        onPage=lambda c, d: _draw_page(c, d, L),
    )])
    doc.build(story, canvasmaker=_NumberedCanvas)
    return buf.getvalue()
