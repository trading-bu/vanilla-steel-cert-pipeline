"""
Vanilla Steel -- Certificate Neutralisation API
FastAPI wrapper around pdf_generator.py + odoo_client.py.
Deploy to Railway. Make.com calls POST /generate-cert with Claude-extracted JSON.

Body format flexibility:
- Wrapped:  {"parsed_cert": {...}, "po_number": "..."}   <- original format
- Raw:      {...cert fields directly...}                  <- Make can send Claude text directly

PO number handling:
- VS POs follow pattern P0XXXX (e.g. P01755). Only these trigger explicit Odoo lookup.
- Supplier order numbers fall through to auto-match by weight/grade.

Slack reply flow (for unmatched certs):
- Make stores extracted cert JSON in a Data Store keyed by cert_number.
- User replies with so_number_override + optional explicit_vsi_ids.
- Make retrieves JSON from Data Store, re-calls /match-cert and /generate-cert with those fields.
- Coil matching runs in Mode 2 (parent-slit sum) when explicit_vsi_ids are provided.

Coil-level matching (applied whenever a SO is found):
- Mode 1 (direct): cert coil weight == VSI article weight (exact kg) + grade/thickness/coating
- Mode 2 (parent-slit): sum(VSI weights) == parent cert coil weight + grade/thickness/coating
- Cert coils not matched to any VSI -> excluded from output cert (Rule 1)
- VSI articles not matched by any cert coil -> reported in Slack via X-Unmatched-VSI header (Rule 2)
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
    Returns (parsed_cert_dict, po_number_str, explicit_vsi_ids, so_number_override).
    Accepts wrapped {"parsed_cert":..., "po_number":...} or raw cert JSON.
    """
    body = await request.json()
    if "parsed_cert" in body:
        parsed = body["parsed_cert"]
        po_num = body.get("po_number") or ""
    else:
        parsed = body
        po_num = body.get("po_number") or ""

    explicit_vsi_ids   = body.get("explicit_vsi_ids") or []
    so_number_override = body.get("so_number_override") or ""

    return parsed, po_num, explicit_vsi_ids, so_number_override


# ---------------------------------------------------------------------------
# PO / SO format helpers
# ---------------------------------------------------------------------------

_VS_PO_RE = re.compile(r"^P\d{4,}$", re.IGNORECASE)
_VS_SO_RE = re.compile(r"^S\d{4,}$", re.IGNORECASE)


def _is_vs_po(s):
    return bool(s and _VS_PO_RE.match(s.strip()))


def _is_vs_so(s):
    return bool(s and _VS_SO_RE.match(s.strip()))


# ---------------------------------------------------------------------------
# Odoo lookup
# ---------------------------------------------------------------------------

def _odoo_lookup(parsed, po_num, explicit_vsi_ids=None, so_number_override=""):
    """
    Returns (odoo_data, match_type, match_score).
    match_type: "explicit_so" | "explicit" | "auto_matched" | "unmatched"

    Priority:
      1. so_number_override (Slack reply) -- fetch SO directly + VSI article details
      2. VS PO on cert (P0XXXX) -- explicit PO lookup
      3. Auto-match by weight / grade / dimensions
    """
    # 1. Slack reply: user provided SO + optional VSI IDs
    if so_number_override and _is_vs_so(so_number_override):
        try:
            data = odoo.get_so_data(so_number_override.strip().upper())
            if explicit_vsi_ids:
                data["vs_articles"] = odoo.get_vsi_article_data(explicit_vsi_ids)
            return data, "explicit_so", 13
        except Exception as exc:
            print("[Odoo] SO override lookup failed for %s: %s" % (so_number_override, exc))

    # 2. VS PO number on cert
    if po_num and _is_vs_po(po_num):
        try:
            data = odoo.get_neutralisation_data(po_num.strip().upper())
            return data, "explicit", 13
        except Exception as exc:
            print("[Odoo] explicit lookup failed for %s: %s" % (po_num, exc))

    # 3. Auto-match by cert signals
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
# Coil matching helper
# ---------------------------------------------------------------------------

def _run_coil_matching(parsed, odoo_data, explicit_vsi_ids, match_type):
    """
    Passes cert-level grade/coating/material_type as cert_meta so coils without
    those fields can still participate in grade/coating qualification checks.
    """
    cert_meta = {
        "grade":         parsed.get("grade", ""),
        "material_type": parsed.get("material_type", ""),
        "quality":       parsed.get("quality", ""),
        "coating":       parsed.get("coating", ""),
    }
    return odoo.match_coils_to_vsi(
        cert_coils=parsed.get("coils") or [],
        vs_articles=odoo_data.get("vs_articles") or [],
        explicit_vsi_ids=explicit_vsi_ids or None,
        cert_meta=cert_meta,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.post("/match-cert")
async def match_cert(request: Request):
    """
    Odoo lookup + coil-level matching -- returns JSON with full match details.
    Make calls this first to build the Slack notification.
    """
    parsed, po_num, explicit_vsi_ids, so_number_override = await _parse_body(request)

    odoo_data, match_type, score = _odoo_lookup(
        parsed, po_num, explicit_vsi_ids, so_number_override
    )
    match_result = _run_coil_matching(parsed, odoo_data, explicit_vsi_ids, match_type)

    return {
        "match_type":          match_type,
        "match_score":         score,
        "po_number":           po_num or odoo_data.get("po_number", ""),
        "so_number":           odoo_data.get("so_number", ""),
        "buyer_name":          odoo_data.get("buyer_name", ""),
        "buyer_country":       odoo_data.get("buyer_country", ""),
        "odoo_data":           odoo_data,
        "cert_number":         parsed.get("cert_number", ""),
        "grade":               parsed.get("grade", ""),
        "total_weight_kg":     parsed.get("total_weight_kg"),
        "coil_count":          len(parsed.get("coils") or []),
        "matched_vsi_ids":     match_result["matched_vsi_ids"],
        "unmatched_vsi_ids":   match_result["unmatched_vsi_ids"],
        "unmatched_cert_coil_nos": [
            c.get("coil_no", "?") for c in match_result["unmatched_cert_coils"]
        ],
        "matched_coil_count":  len(match_result["matched_cert_coils"]),
        "needs_slack_input":   match_type == "unmatched",
        "warning": (
            "No Odoo match found -- Slack will ask for SO + VSI IDs"
            if match_type == "unmatched" else ""
        ),
    }


@app.post("/generate-cert")
async def generate(request: Request):
    """
    Generate the neutralised PDF.
    Returns PDF bytes as application/pdf.

    Coil filtering:
    - Only cert coils matched to a VSI article are included in the output PDF.
    - If no coils matched but vs_articles exist -> warning logged, all coils used as fallback.
    - If match_type == "unmatched" -> all cert coils used (no Odoo data to match against).

    Response headers:
      X-Match-Type     -- explicit_so | explicit | auto_matched | unmatched
      X-Match-Score    -- numeric score
      X-SO-Number      -- matched SO name
      X-Unmatched-VSI  -- comma-separated VSI IDs in SO but not in cert (for Slack)
    """
    parsed, po_num, explicit_vsi_ids, so_number_override = await _parse_body(request)

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

    odoo_data, match_type, score = _odoo_lookup(
        parsed, po_num, explicit_vsi_ids, so_number_override
    )
    match_result = _run_coil_matching(parsed, odoo_data, explicit_vsi_ids, match_type)

    # Apply coil filtering
    matched_coils     = match_result["matched_cert_coils"]
    unmatched_vsi     = match_result["unmatched_vsi_ids"]
    vs_articles_exist = bool(odoo_data.get("vs_articles"))

    if vs_articles_exist and matched_coils:
        cert_for_pdf = dict(parsed)
        cert_for_pdf["coils"] = matched_coils
        total = sum(float(c.get("weight_kg") or 0) for c in matched_coils)
        if total > 0:
            cert_for_pdf["total_weight_kg"] = total
    elif vs_articles_exist and not matched_coils:
        print("[Generate] WARNING: 0 cert coils matched %d VSI articles. "
              "Falling back to all cert coils." % len(odoo_data["vs_articles"]))
        cert_for_pdf = parsed
    else:
        cert_for_pdf = parsed

    try:
        pdf_bytes = generate_certificate(
            parsed_cert=cert_for_pdf,
            odoo_data=odoo_data,
            logo_path=LOGO_PATH or None,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail="PDF generation failed: %s" % exc)

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "X-Match-Type":    match_type,
            "X-Match-Score":   str(score),
            "X-SO-Number":     odoo_data.get("so_number", ""),
            "X-Unmatched-VSI": ",".join(unmatched_vsi),
        },
    )


@app.get("/health")
def health():
    return {"status": "ok"}
