# ⚡ TaskBoard API Documentation

TaskBoard is a secure, high-concurrency Kanban board system featuring a web interface, interactive Telegram Bot with in-place keyboard updates and Guest Chat Mode support, and a complete REST API.

---

## 1. Authentication & Security

### User Accounts & Telegram ID Mapping
The board supports authorized user mapping:
- `dmitry` (Telegram ID: `123456789`)
- `nikita` (Telegram ID: `987654321`)
- `andrii` (Telegram ID: `112233445`)

### Web Authentication
- **Site Password:** Configured via `WEB_PASSWORD` in `.env` (protects against unauthorized web visitors).
- **Session Persistence:** Authenticated visitors receive an HTTP-only session cookie valid for 30 days.
- **Account Selection:** On the web, users select their active profile (`dmitry`, `andrii`, or `nikita`) and optional nickname. The selected profile is remembered via cookies and can be switched dynamically in the header without re-entering the password.
- **Telegram Web Login:** If supported by the browser and domain, users can log in directly using the official Telegram Login Widget, automatically authenticating their mapped user account.

### API Key Authentication
All external REST API requests authenticate using an API key via either header:
```http
X-API-Key: kb_<username>_<random_token>
```
or
```http
Authorization: Bearer kb_<username>_<random_token>
```

#### How to Issue API Keys
1. **Via Website:** Open the board, click the **🔑 API Keys** button in the header, input a label (e.g. `CI/CD Deployer`), and click **Generate Key**.
2. **Via Telegram Bot:** In the bot, click **🔑 API Keys** -> **➕ Generate New API Key**.
3. **Via API:** Call `POST /api/v1/keys` with `{"name": "My Key"}`.

### Attack Resistance & Hardening
- **Sliding-Window Rate Limiting:**
  - `/api/v1/auth/login`: Maximum 5 attempts per minute per IP to prevent brute-force attacks.
  - General API endpoints: Maximum 120 requests per minute per IP.
- **Constant-Time Verification:** Password and API token hashes are compared using `hmac.compare_digest` to prevent timing attacks.
- **File Upload Protection:**
  - Files are sanitized to strip directory traversal sequences (`../`, `/`).
  - Stored under unique random hex IDs.
  - Maximum upload size is strictly enforced at 25 MB.
- **Security Headers:** Every response includes `X-Content-Type-Options: nosniff`, `X-Frame-Options: SAMEORIGIN`, and strict CORS policies.
- **Storage:** Database uses SQLite with Write-Ahead Logging (`WAL`) mode for crash resilience and concurrency.

---

## 2. API Endpoints Reference

Base URL:
- HTTPS: `https://your-domain.com:8445`
- HTTP: `http://your-domain.com:8090`
- Interactive Swagger: `/docs`
- ReDoc: `/redoc`

### Authentication Endpoints

#### `POST /api/v1/auth/login`
Logs in to the web interface.
**Request Body:**
```json
{
  "password": "your_secure_password",
  "user": "dmitry",
  "nickname": "Dmitry Dev"
}
```
**Response (200 OK):**
```json
{
  "ok": true,
  "message": "Login successful",
  "user": "dmitry",
  "nickname": "Dmitry Dev"
}
```

#### `GET /api/v1/auth/me`
Retrieves information about the current authenticated user.
**Response (200 OK):**
```json
{
  "authenticated": true,
  "username": "dmitry",
  "nickname": "Dmitry Dev",
  "auth_type": "api_key",
  "allowed_users": ["dmitry", "andrii", "nikita"]
}
```

---

### Task Management Endpoints

The system operates across 4 distinct workflow states:
1. `open` - Open tasks available for pickup.
2. `in_progress` - Claimed tasks being worked on. Shows `assignee`.
3. `review` - Tasks submitted for verification. Shows `assignee`.
4. `completed` - Verified and approved tasks. Shows `assignee` AND `reviewed_by`.

#### `GET /api/v1/stats`
Returns counts for each Kanban column.
**Response:**
```json
{
  "open": 3,
  "in_progress": 1,
  "review": 1,
  "completed": 5,
  "total": 10
}
```

#### `GET /api/v1/tasks`
Lists tasks with optional filtering.
- Query Parameter `status` (optional): `open`, `in_progress`, `review`, or `completed`.
- Query Parameter `assignee` (optional): `dmitry`, `andrii`, or `nikita`.

#### `POST /api/v1/tasks`
Creates a new task in `open` state.
**Request Body:**
```json
{
  "title": "Build secure webhook receiver",
  "description": "Handle incoming events with signature verification",
  "created_by": "ai agent",
  "ai_enabled": false
}
```
*(Note: `created_by` defaults to the authenticated user if omitted; agents can set `"ai agent"`).*

#### `GET /api/v1/tasks/{id}`
Returns full details of a task, including attachment metadata and audit comments.

#### `DELETE /api/v1/tasks/{id}`
Permanently deletes a task, its comments, attachments, and disk files.
```bash
curl -X DELETE https://your-domain.com:8445/api/v1/tasks/1 \
  -H "X-API-Key: YOUR_KEY"
```

#### `POST /api/v1/tasks/{id}/claim`
Claims an open task. Sets status to `in_progress` and assigns it to the authenticated caller or specified `assignee`.
**Optional Body:**
```json
{
  "assignee": "ai agent"
}
```

#### `POST /api/v1/tasks/{id}/release`
Releases an in-progress task back to `open`.
**Optional Body:**
```json
{
  "username": "ai agent"
}
```

#### `POST /api/v1/tasks/{id}/submit-review`
Submits an in-progress task for verification. Sets status to `review`.

#### `POST /api/v1/tasks/{id}/approve`
Approves a task in `review`. Sets status to `completed` and records the caller as `reviewed_by`.

#### `POST /api/v1/tasks/{id}/reject`
Rejects a task in `review`. Moves it back to `open` and records the required rejection explanation.
**Request Body:**
```json
{
  "comment": "Missing unit tests and integration tests"
}
```

#### `POST /api/v1/tasks/{id}/move`
Moves a task to a different column status and/or updates its vertical ordering position within the column (Kanban Drag & Drop). When moving directly to `completed`, `reviewed_by` can be specified.
**Request Body:**
```json
{
  "status": "completed",
  "position": 0,
  "reviewed_by": "ai agent"
}
```

#### `POST /api/v1/tasks/{id}/comments`
Adds a comment or audit note to a task.
**Request Body:**
```json
{
  "content": "Deployment completed to staging environment."
}
```

---

### Attachments Endpoints

#### `POST /api/v1/tasks/{id}/attachments`
Uploads an image or file attachment (multipart/form-data). Maximum file size: 25 MB.
```bash
curl -X POST https://your-domain.com:8445/api/v1/tasks/1/attachments \
  -H "X-API-Key: YOUR_KEY" \
  -F "file=@screenshot.png"
```

#### `GET /api/v1/attachments/{attachment_id}`
Downloads or displays the uploaded file.

---

### API Key Management Endpoints

#### `GET /api/v1/keys`
Lists all active API keys issued for the authenticated user.

#### `POST /api/v1/keys`
Generates a new API key.
**Request Body:**
```json
{
  "name": "Production Server Bot"
}
```
**Response:**
```json
{
  "ok": true,
  "api_key": "kb_dmitry_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "info": {
    "id": "a1b2c3d4",
    "username": "dmitry",
    "key_prefix": "kb_dmitry_xxxx...xxxx",
    "name": "Production Server Bot"
  }
}
```

#### `DELETE /api/v1/keys/{key_id}`
Revokes an API key.

---

## 3. Autonomous AI Agent & Remote Worker (pixabay_farm)

When **AI Mode** is enabled, an autonomous AI agent daemon (`pixabay_ai_worker.py`) deployed on server **andrii** connects via HTTPS to execute tasks on `/home/neanod/source/music/pixabay_farm`.

### Model Configuration
- **Model:** `gemini-3.8-flash`
- **Effort:** `medium` (`--model gemini-3.8-flash-medium`)
- **CLI Engine:** `antigravity-cli` (`agy`)

### Behavior & Guardrails
1. **Task Polling & Claiming:** Agent polls `GET /api/v1/ai/agent/pending-work`. When an open task with `ai_enabled = 1` is detected, it claims the task (`open` -> `in_progress`).
2. **Autonomous Execution:** Runs non-interactively in `/home/neanod/source/music/pixabay_farm` with `--dangerously-skip-permissions`.
3. **Mandatory Testing:** The agent executes project test suites and validates functionality. System service or server reboots are expected in this environment and are **not** treated as errors.
4. **On Success:** Posts test results as a comment and moves the task to verification (`POST /api/v1/tasks/{id}/submit-review`).
5. **On Failure:** If a task cannot be solved, the agent:
   - Generates a detailed markdown report (`task_{id}_failure_report.md`).
   - Uploads the `.md` file as a task attachment (`POST /api/v1/tasks/{id}/attachments`).
   - Adds a failure summary comment.
   - Releases the task back to `open` (`POST /api/v1/tasks/{id}/release`).

### AI Endpoints Reference

#### `GET /api/v1/settings/public`
Returns public system configuration (including `ai_mode`).
**Response:**
```json
{
  "ai_mode": true
}
```

#### `POST /api/v1/tasks/{task_id}/ai-toggle`
Toggles AI permission (`ai_enabled`) on an open task.
**Response:**
```json
{
  "ok": true,
  "message": "AI enabled for task #42",
  "ai_enabled": 1
}
```

#### `POST /api/v1/ai/chat`
Submits a user prompt to chat with the autonomous AI agent with task context.
**Request Body:**
```json
{
  "prompt": "What is the status of the like button tests?",
  "task_ids": "1,2,3"
}
```
**Response:**
```json
{
  "ok": true,
  "query_id": 5,
  "status": "pending"
}
```

#### `GET /api/v1/ai/chat/{query_id}`
Checks status and retrieves the AI agent's response for a chat query.
**Response:**
```json
{
  "id": 5,
  "user": "nikita",
  "prompt": "What is the status of the like button tests?",
  "task_ids": "1,2,3",
  "status": "done",
  "response": "The like button tests in test_real_like_button.py are passing...",
  "created_at": "2026-09-24T21:30:00Z",
  "completed_at": "2026-09-24T21:30:08Z"
}
```

#### `GET /api/v1/ai/agent/pending-work`
Used by the AI worker daemon on server `andrii` to poll for unclaimed AI-enabled tasks and pending web chat queries. Requires API Key.
**Response:**
```json
{
  "ai_mode": true,
  "pending_tasks": [ ... ],
  "chat_query": { ... }
}
```

#### `POST /api/v1/ai/agent/chat-response/{query_id}`
Posts the agent's completed response to a web chat query. Requires API Key.

#### `GET /api/v1/settings/alerts`
Returns the list of Telegram IDs configured to receive automated farm incident alerts.
**Response:**
```json
{
  "alert_recipients": [123456789]
}
```

#### `POST /api/v1/ai/agent/incident-report`
Submits an autonomous incident diagnosis, resolution, and post-mortem report from the AI agent on `andrii`. The backend immediately formats and dispatches the alert via Telegram Markdown messages to all registered `alert_recipients`.
**Request Body:**
```json
{
  "title": "Total like velocity dropped to 0 for 1h",
  "description": "Zombies detected in Chromium pool",
  "report": "# 🚨 Incident Post-Mortem...",
  "resolved": true
}
```

---

## 4. Telegram Bot Features & Admin Settings

- **Bot Integration:** Interactive Telegram Bot with inline keyboard updates and Guest Chat Mode support.
- **Dynamic In-Place Navigation:** The bot updates inline buttons and message text directly (`editMessageText`), avoiding spammy messages.
- **Project Settings (Admin Exclusive):**
  - Project settings can be accessed and modified **ONLY via Telegram Bot** and **ONLY by the primary admin (configured via `ADMIN_TG_ID`)**.
  - Admin settings allow:
    1. Toggling **AI Worker Mode** (ON/OFF).
    2. Managing **Registered Users** (add username + Telegram ID, remove user).
    3. Managing **🚨 Incident Alert Recipients** (add/remove Telegram IDs of operators who receive automated alerts when farm metrics drop).
- **Automated Farm Incident Monitoring & Remediation:**
  - If target metrics drop (node goes down or like velocity drops to 0 for 1 hour), the autonomous AI agent (`gemini-3.8-flash-medium` via `antigravity-cli`) diagnoses the root cause, repairs the farm, and dispatches detailed markdown post-mortem reports to all configured alert recipients.
- **Task AI Toggle:** When AI Mode is active, every open task displays an interactive button `🤖 AI: Enabled / Disabled` to permit agent pickup.
- **Guest Chat Mode:** Supports Telegram Bot API Guest Mode (`supports_guest_queries`), providing instant status overviews using `answerGuestQuery`.
- **File & Photo Attachments:** Send photos or documents directly in Telegram to attach them to tasks.
