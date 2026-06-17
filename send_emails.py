#!/usr/bin/env python3
"""
Stagify Compass Broker Email Campaign
Stages listing images via Stagify API and sends personalized HTML emails via Resend.
"""

import argparse
import csv
import requests
import json
import time
import hashlib
import re
import sys
from pathlib import Path
from urllib.parse import quote

from debug_settings import debug_subject, is_debug_active, load_debug_settings, resolve_recipients, tracking_email_for_send
from csv_parser import parse_csv as engine_parse_csv

SCRIPT_DIR = Path(__file__).parent

# ─── Load configuration ─────────────────────────────────────────────────────
STAGIFY_KEY = SCRIPT_DIR.joinpath("key.txt").read_text().strip()
RESEND_KEY = SCRIPT_DIR.joinpath("resendkey.txt").read_text().strip()
FROM_EMAIL = SCRIPT_DIR.joinpath("email.txt").read_text().strip()

STAGIFY_HOST = "https://stagify.ai"
STAGE_ENDPOINT = f"{STAGIFY_HOST}/api/stage-by-endpoint-key"
RESEND_URL = "https://api.resend.com/emails"
CACHE_DIR = SCRIPT_DIR / "staged_cache"

STAGE_DELAY = 3
EMAIL_DELAY = 0.5


# ─── Room type normalization ────────────────────────────────────────────────
ROOM_TYPE_MAP = {
    "living room": "Living Room",
    "bedroom": "Bedroom",
    "kitchen": "Kitchen",
    "dining room table": "Dining Room",
    "dining room": "Dining Room",
    "bathroom": "Bathroom",
    "outdoors space": "Patio",
    "patio": "Patio",
    "counter top / table": "Kitchen",
    "counter top": "Kitchen",
    "movie room with living room": "Living Room",
    "beauty salon": "Living Room",
    "bedroom / living room": "Living Room",
    "office": "Office",
}


def normalize_room_type(raw_type):
    if not raw_type:
        return "Living Room", ""

    clean = raw_type.strip()
    lower = clean.lower()

    additional = ""
    paren = re.search(r"\((.+?)\)", clean)
    if paren:
        additional = paren.group(1)
        lower = re.sub(r"\s*\(.+?\)", "", lower).strip()
        clean = re.sub(r"\s*\(.+?\)", "", clean).strip()

    if lower in ROOM_TYPE_MAP:
        return ROOM_TYPE_MAP[lower], additional

    for key, val in ROOM_TYPE_MAP.items():
        if lower.startswith(key):
            leftover = clean[len(key):].strip().lstrip("/").strip()
            return val, additional or leftover

    for kw, rt in [("living", "Living Room"), ("bed", "Bedroom"),
                    ("kitchen", "Kitchen"), ("bath", "Bathroom"),
                    ("dining", "Dining Room"), ("table", "Dining Room")]:
        if kw in lower:
            return rt, additional
    return "Living Room", additional


# ─── CSV parsing ────────────────────────────────────────────────────────────
def parse_csv():
    rows = []
    with open(CSV_FILE, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = row.get("Name", "").strip()
            email = row.get("Email Address", "").strip()
            address = row.get("Listing Address", "").strip()
            image_url = row.get("Image URL", "").strip()
            staged_flag = row.get("Staged?", "").strip()
            image_type = row.get("Image Type", "").strip()

            if not name or name.startswith("-"):
                continue
            if not email or "@" not in email:
                continue
            if not address or address.startswith("-"):
                continue
            if not image_url or not image_url.startswith("http"):
                continue
            # Skip URLs that are listing pages, not direct images
            if "/homedetails/" in image_url:
                continue

            room_type, additional_prompt = normalize_room_type(image_type)

            rows.append({
                "name": name,
                "email": email,
                "address": address,
                "image_url": image_url,
                "remove_furniture": staged_flag.lower() == "yes",
                "room_type": room_type,
                "additional_prompt": additional_prompt,
            })
    return rows


# ─── Cache helpers ───────────────────────────────────────────────────────────
def cache_key(url):
    return hashlib.sha256(url.encode()).hexdigest()[:16]


def load_cached(url):
    path = CACHE_DIR / f"{cache_key(url)}.txt"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return None


def save_cached(url, data):
    CACHE_DIR.mkdir(exist_ok=True)
    path = CACHE_DIR / f"{cache_key(url)}.txt"
    path.write_text(data, encoding="utf-8")


# ─── Stagify API staging ────────────────────────────────────────────────────
def stage_image(image_url, room_type, remove_furniture, additional_prompt=""):
    try:
        img_resp = requests.get(image_url, timeout=30)
        img_resp.raise_for_status()
    except Exception as e:
        print(f"    [ERROR] Download failed: {e}")
        return None

    ct = img_resp.headers.get("Content-Type", "image/jpeg")
    if "webp" in ct or image_url.split("?")[0].rstrip("p").endswith(".web"):
        ext, mime = "webp", "image/webp"
    elif "png" in ct or image_url.endswith(".png"):
        ext, mime = "png", "image/png"
    elif "jpg" in ct or "jpeg" in ct or image_url.endswith((".jpg", ".jpeg")):
        ext, mime = "jpg", "image/jpeg"
    else:
        ext, mime = "jpg", "image/jpeg"

    files = {"image": (f"listing.{ext}", img_resp.content, mime)}
    data = {
        "roomType": room_type,
        "furnitureStyle": "Modern",
        "removeFurniture": "true" if remove_furniture else "false",
    }
    base_prompt = (
        "Virtually stage this room for a real estate listing with a cohesive modern theme. "
        "Replace or upgrade as many furniture pieces as possible with contemporary, "
        "stylish modern alternatives while keeping the layout realistic and livable. "
        "Prioritize a clean, modern aesthetic throughout the room. "
        "Never duplicate items like TVs, sofas, or tables. "
        "Maintain a magazine-quality look that a buyer would find aspirational."
    )
    if additional_prompt:
        data["additionalPrompt"] = f"{base_prompt}. {additional_prompt}"
    else:
        data["additionalPrompt"] = base_prompt

    try:
        resp = requests.post(
            STAGE_ENDPOINT,
            params={"key": STAGIFY_KEY},
            files=files,
            data=data,
            timeout=180,
        )
        resp.raise_for_status()
        result = resp.json()
    except Exception as e:
        print(f"    [ERROR] Staging API failed: {e}")
        if hasattr(e, "response") and e.response is not None:
            try:
                print(f"    Response: {e.response.text[:300]}")
            except Exception:
                pass
        return None

    if not result.get("success"):
        print(f"    [ERROR] Staging unsuccessful: {json.dumps(result)[:200]}")
        return None

    img = result.get("image")
    if not img:
        images = result.get("images")
        if images and len(images) > 0:
            img = images[0]
    return img


# ─── HTML email template ────────────────────────────────────────────────────
def build_html_email(name, address, email="", track_opens=True):
    first_name = name.split()[0] if name else "there"
    if track_opens and (email or "").strip():
        logo_src = (
            "https://stagify.ai/email/logo.png?"
            f"email={quote(email.strip(), safe='')}"
        )
    else:
        logo_src = "https://stagify.ai/logo-full.png"

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Stagify Virtual Staging</title>
  <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap');
  </style>
</head>
<body style="margin:0; padding:0; background-color:#ddeeff;
             font-family:'Inter',Arial,Helvetica,sans-serif;
             -webkit-font-smoothing:antialiased;">
  <table width="100%" cellpadding="0" cellspacing="0" border="0"
         style="background-color:#ddeeff; padding:40px 20px;">
    <tr>
      <td align="center">
        <table width="600" cellpadding="0" cellspacing="0" border="0"
               style="background-color:#ffffff; border-radius:12px;
                      overflow:hidden;
                      box-shadow:0 4px 20px rgba(59,130,246,0.10);">

          <!-- Header -->
          <tr>
            <td style="background-color:rgb(37, 99, 235); padding:30px 40px;
                        text-align:center;">
              <h1 style="margin:0; font-size:30px;
                         font-family:'Inter',Arial,Helvetica,sans-serif;
                         font-weight:700; letter-spacing:0;">
                <a href="https://stagify.ai"
                   style="color:#ffffff; text-decoration:none;">stagify.ai</a>
              </h1>
              <p style="color:#bfdbfe; margin:6px 0 0; font-size:12px;
                        letter-spacing:0.5px; line-height:1.5; white-space:nowrap;">
                Free virtual staging with one click</p>
            </td>
          </tr>

          <!-- Body text -->
          <tr>
            <td style="padding:36px 40px 24px;">
              <p style="font-size:16px; color:#1f2937; line-height:1.7;
                        margin:0 0 14px;">
                Dear {first_name},
              </p>
              <p style="font-size:16px; color:#1f2937; line-height:1.7;
                        margin:0 0 8px;">
                We&#39;ve stumbled across your listing for
                <strong style="color:#3b82f6;">{address}</strong>,
                and staged it for you. Try our virtual staging for free at
                <a href="https://stagify.ai"
                   style="color:#3b82f6; font-weight:600;
                          text-decoration:none;
                          border-bottom:2px solid #3b82f6;">Stagify.ai</a>.
              </p>
            </td>
          </tr>

          <!-- Staged image -->
          <tr>
            <td style="padding:0 40px 24px; text-align:center;">
              <img src="cid:staged_image" alt="Virtually Staged - {address}"
                   style="max-width:100%; width:520px; border-radius:8px;
                          box-shadow:0 2px 12px rgba(0,0,0,0.12);" />
              <p style="font-size:11px; color:#9ca3af; margin:8px 0 0;
                        font-style:italic;">Virtually staged by Stagify</p>
            </td>
          </tr>

          <!-- CTA button -->
          <tr>
            <td style="padding:8px 40px 36px; text-align:center;">
              <a href="https://stagify.ai"
                 style="background-color:#3b82f6; color:#ffffff;
                        padding:14px 36px; text-decoration:none;
                        border-radius:8px; font-size:16px;
                        font-weight:600; display:inline-block;
                        box-shadow:0 2px 8px rgba(59,130,246,0.25);">
                Try Stagify for Free</a>
            </td>
          </tr>

          <!-- Footer -->
          <tr>
            <td style="background-color:#f0f7ff; padding:26px 40px;
                        border-top:2px solid #dbeafe;">
              <p style="font-size:15px; color:#4b5563; margin:0 0 10px;">
                Check us out,</p>
              <table cellpadding="0" cellspacing="0" border="0">
                <tr>
                  <td style="vertical-align:middle;">
                    <p style="font-size:17px; color:#3b82f6; font-weight:700;
                              margin:0;">Stagify Team</p>
                  </td>
                  <td style="padding-left:10px; vertical-align:middle;">
                    <a href="https://stagify.ai" style="text-decoration:none;">
                      <img src="{logo_src}"
                           alt="Stagify" width="36" height="36"
                           style="display:block; width:36px; height:36px;
                                  border:0;" />
                    </a>
                  </td>
                </tr>
              </table>
              <p style="font-size:12px; color:#9ca3af; margin:12px 0 0;">
                <a href="https://stagify.ai"
                   style="color:#6b7280; text-decoration:none;">stagify.ai</a>
              </p>
            </td>
          </tr>

        </table>
      </td>
    </tr>
  </table>
</body>
</html>'''


# ─── Resend email sending ───────────────────────────────────────────────────
def extract_b64_and_mime(data_url):
    """Pull raw base64 and MIME type out of a data-URI or plain base64 string."""
    if data_url.startswith("data:"):
        header, raw = data_url.split(",", 1)
        mime = header.split(":")[1].split(";")[0]
        return raw, mime
    return data_url, "image/png"


def send_email(to_email, subject, html, staged_image_data):
    recipients, _ = resolve_recipients(to_email)

    raw_b64, mime = extract_b64_and_mime(staged_image_data)
    ext = mime.split("/")[-1].replace("jpeg", "jpg")

    payload = {
        "from": f"Stagify Team <{FROM_EMAIL}>",
        "to": recipients,
        "subject": debug_subject(subject, to_email),
        "html": html,
        "attachments": [
            {
                "filename": f"staged-room.{ext}",
                "content": raw_b64,
                "content_type": mime,
                "content_id": "staged_image",
            }
        ],
        "headers": {
            "X-Entity-Ref-ID": hashlib.md5(
                f"{to_email}{subject}".encode()
            ).hexdigest(),
        },
    }

    try:
        resp = requests.post(
            RESEND_URL,
            headers={
                "Authorization": f"Bearer {RESEND_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.HTTPError as e:
        print(f"    [ERROR] Email send failed ({e.response.status_code}): "
              f"{e.response.text[:200]}")
        return None
    except Exception as e:
        print(f"    [ERROR] Email send failed: {e}")
        return None


# ─── Main ───────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Stagify email campaign")
    parser.add_argument("--limit", type=int, default=0,
                        help="Max number of emails to send (0 = all)")
    parser.add_argument("--csv", type=str, default="",
                        help="Path to a CSV file (default: active uploads in data/csvs/)")
    args = parser.parse_args()
    limit = args.limit

    print("=" * 64)
    print("  STAGIFY — COMPASS BROKER EMAIL CAMPAIGN")
    print("=" * 64)
    debug_settings = load_debug_settings()
    if is_debug_active():
        mode = f"ON -> all emails go to {debug_settings['email']}"
    else:
        mode = "OFF"
    print(f"  Debug mode : {mode}")
    print(f"  Stagify API: {STAGIFY_HOST}")
    print(f"  From       : {FROM_EMAIL}")
    if limit:
        print(f"  Limit      : {limit} email(s)")
    print()

    rows = engine_parse_csv(args.csv if args.csv else None)
    if isinstance(rows, dict):
        rows = rows["rows"]
    if limit:
        rows = rows[:limit]
    print(f"  Parsed {len(rows)} rows to process")

    # Deduplicate images for staging
    unique_images = {}
    for row in rows:
        url = row["image_url"]
        if url not in unique_images:
            unique_images[url] = row

    print(f"  Unique images to stage: {len(unique_images)}")

    CACHE_DIR.mkdir(exist_ok=True)
    cached_count = sum(1 for url in unique_images if load_cached(url))
    to_stage = [url for url in unique_images if not load_cached(url)]
    print(f"  Already cached: {cached_count}")
    print(f"  Need to stage : {len(to_stage)}")
    print()

    # ── Stage images ─────────────────────────────────────────────────────
    if to_stage:
        print("  STAGING IMAGES")
        print("  " + "-" * 58)
        for i, url in enumerate(to_stage):
            info = unique_images[url]
            print(f"\n  [{i+1}/{len(to_stage)}] {info['address']}")
            print(f"    Room: {info['room_type']}  |  "
                  f"Remove furniture: {info['remove_furniture']}")

            result = stage_image(
                url,
                info["room_type"],
                info["remove_furniture"],
                info.get("additional_prompt", ""),
            )

            if result:
                save_cached(url, result)
                print(f"    [OK] Staged successfully")
            else:
                print(f"    [FAIL] Failed -- will skip in emails")

            if i < len(to_stage) - 1:
                time.sleep(STAGE_DELAY)

        print()

    # ── Send emails ──────────────────────────────────────────────────────
    staged_count = sum(1 for url in unique_images if load_cached(url))
    print(f"  SENDING EMAILS  ({staged_count} staged images available)")
    print("  " + "-" * 58)

    sent = 0
    failed = 0
    skipped = 0

    for i, row in enumerate(rows):
        staged_data = load_cached(row["image_url"])
        if not staged_data:
            skipped += 1
            continue

        html = build_html_email(
            row["name"],
            row["address"],
            tracking_email_for_send(row["email"]),
        )
        subject = row["address"]
        recipients, _ = resolve_recipients(row["email"])
        target = recipients[0]

        print(f"  [{i+1}/{len(rows)}]  ->  {target}  |  {subject}")

        result = send_email(row["email"], subject, html, staged_data)
        if result and result.get("id"):
            sent += 1
        else:
            failed += 1

        time.sleep(EMAIL_DELAY)

    print()
    print("=" * 64)
    print(f"  DONE -- {sent} sent  |  {failed} failed  |  {skipped} skipped")
    print("=" * 64)


if __name__ == "__main__":
    main()
