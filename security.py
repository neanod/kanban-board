import time
import hmac
import hashlib
import re
import os
from typing import Optional, Tuple, Dict, Any
from fastapi import Request, HTTPException, status
from config import (
    WEB_PASSWORD, BOT_TOKEN, TG_USER_MAP, ALLOWED_USERS,
    LOGIN_RATE_LIMIT, GLOBAL_RATE_LIMIT, MAX_UPLOAD_SIZE,
    SESSION_COOKIE_NAME, PROFILE_COOKIE_NAME, NICKNAME_COOKIE_NAME
)
import database

# In-memory sliding window rate limiter
# ip -> list of timestamps
_login_attempts: Dict[str, list] = {}
_global_requests: Dict[str, list] = {}

def get_client_ip(request: Request) -> str:
    # Handle X-Forwarded-For if behind reverse proxy
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "127.0.0.1"

def check_rate_limit(ip: str, store: Dict[str, list], max_requests: int, window_sec: int = 60) -> bool:
    now = time.time()
    cutoff = now - window_sec
    if ip not in store:
        store[ip] = []
    # Prune old timestamps
    store[ip] = [t for t in store[ip] if t > cutoff]
    if len(store[ip]) >= max_requests:
        return False
    store[ip].append(now)
    return True

def verify_site_password(password: str) -> bool:
    """Constant-time password verification against timing attacks"""
    if not password:
        return False
    return hmac.compare_digest(password.encode("utf-8"), WEB_PASSWORD.encode("utf-8"))

def verify_telegram_auth(auth_data: Dict[str, Any]) -> Optional[str]:
    """
    Verify Telegram Login Widget data as per Telegram docs:
    hash = HMAC_SHA256(data_check_string, SHA256(bot_token))
    Returns the mapped username ('dmitry', 'andrii', 'nikita') or None.
    """
    received_hash = auth_data.get("hash")
    if not received_hash or not BOT_TOKEN:
        return None
    
    # Check auth_date within 24 hours
    auth_date = auth_data.get("auth_date")
    if not auth_date:
        return None
    try:
        auth_ts = int(auth_date)
        if time.time() - auth_ts > 86400:
            return None
    except (ValueError, TypeError):
        return None

    # Check user ID against whitelist
    user_id_val = auth_data.get("id")
    try:
        tg_id = int(user_id_val)
    except (ValueError, TypeError):
        return None

    if tg_id not in TG_USER_MAP:
        return None
    username = TG_USER_MAP[tg_id]

    # Calculate check hash
    check_pairs = []
    for k in sorted(auth_data.keys()):
        if k != "hash":
            check_pairs.append(f"{k}={auth_data[k]}")
    data_check_string = "\n".join(check_pairs)

    secret_key = hashlib.sha256(BOT_TOKEN.encode("utf-8")).digest()
    computed_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()

    if hmac.compare_digest(received_hash, computed_hash):
        return username
    return None

def sanitize_filename(filename: str) -> str:
    """Sanitize filename to prevent directory traversal and invalid characters"""
    filename = os.path.basename(filename)
    filename = re.sub(r'[^a-zA-Z0-9_.-]', '_', filename)
    if not filename or filename.startswith('.'):
        filename = f"file_{int(time.time())}"
    return filename

def authenticate_request(request: Request) -> Dict[str, Any]:
    """
    Authenticate request via API Key or Web Session.
    Returns dict with user info or raises 401.
    """
    # 1. Check API Key in headers
    api_key = request.headers.get("X-API-Key")
    if not api_key:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            api_key = auth_header[7:].strip()

    if api_key:
        key_info = database.verify_api_key(api_key)
        if not key_info:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or revoked API Key"
            )
        return {
            "username": key_info["username"],
            "nickname": key_info["name"],
            "auth_type": "api_key",
            "key_id": key_info["id"]
        }

    # 2. Check Web Session Cookie
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    if session_id:
        session = database.get_session(session_id)
        if session:
            # Check if there is an active profile cookie override or session default
            user = request.cookies.get(PROFILE_COOKIE_NAME) or session["username"]
            if user not in ALLOWED_USERS:
                user = session["username"]
            nickname = request.cookies.get(NICKNAME_COOKIE_NAME) or session.get("nickname") or user.capitalize()
            return {
                "username": user,
                "nickname": nickname,
                "auth_type": "session",
                "session_id": session_id
            }

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required (API key or session cookie)"
    )

def authenticate_optional(request: Request) -> Optional[Dict[str, Any]]:
    try:
        return authenticate_request(request)
    except HTTPException:
        return None
