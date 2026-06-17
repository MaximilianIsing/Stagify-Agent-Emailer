import json

from config import DATA_DIR

SETTINGS_FILE = DATA_DIR / "debug_settings.json"
DEFAULT_SETTINGS = {"enabled": False, "email": ""}


def _atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)


def load_debug_settings():
    if not SETTINGS_FILE.exists():
        return dict(DEFAULT_SETTINGS)
    data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    return {
        "enabled": bool(data.get("enabled", False)),
        "email": (data.get("email") or "").strip(),
    }


def save_debug_settings(enabled, email):
    email = email.strip()
    if enabled and (not email or "@" not in email):
        raise ValueError("A valid debug email is required when debug mode is on.")
    settings = {"enabled": bool(enabled), "email": email}
    _atomic_write(SETTINGS_FILE, settings)
    return settings


def is_debug_active():
    settings = load_debug_settings()
    return settings["enabled"] and bool(settings["email"])


def resolve_recipients(to_email):
    settings = load_debug_settings()
    if settings["enabled"] and settings["email"]:
        return [settings["email"]], settings["email"]
    return [to_email], None


def debug_subject(subject, to_email):
    if is_debug_active():
        return f"[DEBUG] {subject} (for {to_email})"
    return subject


def tracking_email_for_send(to_email):
    """Email address used in open-tracking pixels for this send."""
    settings = load_debug_settings()
    if settings["enabled"] and settings["email"]:
        return settings["email"]
    return to_email
