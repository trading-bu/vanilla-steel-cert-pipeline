"""
Vanilla Steel — Certificate Neutralisation API
FastAPI wrapper around pdf_generator.py + odoo_client.py.
Deploy to Railway. Make.com calls POST /generate-cert with Claude-extracted JSON.

Lessons applied from previous Docsumo/GitHub pipeline:
- All errors return JSON with a 'detail' field so Make can read and forward to Slack
- /match-cert is separate from /generate-cert so Make can show match info before generating
- Validation rejects obviously bad extractions early (no coils = don't attempt PDF)
- match_type in response tells Make whether PO was explicit, auto-matched, or unmatched

Body format flexibility:
- Wrapped:  {"parsed_cert": {...}, "po_number": "..."}   ← original format
- Raw:      {...cert fields directly...}                  ← Make can send Claude text directly

PO number handling:
- VS POs follow pattern P0XXXX (e.g. P01755). Only these trigger explicit Odoo lookup.
- Supplier order numbers (e.g. 41687, 1151079) fall through to auto-match by weight/grade.
"""
import os, re
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response, JSONResponse
from pydantic import BaseModel

import odoo_client as odoo
from pdf_generator import generate_certificate

app = FastAPI(title="VS Cert Generator")

ODOO_API_KEY = os.environ.get("ODOO_API_KEY", "")
LOGO_PATH    = os.environ.get("LOGO_PATH", "")

@app.on_event("startup")
def startup():
    if ODOO_API_KEY:
        odoo.set_api_key(ODOO_API_KEY)


class CertRequest(BaseModel):
    parsed_cert: dict
    po_number:   str | None = None


async def _parse_body(request: Request) -> tuple[dict, str]:
    """
    Accept two body formats:
      1. {"parsed_cert": {...}, "po_number": "..."}  — wrapped (original)
      2. {...cert fields...}                          — raw cert JSON (Make direct)
    Returns (parsed_cert_dict, po_number_str).
    """
    body = await request.json()
    if "parsed_cert" in body:
        return body["parsed_cert"], body.get("po_number") or ""
    else:
        return body, body.get("po_number") or ""


_VS_PO_RE = re.compile(r'^P\d{4,}$', re.IGNORECASE)

def _is_vs_po(s: str) -> bool:
    """True if s looks like a VS Purchase Order number (e.g. P01755, P01234)."""
    return bool(s and _VS_PO_RE.match(s.strip()))


def _odoo_lookup(parsed: dict, po_num: str) -> tuple[dict, str, int]:
    """
    Returns (odoo_data, match_type, match_score).
    match_type: "explicit" | "auto_matched" | "unmatched"

    Only runs an explicit PO lookup if po_num looks like a VS PO (P0XXXX).
    Supplier-side order numbers (e.g. 41687, 1151079) are NOT VS POs —
    they fall through to auto-match by weight/grade/dimensions.
    """
    if po_num and _is_vs_po(po_num):
        try:
            data = odoo.get_neutralisation_data(po_num.strip().upper())
            return data, "explicit", 13
        except Exception as e:
            print(f"[Odoo] explicit lookup failed for {po_num}: {e}")
            # Fall through to auto-match rather than returning unmatched immediately

    # Auto-match
    try:
        first_coil = (parsed.get("coils") or [{}])[0]
        signals = {
            "weight_kg":    parsed.get("total_weight_kg"),
            "grade":        parsed.get("grade", ""),
            "material_type":parsed.get("material_type", ""),
            "quality":      parsed.get("quality", ""),
            "width_mm":     first_coil.get("width_mm", ""),
            "thickness_mm": first_coil.get("thickness_mm", ""),
        }
        matched_po, score, candidates = odoo.find_po_for_cert(signals)
        if matched_po and score >= 8:
            runner_up = candidates[1][1] if len(candidates) > 1 else 0
            if score - runner_up >= 3:
                data = odoo.get_neutralisation_data(matched_po)
                print(f"[Odoo] Auto-matched {matched_po} score={score}")
                return data, "auto_matched", score
        print(f"[Odoo] No confident match. Top score={score}")
    except Exception as e:
        print(f"[Odoo] auto-match error: {e}")

    return {}, "unmatched", 0


@app.post("/match-cert")
async def match_cert(request: Request):
    """
    Odoo lookup only — returns JSON with match details.
    Make calls this first to show match info in Slack notification.
    Accepts both wrapped {"parsed_cert":{...}} and raw cert JSON bodies.
    """
    parsed, po_num = await _parse_body(request)

    odoo_data, match_type, score = _odoo_lookup(parsed, po_num)

    return {
        "match_type":   match_type,   # "explicit" | "auto_matched" | "unmatched"
        "match_score":  score,
        "po_number":    po_num or odoo_data.get("po_number", ""),
        "so_number":    odoo_data.get("so_number", ""),
        "buyer_name":   odoo_data.get("buyer_name", ""),
        "buyer_country":odoo_data.get("buyer_country", ""),
        "odoo_data":    odoo_data,
        "cert_number":  parsed.get("cert_number", ""),
        "grade":        parsed.get("grade", ""),
        "total_weight_kg": parsed.get("total_weight_kg"),
        "coil_count":   len(parsed.get("coils") or []),
        "warning":      "No Odoo match found — check manually" if match_type == "unmatched" else "",
    }


@app.post("/generate-cert")
async def generate(request: Request):
    """
    Generate the neutralised PDF.
    Make calls this after /match-cert (and optional Slack approval).
    Returns PDF bytes as application/pdf.
    Accepts both wrapped {"parsed_cert":{...}} and raw cert JSON bodies.
    """
    parsed, po_num = await _parse_body(request)

    # ── Validate extraction ───────────────────────────────────────────────────
    # Lesson: don't attempt PDF generation on empty/garbage Claude output.
    coils = parsed.get("coils") or []
    if not coils:
        raise HTTPException(
            status_code=422,
            detail="Extraction returned no coils. Check the Claude prompt or the source PDF."
        )
    if not parsed.get("grade") and not parsed.get("material_type"):
        raise HTTPException(
            status_code=422,
            detail="Extraction returned no grade or material type. Cert may need manual processing."
        )
    odoo_data, match_type, score = _odoo_lookup(parsed, po_num)

    try:
        pdf_bytes = generate_certificate(
            parsed_cert=parsed,
            odoo_data=odoo_data,
            logo_path=LOGO_PATH or None,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PDF generation failed: {e}")

    # Return PDF with match info in headers so Make can log it
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "X-Match-Type":  match_type,
            "X-Match-Score": str(score),
            "X-SO-Number":   odoo_data.get("so_number", ""),
        }
    )


@app.get("/health")
def health():
    return {"status": "ok"}
