"""
Certificate Neutralisation Pipeline — Main Orchestrator
========================================================
Runs daily via GitHub Actions. For each cert in Docsumo with status 'reviewing'
that has not already been processed:
  1. Pull extracted values from Docsumo API
  2. Look up Sales Order + VS articles from Odoo
  3. Generate neutralised PDF using VS template
  4. Upload to Google Drive Customer Certs folder
  5. Record the doc_id in processed_ids.json to prevent re-processing

Tracking: processed_ids.json in this folder is committed back to the repo
after each run by the GitHub Actions workflow. Certs whose doc_id appears
in this file are skipped on future runs — Docsumo status is never changed.

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


# ─── Config ──────────────────────────────────────────────────────────────────
LOGO_PATH             = str(Path(__file__).parent / "NEW VS LOGO.jpg")
TRACKING_FILE         = Path(__file__).parent / "processed_ids.json"
CUSTOMER_CERTS_FOLDER = os.environ.get("CUSTOMER_CERTS_FOLDER_ID", "")


def load_processed_ids() -> set:
    """Load the set of already-processed Docsumo doc_ids from the tracking file."""
    if TRACKING_FILE.exists():
        with open(TRACKING_FILE) as f:
            return set(json.load(f))
    return set()


def save_processed_ids(ids: set):
    """Persist the set of processed doc_ids back to the tracking file."""
    with open(TRACKING_FILE, "w") as f:
        json.dump(sorted(ids), f, indent=2)
    print(f"[Tracker] {len(ids)} processed ID(s) saved to {TRACKING_FILE.name}")


def main():
    # ── Initialise clients ────────────────────────────────────────────────────
    docsumo.set_api_key(os.environ["DOCSUMO_API_KEY"])
    odoo.set_api_key(os.environ["ODOO_API_KEY"])
    drive.init_from_env()

    if not CUSTOMER_CERTS_FOLDER:
        print("ERROR: CUSTOMER_CERTS_FOLDER_ID env var not set. Aborting.")
        sys.exit(1)

    if not os.path.exists(LOGO_PATH):
        print(f"WARNING: Logo not found at {LOGO_PATH}. PDF will have no logo.")

    # ── Load tracking file (which doc_ids have already been neutralised) ──────
    already_processed = load_processed_ids()
    print(f"[Tracker] {len(already_processed)} cert(s) already processed in previous runs.")

    # ── Fetch certs from Docsumo ──────────────────────────────────────────────
    all_certs = docsumo.list_reviewing_certs()

    # Filter out already-processed certs
    certs = [c for c in all_certs if c["doc_id"] not in already_processed]
    skipped = len(all_certs) - len(certs)

    if skipped:
        print(f"[Tracker] Skipping {skipped} already-processed cert(s).")

    if not certs:
        print("No new certs to neutralise. Nothing to do.")
        return

    print(f"\n{len(certs)} new cert(s) to process.")

    processed    = 0
    errors       = 0
    newly_done   = set()

    for doc in certs:
        doc_id    = doc["doc_id"]
        doc_title = doc.get("title", doc_id)
        print(f"\n{'─'*60}")
        print(f"Processing: {doc_title}")
        print(f"  doc_id: {doc_id}")

        try:
            # 1. Pull Docsumo extraction
            cert_data = docsumo.get_cert_data(doc_id)
            po_number = cert_data.get("vs_po_number")

            if not po_number:
                print(f"  SKIP — No VS PO number extracted for {doc_title}.")
                print("  → Fix: set up 'Vanilla Steel Order Number' field in Docsumo Field Setup.")
                errors += 1
                continue

            print(f"  VS PO:  {po_number}")

            # 2. Odoo lookup
            odoo_data = odoo.get_neutralisation_data(po_number)
            so_number = odoo_data.get("so_number", "UNKNOWN")
            print(f"  SO:     {so_number}")
            print(f"  Buyer:  {odoo_data.get('buyer_name')} ({odoo_data.get('buyer_country')})")

            # 3. Generate PDF
            print("  Generating neutralised PDF...")
            pdf_bytes = pdf.generate_certificate(cert_data, odoo_data, LOGO_PATH)

            # 4. Upload to Google Drive
            cert_date_s = (cert_data.get("cert_date") or "").replace("/", "-")
            filename    = f"{cert_date_s}_{so_number}_{po_number}.pdf"
            file_url    = drive.upload_pdf(pdf_bytes, filename, CUSTOMER_CERTS_FOLDER)

            # 5. Record as processed (don't touch Docsumo status)
            newly_done.add(doc_id)

            print(f"  ✅ Done — {filename}")
            print(f"     {file_url}")
            processed += 1

        except Exception as e:
            print(f"  ❌ ERROR: {e}")
            import traceback; traceback.print_exc()
            errors += 1
            # Doc_id is NOT added to newly_done — it will be retried next run

    # ── Save updated tracking file ────────────────────────────────────────────
    if newly_done:
        save_processed_ids(already_processed | newly_done)

    print(f"\n{'═'*60}")
    print(f"Run complete.  Processed: {processed}  |  Errors: {errors}")

    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
