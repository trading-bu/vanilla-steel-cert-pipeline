"""
cert_parser.py — Multi-coil supplier certificate parser.

Uses pdfplumber text extraction + regex to parse per-coil chemistry and
mechanical data from supplier PDFs. Handles 4 known formats:

  ArcelorMittal  — integer chemistry (×10⁻³%, ×10⁻², ×10⁻⁴%), multi-coil,
                   mechanical may span two pages
  SSAB           — decimal % chemistry, Ceq first column (skip), coil list
                   on page 1 / chemistry on page 2, linked by cast number
  Wolter-Stahl   — decimal % with comma as decimal separator, cert page 2
  Ori Martin     — standard decimal % table

Returns a unified dict consumed by pdf_generator.py.
"""
import re
import pdfplumber
from io import BytesIO


# ── Element lists ──────────────────────────────────────────────────────────────

# ArcelorMittal column order (C71→C85 in source)
AM_CHEM_COLS = ["C", "Mn", "Si", "P", "S", "N", "Al", "Ti", "Cr", "Ni", "Cu", "Nb", "B", "V", "Mo"]

# AM unit scale per element — used in PDF column headers
AM_UNITS = {
    "C": "-3", "Si": "-3", "P": "-3", "S": "-3", "Al": "-3",
    "Ti": "-3", "Cr": "-3", "Ni": "-3", "Cu": "-3", "Nb": "-3",
    "V": "-3", "Mo": "-3", "Mn": "-2", "N": "-4", "B": "-4",
}

# SSAB column order after (Cast No, Plate No): Ceq [skip], then chemistry
SSAB_CHEM_COLS = ["C", "Si", "Mn", "P", "S", "Al", "Nb", "V", "Ti", "Cu", "Cr", "Ni", "Mo", "B"]


# ── Entry point ────────────────────────────────────────────────────────────────

def _clean_text(text: str) -> str:
    """
    Strip PDF encoding artefacts that appear in some suppliers' text extraction.
    (cid:N) codes are emitted by pdfplumber for glyphs it cannot map to Unicode
    (common in ArcelorMittal certs that use a custom grid font).
    Replace them with a single space so column values stay parseable.
    """
    return re.sub(r'\(cid:\d+\)', ' ', text)


def parse_cert(pdf_bytes: bytes) -> dict:
    """
    Parse a supplier cert PDF.

    Returns:
    {
        "supplier_format": str,          # "arcelormittal" | "ssab" | "wolter" |
                                         #  "ori_martin" | "unknown"
        "is_integer_chemistry": bool,    # True = raw AM integers in chemicals dicts
        "am_units": dict | None,         # element → exponent str ("-3"/"-2"/"-4")
        "grade": str,
        "standard": str,
        "material_type": str,
        "steelmaking": str,
        "total_weight_kg": float,
        "coils": [                       # one dict per coil/item
            {
                "item_orig": str,        # item number from source cert
                "pack_nr":   str,        # pack/coil number
                "coil_no":   str,        # coil id (may equal pack_nr)
                "cast_no":   str,        # heat / cast number
                "thickness_mm": str,
                "width_mm":    str,
                "qty":    int,
                "weight_kg": float | None,
                "chemicals": {           # element → int (AM) or float (others)
                    "C": ..., "Mn": ..., ...
                },
                "mechanical": {          # None if cert has no mech section
                    "cond": str,         # F / V / N
                    "dir":  str,         # L / S / D
                    "rp02": int,
                    "rm":   int,
                    "a_pct": float,
                    "rp02_rm": float | None,
                } | None,
            },
            ...
        ],
        "cond_legend": dict,             # {"F": "Non-Aged", ...}
        "dir_legend":  dict,             # {"L": "Longitudinal (0°)", ...}
        "specimen_dims": str,            # e.g. "LC/L0/B0 120/80/20 mm"
        "remarks": list[str],            # verbatim remark lines
    }
    """
    with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
        pages_text = [_clean_text(p.extract_text() or "") for p in pdf.pages]

    full_text = "\n".join(pages_text)
    supplier  = _detect_supplier(full_text)

    if supplier == "arcelormittal":
        result = _parse_arcelormittal(pages_text, full_text)
    elif supplier == "ssab":
        result = _parse_ssab(pages_text, full_text)
    elif supplier == "wolter":
        result = _parse_wolter(pages_text, full_text)
    else:
        result = _parse_generic(pages_text, full_text)

    result["supplier_format"] = supplier
    result["raw_page_count"]  = len(pages_text)
    return result


# ── Supplier detection ─────────────────────────────────────────────────────────

def _detect_supplier(text: str) -> str:
    t = text.lower()
    if "arcelormittal" in t or "arcelor mittal" in t:
        return "arcelormittal"
    if "ssab" in t:
        return "ssab"
    if "wolter" in t:
        return "wolter"
    if "ori martin" in t or "omas" in t:
        return "ori_martin"
    # Schaefer-Werke / EMW Stahl service centres
    if "schaefer" in t or "schäfer" in t or "emw stahl" in t:
        return "schaefer"
    return "unknown"


# ── Utility helpers ────────────────────────────────────────────────────────────

def _f(s, comma_dec=False):
    """String → float or None."""
    if s is None:
        return None
    s = str(s).strip()
    if not s or s in ("-", "–", ""):
        return None
    if comma_dec:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _collect_conds_dirs(coils: list) -> tuple[dict, dict]:
    """Build legend dicts from actual values present in coil list."""
    _cond = {"F": "Non-Aged", "V": "Aged", "N": "Normalised"}
    _dir  = {"L": "Longitudinal (0°)", "S": "45°", "D": "Transverse (90°)"}
    seen_c, seen_d = set(), set()
    for c in coils:
        m = c.get("mechanical")
        if m:
            if m.get("cond"):
                seen_c.add(m["cond"])
            if m.get("dir"):
                seen_d.add(m["dir"])
    return (
        {k: v for k, v in _cond.items() if k in seen_c},
        {k: v for k, v in _dir.items()  if k in seen_d},
    )


def _extract_grade(text: str) -> str:
    """Best-effort grade extraction from any cert text."""
    # EN10346 grades: S###GD+ZM / S###MC / S###JR etc.
    m = re.search(r'\b(S\d{3}[A-Z]{1,6}(?:\+[A-Z0-9]+)?(?:\s+HIGH\s+PERFORMANCE)?)', text, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return ""


def _extract_standard(text: str) -> str:
    m = re.search(r'(EN\s*\d{4,5}(?:[:\-]\d{4})?)', text, re.IGNORECASE)
    if m:
        return m.group(1).replace(" ", " ")
    return ""


def _extract_steelmaking(text: str) -> str:
    m = re.search(r'STEELMAKING\s+PROCESS[:\s]+(\w+)', text, re.IGNORECASE)
    if m:
        return m.group(1)
    return ""


def _extract_specimen_dims(text: str) -> str:
    m = re.search(r'LC\s*/\s*L0\s*/\s*B0\s*([\d/\s]+MM)', text, re.IGNORECASE)
    if m:
        return f"LC/L0/B0 {m.group(1).strip()}"
    m = re.search(r'(120\s*/\s*80\s*/\s*20)', text)
    if m:
        return f"LC/L0/B0 {m.group(1)} mm"
    return ""


def _extract_remarks(text: str) -> list:
    """Pull lines under REMARKS section."""
    start = text.upper().find("REMARKS")
    if start == -1:
        return []
    block = text[start:start + 600]
    lines = [l.strip() for l in block.split("\n")[1:] if l.strip()]
    # Stop at empty line or next section keyword
    remarks = []
    for l in lines:
        if re.match(r'^[A-Z ]{6,}$', l) and l.isupper():
            break
        if l.startswith("---") or not l:
            continue
        remarks.append(l)
    return remarks


# ══════════════════════════════════════════════════════════════════════════════
# ArcelorMittal parser
# ══════════════════════════════════════════════════════════════════════════════

def _parse_arcelormittal(pages_text: list, full_text: str) -> dict:
    """
    AM cert structure:
    - Product section: item code, width (mm), thickness (mm)
    - Chemistry table: ITEM | PACK NR | GROSS WEIGHT | COIL NO | CAST N. | 15 integers
    - Mechanical table: ITEM | PACK NR | Cond | Dir | Rp0.2 | Rm | A | Rp0.2/Rm
      (may continue on page 2 with no re-printed header for PACK NR)
    """
    chem_rows = _am_chemistry_rows(full_text)
    mech_map  = _am_mechanical_rows(full_text)   # pack_nr → mechanical dict
    dims      = _am_product_dims(full_text)

    coils = []
    for idx, row in enumerate(chem_rows, 1):
        pack_nr = row["pack_nr"]
        coil = {
            "item_orig":    str(idx),
            "pack_nr":      pack_nr,
            "coil_no":      row.get("coil_no", ""),
            "cast_no":      row.get("cast_no", ""),
            "thickness_mm": dims.get("thickness", ""),
            "width_mm":     dims.get("width", ""),
            "qty":          1,
            "weight_kg":    row.get("weight_kg"),
            "chemicals":    row.get("chemicals", {}),
            "mechanical":   mech_map.get(pack_nr),
        }
        coils.append(coil)

    total_w = sum(c["weight_kg"] for c in coils if c["weight_kg"])
    cond_l, dir_l = _collect_conds_dirs(coils)

    return {
        "is_integer_chemistry": True,
        "am_units":             AM_UNITS,
        "grade":                _extract_grade(full_text),
        "standard":             _extract_standard(full_text),
        "material_type":        _am_material_type(full_text),
        "steelmaking":          _extract_steelmaking(full_text),
        "total_weight_kg":      total_w,
        "coils":                coils,
        "cond_legend":          cond_l if cond_l else {"F": "Non-Aged"},
        "dir_legend":           dir_l  if dir_l  else {"L": "Longitudinal (0°)"},
        "specimen_dims":        _extract_specimen_dims(full_text),
        "remarks":              _extract_remarks(full_text),
    }


def _am_chemistry_rows(text: str) -> list:
    """
    Parse ArcelorMittal chemistry table rows.

    Line format:
      [02] 868060226 26040G240222317 31957 72 100 20 13 2 40 38 57 17 20 16 58 1 1 2

    Columns: [item] pack_nr weight+coil_no cast_no C Mn Si P S N Al Ti Cr Ni Cu Nb B V Mo

    Weight and coil_no are concatenated as "<digits>G<digits>" in PDF text.
    """
    rows  = []
    start = text.find("CHEMICAL ANALYSIS")
    if start == -1:
        return rows

    section = text[start:]
    lines   = section.split("\n")

    # Skip until we've seen the element header line
    header_found = False
    for line in lines:
        if not header_found:
            # Header contains "C" "Mn" "Si" close together
            if re.search(r'\bC\b.*\bMn\b', line) and re.search(r'\bSi\b', line):
                header_found = True
            continue

        # Stop when we hit the totals line (just a count + big number) or next section
        stripped = line.strip()
        if not stripped:
            continue
        if re.match(r'^\d{1,2}\s+\d{5,7}\s*$', stripped):
            break
        if re.search(r'MECHANICAL|TENSILE|REMARKS', line, re.IGNORECASE):
            break

        row = _am_parse_chem_line(line)
        if row:
            rows.append(row)

    return rows


def _am_parse_chem_line(line: str) -> dict | None:
    """
    Parse one AM chemistry line after (cid:N) stripping.

    After stripping, columns are space-separated:
      [item] pack_nr gross_weight coil_no cast_no C Mn Si P S N Al Ti Cr Ni Cu Nb B V Mo

    pack_nr:    9 digits starting with 8  (e.g. 868060226)
    gross_wt:   4-6 digits               (e.g. 26040)
    coil_no:    starts with G             (e.g. G240222317)
    cast_no:    5 digits                  (e.g. 31957)
    chemistry:  15 small integers         (e.g. 72 100 20 13 2 40 ...)
    """
    tokens = line.split()
    if len(tokens) < 18:   # need pack + weight + coil + cast + 15 chem = 18 min
        return None

    # Find pack_nr: 9-digit number starting with 8
    pack_nr  = None
    pack_idx = None
    for i, t in enumerate(tokens):
        if re.match(r'^8\d{8}$', t):
            pack_nr  = t
            pack_idx = i
            break
    if not pack_nr:
        return None

    # Collect the last 15 small integers as chemistry values (work backwards)
    rev_chem = []
    for t in reversed(tokens):
        if re.match(r'^\d{1,4}$', t) and len(rev_chem) < 15:
            rev_chem.insert(0, int(t))
        elif rev_chem:
            break   # gap → stop
    if len(rev_chem) < 15:
        return None

    # Mid-tokens between pack_nr and chemistry: weight coil_no cast_no
    chem_start = len(tokens) - len(rev_chem)
    mid        = tokens[pack_idx + 1 : chem_start]

    coil_no   = ""
    cast_no   = ""
    weight_kg = None

    # Position matters: weight comes BEFORE coil_no (G…), cast_no comes AFTER
    for t in mid:
        if re.match(r'^G\d{6,}$', t, re.IGNORECASE):
            coil_no = t
        elif re.match(r'^\d{4,6}$', t):
            if not coil_no:          # before G-token → gross weight
                weight_kg = float(t)
            elif not cast_no:        # after G-token → cast number
                cast_no = t

    chemicals = {
        elem: rev_chem[i] if i < len(rev_chem) else None
        for i, elem in enumerate(AM_CHEM_COLS)
    }

    return {
        "pack_nr":   pack_nr,
        "coil_no":   coil_no,
        "cast_no":   cast_no,
        "weight_kg": weight_kg,
        "chemicals": chemicals,
    }


def _am_mechanical_rows(text: str) -> dict:
    """
    Returns {pack_nr: mechanical_dict} for all AM mechanical rows.

    Line format: [item] 868060226 F L 603 663 15 90
    """
    result = {}
    # AM cert uses "YIELD STRENGTH" (not "MECHANICAL") as section heading
    for keyword in ("YIELD STRENGTH", "MECHANICAL"):
        start = text.upper().find(keyword)
        if start != -1:
            break
    else:
        return result

    lines = text[start:].split("\n")
    header_found = False

    for line in lines:
        if not header_found:
            if re.search(r'Rp0\.2|YIELD', line, re.IGNORECASE):
                header_found = True
            continue

        # Match: [item] pack_nr Cond Dir Rp02 Rm A [Rp02/Rm]
        m = re.search(
            r'(8\d{8})\s+([FVN])\s+([LSD])\s+(\d{3,4})\s+(\d{3,4})\s+(\d{1,3})(?:\s+(\d{2,3}))?',
            line
        )
        if m:
            pack_nr = m.group(1)
            result[pack_nr] = {
                "cond":    m.group(2),
                "dir":     m.group(3),
                "rp02":    int(m.group(4)),
                "rm":      int(m.group(5)),
                "a_pct":   float(m.group(6)),
                "rp02_rm": float(m.group(7)) if m.group(7) else None,
            }

    return result


def _am_product_dims(text: str) -> dict:
    """
    Extract width (mm) and thickness (mm) from AM product section.
    AM uses: "02 1.347 1,50" meaning width=1347mm (1.347m) thickness=1.50mm
    """
    # Look for pattern: item_code width_metres thickness_mm near "THICKNESS" / "WIDTH" keywords
    m = re.search(r'\b0\d\s+([\d\.]+)\s+([\d,\.]+)', text)
    if m:
        try:
            w = float(m.group(1).replace(",", "."))
            t = float(m.group(2).replace(",", "."))
            if w < 10:          # convert metres → mm
                w = w * 1000
            return {"width": str(int(round(w))), "thickness": str(t)}
        except ValueError:
            pass
    return {"width": "", "thickness": ""}


def _am_material_type(text: str) -> str:
    m = re.search(r'(HOT DIP\s+ZN[\w\s/]+COIL|HOT ROLLED[\w\s]+COIL)', text, re.IGNORECASE)
    return m.group(1).strip().title() if m else "Coil"


# ══════════════════════════════════════════════════════════════════════════════
# SSAB parser
# ══════════════════════════════════════════════════════════════════════════════

def _parse_ssab(pages_text: list, full_text: str) -> dict:
    """
    SSAB cert structure:
    - Page 1: coil/item list (Item | Dimensions | Pcs | Weight kg | Cast plate No | SP No | ...)
    - Page 2: chemistry table (Cast No | Plate No | Ceq [SKIP] | C SI MN P S AL NB V TI CU CR NI MO B)
    - Mechanical properties: NOT always present in SSAB format.
    """
    page1 = pages_text[0] if pages_text else ""
    page2 = pages_text[1] if len(pages_text) > 1 else ""

    coil_list  = _ssab_coil_list(page1)
    chem_map   = _ssab_chemistry(page2)   # cast_no → chemicals dict
    mech_map   = _ssab_mechanical(full_text)  # cast_no/pack_nr → mech dict

    # Merge: each coil gets chemistry from its cast_no
    coils = []
    for coil in coil_list:
        cast = coil.get("cast_no", "")
        coil["chemicals"] = chem_map.get(cast, {})
        coil["mechanical"] = mech_map.get(cast) or mech_map.get(coil.get("pack_nr", ""))
        coils.append(coil)

    total_w = sum(c["weight_kg"] for c in coils if c["weight_kg"])
    cond_l, dir_l = _collect_conds_dirs(coils)

    return {
        "is_integer_chemistry": False,
        "am_units":             None,
        "grade":                _ssab_grade(full_text),
        "standard":             _extract_standard(full_text),
        "material_type":        _ssab_material_type(full_text),
        "steelmaking":          "",
        "total_weight_kg":      total_w,
        "coils":                coils,
        "cond_legend":          cond_l if cond_l else {},
        "dir_legend":           dir_l  if dir_l  else {},
        "specimen_dims":        _extract_specimen_dims(full_text),
        "remarks":              _extract_remarks(full_text),
    }


def _ssab_coil_list(text: str) -> list:
    """
    Parse SSAB page-1 coil list.

    Line format: 001 3.00 X 1509 X 0 1 27112 12192 031 FI SSAB FI RAAHE
    Fields: item | thickness X width X length | qty | weight_kg | cast_plate_no | SP_no | ...
    """
    coils = []
    # Match: item(3) thickness X width X 0 qty weight cast_no
    pattern = re.compile(
        r'^(\d{3})\s+'                     # item
        r'([\d\.]+)\s+X\s+([\d]+)\s+X\s+\d+\s+'  # thickness X width X (length)
        r'(\d+)\s+'                         # qty
        r'(\d{4,6})\s+'                     # weight_kg
        r'(\d{4,6})',                        # cast_no (plate)
        re.MULTILINE
    )
    for m in pattern.finditer(text):
        coils.append({
            "item_orig":    m.group(1),
            "pack_nr":      m.group(6),    # cast_no is the primary identifier for SSAB
            "coil_no":      m.group(1),    # item number as coil id
            "cast_no":      m.group(6),
            "thickness_mm": m.group(2),
            "width_mm":     m.group(3),
            "qty":          int(m.group(4)),
            "weight_kg":    float(m.group(5)),
            "chemicals":    {},
            "mechanical":   None,
        })
    return coils


def _ssab_chemistry(text: str) -> dict:
    """
    Parse SSAB page-2 chemistry.
    Returns {cast_no: {C: ..., Si: ..., ...}}

    Line format (one line per cast):
      12192 001 .26 .086 .01 0.95 .007 .011 .034 .001 .005 .015 .016 0.05 0.04 .004.0001
      Columns: cast_no plate_no Ceq(SKIP) C SI MN P S AL NB V TI CU CR NI MO B

    NOTE: Mo and B are often concatenated as ".004.0001" (no space) in extracted PDF text.
    We parse line-by-line so \s never crosses line boundaries.
    """
    result = {}
    for line in text.split("\n"):
        line = line.strip()
        # Line must start with 4-6 digit cast number
        m = re.match(r'^(\d{4,6})[ \t]+(\d{1,3})[ \t]+([\d\.]+)[ \t]+(.*)', line)
        if not m:
            continue
        cast_no = m.group(1)
        # m.group(2) = plate_no, m.group(3) = Ceq (both skipped)
        rest    = m.group(4).strip()

        # Expand any concatenated float pairs (e.g. ".004.0001" → ".004", ".0001")
        tokens = rest.split()
        vals   = []
        for t in tokens:
            # Two merged floats: first ends with digits, second starts with '.'
            split_m = re.match(r'^(\d*\.\d+)(\.\d+)$', t)
            if split_m:
                vals.append(split_m.group(1))
                vals.append(split_m.group(2))
            else:
                vals.append(t)

        if len(vals) < len(SSAB_CHEM_COLS):
            continue   # not a data row (e.g. CEQ= formula line)

        chems = {}
        for i, elem in enumerate(SSAB_CHEM_COLS):
            chems[elem] = _f(vals[i]) if i < len(vals) else None
        result[cast_no] = chems

    return result


def _ssab_mechanical(text: str) -> dict:
    """
    Parse SSAB mechanical properties if present.
    Returns {identifier: mechanical_dict}
    SSAB analysis certs often omit mechanical, so this may return {}.
    """
    result = {}
    if "MECHANICAL" not in text.upper() and "TENSILE" not in text.upper():
        return result

    pattern = re.compile(
        r'(\d{3})\s+'                        # item/cast ref
        r'([FVN])\s+([LSD])\s+'              # cond dir
        r'(\d{3,4})\s+(\d{3,4})\s+([\d\.]+)'  # Rp02 Rm A
        r'(?:\s+([\d\.]+))?',                 # optional Rp02/Rm
        re.MULTILINE
    )
    for m in pattern.finditer(text):
        result[m.group(1)] = {
            "cond":    m.group(2),
            "dir":     m.group(3),
            "rp02":    int(m.group(4)),
            "rm":      int(m.group(5)),
            "a_pct":   float(m.group(6)),
            "rp02_rm": float(m.group(7)) if m.group(7) else None,
        }
    return result


def _ssab_grade(text: str) -> str:
    """SSAB uses grade like 'SECOND CHOICE COILS' or a steel grade on same line as 'Grade'."""
    m = re.search(r'Grade\s+B\d{2}.*?(\S+(?:\s+\S+){0,3})', text, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    # Fall back to generic extraction
    return _extract_grade(text) or ""


def _ssab_material_type(text: str) -> str:
    m = re.search(r'(HOT ROLLED[\w\s]+COIL[S]?)', text, re.IGNORECASE)
    return m.group(1).strip().title() if m else "Hot Rolled Steel Coils"


# ══════════════════════════════════════════════════════════════════════════════
# Wolter-Stahl parser
# ══════════════════════════════════════════════════════════════════════════════

def _parse_wolter(pages_text: list, full_text: str) -> dict:
    """
    Wolter-Stahl: cert is on page 2, comma decimal separator.
    Chemistry is given as key-value running text (e.g. "C 0,069 Si 0,026...").
    Usually one coil per cert.
    """
    # Cert content starts page 2 if there are 2 pages
    cert_text = pages_text[1] if len(pages_text) > 1 else full_text

    chemicals = _wolter_chemicals(cert_text)
    mechanical = _wolter_mechanical(cert_text)

    # Wolter certs are typically single-item
    heat = _wolter_heat(cert_text)
    weight = _wolter_weight(cert_text)
    dims = _wolter_dims(cert_text)

    coil = {
        "item_orig":    "1",
        "pack_nr":      heat,
        "coil_no":      heat,
        "cast_no":      heat,
        "thickness_mm": dims.get("thickness", ""),
        "width_mm":     dims.get("width", ""),
        "qty":          1,
        "weight_kg":    weight,
        "chemicals":    chemicals,
        "mechanical":   mechanical,
    }

    cond_l, dir_l = _collect_conds_dirs([coil])

    return {
        "is_integer_chemistry": False,
        "am_units":             None,
        "grade":                _extract_grade(cert_text),
        "standard":             _extract_standard(cert_text),
        "material_type":        "",
        "steelmaking":          _extract_steelmaking(cert_text),
        "total_weight_kg":      weight or 0,
        "coils":                [coil],
        "cond_legend":          cond_l if cond_l else {},
        "dir_legend":           dir_l  if dir_l  else {},
        "specimen_dims":        _extract_specimen_dims(cert_text),
        "remarks":              _extract_remarks(cert_text),
    }


def _wolter_chemicals(text: str) -> dict:
    """Extract Wolter chemistry from key-value running text. Comma decimal."""
    chems = {}
    elems = ["C", "Si", "Mn", "P", "S", "Cr", "Mo", "Ni", "Al", "Cu", "Ti", "V", "Nb", "B", "N"]
    for elem in elems:
        m = re.search(rf'\b{elem}\s+([\d,\.]+)', text)
        if m:
            chems[elem] = _f(m.group(1), comma_dec=True)
    return chems


def _wolter_mechanical(text: str) -> dict | None:
    """Extract Wolter mechanical values. Comma decimal."""
    rp = re.search(r'Rp\s*0[,\.]2\s*=?\s*([\d,\.]+)', text, re.IGNORECASE)
    rm = re.search(r'Rm\s*=?\s*([\d,\.]+)', text, re.IGNORECASE)
    a  = re.search(r'A(?:80|5)?\s*=?\s*([\d,\.]+)\s*%?', text, re.IGNORECASE)
    if not (rp or rm):
        return None
    return {
        "cond":    "F",
        "dir":     "L",
        "rp02":    int(_f(rp.group(1), True)) if rp else None,
        "rm":      int(_f(rm.group(1), True)) if rm else None,
        "a_pct":   _f(a.group(1), True) if a else None,
        "rp02_rm": None,
    }


def _wolter_heat(text: str) -> str:
    m = re.search(r'(?:Schmelze|Charge|Heat)[:\s#]+([A-Z0-9/\-]+)', text, re.IGNORECASE)
    return m.group(1).strip() if m else ""


def _wolter_weight(text: str) -> float | None:
    m = re.search(r'(\d{1,3}(?:[,\.]\d{3})+|\d{4,6})\s*kg', text, re.IGNORECASE)
    if m:
        return _f(m.group(1).replace(".", "").replace(",", ""))
    return None


def _wolter_dims(text: str) -> dict:
    # "2,50 x 1250" or "2.5 x 1250"
    m = re.search(r'([\d,\.]+)\s*[xX]\s*(\d{3,4})', text)
    if m:
        return {
            "thickness": m.group(1).replace(",", "."),
            "width":     m.group(2),
        }
    return {}


# ══════════════════════════════════════════════════════════════════════════════
# Generic / Ori Martin fallback parser
# ══════════════════════════════════════════════════════════════════════════════

def _parse_generic(pages_text: list, full_text: str) -> dict:
    """
    Generic parser for Ori Martin and unknown formats.
    Assumes standard decimal % chemistry in a table.
    """
    chemicals  = _generic_chemicals(full_text)
    mechanical = _generic_mechanical(full_text)
    weight     = _generic_weight(full_text)
    heat       = _generic_heat(full_text)
    dims       = _generic_dims(full_text)

    coil = {
        "item_orig":    "1",
        "pack_nr":      heat,
        "coil_no":      heat,
        "cast_no":      heat,
        "thickness_mm": dims.get("thickness", ""),
        "width_mm":     dims.get("width", ""),
        "qty":          1,
        "weight_kg":    weight,
        "chemicals":    chemicals,
        "mechanical":   mechanical,
    }

    cond_l, dir_l = _collect_conds_dirs([coil])

    return {
        "is_integer_chemistry": False,
        "am_units":             None,
        "grade":                _extract_grade(full_text),
        "standard":             _extract_standard(full_text),
        "material_type":        "",
        "steelmaking":          _extract_steelmaking(full_text),
        "total_weight_kg":      weight or 0,
        "coils":                [coil],
        "cond_legend":          cond_l if cond_l else {},
        "dir_legend":           dir_l  if dir_l  else {},
        "specimen_dims":        _extract_specimen_dims(full_text),
        "remarks":              _extract_remarks(full_text),
    }


def _generic_chemicals(text: str) -> dict:
    """
    Extract decimal % chemistry from a table row following element headers.
    Handles both period and comma decimal separators (e.g. German certs).
    Skips rows that look like min/max specification limits (Schaefer-Werke
    tables have min, ist, max rows — we want the IST row which contains
    actual measured values, typically appearing between the min and max rows).
    """
    elems = ["C", "Si", "Mn", "P", "S", "Al", "Cr", "Ni", "Mo", "Cu", "Ti", "V", "Nb", "B", "N"]
    chems = {}

    lines = text.split("\n")
    header_idx = None
    header_order = []
    for i, l in enumerate(lines):
        tokens = l.split()
        matched = [t for t in tokens if t.upper() in [e.upper() for e in elems]]
        if len(matched) >= 4:
            header_order = [t.upper() for t in tokens if t.upper() in [e.upper() for e in elems]]
            header_idx = i
            break

    if header_idx is None:
        return chems

    # Search the next several lines for the data row.
    # Prefer a line labelled "ist" (German for actual); otherwise take the
    # first line that has enough numeric values.
    ist_row_vals  = None
    first_row_vals = None

    for j in range(header_idx + 1, min(header_idx + 8, len(lines))):
        line = lines[j]
        tokens = line.split()

        # Try both period and comma as decimal separator
        float_vals = []
        for v in tokens:
            f = _f(v)
            if f is None:
                f = _f(v, comma_dec=True)
            if f is not None and 0 <= f <= 100:
                float_vals.append(f)

        if len(float_vals) >= max(2, len(header_order) - 3):
            label = line.strip().split()[0].lower() if line.strip() else ""
            if label in ("ist", "actual", "gemessen"):
                ist_row_vals = float_vals
                break
            if first_row_vals is None:
                first_row_vals = float_vals

    chosen = ist_row_vals if ist_row_vals is not None else first_row_vals
    if chosen:
        for k, elem in enumerate(header_order):
            chems[elem] = chosen[k] if k < len(chosen) else None

    return chems


def _generic_mechanical(text: str) -> dict | None:
    rp = re.search(r'Rp\s*0[,\.]2\D{0,5}([\d,\.]+)', text, re.IGNORECASE)
    rm = re.search(r'\bRm\b\D{0,5}([\d,\.]+)', text, re.IGNORECASE)
    a  = re.search(r'\bA(?:80|5)?\b\D{0,5}([\d,\.]+)', text, re.IGNORECASE)
    if not (rp and rm):
        return None
    return {
        "cond":    "F",
        "dir":     "L",
        "rp02":    int(_f(rp.group(1))),
        "rm":      int(_f(rm.group(1))),
        "a_pct":   _f(a.group(1)) if a else None,
        "rp02_rm": None,
    }


def _generic_weight(text: str) -> float | None:
    m = re.search(r'(\d{4,6})\s*kg', text, re.IGNORECASE)
    return float(m.group(1)) if m else None


def _generic_heat(text: str) -> str:
    # German keywords first (more specific), then English
    m = re.search(
        r'(?:Einsatzcharge|Chargen-Nr\.?|Chargen-Nummer|Schmelze|Colata|Heat|Charge|Coil|Cast)'
        r'[:/\s#]+([A-Z0-9/\-]+)',
        text, re.IGNORECASE
    )
    return m.group(1).strip() if m else ""


def _generic_dims(text: str) -> dict:
    m = re.search(r'([\d\.]+)\s*[xX]\s*(\d{3,4})', text)
    if m:
        return {"thickness": m.group(1), "width": m.group(2)}
    return {}
