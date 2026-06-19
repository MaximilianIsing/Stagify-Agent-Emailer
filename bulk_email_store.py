import csv
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from config import DATA_DIR

BULK_DIR = DATA_DIR / "bulk_email"
CSV_DIR = BULK_DIR / "csvs"
CAMPAIGN_FILE = BULK_DIR / "campaign.json"

EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def _now():
    return datetime.now(timezone.utc).isoformat()


def _default_campaign():
    return {
        "id": None,
        "csv_name": None,
        "uploaded_at": None,
        "columns": [],
        "rows": [],
        "skipped": [],
        "subject": "",
        "body": "",
        "sign_off": "Sincerely",
        "image_urls": [],
        "updated_at": None,
    }


def _load_campaign():
    if not CAMPAIGN_FILE.exists():
        return _default_campaign()
    try:
        text = CAMPAIGN_FILE.read_text(encoding="utf-8").strip()
        if not text:
            return _default_campaign()
        data = json.loads(text)
        if not isinstance(data, dict):
            return _default_campaign()
        base = _default_campaign()
        base.update(data)
        return base
    except (json.JSONDecodeError, OSError):
        return _default_campaign()


def _save_campaign(campaign):
    BULK_DIR.mkdir(parents=True, exist_ok=True)
    campaign["updated_at"] = _now()
    tmp = CAMPAIGN_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(campaign, indent=2), encoding="utf-8")
    tmp.replace(CAMPAIGN_FILE)


def get_campaign():
    return _load_campaign()


def save_template(subject, body, sign_off, image_urls_text):
    campaign = _load_campaign()
    campaign["subject"] = (subject or "").strip()
    campaign["body"] = body or ""
    campaign["sign_off"] = (sign_off or "Sincerely").strip() or "Sincerely"
    urls = []
    for line in (image_urls_text or "").splitlines():
        url = line.strip()
        if url:
            urls.append(url)
    campaign["image_urls"] = urls
    _save_campaign(campaign)
    return campaign


def _normalize_header(name):
    return (name or "").strip()


def parse_recipient_csv(file_storage):
    if not file_storage or not file_storage.filename:
        raise ValueError("No file selected.")
    if not file_storage.filename.lower().endswith(".csv"):
        raise ValueError("Only .csv files are allowed.")

    CSV_DIR.mkdir(parents=True, exist_ok=True)
    campaign_id = uuid.uuid4().hex[:12]
    stored_path = CSV_DIR / f"{campaign_id}_{Path(file_storage.filename).name}"
    file_storage.save(stored_path)

    rows = []
    skipped = []
    columns = []

    try:
        with open(stored_path, "r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames:
                raise ValueError("CSV is empty or has no header row.")

            raw_headers = [_normalize_header(h) for h in reader.fieldnames]
            if not raw_headers[0]:
                raise ValueError("First column header is missing.")

            email_key = reader.fieldnames[0]
            param_keys = reader.fieldnames[1:]
            columns = [raw_headers[0]] + [_normalize_header(k) for k in param_keys]

            for line_num, row in enumerate(reader, start=2):
                email = (row.get(email_key) or "").strip()
                if not email:
                    if not any((v or "").strip() for v in row.values()):
                        continue
                    skipped.append({"line": line_num, "reason": "Missing email", "preview": ""})
                    continue
                if not EMAIL_RE.match(email):
                    skipped.append({
                        "line": line_num,
                        "reason": f"Invalid email: {email}",
                        "preview": email,
                    })
                    continue

                params = {}
                for key, col_name in zip(param_keys, columns[1:]):
                    params[col_name] = (row.get(key) or "").strip()

                rows.append({"email": email, "params": params})

    except csv.Error as exc:
        stored_path.unlink(missing_ok=True)
        raise ValueError(f"Invalid CSV format: {exc}") from exc
    except OSError as exc:
        stored_path.unlink(missing_ok=True)
        raise ValueError(f"Could not read CSV: {exc}") from exc

    if not rows:
        stored_path.unlink(missing_ok=True)
        raise ValueError("No valid recipient rows found in CSV.")

    campaign = _load_campaign()
    campaign.update({
        "id": campaign_id,
        "csv_name": file_storage.filename,
        "uploaded_at": _now(),
        "columns": columns,
        "rows": rows,
        "skipped": skipped,
        "stored_path": str(stored_path),
    })
    _save_campaign(campaign)
    return campaign
