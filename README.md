# ⚡ TaskBoard: Kanban Board, Telegram Bot & Autonomous AI Agent

TaskBoard is a high-concurrency, attack-resistant Kanban task management system featuring:
- **Interactive Web Interface:** 4 workflow columns with drag-and-drop card reordering, file/image upload with Ctrl+V clipboard paste support, instant profile switching, and an integrated AI assistant.
- **Interactive Telegram Bot:** Complete workflow management with in-place button updates (`editMessageText`), file attachments, API key management, Telegram Guest Chat Mode support, and exclusive admin settings.
- **Autonomous AI Worker Daemon:** Background integration powered by Google Antigravity (`agy`) / Gemini 3.8 Flash to autonomously claim tasks, verify implementations, and diagnose & remediate infrastructure metric drops.
- **RESTful API:** Token-authenticated API with sliding-window rate limiting, constant-time hash verification, and interactive Swagger UI (`/docs`).

---

## 🚀 Key Features

### 1. Four-Stage Kanban Workflow
- **Open (`open`):** Newly created tasks ready for pickup. Displays toggle for autonomous AI agent execution.
- **In Progress (`in_progress`):** Claimed tasks currently being worked on. Shows assignee.
- **Under Review (`review`):** Tasks submitted for quality assurance.
- **Completed (`completed`):** Approved tasks. Records reviewer and completion timestamp.
- **Rejection Loop:** Tasks rejected in review are returned to Open with a mandatory explanation comment.

### 2. Web Interface
- Responsive, dark-themed dashboard.
- Full Drag-and-Drop column and vertical position ordering.
- Image and file attachments via file picker or clipboard paste (`Ctrl+V`).
- Modal with audit comment history and attachment previews.
- Instant profile switcher and session persistence via secure HTTP cookies.
- Integrated AI Chat interface to query project codebase and task status.

### 3. Telegram Bot Integration
- **In-Place Navigation:** Updates buttons and text directly, eliminating chat clutter.
- **Guest Chat Mode:** Supports Telegram Bot API Guest Mode (`answerGuestQuery`) for fast inline queries.
- **Direct File Uploads:** Send documents or photos to the bot to attach them directly to any task.
- **API Key Management:** Generate and view API keys directly in Telegram.
- **Admin Settings (Exclusive):** Primary administrator can toggle AI mode, manage authorized users, and manage alert recipients.

### 4. Autonomous AI Worker (`pixabay_ai_worker.py`)
- Runs as a system daemon alongside your target codebase.
- Polls for unclaimed tasks where AI is enabled.
- Investigates code, implements changes, runs mandatory test verification suites, and submits for human review.
- **Automated Incident Remediation:** Monitors cluster metrics (e.g. like velocity or worker heartbeat). When an incident occurs:
  1. Creates an incident task `(by ai agent)`.
  2. Claims the task immediately.
  3. Diagnoses and fixes hung processes/services.
  4. Verifies the fix; moves directly to Completed if verified or returns to Open with an attached failure report if unresolved.
  5. Dispatches post-mortem markdown incident reports to Telegram alert recipients.

### 5. Attack Hardening & Security
- **Rate Limiting:** Sliding-window per-IP rate limiting on login (5/min) and API endpoints (120/min).
- **Constant-Time Verification:** Password and token comparison using `hmac.compare_digest` against timing attacks.
- **Sanitized Uploads:** Path-traversal protection and strict 25 MB file size limit.
- **Security Headers:** Nosniff, Sameorigin, XSS protection, and strict CORS.
- **Crash-Resilience:** SQLite WAL mode with concurrency locking.

---

## 🛠️ Quick Start

### 1. Prerequisites
- Python 3.10+
- (Optional) Nginx for reverse proxy & SSL termination

### 2. Installation

```bash
# Clone repository
git clone https://github.com/your-username/kanban-board.git
cd kanban-board

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Configuration

Copy `.env.example` to `.env` and fill in your details:

```bash
cp .env.example .env
```

Example `.env`:
```env
# Server
HOST=127.0.0.1
PORT=8092
BASE_URL=https://your-domain.com:8445

# Bot Token (from @BotFather)
BOT_TOKEN=1234567890:ABCdefGhIJKlmNoPQRsTUVwxyZ_1234567

# Web Access Password
WEB_PASSWORD=your_secure_password_here

# Primary Administrator Telegram ID
ADMIN_TG_ID=123456789

# Authorized Users Mapping
TG_USER_MAP={"123456789": "nikita", "987654321": "dmitry", "112233445": "andrii"}
```

### 4. Running the Application

```bash
# Start server and Telegram polling
python main.py
```

Open your browser at `http://localhost:8092` (or your configured `BASE_URL`).

---

## 📖 API Documentation

The board includes interactive documentation:
- **Swagger UI:** `/docs`
- **ReDoc:** `/redoc`
- **Detailed Reference:** See [`API_DOCUMENTATION.md`](API_DOCUMENTATION.md)

### Authentication Header
Authenticate REST calls using your API key:
```http
X-API-Key: kb_<username>_<token>
```

---

## 🌐 Production Deployment (Systemd + Nginx)

### Systemd Service (`/etc/systemd/system/kanban.service`)
```ini
[Unit]
Description=Kanban Task Board Web & Telegram Bot
After=network.target

[Service]
Type=simple
User=your-user
WorkingDirectory=/path/to/kanban-board
ExecStart=/path/to/kanban-board/venv/bin/python main.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

### Nginx Configuration
A production configuration template is provided in [`kanban_nginx.conf`](kanban_nginx.conf).

---

## 📄 License

MIT License. Free for personal and commercial use.
