"""
Odoo XML-RPC client for the certificate neutralisation pipeline.
Looks up Sales Order, buyer country, and VS article numbers from a VS PO number.
"""
import re
import xmlrpc.client
from datetime import datetime, timedelta

ODOO_URL      = "https://erp.ops.vanillasteel.com"
ODOO_DB       = "vanillasteel-main-22503126"
ODOO_LOGIN    = "mridul.goel@vanillasteel.com"
ODOO_API_KEY  = None   # Set via environment variable

_uid    = None
_models = None


def set_api_key(key: str):
    global ODOO_API_KEY
    ODOO_API_KEY = key


def _authenticate():
    """Authenticate once and cache the UID."""
    global _uid, _models
    if _uid is not None:
        return

    common = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/common")
    _uid = common.authenticate(ODOO_DB, ODOO_LOGIN, ODOO_API_KEY, {})
    if not _uid:
        raise RuntimeError("Odoo authentication failed. Check API key.")
    _models = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object")
    print(f"[Odoo] Authenticated as UID {_uid}")


def _call(model: str, method: str, args: list, kwargs: dict = None):
    _authenticate()
    return _models.execute_kw(ODOO_DB, _uid, ODOO_API_KEY, model, method, args, kwargs or {})


# ─── Spec normalisation (for cross-field grade/form/finish/coating matching) ──

_FINISH_NORM = {
    "HR": "HOTROLLED", "HOTROLLED": "HOTROLLED",
    "CR": "COLDROLLED", "COLDROLLED": "COLDROLLED",
    "HDG": "GALVANIZED", "GA": "GALVANIZED", "GI": "GALVANIZED",
    "GALVANIZED": "GALVANIZED", "GALVANISED": "GALVANIZED",
    "EG": "ELECTROGALVANIZED", "ELECTROGALVANIZED": "ELECTROGALVANIZED",
    "PP": "PICKLEDOILED", "PO": "PICKLEDOILED",
}
_FORM_NORM = {
    "COIL": "COIL", "COILS": "COIL", "COL": "COIL", "COLS": "COIL",
    "SLITCOIL": "COIL", "SLITTEDCOIL": "COIL", "SLITSTRIP": "COIL",
    "SHEET": "SHEET", "SHEETS": "SHEET",
    "PLATE": "PLATE", "PLATES": "PLATE",
    "FLATBAR": "FLATBAR", "STRIP": "STRIP",
}


def _norm(s: str) -> str:
    return re.sub(r"[\s\-_\.\/]+", "", (s or "")).upper()


def _spec_tokens(s: str) -> set:
    """
    Normalise and split a composite steel spec into searchable tokens.
    'DX51D+Z275'  → {'DX51D+Z275', 'DX51D', 'Z275'}
    'Cold Rolled'  → {'COLDROLLED', 'CR'}   (via _FINISH_NORM reverse)
    'HR Coil'      → {'HOTROLLED', 'COIL'}
    """
    raw = _norm(s)
    tokens = {raw} if raw else set()
    for part in re.split(r"[+\-/]", raw):
        part = part.strip()
        if len(part) >= 2:
            tokens.add(part)
            if part in _FINISH_NORM:
                tokens.add(_FINISH_NORM[part])
            if part in _FORM_NORM:
                tokens.add(_FORM_NORM[part])
    return tokens - {""}


def _choice_norm(s: str) -> str | None:
    """'first choice', '1st', '1', 'first' → '1';  'second' → '2'."""
    s = re.sub(r"[\s_\-]+", "", (s or "").lower())
    if re.search(r"first|1st|^1$|^1choice", s):
        return "1"
    if re.search(r"second|2nd|^2$|^2choice", s):
        return "2"
    return None


def _score_line(line: dict, cert: dict) -> int:
    """
    Score an Odoo PO line against cert signals.  Max = 13.

    line keys expected: weight_t, grade, choice, form, finish, coating,
                        width_mm, thickness_mm
    cert keys expected: weight_kg, grade, material_type, quality,
                        width_mm, thickness_mm
    """
    score = 0

    # ── Weight (most discriminating) — max 5 pts ──────────────────────────
    odoo_kg = (line.get("weight_t") or 0) * 1000
    cert_kg  = cert.get("weight_kg") or 0
    if odoo_kg > 0 and cert_kg > 0:
        pct = abs(odoo_kg - cert_kg) / odoo_kg
        if   pct <= 0.02: score += 5
        elif pct <= 0.05: score += 3
        elif pct <= 0.10: score += 1

    # ── Grade / spec cross-match — max 3 pts ──────────────────────────────
    # Pool ALL spec-like Odoo fields: grade, form, finish, coating.
    # Any of these might appear under "grade" on a supplier cert.
    odoo_tok: set = set()
    for f in ("grade", "form", "finish", "coating"):
        odoo_tok |= _spec_tokens(line.get(f) or "")

    # Pool cert's spec fields (Docsumo grade, material_type, quality).
    cert_tok: set = set()
    for f in ("grade", "material_type", "quality"):
        cert_tok |= _spec_tokens(cert.get(f) or "")

    if odoo_tok and cert_tok and (odoo_tok & cert_tok):
        score += 3

    # ── Choice / quality match — max 1 pt ─────────────────────────────────
    odoo_ch = _choice_norm(line.get("choice") or "")
    cert_ch  = _choice_norm(cert.get("quality") or "")
    if odoo_ch and cert_ch and odoo_ch == cert_ch:
        score += 1

    # ── Width — max 2 pts ─────────────────────────────────────────────────
    try:
        ow = float(line.get("width_mm") or 0)
        cw = float(cert.get("width_mm") or 0)
        if ow > 0 and cw > 0 and abs(ow - cw) <= 2:
            score += 2
    except (ValueError, TypeError):
        pass

    # ── Thickness — max 2 pts ─────────────────────────────────────────────
    try:
        ot = float(line.get("thickness_mm") or 0)
        ct = float(cert.get("thickness_mm") or 0)
        if ot > 0 and ct > 0 and abs(ot - ct) <= 0.1:
            score += 2
    except (ValueError, TypeError):
        pass

    return score


def get_neutralisation_data(vs_po_number: str) -> dict:
    """
    Given a VS Purchase Order number (e.g. 'P01655'), returns:
    - so_number:      Sales Order reference (e.g. 'S01448')
    - buyer_name:     Customer company name
    - buyer_country:  Country name (for 'Country of Destination' field)
    - vs_articles:    List of dicts: {vs_article, aoo_fast_number, heat_number}
                      Only lines where aoo_fast_number is filled are included.
    """
    print(f"[Odoo] Looking up data for PO: {vs_po_number}")

    # 1. Find the purchase order (no sale_order_id — field doesn't exist in Odoo 19)
    po_records = _call(
        "purchase.order", "search_read",
        [[["name", "=", vs_po_number]]],
        {"fields": ["id", "name"], "limit": 1}
    )
    if not po_records:
        raise ValueError(f"No purchase order found in Odoo for '{vs_po_number}'")

    po = po_records[0]
    po_id = po["id"]
    print(f"[Odoo] Found PO id={po_id}")

    # 2. Get all purchase order lines with per-item weight and product info
    po_lines = _call(
        "purchase.order.line", "search_read",
        [[["order_id", "=", po_id]]],
        {"fields": [
            "id", "vs_article", "aoo_fast_number",
            "original_supplier_article", "sale_line_id",
            "product_uom_qty", "name", "product_id",
            "grade", "choice", "form", "finish", "coating",
            "width", "thickness",
        ]}
    )

    # Batch-fetch product details for grade / dimensions.
    # product_id is a many2one field → [id, display_name].
    # We also try custom fields that VS may have set up on the product template.
    product_ids = [
        l["product_id"][0]
        for l in po_lines
        if l.get("product_id") and isinstance(l["product_id"], list)
    ]
    product_detail: dict[int, dict] = {}
    if product_ids:
        try:
            rows = _call(
                "product.product", "read",
                [list(set(product_ids))],
                {"fields": [
                    "id", "name",
                    # Common custom-field names VS might use — graceful fallback if absent
                    "x_grade", "x_studio_grade",
                    "x_width_mm", "x_studio_width_mm",
                    "x_thickness_mm", "x_studio_thickness_mm",
                    "x_coating", "x_studio_coating",
                    "x_steelmaking", "x_studio_steelmaking",
                    "x_material_type", "x_studio_material_type",
                ]}
            )
            for r in rows:
                product_detail[r["id"]] = r
        except Exception as e:
            print(f"[Odoo] WARNING: Could not fetch product details: {e}")

    def _prod_field(prod: dict, *keys):
        """Return first non-empty value from a list of field alternatives."""
        for k in keys:
            v = prod.get(k)
            if v and v is not False:
                return str(v).strip()
        return ""

    # Build vs_articles — all lines that have a VSI article.
    # Weight-based matching in po_log.py decides which items belong to this cert.
    vs_articles = []
    for l in po_lines:
        if not l.get("vs_article"):
            continue

        prod_id   = (l.get("product_id") or [None])[0]
        prod      = product_detail.get(prod_id, {})
        prod_name = prod.get("name", "") or (l.get("product_id") or [None, ""])[1] or ""

        vs_articles.append({
            "vs_article":                l.get("vs_article") or "–",
            "aoo_fast_number":           l.get("aoo_fast_number"),
            "original_supplier_article": l.get("original_supplier_article"),
            "weight_t":                  l.get("product_uom_qty"),    # tonnes per item
            # Description: prefer PO line name (purchaser-entered), fall back to product name
            "description":               (l.get("name") or prod_name or "").strip(),
            "product_name":              prod_name,
            # Per-item details — prefer PO line fields, fallback to product custom fields
            "grade":        str(l.get("grade") or "").strip() or _prod_field(prod, "x_grade", "x_studio_grade"),
            "quality":      str(l.get("choice") or "").strip(),   # Odoo field 'choice' = quality/yield class
            "width_mm":     str(l.get("width") or "").strip() or _prod_field(prod, "x_width_mm", "x_studio_width_mm"),
            "thickness_mm": str(l.get("thickness") or "").strip() or _prod_field(prod, "x_thickness_mm", "x_studio_thickness_mm"),
            "form":         str(l.get("form") or "").strip(),
            "finish":       str(l.get("finish") or "").strip(),
            "coating":      str(l.get("coating") or "").strip() or _prod_field(prod, "x_coating", "x_studio_coating"),
            "steelmaking":  _prod_field(prod, "x_steelmaking", "x_studio_steelmaking"),
            "material_type": _prod_field(prod, "x_material_type", "x_studio_material_type"),
        })

    print(f"[Odoo] Found {len(vs_articles)} order line(s) with VSI articles.")
    if vs_articles:
        # Debug: show what Odoo product data looks like so we can tune field names
        sample = vs_articles[0]
        print(f"[Odoo] Sample line — description: '{sample['description']}' | "
              f"grade: '{sample['grade']}' | quality: '{sample['quality']}' | "
              f"w×t: {sample['width_mm']}×{sample['thickness_mm']}")

    # 3. Get the linked Sales Order via sale_line_id on PO lines
    so_number = ""
    buyer_name = ""
    buyer_country = ""

    for line in po_lines:
        if line.get("sale_line_id"):
            sale_line_id = line["sale_line_id"][0]
            # sale.order.line → order_id gives the SO
            sol = _call(
                "sale.order.line", "read", [[sale_line_id]],
                {"fields": ["order_id"]}
            )
            if sol and sol[0].get("order_id"):
                so_id = sol[0]["order_id"][0]
                so_records = _call(
                    "sale.order", "read", [[so_id]],
                    {"fields": ["name", "partner_id"]}
                )
                if so_records:
                    so_number  = so_records[0].get("name", "")
                    partner_id = so_records[0].get("partner_id", [None])[0]
                    if partner_id:
                        partners = _call(
                            "res.partner", "read", [[partner_id]],
                            {"fields": ["name", "country_id"]}
                        )
                        if partners:
                            buyer_name    = partners[0].get("name", "")
                            country_field = partners[0].get("country_id")
                            buyer_country = country_field[1] if country_field else ""
                break  # Found SO from first matched line — stop

    print(f"[Odoo] SO={so_number}, Buyer={buyer_name}, Country={buyer_country}")

    return {
        "so_number":    so_number,
        "buyer_name":   buyer_name,
        "buyer_country": buyer_country,
        "vs_articles":  vs_articles,
    }


def find_po_for_cert(cert_signals: dict) -> tuple:
    """
    When no PO number appears on a supplier cert, search all confirmed PO lines
    from the last 60 days and score them against the cert's signals.

    cert_signals keys:
        weight_kg    — total cert weight in kg (float)
        grade        — grade string from cert/Docsumo
        material_type— material description
        quality      — quality/choice string
        width_mm     — width as string
        thickness_mm — thickness as string

    Returns:
        (best_po_number, best_score, candidates)
        candidates: list of (po_number, score, vsi_article) sorted by score desc
                    only candidates scoring >= 4 are included
    """
    since = (datetime.utcnow() - timedelta(days=60)).strftime("%Y-%m-%d %H:%M:%S")

    # Step 1: IDs of all confirmed POs in the last 60 days
    po_ids = _call(
        "purchase.order", "search",
        [[["state", "in", ["purchase", "done"]], ["date_order", ">=", since]]]
    )
    if not po_ids:
        print("[Odoo] No confirmed POs in last 60 days — cannot auto-match.")
        return None, 0, []

    # Step 2: All PO lines for those POs that have a VSI article
    lines = _call(
        "purchase.order.line", "search_read",
        [[["order_id", "in", po_ids], ["vs_article", "!=", False]]],
        {"fields": [
            "id", "order_id", "vs_article",
            "product_uom_qty",                    # weight in tonnes
            "grade", "choice", "form", "finish", "coating",
            "width", "thickness",
        ]}
    )
    print(f"[Odoo] Auto-match: scoring {len(lines)} PO line(s) against cert signals...")

    raw_candidates = []
    for line in lines:
        # Normalise field names to match _score_line expectations
        line["weight_t"]     = line.get("product_uom_qty")
        line["width_mm"]     = str(line.get("width")     or "").strip()
        line["thickness_mm"] = str(line.get("thickness") or "").strip()

        s = _score_line(line, cert_signals)
        if s >= 4:
            po_ref = (line["order_id"][1]
                      if isinstance(line.get("order_id"), list)
                      else str(line.get("order_id", "")))
            raw_candidates.append((po_ref, s, line.get("vs_article", "–")))

    # Keep best-scoring line per PO (a PO may have multiple lines)
    best_per_po: dict[str, tuple] = {}
    for po_ref, score, vsi in sorted(raw_candidates, key=lambda x: x[1], reverse=True):
        if po_ref not in best_per_po or best_per_po[po_ref][0] < score:
            best_per_po[po_ref] = (score, vsi)

    candidates = sorted(
        [(po, sc, vsi) for po, (sc, vsi) in best_per_po.items()],
        key=lambda x: x[1], reverse=True,
    )

    if not candidates:
        return None, 0, []

    return candidates[0][0], candidates[0][1], candidates
