import csv
import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from config import DATA_DIR
from csv_parser import parse_csv_file
from row_range import parse_row_range_params

CSV_DIR = DATA_DIR / "csvs"
REGISTRY_FILE = DATA_DIR / "csv_registry.json"
HASH_REGISTRY_FILE = DATA_DIR / "csv_upload_hashes.json"
REPORTS_DIR = DATA_DIR / "csv_reports"

REQUIRED_COLUMNS = [
    "Name",
    "Email Address",
    "Listing Address",
    "Image URL",
]


def _now():
    return datetime.now(timezone.utc).isoformat()


def _load_registry():
    if not REGISTRY_FILE.exists():
        return []
    return json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))


def _save_registry(entries):
    REGISTRY_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = REGISTRY_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(entries, indent=2), encoding="utf-8")
    tmp.replace(REGISTRY_FILE)


def _load_hash_registry():
    if not HASH_REGISTRY_FILE.exists():
        return {}
    return json.loads(HASH_REGISTRY_FILE.read_text(encoding="utf-8"))


def _save_hash_registry(registry):
    HASH_REGISTRY_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = HASH_REGISTRY_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(registry, indent=2), encoding="utf-8")
    tmp.replace(HASH_REGISTRY_FILE)


def _file_content_hash(path):
    content = path.read_bytes()
    text = content.decode("utf-8-sig")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _check_duplicate_upload(content_hash):
    existing = _load_hash_registry().get(content_hash)
    if not existing:
        return
    uploaded_at = existing.get("uploaded_at", "unknown time")
    if len(uploaded_at) >= 19:
        uploaded_at = uploaded_at[:19].replace("T", " ")
    raise ValueError(
        f"This CSV was already uploaded as \"{existing.get('original_name', 'unknown')}\" "
        f"on {uploaded_at}. Duplicate uploads are not allowed."
    )


def _remove_upload_hash_for_csv(csv_id, content_hash=None):
    registry = _load_hash_registry()
    changed = False
    if content_hash and content_hash in registry:
        del registry[content_hash]
        changed = True
    for key, meta in list(registry.items()):
        if meta.get("csv_id") == csv_id and key in registry:
            del registry[key]
            changed = True
    if changed:
        _save_hash_registry(registry)


def _record_upload_hash(content_hash, csv_id, original_name):
    registry = _load_hash_registry()
    registry[content_hash] = {
        "csv_id": csv_id,
        "original_name": original_name,
        "uploaded_at": _now(),
    }
    _save_hash_registry(registry)


def _sanitize_filename(name):
    base = Path(name).name
    base = re.sub(r"[^\w.\- ]+", "", base).strip().replace(" ", "_")
    return base or "upload.csv"


def _save_parse_report(csv_id, result):
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORTS_DIR / f"{csv_id}.json"
    path.write_text(json.dumps(result, indent=2), encoding="utf-8")


def _delete_parse_report(csv_id):
    path = REPORTS_DIR / f"{csv_id}.json"
    path.unlink(missing_ok=True)


def get_parse_report(csv_id):
    path = REPORTS_DIR / f"{csv_id}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _validate_csv_structure(path):
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames:
                raise ValueError("CSV is empty or has no header row.")
            missing = [col for col in REQUIRED_COLUMNS if col not in reader.fieldnames]
            if missing:
                raise ValueError(f"CSV missing required columns: {', '.join(missing)}")
    except csv.Error as exc:
        raise ValueError(f"Invalid CSV format: {exc}") from exc
    except OSError as exc:
        raise ValueError(f"Could not read CSV: {exc}") from exc


def _format_upload_error(result):
    skipped = result.get("skipped", [])
    if not skipped:
        return "No valid rows found in CSV."
    sample = skipped[:5]
    details = "; ".join(f"line {item['line']}: {item['reason']}" for item in sample)
    extra = f" (+{len(skipped) - 5} more)" if len(skipped) > 5 else ""
    return f"No valid rows found. {details}{extra}"


def list_csvs():
    return sorted(_load_registry(), key=lambda e: e.get("uploaded_at", ""), reverse=True)


def get_active_csv_paths():
    paths = []
    for entry in _load_registry():
        if not entry.get("active", False):
            continue
        path = Path(entry["stored_path"])
        if path.exists():
            paths.append(path)
    return paths


def get_csv(csv_id):
    for entry in _load_registry():
        if entry["id"] == csv_id:
            return entry
    return None


def save_upload(
    file_storage,
    row_start="",
    row_end="",
    row_start_bound="inclusive",
    row_end_bound="inclusive",
):
    if not file_storage or not file_storage.filename:
        raise ValueError("No file selected.")

    original_name = file_storage.filename
    if not original_name.lower().endswith(".csv"):
        raise ValueError("Only .csv files are allowed.")

    CSV_DIR.mkdir(parents=True, exist_ok=True)
    csv_id = uuid.uuid4().hex[:12]
    stored_name = f"{csv_id}_{_sanitize_filename(original_name)}"
    stored_path = CSV_DIR / stored_name
    file_storage.save(stored_path)

    try:
        content_hash = _file_content_hash(stored_path)
        _check_duplicate_upload(content_hash)
        _validate_csv_structure(stored_path)
        result = parse_csv_file(stored_path, source_csv_id=csv_id)
        if not result["rows"]:
            raise ValueError(_format_upload_error(result))
        total_valid = len(result["rows"])
        total_rows = result.get("total_row_count", total_valid + len(result["skipped"]))
        row_range = parse_row_range_params(
            row_start,
            row_end,
            row_start_bound,
            row_end_bound,
            total_valid,
        )
        _save_parse_report(csv_id, result)
    except Exception:
        stored_path.unlink(missing_ok=True)
        raise

    entry = {
        "id": csv_id,
        "original_name": original_name,
        "stored_path": str(stored_path),
        "content_hash": content_hash,
        "uploaded_at": _now(),
        "row_count": total_valid,
        "total_row_count": total_rows,
        "valid_row_count": total_valid,
        "selected_row_count": row_range["selected_row_count"],
        "row_range": row_range,
        "skipped_row_count": len(result["skipped"]),
        "active": True,
    }
    registry = _load_registry()
    registry.append(entry)
    _save_registry(registry)
    _record_upload_hash(content_hash, csv_id, original_name)
    return entry


def set_csv_active(csv_id, active):
    registry = _load_registry()
    found = False
    for entry in registry:
        if entry["id"] == csv_id:
            entry["active"] = bool(active)
            found = True
    if not found:
        raise ValueError("CSV not found.")
    _save_registry(registry)


def delete_csv(csv_id):
    registry = _load_registry()
    kept = []
    deleted_entry = None
    for entry in registry:
        if entry["id"] == csv_id:
            deleted_entry = entry
            Path(entry["stored_path"]).unlink(missing_ok=True)
            _delete_parse_report(csv_id)
        else:
            kept.append(entry)
    if not deleted_entry:
        raise ValueError("CSV not found.")
    _save_registry(kept)
    _remove_upload_hash_for_csv(csv_id, deleted_entry.get("content_hash"))
