import sqlite3
import hashlib
import secrets
import datetime
import json
from typing import Optional, List, Dict, Any, Tuple
import config
from config import (
    DB_PATH, TG_USER_MAP, ALLOWED_USERS,
    STATUS_OPEN, STATUS_IN_PROGRESS, STATUS_REVIEW, STATUS_COMPLETED,
    UPLOAD_DIR, ADMIN_TG_ID
)

def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()

    # Users
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        username TEXT PRIMARY KEY,
        telegram_id INTEGER UNIQUE,
        display_name TEXT,
        created_at TEXT NOT NULL
    );
    """)

    # Populate/Update default allowed users
    # First clear telegram_id to prevent unique constraint conflict on swap
    for tg_id, username in TG_USER_MAP.items():
        cursor.execute("UPDATE users SET telegram_id = NULL WHERE telegram_id = ?", (tg_id,))
    for tg_id, username in TG_USER_MAP.items():
        cursor.execute("""
        INSERT INTO users (username, telegram_id, display_name, created_at)
        VALUES (?, ?, ?, datetime('now'))
        ON CONFLICT(username) DO UPDATE SET telegram_id = excluded.telegram_id
        """, (username, tg_id, username.capitalize()))

    # Tasks
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        description TEXT DEFAULT '',
        status TEXT NOT NULL DEFAULT 'open',
        created_by TEXT NOT NULL,
        assignee TEXT,
        claimed_at TEXT,
        reviewed_by TEXT,
        completed_at TEXT,
        rejection_comment TEXT,
        ai_enabled INTEGER NOT NULL DEFAULT 0,
        position INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """)

    # Migration: check if ai_enabled and position columns exist
    cursor.execute("PRAGMA table_info(tasks)")
    cols = [col["name"] for col in cursor.fetchall()]
    if "ai_enabled" not in cols:
        cursor.execute("ALTER TABLE tasks ADD COLUMN ai_enabled INTEGER NOT NULL DEFAULT 0;")
    if "position" not in cols:
        cursor.execute("ALTER TABLE tasks ADD COLUMN position INTEGER NOT NULL DEFAULT 0;")

    # Settings table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """)
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('ai_mode', '1');")
    admin_seed = json.dumps([str(config.ADMIN_TG_ID)]) if config.ADMIN_TG_ID else "[]"
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('admins', ?);", (admin_seed,))
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('alert_recipients', ?);", (admin_seed,))

    # AI Chat Queries table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS ai_chat_queries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user TEXT NOT NULL,
        prompt TEXT NOT NULL,
        task_ids TEXT DEFAULT '',
        status TEXT NOT NULL DEFAULT 'pending',
        response TEXT DEFAULT '',
        created_at TEXT NOT NULL,
        completed_at TEXT
    );
    """)

    # Task Comments / Timeline History
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS task_comments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id INTEGER NOT NULL,
        author TEXT NOT NULL,
        comment_type TEXT NOT NULL DEFAULT 'comment',
        content TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
    );
    """)

    # Attachments
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS task_attachments (
        id TEXT PRIMARY KEY,
        task_id INTEGER NOT NULL,
        uploader TEXT NOT NULL,
        original_filename TEXT NOT NULL,
        stored_filename TEXT NOT NULL,
        file_size INTEGER NOT NULL,
        mime_type TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
    );
    """)

    # API Keys
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS api_keys (
        id TEXT PRIMARY KEY,
        username TEXT NOT NULL,
        key_hash TEXT NOT NULL UNIQUE,
        key_prefix TEXT NOT NULL,
        name TEXT NOT NULL,
        created_at TEXT NOT NULL,
        last_used_at TEXT,
        is_active INTEGER NOT NULL DEFAULT 1
    );
    """)

    # Web Sessions
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS sessions (
        session_id TEXT PRIMARY KEY,
        username TEXT NOT NULL,
        nickname TEXT DEFAULT '',
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL
    );
    """)

    conn.commit()
    conn.close()

# ----------------- Security & API Keys -----------------

def hash_api_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()

def generate_api_key(username: str, name: str = "API Key") -> Tuple[str, Dict[str, Any]]:
    if username not in ALLOWED_USERS:
        raise ValueError(f"Unknown user: {username}")
    
    key_id = secrets.token_hex(8)
    random_token = secrets.token_urlsafe(32)
    raw_key = f"kb_{username}_{random_token}"
    key_hash = hash_api_key(raw_key)
    key_prefix = raw_key[:14] + "..." + raw_key[-4:]
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()

    conn = get_db_connection()
    conn.execute("""
    INSERT INTO api_keys (id, username, key_hash, key_prefix, name, created_at, is_active)
    VALUES (?, ?, ?, ?, ?, ?, 1)
    """, (key_id, username, key_hash, key_prefix, name, now))
    conn.commit()
    conn.close()

    key_info = {
        "id": key_id,
        "username": username,
        "key_prefix": key_prefix,
        "name": name,
        "created_at": now,
        "is_active": True
    }
    return raw_key, key_info

def verify_api_key(raw_key: str) -> Optional[Dict[str, Any]]:
    if not raw_key or not isinstance(raw_key, str):
        return None
    key_hash = hash_api_key(raw_key)
    conn = get_db_connection()
    row = conn.execute("""
    SELECT id, username, key_prefix, name, is_active FROM api_keys
    WHERE key_hash = ? AND is_active = 1
    """, (key_hash,)).fetchone()

    if row:
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        conn.execute("UPDATE api_keys SET last_used_at = ? WHERE id = ?", (now, row["id"]))
        conn.commit()
        res = dict(row)
        conn.close()
        return res
    conn.close()
    return None

def list_api_keys(username: str) -> List[Dict[str, Any]]:
    conn = get_db_connection()
    rows = conn.execute("""
    SELECT id, username, key_prefix, name, created_at, last_used_at, is_active
    FROM api_keys
    WHERE username = ?
    ORDER BY created_at DESC
    """, (username,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def revoke_api_key(key_id: str, username: str) -> bool:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
    UPDATE api_keys SET is_active = 0
    WHERE id = ? AND username = ?
    """, (key_id, username))
    conn.commit()
    count = cursor.rowcount
    conn.close()
    return count > 0

# ----------------- Sessions -----------------

def create_session(username: str, nickname: str = "", days: int = 30) -> str:
    if username not in ALLOWED_USERS:
        raise ValueError("Invalid user")
    session_id = secrets.token_urlsafe(32)
    now = datetime.datetime.now(datetime.timezone.utc)
    expires = now + datetime.timedelta(days=days)
    conn = get_db_connection()
    conn.execute("""
    INSERT INTO sessions (session_id, username, nickname, created_at, expires_at)
    VALUES (?, ?, ?, ?, ?)
    """, (session_id, username, nickname, now.isoformat(), expires.isoformat()))
    conn.commit()
    conn.close()
    return session_id

def get_session(session_id: str) -> Optional[Dict[str, Any]]:
    if not session_id:
        return None
    conn = get_db_connection()
    row = conn.execute("SELECT session_id, username, nickname, expires_at FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
    conn.close()
    if not row:
        return None
    
    expires_at = datetime.datetime.fromisoformat(row["expires_at"])
    now = datetime.datetime.now(datetime.timezone.utc)
    if now > expires_at:
        delete_session(session_id)
        return None
    return dict(row)

def delete_session(session_id: str):
    conn = get_db_connection()
    conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
    conn.commit()
    conn.close()

# ----------------- Tasks -----------------

def create_task(title: str, description: str, created_by: str, ai_enabled: int = 0) -> Dict[str, Any]:
    title = title.strip()
    description = description.strip()
    if not title:
        raise ValueError("Task title cannot be empty")
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO tasks (title, description, status, created_by, ai_enabled, created_at, updated_at)
    VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (title, description, STATUS_OPEN, created_by, 1 if ai_enabled else 0, now, now))
    task_id = cursor.lastrowid
    
    # Audit log entry
    cursor.execute("""
    INSERT INTO task_comments (task_id, author, comment_type, content, created_at)
    VALUES (?, ?, 'status_change', ?, ?)
    """, (task_id, created_by, f"Task created by @{created_by}", now))
    
    conn.commit()
    conn.close()
    return get_task(task_id)

def get_task(task_id: int) -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not row:
        conn.close()
        return None
    task = dict(row)
    
    # Fetch attachments
    att_rows = conn.execute("""
    SELECT id, original_filename, stored_filename, file_size, mime_type, uploader, created_at
    FROM task_attachments WHERE task_id = ? ORDER BY created_at ASC
    """, (task_id,)).fetchall()
    task["attachments"] = [dict(a) for a in att_rows]

    # Fetch comments
    comm_rows = conn.execute("""
    SELECT id, author, comment_type, content, created_at
    FROM task_comments WHERE task_id = ? ORDER BY created_at ASC
    """, (task_id,)).fetchall()
    task["comments"] = [dict(c) for c in comm_rows]

    conn.close()
    return task

def list_tasks(status: Optional[str] = None, assignee: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = get_db_connection()
    query = "SELECT * FROM tasks WHERE 1=1"
    params = []
    if status:
        query += " AND status = ?"
        params.append(status)
    if assignee:
        query += " AND assignee = ?"
        params.append(assignee)
    query += " ORDER BY position ASC, id DESC"
    
    rows = conn.execute(query, tuple(params)).fetchall()
    tasks = []
    for r in rows:
        t = dict(r)
        # Attachment count
        att_count = conn.execute("SELECT COUNT(*) as cnt FROM task_attachments WHERE task_id = ?", (t["id"],)).fetchone()["cnt"]
        t["attachment_count"] = att_count
        # Comments count
        comm_count = conn.execute("SELECT COUNT(*) as cnt FROM task_comments WHERE task_id = ?", (t["id"],)).fetchone()["cnt"]
        t["comment_count"] = comm_count
        tasks.append(t)
    conn.close()
    return tasks

def claim_task(task_id: int, username: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """Claim a task into In Progress"""
    task = get_task(task_id)
    if not task:
        return False, "Task not found", None
    if task["status"] != STATUS_OPEN:
        return False, f"Cannot claim task with status '{task['status']}' (must be open)", None
    
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
    UPDATE tasks
    SET status = ?, assignee = ?, claimed_at = ?, updated_at = ?
    WHERE id = ? AND status = ?
    """, (STATUS_IN_PROGRESS, username, now, now, task_id, STATUS_OPEN))
    
    if cursor.rowcount == 0:
        conn.close()
        return False, "Task was already taken by someone else", None

    cursor.execute("""
    INSERT INTO task_comments (task_id, author, comment_type, content, created_at)
    VALUES (?, ?, 'status_change', ?, ?)
    """, (task_id, username, f"Task claimed by @{username} (In Progress)", now))

    conn.commit()
    conn.close()
    return True, "Task claimed successfully", get_task(task_id)

def release_task(task_id: int, username: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """Release task back to Open"""
    task = get_task(task_id)
    if not task:
        return False, "Task not found", None
    if task["status"] != STATUS_IN_PROGRESS:
        return False, f"Task is not In Progress", None
    if task["assignee"] != username and task["created_by"] != username and username not in ["nikita", "ai agent"]:
        return False, "Only assignee or creator can release this task", None
    
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn = get_db_connection()
    conn.execute("""
    UPDATE tasks
    SET status = ?, assignee = NULL, claimed_at = NULL, updated_at = ?
    WHERE id = ?
    """, (STATUS_OPEN, now, task_id))

    conn.execute("""
    INSERT INTO task_comments (task_id, author, comment_type, content, created_at)
    VALUES (?, ?, 'status_change', ?, ?)
    """, (task_id, username, f"Task released back to Open by @{username}", now))

    conn.commit()
    conn.close()
    return True, "Task released back to Open", get_task(task_id)

def submit_task_for_review(task_id: int, username: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """Submit task for review"""
    task = get_task(task_id)
    if not task:
        return False, "Task not found", None
    if task["status"] != STATUS_IN_PROGRESS:
        return False, f"Cannot submit task with status '{task['status']}' for review", None
    if task["assignee"] != username and task["created_by"] != username:
        return False, "Only assignee or creator can submit this task for review", None

    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn = get_db_connection()
    conn.execute("""
    UPDATE tasks
    SET status = ?, updated_at = ?
    WHERE id = ?
    """, (STATUS_REVIEW, now, task_id))

    conn.execute("""
    INSERT INTO task_comments (task_id, author, comment_type, content, created_at)
    VALUES (?, ?, 'status_change', ?, ?)
    """, (task_id, username, f"Task submitted for review by @{username}", now))

    conn.commit()
    conn.close()
    return True, "Task submitted for review", get_task(task_id)

def approve_task(task_id: int, username: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """Approve task in review and mark as completed, recording reviewed_by"""
    task = get_task(task_id)
    if not task:
        return False, "Task not found", None
    if task["status"] != STATUS_REVIEW:
        return False, f"Task must be in 'review' status to approve (current: {task['status']})", None

    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn = get_db_connection()
    conn.execute("""
    UPDATE tasks
    SET status = ?, reviewed_by = ?, completed_at = ?, updated_at = ?
    WHERE id = ?
    """, (STATUS_COMPLETED, username, now, now, task_id))

    conn.execute("""
    INSERT INTO task_comments (task_id, author, comment_type, content, created_at)
    VALUES (?, ?, 'status_change', ?, ?)
    """, (task_id, username, f"Task approved and marked completed by reviewer @{username}", now))

    conn.commit()
    conn.close()
    return True, "Task approved and completed", get_task(task_id)

def reject_task(task_id: int, username: str, comment: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """Reject task in review and return to open with comment"""
    comment = comment.strip()
    if not comment:
        return False, "Rejection comment is required", None
    task = get_task(task_id)
    if not task:
        return False, "Task not found", None
    if task["status"] != STATUS_REVIEW:
        return False, f"Task must be in 'review' status to reject (current: {task['status']})", None

    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn = get_db_connection()
    conn.execute("""
    UPDATE tasks
    SET status = ?, rejection_comment = ?, assignee = NULL, claimed_at = NULL, updated_at = ?
    WHERE id = ?
    """, (STATUS_OPEN, comment, now, task_id))

    conn.execute("""
    INSERT INTO task_comments (task_id, author, comment_type, content, created_at)
    VALUES (?, ?, 'rejection', ?, ?)
    """, (task_id, username, f"Rejected by @{username}: {comment}", now))

    conn.commit()
    conn.close()
    return True, "Task rejected and returned to Open", get_task(task_id)

def move_task(task_id: int, new_status: str, new_position: int, username: str, reviewed_by_override: Optional[str] = None) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """Move task to a new status and/or vertical position in that column"""
    if new_status not in [STATUS_OPEN, STATUS_IN_PROGRESS, STATUS_REVIEW, STATUS_COMPLETED]:
        return False, f"Invalid status: {new_status}", None
    task = get_task(task_id)
    if not task:
        return False, "Task not found", None

    old_status = task["status"]
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn = get_db_connection()
    cursor = conn.cursor()

    assignee = task.get("assignee")
    claimed_at = task.get("claimed_at")
    reviewed_by = task.get("reviewed_by")
    completed_at = task.get("completed_at")

    if new_status != old_status:
        if new_status == STATUS_IN_PROGRESS:
            if not assignee:
                assignee = username
                claimed_at = now
        elif new_status == STATUS_OPEN:
            assignee = None
            claimed_at = None
        elif new_status == STATUS_REVIEW:
            if not assignee:
                assignee = username
        elif new_status == STATUS_COMPLETED:
            reviewed_by = reviewed_by_override or username
            completed_at = now

        cursor.execute("""
        INSERT INTO task_comments (task_id, author, comment_type, content, created_at)
        VALUES (?, ?, 'status_change', ?, ?)
        """, (task_id, username, f"Task moved from '{old_status}' to '{new_status}' by @{username}", now))

    # Fetch all tasks currently in new_status excluding task_id, ordered by position ASC, id DESC
    rows = cursor.execute("""
        SELECT id FROM tasks
        WHERE status = ? AND id != ?
        ORDER BY position ASC, id DESC
    """, (new_status, task_id)).fetchall()

    target_ids = [r["id"] for r in rows]
    if new_position < 0:
        new_position = 0
    if new_position > len(target_ids):
        new_position = len(target_ids)

    target_ids.insert(new_position, task_id)

    cursor.execute("""
        UPDATE tasks
        SET status = ?, assignee = ?, reviewed_by = ?, claimed_at = ?, completed_at = ?, updated_at = ?
        WHERE id = ?
    """, (new_status, assignee, reviewed_by, claimed_at, completed_at, now, task_id))

    for idx, t_id in enumerate(target_ids):
        cursor.execute("UPDATE tasks SET position = ? WHERE id = ?", (idx * 10, t_id))

    conn.commit()
    conn.close()
    return True, f"Task moved successfully to {new_status} at position {new_position}", get_task(task_id)

def delete_task(task_id: int) -> bool:
    """Delete a task, its audit history, and associated disk files"""
    conn = get_db_connection()
    att_rows = conn.execute("SELECT stored_filename FROM task_attachments WHERE task_id = ?", (task_id,)).fetchall()
    for row in att_rows:
        try:
            file_path = UPLOAD_DIR / row["stored_filename"]
            if file_path.exists():
                file_path.unlink(missing_ok=True)
        except Exception:
            pass

    cursor = conn.cursor()
    cursor.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    conn.commit()
    deleted = cursor.rowcount > 0
    conn.close()
    return deleted

def set_task_ai(task_id: int, enabled: bool) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    task = get_task(task_id)
    if not task:
        return False, "Task not found", None
    val = 1 if enabled else 0
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn = get_db_connection()
    conn.execute("UPDATE tasks SET ai_enabled = ?, updated_at = ? WHERE id = ?", (val, now, task_id))
    conn.commit()
    conn.close()
    return True, f"AI {'enabled' if enabled else 'disabled'} for task #{task_id}", get_task(task_id)

set_task_ai_enabled = set_task_ai

# ----------------- Settings & User Management -----------------

def get_setting(key: str, default: Any = None) -> Any:
    conn = get_db_connection()
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    conn.close()
    if not row:
        return default
    return row["value"]

def set_setting(key: str, value: str):
    conn = get_db_connection()
    conn.execute("""
    INSERT INTO settings (key, value) VALUES (?, ?)
    ON CONFLICT(key) DO UPDATE SET value = excluded.value
    """, (key, str(value)))
    conn.commit()
    conn.close()

def is_ai_mode() -> bool:
    val = get_setting("ai_mode", "1")
    return val in ["1", "true", "True"]

def toggle_ai_mode() -> bool:
    curr = is_ai_mode()
    new_val = "0" if curr else "1"
    set_setting("ai_mode", new_val)
    return not curr

def get_admins() -> List[int]:
    val = get_setting("admins", "[]")
    try:
        raw_list = json.loads(val)
        res = [int(x) for x in raw_list]
        return res if res else ([config.ADMIN_TG_ID] if config.ADMIN_TG_ID else [])
    except Exception:
        return [config.ADMIN_TG_ID] if config.ADMIN_TG_ID else []

def is_admin(tg_id: int) -> bool:
    return tg_id in get_admins()

def get_alert_recipients() -> List[int]:
    val = get_setting("alert_recipients", "[]")
    try:
        raw_list = json.loads(val)
        res = []
        for x in raw_list:
            try:
                res.append(int(x))
            except Exception:
                pass
        return res if res else ([config.ADMIN_TG_ID] if config.ADMIN_TG_ID else [])
    except Exception:
        return [config.ADMIN_TG_ID] if config.ADMIN_TG_ID else []

def add_alert_recipient(tg_id: int) -> bool:
    recipients = get_alert_recipients()
    if tg_id not in recipients:
        recipients.append(tg_id)
        set_setting("alert_recipients", json.dumps([str(x) for x in recipients]))
        return True
    return False

def remove_alert_recipient(tg_id: int) -> bool:
    recipients = get_alert_recipients()
    if tg_id in recipients:
        recipients.remove(tg_id)
        set_setting("alert_recipients", json.dumps([str(x) for x in recipients]))
        return True
    return False

def add_user(username: str, tg_id: int, display_name: str = "") -> bool:
    username = username.strip().lower()
    if not username or not tg_id:
        return False
    if not display_name:
        display_name = username.capitalize()
    conn = get_db_connection()
    try:
        conn.execute("UPDATE users SET telegram_id = NULL WHERE telegram_id = ?", (tg_id,))
        conn.execute("""
        INSERT INTO users (username, telegram_id, display_name, created_at)
        VALUES (?, ?, ?, datetime('now'))
        ON CONFLICT(username) DO UPDATE SET telegram_id = excluded.telegram_id, display_name = excluded.display_name
        """, (username, tg_id, display_name))
        conn.commit()
        config.TG_USER_MAP[tg_id] = username
        if username not in config.ALLOWED_USERS:
            config.ALLOWED_USERS.append(username)
        return True
    except Exception:
        return False
    finally:
        conn.close()

def remove_user(username: str) -> bool:
    username = username.strip().lower()
    if username == "nikita":
        return False
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM users WHERE username = ?", (username,))
    conn.commit()
    deleted = cursor.rowcount > 0
    conn.close()
    if deleted:
        if username in config.ALLOWED_USERS:
            config.ALLOWED_USERS.remove(username)
        keys_to_del = [k for k, v in config.TG_USER_MAP.items() if v == username]
        for k in keys_to_del:
            del config.TG_USER_MAP[k]
    return deleted

def get_all_users() -> List[Dict[str, Any]]:
    conn = get_db_connection()
    rows = conn.execute("SELECT username, telegram_id, display_name, created_at FROM users ORDER BY created_at ASC").fetchall()
    conn.close()
    return [dict(r) for r in rows]

# ----------------- AI Chat Queries & Agent Queue -----------------

def create_chat_query(user: str, prompt: str, task_ids: str = "") -> Dict[str, Any]:
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO ai_chat_queries (user, prompt, task_ids, status, created_at)
    VALUES (?, ?, ?, 'pending', ?)
    """, (user, prompt.strip(), task_ids.strip(), now))
    query_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return get_chat_query(query_id)

def get_chat_query(query_id: int) -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    row = conn.execute("SELECT * FROM ai_chat_queries WHERE id = ?", (query_id,)).fetchone()
    conn.close()
    return dict(row) if row else None

def get_pending_chat_query() -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    row = conn.execute("SELECT * FROM ai_chat_queries WHERE status = 'pending' ORDER BY id ASC LIMIT 1").fetchone()
    if row:
        conn.execute("UPDATE ai_chat_queries SET status = 'processing' WHERE id = ?", (row["id"],))
        conn.commit()
    conn.close()
    return dict(row) if row else None

def complete_chat_query(query_id: int, response: str, status: str = "done"):
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn = get_db_connection()
    conn.execute("""
    UPDATE ai_chat_queries
    SET response = ?, status = ?, completed_at = ?
    WHERE id = ?
    """, (response, status, now, query_id))
    conn.commit()
    conn.close()

def get_pending_ai_tasks() -> List[Dict[str, Any]]:
    conn = get_db_connection()
    rows = conn.execute("""
    SELECT id FROM tasks
    WHERE status = 'open' AND ai_enabled = 1 AND assignee IS NULL
    ORDER BY id ASC
    """).fetchall()
    conn.close()
    tasks = []
    for r in rows:
        t = get_task(r["id"])
        if t:
            tasks.append(t)
    return tasks

def add_comment(task_id: int, author: str, content: str, comment_type: str = "comment") -> Optional[Dict[str, Any]]:
    content = content.strip()
    if not content:
        return None
    task = get_task(task_id)
    if not task:
        return None
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO task_comments (task_id, author, comment_type, content, created_at)
    VALUES (?, ?, ?, ?, ?)
    """, (task_id, author, comment_type, content, now))
    comment_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return {
        "id": comment_id,
        "task_id": task_id,
        "author": author,
        "comment_type": comment_type,
        "content": content,
        "created_at": now
    }

def add_attachment(task_id: int, uploader: str, original_filename: str, stored_filename: str, file_size: int, mime_type: str) -> Dict[str, Any]:
    att_id = secrets.token_hex(12)
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn = get_db_connection()
    conn.execute("""
    INSERT INTO task_attachments (id, task_id, uploader, original_filename, stored_filename, file_size, mime_type, created_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (att_id, task_id, uploader, original_filename, stored_filename, file_size, mime_type, now))
    
    # Audit comment
    conn.execute("""
    INSERT INTO task_comments (task_id, author, comment_type, content, created_at)
    VALUES (?, ?, 'comment', ?, ?)
    """, (task_id, uploader, f"Attached file: {original_filename}", now))

    conn.commit()
    conn.close()
    return {
        "id": att_id,
        "task_id": task_id,
        "uploader": uploader,
        "original_filename": original_filename,
        "stored_filename": stored_filename,
        "file_size": file_size,
        "mime_type": mime_type,
        "created_at": now
    }

def get_attachment_by_id(attachment_id: str) -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    row = conn.execute("SELECT * FROM task_attachments WHERE id = ?", (attachment_id,)).fetchone()
    conn.close()
    if not row:
        return None
    return dict(row)

def get_stats() -> Dict[str, int]:
    conn = get_db_connection()
    stats = {
        STATUS_OPEN: 0,
        STATUS_IN_PROGRESS: 0,
        STATUS_REVIEW: 0,
        STATUS_COMPLETED: 0,
        "total": 0
    }
    rows = conn.execute("SELECT status, COUNT(*) as cnt FROM tasks GROUP BY status").fetchall()
    total = 0
    for r in rows:
        stats[r["status"]] = r["cnt"]
        total += r["cnt"]
    stats["total"] = total
    conn.close()
    return stats
