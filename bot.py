import asyncio
import logging
import httpx
import os
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
            {"text": "🔑 API Keys", "callback_data": "action:api_keys"}
        ],
        [
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
    if database.is_ai_mode():
        ai_stat = "✅ Allowed (Autonomous Agent)" if task.get("ai_enabled", 0) else "❌ Disabled"
        text += f"🤖 *AI Agent:* {ai_stat}\n"

    if task.get("assignee"):
        text += f"Taken by: `@{task['assignee']}`\n"
    if task.get("reviewed_by"):
        text += f"Verified by: `@{task['reviewed_by']}` on `{task.get('completed_at', '')[:16].replace('T', ' ')}`\n"
    
    if task.get("rejection_comment") and task["status"] == config.STATUS_OPEN:
        text += f"⚠️ *Rejection Reason:* _{task['rejection_comment']}_\n"

    text += f"────────────────────────\n*Description:*\n"
    desc = task.get("description") or "_No description provided._"
    text += f"{desc}\n────────────────────────\n"

    # Attachments
    attachments = task.get("attachments", [])
    if attachments:
        text += f"📎 *Attachments ({len(attachments)}):*\n"
        for a in attachments:
            size_kb = max(1, a["file_size"] // 1024)
            text += f"• `{a['original_filename']}` ({size_kb} KB)\n"
        text += "────────────────────────\n"

    # Recent Comments
    comments = task.get("comments", [])
    if comments:
        text += f"💬 *Recent Activity / Comments:*\n"
        for c in comments[-3:]:
            prefix = "⚠️ " if c["comment_type"] == "rejection" else "• "
            text += f"{prefix}*@{c['author']}*: {c['content']}\n"
        text += "────────────────────────\n"

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

    if database.is_ai_mode() and status == config.STATUS_OPEN:
        ai_on = bool(task.get("ai_enabled", 0))
        btn_text = "🤖 AI: ✅ Enabled (Tap to Disable)" if ai_on else "🤖 AI: ❌ Disabled (Tap to Enable)"
        buttons.append([{"text": btn_text, "callback_data": f"act:ai_toggle:{task_id}"}])

    buttons.append([
        {"text": "💬 Add Comment", "callback_data": f"act:comment_prompt:{task_id}"},
        {"text": "📎 Attach File", "callback_data": f"act:attach_prompt:{task_id}"}
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

    # Acknowledge callback immediately
    await send_tg_request("answerCallbackQuery", {"callback_query_id": cq_id})

    # Clear pending state if navigation occurs
    if data.startswith("nav:") or data.startswith("col:") or data.startswith("task:") or data.startswith("docs:") or data == "action:api_docs":
        user_states.pop(tg_id, None)

    # Route navigation
    if data == "nav:main":
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

    elif data.startswith("col:"):
        parts = data.split(":")
        status_key = parts[1]
        page = int(parts[2]) if len(parts) > 2 else 0
        tasks = database.list_tasks(status=status_key)
        text = build_column_text(status_key, tasks, page)
        reply_markup = build_column_keyboard(status_key, tasks, page)
        if message_id:
            await send_tg_request("editMessageText", {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text,
                "parse_mode": "Markdown",
                "reply_markup": reply_markup
            })

    elif data.startswith("task:"):
        task_id = int(data.split(":")[1])
        task = database.get_task(task_id)
        if not task:
            if message_id:
                await send_tg_request("editMessageText", {
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "text": f"❌ Task #{task_id} not found.",
                    "reply_markup": {"inline_keyboard": [[{"text": "🔙 Main Menu", "callback_data": "nav:main"}]]}
                })
            return
        text = build_task_detail_text(task)
        reply_markup = build_task_keyboard(task, username)
        if message_id:
            await send_tg_request("editMessageText", {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text,
                "parse_mode": "Markdown",
                "reply_markup": reply_markup
            })

    elif data.startswith("act:claim:"):
        task_id = int(data.split(":")[2])
        ok, msg, updated_task = database.claim_task(task_id, username)
        if ok and updated_task:
            text = build_task_detail_text(updated_task)
            reply_markup = build_task_keyboard(updated_task, username)
            await send_tg_request("editMessageText", {
                "chat_id": chat_id, "message_id": message_id,
                "text": f"✅ *{msg}*\n\n" + text, "parse_mode": "Markdown", "reply_markup": reply_markup
            })
        else:
            await send_tg_request("answerCallbackQuery", {"callback_query_id": cq_id, "text": f"❌ {msg}", "show_alert": True})

    elif data.startswith("act:release:"):
        task_id = int(data.split(":")[2])
        ok, msg, updated_task = database.release_task(task_id, username)
        if ok and updated_task:
            text = build_task_detail_text(updated_task)
            reply_markup = build_task_keyboard(updated_task, username)
            await send_tg_request("editMessageText", {
                "chat_id": chat_id, "message_id": message_id,
                "text": f"ℹ️ *{msg}*\n\n" + text, "parse_mode": "Markdown", "reply_markup": reply_markup
            })
        else:
            await send_tg_request("answerCallbackQuery", {"callback_query_id": cq_id, "text": f"❌ {msg}", "show_alert": True})

    elif data.startswith("act:submit:"):
        task_id = int(data.split(":")[2])
        ok, msg, updated_task = database.submit_task_for_review(task_id, username)
        if ok and updated_task:
            text = build_task_detail_text(updated_task)
            reply_markup = build_task_keyboard(updated_task, username)
            await send_tg_request("editMessageText", {
                "chat_id": chat_id, "message_id": message_id,
                "text": f"🚀 *{msg}*\n\n" + text, "parse_mode": "Markdown", "reply_markup": reply_markup
            })
        else:
            await send_tg_request("answerCallbackQuery", {"callback_query_id": cq_id, "text": f"❌ {msg}", "show_alert": True})

    elif data.startswith("act:approve:"):
        task_id = int(data.split(":")[2])
        ok, msg, updated_task = database.approve_task(task_id, username)
        if ok and updated_task:
            text = build_task_detail_text(updated_task)
            reply_markup = build_task_keyboard(updated_task, username)
            await send_tg_request("editMessageText", {
                "chat_id": chat_id, "message_id": message_id,
                "text": f"🎉 *{msg}*\n\n" + text, "parse_mode": "Markdown", "reply_markup": reply_markup
            })
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
    chat_id = msg.get("chat", {}).get("id", tg_id)
    text = msg.get("text", "").strip()

    # Check authorization
    if tg_id not in config.TG_USER_MAP:
        await send_tg_request("sendMessage", {
            "chat_id": chat_id,
            "text": f"⛔ Access Denied. Your Telegram ID ({tg_id}) is not authorized to access this Kanban Board."
        })
        return

    username = config.TG_USER_MAP[tg_id]
    state_info = user_states.get(tg_id)

    # API Documentation quick commands
    if text in ["/docs", "/api", "/help_api"]:
        user_states.pop(tg_id, None)
        docs_text = build_api_docs_text("overview")
        reply_markup = build_api_docs_keyboard("overview")
        await send_tg_request("sendMessage", {
            "chat_id": chat_id,
            "text": docs_text,
            "parse_mode": "Markdown",
            "reply_markup": reply_markup
        })
        return

    # If user sent /start or /menu or has no active state
    if text in ["/start", "/menu"] or not state_info:
        user_states.pop(tg_id, None)
        menu_text = build_main_menu_text(username, tg_id)
        reply_markup = build_main_menu_keyboard(tg_id)
        await send_tg_request("sendMessage", {
            "chat_id": chat_id,
            "text": menu_text,
            "parse_mode": "Markdown",
            "reply_markup": reply_markup
        })
        return

    # Handle active states
    state = state_info.get("state")

    if state == "waiting_rejection":
        task_id = state_info["task_id"]
        user_states.pop(tg_id, None)
        ok, res_msg, updated_task = database.reject_task(task_id, username, text or "Rejected by reviewer")
        if ok and updated_task:
            task_text = build_task_detail_text(updated_task)
            reply_markup = build_task_keyboard(updated_task, username)
            await send_tg_request("sendMessage", {
                "chat_id": chat_id,
                "text": f"⚠️ *Task #{task_id} rejected and returned to Open!*\nReason: _{text}_\n\n" + task_text,
                "parse_mode": "Markdown",
                "reply_markup": reply_markup
            })
        else:
            await send_tg_request("sendMessage", {
                "chat_id": chat_id,
                "text": f"❌ Failed to reject task: {res_msg}",
                "reply_markup": {"inline_keyboard": [[{"text": "🔙 Back", "callback_data": f"task:{task_id}"}]]}
            })

    elif state == "waiting_comment":
        task_id = state_info["task_id"]
        user_states.pop(tg_id, None)
        database.add_comment(task_id, username, text)
        updated_task = database.get_task(task_id)
        task_text = build_task_detail_text(updated_task)
        reply_markup = build_task_keyboard(updated_task, username)
        await send_tg_request("sendMessage", {
            "chat_id": chat_id,
            "text": f"💬 *Comment added to Task #{task_id}!*\n\n" + task_text,
            "parse_mode": "Markdown",
            "reply_markup": reply_markup
        })

    elif state == "waiting_attachment":
        task_id = state_info["task_id"]
        user_states.pop(tg_id, None)
        # Check if message contains photo or document
        file_id = None
        orig_name = "attachment"
        mime = "application/octet-stream"
        
        if "photo" in msg:
            # Get largest photo
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
            # Download file from Telegram Bot API
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
                await send_tg_request("sendMessage", {
                    "chat_id": chat_id,
                    "text": f"📎 *File attached successfully!*\n\n" + task_text,
                    "parse_mode": "Markdown",
                    "reply_markup": reply_markup
                })
                return

        await send_tg_request("sendMessage", {
            "chat_id": chat_id,
            "text": "❌ No photo or document received. Attachment cancelled.",
            "reply_markup": {"inline_keyboard": [[{"text": "🔙 Back", "callback_data": f"task:{task_id}"}]]}
        })

    elif state == "waiting_task_create":
        user_states.pop(tg_id, None)
        title = ""
        description = ""
        caption = msg.get("caption", "").strip()
        
        # Check text or photo/document
        if text:
            lines = text.split("\n", 1)
            title = lines[0].strip()
            if len(lines) > 1:
                description = lines[1].strip()
        elif caption:
            lines = caption.split("\n", 1)
            title = lines[0].strip()
            if len(lines) > 1:
                description = lines[1].strip()
        else:
            title = "Untitled Task"

        try:
            new_task = database.create_task(title, description, username)
            task_id = new_task["id"]

            # Check if attachment included with photo/doc
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
                    new_task = database.get_task(task_id)

            task_text = build_task_detail_text(new_task)
            reply_markup = build_task_keyboard(new_task, username)
            await send_tg_request("sendMessage", {
                "chat_id": chat_id,
                "text": f"🎉 *Task #{task_id} Created Successfully!*\n\n" + task_text,
                "parse_mode": "Markdown",
                "reply_markup": reply_markup
            })
        except Exception as e:
            logger.error(f"Error creating task: {e}")
            await send_tg_request("sendMessage", {
                "chat_id": chat_id,
                "text": f"❌ Failed to create task: {e}",
                "reply_markup": {"inline_keyboard": [[{"text": "🔙 Main Menu", "callback_data": "nav:main"}]]}
            })

    elif state == "waiting_add_user":
        user_states.pop(tg_id, None)
        parts = text.split()
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
        await send_tg_request("sendMessage", {
            "chat_id": chat_id,
            "text": text_out,
            "parse_mode": "Markdown",
            "reply_markup": reply_markup
        })

    elif state == "waiting_add_alert":
        user_states.pop(tg_id, None)
        raw_val = text.strip()
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
        await send_tg_request("sendMessage", {
            "chat_id": chat_id,
            "text": text_out,
            "parse_mode": "Markdown",
            "reply_markup": reply_markup
        })

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

async def send_incident_alert_to_recipients(title: str, report_text: str, resolved: bool = False) -> list:
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
    for r_id in recipients:
        res = await send_tg_request("sendMessage", {
            "chat_id": r_id,
            "text": full_text,
            "parse_mode": "Markdown"
        })
        if res and res.get("ok"):
            sent.append(r_id)
        else:
            # Fallback to plain text if markdown formatting has unmatched tags
            try:
                plain_body = f"{status_emoji}\nPixabay Farm Autonomous Incident Report\nTime: {now_str}\nIssue: {title}\n------------------------\n\n{report_text.strip()[:3800]}"
                res2 = await send_tg_request("sendMessage", {
                    "chat_id": r_id,
                    "text": plain_body
                })
                if res2 and res2.get("ok"):
                    sent.append(r_id)
            except Exception as e:
                logger.error(f"Failed to send incident alert to {r_id}: {e}")
    return sent

async def start_telegram_bot_poller():
    """
    Long-polling loop for Telegram Bot updates.
    Handles private chats, inline callback queries, and guest queries.
    """
    logger.info("Starting Telegram Bot poller...")
    offset = 0
    # Make sure webhook is removed so getUpdates works cleanly
    await send_tg_request("deleteWebhook", {"drop_pending_updates": False})

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
