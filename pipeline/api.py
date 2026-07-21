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

Slack interactive flow (no PO on cert):
- POST /pending-cert  : stores cert, posts Slack message with "Enter PO Number" button
- POST /slack/interactive : handles button click (opens modal) + modal submission (generates cert)
- Persistent storage: JSON file at PENDING_CERTS_FILE (default /data/pending_certs.json)
  Requires a Railway Volume mounted at /data to survive restarts.
"""
import io
import os
import re
import json
import uuid
import hmac
import hashlib
import time
import urllib.request
import urllib.parse
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response

import odoo_client as odoo
from pdf_generator import generate_certificate

app = FastAPI(title="VS Cert Generator")

ODOO_API_KEY          = os.environ.get("ODOO_API_KEY", "")
LOGO_PATH             = os.environ.get("LOGO_PATH", "")
SLACK_BOT_TOKEN       = os.environ.get("SLACK_BOT_TOKEN", "")
SLACK_SIGNING_SECRET  = os.environ.get("SLACK_SIGNING_SECRET", "")
SLACK_CHANNEL_ID      = os.environ.get("SLACK_CHANNEL_ID", "")
PENDING_CERTS_FILE    = os.environ.get("PENDING_CERTS_FILE", "/data/pending_certs.json")
GDRIVE_FOLDER_ID      = os.environ.get("GDRIVE_FOLDER_ID", "1xPfEoqAN8g2CcooOrTUaZy2W6ogjgHM7")
GDRIVE_CLIENT_ID      = os.environ.get("GDRIVE_CLIENT_ID", "")
GDRIVE_CLIENT_SECRET  = os.environ.get("GDRIVE_CLIENT_SECRET", "")
GDRIVE_REFRESH_TOKEN  = os.environ.get("GDRIVE_REFRESH_TOKEN", "")


@app.on_event("startup")
def startup():
    if ODOO_API_KEY:
        odoo.set_api_key(ODOO_API_KEY)
    # Ensure data directory exists
    os.makedirs(os.path.dirname(PENDING_CERTS_FILE), exist_ok=True)


# ---------------------------------------------------------------------------
# Persistent cert storage (JSON file on Railway Volume at /data)
# ---------------------------------------------------------------------------

def _load_pending() -> dict:
    try:
        with open(PENDING_CERTS_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_pending(data: dict):
    with open(PENDING_CERTS_FILE, "w") as f:
        json.dump(data, f)


def _store_cert(cert_id: str, cert_data: dict):
    pending = _load_pending()
    pending[cert_id] = cert_data
    _save_pending(pending)


def _pop_cert(cert_id: str) -> dict:
    pending = _load_pending()
    cert = pending.pop(cert_id, None)
    _save_pending(pending)
    return cert


# ---------------------------------------------------------------------------
# Slack helpers
# ---------------------------------------------------------------------------

def _verify_slack_signature(body: bytes, timestamp: str, signature: str) -> bool:
    """Reject requests older than 5 min or with bad HMAC."""
    try:
        if abs(time.time() - int(timestamp)) > 300:
            return False
    except ValueError:
        return False
    sig_base = f"v0:{timestamp}:{body.decode()}".encode()
    expected = "v0=" + hmac.new(
        SLACK_SIGNING_SECRET.encode(), sig_base, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def _slack_api(method: str, data: dict) -> dict:
    url = f"https://slack.com/api/{method}"
    payload = json.dumps(data).encode()
    req = urllib.request.Request(url, data=payload, headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
    })
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def _slack_form_api(method: str, data: dict) -> dict:
    """Slack API call with form-encoded body (required by files.getUploadURLExternal)."""
    url     = f"https://slack.com/api/{method}"
    payload = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=payload, headers={
        "Content-Type": "application/x-www-form-urlencoded",
        "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
    })
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def _slack_upload_pdf(pdf_bytes: bytes, filename: str, channel_id: str,
                      thread_ts: str, comment: str):
    """Upload a PDF to Slack using the v2 external upload API."""
    # Step 1: get upload URL — must use form-encoded, not JSON
    url_resp = _slack_form_api("files.getUploadURLExternal", {
        "filename": filename,
        "length":   len(pdf_bytes),
    })
    if not url_resp.get("ok"):
        raise RuntimeError(f"files.getUploadURLExternal failed: {url_resp.get('error')}")

    upload_url = url_resp["upload_url"]
    file_id    = url_resp["file_id"]

    # Step 2: POST bytes to the pre-signed URL
    put_req = urllib.request.Request(
        upload_url, data=pdf_bytes, method="POST",
        headers={"Content-Type": "application/octet-stream"},
    )
    with urllib.request.urlopen(put_req):
        pass

    # Step 3: complete upload, post to channel/thread
    complete_data = {
        "files":           [{"id": file_id, "title": filename}],
        "channel_id":      channel_id,
        "initial_comment": comment,
    }
    if thread_ts:
        complete_data["thread_ts"] = thread_ts

    _slack_api("files.completeUploadExternal", complete_data)


# ---------------------------------------------------------------------------
# Google Drive upload
# ---------------------------------------------------------------------------

def _gdrive_upload_pdf(pdf_bytes: bytes, filename: str) -> str:
    """
    Upload a PDF to the configured GDrive folder using OAuth credentials
    (support@vanillasteel.com account via stored refresh token).
    Returns the webViewLink of the uploaded file.
    Requires env vars: GDRIVE_CLIENT_ID, GDRIVE_CLIENT_SECRET, GDRIVE_REFRESH_TOKEN, GDRIVE_FOLDER_ID.
    """
    if not all([GDRIVE_CLIENT_ID, GDRIVE_CLIENT_SECRET, GDRIVE_REFRESH_TOKEN]):
        raise RuntimeError("GDRIVE_CLIENT_ID / GDRIVE_CLIENT_SECRET / GDRIVE_REFRESH_TOKEN not set")
    if not GDRIVE_FOLDER_ID:
        raise RuntimeError("GDRIVE_FOLDER_ID not set — cannot upload to Drive")

    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseUpload

    creds = Credentials(
        token=None,
        refresh_token=GDRIVE_REFRESH_TOKEN,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=GDRIVE_CLIENT_ID,
        client_secret=GDRIVE_CLIENT_SECRET,
    )
    creds.refresh(Request())
    service = build("drive", "v3", credentials=creds, cache_discovery=False)

    file_metadata = {"name": filename, "parents": [GDRIVE_FOLDER_ID]}
    media = MediaIoBaseUpload(io.BytesIO(pdf_bytes), mimetype="application/pdf")
    result = service.files().create(
        body=file_metadata,
        media_body=media,
        fields="id,name,webViewLink",
    ).execute()

    link = result.get("webViewLink", "")
    print(f"[Drive] Uploaded '{filename}' → {link}")
    return link


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

_VS_PO_RE = re.compile(r"^P[O0]\d{4}$", re.IGNORECASE)


def _is_vs_po(s):
    return bool(s and _VS_PO_RE.match(s.strip()))


def _normalise_po(s: str) -> str:
    """Normalise POXXXX (letter O) → P0XXXX (digit zero)."""
    if s and len(s) == 6 and s[0].upper() == "P" and s[1].upper() == "O":
        return "P0" + s[2:]
    return s


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
            po_num = _normalise_po(po_num.strip().upper())
            data = odoo.get_neutralisation_data(po_num)
            return data, "explicit", 13
        except Exception as exc:
            print("[Odoo] explicit lookup failed for %s: %s" % (po_num, exc))

    # 2. Auto-match by cert signals
    try:
        first_coil = (parsed.get("coils") or [{}])[0]
        signals = {
            "weight_kg":     parsed.get("total_weight_kg"),
            "grade":         parsed.get("grade", ""),
            "grade_full":    parsed.get("grade_full", ""),
            "material_type": parsed.get("material_type", ""),
            "quality":       parsed.get("quality", ""),
            "coating":       parsed.get("coating", ""),
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
# Endpoints — standard flow
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
        "match_type":        match_type,
        "match_score":       score,
        "po_number":         po_num or odoo_data.get("po_number", "") or parsed.get("po_number", ""),
        "so_number":         odoo_data.get("so_number", ""),
        "buyer_name":        odoo_data.get("buyer_name", ""),
        "buyer_country":     odoo_data.get("buyer_country", ""),
        "odoo_data":         odoo_data,
        "cert_number":       parsed.get("cert_number", ""),
        "grade":             parsed.get("grade", ""),
        "total_weight_kg":   parsed.get("total_weight_kg"),
        "coil_count":        len(parsed.get("coils") or []),
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


# ---------------------------------------------------------------------------
# Endpoints — Slack interactive flow (no PO number on cert)
# ---------------------------------------------------------------------------

@app.post("/pending-cert")
async def pending_cert(request: Request):
    """
    Called by Make.com when Claude extracts a cert with no PO number.
    Stores the cert and posts an interactive Slack message asking for the PO.
    """
    body = await request.json()
    parsed = body.get("parsed_cert") or body

    cert_id  = str(uuid.uuid4())
    _store_cert(cert_id, parsed)

    supplier       = parsed.get("supplier_name") or parsed.get("manufacturer") or "Unknown supplier"
    mill_cert      = parsed.get("cert_number") or parsed.get("mill_cert_number") or "—"
    weight_kg      = parsed.get("total_weight_kg")
    weight_str     = f"{weight_kg / 1000:.2f}t" if weight_kg else "—"
    coils          = len(parsed.get("coils") or [])
    grade          = parsed.get("grade") or parsed.get("material_type") or "—"
    source_file_url = body.get("source_file_url", "")

    source_line = f"\n<{source_file_url}|View original cert in Drive>" if source_file_url else ""

    result = _slack_api("chat.postMessage", {
        "channel": SLACK_CHANNEL_ID,
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"📄 *New cert received — no PO number found*\n\n"
                        f"*Supplier:* {supplier}\n"
                        f"*Mill cert no:* {mill_cert}\n"
                        f"*Total weight:* {weight_str}  |  *Coils:* {coils}\n"
                        f"*Grade:* {grade}"
                        f"{source_line}\n\n"
                        f"Please enter the VS PO number to continue processing."
                    ),
                },
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Enter PO Number"},
                        "style": "primary",
                        "action_id": "open_po_modal",
                        "value": cert_id,
                    }
                ],
            },
        ],
    })

    if not result.get("ok"):
        raise HTTPException(status_code=500, detail=f"Slack error: {result.get('error')}")

    return {"status": "pending", "cert_id": cert_id}


@app.post("/slack/interactive")
async def slack_interactive(request: Request):
    """
    Slack posts here for all interactive events:
      - block_actions : user clicked "Enter PO Number" → open modal
      - view_submission : user submitted PO number → match + generate + upload PDF
    """
    raw_body  = await request.body()
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    signature = request.headers.get("X-Slack-Signature", "")

    if SLACK_SIGNING_SECRET and not _verify_slack_signature(raw_body, timestamp, signature):
        raise HTTPException(status_code=403, detail="Invalid Slack signature")

    form    = urllib.parse.parse_qs(raw_body.decode())
    payload = json.loads(form.get("payload", ["{}"])[0])

    # ── Button click: open the PO input modal ───────────────────────────────
    if payload.get("type") == "block_actions":
        action     = (payload.get("actions") or [{}])[0]
        cert_id    = action.get("value", "")
        trigger_id = payload.get("trigger_id", "")
        channel_id = payload.get("channel", {}).get("id", "") or SLACK_CHANNEL_ID
        message_ts = payload.get("message", {}).get("ts", "")

        _slack_api("views.open", {
            "trigger_id": trigger_id,
            "view": {
                "type":            "modal",
                "callback_id":     "po_modal",
                "private_metadata": json.dumps({
                    "cert_id":    cert_id,
                    "channel_id": channel_id,
                    "message_ts": message_ts,
                }),
                "title":  {"type": "plain_text", "text": "Enter PO Number"},
                "submit": {"type": "plain_text", "text": "Submit"},
                "close":  {"type": "plain_text", "text": "Cancel"},
                "blocks": [
                    {
                        "type":     "input",
                        "block_id": "po_block",
                        "label":    {"type": "plain_text", "text": "VS PO Number"},
                        "hint":     {"type": "plain_text", "text": "Format: P0XXXX — e.g. P01755"},
                        "element":  {
                            "type":        "plain_text_input",
                            "action_id":   "po_input",
                            "placeholder": {"type": "plain_text", "text": "P01755"},
                        },
                    }
                ],
            },
        })
        return Response(content="", status_code=200)

    # ── Modal submission: process PO number ─────────────────────────────────
    if payload.get("type") == "view_submission":
        view = payload.get("view", {})
        if view.get("callback_id") != "po_modal":
            return Response(content="", status_code=200)

        meta       = json.loads(view.get("private_metadata", "{}"))
        cert_id    = meta.get("cert_id", "")
        channel_id = meta.get("channel_id") or SLACK_CHANNEL_ID
        message_ts = meta.get("message_ts", "")

        values = view.get("state", {}).get("values", {})
        po_num = _normalise_po(values.get("po_block", {}).get("po_input", {}).get("value", "").strip().upper())

        # Validate PO format
        if not _is_vs_po(po_num):
            return {
                "response_action": "errors",
                "errors": {"po_block": f"'{po_num}' doesn't look like a VS PO number (expected P0XXXX). Please check and try again."},
            }

        # Load stored cert
        cert_data = _pop_cert(cert_id)
        if not cert_data:
            return {
                "response_action": "errors",
                "errors": {"po_block": "Cert data not found — it may have been processed already or the server was restarted. Please re-run the cert through Make.com."},
            }

        # Odoo lookup — call directly so the real error surfaces in the modal
        try:
            odoo_data  = odoo.get_neutralisation_data(po_num)
            match_type = "explicit"
            score      = 13
        except Exception as exc:
            _store_cert(cert_id, cert_data)  # put back so user can retry
            err_msg = str(exc)
            print(f"[Odoo] Slack modal lookup failed for {po_num}: {err_msg}")
            return {
                "response_action": "errors",
                "errors": {"po_block": f"Odoo error for {po_num}: {err_msg}"},
            }

        # Generate PDF
        try:
            pdf_bytes = generate_certificate(
                parsed_cert=cert_data,
                odoo_data=odoo_data,
                logo_path=LOGO_PATH or None,
            )
        except Exception as exc:
            _store_cert(cert_id, cert_data)
            return {
                "response_action": "errors",
                "errors": {"po_block": f"PDF generation failed: {exc}"},
            }

        # Upload PDF to Google Drive, then post Slack confirmation
        supplier     = cert_data.get("supplier_name") or cert_data.get("manufacturer") or "cert"
        so_num       = odoo_data.get("so_number", "")
        cert_date_raw = cert_data.get("cert_date") or ""
        cert_date    = cert_date_raw.replace("/", "-").replace(".", "-")
        filename     = f"{cert_date}_{so_num}_{po_num}.pdf".lstrip("_-")

        drive_link = ""
        try:
            drive_link = _gdrive_upload_pdf(pdf_bytes, filename)
        except Exception as exc:
            print(f"[Drive] Upload failed: {exc}")

        # Post confirmation to Slack thread
        if drive_link:
            slack_text = (
                f"✅ *Neutralised cert uploaded to Google Drive*\n"
                f"PO: `{po_num}` · SO: `{so_num}`\n"
                f"<{drive_link}|Open in Drive>"
            )
        else:
            slack_text = (
                f"✅ *Neutralised cert generated* (Drive upload failed — check Railway logs)\n"
                f"PO: `{po_num}` · SO: `{so_num}`"
            )

        _slack_api("chat.postMessage", {
            "channel":   channel_id,
            "thread_ts": message_ts,
            "text":      slack_text,
        })

        # Close the modal
        return {"response_action": "clear"}

    return Response(content="", status_code=200)


@app.get("/health")
def health():
    return {"status": "ok"}
