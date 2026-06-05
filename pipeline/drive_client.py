"""
Google Drive client — uploads the neutralised PDF to the Customer Certs folder.
Uses a Service Account JSON key (stored as GitHub Secret GOOGLE_SERVICE_ACCOUNT_JSON).
"""
import json
import os
from io import BytesIO

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

SCOPES = ["https://www.googleapis.com/auth/drive.file"]

_service = None


def init_from_env():
    """
    Initialises the Drive service from the GOOGLE_SERVICE_ACCOUNT_JSON env var.
    Call this once at startup.
    """
    global _service
    raw_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not raw_json:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON environment variable not set.")

    info = json.loads(raw_json)
    creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    _service = build("drive", "v3", credentials=creds, cache_discovery=False)
    print("[Drive] Service account authenticated.")


def upload_pdf(pdf_bytes: bytes, filename: str, folder_id: str) -> str:
    """
    Uploads a PDF to the specified Google Drive folder.
    Returns the Drive file URL.
    """
    if _service is None:
        raise RuntimeError("Drive client not initialised. Call init_from_env() first.")

    metadata = {
        "name":    filename,
        "parents": [folder_id],
        "mimeType": "application/pdf",
    }
    media = MediaIoBaseUpload(BytesIO(pdf_bytes), mimetype="application/pdf", resumable=False)

    file = _service.files().create(
        body=metadata,
        media_body=media,
        fields="id, webViewLink"
    ).execute()

    file_url = file.get("webViewLink", "")
    print(f"[Drive] Uploaded '{filename}' → {file_url}")
    return file_url
