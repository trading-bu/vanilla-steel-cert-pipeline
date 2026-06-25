"""
PO Log — tracks which line items in each Purchase Order have been certificate'd.
Persistent JSON store: pipeline/po_log.json

Structure:
{
  "P01744": {
    "so_number":     "S01457",
    "buyer_name":    "...",
    "buyer_country": "Italy",
    "created":       "2026-06-25",
    "items": [
      {
        "vsi_id":      "VSI-17887753",
        "weight_t":    2.170,         # from Odoo product_uom_qty (tonnes)
        "description": "DX51D+Z ...", # from Odoo PO line name
        "heat_number": null,          # populated when cert is matched
        "cert_doc_id": null,          # Docsumo doc_id that matched this item
        "cert_date":   null,
        "matched":     false
      },
      ...
    ]
  }
}
"""

import json
from datetime import date
from pathlib import Path

LOG_FILE = Path(__file__).parent / "po_log.json"

# Weight tolerance for matching: ±100 kg (0.1 t)
WEIGHT_TOL_T = 0.10


def load() -> dict:
    if LOG_FILE.exists():
        with open(LOG_FILE) as f:
            return json.load(f)
    return {}


def save(log: dict):
    with open(LOG_FILE, "w") as f:
        json.dump(log, f, indent=2)
    print(f"[POLog] Saved — {len(log)} PO(s) tracked.")


def ensure_po(log: dict, po_number: str, odoo_data: dict) -> dict:
    """
    Ensure PO exists in the log. Creates it from odoo_data if missing.
    If PO already exists, adds any new VSI articles found in odoo_data.
    Returns the PO entry (mutates log in-place).
    """
    if po_number in log:
        existing = log[po_number]
        existing_vsis = {i["vsi_id"] for i in existing.get("items", [])}
        added = 0
        for art in odoo_data.get("vs_articles", []):
            vsi = art.get("vs_article", "–")
            if vsi not in existing_vsis:
                existing["items"].append({
                    "vsi_id":       vsi,
                    "weight_t":     art.get("weight_t"),
                    "description":  art.get("description", ""),
                    "product_name": art.get("product_name", ""),
                    "grade":        art.get("grade", ""),
                    "width_mm":     art.get("width_mm", ""),
                    "thickness_mm": art.get("thickness_mm", ""),
                    "coating":      art.get("coating", ""),
                    "steelmaking":  art.get("steelmaking", ""),
                    "material_type": art.get("material_type", ""),
                    "heat_number":  None,
                    "cert_doc_id":  None,
                    "cert_date":    None,
                    "matched":      False,
                })
                added += 1
        if added:
            print(f"[POLog] {po_number}: added {added} new item(s) from Odoo.")
        return existing

    # First time seeing this PO — create full entry
    items = [
        {
            "vsi_id":       art.get("vs_article", "–"),
            "weight_t":     art.get("weight_t"),
            "description":  art.get("description", ""),
            "product_name": art.get("product_name", ""),
            "grade":        art.get("grade", ""),
            "width_mm":     art.get("width_mm", ""),
            "thickness_mm": art.get("thickness_mm", ""),
            "coating":      art.get("coating", ""),
            "steelmaking":  art.get("steelmaking", ""),
            "material_type": art.get("material_type", ""),
            "heat_number":  None,
            "cert_doc_id":  None,
            "cert_date":    None,
            "matched":      False,
        }
        for art in odoo_data.get("vs_articles", [])
    ]

    log[po_number] = {
        "so_number":     odoo_data.get("so_number", ""),
        "buyer_name":    odoo_data.get("buyer_name", ""),
        "buyer_country": odoo_data.get("buyer_country", ""),
        "created":       str(date.today()),
        "items":         items,
    }
    print(f"[POLog] New PO {po_number}: {len(items)} item(s) logged "
          f"(SO {odoo_data.get('so_number', '?')}, "
          f"buyer: {odoo_data.get('buyer_name', '?')}).")
    return log[po_number]


def find_matching_items(po_entry: dict,
                        cert_weight_kg: float | None,
                        cert_heat: str | None) -> list[dict]:
    """
    Identify which PO line items this cert covers.

    Matching priority:
      1. Heat number — if any item already has this heat recorded (re-run safety)
      2. Weight     — find unmatched item(s) where weight_t ≈ cert_weight_kg / 1000
      3. Fallback   — all unmatched items (cert scope unclear)

    Returns live references into po_entry["items"] — mutations propagate.
    """
    items = po_entry.get("items", [])

    # 1. Heat number match (idempotency: handles re-runs for the same cert)
    if cert_heat:
        heat_matches = [i for i in items if i.get("heat_number") == cert_heat]
        if heat_matches:
            vsis = [i["vsi_id"] for i in heat_matches]
            print(f"[POLog] Heat match '{cert_heat}': {vsis}")
            return heat_matches

    # 2. Weight match against unmatched items
    unmatched = [i for i in items if not i.get("matched")]
    if cert_weight_kg and cert_weight_kg > 0:
        cert_t = cert_weight_kg / 1000.0
        weight_matches = [
            i for i in unmatched
            if i.get("weight_t") is not None
            and abs(i["weight_t"] - cert_t) <= WEIGHT_TOL_T
        ]
        if weight_matches:
            vsis = [i["vsi_id"] for i in weight_matches]
            flag = " ⚠ ambiguous match" if len(weight_matches) > 1 else ""
            print(f"[POLog] Weight match {cert_t:.3f} t{flag}: {vsis}")
            return weight_matches
        else:
            print(f"[POLog] No weight match for {cert_t:.3f} t "
                  f"(tolerance ±{WEIGHT_TOL_T} t). "
                  f"Unmatched items: {[i.get('weight_t') for i in unmatched]}")

    # 3. Fallback
    if unmatched:
        print(f"[POLog] Fallback — returning all {len(unmatched)} unmatched item(s).")
        return unmatched

    print("[POLog] All items already matched — returning full item list.")
    return items


def mark_matched(items: list[dict], cert_heat: str | None,
                 doc_id: str, cert_date: str):
    """Record cert details on matched items and flag them as processed."""
    for item in items:
        item["matched"]     = True
        item["cert_doc_id"] = doc_id
        item["cert_date"]   = cert_date
        if cert_heat:
            item["heat_number"] = cert_heat


def summary(log: dict) -> str:
    """Return a human-readable summary of the log for debugging."""
    lines = [f"PO Log: {len(log)} order(s)"]
    for po, entry in sorted(log.items()):
        items   = entry.get("items", [])
        matched = sum(1 for i in items if i.get("matched"))
        lines.append(
            f"  {po} (SO {entry.get('so_number', '?')}): "
            f"{matched}/{len(items)} item(s) matched"
        )
    return "\n".join(lines)
