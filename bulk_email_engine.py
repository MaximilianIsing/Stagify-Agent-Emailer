import hashlib
import html
import re

import requests

from config import RESEND_URL, load_config
from debug_settings import debug_subject, resolve_recipients
from email_engine import _footer_logo_src, normalize_email

PLACEHOLDER_RE = re.compile(r"\{([A-Za-z0-9_ ]+)\}")
IMAGE_PLACEHOLDER_RE = re.compile(r"\{image(\d+)\}", re.IGNORECASE)


def _image_tag(url, index):
    safe_url = html.escape(url.strip(), quote=True)
    return (
        f'<img src="{safe_url}" alt="Image {index}" '
        f'style="max-width:100%; width:520px; border-radius:8px; '
        f'margin:16px auto; display:block; '
        f'box-shadow:0 2px 12px rgba(0,0,0,0.12);" />'
    )


def substitute_text(template, params, image_urls=None):
    """Replace {ColumnName} and {image1} placeholders."""
    if not template:
        return ""

    image_urls = image_urls or []

    def repl_image(match):
        idx = int(match.group(1))
        if 1 <= idx <= len(image_urls):
            return _image_tag(image_urls[idx - 1], idx)
        return match.group(0)

    text = IMAGE_PLACEHOLDER_RE.sub(repl_image, template)

    def repl_param(match):
        key = match.group(1).strip()
        if key.lower().startswith("image"):
            return match.group(0)
        val = params.get(key)
        if val is None:
            for pk, pv in params.items():
                if pk.lower() == key.lower():
                    val = pv
                    break
        if val is None:
            return match.group(0)
        return html.escape(str(val))

    return PLACEHOLDER_RE.sub(repl_param, text)


def _body_to_html(text):
    if not text or not text.strip():
        return (
            '              <p style="font-size:16px; color:#1f2937; line-height:1.7; margin:0;">'
            "&nbsp;</p>"
        )
    paragraphs = re.split(r"\n\s*\n", text.strip())
    parts = []
    for para in paragraphs:
        chunks = []
        for line in para.split("\n"):
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("<img "):
                chunks.append(stripped)
            else:
                chunks.append(html.escape(stripped))
        if not chunks:
            continue
        inner = "<br />\n                ".join(chunks)
        parts.append(
            f'              <p style="font-size:16px; color:#1f2937; line-height:1.7;\n'
            f'                        margin:0 0 14px;">\n'
            f"                {inner}\n"
            f"              </p>"
        )
    return "\n".join(parts) if parts else (
        '              <p style="font-size:16px; color:#1f2937; line-height:1.7; margin:0;">'
        "&nbsp;</p>"
    )


def build_bulk_html_email(body_text, sign_off, email="", track_opens=True):
    """Staging-style shell with custom body and sign-off."""
    body_html = _body_to_html(body_text)
    sign_off_safe = html.escape(sign_off or "Sincerely")
    logo_src = _footer_logo_src(email, track_opens)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Stagify</title>
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
{body_html}
            </td>
          </tr>
          <tr>
            <td style="background-color:#f0f7ff; padding:26px 40px;
                        border-top:2px solid #dbeafe;">
              <p style="font-size:15px; color:#4b5563; margin:0 0 10px;">
                {sign_off_safe},</p>
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
</html>"""


def render_bulk_email_for_recipient(campaign, row, track_opens=True, tracking_email=None):
    params = row.get("params", {})
    image_urls = campaign.get("image_urls", [])
    body = substitute_text(campaign.get("body", ""), params, image_urls)
    subject = substitute_text(campaign.get("subject", ""), params, image_urls)
    recipient_email = row["email"]
    track_as = tracking_email if tracking_email is not None else recipient_email
    html_out = build_bulk_html_email(
        body,
        campaign.get("sign_off", "Sincerely"),
        email=track_as,
        track_opens=track_opens,
    )
    return subject.strip() or "Message from Stagify", html_out


def send_html_email(to_email, subject, html):
    cfg = load_config()
    recipients, _debug_to = resolve_recipients(to_email)

    payload = {
        "from": f"Stagify Team <{cfg['from_email']}>",
        "to": recipients,
        "subject": debug_subject(subject, to_email),
        "html": html,
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
