"""
Docsumo API client for the certificate neutralisation pipeline.
Polls for certs in 'reviewing' status, pulls extracted data, marks as processed.
"""
import requests

DOCSUMO_API_KEY  = None   # Set via environment variable
DOCSUMO_BASE_URL = "https://app.docsumo.com/api/v1/eevee/apikey"
CERT_DOC_TYPE    = "others__IfrSa"


def _headers():
    return {
        "apikey": DOCSUMO_API_KEY,
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0",
    }


def set_api_key(key: str):
    global DOCSUMO_API_KEY
    DOCSUMO_API_KEY = key


def list_reviewing_certs(limit: int = 50) -> list[dict]:
    """
    Returns all cert documents currently in 'reviewing' status.
    These are certs the user has checked and are ready to neutralise.
    """
    # Docsumo doesn't expose a reliable filter-by-status list endpoint
    # via the public API, so we page through all docs and filter locally.
    url = f"{DOCSUMO_BASE_URL}/user/documents/"
    params = {"doc_type_id": CERT_DOC_TYPE, "limit": limit}
    resp = requests.get(url, headers=_headers(), params=params)
    resp.raise_for_status()

    docs = resp.json().get("data", {}).get("documents", [])
    reviewing = [d for d in docs if d.get("status") == "reviewing"]
    print(f"[Docsumo] Found {len(reviewing)} cert(s) in 'reviewing' status.")
    return reviewing


def get_cert_data(doc_id: str) -> dict:
    """
    Pulls the extracted field values for a certificate.
    Returns a structured dict with all extracted values.
    """
    url = f"{DOCSUMO_BASE_URL}/data/simplified/{doc_id}/"
    resp = requests.get(url, headers=_headers())
    resp.raise_for_status()

    raw = resp.json().get("data", {})
    return _parse_cert_data(raw)


def _parse_cert_data(raw: dict) -> dict:
    """
    Normalises the Docsumo simplified JSON into a clean dict
    the rest of the pipeline can use without knowing Docsumo's schema.
    """
    def val(section, field):
        return raw.get(section, {}).get(field, {}).get("value", "") or ""

    def chem(element):
        return raw.get("Chemical Composition", {}).get(f"{element} actual", {}).get("value") or None

    def mech(field):
        return raw.get("Mechanical Properties", {}).get(field, {}).get("value") or None

    # Weight: Docsumo may return 18.846 (European thousands sep) meaning 18,846 kg
    raw_weight = val("Product Details", "Weight")
    try:
        weight_kg = float(str(raw_weight).replace(",", "."))
        # If value looks like tonnes (< 500), convert to kg
        if weight_kg < 500:
            weight_kg = weight_kg * 1000
    except (ValueError, TypeError):
        weight_kg = None

    return {
        # Administrative
        "vs_po_number":       val("Basic Information", "Vanilla Steel Order Number"),
        "cert_number":        val("Basic Information", "Certificate Number"),
        "cert_date":          val("Basic Information", "Date"),
        "cert_type":          val("Basic Information", "Certification Type") or "EN 10204 3.1",
        "delivery_note":      val("Basic Information", "Delivery Note Number"),
        "supplier_conf":      val("Basic Information", "Supplier Order Number"),

        # Supplier & buyer
        "supplier_name":      val("Contact Information", "Company Name"),
        "supplier_address":   val("Contact Information", "Company Address"),
        "inspector":          val("Basic Information", "Quality Control Manager"),

        # Material
        "grade":              val("Product Details", "Grade"),
        "material_type":      val("Product Details", "Material Type"),
        "dimensions":         val("Product Details", "Dimensions"),
        "heat_number":        val("Product Details", "Supplier Coil Number") or "",
        "weight_kg":          weight_kg,

        # Chemicals (all as floats or None)
        "chemicals": {
            "C":  chem("C"),
            "Si": chem("Si"),
            "Mn": chem("Mn"),
            "P":  chem("P"),
            "S":  chem("S"),
            "Cr": chem("Cr"),
            "Ni": chem("Ni"),
            "Mo": chem("Mo"),
            "Cu": chem("Cu"),
            "Al": chem("Al"),
            "B":  chem("B"),
            "Ti": chem("Ti"),
            "V":  chem("V"),
        },

        # Mechanical (None if not in cert)
        "mechanical": {
            "reh":   mech("Re actual"),
            "rm":    mech("Rm actual"),
            "a80":   mech("A80 actual"),
        },
    }


# Docsumo status is never changed by this pipeline.
# Processed doc_ids are tracked in processed_ids.json instead.
