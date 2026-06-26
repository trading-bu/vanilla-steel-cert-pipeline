"""
Certificate Neutralisation Pipeline — Main Orchestrator
========================================================
Runs daily via GitHub Actions. For each cert in Docsumo with status 'reviewing'
that has not already been processed:
  1. Pull header fields (PO number, cert number, date, grade) from Docsumo API
  2. Download the original PDF from Docsumo
  3. Parse multi-coil data (chemistry + mechanical per coil) with pdfplumber
  4. Look up Sales Order + VS articles from Odoo (AOO filter)
  5. Generate neutralised PDF using VS template
  6. Upload to Google Drive Customer Certs folder
  7. Record the doc_id in processed_ids.json to prevent re-processing

Environment variables (set as GitHub Secrets):
  DOCSUMO_API_KEY              — Docsumo API key
  ODOO_API_KEY                 — Odoo XML-RPC API key
  GOOGLE_SERVICE_ACCOUNT_JSON  — Service account JSON (full content)
  CUSTOMER_CERTS_FOLDER_ID     — Google Drive folder ID for output certs
"""
import json
import os
import sys
from pathlib import Path

import docsumo_client as docsumo


def _safe_int(v):
    """Convert v to int, return None on failure."""
    try:
        return int(float(str(v))) if v else None
    except (ValueError, TypeError):
        return None


def _safe_float(v):
    """Convert v to float, return None on failure."""
    try:
        return float(str(v)) if v else None
    except (ValueError, TypeError):
        return None
import odoo_client    as odoo
import pdf_generator  as pdf
import drive_client   as drive
import cert_parser    as parser
import po_log


# ─── Config ───────────────────────────────────────────────────────────────────
LOGO_PATH             = str(Path(__file__).parent / "NEW VS LOGO.jpg")
TRACKING_FILE         = Path(__file__).parent / "processed_ids.json"
CUSTOMER_CERTS_FOLDER = os.environ.get("CUSTOMER_CERTS_FOLDER_ID", "")


def load_processed_ids() -> set:
    if TRACKING_FILE.exists():
        with open(TRACKING_FILE) as f:
            return set(json.load(f))
    return set()


def save_processed_ids(ids: set):
    with open(TRACKING_FILE, "w") as f:
        json.dump(sorted(ids), f, indent=2)
    print(f"[Tracker] {len(ids)} processed ID(s) saved.")


def _find_cert_coil_for_odoo_item(cert_coils: list, odoo_weight_kg: float | None,
                                   tolerance_kg: float = 100) -> dict:
    """
    Find the cert coil that best matches a specific Odoo line item by weight.

    Handles the case where a supplier cert covers MORE coils than VS bought.
    Example: supplier cert has 3 coils from the same heat; VS bought only 1.
    → identify which cert coil belongs to our Odoo item; ignore the other 2.

    Returns the best-matching coil dict, or cert_coils[0] as a fallback.
    """
    if not cert_coils:
        return {}
    if len(cert_coils) == 1:
        return cert_coils[0]
    if not odoo_weight_kg:
        return cert_coils[0]

    candidates = [
        c for c in cert_coils
        if c.get("weight_kg") is not None
        and abs(c["weight_kg"] - odoo_weight_kg) <= tolerance_kg
    ]
    if candidates:
        return min(candidates, key=lambda c: abs(c["weight_kg"] - odoo_weight_kg))

    # No weight match — fall back to first coil
    return cert_coils[0]


def _apply_aoo_filter(parsed_cert: dict, odoo_data: dict) -> list:
    """
    Match parsed coils against Odoo AOO-filtered vs_articles.

    Strategy:
      1. Try to match coil pack_nr / cast_no against original_supplier_article on PO line.
      2. If no match, use positional pairing (first N coils ↔ first N Odoo lines).

    Returns a list of coil dicts, each enriched with 'vs_article' key.
    """
    coils       = parsed_cert.get("coils", [])
    vs_articles = odoo_data.get("vs_articles", [])

    if not vs_articles:
        # No AOO filter available — include all coils without VS article
        for coil in coils:
            coil["vs_article"] = "–"
        return coils

    # Build lookup: supplier article number → vs_article
    supplier_art_map = {}
    for art in vs_articles:
        osa = (art.get("original_supplier_article") or "").strip()
        if osa:
            supplier_art_map[osa] = art.get("vs_article", "–")

    # Try exact matching
    matched = []
    if supplier_art_map:
        for coil in coils:
            key = coil.get("pack_nr", "") or coil.get("cast_no", "")
            if key in supplier_art_map:
                coil["vs_article"] = supplier_art_map[key]
                matched.append(coil)

    if len(matched) == len(vs_articles):
        print(f"  [Filter] Matched {len(matched)} coil(s) by supplier article number.")
        return matched

    # Fallback: positional pairing — take first N coils
    n = len(vs_articles)
    selected = coils[:n]
    for i, (coil, art) in enumerate(zip(selected, vs_articles)):
        coil["vs_article"] = art.get("vs_article", "–")

    print(f"  [Filter] Positional pairing: {len(selected)} of {len(coils)} coil(s) selected.")
    return selected


def main():
    docsumo.set_api_key(os.environ["DOCSUMO_API_KEY"])
    odoo.set_api_key(os.environ["ODOO_API_KEY"])
    drive.init_from_env()

    if not CUSTOMER_CERTS_FOLDER:
        print("ERROR: CUSTOMER_CERTS_FOLDER_ID not set. Aborting.")
        sys.exit(1)

    already_processed = load_processed_ids()
    print(f"[Tracker] {len(already_processed)} cert(s) already processed.")

    po_log_data = po_log.load()
    print(po_log.summary(po_log_data))

    all_certs = docsumo.list_reviewing_certs()
    certs     = [c for c in all_certs if c["doc_id"] not in already_processed]
    skipped   = len(all_certs) - len(certs)

    if skipped:
        print(f"[Tracker] Skipping {skipped} already-processed cert(s).")

    if not certs:
        print("No new certs to neutralise. Nothing to do.")
        return

    print(f"\n{len(certs)} new cert(s) to process.")

    processed  = 0
    errors     = 0
    newly_done = set()

    for doc in certs:
        doc_id    = doc["doc_id"]
        doc_title = doc.get("title", doc_id)
        print(f"\n{'─'*60}")
        print(f"Processing: {doc_title}  (doc_id: {doc_id})")

        try:
            # ── 1. Header fields from Docsumo ────────────────────────────────────
            cert_header = docsumo.get_cert_data(doc_id)
            po_number   = cert_header.get("vs_po_number")

            if not po_number:
                print(f"  SKIP — No VS PO number found for {doc_title}.")
                errors += 1
                continue

            print(f"  VS PO: {po_number}")

            # ── 2. Download original PDF from Docsumo (best-effort) ────────────
            print("  Downloading original PDF from Docsumo...")
            pdf_bytes = None
            try:
                pdf_bytes = docsumo.download_cert_pdf(doc_id)
                print(f"  PDF downloaded: {len(pdf_bytes):,} bytes")
            except Exception as dl_err:
                print(f"  WARNING: PDF download failed ({dl_err.__class__.__name__}: {dl_err}). "
                      "Continuing with Docsumo-only extraction.")

            # ── 3. Parse multi-coil data from PDF ─────────────────────────────
            if pdf_bytes:
                print("  Parsing certificate (pdfplumber)...")
                parsed_cert = parser.parse_cert(pdf_bytes)
                fmt         = parsed_cert.get("supplier_format", "unknown")
                n_coils     = len(parsed_cert.get("coils", []))
                print(f"  Format: {fmt} | Coils found: {n_coils}")
                if n_coils == 0:
                    print("  WARNING: No coils parsed from PDF. Check cert_parser for this format.")
            else:
                # No PDF available — build parsed_cert directly from Docsumo fields
                h    = cert_header
                dm   = h.get("mechanical") or {}
                mech = None
                if dm.get("rm"):
                    mech = {
                        "cond":    "F",
                        "dir":     "L",
                        "rp02":    _safe_int(dm.get("reh")),
                        "rm":      _safe_int(dm.get("rm")),
                        "a_pct":   _safe_float(dm.get("a80")),
                        "rp02_rm": None,
                    }
                heat  = h.get("heat_number", "")
                chems = {k: v for k, v in (h.get("chemicals") or {}).items()
                         if v is not None and v != ""}
                parsed_cert = {
                    "supplier_format": "docsumo",
                    "cert_date":       h.get("cert_date", ""),
                    "cert_number":     h.get("cert_number", ""),
                    "cert_type":       h.get("cert_type", "EN 10204 3.1"),
                    "grade":           h.get("grade", ""),
                    "material_type":   h.get("material_type", ""),
                    "total_weight_kg": h.get("weight_kg"),
                    "coils": [{
                        "cast_no":    heat,
                        "pack_nr":    heat,
                        "coil_no":    heat,
                        "weight_kg":  h.get("weight_kg"),
                        "chemicals":  chems,
                        "mechanical": mech,
                    }],
                }
                n_coils = 1
                print("  [Docsumo-only] Cert built from Docsumo extraction (no original PDF).")

            # ── 3b. Docsumo fallback for empty chemistry / mechanical ──────────
            # Only applies when PDF was parsed — fills gaps cert_parser missed.
            if pdf_bytes and n_coils > 0:
                coil = parsed_cert["coils"][0]

                # Chemistry: fall back if cert_parser returned an empty dict
                ds_chems = cert_header.get("chemicals") or {}
                ds_chems = {k: v for k, v in ds_chems.items() if v is not None and v != ""}
                if ds_chems and not any(v is not None for v in (coil.get("chemicals") or {}).values()):
                    coil["chemicals"] = ds_chems
                    print("  [Fallback] Chemistry filled from Docsumo extraction.")

                # Mechanical: fall back if cert_parser returned None
                if coil.get("mechanical") is None:
                    dm = cert_header.get("mechanical") or {}
                    if dm.get("rm"):
                        coil["mechanical"] = {
                            "cond":    "F",
                            "dir":     "L",
                            "rp02":    int(dm["reh"])   if dm.get("reh")  else None,
                            "rm":      int(dm["rm"]),
                            "a_pct":   float(dm["a80"]) if dm.get("a80") else None,
                            "rp02_rm": None,
                        }
                        print("  [Fallback] Mechanical filled from Docsumo extraction.")

                # Weight: fall back if cert_parser found nothing
                if not coil.get("weight_kg") and cert_header.get("weight_kg"):
                    coil["weight_kg"] = cert_header["weight_kg"]
                    parsed_cert["total_weight_kg"] = cert_header["weight_kg"]

                # Heat number: fall back if cert_parser couldn't find it
                if not coil.get("cast_no") and cert_header.get("heat_number"):
                    h = cert_header["heat_number"]
                    coil["cast_no"] = h
                    coil["pack_nr"] = h
                    coil["coil_no"] = h

                # Grade / material type
                if cert_header.get("material_type") and not parsed_cert.get("material_type"):
                    parsed_cert["material_type"] = cert_header["material_type"]

            # ── 4. Odoo lookup ─────────────────────────────────────────────────
            odoo_data = odoo.get_neutralisation_data(po_number)
            so_number = odoo_data.get("so_number", "UNKNOWN")
            print(f"  SO: {so_number} | Buyer: {odoo_data.get('buyer_name')}")

            # ── 5. PO log: register order + match cert to specific item(s) ─────
            #
            # First time we see this PO, all line items (with weights from Odoo)
            # are written to po_log.json.  Subsequent certs for the same PO use
            # the log to find still-unmatched items.
            #
            # Matching priority:
            #   1. Heat number (idempotent re-run safety)
            #   2. Weight: cert_weight ≈ item weight_t × 1000  (±100 kg)
            #   3. Fallback: all unmatched items
            #
            po_entry = po_log.ensure_po(po_log_data, po_number, odoo_data)

            cert_heat      = (cert_header.get("heat_number") or "").strip() or None
            cert_weight_kg = (cert_header.get("weight_kg")
                              or parsed_cert.get("total_weight_kg"))

            matched_items = po_log.find_matching_items(
                po_entry, cert_weight_kg, cert_heat
            )
            print(f"  Matched {len(matched_items)} PO line item(s) for this cert.")

            # Build one coil row per matched Odoo item.
            #
            # Bi-directional matching:
            #   • po_log already narrowed Odoo items → only items this cert covers
            #   • _find_cert_coil_for_odoo_item() picks the right cert coil when the
            #     supplier cert covers MORE coils than VS bought (e.g. supplier cert
            #     has 3 coils but we only purchased 1 → ignore the other 2)
            #
            # Data sources:
            #   • Chemistry + mechanical  → from the matched cert coil
            #   • VSI article + weight    → from Odoo (authoritative)
            #   • Grade + dimensions      → from Odoo product (if configured)
            cert_coils     = parsed_cert.get("coils") or [{}]
            filtered_coils = []

            for item in matched_items:
                odoo_wt_kg = round(item["weight_t"] * 1000) if item.get("weight_t") else None

                # Pick the cert coil whose weight matches this Odoo item
                cert_coil = _find_cert_coil_for_odoo_item(cert_coils, odoo_wt_kg)

                coil = dict(cert_coil)          # copy chem + mechanical from cert
                coil["vs_article"]   = item["vsi_id"]
                coil["pack_nr"]      = cert_heat or cert_coil.get("pack_nr", "")
                coil["cast_no"]      = cert_heat or cert_coil.get("cast_no", "")
                coil["coil_no"]      = cert_heat or cert_coil.get("coil_no", "")
                coil["weight_kg"]    = odoo_wt_kg or cert_coil.get("weight_kg")

                # Enrich from Odoo — fills columns the supplier cert doesn't have
                cert_w = cert_coil.get("width_mm") or ""
                cert_t = cert_coil.get("thickness_mm") or ""
                odoo_w = item.get("width_mm") or ""
                odoo_t = item.get("thickness_mm") or ""

                # Width: supplier cert takes priority; Odoo is fallback; warn on mismatch
                if cert_w and odoo_w and cert_w != odoo_w:
                    try:
                        if abs(float(cert_w) - float(odoo_w)) > 1.0:
                            print(f"  ⚠️  Width mismatch for {item['vsi_id']}: "
                                  f"cert={cert_w}mm, Odoo={odoo_w}mm")
                    except (ValueError, TypeError):
                        pass
                coil["width_mm"] = cert_w or odoo_w or ""

                # Thickness: supplier cert takes priority; Odoo is fallback; warn on mismatch
                if cert_t and odoo_t and cert_t != odoo_t:
                    try:
                        if abs(float(cert_t) - float(odoo_t)) > 0.05:
                            print(f"  ⚠️  Thickness mismatch for {item['vsi_id']}: "
                                  f"cert={cert_t}mm, Odoo={odoo_t}mm")
                    except (ValueError, TypeError):
                        pass
                coil["thickness_mm"] = cert_t or odoo_t or ""

                # Grade and quality per-coil (for Section 2 table and Section 1 display)
                coil["grade"]   = item.get("grade") or ""
                coil["quality"] = item.get("quality") or ""

                # Grade at cert level: prefer cert extraction → Odoo
                if not parsed_cert.get("grade"):
                    if item.get("grade"):
                        parsed_cert["grade"] = item["grade"]
                    elif item.get("product_name"):
                        print(f"  [Odoo] Product name for grade inspection: '{item['product_name']}'")

                # Quality at cert level: from Odoo
                if not parsed_cert.get("quality") and item.get("quality"):
                    parsed_cert["quality"] = item["quality"]

                # Material type / description for Section 1 of the cert
                if not parsed_cert.get("material_type"):
                    parsed_cert["material_type"] = (
                        item.get("material_type")
                        or item.get("description")
                        or item.get("product_name")
                        or ""
                    )

                filtered_coils.append(coil)

            if not filtered_coils:
                filtered_coils = [dict(cert_coils[0])] if cert_coils else [{}]

            parsed_cert["coils"] = filtered_coils
            print(f"  Coils in output cert: {len(filtered_coils)}")

            # Merge Docsumo header into parsed_cert (cert_date, grade override if present)
            if cert_header.get("cert_date"):
                parsed_cert["cert_date"] = cert_header["cert_date"]
            if cert_header.get("cert_number"):
                parsed_cert["cert_number"] = cert_header["cert_number"]
            if cert_header.get("cert_type"):
                parsed_cert["cert_type"] = cert_header["cert_type"]
            if cert_header.get("grade") and not parsed_cert.get("grade"):
                parsed_cert["grade"] = cert_header["grade"]

            # ── 6. Generate neutralised PDF ────────────────────────────────────
            print("  Generating neutralised PDF...")
            out_bytes = pdf.generate_certificate(parsed_cert, odoo_data, LOGO_PATH)

            # ── 7. Upload to Google Drive ──────────────────────────────────────
            cert_date_s = (parsed_cert.get("cert_date") or "").replace("/", "-")
            filename    = f"{cert_date_s}_{so_number}_{po_number}.pdf"
            file_url    = drive.upload_pdf(out_bytes, filename, CUSTOMER_CERTS_FOLDER)

            newly_done.add(doc_id)

            # Mark matched items in PO log and persist
            po_log.mark_matched(
                matched_items, cert_heat, doc_id,
                cert_header.get("cert_date", "")
            )
            po_log.save(po_log_data)

            print(f"  ✅ Done — {filename}")
            print(f"     {file_url}")
            processed += 1

        except Exception as e:
            print(f"  ❌ ERROR: {e}")
            import traceback; traceback.print_exc()
            errors += 1

    if newly_done:
        save_processed_ids(already_processed | newly_done)

    print(f"\n{'═'*60}")
    print(f"Run complete.  Processed: {processed}  |  Errors: {errors}")

    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
