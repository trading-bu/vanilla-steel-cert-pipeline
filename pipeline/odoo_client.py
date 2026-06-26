"""
Odoo XML-RPC client for the certificate neutralisation pipeline.
Looks up Sales Order, buyer country, and VS article numbers from a VS PO number.
"""
import xmlrpc.client

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
            "grade", "choice", "width", "thickness",
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
            "coating":      _prod_field(prod, "x_coating", "x_studio_coating"),
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
