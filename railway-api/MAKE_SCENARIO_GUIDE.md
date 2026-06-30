# Make.com Scenario — VS Certificate Neutralisation
## Step-by-step configuration guide

---

## Overview

One Make.com scenario, 5 modules:

```
[1] GDrive Watch  →  [2] GDrive Download  →  [3] Claude API (extract)
                                                      ↓
[5] GDrive Upload  ←  [4] Railway API (generate PDF)
```

The Railway API handles Odoo matching and PDF generation internally.  
Make.com just moves data between services.

---

## Prerequisites

Before building the scenario:
- [ ] Railway endpoint deployed and `/health` returns `{"status":"ok"}`
- [ ] `ODOO_API_KEY` set in Railway environment variables
- [ ] `MAKE_SECRET` set in Railway (and noted for step 4 below)
- [ ] GDrive folders created:
  - `/Supplier Certs Inbox` — where supplier PDFs are dropped
  - `/Neutralised Certs Output` — where the finished certs are saved
- [ ] Claude API key available

---

## Module 1 — Google Drive: Watch Files in a Folder

| Setting | Value |
|---------|-------|
| Connection | Your Google account |
| Drive | My Drive |
| Folder | `/Supplier Certs Inbox` |
| What to watch | New Files Only |
| Maximum number of files | 1 |
| Filter | File extension = `pdf` |

**Scheduling:** Set scenario to run every 15 minutes (or use a Google Drive push trigger for near-instant response).

---

## Module 2 — Google Drive: Download a File

| Setting | Value |
|---------|-------|
| Connection | Your Google account |
| File ID | `{{1.id}}` (from Module 1 output) |
| Convert to Base64 | ✅ Yes |

This outputs the PDF as a base64 string (`{{2.data}}`).

---

## Module 3 — HTTP: Make a request (Claude API)

| Setting | Value |
|---------|-------|
| URL | `https://api.anthropic.com/v1/messages` |
| Method | POST |
| Headers | See below |
| Body type | Raw |
| Content type | application/json |
| Request content | See below |
| Parse response | ✅ Yes |

**Headers:**
```
x-api-key:          <your Claude API key>
anthropic-version:  2023-06-01
content-type:       application/json
```

**Request content (paste as raw JSON):**
```json
{
  "model": "claude-sonnet-4-6",
  "max_tokens": 8192,
  "system": "<PASTE THE FULL SYSTEM PROMPT FROM CLAUDE_EXTRACTION_PROMPT.md>",
  "messages": [
    {
      "role": "user",
      "content": [
        {
          "type": "document",
          "source": {
            "type": "base64",
            "media_type": "application/pdf",
            "data": "{{2.data}}"
          }
        },
        {
          "type": "text",
          "text": "Extract all data from this steel inspection certificate and return as JSON."
        }
      ]
    }
  ]
}
```

**After this module:** Add a **Tools > Parse JSON** module.
- String to parse: `{{3.content[].text}}` → select the first item → `text` field
- This gives you a usable JSON object for Module 4.

---

## Module 4 — HTTP: Make a request (Railway API)

| Setting | Value |
|---------|-------|
| URL | `https://your-app.railway.app/generate-cert` |
| Method | POST |
| Headers | See below |
| Body type | Raw |
| Content type | application/json |
| Request content | See below |
| Parse response | ✅ Yes |

**Headers:**
```
content-type:   application/json
X-Make-Secret:  <your MAKE_SECRET value>
```

**Request content:**
```json
{
  "cert_json": {{parseJSON_module_output}}
}
```

Replace `{{parseJSON_module_output}}` with the full JSON object from the Parse JSON module (use Make.com's variable picker to select the entire parsed object).

**Expected response fields:**
- `status` — `"ok"` or `"error"`
- `pdf_base64` — the neutralised PDF as base64
- `filename` — suggested filename (e.g. `29-06-2026_S01448_P01655.pdf`)
- `so_number`, `buyer_name`, `buyer_country` — for logging / notification
- `match_confidence` — Odoo match score (for debugging)

**Handle errors:** Add a Make.com Error Handler or Router to catch `422` (no Odoo match) and `500` (server error). On error, have Make.com send a Slack message or email with the cert filename and error details for manual review.

---

## Module 5 — Google Drive: Upload a File

| Setting | Value |
|---------|-------|
| Connection | Your Google account |
| Drive | My Drive |
| Folder | `/Neutralised Certs Output` |
| File name | `{{4.filename}}` |
| Data | `{{4.pdf_base64}}` |
| Data is base64 | ✅ Yes |
| Convert to | application/pdf |

---

## Optional: Move processed file

After Module 5, add a **Google Drive: Move a File** module:
- File ID: `{{1.id}}`
- New folder: `/Supplier Certs Processed`

This prevents the same cert from being re-triggered on the next poll.

---

## Optional: Slack notification

Add a **Slack: Create a Message** module after Module 5:

```
✅ Cert neutralised: {{4.filename}}
SO: {{4.so_number}}  |  Buyer: {{4.buyer_name}}  |  Country: {{4.buyer_country}}
Odoo match confidence: {{4.match_confidence}}
```

---

## Error handling

| Error | Cause | Action |
|-------|-------|--------|
| Module 3 fails | Claude API error | Retry once; Slack alert |
| Module 4 returns `422` | No Odoo PO match found | Slack alert with cert filename — manual review needed |
| Module 4 returns `500` | Railway server error | Check Railway logs |
| Module 5 fails | GDrive upload error | File likely already exists — check output folder |

---

## Testing the scenario

1. Drop a known cert PDF into `/Supplier Certs Inbox`
2. Run the scenario manually (Run Once button)
3. Check each module's output in the execution log
4. Verify the neutralised PDF appears in `/Neutralised Certs Output`
5. Open the PDF and confirm: SO number in header, VS Article per coil, no supplier branding

---

## Railway deployment quick-start

```bash
# From the railway-api/ folder in this project:
cd railway-api
git init
git add .
git commit -m "VS cert API initial"

# Push to GitHub, then connect the repo in railway.app
# Set environment variable: ODOO_API_KEY = eea7aafaf42cd10d24a2b714fbee32f197efe630
# Set environment variable: MAKE_SECRET = <choose a random string>
# Railway auto-detects Procfile and deploys
```

**Important:** Copy `NEW VS LOGO.jpg` from `pipeline/` into `railway-api/` before committing.  
It's already there if you ran the setup script.
