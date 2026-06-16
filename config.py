from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
DATA_DIR = SCRIPT_DIR / "data"
DRAFTS_DIR = DATA_DIR / "drafts"
SENT_REGISTRY_FILE = DATA_DIR / "sent_registry.json"
WORKER_STATE_FILE = DATA_DIR / "worker_state.json"
CACHE_DIR = SCRIPT_DIR / "staged_cache"

STAGIFY_HOST = "https://stagify.ai"
STAGE_ENDPOINT = f"{STAGIFY_HOST}/api/stage-by-endpoint-key"
RESEND_URL = "https://api.resend.com/emails"

STAGE_DELAY = 3
WORKER_POLL_SECONDS = 10


def read_txt(filename):
    path = SCRIPT_DIR / filename
    if path.exists():
        text = path.read_text(encoding="utf-8-sig").strip()
        return text
    return ""


def normalize_password(value):
    if not value:
        return ""
    cleaned = value.strip().replace("\r", "").replace("\n", "").replace("\t", "")
    return "".join(ch for ch in cleaned if ch.isprintable())


def load_config():
    stagify_key = read_txt("key.txt")
    return {
        "stagify_key": stagify_key,
        "resend_key": read_txt("resendkey.txt"),
        "from_email": read_txt("email.txt"),
        "admin_password": stagify_key,
        "session_secret": read_txt("session_secret.txt") or _ensure_session_secret(),
    }


def _ensure_session_secret():
    for name in ("session_secret.txt", ".session_secret"):
        path = SCRIPT_DIR / name
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
    import secrets
    value = secrets.token_hex(32)
    (SCRIPT_DIR / "session_secret.txt").write_text(value, encoding="utf-8")
    return value
