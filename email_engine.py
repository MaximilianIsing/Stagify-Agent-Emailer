import hashlib
import json
import re

import requests

from config import CACHE_DIR, RESEND_URL, STAGE_ENDPOINT, load_config
from debug_settings import debug_subject, resolve_recipients

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


def normalize_email(email):
    return email.strip().lower()


def dedup_key(email, address):
    raw = f"{normalize_email(email)}|{address.strip().lower()}"
    return hashlib.sha256(raw.encode()).hexdigest()


def draft_id_for_row(row):
    return dedup_key(row["email"], row["address"])[:16]


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

    for kw, rt in [
        ("living", "Living Room"),
        ("bed", "Bedroom"),
        ("kitchen", "Kitchen"),
        ("bath", "Bathroom"),
        ("dining", "Dining Room"),
        ("table", "Dining Room"),
    ]:
        if kw in lower:
            return rt, additional
    return "Living Room", additional


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


def download_listing_image(image_url):
    try:
        img_resp = requests.get(image_url, timeout=30)
        img_resp.raise_for_status()
    except Exception:
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
    return img_resp.content, mime, ext


def stage_image(image_url, room_type, remove_furniture, additional_prompt="",
                image_content=None, image_mime=None, image_ext=None):
    cfg = load_config()
    if image_content is None:
        downloaded = download_listing_image(image_url)
        if not downloaded:
            return None
        image_content, image_mime, image_ext = downloaded

    files = {"image": (f"listing.{image_ext}", image_content, image_mime)}
    data = {
        "roomType": room_type,
        "furnitureStyle": "Modern",
        "removeFurniture": "true" if remove_furniture else "false",
    }
    base_prompt = (
        "Virtually stage this room to look its best for a real estate listing. "
        "Replace or upgrade key furniture pieces with modern, stylish alternatives "
        "while keeping the layout realistic and livable. "
        "Never duplicate items like TVs, sofas, or tables. "
        "Maintain a cohesive, magazine-quality look that a buyer would find aspirational."
    )
    if additional_prompt:
        data["additionalPrompt"] = f"{base_prompt}. {additional_prompt}"
    else:
        data["additionalPrompt"] = base_prompt

    try:
        resp = requests.post(
            STAGE_ENDPOINT,
            params={"key": cfg["stagify_key"]},
            files=files,
            data=data,
            timeout=180,
        )
        resp.raise_for_status()
        result = resp.json()
    except Exception:
        return None

    if not result.get("success"):
        return None

    img = result.get("image")
    if not img:
        images = result.get("images")
        if images:
            img = images[0]
    return img


def build_html_email(name, address, image_src="cid:staged_image"):
    first_name = name.split()[0] if name else "there"

    return f"""<!DOCTYPE html>
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
          <tr>
            <td style="padding:0 40px 24px; text-align:center;">
              <img src="{image_src}" alt="Virtually Staged - {address}"
                   style="max-width:100%; width:520px; border-radius:8px;
                          box-shadow:0 2px 12px rgba(0,0,0,0.12);" />
              <p style="font-size:11px; color:#9ca3af; margin:8px 0 0;
                        font-style:italic;">Virtually staged by Stagify</p>
            </td>
          </tr>
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
                      <img src="https://stagify.ai/logo-full.png"
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
</html>"""


def extract_b64_and_mime(data_url):
    if data_url.startswith("data:"):
        header, raw = data_url.split(",", 1)
        mime = header.split(":")[1].split(";")[0]
        return raw, mime
    return data_url, "image/png"


def send_email(to_email, subject, html, staged_image_data):
    cfg = load_config()
    recipients, _debug_to = resolve_recipients(to_email)

    raw_b64, mime = extract_b64_and_mime(staged_image_data)
    ext = mime.split("/")[-1].replace("jpeg", "jpg")

    payload = {
        "from": f"Stagify Team <{cfg['from_email']}>",
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
                f"{normalize_email(to_email)}{subject}".encode()
            ).hexdigest(),
        },
    }

    resp = requests.post(
        RESEND_URL,
        headers={
            "Authorization": f"Bearer {cfg['resend_key']}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()
