import json

from config import DATA_DIR

SETTINGS_FILE = DATA_DIR / "storage_settings.json"
DEFAULT_SETTINGS = {"auto_delete_sent_drafts": False}


def _atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)


def load_storage_settings():
    if not SETTINGS_FILE.exists():
        return dict(DEFAULT_SETTINGS)
    try:
        text = SETTINGS_FILE.read_text(encoding="utf-8").strip()
        if not text:
            return dict(DEFAULT_SETTINGS)
        data = json.loads(text)
    except (json.JSONDecodeError, OSError):
        return dict(DEFAULT_SETTINGS)
    return {
        "auto_delete_sent_drafts": bool(data.get("auto_delete_sent_drafts", False)),
    }


def save_storage_settings(auto_delete_sent_drafts):
    settings = {
        "auto_delete_sent_drafts": bool(auto_delete_sent_drafts),
    }
    _atomic_write(SETTINGS_FILE, settings)
    return settings
