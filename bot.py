import asyncio
import logging
import httpx
import os
import re
import secrets
from typing import Dict, Any, Optional

import config
import database

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("telegram_bot")

TELEGRAM_API_BASE = f"https://api.telegram.org/bot{config.BOT_TOKEN}"

# In-memory user state for conversation flows (e.g. creating task, rejecting, adding comment)
# user_id -> {"state": "waiting_rejection", "task_id": 123}
user_states: Dict[int, Dict[str, Any]] = {}
active_bot_messages: Dict[int, int] = {}  # chat_id -> message_id of the bot's interactive menu message
BOT_USERNAME = "tasksboard67bot"

async def send_tg_request(method: str, payload: dict) -> Optional[dict]:
    url = f"{TELEGRAM_API_BASE}/{method}"
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            resp = await client.post(url, json=payload)
            data = resp.json()
            if not data.get("ok"):
                logger.warning(f"Telegram API {method} error: {data}")
            return data
        except Exception as e:
            logger.error(f"Error calling Telegram API {method}: {e}")
            return None

async def send_tg_document(chat_id: int | str, filename: str, content: bytes, caption: str = "", mime_type: str = "text/markdown") -> Optional[dict]:
    url = f"{TELEGRAM_API_BASE}/sendDocument"
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            files = {
                "document": (filename, content, mime_type)
            }
            data = {
                "chat_id": str(chat_id),
                "caption": caption[:1024] if caption else "",
                "parse_mode": "Markdown"
            }
            resp = await client.post(url, data=data, files=files)
            res_data = resp.json()
            if not res_data.get("ok"):
                data.pop("parse_mode", None)
                resp = await client.post(url, data=data, files={"document": (filename, content, mime_type)})
                res_data = resp.json()
            return res_data
        except Exception as e:
            logger.error(f"Error calling Telegram API sendDocument: {e}")
            return None

async def update_or_send_main_message(chat_id: int, text: str, reply_markup: dict, parse_mode: str = "Markdown") -> Optional[int]:
    """
    Edits the bot's single persistent interactive message in the chat,
    or sends a new one if editing fails, remembering its message_id.
    """
    msg_id = active_bot_messages.get(chat_id)
    if msg_id:
        res = await send_tg_request("editMessageText", {
            "chat_id": chat_id,
            "message_id": msg_id,
            "text": text,
            "parse_mode": parse_mode,
            "reply_markup": reply_markup
        })
        if res and res.get("ok"):
            return msg_id
        # Fallback to plain text if markdown formatting failed
        res_plain = await send_tg_request("editMessageText", {
            "chat_id": chat_id,
            "message_id": msg_id,
            "text": text.replace("*", "").replace("_", "").replace("`", ""),
            "reply_markup": reply_markup
        })
        if res_plain and res_plain.get("ok"):
            return msg_id
        # If edit failed (e.g. message deleted by user), clear and send new
        active_bot_messages.pop(chat_id, None)

    res = await send_tg_request("sendMessage", {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "reply_markup": reply_markup
    })
    if res and res.get("ok"):
        new_id = res["result"]["message_id"]
        active_bot_messages[chat_id] = new_id
        return new_id
    elif not (res and res.get("ok")):
        res_plain = await send_tg_request("sendMessage", {
            "chat_id": chat_id,
            "text": text.replace("*", "").replace("_", "").replace("`", ""),
            "reply_markup": reply_markup
        })
        if res_plain and res_plain.get("ok"):
            new_id = res_plain["result"]["message_id"]
            active_bot_messages[chat_id] = new_id
            return new_id
    return None

async def safe_edit_message_text(chat_id: int, message_id: Optional[int], text: str, reply_markup: dict) -> bool:
    """
    Safely edits a Telegram message with Markdown parsing.
    If Telegram rejects due to entity parsing error or length,
    automatically falls back to plain text truncated to 4000 chars.
    """
    if not message_id:
        return False
    # Attempt 1: Markdown parse mode with length clamped to 4000
    res = await send_tg_request("editMessageText", {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text[:4000],
        "parse_mode": "Markdown",
        "reply_markup": reply_markup
    })
    if res and res.get("ok"):
        return True

    logger.warning(f"editMessageText with Markdown failed in chat {chat_id}, trying plain text fallback...")

    # Attempt 2: Plain text (strip backticks, asterisks, underscores) clamped to 3900
    plain_text = text.replace("*", "").replace("`", "").replace("_", "")[:3900]
    res_plain = await send_tg_request("editMessageText", {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": plain_text,
        "reply_markup": reply_markup
    })
    return bool(res_plain and res_plain.get("ok"))

def is_admin_user(tg_id: int) -> bool:
    if config.ADMIN_TG_ID and tg_id == config.ADMIN_TG_ID:
        return True
    return database.is_admin(tg_id)

def build_main_menu_text(username: str, tg_id: int) -> str:
    stats = database.get_stats()
    text = (
        f"⚡ *Kanban Task Board*\n"
        f"*Logged in as:* `@{username}` _(ID: {tg_id})_\n"
        f"────────────────────────\n"
        f"📊 *Columns Overview:*\n"
        f"• 📥 *Open:* `{stats[config.STATUS_OPEN]}`\n"
        f"• ⏳ *In Progress:* `{stats[config.STATUS_IN_PROGRESS]}`\n"
        f"• 🔍 *Under Review:* `{stats[config.STATUS_REVIEW]}`\n"
        f"• ✅ *Completed:* `{stats[config.STATUS_COMPLETED]}`\n"
        f"• 🤖 *AI Mode:* `{'ENABLED' if database.is_ai_mode() else 'DISABLED'}`\n"
        f"────────────────────────\n"
        f"Select a column or action below:"
    )
    return text

def build_servers_text() -> str:
    lines = [
        "🖥 *Инфраструктура кластера и алиасы серверов:*",
        "────────────────────────"
    ]
    for s_key, s_data in config.CLUSTER_SERVERS.items():
        aliases_str = ", ".join([f"`{a}`" for a in s_data["aliases"]])
        aliases_line = f" (алиасы: {aliases_str})" if aliases_str else ""
        lines.append(f"• 🔹 *{s_data['name']}*{aliases_line}:")
        lines.append(f"  IP: `{s_data['ip']}` | SSH: `ssh {s_data['ssh_host']}`")
        lines.append(f"  _{s_data['description']}_\n")
    
    lines.append("────────────────────────")
    lines.append("🤖 *Управление через AI Agent:*")
    lines.append("AI-агент на сервере `andrii` знает все эти алиасы и имеет настроенный SSH-доступ ко всем серверам.")
    lines.append("")
    lines.append("_Вы можете просто написать в чат бота, например:_")
    lines.append("• «Сделай что-то с сервером home»")
    lines.append("• «Проверь нагрузку на шкафу»")
    lines.append("• «Перезагрузи службу на сервере vpn»")
    return "\n".join(lines)

def build_servers_keyboard() -> dict:
    return {
        "inline_keyboard": [
            [{"text": "🔙 Главное меню", "callback_data": "nav:main"}]
        ]
    }

def build_main_menu_keyboard(tg_id: int = 0) -> dict:
    stats = database.get_stats()
    rows = [
        [
            {"text": f"📥 Open ({stats[config.STATUS_OPEN]})", "callback_data": f"col:{config.STATUS_OPEN}:0"},
            {"text": f"⏳ In Progress ({stats[config.STATUS_IN_PROGRESS]})", "callback_data": f"col:{config.STATUS_IN_PROGRESS}:0"}
        ],
        [
            {"text": f"🔍 In Review ({stats[config.STATUS_REVIEW]})", "callback_data": f"col:{config.STATUS_REVIEW}:0"},
            {"text": f"✅ Completed ({stats[config.STATUS_COMPLETED]})", "callback_data": f"col:{config.STATUS_COMPLETED}:0"}
        ],
        [
            {"text": "➕ Create Task", "callback_data": "action:create_task"},
            {"text": "🖥 Servers & Cluster", "callback_data": "action:servers"}
        ],
        [
            {"text": "🔑 API Keys", "callback_data": "action:api_keys"},
            {"text": "📚 API Documentation", "callback_data": "action:api_docs"}
        ]
    ]

    # Only show Settings button for admin
    if is_admin_user(tg_id):
        rows.append([{"text": "⚙️ Project Settings", "callback_data": "settings:menu"}])

    rows.append([{"text": "🔄 Refresh", "callback_data": "nav:main"}])
    return {"inline_keyboard": rows}

def build_settings_menu_text() -> str:
    ai_mode = database.is_ai_mode()
    users = database.get_all_users()
    admins = database.get_admins()
    alert_recipients = database.get_alert_recipients()
    
    text = (
        f"⚙️ *Project & Farm Settings (Admin: nikita)*\n"
        f"────────────────────────\n"
        f"• 🤖 *AI Worker Mode:* `{'ENABLED' if ai_mode else 'DISABLED'}`\n"
        f"  _(Model: gemini-3.8-flash, effort: medium on server 'andrii')_\n\n"
        f"• 🚨 *Incident Alert Recipients ({len(alert_recipients)}):*\n"
    )
    for r in alert_recipients:
        uname = config.TG_USER_MAP.get(r, "")
        u_suffix = f" (@{uname})" if uname else ""
        text += f"  - `{r}`{u_suffix}\n"
    text += f"\n• 👥 *Registered Users ({len(users)}):*\n"
    for u in users:
        is_adm = " [Admin]" if u["telegram_id"] in admins else ""
        text += f"  - `@{u['username']}` (ID: `{u['telegram_id']}`){is_adm}\n"
    text += f"\n• 🛡️ *Admins:* {', '.join(str(a) for a in admins)}\n"
    text += "────────────────────────\nChoose an action below:"
    return text

def build_settings_menu_keyboard() -> dict:
    ai_mode = database.is_ai_mode()
    toggle_text = "🤖 Turn OFF AI Mode" if ai_mode else "🤖 Turn ON AI Mode"
    return {
        "inline_keyboard": [
            [{"text": toggle_text, "callback_data": "settings:toggle_ai"}],
            [{"text": "👥 Manage Users (Add/Remove)", "callback_data": "settings:users"}],
            [{"text": "🚨 Incident Alert Recipients", "callback_data": "settings:alerts"}],
            [{"text": "🔙 Main Menu", "callback_data": "nav:main"}]
        ]
    }

def build_manage_alerts_text() -> str:
    recipients = database.get_alert_recipients()
    text = (
        f"🚨 *Farm Incident Alert Recipients*\n"
        f"────────────────────────\n"
        f"Users in this list receive automated incident reports from the Pixabay Farm AI agent when metrics drop (0 likes in 1h or server down).\n\n"
        f"*Current Recipients ({len(recipients)}):*\n"
    )
    for r in recipients:
        uname = config.TG_USER_MAP.get(r, "")
        u_suffix = f" (@{uname})" if uname else ""
        text += f"• `{r}`{u_suffix}\n"
    text += "\n────────────────────────\nClick a recipient to remove or click Add Recipient:"
    return text

def build_manage_alerts_keyboard() -> dict:
    recipients = database.get_alert_recipients()
    buttons = [
        [{"text": "➕ Add Alert Recipient", "callback_data": "settings:add_alert_prompt"}]
    ]
    for r in recipients:
        uname = config.TG_USER_MAP.get(r, "")
        u_suffix = f" (@{uname})" if uname else ""
        buttons.append([{"text": f"❌ Remove {r}{u_suffix}", "callback_data": f"settings:remove_alert:{r}"}])
    buttons.append([{"text": "🔙 Back to Settings", "callback_data": "settings:menu"}])
    return {"inline_keyboard": buttons}

def build_manage_users_text() -> str:
    users = database.get_all_users()
    text = (
        f"👥 *Manage Project Users*\n"
        f"────────────────────────\n"
    )
    for u in users:
        text += f"• `@{u['username']}` (ID: `{u['telegram_id']}`)\n"
    text += "────────────────────────\nClick a user to remove or click Add User:"
    return text

def build_manage_users_keyboard() -> dict:
    users = database.get_all_users()
    buttons = [
        [{"text": "➕ Add New User", "callback_data": "settings:add_user_prompt"}]
    ]
    for u in users:
        if u["username"] != "nikita":
            buttons.append([{"text": f"❌ Remove @{u['username']}", "callback_data": f"settings:remove_user:{u['username']}"}])
    buttons.append([{"text": "🔙 Back to Settings", "callback_data": "settings:menu"}])
    return {"inline_keyboard": buttons}

def build_column_text(status_key: str, tasks: list, page: int = 0, per_page: int = 5) -> str:
    status_titles = {
        config.STATUS_OPEN: "📥 Open Tasks",
        config.STATUS_IN_PROGRESS: "⏳ Tasks In Progress",
        config.STATUS_REVIEW: "🔍 Tasks Under Review",
        config.STATUS_COMPLETED: "✅ Completed Tasks",
    }
    title = status_titles.get(status_key, "Tasks")
    total = len(tasks)
    start = page * per_page
    end = min(start + per_page, total)
    
    text = f"*{title}* (Total: {total})\n────────────────────────\n"
    if not tasks:
        text += "_No tasks in this column._\n"
    else:
        for i, t in enumerate(tasks[start:end], start=start + 1):
            assignee_str = f" [Assignee: @{t['assignee']}]" if t.get("assignee") else ""
            reviewer_str = f" [Reviewed by: @{t['reviewed_by']}]" if t.get("reviewed_by") else ""
            rej_str = " ⚠️(Previously Rejected)" if t.get("rejection_comment") and t["status"] == config.STATUS_OPEN else ""
            text += f"*{t['id']}.* {t['title']}{assignee_str}{reviewer_str}{rej_str}\n"
    text += "────────────────────────\nClick a task below to view details:"
    return text

def build_column_keyboard(status_key: str, tasks: list, page: int = 0, per_page: int = 5) -> dict:
    total = len(tasks)
    start = page * per_page
    end = min(start + per_page, total)
    buttons = []

    # Task buttons
    for t in tasks[start:end]:
        title_snippet = t['title'][:25] + ("..." if len(t['title']) > 25 else "")
        buttons.append([{"text": f"#{t['id']} {title_snippet}", "callback_data": f"task:{t['id']}"}])

    # Pagination buttons
    nav_row = []
    if page > 0:
        nav_row.append({"text": "⬅️ Prev", "callback_data": f"col:{status_key}:{page-1}"})
    if end < total:
        nav_row.append({"text": "Next ➡️", "callback_data": f"col:{status_key}:{page+1}"})
    if nav_row:
        buttons.append(nav_row)

    buttons.append([
        {"text": "🔙 Main Menu", "callback_data": "nav:main"},
        {"text": "🔄 Refresh", "callback_data": f"col:{status_key}:{page}"}
    ])
    return {"inline_keyboard": buttons}

def build_task_detail_text(task: dict) -> str:
    status_badges = {
        config.STATUS_OPEN: "📥 OPEN",
        config.STATUS_IN_PROGRESS: "⏳ IN PROGRESS",
        config.STATUS_REVIEW: "🔍 UNDER REVIEW",
        config.STATUS_COMPLETED: "✅ COMPLETED",
    }
    badge = status_badges.get(task["status"], task["status"].upper())
    
    text = (
        f"📌 *Task #{task['id']}: {task['title']}*\n"
        f"Status: *{badge}*\n"
        f"Created by: `@{task['created_by']}` on `{task['created_at'][:16].replace('T', ' ')}`\n"
    )
    ai_stat = "✅ Разрешен (Autonomous Agent)" if task.get("ai_enabled", 0) else "❌ Выключен"
    text += f"🤖 *AI Agent:* {ai_stat}\n"

    if task.get("assignee"):
        text += f"Taken by: `@{task['assignee']}`\n"
    if task.get("reviewed_by"):
        text += f"Verified by: `@{task['reviewed_by']}` on `{task.get('completed_at', '')[:16].replace('T', ' ')}`\n"
    
    if task.get("rejection_comment") and task["status"] == config.STATUS_OPEN:
        text += f"⚠️ *Rejection Reason:* _{task['rejection_comment']}_\n"

    text += f"────────────────────────\n*Description:*\n"
    desc = task.get("description") or "_No description provided._"
    if len(desc) > 600:
        desc = desc[:597] + "..."
    text += f"{desc}\n────────────────────────\n"

    # Attachments
    attachments = task.get("attachments", [])
    if attachments:
        text += f"📎 *Attachments ({len(attachments)}):*\n"
        for a in attachments[:5]:
            size_kb = max(1, a["file_size"] // 1024)
            text += f"• `{a['original_filename']}` ({size_kb} KB)\n"
        if len(attachments) > 5:
            text += f"_...and {len(attachments) - 5} more attachments_\n"
        text += "────────────────────────\n"

    # Recent Comments
    comments = task.get("comments", [])
    if comments:
        text += f"💬 *Recent Activity / Comments:*\n"
        for c in comments[-3:]:
            prefix = "⚠️ " if c.get("comment_type") == "rejection" else "• "
            c_author = c.get("author", "user")
            c_content = (c.get("content") or "").strip()
            if len(c_content) > 250:
                c_content = c_content[:247] + "..."
            text += f"{prefix}*@{c_author}*: {c_content}\n"
        text += "────────────────────────\n"

    if len(text) > 3800:
        text = text[:3750] + "\n\n...(truncated)"

    return text

def build_task_keyboard(task: dict, current_user: str) -> dict:
    buttons = []
    task_id = task["id"]
    status = task["status"]

    action_row = []
    if status == config.STATUS_OPEN:
        action_row.append({"text": "🎯 Claim Task (Take)", "callback_data": f"act:claim:{task_id}"})
    elif status == config.STATUS_IN_PROGRESS:
        action_row.append({"text": "📨 Submit for Review", "callback_data": f"act:submit:{task_id}"})
        action_row.append({"text": "↩️ Release", "callback_data": f"act:release:{task_id}"})
    elif status == config.STATUS_REVIEW:
        action_row.append({"text": "✅ Approve & Complete", "callback_data": f"act:approve:{task_id}"})
        action_row.append({"text": "❌ Reject", "callback_data": f"act:reject_prompt:{task_id}"})
    
    if action_row:
        buttons.append(action_row)

    if status == config.STATUS_OPEN:
        ai_on = bool(task.get("ai_enabled", 0))
        btn_text = "🤖 AI: ✅ Включен (Выключить)" if ai_on else "🤖 AI: ❌ Выключен (Разрешить нейронке)"
        buttons.append([{"text": btn_text, "callback_data": f"act:ai_toggle:{task_id}"}])

    buttons.append([
        {"text": "💬 Add Comment", "callback_data": f"act:comment_prompt:{task_id}"},
        {"text": "📎 Attach File", "callback_data": f"act:attach_prompt:{task_id}"}
    ])

    if task.get("attachments"):
        buttons.append([
            {"text": f"📥 Скачать отчет / файлы ({len(task['attachments'])})", "callback_data": f"act:get_files:{task_id}"}
        ])

    buttons.append([
        {"text": "🗑 Delete Task", "callback_data": f"act:delete_prompt:{task_id}"}
    ])

    buttons.append([
        {"text": "🔙 Back to Column", "callback_data": f"col:{status}:0"},
        {"text": "🏠 Main Menu", "callback_data": "nav:main"}
    ])
    return {"inline_keyboard": buttons}

def build_api_keys_text(username: str) -> str:
    keys = database.list_api_keys(username)
    text = (
        f"🔑 *API Keys for @{username}*\n"
        f"────────────────────────\n"
        f"Use API keys to access the Kanban board via REST API.\n\n"
    )
    if not keys:
        text += "_You have no active API keys yet._\n"
    else:
        for k in keys:
            status_ico = "🟢" if k["is_active"] else "🔴"
            last_used = f" (Used: {k['last_used_at'][:10]})" if k["last_used_at"] else ""
            text += f"{status_ico} *{k['name']}*: `{k['key_prefix']}`{last_used}\n"
    text += "────────────────────────\n"
    return text

def build_api_keys_keyboard() -> dict:
    return {
        "inline_keyboard": [
            [{"text": "➕ Generate New API Key", "callback_data": "act:gen_key"}],
            [{"text": "📚 API Documentation", "callback_data": "action:api_docs"}],
            [{"text": "🔙 Main Menu", "callback_data": "nav:main"}]
        ]
    }

def build_api_docs_text(section: str = "overview") -> str:
    if section == "tasks":
        return (
            "📋 *API Docs: Tasks Management*\n"
            "────────────────────────\n"
            "• `GET /api/v1/tasks`\n"
            "  List all tasks. Optional filters:\n"
            "  `?status=open&assignee=dmitry`\n\n"
            "• `GET /api/v1/tasks/{id}`\n"
            "  Get detailed task object with comments and attachments.\n\n"
            "• `POST /api/v1/tasks`\n"
            "  Create task. Body:\n"
            "  `{\"title\":\"...\", \"description\":\"...\", \"ai_enabled\": 0}`\n\n"
            "• `POST /api/v1/tasks/{id}/claim`\n"
            "  Take task (Open ➔ In Progress).\n\n"
            "• `POST /api/v1/tasks/{id}/release`\n"
            "  Release task (In Progress ➔ Open).\n\n"
            "• `POST /api/v1/tasks/{id}/submit-review`\n"
            "  Submit task (In Progress ➔ Review).\n\n"
            "• `POST /api/v1/tasks/{id}/approve`\n"
            "  Approve task (Review ➔ Completed).\n\n"
            "• `POST /api/v1/tasks/{id}/reject`\n"
            "  Reject task back to Open. Body:\n"
            "  `{\"comment\": \"Reason...\"}`\n\n"
            "• `POST /api/v1/tasks/{id}/move`\n"
            "  Move column/order. Body: `{\"status\":\"...\", \"position\":0, \"reviewed_by\":\"...\"}`\n\n"
            "• `DELETE /api/v1/tasks/{id}`\n"
            "  Permanently delete task.\n"
            "────────────────────────"
        )
    elif section == "media":
        return (
            "📎 *API Docs: Attachments & Comments*\n"
            "────────────────────────\n"
            "• `POST /api/v1/tasks/{id}/comments`\n"
            "  Add comment or audit message.\n"
            "  Body: `{\"content\": \"...\"}` (max 10,000 chars)\n\n"
            "• `POST /api/v1/tasks/{id}/attachments`\n"
            "  Upload file (images, docs, archives, max 25MB).\n"
            "  Multipart Form-Data: `file=@photo.png`\n\n"
            "• `GET /api/v1/attachments/{id}`\n"
            "  Download/stream attachment file.\n\n"
            "• `GET /api/v1/keys`\n"
            "  List your active API keys.\n\n"
            "• `POST /api/v1/keys`\n"
            "  Generate new API key. Body: `{\"name\": \"...\"}`\n\n"
            "• `DELETE /api/v1/keys/{key_id}`\n"
            "  Revoke an existing API key.\n"
            "────────────────────────"
        )
    elif section == "ai":
        return (
            "🤖 *API Docs: Autonomous AI Agent & Chat*\n"
            "────────────────────────\n"
            "• `GET /api/v1/settings/public`\n"
            "  Returns `{ \"ai_mode\": true/false }`.\n\n"
            "• `POST /api/v1/tasks/{id}/ai-toggle`\n"
            "  Toggle AI permission on open task.\n"
            "  Optional Body: `{\"enabled\": true/false}`\n\n"
            "• `POST /api/v1/ai/chat`\n"
            "  Ask AI agent question with task context.\n"
            "  Body: `{\"prompt\": \"...\", \"task_ids\": \"1,4\"}`\n"
            "  Returns: `{\"ok\": true, \"query_id\": 1}`\n\n"
            "• `GET /api/v1/ai/chat/{query_id}`\n"
            "  Poll status of chat response (`pending` / `done`).\n\n"
            "• `GET /api/v1/ai/agent/pending-work`\n"
            "  Used by daemon on server 'andrii' to poll pending tasks and chat queries.\n\n"
            "• `POST /api/v1/ai/agent/chat-response/{id}`\n"
            "  Post response back from daemon.\n\n"
            "• `GET /api/v1/settings/alerts`\n"
            "  List Telegram IDs receiving farm incident alerts.\n\n"
            "• `POST /api/v1/ai/agent/incident-report`\n"
            "  Submit incident remediation report from agent. Sends Telegram alerts to all recipients.\n"
            "────────────────────────"
        )
    elif section == "examples":
        base = config.BASE_URL
        return (
            "💻 *API Docs: cURL Examples*\n"
            "────────────────────────\n"
            "*1. List Open Tasks:*\n"
            f"`curl -sk -H \"X-API-Key: YOUR_KEY\" \\\n"
            f"  \"{base}/api/v1/tasks?status=open\"`\n\n"
            "*2. Create Task with AI Enabled:*\n"
            f"`curl -sk -X POST {base}/api/v1/tasks \\\n"
            "  -H \"X-API-Key: YOUR_KEY\" \\\n"
            "  -H \"Content-Type: application/json\" \\\n"
            "  -d '{\"title\":\"New Task\",\"description\":\"Notes...\",\"ai_enabled\":1}'`\n\n"
            "*3. Chat with Autonomous Agent:*\n"
            f"`curl -sk -X POST {base}/api/v1/ai/chat \\\n"
            "  -H \"X-API-Key: YOUR_KEY\" \\\n"
            "  -H \"Content-Type: application/json\" \\\n"
            "  -d '{\"prompt\":\"Check test status\",\"task_ids\":\"1,4\"}'`\n\n"
            "*4. Upload File Attachment:*\n"
            f"`curl -sk -X POST {base}/api/v1/tasks/4/attachments \\\n"
            "  -H \"X-API-Key: YOUR_KEY\" \\\n"
            "  -F \"file=@report.md\"`\n"
            "────────────────────────"
        )
    else:  # overview
        base = config.BASE_URL
        return (
            "📚 *Kanban Board REST API Documentation*\n"
            "────────────────────────\n"
            "*Base Endpoint:*\n"
            f"• `{base}`\n\n"
            "*Interactive Web Documentation:*\n"
            f"• Swagger UI: `{base}/docs`\n"
            f"• Docs UI: `{base}/api-docs`\n\n"
            "*Authentication:*\n"
            "Provide your API key in every request:\n"
            "`X-API-Key: kb_<user>_<token>`\n"
            "or\n"
            "`Authorization: Bearer kb_<user>_<token>`\n\n"
            "*Rate Limiting & Protection:*\n"
            "• Max 120 req/min per IP.\n"
            "• Timing-safe hash comparison.\n"
            "• Maximum attachment size: 25 MB.\n"
            "────────────────────────\n"
            "_Select a topic below for detailed endpoint specifications:_"
        )

def build_api_docs_keyboard(current_section: str = "overview") -> dict:
    def mark(sec: str, label: str) -> str:
        return f"• {label} •" if sec == current_section else label

    return {
        "inline_keyboard": [
            [
                {"text": mark("overview", "📌 Overview"), "callback_data": "docs:overview"},
                {"text": mark("tasks", "📋 Tasks"), "callback_data": "docs:tasks"}
            ],
            [
                {"text": mark("media", "📎 Media & Keys"), "callback_data": "docs:media"},
                {"text": mark("ai", "🤖 AI Agent"), "callback_data": "docs:ai"}
            ],
            [
                {"text": mark("examples", "💻 cURL Examples"), "callback_data": "docs:examples"}
            ],
            [
                {"text": "🔑 Manage API Keys", "callback_data": "action:api_keys"},
                {"text": "🏠 Main Menu", "callback_data": "nav:main"}
            ]
        ]
    }

async def handle_callback_query(cq: dict):
    cq_id = cq["id"]
    from_user = cq["from"]
    tg_id = from_user["id"]
    data = cq.get("data", "")
    message = cq.get("message")
    
    # Check authorization
    if tg_id not in config.TG_USER_MAP:
        await send_tg_request("answerCallbackQuery", {
            "callback_query_id": cq_id,
            "text": "⛔ Unauthorized: Access Denied",
            "show_alert": True
        })
        return

    username = config.TG_USER_MAP[tg_id]
    chat_id = message["chat"]["id"] if message else tg_id
    message_id = message["message_id"] if message else None

    if message_id:
        active_bot_messages[chat_id] = message_id

    # Acknowledge callback immediately
    await send_tg_request("answerCallbackQuery", {"callback_query_id": cq_id})

    # Clear pending state if navigation occurs
    if data.startswith("nav:") or data.startswith("col:") or data.startswith("task:") or data.startswith("docs:") or data in ["action:api_docs", "action:servers", "prompt:cancel"]:
        user_states.pop(tg_id, None)

    # Route navigation
    if data == "nav:main":
        text = build_main_menu_text(username, tg_id)
        reply_markup = build_main_menu_keyboard(tg_id)
        if message_id:
            await safe_edit_message_text(chat_id, message_id, text, reply_markup)

    elif data.startswith("col:"):
        parts = data.split(":")
        status_key = parts[1]
        page = int(parts[2]) if len(parts) > 2 else 0
        tasks = database.list_tasks(status=status_key)
        text = build_column_text(status_key, tasks, page)
        reply_markup = build_column_keyboard(status_key, tasks, page)
        if message_id:
            await safe_edit_message_text(chat_id, message_id, text, reply_markup)

    elif data.startswith("task:"):
        task_id = int(data.split(":")[1])
        task = database.get_task(task_id)
        if not task:
            if message_id:
                await safe_edit_message_text(
                    chat_id, message_id,
                    f"❌ Task #{task_id} not found.",
                    {"inline_keyboard": [[{"text": "🔙 Main Menu", "callback_data": "nav:main"}]]}
                )
            return
        text = build_task_detail_text(task)
        reply_markup = build_task_keyboard(task, username)
        if message_id:
            await safe_edit_message_text(chat_id, message_id, text, reply_markup)

    elif data.startswith("act:claim:"):
        task_id = int(data.split(":")[2])
        ok, msg, updated_task = database.claim_task(task_id, username)
        if ok and updated_task:
            text = build_task_detail_text(updated_task)
            reply_markup = build_task_keyboard(updated_task, username)
            await safe_edit_message_text(chat_id, message_id, f"✅ *{msg}*\n\n" + text, reply_markup)
        else:
            await send_tg_request("answerCallbackQuery", {"callback_query_id": cq_id, "text": f"❌ {msg}", "show_alert": True})

    elif data.startswith("act:release:"):
        task_id = int(data.split(":")[2])
        ok, msg, updated_task = database.release_task(task_id, username)
        if ok and updated_task:
            text = build_task_detail_text(updated_task)
            reply_markup = build_task_keyboard(updated_task, username)
            await safe_edit_message_text(chat_id, message_id, f"ℹ️ *{msg}*\n\n" + text, reply_markup)
        else:
            await send_tg_request("answerCallbackQuery", {"callback_query_id": cq_id, "text": f"❌ {msg}", "show_alert": True})

    elif data.startswith("act:submit:"):
        task_id = int(data.split(":")[2])
        ok, msg, updated_task = database.submit_task_for_review(task_id, username)
        if ok and updated_task:
            text = build_task_detail_text(updated_task)
            reply_markup = build_task_keyboard(updated_task, username)
            await safe_edit_message_text(chat_id, message_id, f"🚀 *{msg}*\n\n" + text, reply_markup)
        else:
            await send_tg_request("answerCallbackQuery", {"callback_query_id": cq_id, "text": f"❌ {msg}", "show_alert": True})

    elif data.startswith("act:approve:"):
        task_id = int(data.split(":")[2])
        ok, msg, updated_task = database.approve_task(task_id, username)
        if ok and updated_task:
            text = build_task_detail_text(updated_task)
            reply_markup = build_task_keyboard(updated_task, username)
            await safe_edit_message_text(chat_id, message_id, f"🎉 *{msg}*\n\n" + text, reply_markup)
        else:
            await send_tg_request("answerCallbackQuery", {"callback_query_id": cq_id, "text": f"❌ {msg}", "show_alert": True})

    elif data.startswith("act:reject_prompt:"):
        task_id = int(data.split(":")[2])
        user_states[tg_id] = {"state": "waiting_rejection", "task_id": task_id, "msg_id": message_id}
        await send_tg_request("editMessageText", {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": (
                f"❌ *Rejecting Task #{task_id}*\n\n"
                f"Please send a message with your *rejection comment* explaining why this task is being returned to Open.\n\n"
                f"_Send text in your next message or click Cancel._"
            ),
            "parse_mode": "Markdown",
            "reply_markup": {"inline_keyboard": [[{"text": "❌ Cancel", "callback_data": f"task:{task_id}"}]]}
        })

    elif data.startswith("act:comment_prompt:"):
        task_id = int(data.split(":")[2])
        user_states[tg_id] = {"state": "waiting_comment", "task_id": task_id, "msg_id": message_id}
        await send_tg_request("editMessageText", {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": (
                f"💬 *Adding Comment to Task #{task_id}*\n\n"
                f"Send your comment text in your next message."
            ),
            "parse_mode": "Markdown",
            "reply_markup": {"inline_keyboard": [[{"text": "❌ Cancel", "callback_data": f"task:{task_id}"}]]}
        })

    elif data.startswith("act:attach_prompt:"):
        task_id = int(data.split(":")[2])
        user_states[tg_id] = {"state": "waiting_attachment", "task_id": task_id, "msg_id": message_id}
        await send_tg_request("editMessageText", {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": (
                f"📎 *Attach File to Task #{task_id}*\n\n"
                f"Send any photo or document file now to attach it to this task."
            ),
            "parse_mode": "Markdown",
            "reply_markup": {"inline_keyboard": [[{"text": "❌ Cancel", "callback_data": f"task:{task_id}"}]]}
        })

    elif data.startswith("act:get_files:"):
        task_id = int(data.split(":")[2])
        task = database.get_task(task_id)
        if not task or not task.get("attachments"):
            await send_tg_request("answerCallbackQuery", {
                "callback_query_id": cq_id,
                "text": "У этой задачи нет файлов или отчетов.",
                "show_alert": True
            })
            return
        
        await send_tg_request("answerCallbackQuery", {
            "callback_query_id": cq_id,
            "text": "Отправляю файлы задачи..."
        })
        for att in task["attachments"]:
            file_path = config.UPLOAD_DIR / att["stored_filename"]
            if file_path.is_file():
                try:
                    content = file_path.read_bytes()
                    mime = att.get("mime_type") or "application/octet-stream"
                    caption = f"📎 Задача #{task_id}: `{att['original_filename']}`"
                    await send_tg_document(
                        chat_id=chat_id,
                        filename=att["original_filename"],
                        content=content,
                        caption=caption,
                        mime_type=mime
                    )
                except Exception as e:
                    logger.error(f"Failed to send attachment {att['stored_filename']} to chat {chat_id}: {e}")

    elif data.startswith("act:delete_prompt:"):
        task_id = int(data.split(":")[2])
        await send_tg_request("editMessageText", {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": (
                f"🗑 *Delete Task #{task_id}?*\n\n"
                f"Are you sure you want to permanently delete this task and its attachments?"
            ),
            "parse_mode": "Markdown",
            "reply_markup": {"inline_keyboard": [
                [{"text": "⚠️ Yes, Delete", "callback_data": f"act:delete_confirm:{task_id}"}],
                [{"text": "❌ Cancel", "callback_data": f"task:{task_id}"}]
            ]}
        })

    elif data.startswith("act:delete_confirm:"):
        task_id = int(data.split(":")[2])
        database.delete_task(task_id)
        await send_tg_request("editMessageText", {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": f"✅ *Task #{task_id} has been permanently deleted.*",
            "parse_mode": "Markdown",
            "reply_markup": {"inline_keyboard": [
                [{"text": "🏠 Main Menu", "callback_data": "nav:main"}]
            ]}
        })

    elif data == "action:create_task":
        user_states[tg_id] = {"state": "waiting_task_create", "msg_id": message_id}
        await send_tg_request("editMessageText", {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": (
                f"➕ *Create New Task*\n\n"
                f"Send the task title and optional description in your next message.\n\n"
                f"_Format example:_\n"
                f"`Fix payment gateway timeout\nInvestigate webhook retries`\n\n"
                f"_You can also send a photo or document with a caption._"
            ),
            "parse_mode": "Markdown",
            "reply_markup": {"inline_keyboard": [[{"text": "❌ Cancel", "callback_data": "nav:main"}]]}
        })

    elif data == "action:api_keys":
        text = build_api_keys_text(username)
        reply_markup = build_api_keys_keyboard()
        await send_tg_request("editMessageText", {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": "Markdown",
            "reply_markup": reply_markup
        })

    elif data == "action:servers":
        text = build_servers_text()
        reply_markup = build_servers_keyboard()
        if message_id:
            await send_tg_request("editMessageText", {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text,
                "parse_mode": "Markdown",
                "reply_markup": reply_markup
            })

    elif data == "act:gen_key":
        raw_key, info = database.generate_api_key(username, "Telegram Bot Key")
        text = (
            f"✅ *New API Key Generated!*\n\n"
            f"🔑 *Your Key:*\n`{raw_key}`\n\n"
            f"⚠️ *Important:* Copy and store this key now. For security, it cannot be displayed again.\n\n"
            f"You can authenticate requests with:\n"
            f"`X-API-Key: {raw_key}`"
        )
        reply_markup = {"inline_keyboard": [
            [{"text": "🔑 Back to API Keys", "callback_data": "action:api_keys"}],
            [{"text": "📚 API Documentation", "callback_data": "action:api_docs"}],
            [{"text": "🏠 Main Menu", "callback_data": "nav:main"}]
        ]}
        await send_tg_request("editMessageText", {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": "Markdown",
            "reply_markup": reply_markup
        })

    elif data == "action:api_docs" or data.startswith("docs:"):
        section = "overview"
        if data.startswith("docs:"):
            parts = data.split(":")
            if len(parts) > 1 and parts[1]:
                section = parts[1]
        text = build_api_docs_text(section)
        reply_markup = build_api_docs_keyboard(section)
        if message_id:
            await send_tg_request("editMessageText", {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text,
                "parse_mode": "Markdown",
                "reply_markup": reply_markup
            })

    elif data.startswith("act:ai_toggle:"):
        task_id = int(data.split(":")[2])
        task = database.get_task(task_id)
        if not task:
            await send_tg_request("answerCallbackQuery", {"callback_query_id": cq_id, "text": "❌ Task not found", "show_alert": True})
            return
        curr_ai = bool(task.get("ai_enabled", 0))
        new_val = not curr_ai
        database.set_task_ai_enabled(task_id, new_val)
        updated_task = database.get_task(task_id)
        text = build_task_detail_text(updated_task)
        reply_markup = build_task_keyboard(updated_task, username)
        status_word = "enabled" if new_val else "disabled"
        await send_tg_request("answerCallbackQuery", {"callback_query_id": cq_id, "text": f"🤖 AI Worker {status_word} for task #{task_id}"})
        if message_id:
            await safe_edit_message_text(chat_id, message_id, text, reply_markup)

    elif data == "prompt:run_ai":
        st = user_states.pop(tg_id, None)
        prompt_text = st.get("prompt_text", "") if st else ""
        if not prompt_text:
            await send_tg_request("editMessageText", {
                "chat_id": chat_id, "message_id": message_id,
                "text": "❌ Время действия запроса истекло или текст пуст.",
                "reply_markup": {"inline_keyboard": [[{"text": "🔙 Главное меню", "callback_data": "nav:main"}]]}
            })
            return
        lines = prompt_text.split("\n", 1)
        title = lines[0].strip()
        desc = lines[1].strip() if len(lines) > 1 else ""
        ai_global = database.is_ai_mode()
        task = database.create_task(title=title, description=desc, created_by=username, ai_enabled=1)
        notice_ai = "AI-агент на сервере `andrii` автоматически возьмет ее в работу." if ai_global else "⚠️ Внимание: глобальный AI Mode сейчас выключен в настройках. Включите его в настройках проекта, чтобы агент начал выполнение."
        await send_tg_request("editMessageText", {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": (
                f"🤖 *Задача передана AI-агенту!*\n\n"
                f"📌 *Task #{task['id']}:* {task['title']}\n"
                f"👤 Автор: `@{username}`\n"
                f"⚙️ Статус: `{task['status']}` | 🤖 AI Worker: `Разрешен`\n\n"
                f"_{notice_ai}_"
            ),
            "parse_mode": "Markdown",
            "reply_markup": {"inline_keyboard": [
                [{"text": f"📋 Открыть задачу #{task['id']}", "callback_data": f"task:{task['id']}"}],
                [{"text": "🏠 Главное меню", "callback_data": "nav:main"}]
            ]}
        })

    elif data == "prompt:ask_ai":
        st = user_states.pop(tg_id, None)
        prompt_text = st.get("prompt_text", "") if st else ""
        if not prompt_text:
            await send_tg_request("editMessageText", {
                "chat_id": chat_id, "message_id": message_id,
                "text": "❌ Время действия запроса истекло или текст пуст.",
                "reply_markup": {"inline_keyboard": [[{"text": "🔙 Главное меню", "callback_data": "nav:main"}]]}
            })
            return
        query = database.create_chat_query(user=username, prompt=prompt_text, task_ids="")
        await send_tg_request("editMessageText", {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": (
                f"💬 *Вопрос передан AI-ассистенту (Query #{query['id']})*\n\n"
                f"«_{prompt_text}_»\n\n"
                f"⏳ AI-агент на сервере `andrii` обрабатывает ваш запрос с учетом инфраструктуры кластера (home, vpn, andrii, dmitry).\n"
                f"Ответ поступит сюда сразу после генерации."
            ),
            "parse_mode": "Markdown",
            "reply_markup": {"inline_keyboard": [
                [{"text": "🏠 Главное меню", "callback_data": "nav:main"}]
            ]}
        })

    elif data == "prompt:create_task":
        st = user_states.pop(tg_id, None)
        prompt_text = st.get("prompt_text", "") if st else ""
        if not prompt_text:
            await send_tg_request("editMessageText", {
                "chat_id": chat_id, "message_id": message_id,
                "text": "❌ Время действия запроса истекло.",
                "reply_markup": {"inline_keyboard": [[{"text": "🔙 Главное меню", "callback_data": "nav:main"}]]}
            })
            return
        lines = prompt_text.split("\n", 1)
        title = lines[0].strip()
        desc = lines[1].strip() if len(lines) > 1 else ""
        task = database.create_task(title=title, description=desc, created_by=username, ai_enabled=0)
        await send_tg_request("editMessageText", {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": (
                f"✅ *Задача создана в колонке Open (без AI)!*\n\n"
                f"📌 *Task #{task['id']}:* {task['title']}\n"
                f"👤 Автор: `@{username}`\n"
            ),
            "parse_mode": "Markdown",
            "reply_markup": {"inline_keyboard": [
                [{"text": f"📋 Открыть задачу #{task['id']}", "callback_data": f"task:{task['id']}"}],
                [{"text": "🏠 Главное меню", "callback_data": "nav:main"}]
            ]}
        })

    elif data == "prompt:cancel":
        user_states.pop(tg_id, None)
        text = build_main_menu_text(username, tg_id)
        reply_markup = build_main_menu_keyboard(tg_id)
        if message_id:
            await send_tg_request("editMessageText", {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text,
                "parse_mode": "Markdown",
                "reply_markup": reply_markup
            })

    elif data == "settings:menu":
        if not is_admin_user(tg_id):
            await send_tg_request("answerCallbackQuery", {
                "callback_query_id": cq_id,
                "text": "⛔ Project settings can only be accessed by admin",
                "show_alert": True
            })
            return
        text = build_settings_menu_text()
        reply_markup = build_settings_menu_keyboard()
        if message_id:
            await send_tg_request("editMessageText", {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text,
                "parse_mode": "Markdown",
                "reply_markup": reply_markup
            })

    elif data == "settings:toggle_ai":
        if not is_admin_user(tg_id):
            await send_tg_request("answerCallbackQuery", {
                "callback_query_id": cq_id,
                "text": "⛔ Settings can only be edited by admin",
                "show_alert": True
            })
            return
        new_mode = database.toggle_ai_mode()
        status_word = "ENABLED" if new_mode else "DISABLED"
        await send_tg_request("answerCallbackQuery", {
            "callback_query_id": cq_id,
            "text": f"🤖 AI Worker Mode is now {status_word}"
        })
        text = build_settings_menu_text()
        reply_markup = build_settings_menu_keyboard()
        if message_id:
            await send_tg_request("editMessageText", {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text,
                "parse_mode": "Markdown",
                "reply_markup": reply_markup
            })

    elif data == "settings:users":
        if not is_admin_user(tg_id):
            await send_tg_request("answerCallbackQuery", {
                "callback_query_id": cq_id,
                "text": "⛔ Settings can only be edited by admin",
                "show_alert": True
            })
            return
        text = build_manage_users_text()
        reply_markup = build_manage_users_keyboard()
        if message_id:
            await send_tg_request("editMessageText", {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text,
                "parse_mode": "Markdown",
                "reply_markup": reply_markup
            })

    elif data == "settings:add_user_prompt":
        if not is_admin_user(tg_id):
            await send_tg_request("answerCallbackQuery", {
                "callback_query_id": cq_id,
                "text": "⛔ Settings can only be edited by admin",
                "show_alert": True
            })
            return
        user_states[tg_id] = {"state": "waiting_add_user", "msg_id": message_id}
        if message_id:
            await send_tg_request("editMessageText", {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": (
                    "➕ *Add New User to Project*\n\n"
                    "Send the username and Telegram ID in your next message.\n\n"
                    "Format: `<username> <telegram_id>`\n"
                    "Example: `misha 987654321`"
                ),
                "parse_mode": "Markdown",
                "reply_markup": {"inline_keyboard": [[{"text": "❌ Cancel", "callback_data": "settings:users"}]]}
            })

    elif data.startswith("settings:remove_user:"):
        if not is_admin_user(tg_id):
            await send_tg_request("answerCallbackQuery", {
                "callback_query_id": cq_id,
                "text": "⛔ Settings can only be edited by admin",
                "show_alert": True
            })
            return
        target_user = data.split(":")[2]
        admin_uname = config.TG_USER_MAP.get(config.ADMIN_TG_ID, "admin")
        if target_user == admin_uname or target_user == "nikita":
            await send_tg_request("answerCallbackQuery", {
                "callback_query_id": cq_id,
                "text": f"⛔ Cannot remove primary admin @{target_user}",
                "show_alert": True
            })
            return
        removed = database.remove_user(target_user)
        if removed:
            await send_tg_request("answerCallbackQuery", {
                "callback_query_id": cq_id,
                "text": f"✅ User @{target_user} removed."
            })
        else:
            await send_tg_request("answerCallbackQuery", {
                "callback_query_id": cq_id,
                "text": f"❌ Could not remove user @{target_user}."
            })
        text = build_manage_users_text()
        reply_markup = build_manage_users_keyboard()
        if message_id:
            await send_tg_request("editMessageText", {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text,
                "parse_mode": "Markdown",
                "reply_markup": reply_markup
            })

    elif data == "settings:alerts":
        if not is_admin_user(tg_id):
            await send_tg_request("answerCallbackQuery", {
                "callback_query_id": cq_id,
                "text": "⛔ Settings can only be edited by admin",
                "show_alert": True
            })
            return
        text = build_manage_alerts_text()
        reply_markup = build_manage_alerts_keyboard()
        if message_id:
            await send_tg_request("editMessageText", {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text,
                "parse_mode": "Markdown",
                "reply_markup": reply_markup
            })

    elif data == "settings:add_alert_prompt":
        if not is_admin_user(tg_id):
            await send_tg_request("answerCallbackQuery", {
                "callback_query_id": cq_id,
                "text": "⛔ Settings can only be edited by admin",
                "show_alert": True
            })
            return
        user_states[tg_id] = {"state": "waiting_add_alert", "msg_id": message_id}
        if message_id:
            await send_tg_request("editMessageText", {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": (
                    "🚨 *Add Farm Incident Alert Recipient*\n\n"
                    "Send the Telegram ID of the user who should receive automated farm incident alerts.\n\n"
                    "Example: `123456789`"
                ),
                "parse_mode": "Markdown",
                "reply_markup": {"inline_keyboard": [[{"text": "❌ Cancel", "callback_data": "settings:alerts"}]]}
            })

    elif data.startswith("settings:remove_alert:"):
        if not is_admin_user(tg_id):
            await send_tg_request("answerCallbackQuery", {
                "callback_query_id": cq_id,
                "text": "⛔ Settings can only be edited by admin",
                "show_alert": True
            })
            return
        try:
            target_id = int(data.split(":")[2])
        except Exception:
            target_id = 0
        removed = database.remove_alert_recipient(target_id)
        if removed:
            await send_tg_request("answerCallbackQuery", {
                "callback_query_id": cq_id,
                "text": f"✅ Recipient {target_id} removed from alerts."
            })
        else:
            await send_tg_request("answerCallbackQuery", {
                "callback_query_id": cq_id,
                "text": f"❌ Recipient {target_id} not found."
            })
        text = build_manage_alerts_text()
        reply_markup = build_manage_alerts_keyboard()
        if message_id:
            await send_tg_request("editMessageText", {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text,
                "parse_mode": "Markdown",
                "reply_markup": reply_markup
            })

async def handle_incoming_message(msg: dict):
    from_user = msg.get("from", {})
    tg_id = from_user.get("id")
    chat = msg.get("chat", {})
    chat_id = chat.get("id", tg_id)
    chat_type = chat.get("type", "private")
    user_msg_id = msg.get("message_id")
    raw_text = msg.get("text", "")
    caption = msg.get("caption", "")
    full_text = (raw_text or caption).strip()

    # Look up username from TG_USER_MAP or database
    username = config.TG_USER_MAP.get(tg_id)
    if not username:
        for u in database.get_all_users():
            if u["telegram_id"] == tg_id:
                username = u["username"]
                break

    # 1. Group / Supergroup message handling OR bot mention (@tasksboard67bot)
    bot_mention = f"@{BOT_USERNAME.lower()}"
    has_mention = bot_mention in full_text.lower()

    if chat_type in ["group", "supergroup"] or has_mention:
        if not has_mention:
            return  # In group chats, ignore messages that don't address the bot

        # Check if user is in our allowed user list
        if not username:
            logger.info(f"Group mention ignored from unauthorized user tg_id={tg_id}")
            return

        # User is authorized! Clean up the message (remove @botusername)
        clean_text = re.sub(rf"(?i)@{re.escape(BOT_USERNAME)}\b", "", full_text).strip()
        if not clean_text:
            await send_tg_request("sendMessage", {
                "chat_id": chat_id,
                "reply_to_message_id": user_msg_id,
                "text": f"ℹ️ Чтобы создать задачу, укажите ее описание:\n`@{BOT_USERNAME} сделать вот такую таску`",
                "parse_mode": "Markdown"
            })
            return

        lines = clean_text.split("\n", 1)
        title = lines[0].strip()
        desc = lines[1].strip() if len(lines) > 1 else ""
        if not title:
            title = "Untitled Task"

        # Save task directly into Open with ai_enabled=0 (without AI permission)
        task = database.create_task(title=title, description=desc, created_by=username, ai_enabled=0)
        
        reply_text = (
            f"📥 *Задача #{task['id']} добавлена в Open!*\n\n"
            f"📌 *{task['title']}*\n"
            f"👤 Автор: `@{username}`\n"
            f"🤖 AI: `❌ Без разрешения на нейронку`\n\n"
            f"_Задача создана в открытых тасках._"
        )
        await send_tg_request("sendMessage", {
            "chat_id": chat_id,
            "reply_to_message_id": user_msg_id,
            "text": reply_text,
            "parse_mode": "Markdown"
        })
        return

    # 2. Private Chat Handling (Direct interaction with the bot)
    if not username:
        await send_tg_request("sendMessage", {
            "chat_id": chat_id,
            "text": f"⛔ Access Denied. Your Telegram ID ({tg_id}) is not authorized to access this Kanban Board."
        })
        return

    state_info = user_states.get(tg_id)
    state = state_info.get("state") if state_info else None

    # Handle quick slash commands: delete user message and update persistent single bot message
    if full_text in ["/docs", "/api", "/help_api"]:
        user_states.pop(tg_id, None)
        await send_tg_request("deleteMessage", {"chat_id": chat_id, "message_id": user_msg_id})
        docs_text = build_api_docs_text("overview")
        reply_markup = build_api_docs_keyboard("overview")
        await update_or_send_main_message(chat_id, docs_text, reply_markup)
        return

    if full_text in ["/servers", "/cluster", "/hosts", "/infra", "/infrastructure"]:
        user_states.pop(tg_id, None)
        await send_tg_request("deleteMessage", {"chat_id": chat_id, "message_id": user_msg_id})
        servers_text = build_servers_text()
        reply_markup = build_servers_keyboard()
        await update_or_send_main_message(chat_id, servers_text, reply_markup)
        return

    if full_text in ["/start", "/menu"]:
        user_states.pop(tg_id, None)
        await send_tg_request("deleteMessage", {"chat_id": chat_id, "message_id": user_msg_id})
        menu_text = build_main_menu_text(username, tg_id)
        reply_markup = build_main_menu_keyboard(tg_id)
        await update_or_send_main_message(chat_id, menu_text, reply_markup)
        return

    # Active conversation states (rejection, comment, attachment, admin settings)
    if state == "waiting_rejection":
        task_id = state_info["task_id"]
        user_states.pop(tg_id, None)
        await send_tg_request("deleteMessage", {"chat_id": chat_id, "message_id": user_msg_id})
        ok, res_msg, updated_task = database.reject_task(task_id, username, full_text or "Rejected by reviewer")
        if ok and updated_task:
            task_text = build_task_detail_text(updated_task)
            reply_markup = build_task_keyboard(updated_task, username)
            await update_or_send_main_message(
                chat_id,
                f"⚠️ *Task #{task_id} rejected and returned to Open!*\nReason: _{full_text}_\n\n" + task_text,
                reply_markup
            )
        else:
            await update_or_send_main_message(
                chat_id,
                f"❌ Failed to reject task: {res_msg}",
                {"inline_keyboard": [[{"text": "🔙 Back", "callback_data": f"task:{task_id}"}]]}
            )
        return

    elif state == "waiting_comment":
        task_id = state_info["task_id"]
        user_states.pop(tg_id, None)
        await send_tg_request("deleteMessage", {"chat_id": chat_id, "message_id": user_msg_id})
        database.add_comment(task_id, username, full_text)
        updated_task = database.get_task(task_id)
        task_text = build_task_detail_text(updated_task)
        reply_markup = build_task_keyboard(updated_task, username)
        await update_or_send_main_message(
            chat_id,
            f"💬 *Comment added to Task #{task_id}!*\n\n" + task_text,
            reply_markup
        )
        return

    elif state == "waiting_attachment":
        task_id = state_info["task_id"]
        user_states.pop(tg_id, None)
        await send_tg_request("deleteMessage", {"chat_id": chat_id, "message_id": user_msg_id})
        file_id = None
        orig_name = "attachment"
        mime = "application/octet-stream"
        
        if "photo" in msg:
            photo = msg["photo"][-1]
            file_id = photo["file_id"]
            orig_name = f"photo_{int(asyncio.get_event_loop().time())}.jpg"
            mime = "image/jpeg"
        elif "document" in msg:
            doc = msg["document"]
            file_id = doc["file_id"]
            orig_name = doc.get("file_name", "document")
            mime = doc.get("mime_type", "application/octet-stream")

        if file_id:
            file_info = await send_tg_request("getFile", {"file_id": file_id})
            if file_info and file_info.get("ok"):
                file_path = file_info["result"]["file_path"]
                download_url = f"https://api.telegram.org/file/bot{config.BOT_TOKEN}/{file_path}"
                stored_name = f"{secrets.token_hex(16)}_{orig_name}"
                dest = config.UPLOAD_DIR / stored_name
                async with httpx.AsyncClient() as client:
                    file_res = await client.get(download_url)
                    with open(dest, "wb") as f:
                        f.write(file_res.content)
                size = os.path.getsize(dest)
                database.add_attachment(task_id, username, orig_name, stored_name, size, mime)
                updated_task = database.get_task(task_id)
                task_text = build_task_detail_text(updated_task)
                reply_markup = build_task_keyboard(updated_task, username)
                await update_or_send_main_message(
                    chat_id,
                    f"📎 *File attached successfully!*\n\n" + task_text,
                    reply_markup
                )
                return

        await update_or_send_main_message(
            chat_id,
            "❌ No photo or document received. Attachment cancelled.",
            {"inline_keyboard": [[{"text": "🔙 Back", "callback_data": f"task:{task_id}"}]]}
        )
        return

    elif state == "waiting_add_user":
        user_states.pop(tg_id, None)
        await send_tg_request("deleteMessage", {"chat_id": chat_id, "message_id": user_msg_id})
        parts = full_text.split()
        if len(parts) >= 2 and parts[1].isdigit():
            new_u = parts[0].strip().lstrip("@").lower()
            new_id = int(parts[1])
            success = database.add_user(new_u, new_id)
            if success:
                res_msg = f"✅ User `@{new_u}` (ID: `{new_id}`) added successfully!"
            else:
                res_msg = f"❌ Failed to add user `@{new_u}`."
        else:
            res_msg = "❌ Invalid format. Please use: `username telegram_id`\nExample: `misha 987654321`"

        text_out = res_msg + "\n\n" + build_manage_users_text()
        reply_markup = build_manage_users_keyboard()
        await update_or_send_main_message(chat_id, text_out, reply_markup)
        return

    elif state == "waiting_add_alert":
        user_states.pop(tg_id, None)
        await send_tg_request("deleteMessage", {"chat_id": chat_id, "message_id": user_msg_id})
        raw_val = full_text.strip()
        if raw_val.isdigit():
            new_alert_id = int(raw_val)
            added = database.add_alert_recipient(new_alert_id)
            if added:
                uname = config.TG_USER_MAP.get(new_alert_id, "")
                u_text = f" (@{uname})" if uname else ""
                res_msg = f"✅ Telegram ID `{new_alert_id}`{u_text} added to Incident Alert Recipients!"
            else:
                res_msg = f"ℹ️ Telegram ID `{new_alert_id}` is already in the alert recipients list."
        else:
            res_msg = "❌ Invalid format. Please send a numeric Telegram ID (e.g. `123456789`)."

        text_out = res_msg + "\n\n" + build_manage_alerts_text()
        reply_markup = build_manage_alerts_keyboard()
        await update_or_send_main_message(chat_id, text_out, reply_markup)
        return

    # 3. Default Direct Message in Private Chat (no active state OR waiting_task_create)
    # Directly creates a task for the AI agent (ai_enabled=1), deletes the user's message,
    # and updates the bot's single persistent message displaying the saved task with a Back button.
    user_states.pop(tg_id, None)
    await send_tg_request("deleteMessage", {"chat_id": chat_id, "message_id": user_msg_id})

    if not full_text:
        return

    lines = full_text.split("\n", 1)
    title = lines[0].strip()
    desc = lines[1].strip() if len(lines) > 1 else ""
    if not title:
        title = "Untitled Task"

    # Create task with AI enabled
    task = database.create_task(title=title, description=desc, created_by=username, ai_enabled=1)
    task_id = task["id"]

    # Check if photo or document was attached
    file_id = None
    orig_name = "attachment"
    mime = "application/octet-stream"
    if "photo" in msg:
        photo = msg["photo"][-1]
        file_id = photo["file_id"]
        orig_name = f"task_{task_id}_photo.jpg"
        mime = "image/jpeg"
    elif "document" in msg:
        doc = msg["document"]
        file_id = doc["file_id"]
        orig_name = doc.get("file_name", "document")
        mime = doc.get("mime_type", "application/octet-stream")

    if file_id:
        file_info = await send_tg_request("getFile", {"file_id": file_id})
        if file_info and file_info.get("ok"):
            file_path = file_info["result"]["file_path"]
            download_url = f"https://api.telegram.org/file/bot{config.BOT_TOKEN}/{file_path}"
            stored_name = f"{secrets.token_hex(16)}_{orig_name}"
            dest = config.UPLOAD_DIR / stored_name
            async with httpx.AsyncClient() as client:
                file_res = await client.get(download_url)
                with open(dest, "wb") as f:
                    f.write(file_res.content)
            size = os.path.getsize(dest)
            database.add_attachment(task_id, username, orig_name, stored_name, size, mime)

    saved_text = (
        f"🤖 *Задача сохранена для AI-агента!*\n\n"
        f"📌 *Task #{task['id']}:* {task['title']}\n"
        f"👤 Автор: `@{username}`\n"
        f"⚙️ Статус: `{task['status']}` | 🤖 AI: `✅ Разрешен`\n\n"
        f"_AI-агент на сервере `andrii` подхватит выполнение задачи._"
    )
    saved_kb = {
        "inline_keyboard": [
            [{"text": f"📋 Открыть задачу #{task['id']}", "callback_data": f"task:{task['id']}"}],
            [{"text": "🔙 Назад", "callback_data": "nav:main"}]
        ]
    }
    await update_or_send_main_message(chat_id, saved_text, saved_kb)
    return

async def handle_guest_query(update: dict):
    """
    Handle Telegram Guest Chat Mode queries (Bot API 10.0+).
    When the bot is mentioned or summoned via guest mode.
    """
    guest_msg = update.get("guest_message") or update.get("message", {})
    guest_query_id = guest_msg.get("guest_query_id") or update.get("guest_query_id")
    if not guest_query_id:
        return

    caller_user = guest_msg.get("guest_bot_caller_user") or guest_msg.get("from", {})
    tg_id = caller_user.get("id")

    stats = database.get_stats()
    if tg_id in config.TG_USER_MAP:
        username = config.TG_USER_MAP[tg_id]
        summary_text = (
            f"⚡ *Kanban Board Status (Guest Mode)*\n"
            f"Caller: @{username}\n"
            f"• 📥 Open: {stats[config.STATUS_OPEN]}\n"
            f"• ⏳ In Progress: {stats[config.STATUS_IN_PROGRESS]}\n"
            f"• 🔍 Under Review: {stats[config.STATUS_REVIEW]}\n"
            f"• ✅ Completed: {stats[config.STATUS_COMPLETED]}\n\n"
            f"Open private chat with @tasksboard67bot to interact with the board."
        )
    else:
        summary_text = f"⛔ Kanban Board: Caller ID {tg_id} is not authorized."

    result_payload = {
        "guest_query_id": guest_query_id,
        "result": {
            "type": "article",
            "id": secrets.token_hex(8),
            "title": "Kanban Tasks Overview",
            "input_message_content": {
                "message_text": summary_text,
                "parse_mode": "Markdown"
            }
        }
    }
    await send_tg_request("answerGuestQuery", result_payload)

async def send_incident_alert_to_recipients(title: str, report_text: str, resolved: bool = False, report_filename: Optional[str] = None) -> list:
    import datetime
    recipients = database.get_alert_recipients()
    status_emoji = "✅ [RESOLVED]" if resolved else "🚨 [ALERT: METRIC DROP DETECTED]"
    now_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    header = (
        f"{status_emoji}\n"
        f"🤖 *Pixabay Farm Autonomous Incident Report*\n"
        f"⏰ *Time:* `{now_str}`\n"
        f"📌 *Issue:* *{title}*\n"
        f"────────────────────────\n\n"
    )
    full_text = header + report_text.strip()
    if len(full_text) > 4000:
        full_text = full_text[:3950] + "\n\n...(report truncated)"

    sent = []
    report_bytes = report_text.encode("utf-8")
    doc_filename = report_filename or f"incident_report_{datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d_%H%M%S')}.md"
    caption = f"{status_emoji} 🤖 Отчет инцидента: {title[:120]}"

    for r_id in recipients:
        res = await send_tg_request("sendMessage", {
            "chat_id": r_id,
            "text": full_text,
            "parse_mode": "Markdown"
        })
        if not (res and res.get("ok")):
            # Fallback to plain text if markdown formatting has unmatched tags
            try:
                plain_body = f"{status_emoji}\nPixabay Farm Autonomous Incident Report\nTime: {now_str}\nIssue: {title}\n------------------------\n\n{report_text.strip()[:3800]}"
                await send_tg_request("sendMessage", {
                    "chat_id": r_id,
                    "text": plain_body
                })
            except Exception as e:
                logger.error(f"Failed to send incident alert to {r_id}: {e}")

        # Also send full markdown file directly to the recipient
        try:
            doc_res = await send_tg_document(r_id, doc_filename, report_bytes, caption, mime_type="text/markdown")
            if doc_res and doc_res.get("ok") and r_id not in sent:
                sent.append(r_id)
        except Exception as e:
            logger.error(f"Failed to send incident report document to {r_id}: {e}")

        if r_id not in sent and res and res.get("ok"):
            sent.append(r_id)
    return sent

async def send_task_report_document(username: str, filename: str, report_content: str, caption: str = "") -> bool:
    """
    Sends an autonomous agent task report (.md document) directly to the Telegram user.
    """
    tg_id = config.USERNAME_TO_TG.get(username)
    if not tg_id:
        users = database.get_all_users()
        for u in users:
            if u["username"] == username:
                tg_id = u["telegram_id"]
                break
    if not tg_id:
        tg_id = config.ADMIN_TG_ID
    if not tg_id:
        logger.warning(f"Could not find Telegram ID for user: {username}")
        return False

    content_bytes = report_content.encode("utf-8")
    res = await send_tg_document(tg_id, filename, content_bytes, caption, mime_type="text/markdown")
    return bool(res and res.get("ok"))

async def send_ai_chat_response_to_user(username: str, response_text: str) -> bool:
    """
    Sends the AI Assistant response back to the Telegram user who submitted the prompt.
    """
    tg_id = config.USERNAME_TO_TG.get(username)
    if not tg_id:
        users = database.get_all_users()
        for u in users:
            if u["username"] == username:
                tg_id = u["telegram_id"]
                break
    if not tg_id:
        logger.warning(f"Could not find Telegram ID for username: {username}")
        return False
    
    header = "🤖 *Ответ AI-ассистента:*\n────────────────────────\n"
    footer = "\n────────────────────────"
    full_text = header + response_text.strip() + footer
    
    # Send message with fallback to plain text if Markdown parsing fails
    res = await send_tg_request("sendMessage", {
        "chat_id": tg_id,
        "text": full_text[:4000],
        "parse_mode": "Markdown",
        "reply_markup": {"inline_keyboard": [
            [{"text": "🏠 Главное меню", "callback_data": "nav:main"}]
        ]}
    })
    if not res or not res.get("ok"):
        plain_text = f"🤖 Ответ AI-ассистента:\n------------------------\n{response_text.strip()[:3800]}\n------------------------"
        res2 = await send_tg_request("sendMessage", {
            "chat_id": tg_id,
            "text": plain_text,
            "reply_markup": {"inline_keyboard": [
                [{"text": "🏠 Главное меню", "callback_data": "nav:main"}]
            ]}
        })
        return bool(res2 and res2.get("ok"))
    return True

async def start_telegram_bot_poller():
    """
    Long-polling loop for Telegram Bot updates.
    Handles private chats, inline callback queries, and guest queries.
    """
    logger.info("Starting Telegram Bot poller...")
    offset = 0
    # Make sure webhook is removed so getUpdates works cleanly
    await send_tg_request("deleteWebhook", {"drop_pending_updates": False})

    global BOT_USERNAME
    me = await send_tg_request("getMe", {})
    if me and me.get("ok"):
        BOT_USERNAME = me["result"].get("username", BOT_USERNAME)
        logger.info(f"Bot initialized as @{BOT_USERNAME}")

    while True:
        try:
            url = f"{TELEGRAM_API_BASE}/getUpdates"
            payload = {
                "offset": offset,
                "timeout": 30,
                "allowed_updates": ["message", "callback_query", "guest_message"]
            }
            async with httpx.AsyncClient(timeout=45.0) as client:
                resp = await client.post(url, json=payload)
                data = resp.json()
                if not data.get("ok"):
                    logger.warning(f"getUpdates error: {data}")
                    await asyncio.sleep(3)
                    continue

                updates = data.get("result", [])
                for u in updates:
                    offset = u["update_id"] + 1

                    # 1. Check Guest Query / Guest Message
                    if "guest_query_id" in u or ("message" in u and "guest_query_id" in u["message"]) or "guest_message" in u:
                        await handle_guest_query(u)
                        continue

                    # 2. Check Callback Query (Button clicks)
                    if "callback_query" in u:
                        await handle_callback_query(u["callback_query"])
                        continue

                    # 3. Check Normal Message
                    if "message" in u:
                        await handle_incoming_message(u["message"])
                        continue

        except asyncio.CancelledError:
            logger.info("Telegram Bot poller cancelled.")
            break
        except Exception as e:
            logger.error(f"Polling loop exception: {e}")
            await asyncio.sleep(4)
