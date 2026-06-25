"""
Docsumo API client for the certificate neutralisation pipeline.
Polls for certs in 'reviewing' status, pulls extracted data, marks as processed.
"""
import requests

DOCSUMO_API_KEY  = None   # Set via environment variable
DOCSUMO_BASE_URL = "https://app.docsumo.com/api/v1/eevee/apikey"
CERT_DOC_TYPE    = "others__IfrSa"


def _headers():
    # Full browser-like headers — required for Docsumo's Cloudflare-protected endpoints
    return {
        "apikey":          DOCSUMO_API_KEY,
        "Accept":          "application/json",
        "Accept-Language": "en-US,en;q=0.9",
        "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    }


def set_api_key(key: str):
    global DOCSUMO_API_KEY
    DOCSUMO_API_KEY = key


def list_reviewing_certs(limit: int = 100) -> list[dict]:
    """
    Returns all cert documents currently in 'reviewing' status.
    Docsumo's doc_type_id filter parameter is unreliable (returns 404),
    so we fetch all documents and filter by type + status in Python.
    """
    url = f"{DOCSUMO_BASE_URL}/user/documents/"
    # Do NOT pass doc_type_id as a query param — causes 404
    params = {"limit": limit}
    resp = requests.get(url, headers=_headers(), params=params)

    if resp.status_code == 404:
        # Try alternate endpoint path used by some Docsumo versions
        url = f"{DOCSUMO_BASE_URL}/documents/"
        resp = requests.get(url, headers=_headers(), params=params)

    resp.raise_for_status()

    docs = resp.json().get("data", {}).get("documents", [])
    reviewing = [
        d for d in docs
        if d.get("status") == "reviewing"
        and d.get("type") == CERT_DOC_TYPE
    ]
    print(f"[Docsumo] Fetched {len(docs)} total doc(s), "
          f"{len(reviewing)} cert(s) in 'reviewing' status.")
    # DEBUG: print download-relevant fields
    if reviewing:
        d0 = reviewing[0]
        print(f"[Docsumo][DEBUG] s3_filename   : {d0.get('s3_filename')}")
        print(f"[Docsumo][DEBUG] review_url    : {d0.get('review_url')}")
        print(f"[Docsumo][DEBUG] review_token  : {d0.get('review_token')}")
        print(f"[Docsumo][DEBUG] user_doc_id   : {d0.get('user_doc_id')}")
        print(f"[Docsumo][DEBUG] preview_image : {d0.get('preview_image')}")
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
        # Use explicit None check — don't use `or None` which drops valid 0.0 values
        field = raw.get("Chemical Composition", {}).get(f"{element} actual", {})
        v = field.get("value")
        return v if v is not None else None

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
        # Field was renamed; try new name first, fall back to old for safety
        "heat_number":        (val("Product Details", "Heat / Charge Number")
                               or val("Product Details", "Supplier Coil Number") or ""),
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
            "Nb": chem("Nb"),
        },

        # Mechanical (None if not in cert)
        "mechanical": {
            "reh":   mech("Re actual"),
            "rm":    mech("Rm actual"),
            "a80":   mech("A80 actual"),
        },
    }


def download_cert_pdf(doc_id: str) -> bytes:
    """
    Download the original PDF for a Docsumo document.
    Returns raw PDF bytes.
    """
    # Try the standard download endpoint
    url = f"{DOCSUMO_BASE_URL}/download/{doc_id}/"
    resp = requests.get(url, headers=_headers())

    if resp.status_code == 404:
        # Some Docsumo versions use /documents/{doc_id}/download/
        url = f"{DOCSUMO_BASE_URL}/documents/{doc_id}/download/"
        resp = requests.get(url, headers=_headers())

    resp.raise_for_status()

    # Response may be a JSON wrapper with a URL, or raw PDF bytes
    ct = resp.headers.get("Content-Type", "")
    if "application/json" in ct:
        data = resp.json()
        pdf_url = (
            data.get("data", {}).get("url")
            or data.get("data", {}).get("download_url")
            or data.get("url")
        )
        if not pdf_url:
            raise RuntimeError(f"No PDF URL in Docsumo download response: {data}")
        pdf_resp = requests.get(pdf_url, headers=_headers())
        pdf_resp.raise_for_status()
        return pdf_resp.content

    # Raw PDF bytes
    return resp.content


# Docsumo status is never changed by this pipeline.
# Processed doc_ids are tracked in processed_ids.json instead.
