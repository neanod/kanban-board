import os
import json
from pathlib import Path

# Paths
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = BASE_DIR / "uploads"
STATIC_DIR = BASE_DIR / "static"

DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# Minimal .env loader without external dependencies
def load_dotenv(env_path: Path):
    if env_path.is_file():
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip("\"'")
                if k and k not in os.environ:
                    os.environ[k] = v

load_dotenv(BASE_DIR / ".env")

# Database
DB_PATH = os.environ.get("DB_PATH", str(DATA_DIR / "kanban.db"))

# Server
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8092"))
BASE_URL = os.environ.get("BASE_URL", "http://localhost:8092").rstrip("/")

# Bot & Security
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
WEB_PASSWORD = os.environ.get("WEB_PASSWORD", "change_me_in_env")
ADMIN_TG_ID = int(os.environ.get("ADMIN_TG_ID", "0"))

# Allowed users mapping: Telegram ID (int) -> username (str)
_raw_user_map = os.environ.get("TG_USER_MAP")
if _raw_user_map:
    try:
        parsed = json.loads(_raw_user_map)
        TG_USER_MAP = {int(k): str(v) for k, v in parsed.items()}
    except Exception:
        TG_USER_MAP = {123456789: "dmitry", 987654321: "nikita", 112233445: "andrii"}
else:
    TG_USER_MAP = {
        123456789: "dmitry",
        987654321: "nikita",
        112233445: "andrii",
    }

USERNAME_TO_TG = {v: k for k, v in TG_USER_MAP.items()}
ALLOWED_USERS = list(USERNAME_TO_TG.keys())

# Status definitions
STATUS_OPEN = "open"
STATUS_IN_PROGRESS = "in_progress"
STATUS_REVIEW = "review"
STATUS_COMPLETED = "completed"

VALID_STATUSES = [STATUS_OPEN, STATUS_IN_PROGRESS, STATUS_REVIEW, STATUS_COMPLETED]

# Security & limits
MAX_UPLOAD_SIZE = 25 * 1024 * 1024  # 25 MB
SESSION_COOKIE_NAME = "kanban_session"
PROFILE_COOKIE_NAME = "kanban_user"
NICKNAME_COOKIE_NAME = "kanban_nickname"

# Rate Limiting
LOGIN_RATE_LIMIT = 5       # Max 5 attempts per minute per IP
GLOBAL_RATE_LIMIT = 120    # Max 120 requests per minute per IP
