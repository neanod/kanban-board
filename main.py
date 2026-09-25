import asyncio
import os
import shutil
import secrets
import mimetypes
import logging
from pathlib import Path
from typing import Optional, List, Dict, Any
from contextlib import asynccontextmanager

from fastapi import (
    FastAPI, Request, Response, Depends, HTTPException,
    status, UploadFile, File, Form, Query
)
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

import config
import database
import security
import bot

logger = logging.getLogger("kanban_app")

# Initialize database
database.init_db()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Start Telegram bot background task
    bot_task = asyncio.create_task(bot.start_telegram_bot_poller())
    yield
    # Shutdown: cancel bot poller
    bot_task.cancel()
    try:
        await bot_task
    except asyncio.CancelledError:
        pass

app = FastAPI(
    title="Kanban Task Board API",
    description="High-performance, exploit-resistant Kanban API with Telegram Bot integration, access control, and complete workflow management.",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan
)

# CORS & Security headers
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def security_middleware(request: Request, call_next):
    # Global Rate Limiting
    client_ip = security.get_client_ip(request)
    if request.url.path.startswith("/api/"):
        if not security.check_rate_limit(client_ip, security._global_requests, config.GLOBAL_RATE_LIMIT, 60):
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={"detail": "Too many requests. Please slow down."}
            )

    response = await call_next(request)
    
    # Hardened Security Headers
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response

# Mount Static directory
app.mount("/static", StaticFiles(directory=str(config.STATIC_DIR)), name="static")

# ----------------- Pydantic Request Models -----------------

class LoginRequest(BaseModel):
    password: Optional[str] = None
    user: Optional[str] = None
    nickname: Optional[str] = None
    telegram_auth: Optional[Dict[str, Any]] = None

class SwitchProfileRequest(BaseModel):
    user: str
    nickname: Optional[str] = None

class TaskCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = Field("", max_length=10000)
    ai_enabled: Optional[bool] = False
    created_by: Optional[str] = None

class ClaimTaskRequest(BaseModel):
    assignee: Optional[str] = None

class ReleaseTaskRequest(BaseModel):
    username: Optional[str] = None

class AIToggleRequest(BaseModel):
    enabled: Optional[bool] = None

class AIChatRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=5000)
    task_ids: Optional[str] = ""

class AIChatResponsePayload(BaseModel):
    response: str
    status: Optional[str] = "done"

class IncidentReportPayload(BaseModel):
    title: str = Field(..., min_length=1, max_length=300)
    description: Optional[str] = Field("", max_length=5000)
    report: str = Field(..., min_length=1, max_length=50000)
    resolved: Optional[bool] = False

class RejectTaskRequest(BaseModel):
    comment: str = Field(..., min_length=1, max_length=2000)

class MoveTaskRequest(BaseModel):
    status: str
    position: Optional[int] = 0
    reviewed_by: Optional[str] = None

class AddCommentRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=10000)

class GenerateKeyRequest(BaseModel):
    name: Optional[str] = Field("API Key", max_length=100)

# ----------------- Auth Endpoints -----------------

@app.post("/api/v1/auth/login", tags=["Authentication"])
async def login(req: LoginRequest, request: Request, response: Response):
    client_ip = security.get_client_ip(request)
    
    # Rate limit password / login attempts
    if not security.check_rate_limit(client_ip, security._login_attempts, config.LOGIN_RATE_LIMIT, 60):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts. Please wait 1 minute."
        )

    authenticated_user = None
    nickname = req.nickname or ""

    # 1. Telegram Login Widget Verification (if provided)
    if req.telegram_auth:
        tg_username = security.verify_telegram_auth(req.telegram_auth)
        if tg_username:
            authenticated_user = tg_username
            if not nickname:
                nickname = req.telegram_auth.get("first_name", tg_username.capitalize())
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Telegram authentication verification failed"
            )

    # 2. Site Password Verification
    elif req.password:
        if not security.verify_site_password(req.password):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect site password"
            )
        # Verify selected user is in allowed list
        if not req.user or req.user not in config.ALLOWED_USERS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Please select one of allowed accounts: {', '.join(config.ALLOWED_USERS)}"
            )
        authenticated_user = req.user
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Must provide either site password or Telegram login payload"
        )

    if not nickname:
        nickname = authenticated_user.capitalize()

    # Create session
    session_id = database.create_session(authenticated_user, nickname, days=30)
    
    # Set cookies
    response.set_cookie(
        key=config.SESSION_COOKIE_NAME,
        value=session_id,
        max_age=30 * 86400,
        httponly=True,
        samesite="lax",
        path="/"
    )
    response.set_cookie(
        key=config.PROFILE_COOKIE_NAME,
        value=authenticated_user,
        max_age=30 * 86400,
        httponly=False,
        samesite="lax",
        path="/"
    )
    response.set_cookie(
        key=config.NICKNAME_COOKIE_NAME,
        value=nickname,
        max_age=30 * 86400,
        httponly=False,
        samesite="lax",
        path="/"
    )

    return {
        "ok": True,
        "message": "Login successful",
        "user": authenticated_user,
        "nickname": nickname
    }

@app.post("/api/v1/auth/switch-profile", tags=["Authentication"])
async def switch_profile(req: SwitchProfileRequest, request: Request, response: Response):
    user_info = security.authenticate_request(request)
    if req.user not in config.ALLOWED_USERS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid user profile")
    
    nickname = req.nickname or req.user.capitalize()
    response.set_cookie(
        key=config.PROFILE_COOKIE_NAME,
        value=req.user,
        max_age=30 * 86400,
        httponly=False,
        samesite="lax",
        path="/"
    )
    response.set_cookie(
        key=config.NICKNAME_COOKIE_NAME,
        value=nickname,
        max_age=30 * 86400,
        httponly=False,
        samesite="lax",
        path="/"
    )
    return {"ok": True, "user": req.user, "nickname": nickname}

@app.post("/api/v1/auth/logout", tags=["Authentication"])
async def logout(request: Request, response: Response):
    session_id = request.cookies.get(config.SESSION_COOKIE_NAME)
    if session_id:
        database.delete_session(session_id)
    response.delete_cookie(config.SESSION_COOKIE_NAME, path="/")
    response.delete_cookie(config.PROFILE_COOKIE_NAME, path="/")
    response.delete_cookie(config.NICKNAME_COOKIE_NAME, path="/")
    return {"ok": True, "message": "Logged out successfully"}

@app.get("/api/v1/auth/me", tags=["Authentication"])
async def get_me(request: Request):
    user_info = security.authenticate_optional(request)
    if not user_info:
        return {"authenticated": False}
    return {
        "authenticated": True,
        "username": user_info["username"],
        "nickname": user_info["nickname"],
        "auth_type": user_info["auth_type"],
        "allowed_users": config.ALLOWED_USERS
    }

# ----------------- Tasks API -----------------

@app.get("/api/v1/stats", tags=["Tasks"])
async def get_board_stats(request: Request):
    security.authenticate_request(request)
    return database.get_stats()

@app.get("/api/v1/tasks", tags=["Tasks"])
async def get_tasks(
    status: Optional[str] = None,
    assignee: Optional[str] = None,
    request: Request = None
):
    security.authenticate_request(request)
    if status and status not in config.VALID_STATUSES:
        raise HTTPException(status_code=400, detail=f"Invalid status '{status}'. Must be one of {config.VALID_STATUSES}")
    tasks = database.list_tasks(status=status, assignee=assignee)
    return {"tasks": tasks, "count": len(tasks)}

@app.post("/api/v1/tasks", status_code=status.HTTP_201_CREATED, tags=["Tasks"])
async def create_new_task(task_in: TaskCreateRequest, request: Request):
    user_info = security.authenticate_request(request)
    username = user_info["username"]
    created_by = (task_in.created_by.strip() if task_in.created_by and task_in.created_by.strip() else None) or username
    task = database.create_task(
        task_in.title,
        task_in.description or "",
        created_by,
        ai_enabled=1 if task_in.ai_enabled else 0
    )
    return {"ok": True, "message": "Task created", "task": task}

@app.get("/api/v1/tasks/{task_id}", tags=["Tasks"])
async def get_task_details(task_id: int, request: Request):
    security.authenticate_request(request)
    task = database.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task

@app.delete("/api/v1/tasks/{task_id}", tags=["Tasks"])
async def delete_task_endpoint(task_id: int, request: Request):
    user_info = security.authenticate_request(request)
    task = database.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    success = database.delete_task(task_id)
    if not success:
        raise HTTPException(status_code=400, detail="Failed to delete task")
    return {"ok": True, "message": f"Task #{task_id} deleted successfully"}

@app.post("/api/v1/tasks/{task_id}/claim", tags=["Tasks"])
async def claim_task_endpoint(task_id: int, request: Request, body: Optional[ClaimTaskRequest] = None):
    user_info = security.authenticate_request(request)
    username = user_info["username"]
    assignee = (body.assignee.strip() if body and body.assignee and body.assignee.strip() else None) or username
    ok, msg, updated = database.claim_task(task_id, assignee)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"ok": True, "message": msg, "task": updated}

@app.post("/api/v1/tasks/{task_id}/release", tags=["Tasks"])
async def release_task_endpoint(task_id: int, request: Request, body: Optional[ReleaseTaskRequest] = None):
    user_info = security.authenticate_request(request)
    username = user_info["username"]
    release_user = (body.username.strip() if body and body.username and body.username.strip() else None) or username
    ok, msg, updated = database.release_task(task_id, release_user)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"ok": True, "message": msg, "task": updated}

@app.post("/api/v1/tasks/{task_id}/submit-review", tags=["Tasks"])
async def submit_review_endpoint(task_id: int, request: Request):
    user_info = security.authenticate_request(request)
    username = user_info["username"]
    ok, msg, updated = database.submit_task_for_review(task_id, username)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"ok": True, "message": msg, "task": updated}

@app.post("/api/v1/tasks/{task_id}/approve", tags=["Tasks"])
async def approve_task_endpoint(task_id: int, request: Request):
    user_info = security.authenticate_request(request)
    username = user_info["username"]
    ok, msg, updated = database.approve_task(task_id, username)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"ok": True, "message": msg, "task": updated}

@app.post("/api/v1/tasks/{task_id}/reject", tags=["Tasks"])
async def reject_task_endpoint(task_id: int, body: RejectTaskRequest, request: Request):
    user_info = security.authenticate_request(request)
    username = user_info["username"]
    ok, msg, updated = database.reject_task(task_id, username, body.comment)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"ok": True, "message": msg, "task": updated}

@app.post("/api/v1/tasks/{task_id}/move", tags=["Tasks"])
async def move_task_endpoint(task_id: int, body: MoveTaskRequest, request: Request):
    user_info = security.authenticate_request(request)
    username = user_info["username"]
    if body.status not in config.VALID_STATUSES:
        raise HTTPException(status_code=400, detail=f"Invalid status '{body.status}'. Must be one of {config.VALID_STATUSES}")
    ok, msg, updated = database.move_task(
        task_id,
        body.status,
        body.position or 0,
        username,
        reviewed_by_override=body.reviewed_by.strip() if body.reviewed_by else None
    )
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"ok": True, "message": msg, "task": updated}

@app.post("/api/v1/tasks/{task_id}/comments", tags=["Tasks"])
async def add_task_comment(task_id: int, body: AddCommentRequest, request: Request):
    user_info = security.authenticate_request(request)
    username = user_info["username"]
    comment = database.add_comment(task_id, username, body.content)
    if not comment:
        raise HTTPException(status_code=400, detail="Failed to add comment")
@app.get("/api/v1/settings/public", tags=["Settings"])
async def get_public_settings():
    return {
        "ai_mode": database.is_ai_mode(),
        "allowed_users": [u["username"] for u in database.get_all_users()]
    }

@app.post("/api/v1/tasks/{task_id}/ai-toggle", tags=["Tasks"])
async def toggle_task_ai_endpoint(task_id: int, request: Request, body: Optional[AIToggleRequest] = None):
    user_info = security.authenticate_request(request)
    if not database.is_ai_mode():
        raise HTTPException(status_code=400, detail="AI mode is disabled in project settings")
    task = database.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    if body and body.enabled is not None:
        target_val = body.enabled
    else:
        target_val = not bool(task.get("ai_enabled", 0))

    ok, msg, updated = database.set_task_ai_enabled(task_id, target_val)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"ok": True, "message": msg, "task": updated}

# ----------------- AI Chat & Agent Endpoints -----------------

@app.post("/api/v1/ai/chat", tags=["AI Agent"])
async def send_ai_chat_query(body: AIChatRequest, request: Request):
    user_info = security.authenticate_request(request)
    if not database.is_ai_mode():
        raise HTTPException(status_code=400, detail="AI mode is disabled in project settings")
    query = database.create_chat_query(user_info["username"], body.prompt, body.task_ids or "")
    return {"ok": True, "query_id": query["id"], "status": query["status"]}

@app.get("/api/v1/ai/chat/{query_id}", tags=["AI Agent"])
async def get_ai_chat_query_status(query_id: int, request: Request):
    security.authenticate_request(request)
    query = database.get_chat_query(query_id)
    if not query:
        raise HTTPException(status_code=404, detail="Chat query not found")
    return {
        "id": query["id"],
        "status": query["status"],
        "response": query["response"],
        "created_at": query["created_at"],
        "completed_at": query.get("completed_at")
    }

@app.get("/api/v1/ai/agent/pending-work", tags=["AI Agent"])
async def get_agent_pending_work(request: Request):
    # Only authenticated via API key
    user_info = security.authenticate_request(request)
    if not database.is_ai_mode():
        return {"ai_mode": False, "pending_chats": [], "pending_tasks": []}
    
    pending_chat = database.get_pending_chat_query()
    pending_tasks = database.get_pending_ai_tasks()
    return {
        "ai_mode": True,
        "pending_chats": [pending_chat] if pending_chat else [],
        "pending_tasks": pending_tasks
    }

@app.post("/api/v1/ai/agent/chat-response/{query_id}", tags=["AI Agent"])
async def submit_agent_chat_response(query_id: int, body: AIChatResponsePayload, request: Request):
    user_info = security.authenticate_request(request)
    query = database.get_chat_query(query_id)
    database.complete_chat_query(query_id, body.response, body.status or "done")
    if query and query.get("user"):
        try:
            await bot.send_ai_chat_response_to_user(query["user"], body.response)
        except Exception as e:
            logger.error(f"Failed to forward chat response to telegram user: {e}")
    return {"ok": True, "message": "Response recorded"}

@app.get("/api/v1/settings/alerts", tags=["Settings"])
async def get_alerts_settings(request: Request):
    user_info = security.authenticate_request(request)
    return {
        "alert_recipients": database.get_alert_recipients()
    }

@app.post("/api/v1/ai/agent/incident-report", tags=["AI Agent"])
async def receive_incident_report(body: IncidentReportPayload, request: Request):
    user_info = security.authenticate_request(request)
    sent_to = await bot.send_incident_alert_to_recipients(
        title=body.title,
        report_text=body.report,
        resolved=bool(body.resolved)
    )
    return {
        "ok": True,
        "message": f"Incident report dispatched to {len(sent_to)} recipients",
        "sent_to": sent_to
    }

@app.post("/api/v1/tasks/{task_id}/attachments", tags=["Attachments"])
async def upload_attachment(
    task_id: int,
    request: Request,
    file: UploadFile = File(...)
):
    user_info = security.authenticate_request(request)
    username = user_info["username"]
    task = database.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    original_filename = security.sanitize_filename(file.filename or "attachment")
    stored_filename = f"{task_id}_{secrets.token_hex(8)}_{original_filename}"
    target_path = config.UPLOAD_DIR / stored_filename

    # Read and enforce file size
    bytes_read = 0
    with open(target_path, "wb") as f:
        while True:
            chunk = await file.read(65536)
            if not chunk:
                break
            bytes_read += len(chunk)
            if bytes_read > config.MAX_UPLOAD_SIZE:
                target_path.unlink(missing_ok=True)
                raise HTTPException(status_code=413, detail="File too large (max 25MB)")
            f.write(chunk)

    mime_type, _ = mimetypes.guess_type(original_filename)
    if not mime_type:
        mime_type = file.content_type or "application/octet-stream"

    att = database.add_attachment(
        task_id=task_id,
        uploader=username,
        original_filename=original_filename,
        stored_filename=stored_filename,
        file_size=bytes_read,
        mime_type=mime_type
    )
    return {"ok": True, "attachment": att}

@app.get("/api/v1/attachments/{attachment_id}", tags=["Attachments"])
async def get_attachment_file(attachment_id: str, request: Request):
    security.authenticate_request(request)
    att = database.get_attachment_by_id(attachment_id)
    if not att:
        raise HTTPException(status_code=404, detail="Attachment not found")

    file_path = config.UPLOAD_DIR / att["stored_filename"]
    # Path traversal check
    if not file_path.resolve().is_relative_to(config.UPLOAD_DIR.resolve()) or not file_path.exists():
        raise HTTPException(status_code=404, detail="File missing on disk")

    return FileResponse(
        path=str(file_path),
        filename=att["original_filename"],
        media_type=att["mime_type"]
    )

# ----------------- API Key Management -----------------

@app.get("/api/v1/keys", tags=["API Keys"])
async def list_keys_endpoint(request: Request):
    user_info = security.authenticate_request(request)
    keys = database.list_api_keys(user_info["username"])
    return {"keys": keys}

@app.post("/api/v1/keys", status_code=status.HTTP_201_CREATED, tags=["API Keys"])
async def generate_key_endpoint(body: GenerateKeyRequest, request: Request):
    user_info = security.authenticate_request(request)
    username = user_info["username"]
    raw_key, info = database.generate_api_key(username, body.name or "API Key")
    return {
        "ok": True,
        "message": "API key generated successfully. Save it now, it will not be shown again!",
        "api_key": raw_key,
        "info": info
    }

@app.delete("/api/v1/keys/{key_id}", tags=["API Keys"])
async def revoke_key_endpoint(key_id: str, request: Request):
    user_info = security.authenticate_request(request)
    username = user_info["username"]
    success = database.revoke_api_key(key_id, username)
    if not success:
        raise HTTPException(status_code=404, detail="API key not found or already revoked")
    return {"ok": True, "message": "API key revoked"}

# ----------------- HTML Pages -----------------

@app.get("/", response_class=HTMLResponse)
async def serve_kanban_ui():
    index_file = config.STATIC_DIR / "index.html"
    if not index_file.exists():
        return HTMLResponse("<h1>Loading Kanban Board...</h1>", status_code=200)
    with open(index_file, "r", encoding="utf-8") as f:
        return HTMLResponse(f.read())

@app.get("/api-docs", response_class=HTMLResponse)
async def serve_api_docs_ui():
    docs_file = config.STATIC_DIR / "docs.html"
    if not docs_file.exists():
        return RedirectResponse("/docs")
    with open(docs_file, "r", encoding="utf-8") as f:
        return HTMLResponse(f.read())

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host=config.HOST, port=config.PORT, reload=False)
