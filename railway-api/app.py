"""
Vanilla Steel — Certificate Neutralisation API
Railway endpoint called by Make.com in the cert automation pipeline.

Flow:
  Make.com trigger (new PDF in GDrive)
      → downloads PDF
      → POSTs to Claude API → gets cert_json
      → POSTs cert_json here → gets neutralised PDF as base64
      → uploads PDF to output GDrive folder

Environment variables (set in Railway):
    ODOO_API_KEY   — Odoo XML-RPC API key
    MAKE_SECRET    — optional shared secret for basic auth (set same in Make.com)
"""
import base64
import os
from pathlib import Path

from flask import Flask, request, jsonify
import odoo_client as odoo
import pdf_generator as pdf_gen

app = Flask(__name__)

LOGO_PATH  = str(Path(__file__).parent / "NEW VS LOGO.jpg")
MAKE_SECRET = os.environ.get("MAKE_SECRET", "")


# ─── Auth helper ──────────────────────────────────────────────────────────────

def _check_auth() -> bool:
    """If MAKE_SECRET is set, enforce it via X-Make-Secret header."""
    if not MAKE_SECRET:
        return True
    return request.headers.get("X-Make-Secret", "") == MAKE_SECRET


# ─── AOO filter (coil → vs_article mapping) ───────────────────────────────────

def _apply_aoo_filter(cert_coils: list, vs_articles: list) -> list:
    """
    Map cert coils to Odoo vs_articles.

    Strategy 1: exact match on original_supplier_article (pack_nr / cast_no / coil_no)
    Strategy 2: positional pairing — first N cert coils ↔ first N vs_articles

    Returns: list of coil dicts, each with 'vs_article' populated.
    """
    coils = [dict(c) for c in cert_coils]   # copy so we don't mutate input

    if not vs_articles:
        for coil in coils:
            coil["vs_article"] = "–"
        return coils

    # Build lookup: supplier article reference → vs_article
    supplier_art_map: dict[str, str] = {}
    for art in vs_articles:
        osa = (art.get("original_supplier_article") or "").strip()
        if osa:
            supplier_art_map[osa] = art.get("vs_article", "–")

    # Attempt exact matching
    if supplier_art_map:
        matched = []
        for coil in coils:
            key = (coil.get("pack_nr") or coil.get("cast_no") or
                   coil.get("coil_no") or "")
            if key and key in supplier_art_map:
                coil["vs_article"] = supplier_art_map[key]
                matched.append(coil)
        if len(matched) == len(vs_articles):
            print(f"  [AOO] Matched {len(matched)} coil(s) by supplier article number.")
            return matched

    # Fallback: positional pairing
    n = len(vs_articles)
    selected = coils[:n]
    for coil, art in zip(selected, vs_articles):
        coil["vs_article"] = art.get("vs_article", "–")
    print(f"  [AOO] Positional pairing: {len(selected)} of {len(coils)} coil(s) selected.")
    return selected


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "vs-cert-api"})


@app.route("/generate-cert", methods=["POST"])
def generate_cert():
    """
    POST body (JSON):
    {
      "cert_json": {
        "cert_number":      str,
        "cert_date":        str,          // e.g. "29.06.2026"
        "cert_type":        str,          // e.g. "EN 10204 3.1"
        "grade":            str,
        "standard":         str,
        "material_type":    str,
        "quality":          str | null,
        "supplier_format":  "SSAB" | "AM" | "other",
        "is_integer_chemistry": bool,     // true for AM raw-integer format
        "am_units":         {element: "-3"|"-2"|"-4"} | null,
        "total_weight_kg":  float | null,
        "quality_system":   str | null,   // e.g. "ISO 9001" or null
        "remarks":          [str],
        "coils": [
          {
            "pack_nr":       str,
            "cast_no":       str,
            "coil_no":       str,
            "width_mm":      str,
            "thickness_mm":  str,
            "weight_kg":     float | null,
            "net_weight_kg": float | null,
            "qty":           int,
            "chemicals": {"C": float, "Si": float, ...},
            "mechanical": {
              "cond": str, "dir": str,
              "rp02": float, "rm": float,
              "a_pct": float, "rp02_rm": float | null
            } | null
          }
        ]
      }
    }

    Response 200:
    {
      "status":           "ok",
      "pdf_base64":       str,
      "filename":         str,
      "so_number":        str,
      "buyer_name":       str,
      "buyer_country":    str,
      "po_number":        str,
      "match_confidence": int,
      "coils_matched":    int
    }

    Response 422: no Odoo match found
    Response 500: internal error
    """
    if not _check_auth():
        return jsonify({"status": "error", "error": "unauthorized"}), 401

    try:
        body = request.get_json(force=True)
        if not body:
            return jsonify({"status": "error", "error": "empty_body"}), 400

        cert_json = body.get("cert_json")
        if not cert_json:
            return jsonify({"status": "error", "error": "missing_cert_json",
                            "message": "Request must include 'cert_json' key."}), 400

        # Initialise Odoo client
        api_key = os.environ.get("ODOO_API_KEY", "")
        if not api_key:
            return jsonify({"status": "error", "error": "missing_env",
                            "message": "ODOO_API_KEY not set on server."}), 500
        odoo.set_api_key(api_key)

        # ── Build cert signals for Odoo matching ──────────────────────────────
        coils = cert_json.get("coils") or []
        first_coil = coils[0] if coils else {}
        total_wt_kg = cert_json.get("total_weight_kg") or sum(
            (c.get("weight_kg") or 0) for c in coils
        ) or None

        cert_signals = {
            "weight_kg":    total_wt_kg,
            "grade":        cert_json.get("grade", ""),
            "material_type": cert_json.get("material_type", ""),
            "quality":      cert_json.get("quality", ""),
            "width_mm":     str(first_coil.get("width_mm", "")),
            "thickness_mm": str(first_coil.get("thickness_mm", "")),
        }
        print(f"[API] Cert signals: grade='{cert_signals['grade']}' "
              f"w={cert_signals['weight_kg']}kg "
              f"{cert_signals['width_mm']}×{cert_signals['thickness_mm']}mm")

        # ── Odoo: find matching PO ────────────────────────────────────────────
        po_number, confidence, candidates = odoo.find_po_for_cert(cert_signals)

        if not po_number or confidence < 6:
            print(f"[API] No Odoo match. Best score: {confidence}")
            return jsonify({
                "status":     "error",
                "error":      "no_odoo_match",
                "message":    f"No Odoo PO matched cert signals. Best score: {confidence}.",
                "candidates": [
                    {"po": p, "score": s, "vs_article": v}
                    for p, s, v in (candidates[:3] if candidates else [])
                ],
            }), 422

        print(f"[API] Matched PO: {po_number} (confidence={confidence})")

        # ── Odoo: get full order data ─────────────────────────────────────────
        odoo_data = odoo.get_neutralisation_data(po_number)
        so_number  = odoo_data.get("so_number", "")
        print(f"[API] SO: {so_number} | Buyer: {odoo_data.get('buyer_name')}")

        # ── AOO filter: map coils → vs_articles ───────────────────────────────
        vs_articles    = odoo_data.get("vs_articles") or []
        filtered_coils = _apply_aoo_filter(coils, vs_articles)
        cert_json["coils"] = filtered_coils

        # Enrich cert_json with per-coil grade/dims from Odoo if missing
        for coil, art in zip(filtered_coils, vs_articles):
            if not coil.get("grade") and art.get("grade"):
                coil["grade"] = art["grade"]
            if not coil.get("width_mm") and art.get("width_mm"):
                coil["width_mm"] = art["width_mm"]
            if not coil.get("thickness_mm") and art.get("thickness_mm"):
                coil["thickness_mm"] = art["thickness_mm"]
            if not cert_json.get("material_type") and art.get("material_type"):
                cert_json["material_type"] = art["material_type"]
            if not cert_json.get("grade") and art.get("grade"):
                cert_json["grade"] = art["grade"]

        # ── Generate neutralised PDF ──────────────────────────────────────────
        print(f"[API] Generating PDF — {len(filtered_coils)} coil(s)…")
        pdf_bytes = pdf_gen.generate_certificate(cert_json, odoo_data, LOGO_PATH)
        print(f"[API] PDF generated: {len(pdf_bytes):,} bytes")

        # ── Build filename ────────────────────────────────────────────────────
        cert_date = (cert_json.get("cert_date") or "").replace("/", "-").replace(".", "-")
        filename  = f"{cert_date}_{so_number}_{po_number}.pdf".lstrip("_-")

        return jsonify({
            "status":           "ok",
            "pdf_base64":       base64.b64encode(pdf_bytes).decode("utf-8"),
            "filename":         filename,
            "so_number":        so_number,
            "buyer_name":       odoo_data.get("buyer_name", ""),
            "buyer_country":    odoo_data.get("buyer_country", ""),
            "po_number":        po_number,
            "match_confidence": confidence,
            "coils_matched":    len(filtered_coils),
        })

    except Exception as exc:
        import traceback
        tb = traceback.format_exc()
        print(f"[API] ERROR: {exc}\n{tb}")
        return jsonify({
            "status":    "error",
            "error":     "internal_error",
            "message":   str(exc),
            "traceback": tb,
        }), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
