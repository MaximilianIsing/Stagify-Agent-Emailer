import json
import logging
import threading
import time
from datetime import datetime, timezone

from config import STAGE_DELAY, WORKER_POLL_SECONDS, WORKER_STATE_FILE
from draft_store import create_draft, draft_exists, draft_id_for_row, get_draft, mark_row_failed
from csv_parser import parse_csv
from email_engine import (
    build_html_email,
    download_listing_image,
    load_cached,
    save_cached,
    stage_image,
)
from sent_registry import is_dedup_key_sent, is_sent

logger = logging.getLogger(__name__)

_worker_thread = None
_worker_lock = threading.Lock()


def _now():
    return datetime.now(timezone.utc).isoformat()


def _load_worker_state():
    if not WORKER_STATE_FILE.exists():
        return {"last_run": None, "last_error": None, "drafts_created": 0}
    return json.loads(WORKER_STATE_FILE.read_text(encoding="utf-8"))


def _save_worker_state(state):
    WORKER_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    WORKER_STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def get_worker_state():
    state = _load_worker_state()
    state["running"] = _worker_thread is not None and _worker_thread.is_alive()
    return state


def _should_skip_row(row):
    if is_sent(row["email"]):
        return "already_sent"
    if is_dedup_key_sent(row["email"], row["address"]):
        return "already_sent"
    draft_id = draft_id_for_row(row)
    if draft_exists(draft_id):
        existing = get_draft(draft_id)
        if existing and existing.get("status") in (
            "pending", "rejected", "sent", "failed", "discarded"
        ):
            return "draft_exists"
    return None


def process_next_draft():
    parsed = parse_csv(include_skipped=True)
    rows = parsed["rows"]
    state = _load_worker_state()

    if not rows:
        if parsed["skipped"]:
            state["last_error"] = (
                f"No valid rows to process ({len(parsed['skipped'])} CSV row errors)"
            )
        else:
            state["last_error"] = "No active CSV uploads"
        state["last_run"] = _now()
        _save_worker_state(state)
        return False

    state["last_error"] = None
    for row in rows:
        skip_reason = _should_skip_row(row)
        if skip_reason:
            continue

        draft_id = draft_id_for_row(row)
        logger.info("Staging draft for %s <%s>", row["name"], row["email"])

        original_bytes = original_mime = original_ext = None
        staged_data = load_cached(row["image_url"])
        if not staged_data:
            downloaded = download_listing_image(row["image_url"])
            if not downloaded:
                error = f"Download failed for image URL: {row['image_url']}"
                mark_row_failed(row, error)
                state["last_error"] = error
                state["last_run"] = _now()
                _save_worker_state(state)
                logger.warning("Skipping %s after download failure", row["email"])
                continue
            original_bytes, original_mime, original_ext = downloaded
            staged_data = stage_image(
                row["image_url"],
                row["room_type"],
                row["remove_furniture"],
                row.get("additional_prompt", ""),
                image_content=original_bytes,
                image_mime=original_mime,
                image_ext=original_ext,
            )
            if staged_data:
                save_cached(row["image_url"], staged_data)
            else:
                error = f"Staging failed for {row['email']}"
                mark_row_failed(row, error)
                state["last_error"] = error
                state["last_run"] = _now()
                _save_worker_state(state)
                logger.warning("Skipping %s after staging failure", row["email"])
                continue
        elif not draft_exists(draft_id):
            downloaded = download_listing_image(row["image_url"])
            if downloaded:
                original_bytes, original_mime, _ = downloaded

        preview_html = build_html_email(
            row["name"],
            row["address"],
            image_src=f"/drafts/{draft_id}/image",
            email=row["email"],
        )
        create_draft(
            row,
            staged_data,
            preview_html,
            original_bytes=original_bytes,
            original_mime=original_mime,
        )

        state["drafts_created"] = state.get("drafts_created", 0) + 1
        state["last_error"] = None
        state["last_run"] = _now()
        _save_worker_state(state)
        logger.info("Created draft %s", draft_id)
        return True

    state["last_run"] = _now()
    state["last_error"] = None
    _save_worker_state(state)
    return False


def worker_loop():
    logger.info("Draft worker started")
    while True:
        try:
            created = process_next_draft()
            delay = STAGE_DELAY if created else WORKER_POLL_SECONDS
        except Exception as exc:
            logger.exception("Worker error: %s", exc)
            state = _load_worker_state()
            state["last_error"] = str(exc)
            state["last_run"] = _now()
            _save_worker_state(state)
            delay = WORKER_POLL_SECONDS
        time.sleep(delay)


def start_worker():
    global _worker_thread
    with _worker_lock:
        if _worker_thread and _worker_thread.is_alive():
            return
        _worker_thread = threading.Thread(target=worker_loop, daemon=True)
        _worker_thread.start()
