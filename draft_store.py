import json
from datetime import datetime, timezone
from pathlib import Path

from config import DRAFTS_DIR
from email_engine import draft_id_for_row

VALID_STATUSES = {"pending", "rejected", "sent", "failed", "skipped"}


def _now():
    return datetime.now(timezone.utc).isoformat()


def draft_dir(draft_id):
    return DRAFTS_DIR / draft_id


def draft_exists(draft_id):
    return (draft_dir(draft_id) / "meta.json").exists()


def list_drafts(status=None):
    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    drafts = []
    for path in sorted(DRAFTS_DIR.iterdir()):
        if not path.is_dir():
            continue
        meta_path = path / "meta.json"
        if not meta_path.exists():
            continue
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if status is None or meta.get("status") == status:
            drafts.append(meta)
    drafts.sort(key=lambda d: d.get("created_at", ""), reverse=True)
    return drafts


def get_draft(draft_id):
    meta_path = draft_dir(draft_id) / "meta.json"
    if not meta_path.exists():
        return None
    return json.loads(meta_path.read_text(encoding="utf-8"))


def save_draft_meta(draft_id, meta):
    folder = draft_dir(draft_id)
    folder.mkdir(parents=True, exist_ok=True)
    meta_path = folder / "meta.json"
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")


def create_draft(row, staged_data, html_preview, original_bytes=None, original_mime=None):
    draft_id = draft_id_for_row(row)
    folder = draft_dir(draft_id)

    if (folder / "meta.json").exists():
        return get_draft(draft_id)

    folder.mkdir(parents=True, exist_ok=True)
    (folder / "staged.txt").write_text(staged_data, encoding="utf-8")
    (folder / "email.html").write_text(html_preview, encoding="utf-8")
    if original_bytes and original_mime:
        ext = _mime_to_ext(original_mime)
        (folder / f"original.{ext}").write_bytes(original_bytes)

    meta = {
        "id": draft_id,
        "status": "pending",
        "name": row["name"],
        "email": row["email"],
        "email_normalized": row["email"].strip().lower(),
        "address": row["address"],
        "subject": row["address"],
        "image_url": row["image_url"],
        "room_type": row["room_type"],
        "source_csv_id": row.get("source_csv_id"),
        "original_mime": original_mime,
        "created_at": _now(),
        "updated_at": _now(),
        "sent_at": None,
        "resend_id": None,
        "error": None,
    }
    save_draft_meta(draft_id, meta)
    return meta


def mark_row_failed(row, error):
    draft_id = draft_id_for_row(row)
    if draft_exists(draft_id):
        return update_draft(draft_id, status="failed", error=error)

    folder = draft_dir(draft_id)
    folder.mkdir(parents=True, exist_ok=True)
    meta = {
        "id": draft_id,
        "status": "failed",
        "name": row["name"],
        "email": row["email"],
        "email_normalized": row["email"].strip().lower(),
        "address": row["address"],
        "subject": row["address"],
        "image_url": row["image_url"],
        "room_type": row.get("room_type", "Living Room"),
        "source_csv_id": row.get("source_csv_id"),
        "original_mime": None,
        "created_at": _now(),
        "updated_at": _now(),
        "sent_at": None,
        "resend_id": None,
        "error": error,
    }
    save_draft_meta(draft_id, meta)
    return meta


def update_draft(draft_id, **fields):
    meta = get_draft(draft_id)
    if not meta:
        return None
    meta.update(fields)
    meta["updated_at"] = _now()
    save_draft_meta(draft_id, meta)
    return meta


def get_staged_data(draft_id):
    path = draft_dir(draft_id) / "staged.txt"
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def get_email_html(draft_id):
    path = draft_dir(draft_id) / "email.html"
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def _mime_to_ext(mime):
    ext = mime.split("/")[-1].replace("jpeg", "jpg")
    return ext if ext in ("jpg", "png", "webp") else "jpg"


def save_original_image(draft_id, content, mime):
    folder = draft_dir(draft_id)
    folder.mkdir(parents=True, exist_ok=True)
    ext = _mime_to_ext(mime)
    path = folder / f"original.{ext}"
    path.write_bytes(content)
    meta = get_draft(draft_id)
    if meta:
        meta["original_mime"] = mime
        save_draft_meta(draft_id, meta)
    return path


def get_original_image(draft_id):
    folder = draft_dir(draft_id)
    for ext in ("webp", "jpg", "png"):
        path = folder / f"original.{ext}"
        if path.exists():
            mime = {
                "webp": "image/webp",
                "jpg": "image/jpeg",
                "png": "image/png",
            }[ext]
            return path.read_bytes(), mime
    return None, None


def has_original_image(draft_id):
    return get_original_image(draft_id)[0] is not None


def count_by_status():
    counts = {s: 0 for s in VALID_STATUSES}
    for draft in list_drafts():
        status = draft.get("status", "pending")
        counts[status] = counts.get(status, 0) + 1
    return counts


def reject_all_pending():
    count = 0
    for draft in list_drafts(status="pending"):
        update_draft(draft["id"], status="rejected", error=None)
        count += 1
    return count
