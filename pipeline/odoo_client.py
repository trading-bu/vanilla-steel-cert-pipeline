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

    # 2. Get purchase order lines
    po_lines = _call(
        "purchase.order.line", "search_read",
        [[["order_id", "=", po_id]]],
        {"fields": [
            "id", "vs_article", "aoo_fast_number",
            "original_supplier_article", "sale_line_id",
            "product_uom_qty"
        ]}
    )

    # AOO filter: only lines with aoo_fast_number filled
    filtered = [l for l in po_lines if l.get("aoo_fast_number")]
    if not filtered:
        print(f"[Odoo] WARNING: No lines with aoo_fast_number for PO {vs_po_number}. "
              "Including all lines (AOO filter skipped).")
        filtered = po_lines

    vs_articles = [
        {
            "vs_article":           l.get("vs_article") or "–",
            "aoo_fast_number":      l.get("aoo_fast_number"),
            "original_supplier_article": l.get("original_supplier_article"),
            "qty_tonnes":           l.get("product_uom_qty"),
        }
        for l in filtered
    ]
    print(f"[Odoo] Found {len(vs_articles)} order line(s) with AOO fast number.")

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
