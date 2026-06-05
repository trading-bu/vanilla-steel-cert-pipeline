# Certificate Neutralisation Pipeline — Full Setup Guide

## What this does

Every morning at 08:05 Berlin time, a GitHub Action automatically:

1. Checks Docsumo for any new certificates (status: `reviewing`) that haven't been processed yet
2. Pulls the extracted values (PO number, grade, chemistry, etc.)
3. Looks up the Sales Order, buyer country, and VS article numbers from Odoo
4. Generates a neutralised PDF in the Vanilla Steel template
5. Saves it to your Google Drive "Customer Certs" folder — ready to forward to the buyer
6. Records the cert as done in `processed_ids.json` so it's never run twice

Certs stay in `reviewing` status in Docsumo — this pipeline never touches the Docsumo status.

---

## Overview of steps

1. Create a GitHub account and install GitHub Desktop
2. Create the repository and add the pipeline files
3. Set up a Google Service Account (robot Drive access)
4. Add 4 secrets to GitHub
5. Test the pipeline manually
6. Done — automation runs every morning

Estimated total time: **25–35 minutes**

---

---

## STEP 1 — GitHub account

If you already have a GitHub account, skip to Step 2.

1. Go to [github.com](https://github.com) → click **Sign up**
2. Enter your email, create a password, choose a username
3. Verify your email address

---

## STEP 2 — Install GitHub Desktop

GitHub Desktop is a visual app that lets you work with GitHub without using the command line.

1. Go to [desktop.github.com](https://desktop.github.com) → click **Download for Windows**
2. Run the installer
3. Open GitHub Desktop → click **Sign in to GitHub.com**
4. A browser window opens → click **Authorize desktop** → return to GitHub Desktop
5. Set your name and email when prompted (these appear on your commits)

---

## STEP 3 — Create the repository on GitHub

A repository (repo) is a folder on GitHub that stores your code and tracks all changes.

1. In GitHub Desktop, click **File → New repository** (or the **+** button → New repository)
2. Fill in:
   - **Name:** `vanilla-steel-cert-pipeline` (no spaces)
   - **Description:** Certificate neutralisation automation
   - **Local path:** Choose where you want it on your computer (e.g. `C:\Users\MridulGoel\Documents\GitHub\`)
   - **Initialize this repository with a README:** ✅ tick this
3. Click **Create repository**

You now have a folder on your computer and a matching repo on GitHub.

---

## STEP 4 — Add the pipeline files

### 4a. Open the repo folder

In GitHub Desktop → click **Repository → Show in Explorer**. This opens the folder in Windows Explorer.

### 4b. Create the folder structure

Inside the repo folder, create a folder called `pipeline`. Then inside `pipeline`, create another folder called `.github`, and inside that a folder called `workflows`.

Your folder structure should look like this:
```
vanilla-steel-cert-pipeline/
    pipeline/
        main.py
        docsumo_client.py
        odoo_client.py
        pdf_generator.py
        drive_client.py
        requirements.txt
        processed_ids.json
        NEW VS LOGO.jpg
    .github/
        workflows/
            neutralise.yml
    README.md
```

### 4c. Copy all the pipeline files

From your `Neutralising certificates` project folder, copy these files into the repo's `pipeline/` folder:
- `main.py`
- `docsumo_client.py`
- `odoo_client.py`
- `pdf_generator.py`
- `drive_client.py`
- `requirements.txt`
- `processed_ids.json`
- `NEW VS LOGO.jpg`

Then copy `neutralise.yml` into `.github/workflows/`.

> **Important:** The `.github` folder name starts with a dot. Windows Explorer might not show it by default. To create it: in Explorer, click the address bar, type the full path including `.github`, press Enter.

### 4d. Commit and push the files

Switch back to GitHub Desktop. You'll see all the new files listed on the left under "Changes".

1. In the bottom-left box, type a commit message: `Add certificate neutralisation pipeline`
2. Click **Commit to main**
3. Click **Publish repository** (top bar) — this uploads everything to GitHub
   - Make sure **Keep this code private** is ticked ✅ (your API keys will be in Secrets, but keep the repo private anyway)
4. Click **Publish Repository**

Your files are now on GitHub at `github.com/YOUR-USERNAME/vanilla-steel-cert-pipeline`.

---

## STEP 5 — Google Service Account

### Why you need this

GitHub Actions runs in the cloud with no browser — it cannot log in to Google Drive the normal way. A **Service Account** is a robot Google account that has its own API credentials. You share your Drive folder with it (like adding a colleague), and the script can then save files there automatically.

### 5a. Create a Google Cloud project

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Click the project dropdown at the top → **New Project**
3. Name it: `vanilla-steel-certs` → click **Create**
4. Wait a few seconds for it to be created, then select it from the dropdown

### 5b. Enable the Google Drive API

1. In the left sidebar, click **APIs & Services → Library**
2. Search for `Google Drive API`
3. Click it → click **Enable**

### 5c. Create the Service Account

1. In the left sidebar, click **IAM & Admin → Service Accounts**
2. Click **+ Create Service Account** at the top
3. Fill in:
   - **Service account name:** `cert-pipeline`
   - **Service account ID:** auto-filled (leave it)
4. Click **Create and Continue**
5. Skip the "Grant this service account access" step → click **Continue**
6. Skip the "Grant users access" step → click **Done**

### 5d. Download the JSON key

1. You'll see your new service account in the list. Click on it.
2. Click the **Keys** tab
3. Click **Add Key → Create new key**
4. Select **JSON** → click **Create**
5. A `.json` file downloads automatically — **do not lose this file**. It's the service account's password.
6. Note the **email address** of the service account — it looks like:
   `cert-pipeline@vanilla-steel-certs.iam.gserviceaccount.com`

### 5e. Share your Drive folder with the Service Account

1. Go to [drive.google.com](https://drive.google.com)
2. Create a folder called **"Customer Certs"** (if you don't have one already)
3. Right-click the folder → **Share**
4. Paste the service account email address (from 5d) into the share box
5. Set role to **Editor**
6. Click **Send** (it may say "no notification will be sent" — that's fine)
7. **Copy the folder ID** from the URL — when you open the folder, the URL looks like:
   `https://drive.google.com/drive/folders/1ABC123xyzDEF456`
   The part after `/folders/` is your folder ID: `1ABC123xyzDEF456`

---

## STEP 6 — Add GitHub Secrets

Secrets are encrypted values stored on GitHub — your code reads them as environment variables, but they're never visible to anyone, including in the logs.

1. Go to your repo on GitHub: `github.com/YOUR-USERNAME/vanilla-steel-cert-pipeline`
2. Click **Settings** (top navigation bar)
3. In the left sidebar, click **Secrets and variables → Actions**
4. Click **New repository secret** for each of the following:

---

**Secret 1:**
- Name: `DOCSUMO_API_KEY`
- Value: `ysxF7NMxah2hz8bMmzt6CDDDUDLwCQ5j4fOKso3vgSGdmQEt0b72MubNWBwg`

---

**Secret 2:**
- Name: `ODOO_API_KEY`
- Value: `eea7aafaf42cd10d24a2b714fbee32f197efe630`

---

**Secret 3:**
- Name: `GOOGLE_SERVICE_ACCOUNT_JSON`
- Value: Open the JSON file you downloaded in Step 5d, **select all the text inside it**, paste it as the secret value

---

**Secret 4:**
- Name: `CUSTOMER_CERTS_FOLDER_ID`
- Value: The folder ID from Step 5e (e.g. `1ABC123xyzDEF456`)

---

After adding all four, your Secrets page should show 4 secrets listed.

---

## STEP 7 — Test the pipeline manually

1. Go to your repo on GitHub
2. Click **Actions** in the top navigation
3. In the left sidebar, click **Neutralise Certificates**
4. Click **Run workflow** (top right of the run list) → **Run workflow** again in the popup
5. A new run appears — click on it to watch the live log

### What a successful run looks like:

```
[Docsumo] Found 2 cert(s) in 'reviewing' status.
[Tracker] 0 cert(s) already processed in previous runs.
2 new cert(s) to process.

────────────────────────────────────────────────────
Processing: 2026-05-05_orimartin.it_22889800006.pdf
  doc_id: 2b875f97...
  VS PO:  P01655
  SO:     S01448
  Buyer:  ACME Customer GmbH (Germany)
  Generating neutralised PDF...
  ✅ Done — 2026-05-05_S01448_P01655.pdf
     https://drive.google.com/file/d/...

════════════════════════════════════════════════════
Run complete.  Processed: 2  |  Errors: 0
```

The neutralised PDF now appears in your Google Drive "Customer Certs" folder.

### If you see errors:

| Error message | Fix |
|---|---|
| `No VS PO number extracted` | The "Vanilla Steel Order Number" field isn't set up in Docsumo Field Setup yet |
| `No purchase order found in Odoo for 'P01655'` | PO doesn't exist in Odoo yet, or the number Docsumo extracted is slightly wrong |
| `GOOGLE_SERVICE_ACCOUNT_JSON environment variable not set` | The secret name is misspelled — check Step 6 |
| `Logo not found` | `NEW VS LOGO.jpg` was not copied into the `pipeline/` folder in the repo |
| `403 Forbidden` on Drive upload | The service account email was not given Editor access to the folder — redo Step 5e |

---

## STEP 8 — Making changes later

Whenever you need to update the script (e.g. fix a field name, update the PDF layout):

1. Edit the file on your computer (in the repo folder)
2. Open **GitHub Desktop** — it automatically detects the change
3. Write a commit message describing what you changed
4. Click **Commit to main**
5. Click **Push origin** (top bar) — this uploads the change to GitHub
6. The next time the workflow runs, it uses the updated code

---

## Your daily workflow (once set up)

1. Cert arrives in Gmail → Apps Script sends it to Docsumo + "Supplier Certs" Drive folder
2. Docsumo automatically extracts the fields (cert stays in `reviewing` status)
3. Optionally: open Docsumo and check the extracted values are correct
4. At **08:05 the next morning**, the pipeline runs automatically
5. Neutralised cert appears in Google Drive "Customer Certs" — ready to forward to buyer

**To process a cert immediately** (without waiting until morning):
Go to GitHub → Actions → Neutralise Certificates → Run workflow

---

## How the "already processed" tracking works

After each successful neutralisation, the cert's Docsumo doc_id is added to `processed_ids.json` in the repo. GitHub Actions automatically commits this file back to the repo after each run. On the next run, any cert whose doc_id is already in this file is skipped — so the same cert is never neutralised twice, even though its Docsumo status never changes.

If you ever want to re-run a cert (e.g. you fixed the field setup and want a corrected version), just open `processed_ids.json` in the repo, delete the relevant doc_id, commit and push — the next run will pick it up again.
