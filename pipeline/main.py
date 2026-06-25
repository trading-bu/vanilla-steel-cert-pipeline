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
import odoo_client    as odoo
import pdf_generator  as pdf
import drive_client   as drive
import cert_parser    as parser


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
            # ── 1. Header fields from Docsumo ──────────────────────────────────
            cert_header = docsumo.get_cert_data(doc_id)
            po_number   = cert_header.get("vs_po_number")

            if not po_number:
                print(f"  SKIP — No VS PO number found for {doc_title}.")
                errors += 1
                continue

            print(f"  VS PO: {po_number}")

            # ── 2. Download original PDF from Docsumo ──────────────────────────
            print("  Downloading original PDF from Docsumo...")
            pdf_bytes = docsumo.download_cert_pdf(doc_id)
            print(f"  PDF downloaded: {len(pdf_bytes):,} bytes")

            # ── 3. Parse multi-coil data from PDF ─────────────────────────────
            print("  Parsing certificate (pdfplumber)...")
            parsed_cert = parser.parse_cert(pdf_bytes)
            fmt         = parsed_cert.get("supplier_format", "unknown")
            n_coils     = len(parsed_cert.get("coils", []))
            print(f"  Format: {fmt} | Coils found: {n_coils}")

            if n_coils == 0:
                print("  WARNING: No coils parsed from PDF. Check cert_parser for this format.")

            # ── 3b. Docsumo fallback for empty chemistry / mechanical ──────────
            # For formats cert_parser doesn't fully handle (e.g. Schaefer-Werke),
            # fill missing per-coil data from Docsumo's own field extraction.
            if n_coils > 0:
                coil = parsed_cert["coils"][0]

                # Chemistry: fall back if cert_parser returned an empty dict
                ds_chems = cert_header.get("chemicals") or {}
                ds_chems = {k: v for k, v in ds_chems.items() if v is not None}
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

            # ── 5. Apply AOO filter — select coils that are in this order ──────
            filtered_coils = _apply_aoo_filter(parsed_cert, odoo_data)
            parsed_cert["coils"] = filtered_coils
            print(f"  Coils after AOO filter: {len(filtered_coils)}")

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
