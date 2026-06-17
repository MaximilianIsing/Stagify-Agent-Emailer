import csv
import re
from pathlib import Path

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

REQUIRED_COLUMNS = ("Name", "Email Address", "Listing Address", "Image URL")
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


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


def _cell(row, key):
    val = row.get(key)
    if val is None:
        return ""
    return str(val).strip()


def _row_preview(name, email, address):
    parts = [p for p in (name, email, address) if p]
    preview = " · ".join(parts)
    return preview[:120] + ("…" if len(preview) > 120 else "")


def validate_csv_row(line_num, row):
    """Return (parsed_row, None), (None, error), or (None, None) for blank rows."""
    name = _cell(row, "Name")
    email = _cell(row, "Email Address")
    address = _cell(row, "Listing Address")
    image_url = _cell(row, "Image URL")
    staged_flag = _cell(row, "Staged?")
    image_type = _cell(row, "Image Type")

    if not any((name, email, address, image_url, staged_flag, image_type)):
        return None, None

    if not name or name.startswith("-"):
        return None, "Missing or invalid name"
    if not email:
        return None, "Missing email address"
    if not EMAIL_RE.match(email):
        return None, f"Invalid email format: {email}"
    if not address or address.startswith("-"):
        return None, "Missing or invalid listing address"
    if not image_url:
        return None, "Missing image URL"
    if not image_url.startswith(("http://", "https://")):
        return None, "Image URL must start with http:// or https://"
    if "/homedetails/" in image_url:
        return None, "Image URL looks like a listing page, not a direct image link"

    room_type, additional_prompt = normalize_room_type(image_type)

    return {
        "name": name,
        "email": email,
        "address": address,
        "image_url": image_url,
        "remove_furniture": staged_flag.lower() == "yes",
        "room_type": room_type,
        "additional_prompt": additional_prompt,
    }, None


def parse_csv_file(csv_path, source_csv_id=None):
    rows = []
    skipped = []
    total_data_rows = 0
    path = Path(csv_path)

    try:
        handle = open(path, "r", encoding="utf-8-sig", newline="")
    except OSError as exc:
        return {
            "rows": [],
            "skipped": [{"line": 0, "reason": f"Could not read file: {exc}", "preview": ""}],
            "source_csv_id": source_csv_id,
            "total_row_count": 0,
        }

    with handle as f:
        try:
            reader = csv.DictReader(f)
        except csv.Error as exc:
            return {
                "rows": [],
                "skipped": [{"line": 0, "reason": f"Invalid CSV format: {exc}", "preview": ""}],
                "source_csv_id": source_csv_id,
                "total_row_count": 0,
            }

        if not reader.fieldnames:
            return {
                "rows": [],
                "skipped": [{"line": 0, "reason": "CSV is empty or has no header row", "preview": ""}],
                "source_csv_id": source_csv_id,
                "total_row_count": 0,
            }

        missing = [col for col in REQUIRED_COLUMNS if col not in reader.fieldnames]
        if missing:
            return {
                "rows": [],
                "skipped": [{
                    "line": 1,
                    "reason": f"Missing required columns: {', '.join(missing)}",
                    "preview": "",
                }],
                "source_csv_id": source_csv_id,
                "total_row_count": 0,
            }

        for line_num, row in enumerate(reader, start=2):
            total_data_rows += 1
            try:
                parsed, err = validate_csv_row(line_num, row)
            except Exception as exc:
                skipped.append({
                    "line": line_num,
                    "reason": f"Malformed row: {exc}",
                    "preview": _row_preview(_cell(row, "Name"), _cell(row, "Email Address"), ""),
                })
                continue

            if err:
                skipped.append({
                    "line": line_num,
                    "reason": err,
                    "preview": _row_preview(
                        _cell(row, "Name"),
                        _cell(row, "Email Address"),
                        _cell(row, "Listing Address"),
                    ),
                })
                continue

            if parsed:
                parsed["source_csv_id"] = source_csv_id
                parsed["row_index"] = len(rows) + 1
                rows.append(parsed)

    return {
        "rows": rows,
        "skipped": skipped,
        "source_csv_id": source_csv_id,
        "total_row_count": total_data_rows,
    }


def parse_csv(csv_path=None, include_skipped=False):
    if csv_path:
        result = parse_csv_file(csv_path)
        return result if include_skipped else result["rows"]

    from csv_store import get_active_csv_paths, list_csvs
    from row_range import filter_rows_by_range

    rows = []
    skipped = []
    entries_by_path = {
        entry["stored_path"]: entry
        for entry in list_csvs()
        if entry.get("active")
    }
    for path in get_active_csv_paths():
        entry = entries_by_path.get(str(path))
        source_id = entry["id"] if entry else None

        result = parse_csv_file(path, source_csv_id=source_id)
        file_rows = result["rows"]
        if entry and entry.get("row_range"):
            file_rows = filter_rows_by_range(file_rows, entry["row_range"])
        rows.extend(file_rows)
        skipped.extend(result["skipped"])

    if include_skipped:
        return {"rows": rows, "skipped": skipped}
    return rows
