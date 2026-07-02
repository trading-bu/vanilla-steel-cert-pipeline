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
                    ]}
                )
                if so_records:
                    so   = so_records[0]
                    so_number    = so.get("name", "")
                    customer_po  = so.get("client_order_ref") or ""
                    vs_reference = so_number  # SO name IS the VS reference

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

    po_ids = _call(
        "purchase.order", "search",
        [[["state", "in", ["purchase", "done"]], ["date_order", ">=", since]]]
    )
    if not po_ids:
        print("[Odoo] No confirmed POs in last 60 days — cannot auto-match.")
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
    print(f"[Odoo] Auto-match: scoring {len(lines)} PO line(s) against cert signals...")

    raw_candidates = []
    for line in lines:
        line["weight_t"]     = line.get("product_uom_qty")
        line["width_mm"]     = str(line.get("width")     or "").strip()
        line["thickness_mm"] = str(line.get("thickness") or "").strip()
        s = _score_line(line, cert_signals)
        if s >= 4:
            po_ref = (line["order_id"][1]
                      if isinstance(line.get("order_id"), list)
                      else str(line.get("order_id", "")))
            raw_candidates.append((po_ref, s, line.get("vs_article", "–")))

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


# ---------------------------------------------------------------------------
# Coil-level matching
# ---------------------------------------------------------------------------

def match_coils_to_vsi(
    cert_coils,
    vs_articles,
    explicit_vsi_ids=None,
    cert_meta=None,
):
    """
    Match supplier cert coils to VS internal articles (VSI IDs) on the Odoo order.

    Mode 1 — Direct (no explicit_vsi_ids):
      Each cert coil is paired 1-to-1 with a VSI article by exact weight (rounded kg).
      Grade / thickness / coating are verified when both sides carry values.

    Mode 2 — Parent-slit (explicit_vsi_ids provided):
      Sum the weights of the explicitly listed VSI articles.
      Find the cert coil whose weight equals that sum.
      Verify grade / thickness / coating.
      Every VSI article inherits the parent coil's chemistry + mechanical data,
      with coil_no replaced by the VSI ID.

    Returns dict with keys:
      matched_cert_coils   — coil dicts to include in the output PDF
      matched_vsi_ids      — VSI IDs successfully matched
      unmatched_cert_coils — cert coils excluded from output (Rule 1)
      unmatched_vsi_ids    — VSI IDs not matched by any cert coil (Rule 2 — report in Slack)
    """
    cert_meta = cert_meta or {}

    def _vsi_kg(vsi):
        w = vsi.get("weight_t") or 0
        return round(float(w) * 1000)

    def _coil_kg(coil):
        v = coil.get("weight_kg") or coil.get("gross_weight_kg") or 0
        return round(float(v))

    def _norm_val(v):
        return str(v).strip().upper() if v else ""

    def _qualifies(cert_coil, vsi_list):
        """
        Check grade / thickness / coating compatibility between a cert coil
        and a list of VSI articles. Uses cert_meta as fallback for coil-level
        fields that may be absent on individual coil dicts.
        """
        # Collect cert-side values (coil overrides cert_meta)
        c_grade = _norm_val(cert_coil.get("grade") or cert_meta.get("grade", ""))
        c_thick = _norm_val(cert_coil.get("thickness_mm") or "")
        c_coat  = _norm_val(
            cert_coil.get("coating") or cert_meta.get("coating", "")
        )

        for vsi in vsi_list:
            v_grade = _norm_val(vsi.get("grade", ""))
            v_thick = _norm_val(vsi.get("thickness_mm", ""))
            v_coat  = _norm_val(vsi.get("coating", ""))

            if c_grade and v_grade and c_grade != v_grade:
                return False
            if c_thick and v_thick and c_thick != v_thick:
                return False
            if c_coat and v_coat and c_coat != v_coat:
                return False
        return True

    # -- empty inputs: return everything unmatched ---------------------------
    if not vs_articles:
        return {
            "matched_cert_coils":   cert_coils,
            "matched_vsi_ids":      [],
            "unmatched_cert_coils": [],
            "unmatched_vsi_ids":    [],
        }

    if not cert_coils:
        return {
            "matched_cert_coils":   [],
            "matched_vsi_ids":      [],
            "unmatched_cert_coils": [],
            "unmatched_vsi_ids":    [v.get("vs_article", "") for v in vs_articles],
        }

    matched_cert_coils   = []
    matched_vsi_ids      = []
    unmatched_cert_coils = []
    used_coil_indices    = set()

    # -----------------------------------------------------------------------
    # MODE 2: explicit VSI IDs provided (parent-slit relationship)
    # -----------------------------------------------------------------------
    if explicit_vsi_ids:
        # Build lookup: vsi_id -> vsi dict
        vsi_map = {v.get("vs_article", ""): v for v in vs_articles}

        # Only consider the VSI IDs explicitly provided
        explicit_vsis = [vsi_map[vid] for vid in explicit_vsi_ids if vid in vsi_map]
        if not explicit_vsis:
            # None of the explicit IDs found in vs_articles -- treat as unmatched
            return {
                "matched_cert_coils":   [],
                "matched_vsi_ids":      [],
                "unmatched_cert_coils": list(cert_coils),
                "unmatched_vsi_ids":    list(explicit_vsi_ids),
            }

        sum_kg = sum(_vsi_kg(v) for v in explicit_vsis)

        # Find the cert coil whose weight matches the sum
        parent_coil = None
        parent_idx  = None
        for i, coil in enumerate(cert_coils):
            if _coil_kg(coil) == sum_kg and _qualifies(coil, explicit_vsis):
                parent_coil = coil
                parent_idx  = i
                break

        if parent_coil is not None:
            used_coil_indices.add(parent_idx)
            for vsi in explicit_vsis:
                # Each slit VSI inherits parent coil chemistry + mechanical data
                slit_entry = dict(parent_coil)
                slit_entry["coil_no"]     = vsi.get("vs_article", parent_coil.get("coil_no", ""))
                slit_entry["weight_kg"]   = _vsi_kg(vsi)
                matched_cert_coils.append(slit_entry)
                matched_vsi_ids.append(vsi.get("vs_article", ""))

        # Any cert coils not used -> unmatched
        for i, coil in enumerate(cert_coils):
            if i not in used_coil_indices:
                unmatched_cert_coils.append(coil)

        # VSI IDs not in explicit list are simply not part of this call
        all_vsi_ids   = {v.get("vs_article", "") for v in vs_articles}
        explicit_set  = set(explicit_vsi_ids)
        unmatched_vsi = sorted(all_vsi_ids - set(matched_vsi_ids))

        return {
            "matched_cert_coils":   matched_cert_coils,
            "matched_vsi_ids":      matched_vsi_ids,
            "unmatched_cert_coils": unmatched_cert_coils,
            "unmatched_vsi_ids":    unmatched_vsi,
        }

    # -----------------------------------------------------------------------
    # MODE 1: direct 1-to-1 matching by exact weight
    # -----------------------------------------------------------------------
    # Build kg -> [vsi] lookup (multiple VSIs can share the same weight)
    vsi_by_kg = {}
    for vsi in vs_articles:
        kg = _vsi_kg(vsi)
        vsi_by_kg.setdefault(kg, []).append(vsi)

    used_vsi_ids = set()

    for i, coil in enumerate(cert_coils):
        kg = _coil_kg(coil)
        candidates_for_weight = vsi_by_kg.get(kg, [])
        # Pick the first unused VSI at this weight that passes qualification
        matched_vsi = None
        for vsi in candidates_for_weight:
            vid = vsi.get("vs_article", "")
            if vid not in used_vsi_ids and _qualifies(coil, [vsi]):
                matched_vsi = vsi
                break

        if matched_vsi is not None:
            used_coil_indices.add(i)
            used_vsi_ids.add(matched_vsi.get("vs_article", ""))
            matched_cert_coils.append(coil)
            matched_vsi_ids.append(matched_vsi.get("vs_article", ""))
        else:
            unmatched_cert_coils.append(coil)

    all_vsi_ids   = {v.get("vs_article", "") for v in vs_articles}
    unmatched_vsi = sorted(all_vsi_ids - used_vsi_ids)

    return {
        "matched_cert_coils":   matched_cert_coils,
        "matched_vsi_ids":      matched_vsi_ids,
        "unmatched_cert_coils": unmatched_cert_coils,
        "unmatched_vsi_ids":    unmatched_vsi,
    }


# ---------------------------------------------------------------------------
# Slack reply flow: SO direct lookup + VSI article fetch
# ---------------------------------------------------------------------------

def get_so_data(so_number: str) -> dict:
    """
    Fetch buyer / address / delivery note data directly from a VS SO name
    (e.g. 'S01382'). Used in the Slack reply flow when the user supplies the
    SO because no PO was found automatically.

    Returns the same structure as get_neutralisation_data() but with
    vs_articles=[] (caller fills this via get_vsi_article_data()).
    """
    uid, api_key = _authenticate()

    # Resolve SO
    so_ids = _call("sale.order", "search",
                   [[["name", "=", so_number]]])
    if not so_ids:
        raise ValueError("SO not found: %s" % so_number)

    so_list = _call("sale.order", "read",
                    [so_ids],
                    {"fields": ["name", "partner_id", "partner_invoice_id",
                                "partner_shipping_id", "client_order_ref"]})
    so = so_list[0]

    def _partner_name(ref):
        if isinstance(ref, list):
            return ref[1]
        return str(ref) if ref else ""

    def _partner_id(ref):
        if isinstance(ref, list):
            return ref[0]
        return ref

    buyer_partner_id = _partner_id(so.get("partner_invoice_id") or so.get("partner_id"))
    ship_partner_id  = _partner_id(so.get("partner_shipping_id") or so.get("partner_id"))

    def _get_address(pid):
        if not pid:
            return "", "", ""
        pdata = _call("res.partner", "read", [[pid]],
                      {"fields": ["name", "street", "city", "zip",
                                  "country_id", "parent_id"]})
        if not pdata:
            return "", "", ""
        p = pdata[0]
        name = p.get("name", "")
        # Use parent company name if this is a contact
        if p.get("parent_id"):
            parent = _call("res.partner", "read",
                           [[_partner_id(p["parent_id"])]],
                           {"fields": ["name"]})
            if parent:
                name = parent[0].get("name", name)
        parts = [p.get("street", ""), p.get("city", ""), p.get("zip", "")]
        address = ", ".join(x for x in parts if x)
        country = (p["country_id"][1] if isinstance(p.get("country_id"), list)
                   else "")
        return name, address, country

    buyer_name, buyer_address, buyer_country = _get_address(buyer_partner_id)
    dest_name,  dest_address,  _             = _get_address(ship_partner_id)

    # Delivery notes via stock.move -> stock.picking (state=done)
    so_line_ids = _call("sale.order.line", "search",
                        [[["order_id", "=", so_ids[0]]]])
    move_ids = _call("stock.move", "search",
                     [[["sale_line_id", "in", so_line_ids],
                       ["state", "=", "done"]]])
    delivery_note = ""
    if move_ids:
        moves = _call("stock.move", "read", [move_ids[:50]],
                      {"fields": ["picking_id"]})
        pick_ids = list({m["picking_id"][0]
                         for m in moves
                         if isinstance(m.get("picking_id"), list)})
        if pick_ids:
            picks = _call("stock.picking", "read", [pick_ids],
                          {"fields": ["name", "state"]})
            done_picks = [p["name"] for p in picks if p.get("state") == "done"]
            delivery_note = ", ".join(done_picks)

    return {
        "so_number":       so.get("name", so_number),
        "vs_reference":    so.get("name", so_number),
        "customer_po":     so.get("client_order_ref", ""),
        "po_number":       "",
        "buyer_name":      buyer_name,
        "buyer_country":   buyer_country,
        "buyer_address":   buyer_address,
        "dest_name":       dest_name,
        "dest_address":    dest_address,
        "delivery_note":   delivery_note,
        "vs_articles":     [],   # filled by caller via get_vsi_article_data()
    }


def get_vsi_article_data(vsi_ids: list) -> list:
    """
    Fetch VSI article attributes (weight, grade, thickness, coating, etc.)
    from Odoo purchase order lines.

    Used in the Slack reply flow when the user provides explicit VSI IDs
    so that Mode 2 matching can sum their weights and find the parent coil.

    Returns list of dicts:
      {vs_article, weight_t, grade, quality, width_mm, thickness_mm,
       form, finish, coating}
    """
    if not vsi_ids:
        return []

    lines = _call(
        "purchase.order.line", "search_read",
        [[["x_vs_article", "in", vsi_ids]]],
        {"fields": ["x_vs_article", "x_weight", "x_grade", "x_quality",
                    "x_width", "x_thickness", "x_form", "x_finish", "x_coating"]},
    )

    # Deduplicate by VSI ID (keep last / most recent)
    seen = {}
    for line in lines:
        vid = line.get("x_vs_article", "")
        if vid:
            seen[vid] = {
                "vs_article":   vid,
                "weight_t":     line.get("x_weight") or 0,
                "grade":        line.get("x_grade", ""),
                "quality":      line.get("x_quality", ""),
                "width_mm":     line.get("x_width", ""),
                "thickness_mm": line.get("x_thickness", ""),
                "form":         line.get("x_form", ""),
                "finish":       line.get("x_finish", ""),
                "coating":      line.get("x_coating", ""),
            }

    return list(seen.values())
 vsi_ids:
        l = seen.get(vid)
        if l:
            result.append({
                "vs_article":   vid,
                "weight_t":     l.get("product_uom_qty"),
                "grade":        str(l.get("grade") or "").strip(),
                "quality":      str(l.get("choice") or "").strip(),
                "width_mm":     str(l.get("width") or "").strip(),
                "thickness_mm": str(l.get("thickness") or "").strip(),
                "form":         str(l.get("form") or "").strip(),
                "finish":       str(l.get("finish") or "").strip(),
                "coating":      str(l.get("coating") or "").strip(),
            })
        else:
            print(f"[Odoo] WARNING: VSI '{vid}' not found in any PO line")
            result.append({"vs_article": vid, "weight_t": None})
    return result
