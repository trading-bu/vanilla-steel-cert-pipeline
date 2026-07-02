"""
Vanilla Steel -- Certificate Neutralisation API
FastAPI wrapper around pdf_generator.py + odoo_client.py.
Deploy to Railway. Make.com calls POST /generate-cert with Claude-extracted JSON.

Body format flexibility:
- Wrapped:  {"parsed_cert": {...}, "po_number": "..."}   <- original format
- Raw:      {...cert fields directly...}                  <- Make sends Claude text directly

PO number handling:
- VS POs follow pattern P0XXXX (e.g. P01755). Only these trigger explicit Odoo lookup.
- Supplier order numbers fall through to auto-match by weight/grade.
"""
import os
import re
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response

import odoo_client as odoo
from pdf_generator import generate_certificate

app = FastAPI(title="VS Cert Generator")

ODOO_API_KEY = os.environ.get("ODOO_API_KEY", "")
LOGO_PATH    = os.environ.get("LOGO_PATH", "")


@app.on_event("startup")
def startup():
    if ODOO_API_KEY:
        odoo.set_api_key(ODOO_API_KEY)


# ---------------------------------------------------------------------------
# Body parsing
# ---------------------------------------------------------------------------

async def _parse_body(request: Request):
    """
    Returns (parsed_cert_dict, po_number_str).
    Accepts wrapped {"parsed_cert":..., "po_number":...} or raw cert JSON.
    """
    body = await request.json()
    if "parsed_cert" in body:
        parsed = body["parsed_cert"]
        po_num = body.get("po_number") or ""
    else:
        parsed = body
        po_num = body.get("po_number") or ""
    return parsed, po_num


# ---------------------------------------------------------------------------
# PO format helper
# ---------------------------------------------------------------------------

_VS_PO_RE = re.compile(r"^P\d{4,}$", re.IGNORECASE)


def _is_vs_po(s):
    return bool(s and _VS_PO_RE.match(s.strip()))


# ---------------------------------------------------------------------------
# Odoo lookup
# ---------------------------------------------------------------------------

def _odoo_lookup(parsed, po_num):
    """
    Returns (odoo_data, match_type, match_score).
    match_type: "explicit" | "auto_matched" | "unmatched"

    Priority:
      1. VS PO on cert (P0XXXX) -- explicit PO lookup
      2. Auto-match by weight / grade / dimensions
    """
    # 1. VS PO number on cert
    if po_num and _is_vs_po(po_num):
        try:
            data = odoo.get_neutralisation_data(po_num.strip().upper())
            return data, "explicit", 13
        except Exception as exc:
            print("[Odoo] explicit lookup failed for %s: %s" % (po_num, exc))

    # 2. Auto-match by cert signals
    try:
        first_coil = (parsed.get("coils") or [{}])[0]
        signals = {
            "weight_kg":     parsed.get("total_weight_kg"),
            "grade":         parsed.get("grade", ""),
            "material_type": parsed.get("material_type", ""),
            "quality":       parsed.get("quality", ""),
            "width_mm":      first_coil.get("width_mm", ""),
            "thickness_mm":  first_coil.get("thickness_mm", ""),
        }
        matched_po, score, candidates = odoo.find_po_for_cert(signals)
        if matched_po and score >= 8:
            runner_up = candidates[1][1] if len(candidates) > 1 else 0
            if score - runner_up >= 3:
                data = odoo.get_neutralisation_data(matched_po)
                print("[Odoo] Auto-matched %s score=%s" % (matched_po, score))
                return data, "auto_matched", score
        print("[Odoo] No confident match. Top score=%s" % score)
    except Exception as exc:
        print("[Odoo] auto-match error: %s" % exc)

    return {}, "unmatched", 0


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.post("/match-cert")
async def match_cert(request: Request):
    """
    Odoo lookup -- returns JSON with match details.
    Make calls this first to build the Slack notification.
    """
    parsed, po_num = await _parse_body(request)
    odoo_data, match_type, score = _odoo_lookup(parsed, po_num)

    return {
        "match_type":       match_type,
        "match_score":      score,
        "po_number":        po_num or odoo_data.get("po_number", ""),
        "so_number":        odoo_data.get("so_number", ""),
        "buyer_name":       odoo_data.get("buyer_name", ""),
        "buyer_country":    odoo_data.get("buyer_country", ""),
        "odoo_data":        odoo_data,
        "cert_number":      parsed.get("cert_number", ""),
        "grade":            parsed.get("grade", ""),
        "total_weight_kg":  parsed.get("total_weight_kg"),
        "coil_count":       len(parsed.get("coils") or []),
        "needs_slack_input": match_type == "unmatched",
        "warning": (
            "No Odoo match found"
            if match_type == "unmatched" else ""
        ),
    }


@app.post("/generate-cert")
async def generate(request: Request):
    """
    Generate the neutralised PDF.
    Returns PDF bytes as application/pdf.
    """
    parsed, po_num = await _parse_body(request)

    # Validate extraction
    coils = parsed.get("coils") or []
    if not coils:
        raise HTTPException(
            status_code=422,
            detail="Extraction returned no coils. Check the Claude prompt or the source PDF.",
        )
    if not parsed.get("grade") and not parsed.get("material_type"):
        raise HTTPException(
            status_code=422,
            detail="Extraction returned no grade or material type. Cert may need manual processing.",
        )

    odoo_data, match_type, score = _odoo_lookup(parsed, po_num)

    try:
        pdf_bytes = generate_certificate(
            parsed_cert=parsed,
            odoo_data=odoo_data,
            logo_path=LOGO_PATH or None,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail="PDF generation failed: %s" % exc)

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "X-Match-Type":  match_type,
            "X-Match-Score": str(score),
            "X-SO-Number":   odoo_data.get("so_number", ""),
        },
    )


@app.get("/health")
def health():
    return {"status": "ok"}
