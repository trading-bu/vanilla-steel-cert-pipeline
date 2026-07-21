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



# Marketing prefixes suppliers prepend to base grades before comparison
_GRADE_PREFIX_RE = re.compile(
    r"^(MAGNELIS|GALV|PREBOND|EXTRAGAL|SENDZIMIR|GALFAN|ALUZINC|ALUSI|ZINCOR|GALFLEX|GALMAG)[\-\s]+",
    re.IGNORECASE,
)


def _strip_grade_prefix(s: str) -> str:
    """Strip supplier marketing prefix before grade comparison.

    'MAGNELIS-S220GD+ZM310' → 'S220GD+ZM310'
    'GALV-DX51D+Z275'       → 'DX51D+Z275'
    """
    return _GRADE_PREFIX_RE.sub("", (s or "").strip())


# Coating designation codes: Z275, ZM310, AZ150, ZA255, AS80 …
_COATING_CODE_RE = re.compile(
    r"\b(ZM\s*\d+|Z\s*\d+|AZ\s*\d+|ZA\s*\d+|AS\s*\d+|ZF\s*\d+)\b",
    re.IGNORECASE,
)


def _extract_coating_code(s: str) -> str:
    """Extract the coating designation from a full coating description.

    'Magnelis ZM310 155/155 g/m²' → 'ZM310'
    'hot dip galv Z275-MA-C'      → 'Z275'
    'DX51D+Z275-M-A-C'            → 'Z275'
    Returns '' when no coating code found.
    """
    m = _COATING_CODE_RE.search(s or "")
    return re.sub(r"\s+", "", m.group(0)).upper() if m else ""


def _score_line(line: dict, cert: dict) -> int:
    """
    Score an Odoo PO aggregate against cert signals.  Max = 15.

    Scoring breakdown:
      weight    max 5   — PO total vs cert total weight
      grade     max 3   — spec token cross-match (prefixes stripped, grade_full pooled)
      coating   max 2   — coating code match (Z275, ZM310 …) as a SEPARATE signal
      choice    max 1   — first/second choice
      width     max 2   — ±2 mm tolerance
      thickness max 2   — ±0.1 mm tolerance

    A cert matching grade + coating + width + thickness (9 pts) reaches the
    auto-match threshold of 8 even when weight is unknown / mismatched,
    which is the correct behaviour for multi-coil deliveries.

    line keys expected : weight_t, grade, choice, form, finish, coating,
                         width_mm, thickness_mm
    cert keys expected : weight_kg, grade, grade_full, material_type, quality,
                         coating, width_mm, thickness_mm
    """
    score = 0

    # ── Weight — max 5 pts ────────────────────────────────────────────────
    odoo_kg = (line.get("weight_t") or 0) * 1000
    cert_kg = cert.get("weight_kg") or 0
    if odoo_kg > 0 and cert_kg > 0:
        pct = abs(odoo_kg - cert_kg) / odoo_kg
        if   pct <= 0.02: score += 5
        elif pct <= 0.05: score += 3
        elif pct <= 0.10: score += 1

    # ── Grade / spec cross-match — max 3 pts ──────────────────────────────
    # Strip marketing prefixes on both sides, then pool tokens from all
    # spec-like fields. grade_full (e.g. "DX51D+Z275-M-A-C") is included
    # from the cert side so the base grade still matches even with suffixes.
    odoo_tok: set = set()
    for f in ("grade", "form", "finish", "coating"):
        odoo_tok |= _spec_tokens(_strip_grade_prefix(line.get(f) or ""))

    cert_tok: set = set()
    for f in ("grade", "grade_full", "material_type"):
        cert_tok |= _spec_tokens(_strip_grade_prefix(cert.get(f) or ""))

    if odoo_tok and cert_tok and (odoo_tok & cert_tok):
        score += 3

    # ── Coating code — max 2 pts ──────────────────────────────────────────
    # Separate signal: extract the coating designation (Z275, ZM310 …) from
    # the full coating description on each side and compare directly.
    cert_coat = _extract_coating_code(cert.get("coating") or cert.get("quality") or "")
    odoo_coat = _extract_coating_code(line.get("coating") or "")
    if cert_coat and odoo_coat and cert_coat == odoo_coat:
        score += 2

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

    # 1. Find the purchase order.
    #    VS PO numbers (P0XXXX) are the name of vs.deal records.
    #    vs.deal has a purchase_order_id many2one → purchase.order.
    #    So: search vs.deal by name → get the linked purchase.order id.
    po_id   = None
    po_name = None

    deal_records = _call(
        "vs.deal", "search_read",
        [[["name", "=", vs_po_number]]],
        {"fields": ["id", "name", "purchase_order_id"], "limit": 1}
    )
    if deal_records:
        deal = deal_records[0]
        print(f"[Odoo] Found vs.deal: id={deal['id']} name={deal['name']} "
              f"purchase_order_id={deal.get('purchase_order_id')}")
        if deal.get("purchase_order_id"):
            po_id   = deal["purchase_order_id"][0]
            po_name = deal["purchase_order_id"][1]
    else:
        print(f"[Odoo] No vs.deal found for name='{vs_po_number}', trying purchase.order.name directly.")

    # Fallback: search purchase.order by name directly
    if not po_id:
        po_records_fallback = _call(
            "purchase.order", "search_read",
            [[["name", "=", vs_po_number]]],
            {"fields": ["id", "name"], "limit": 1}
        )
        if po_records_fallback:
            po_id   = po_records_fallback[0]["id"]
            po_name = po_records_fallback[0]["name"]
            print(f"[Odoo] Found PO by name: id={po_id} name={po_name}")

    if not po_id:
        raise ValueError(
            f"No record found in Odoo for '{vs_po_number}'. "
            f"Searched vs.deal.name and purchase.order.name."
        )

    print(f"[Odoo] Using PO id={po_id} name={po_name}")

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
            # Fetch standard product fields only — avoid custom x_ fields that may not exist
            rows = _call(
                "product.product", "read",
                [list(set(product_ids))],
                {"fields": ["id", "name"]}
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

        qty = l.get("product_uom_qty") or 0
        vs_articles.append({
            "vs_article":                l.get("vs_article") or "–",
            "aoo_fast_number":           l.get("aoo_fast_number"),
            "original_supplier_article": l.get("original_supplier_article"),
            "weight_t":                  qty,    # tonnes per item
            # Flag placeholder/cancelled lines — zero ordered qty should not auto-match
            "confidence":                "LOW_CONFIDENCE" if qty == 0 else "OK",
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

    # 3. Collect all sale_line_ids from PO lines (needed for delivery note matching)
    sale_line_ids = [
        l["sale_line_id"][0]
        for l in po_lines
        if l.get("sale_line_id") and isinstance(l["sale_line_id"], list)
    ]

    # 4. Get the linked Sales Order via sale_line_id on PO lines
    so_number     = ""
    so_id         = None
    buyer_name    = ""
    buyer_country = ""
    buyer_address = ""
    dest_name     = ""
    dest_address  = ""
    vs_reference  = ""
    customer_po   = ""
    delivery_note = ""

    for line in po_lines:
        if line.get("sale_line_id"):
            sale_line_id = line["sale_line_id"][0]
            sol = _call(
                "sale.order.line", "read", [[sale_line_id]],
                {"fields": ["order_id"]}
            )
            if sol and sol[0].get("order_id"):
                so_id = sol[0]["order_id"][0]
                so_records = _call(
                    "sale.order", "read", [[so_id]],
                    {"fields": [
                        "name",                # S01501 — the SO number
                        "partner_id",          # billing/consignee contact
                        "partner_shipping_id", # shipping address (delivery point)
                        "client_order_ref",    # customer's own PO number to VS
                        "deal_reference",      # VSO-XXXX — the VS deal/reference number
                    ]}
                )
                if so_records:
                    so   = so_records[0]
                    so_number    = so.get("name", "")
                    customer_po  = so.get("client_order_ref") or ""
                    vs_reference = so.get("deal_reference") or ""  # VSO-XXXX

                    billing_partner_id  = (so.get("partner_id") or [None])[0]
                    shipping_partner_id = (so.get("partner_shipping_id") or [None])[0]

                    # ── Consignee = billing partner (company VS sold to) ────────
                    if billing_partner_id:
                        partners = _call(
                            "res.partner", "read", [[billing_partner_id]],
                            {"fields": ["name", "street", "street2",
                                        "city", "zip", "country_id",
                                        "is_company", "parent_id"]}
                        )
                        if partners:
                            p = partners[0]
                            # If this is an individual contact, prefer their parent company
                            if not p.get("is_company") and p.get("parent_id"):
                                parent_id = p["parent_id"][0]
                                parent = _call(
                                    "res.partner", "read", [[parent_id]],
                                    {"fields": ["name", "street", "street2",
                                                "city", "zip", "country_id"]}
                                )
                                if parent:
                                    p = parent[0]
                            buyer_name    = p.get("name", "")
                            country_field = p.get("country_id")
                            buyer_country = country_field[1] if country_field else ""
                            parts = [
                                p.get("street") or "",
                                p.get("street2") or "",
                                " ".join(filter(None, [p.get("zip", ""), p.get("city", "")])),
                                buyer_country,
                            ]
                            buyer_address = ", ".join(x for x in parts if x)

                    # ── Destination = shipping address (if different from billing) ──
                    if shipping_partner_id and shipping_partner_id != billing_partner_id:
                        partners = _call(
                            "res.partner", "read", [[shipping_partner_id]],
                            {"fields": ["name", "street", "street2",
                                        "city", "zip", "country_id", "parent_id"]}
                        )
                        if partners:
                            p = partners[0]
                            country_field = p.get("country_id")
                            dest_country  = country_field[1] if country_field else ""
                            parts = [
                                p.get("street") or "",
                                p.get("street2") or "",
                                " ".join(filter(None, [p.get("zip", ""), p.get("city", "")])),
                                dest_country,
                            ]
                            dest_name    = p.get("name", "")
                            dest_address = ", ".join(x for x in parts if x)
                    else:
                        # Shipping same as billing — destination = consignee
                        dest_name    = buyer_name
                        dest_address = buyer_address
                break  # Found SO — stop

    # 5. Delivery note: find done stock.picking records that contain our VSI lines
    if so_id and sale_line_ids:
        try:
            # stock.move links sale_line_id → picking_id
            moves = _call(
                "stock.move", "search_read",
                [[["sale_line_id", "in", sale_line_ids],
                  ["state", "=", "done"]]],
                {"fields": ["picking_id", "sale_line_id"]}
            )
            picking_ids = list({
                m["picking_id"][0]
                for m in moves
                if m.get("picking_id") and isinstance(m["picking_id"], list)
            })
            if picking_ids:
                pickings = _call(
                    "stock.picking", "read",
                    [picking_ids],
                    {"fields": ["name", "state", "date_done"]}
                )
                done = [p["name"] for p in pickings if p.get("state") == "done"]
                delivery_note = ", ".join(sorted(done))
        except Exception as e:
            print(f"[Odoo] WARNING: Could not fetch delivery notes: {e}")

    print(f"[Odoo] SO={so_number} | VSO={vs_reference} | "
          f"Buyer={buyer_name} | Delivery={delivery_note or '(pending)'}")

    return {
        "po_number":      vs_po_number,   # VS PO number this data was fetched for
        "so_number":      so_number,
        "vs_reference":   vs_reference,   # = so_number (the VS SO ref)
        "customer_po":    customer_po,    # = client_order_ref (buyer's PO to VS)
        "buyer_name":     buyer_name,     # consignee company name
        "buyer_country":  buyer_country,
        "buyer_address":  buyer_address,  # consignee address
        "dest_name":      dest_name,      # destination name (shipping address)
        "dest_address":   dest_address,   # destination address
        "delivery_note":  delivery_note,
        "vs_articles":    vs_articles,
    }




def find_po_for_cert(cert_signals: dict) -> tuple:
    """
    When no PO number appears on a supplier cert, search all confirmed PO lines
    from the last 60 days and score each PO (not individual lines) against the
    cert signals.

    Key fix: cert total weight is compared against the PO TOTAL weight
    (sum of all lines), not individual line weights. A cert typically covers
    one full PO, so cert total ~ PO total.

    Returns (best_po_number, best_score, candidates)
    """
    since = (datetime.utcnow() - timedelta(days=60)).strftime("%Y-%m-%d %H:%M:%S")

    po_ids = _call(
        "purchase.order", "search",
        [[["state", "in", ["purchase", "done"]], ["date_order", ">=", since]]]
    )
    if not po_ids:
        print("[Odoo] No confirmed POs in last 60 days -- cannot auto-match.")
        return None, 0, []

    lines = _call(
        "purchase.order.line", "search_read",
        [[["order_id", "in", po_ids], ["vs_article", "!=", False]]],
        {"fields": [
            "id", "order_id", "vs_article",
            "product_uom_qty",
            "grade", "choice", "form", "finish", "coating",
            "width", "thickness",
        ]}
    )
    print("[Odoo] Auto-match: %d PO line(s) across %d POs" % (len(lines), len(po_ids)))

    # Group lines by PO, sum weights, keep first line's specs as representative
    po_agg = {}
    for line in lines:
        if isinstance(line.get("order_id"), list):
            po_ref = line["order_id"][1]
        else:
            po_ref = str(line.get("order_id", ""))

        if po_ref not in po_agg:
            po_agg[po_ref] = {
                "weight_t":    0.0,
                "grade":       line.get("grade") or "",
                "choice":      line.get("choice") or "",
                "form":        line.get("form") or "",
                "finish":      line.get("finish") or "",
                "coating":     line.get("coating") or "",
                "width_mm":    str(line.get("width") or "").strip(),
                "thickness_mm": str(line.get("thickness") or "").strip(),
                "first_vsi":   line.get("vs_article") or "-",
            }
        po_agg[po_ref]["weight_t"] += float(line.get("product_uom_qty") or 0)

    # Score each PO against the cert signals
    candidates = []
    for po_ref, agg in po_agg.items():
        s = _score_line(agg, cert_signals)
        if s >= 4:
            candidates.append((po_ref, s, agg["first_vsi"]))

    candidates.sort(key=lambda x: x[1], reverse=True)
    print("[Odoo] Top candidates: %s" % str(candidates[:3]))

    if not candidates:
        return None, 0, []

    return candidates[0][0], candidates[0][1], candidates
