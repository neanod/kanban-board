#!/usr/bin/env python3
"""
Autonomous AI Worker Daemon for Pixabay Farm Project.
Runs on server 'andrii' using antigravity-cli ('agy').
Model: gemini-3.8-flash with effort=medium (--model gemini-3.8-flash-medium).
Connects to Kanban Board API to execute tasks and handle web chat queries.
"""

import os
import sys
import time
import json
import ssl
import signal
import secrets
import logging
import sqlite3
import subprocess
import urllib.request
import urllib.error
from typing import Dict, Any, Optional, Tuple, List

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ]
)
logger = logging.getLogger("ai_worker")

# Configuration
KANBAN_API_URL = os.environ.get("KANBAN_API_URL", "https://your-domain.com:8445").rstrip("/")
API_KEY = os.environ.get("KANBAN_API_KEY", "")
PROJECT_DIR = os.environ.get("PROJECT_DIR", "/path/to/pixabay_farm")
MODEL_NAME = "gemini-3.8-flash-medium"
POLL_INTERVAL_SECONDS = 5
METRICS_CHECK_INTERVAL = int(os.environ.get("METRICS_CHECK_INTERVAL", "60"))
INCIDENT_COOLDOWN = int(os.environ.get("INCIDENT_COOLDOWN", "1800"))

CLUSTER_SERVERS_CONTEXT = """
CLUSTER INFRASTRUCTURE & SERVER TOPOLOGY:
You are deployed on server 'andrii' and have pre-configured passwordless SSH access to all cluster nodes defined in ~/.ssh/config:

1. Server "home" (Aliases: "russia", "neanod", "nikita"):
   - Primary user workstation / main PC ('home', ZeroTier IP 10.157.97.70).
   - Reachable via command: `ssh home <command>` (or `ssh russia`, `ssh neanod`, `ssh nikita`).
   - If a task or user asks to do something with "home" / "russia" / "neanod" / "nikita" (e.g. check status, run script, manage files, inspect services), execute commands via `ssh home <command>`.

2. Server "latvia" (Aliases: "vpn"):
   - External VPN & Kanban Board API host (IP 89.36.161.118, domain bebra1488.ru).
   - Reachable via command: `ssh vpn <command>` (or `ssh latvia`).

3. Server "andrii" (Aliases: "шкаф", "kiyv", "kyiv", localhost 127.0.0.1 / 10.157.97.4):
   - THIS CURRENT LOCAL MACHINE where you are executing.
   - Hosts Pixabay Farm cluster controller, Minecraft server, and this AI Worker daemon.
   - Commands run locally here without ssh.

4. Server "dmitry":
   - Farm worker node (IP 10.157.97.182).
   - Reachable via command: `ssh dmitry <command>`.

When user requests involve server "home", "vpn", "andrii" ("шкаф"), or "dmitry", automatically recognize the target server and execute necessary commands using ssh or locally.
"""

RUNNING = True

def handle_signal(sig, frame):
    global RUNNING
    logger.info(f"Received exit signal {sig}, terminating gracefully...")
    RUNNING = False

signal.signal(signal.SIGINT, handle_signal)
signal.signal(signal.SIGTERM, handle_signal)

def get_ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx

def make_multipart_body(fields: Dict[str, Any], files: Dict[str, Tuple[str, bytes, str]]) -> Tuple[bytes, str]:
    boundary = secrets.token_hex(16)
    lines: List[bytes] = []
    
    for k, v in fields.items():
        lines.append(f"--{boundary}".encode())
        lines.append(f'Content-Disposition: form-data; name="{k}"'.encode())
        lines.append(b"")
        lines.append(str(v).encode())
        
    for name, (filename, content, content_type) in files.items():
        lines.append(f"--{boundary}".encode())
        lines.append(f'Content-Disposition: form-data; name="{name}"; filename="{filename}"'.encode())
        lines.append(f"Content-Type: {content_type}".encode())
        lines.append(b"")
        lines.append(content if isinstance(content, bytes) else content.encode())
        
    lines.append(f"--{boundary}--".encode())
    lines.append(b"")
    body = b"\r\n".join(lines)
    return body, f"multipart/form-data; boundary={boundary}"

def api_request(method: str, endpoint: str, data: Optional[Dict[str, Any]] = None, files: Optional[Dict[str, Tuple[str, bytes, str]]] = None) -> Optional[Dict[str, Any]]:
    url = f"{KANBAN_API_URL}{endpoint}"
    headers = {
        "X-API-Key": API_KEY,
        "User-Agent": "PixabayAIWorker/1.0 (Server: andrii)"
    }
    
    req_body = None
    if files:
        fields = data or {}
        req_body, ctype = make_multipart_body(fields, files)
        headers["Content-Type"] = ctype
    elif data is not None:
        req_body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=req_body, headers=headers, method=method.upper())
    ctx = get_ssl_context()
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw)
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode("utf-8", errors="replace")
        logger.error(f"HTTPError {e.code} on {method} {endpoint}: {err_msg}")
        return None
    except Exception as e:
        logger.error(f"Request failed for {method} {endpoint}: {e}")
        return None

def run_agy(prompt: str, timeout: int = 600) -> Tuple[int, str, str]:
    """
    Executes antigravity-cli ('agy') non-interactively with gemini-3.8-flash-medium.
    """
    cmd = [
        "agy",
        "-p", prompt,
        "--model", MODEL_NAME,
        "--dangerously-skip-permissions"
    ]
    logger.info(f"Invoking agy in {PROJECT_DIR} (Model: {MODEL_NAME}, timeout: {timeout}s)...")
    try:
        proc = subprocess.run(
            cmd,
            cwd=PROJECT_DIR,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as e:
        logger.error(f"agy execution timed out after {timeout} seconds")
        stdout = e.stdout.decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
        stderr = e.stderr.decode() if isinstance(e.stderr, bytes) else (e.stderr or "")
        return -1, stdout, stderr + f"\nTimed out after {timeout} seconds"
    except Exception as e:
        logger.error(f"Failed to spawn agy process: {e}")
        return -2, "", str(e)

def process_chat_query(query: Dict[str, Any]):
    query_id = query["id"]
    user = query["user"]
    prompt = query["prompt"]
    task_ids_str = query.get("task_ids", "")

    logger.info(f"Processing AI Web Chat query #{query_id} from @{user}...")

    # Fetch context tasks if any
    task_contexts = []
    if task_ids_str:
        for tid_str in task_ids_str.split(","):
            tid_str = tid_str.strip()
            if not tid_str.isdigit():
                continue
            tid = int(tid_str)
            t_data = api_request("GET", f"/api/v1/tasks/{tid}")
            if t_data:
                task_contexts.append(
                    f"Task #{t_data['id']}: {t_data['title']} (Status: {t_data['status']}, Assignee: {t_data.get('assignee', 'none')})\n"
                    f"Description: {t_data.get('description', '')}\n"
                )

    context_text = "\n".join(task_contexts) if task_contexts else "No specific tasks selected."

    agent_prompt = f"""You are an autonomous AI coding assistant and systems specialist deployed on server 'andrii' working on:
{PROJECT_DIR}

{CLUSTER_SERVERS_CONTEXT}

User: @{user}
Context Tasks:
{context_text}

User Question / Command:
{prompt}

Instruction:
Answer the user's question concisely, professionally, and accurately based on the current codebase, infrastructure status, and server context.
"""
    retcode, stdout, stderr = run_agy(agent_prompt, timeout=180)
    
    # Extract response
    response_text = stdout.strip()
    if not response_text and stderr:
        response_text = f"Agent encountered an issue: {stderr.strip()}"
    elif not response_text:
        response_text = "Agent completed execution with empty output."

    # Post response back to Kanban Board
    api_request("POST", f"/api/v1/ai/agent/chat-response/{query_id}", data={"response": response_text})
    logger.info(f"Completed AI Web Chat query #{query_id}")

def process_task(task: Dict[str, Any]):
    task_id = task["id"]
    title = task["title"]
    description = task.get("description", "")
    created_by = task.get("created_by", "unknown")

    logger.info(f"=== Starting Autonomous Execution on Task #{task_id}: {title} ===")

    # Step 1: Claim task
    claim_res = api_request("POST", f"/api/v1/tasks/{task_id}/claim")
    if not claim_res:
        logger.error(f"Failed to claim task #{task_id}, skipping")
        return

    # Step 2: Post initial working comment
    api_request("POST", f"/api/v1/tasks/{task_id}/comments", data={
        "content": f"🤖 Autonomous AI Agent started working on this task.\nModel: {MODEL_NAME}\nWorking Directory: {PROJECT_DIR}"
    })

    # Step 3: Build autonomous task execution prompt
    execution_prompt = f"""You are an autonomous senior software engineer and systems administrator working on:
{PROJECT_DIR}

Task #{task_id}: {title}
Creator: @{created_by}
Description:
{description}

{CLUSTER_SERVERS_CONTEXT}

STRICT REQUIREMENTS:
1. Thoroughly investigate what needs to be done. If the task relates to a specific server (e.g. "home", "russia", "neanod", "nikita", "vpn", "latvia", "шкаф", "kiyv", "andrii", "dmitry"), use your configured SSH access (`ssh home ...`, `ssh vpn ...`, `ssh dmitry ...`, or local commands) to inspect, execute, or manage operations on that server.
2. TESTING IS MANDATORY: You MUST run and verify relevant test suites, scripts, or status checks to confirm everything works as expected.
3. System service reboots, process restarts, or temporary network resets are EXPECTED standard operations in this environment. DO NOT consider them errors.
4. When finished:
   - If ALL tests pass and the implementation is complete and verified, output:
     TASK_RESULT: SUCCESS
     Followed by a concise bulleted summary of changes made and test outputs.
   - If you are completely unable to solve the task or hit an insurmountable barrier, output:
     TASK_RESULT: FAILED
     Followed by a detailed technical root-cause explanation of what failed and what was tried.
"""

    # Step 4: Run agy
    retcode, stdout, stderr = run_agy(execution_prompt, timeout=900)
    output = stdout.strip()

    is_success = ("TASK_RESULT: SUCCESS" in output) and (retcode == 0)
    is_failed = ("TASK_RESULT: FAILED" in output) or (retcode != 0)

    if is_success:
        logger.info(f"Task #{task_id} successfully completed and verified by AI Agent!")
        
        # Post summary comment
        comment_content = f"✅ **AI Autonomous Execution Succeeded**\n\n{output}"
        # Truncate if excessively long
        if len(comment_content) > 9500:
            comment_content = comment_content[:9500] + "\n...(truncated)"
        api_request("POST", f"/api/v1/tasks/{task_id}/comments", data={"content": comment_content})

        # Submit for review
        api_request("POST", f"/api/v1/tasks/{task_id}/submit-review")
        logger.info(f"Task #{task_id} submitted for human review.")

    else:
        logger.warning(f"Task #{task_id} could not be completed by AI Agent. Generating failure report...")
        
        # Generate markdown failure report
        failure_report_content = f"""# Autonomous AI Agent Failure Report
**Task #{task_id}:** {title}  
**Date:** {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}  
**Host:** server `andrii`  
**Model:** `{MODEL_NAME}`  
**Project Path:** `{PROJECT_DIR}`  

---

## 1. Problem Description
{description or 'No description provided.'}

---

## 2. Technical Reasons & Failure Diagnosis
The autonomous agent encountered obstacles preventing successful completion of this task:

```text
{output if output else stderr}
```

---

## 3. Standard Environment Diagnostics & Notes
- Server/daemon service resets occurred as expected during tests.
- Re-run or manual human intervention is recommended.
"""
        # Upload .md report as attachment
        filename = f"task_{task_id}_failure_report.md"
        files = {
            "file": (filename, failure_report_content.encode("utf-8"), "text/markdown")
        }
        att_res = api_request("POST", f"/api/v1/tasks/{task_id}/attachments", files=files)
        if att_res:
            logger.info(f"Uploaded failure report attachment for task #{task_id}")

        # Post comment
        api_request("POST", f"/api/v1/tasks/{task_id}/comments", data={
            "content": f"⚠️ AI Agent was unable to complete this task and gave up. A detailed failure report `{filename}` has been uploaded as an attachment. Returning task to Open."
        })

        # Disable AI execution on this task to prevent retry loops
        api_request("POST", f"/api/v1/tasks/{task_id}/ai-toggle")

        # Release task back to Open
        api_request("POST", f"/api/v1/tasks/{task_id}/release")
        logger.info(f"Task #{task_id} released back to Open.")

def check_farm_target_metrics() -> Tuple[bool, str, Dict[str, Any]]:
    """
    Evaluates Pixabay Farm target metrics:
    1. Overall like velocity dropped to 0 over the past 1 hour (while active tracks need likes).
    2. Server / node is not performing likes (node marked online but liker dead / no heartbeat / local pixabay_farm.py dead).
    Returns: (incident_detected: bool, reason: str, stats: dict)
    """
    db_path = os.path.join(PROJECT_DIR, "pixabay_farm.db")
    if not os.path.isfile(db_path):
        return False, "Database not found", {}

    stats: Dict[str, Any] = {
        "likes_last_hour": 0,
        "active_tracks": 0,
        "last_like_time": "Never",
        "nodes": [],
        "local_liker_running": False
    }

    try:
        conn = sqlite3.connect(db_path, timeout=10.0)
        c = conn.cursor()

        # 1. Count active tracks that need likes
        c.execute("SELECT count(*) FROM tracks WHERE status = 'active'")
        active_tracks = c.fetchone()[0]
        stats["active_tracks"] = active_tracks

        # 2. Count likes in the last 1 hour
        c.execute("SELECT count(*) FROM likes_history WHERE liked_at >= datetime('now', '-1 hour')")
        likes_1h = c.fetchone()[0]
        stats["likes_last_hour"] = likes_1h

        # 3. Last recorded like
        c.execute("SELECT max(liked_at) FROM likes_history")
        row = c.fetchone()
        stats["last_like_time"] = row[0] if row and row[0] else "Never"

        # 4. Check registered nodes
        try:
            c.execute("SELECT node_id, name, ip, status, current_task, last_heartbeat, meta_json FROM farm_nodes")
            for r in c.fetchall():
                node_info = {
                    "node_id": r[0],
                    "name": r[1],
                    "ip": r[2],
                    "status": r[3],
                    "current_task": r[4],
                    "last_heartbeat": r[5],
                    "meta": {}
                }
                if r[6]:
                    try:
                        node_info["meta"] = json.loads(r[6])
                    except Exception:
                        pass
                stats["nodes"].append(node_info)
        except Exception:
            pass

        conn.close()
    except Exception as e:
        logger.error(f"Error querying pixabay_farm.db: {e}")
        return False, f"DB error: {e}", stats

    # 5. Check if local liker process is running on this server ('andrii')
    local_liker_running = False
    try:
        ps_out = subprocess.check_output(["pgrep", "-f", "pixabay_farm.py"]).decode().strip()
        if ps_out:
            local_liker_running = True
    except Exception:
        local_liker_running = False
    stats["local_liker_running"] = local_liker_running

    # Check Condition 1: Total like velocity dropped to 0 for 1 hour while active tracks exist
    if active_tracks > 0 and likes_1h == 0:
        reason = (
            f"Farm like rate dropped to 0 likes in the last 1 hour!\n"
            f"• Active tracks needing likes: {active_tracks}\n"
            f"• Total likes in last 60 minutes: 0\n"
            f"• Last successful like: {stats['last_like_time']} UTC\n"
            f"• Local liker process on andrii: {'RUNNING' if local_liker_running else 'STOPPED'}"
        )
        return True, reason, stats

    # Check Condition 2: Local server or one of the nodes is not performing likes
    if active_tracks > 0 and not local_liker_running:
        reason = (
            f"Server 'andrii' liker process is inactive / not performing likes!\n"
            f"• pixabay_farm.py is not running.\n"
            f"• Active tracks in queue: {active_tracks}\n"
            f"• Last recorded like: {stats['last_like_time']} UTC"
        )
        return True, reason, stats

    for node in stats["nodes"]:
        if node["status"] == "online":
            liker_alive = node.get("meta", {}).get("liker_alive", True)
            if not liker_alive:
                reason = (
                    f"Node '{node['name']}' ({node['node_id']} @ {node['ip']}) reported liker_alive = False!\n"
                    f"• Node is not performing likes."
                )
                return True, reason, stats

    return False, "All target metrics healthy", stats

def handle_incident(reason: str, stats: Dict[str, Any]):
    """
    Invokes the autonomous AI agent (gemini-3.8-flash-medium) to investigate and resolve
    the target metric drop, managing the Kanban task lifecycle and alerting Telegram recipients:
    1. Creates a task with (by ai agent) and created_by="ai agent".
    2. Immediately claims the task into In Progress by "ai agent".
    3. Investigates, fixes, and verifies the resolution via agy.
    4. If verified/resolved: moves directly to Completed (reviewed_by="ai agent").
    5. If failed/gave up: returns to Open with an attached .md failure report & explanation comment.
    6. Dispatches post-mortem incident report to Telegram alert recipients.
    """
    logger.warning(f"🚨 [INCIDENT DETECTED] {reason}")
    first_line = reason.splitlines()[0]
    task_title = f"🚨 {first_line} (by ai agent)"

    # Step 1: Create Kanban task by ai agent
    logger.info(f"Creating incident task on Kanban board: '{task_title}'...")
    task_id = None
    try:
        task_res = api_request("POST", "/api/v1/tasks", data={
            "title": task_title,
            "description": f"Target metric drop detected by Pixabay Farm monitor:\n\n{reason}\n\nAutomated AI remediation in progress.",
            "created_by": "ai agent",
            "ai_enabled": True
        })
        if task_res and task_res.get("task"):
            task_id = task_res["task"]["id"]
            logger.info(f"Created incident task #{task_id} (by ai agent)")
        else:
            logger.error(f"Failed to create incident task on Kanban board: {task_res}")
    except Exception as e:
        logger.error(f"Exception creating incident task: {e}")

    # Step 2: Immediately claim task into In Progress
    if task_id:
        claim_res = api_request("POST", f"/api/v1/tasks/{task_id}/claim", data={"assignee": "ai agent"})
        if claim_res and claim_res.get("ok"):
            logger.info(f"Task #{task_id} successfully claimed into In Progress by ai agent")
        else:
            logger.warning(f"Failed to claim task #{task_id}: {claim_res}")

        # Post initial investigation comment
        api_request("POST", f"/api/v1/tasks/{task_id}/comments", data={
            "content": f"🤖 Autonomous AI Agent ({MODEL_NAME}) claimed this incident task and began investigating logs, cluster nodes, and liker processes on server 'andrii'."
        })

    # Step 3: Run AI agent to diagnose, remediate, and verify
    logger.info(f"Triggering autonomous AI Agent ({MODEL_NAME}) on server 'andrii' to investigate and resolve...")

    prompt = f"""You are an autonomous Site Reliability Engineer and Senior Systems Specialist for the Pixabay Farm cluster located at:
{PROJECT_DIR}

{CLUSTER_SERVERS_CONTEXT}

🚨 CRITICAL INCIDENT DETECTED (Kanban Task #{task_id if task_id else 'N/A'}):
{reason}

METRICS SUMMARY:
- Likes in last 1 hour: {stats.get('likes_last_hour', 0)}
- Active tracks waiting for likes: {stats.get('active_tracks', 0)}
- Last like timestamp: {stats.get('last_like_time', 'Never')} UTC
- Local liker running: {stats.get('local_liker_running', False)}

YOUR MISSION:
1. INVESTIGATE & DIAGNOSE:
   - Check recent logs: `logs/liker_andrii.log`, `logs/panel_debug.log`, `web_panel.log`, `logs/agent.log`.
   - Inspect processes: `ps aux | grep -E "chrome|chromium|pixabay|node_agent"` (look for hung Chromium processes with high CPU, zombie renderers, or dead workers).
   - Check if Xray proxy pool is active on ports 10811..10818 (`netstat -tuln` or `ss -tuln`).
   - Check whether `web_panel.py` / `pixabay_panel.service` is healthy or if `pixabay_farm.py` died due to an unhandled exception or timeout.

2. RESOLVE & RECOVER:
   - Kill any hung/zombie Chrome or Python processes if stuck:
     e.g., `kill -9 <PID>` or `pkill -9 -f chrome`.
   - If `pixabay_panel.service` or proxy pool is malfunctioning, restart:
     `systemctl --user restart pixabay_panel.service`
   - Start or ensure the liker worker is running and operating cleanly:
     Run `systemctl --user restart pixabay_liker.service` or `nohup python3 pixabay_farm.py >> logs/liker_andrii.log 2>&1 &`.
   - Verify that the process started, is listening or writing to logs, and that errors are cleared.

3. VERIFY FIX:
   - Run verification commands (check process running via `pgrep -f pixabay_farm.py`, tail logs `tail -n 20 logs/liker_andrii.log`, confirm no crash loop).
   - If the fix is verified to work, status MUST be RESOLVED.
   - If you are completely unable to solve the problem and give up, status MUST be FAILED.

4. REPORT:
   Provide an incident and resolution post-mortem enclosed EXACTLY between these tags:
   ===INCIDENT_REPORT_START===
   # 🚨 Pixabay Farm Incident & Resolution Report
   **Status:** [RESOLVED / FAILED]
   **Trigger:** {reason.splitlines()[0]}
   **Timestamp:** {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}

   ### 1. Root Cause Diagnosis
   <Detailed explanation of what failed: hung chrome processes, cloudflare challenge, dead worker loop, proxy pool, etc.>

   ### 2. Remediations & Actions Executed
   <List of actions taken: zombie processes terminated, service restarted, worker resumed, etc.>

   ### 3. Verification & Live Operational Status
   <Live log output, process status, and proof of operation>

   ### 4. Health Summary & Next Steps
   <Summary of current cluster state>
   ===INCIDENT_REPORT_END===
"""

    retcode, stdout, stderr = run_agy(prompt, timeout=900)
    output = stdout.strip()

    # Extract report
    report_text = ""
    if "===INCIDENT_REPORT_START===" in output and "===INCIDENT_REPORT_END===" in output:
        s = output.find("===INCIDENT_REPORT_START===") + len("===INCIDENT_REPORT_START===")
        e = output.find("===INCIDENT_REPORT_END===")
        report_text = output[s:e].strip()
    elif "# 🚨 Pixabay Farm" in output:
        s = output.find("# 🚨 Pixabay Farm")
        report_text = output[s:].strip()
    else:
        report_text = f"## Automated Farm Diagnostic & Repair Log\n\n```text\n{output if output else stderr}\n```"

    is_resolved = ("Status:** [RESOLVED]" in report_text or "Status:** RESOLVED" in report_text or "RESOLVED" in output[:200]) and (retcode == 0)

    # Double check if local liker process is active
    local_running = False
    try:
        ps_out = subprocess.check_output(["pgrep", "-f", "pixabay_farm.py"]).decode().strip()
        if ps_out:
            local_running = True
    except Exception:
        local_running = False

    if is_resolved and not local_running:
        logger.warning("Report stated RESOLVED but pixabay_farm.py is not running. Treating as not fully resolved.")
        is_resolved = False

    # Step 4: Handle task resolution and update board
    if is_resolved:
        logger.info(f"✅ Fix verified! AI Agent resolved incident for task #{task_id}.")
        if task_id:
            # Post summary comment
            comment_content = f"✅ **Incident Remediated & Verified by AI Agent**\n\n{report_text}"
            if len(comment_content) > 9500:
                comment_content = comment_content[:9500] + "\n...(truncated)"
            api_request("POST", f"/api/v1/tasks/{task_id}/comments", data={"content": comment_content})

            # Move directly to Completed
            move_res = api_request("POST", f"/api/v1/tasks/{task_id}/move", data={
                "status": "completed",
                "position": 0,
                "reviewed_by": "ai agent"
            })
            if move_res and move_res.get("ok"):
                logger.info(f"Task #{task_id} successfully moved to Completed (reviewed_by: ai agent).")
            else:
                logger.error(f"Failed to move task #{task_id} to Completed: {move_res}")
    else:
        logger.warning(f"❌ AI Agent gave up or could not resolve incident for task #{task_id}. Reverting task to Open...")
        if task_id:
            # Upload markdown failure report
            failure_report_content = f"""# Autonomous AI Agent Incident Remediation Failure Report
**Task #{task_id}:** {task_title}  
**Date:** {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}  
**Host:** server `andrii`  
**Model:** `{MODEL_NAME}`  
**Project Path:** `{PROJECT_DIR}`  

---

## 1. Incident Trigger
{reason}

---

## 2. Technical Reasons & Failure Diagnosis
The autonomous agent attempted diagnosis and recovery but was unable to resolve the incident:

```text
{report_text if report_text else (output if output else stderr)}
```

---

## 3. Recommended Actions for Human Engineers
- Inspect logs: `{PROJECT_DIR}/logs/liker_andrii.log`
- Check proxy status on ports 10811..10818.
- Verify browser / captcha constraints or restart user services.
"""
            filename = f"task_{task_id}_incident_failure_report.md"
            files = {
                "file": (filename, failure_report_content.encode("utf-8"), "text/markdown")
            }
            att_res = api_request("POST", f"/api/v1/tasks/{task_id}/attachments", files=files)
            if att_res:
                logger.info(f"Uploaded failure report attachment `{filename}` for task #{task_id}")

            # Post comment
            api_request("POST", f"/api/v1/tasks/{task_id}/comments", data={
                "content": f"⚠️ AI Agent was unable to resolve this incident and gave up.\nA detailed failure report `{filename}` has been uploaded as an attachment.\nReturning task to Open for human investigation."
            })

            # Disable AI on this task to prevent re-pickup loop
            api_request("POST", f"/api/v1/tasks/{task_id}/ai-toggle", data={"enabled": False})

            # Return task to Open
            move_res = api_request("POST", f"/api/v1/tasks/{task_id}/move", data={
                "status": "open",
                "position": 0
            })
            if move_res and move_res.get("ok"):
                logger.info(f"Task #{task_id} returned to Open column.")
            else:
                api_request("POST", f"/api/v1/tasks/{task_id}/release", data={"username": "ai agent"})

    # Step 5: Dispatch incident report to Telegram alert recipients
    payload = {
        "title": first_line,
        "description": reason,
        "report": report_text,
        "resolved": is_resolved
    }

    logger.info("Dispatching incident report to Kanban API (/api/v1/ai/agent/incident-report)...")
    res = api_request("POST", "/api/v1/ai/agent/incident-report", data=payload)
    if res and res.get("ok"):
        logger.info(f"✅ Incident alert successfully dispatched to {len(res.get('sent_to', []))} recipients: {res.get('sent_to')}")
    else:
        logger.error(f"❌ Failed to dispatch incident alert: {res}")

def main():
    logger.info("==================================================")
    logger.info("Pixabay Farm AI Autonomous Worker Starting")
    logger.info(f"Target Project: {PROJECT_DIR}")
    logger.info(f"Kanban API URL: {KANBAN_API_URL}")
    logger.info(f"AI Model: {MODEL_NAME}")
    logger.info(f"Metrics Check Interval: {METRICS_CHECK_INTERVAL}s")
    logger.info("==================================================")

    # Initial check of directory
    if not os.path.isdir(PROJECT_DIR):
        logger.error(f"Project directory {PROJECT_DIR} does not exist!")
        sys.exit(1)

    last_metrics_check = 0.0
    last_incident_triggered = 0.0
    incident_active = False

    while RUNNING:
        try:
            # Poll for pending work
            work_data = api_request("GET", "/api/v1/ai/agent/pending-work")
            if not work_data:
                time.sleep(POLL_INTERVAL_SECONDS)
                continue

            ai_mode = work_data.get("ai_mode", False)
            if not ai_mode:
                # AI mode disabled in project settings
                time.sleep(POLL_INTERVAL_SECONDS)
                continue

            # 1. Process pending chat query with priority
            pending_chats = work_data.get("pending_chats", [])
            chat_query = work_data.get("chat_query") or (pending_chats[0] if pending_chats else None)
            if chat_query:
                process_chat_query(chat_query)
                continue

            # 2. Process pending task
            pending_tasks = work_data.get("pending_tasks", [])
            if pending_tasks:
                task = pending_tasks[0]
                process_task(task)
                continue

            # 3. Check Farm Target Metrics
            now = time.time()
            if now - last_metrics_check >= METRICS_CHECK_INTERVAL:
                last_metrics_check = now
                has_incident, incident_reason, metric_stats = check_farm_target_metrics()
                if has_incident:
                    if (now - last_incident_triggered) >= INCIDENT_COOLDOWN or not incident_active:
                        incident_active = True
                        last_incident_triggered = now
                        handle_incident(incident_reason, metric_stats)
                else:
                    if incident_active:
                        logger.info("🎉 Farm metrics have recovered to healthy status!")
                        incident_active = False

            time.sleep(POLL_INTERVAL_SECONDS)

        except Exception as e:
            logger.error(f"Unhandled exception in worker loop: {e}", exc_info=True)
            time.sleep(POLL_INTERVAL_SECONDS)

    logger.info("Pixabay Farm AI Worker shutdown complete.")

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--check-metrics":
        has_inc, reason, stats = check_farm_target_metrics()
        print("Incident:", has_inc)
        print("Reason:", reason)
        print("Stats:", json.dumps(stats, indent=2))
        sys.exit(0)
    elif len(sys.argv) > 1 and sys.argv[1] == "--test-incident":
        has_inc, reason, stats = check_farm_target_metrics()
        print("Triggering incident resolution...")
        handle_incident(reason or "Manual test incident trigger", stats)
        sys.exit(0)
    main()
