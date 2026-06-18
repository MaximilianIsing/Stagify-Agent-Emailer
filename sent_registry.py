import json
from datetime import datetime, timezone

from filelock import FileLock

from config import DATA_DIR, SENT_REGISTRY_FILE
from email_engine import dedup_key, normalize_email

LOCK_FILE = DATA_DIR / "sent_registry.lock"


class DuplicateSendError(Exception):
    pass


def _now():
    return datetime.now(timezone.utc).isoformat()


def _empty_registry():
    return {"version": 1, "by_email": {}, "by_dedup_key": {}}


def _atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)


def _load_unlocked():
    if not SENT_REGISTRY_FILE.exists():
        return _empty_registry()
    try:
        text = SENT_REGISTRY_FILE.read_text(encoding="utf-8").strip()
        if not text:
            return _empty_registry()
        data = json.loads(text)
        if not isinstance(data, dict):
            return _empty_registry()
        data.setdefault("version", 1)
        data.setdefault("by_email", {})
        data.setdefault("by_dedup_key", {})
        return data
    except (json.JSONDecodeError, OSError):
        return _empty_registry()


def load_registry():
    with FileLock(LOCK_FILE):
        return _load_unlocked()


def is_sent(email):
    key = normalize_email(email)
    with FileLock(LOCK_FILE):
        registry = _load_unlocked()
        return key in registry["by_email"]


def is_dedup_key_sent(email, address):
    key = dedup_key(email, address)
    with FileLock(LOCK_FILE):
        registry = _load_unlocked()
        return key in registry["by_dedup_key"]


def reserve_send(email, address, draft_id, name):
    """Atomically reserve a recipient so no parallel send can slip through."""
    email_key = normalize_email(email)
    dkey = dedup_key(email, address)

    with FileLock(LOCK_FILE):
        registry = _load_unlocked()

        if email_key in registry["by_email"]:
            existing = registry["by_email"][email_key]
            raise DuplicateSendError(
                f"Email already sent to {email_key} on {existing.get('sent_at')}"
            )

        if dkey in registry["by_dedup_key"]:
            raise DuplicateSendError(
                f"Campaign entry already sent for {email_key} / {address}"
            )

        record = {
            "draft_id": draft_id,
            "name": name,
            "email": email_key,
            "address": address,
            "dedup_key": dkey,
            "sent_at": _now(),
            "resend_id": None,
            "status": "sending",
        }
        registry["by_email"][email_key] = record
        registry["by_dedup_key"][dkey] = email_key
        _atomic_write(SENT_REGISTRY_FILE, registry)
        return record


def finalize_send(email, address, resend_id):
    email_key = normalize_email(email)
    dkey = dedup_key(email, address)

    with FileLock(LOCK_FILE):
        registry = _load_unlocked()
        record = registry["by_email"].get(email_key)
        if not record or record.get("dedup_key") != dkey:
            raise DuplicateSendError(f"No reservation found for {email_key}")
        record["resend_id"] = resend_id
        record["status"] = "sent"
        record["sent_at"] = _now()
        _atomic_write(SENT_REGISTRY_FILE, registry)
        return record


def release_reservation(email, address):
    email_key = normalize_email(email)
    dkey = dedup_key(email, address)

    with FileLock(LOCK_FILE):
        registry = _load_unlocked()
        record = registry["by_email"].get(email_key)
        if record and record.get("status") == "sending" and record.get("dedup_key") == dkey:
            del registry["by_email"][email_key]
            if registry["by_dedup_key"].get(dkey) == email_key:
                del registry["by_dedup_key"][dkey]
            _atomic_write(SENT_REGISTRY_FILE, registry)


def mark_sent(email, address, draft_id, name, resend_id=None):
    email_key = normalize_email(email)
    dkey = dedup_key(email, address)

    with FileLock(LOCK_FILE):
        registry = _load_unlocked()

        if email_key in registry["by_email"]:
            existing = registry["by_email"][email_key]
            if existing.get("status") == "sent":
                raise DuplicateSendError(
                    f"Email already sent to {email_key} on {existing.get('sent_at')}"
                )

        if dkey in registry["by_dedup_key"]:
            raise DuplicateSendError(
                f"Campaign entry already sent for {email_key} / {address}"
            )

        record = {
            "draft_id": draft_id,
            "name": name,
            "email": email_key,
            "address": address,
            "dedup_key": dkey,
            "sent_at": _now(),
            "resend_id": resend_id,
            "status": "sent",
        }
        registry["by_email"][email_key] = record
        registry["by_dedup_key"][dkey] = email_key
        _atomic_write(SENT_REGISTRY_FILE, registry)
        return record


def list_sent():
    registry = load_registry()
    records = list(registry["by_email"].values())
    records.sort(key=lambda r: r.get("sent_at", ""), reverse=True)
    return records


def sent_count():
    return len(load_registry()["by_email"])
