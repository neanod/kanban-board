// Global State
let currentUser = null;
let currentNickname = "";
let allTasks = [];
let activeTaskModalId = null;
let pollTimer = null;
let autoRefresh = true;
let aiModeActive = false;
let selectedAiTaskIds = new Set();
let draggedTaskId = null;
let draggedTaskStatus = null;
let isDraggingCard = false;
let dragDropInitialized = false;

// Toast helper
function showToast(message, type = "info") {
  const toast = document.getElementById("toast");
  toast.innerText = message;
  toast.style.borderColor = type === "error" ? "var(--accent-red)" : (type === "success" ? "var(--accent-green)" : "var(--accent-blue)");
  toast.style.display = "block";
  setTimeout(() => {
    toast.style.display = "none";
  }, 3500);
}

// ----------------- Auth & Init -----------------

async function checkAuth() {
  try {
    const res = await fetch("/api/v1/auth/me");
    const data = await res.json();
    if (data.authenticated) {
      currentUser = data.username;
      currentNickname = data.nickname || data.username;
      document.getElementById("authModal").style.display = "none";
      updateUserUI();
      loadBoard();
      startPolling();
    } else {
      document.getElementById("authModal").style.display = "flex";
    }
  } catch (err) {
    console.error("Auth check failed:", err);
    document.getElementById("authModal").style.display = "flex";
  }
}

function updateUserUI() {
  const avatar = document.getElementById("userAvatar");
  const nameDisplay = document.getElementById("userDisplayName");
  const switcher = document.getElementById("profileSwitcher");

  if (avatar && nameDisplay) {
    avatar.innerText = (currentNickname || currentUser || "U")[0].toUpperCase();
    nameDisplay.innerText = `${currentNickname} (@${currentUser})`;
  }
  if (switcher) {
    switcher.value = currentUser;
  }
}

async function handleLogin(e) {
  if (e) e.preventDefault();
  const password = document.getElementById("loginPassword").value;
  const selectedUser = document.querySelector(".profile-option.selected")?.dataset.user || "dmitry";
  const nickname = document.getElementById("loginNickname").value || selectedUser.capitalize();

  try {
    const res = await fetch("/api/v1/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password, user: selectedUser, nickname })
    });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || "Login failed");
    }
    currentUser = data.user;
    currentNickname = data.nickname;
    document.getElementById("authModal").style.display = "none";
    updateUserUI();
    showToast(`Logged in as @${currentUser}`, "success");
    loadBoard();
    startPolling();
  } catch (err) {
    showToast(err.message, "error");
  }
}

async function switchProfile(newUser) {
  try {
    const res = await fetch("/api/v1/auth/switch-profile", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user: newUser })
    });
    const data = await res.json();
    if (res.ok) {
      currentUser = data.user;
      currentNickname = data.nickname;
      updateUserUI();
      showToast(`Switched account to @${currentUser}`, "success");
      loadBoard();
    }
  } catch (err) {
    showToast("Failed to switch profile", "error");
  }
}

async function handleLogout() {
  await fetch("/api/v1/auth/logout", { method: "POST" });
  window.location.reload();
}

// ----------------- Board Operations -----------------

async function loadBoard() {
  try {
    const [tasksRes, statsRes, settingsRes] = await Promise.all([
      fetch("/api/v1/tasks"),
      fetch("/api/v1/stats"),
      fetch("/api/v1/settings/public")
    ]);
    if (!tasksRes.ok || !statsRes.ok) return;

    const tasksData = await tasksRes.json();
    const stats = await statsRes.json();
    allTasks = tasksData.tasks || [];

    if (settingsRes && settingsRes.ok) {
      const settingsData = await settingsRes.json();
      aiModeActive = Boolean(settingsData.ai_mode);
      const btnAi = document.getElementById("btnAiChat");
      if (btnAi) {
        btnAi.style.display = aiModeActive ? "inline-flex" : "none";
      }
      const createTaskAi = document.getElementById("createTaskAiGroup");
      if (createTaskAi) {
        createTaskAi.style.display = aiModeActive ? "block" : "none";
      }
    }

    // Update stats bar
    document.getElementById("statTotal").innerText = stats.total || 0;
    document.getElementById("statOpen").innerText = stats.open || 0;
    document.getElementById("statInProgress").innerText = stats.in_progress || 0;
    document.getElementById("statReview").innerText = stats.review || 0;
    document.getElementById("statCompleted").innerText = stats.completed || 0;

    // Update column count badges
    document.getElementById("colBadgeOpen").innerText = stats.open || 0;
    document.getElementById("colBadgeInProgress").innerText = stats.in_progress || 0;
    document.getElementById("colBadgeReview").innerText = stats.review || 0;
    document.getElementById("colBadgeCompleted").innerText = stats.completed || 0;

    renderTasks();
    initDragAndDrop();
  } catch (err) {
    console.error("Failed to load board data:", err);
  }
}

function renderTasks() {
  const searchTerm = (document.getElementById("searchBox")?.value || "").toLowerCase();
  const filterAssignee = document.getElementById("filterAssignee")?.value || "";

  const filtered = allTasks.filter(t => {
    const matchSearch = t.title.toLowerCase().includes(searchTerm) || (t.description && t.description.toLowerCase().includes(searchTerm));
    const matchAssignee = !filterAssignee || t.assignee === filterAssignee;
    return matchSearch && matchAssignee;
  });

  const columns = {
    open: document.getElementById("cardsOpen"),
    in_progress: document.getElementById("cardsInProgress"),
    review: document.getElementById("cardsReview"),
    completed: document.getElementById("cardsCompleted")
  };

  Object.values(columns).forEach(col => { col.innerHTML = ""; });

  filtered.forEach(task => {
    const col = columns[task.status];
    if (col) {
      col.appendChild(createCardElement(task));
    }
  });

  // Empty state hints
  Object.entries(columns).forEach(([status, col]) => {
    if (col.children.length === 0) {
      col.innerHTML = `<div class="empty-col-hint" style="text-align:center; padding: 24px; color: var(--text-muted); font-size: 0.85rem; pointer-events: none;">No tasks in this column</div>`;
    }
  });
}

function createCardElement(task) {
  const card = document.createElement("div");
  card.className = "task-card";
  card.setAttribute("draggable", "true");
  card.dataset.taskId = task.id;
  card.dataset.status = task.status;

  card.addEventListener("dragstart", (e) => {
    isDraggingCard = true;
    draggedTaskId = task.id;
    draggedTaskStatus = task.status;
    card.classList.add("dragging");
    e.dataTransfer.setData("text/plain", String(task.id));
    e.dataTransfer.effectAllowed = "move";
  });

  card.addEventListener("dragend", (e) => {
    card.classList.remove("dragging");
    removeDropPlaceholder();
    document.querySelectorAll(".column").forEach(c => c.classList.remove("drag-over"));
    setTimeout(() => {
      isDraggingCard = false;
      draggedTaskId = null;
      draggedTaskStatus = null;
    }, 50);
  });

  card.onclick = (e) => {
    if (isDraggingCard) return;
    // If clicking an action button, don't open modal
    if (e.target.closest("button")) return;
    openTaskModal(task.id);
  };

  // Rejection alert
  let rejectionHtml = "";
  if (task.status === "open" && task.rejection_comment) {
    rejectionHtml = `
      <div class="rejection-banner">
        <strong>⚠️ Previously Rejected:</strong>
        <span>${escapeHtml(task.rejection_comment)}</span>
      </div>
    `;
  }

  // Meta info (Assignee, Reviewer, Dates)
  let assigneeHtml = "";
  if (task.assignee) {
    assigneeHtml = `<span class="user-tag" title="Assigned / Taken by">@${escapeHtml(task.assignee)}</span>`;
  }

  let reviewerHtml = "";
  if (task.status === "completed" && task.reviewed_by) {
    reviewerHtml = `<span class="user-tag" style="background: rgba(52, 211, 153, 0.15); color: #34d399;" title="Verified by">Verified: @${escapeHtml(task.reviewed_by)}</span>`;
  }

  let attachmentsHtml = "";
  if (task.attachment_count > 0) {
    attachmentsHtml = `<span title="Attachments" style="color: var(--accent-blue);">📎 ${task.attachment_count}</span>`;
  }

  // Action buttons
  let actionsHtml = "";
  if (task.status === "open") {
    let aiToggleBtn = "";
    if (aiModeActive) {
      aiToggleBtn = `<button class="btn btn-outline btn-sm" onclick="toggleTaskAi(${task.id})" style="font-size:0.75rem; border-color:${task.ai_enabled ? 'rgba(56,189,248,0.4)' : 'var(--border-color)'}; color:${task.ai_enabled ? 'var(--accent-blue)' : 'var(--text-muted)'};" title="Toggle AI Autonomous Execution">${task.ai_enabled ? '🤖 AI: ON' : '🤖 AI: OFF'}</button>`;
    }
    actionsHtml = `
      <button class="btn btn-primary btn-sm" onclick="claimTask(${task.id})">🎯 Claim Task</button>
      ${aiToggleBtn}
    `;
  } else if (task.status === "in_progress") {
    actionsHtml = `
      <button class="btn btn-success btn-sm" onclick="submitForReview(${task.id})">📨 Submit for Review</button>
      <button class="btn btn-secondary btn-sm" onclick="releaseTask(${task.id})">↩️ Release</button>
    `;
  } else if (task.status === "review") {
    actionsHtml = `
      <button class="btn btn-success btn-sm" onclick="approveTask(${task.id})">✅ Approve & Complete</button>
      <button class="btn btn-danger btn-sm" onclick="openRejectModal(${task.id})">❌ Reject</button>
    `;
  }

  let aiBadgeHtml = "";
  if (task.ai_enabled) {
    aiBadgeHtml = `<span class="ai-badge" title="Autonomous AI Execution Permitted (Gemini 3.8 Flash)">🤖 AI</span>`;
  }

  card.innerHTML = `
    <div class="card-top">
      <div style="display:flex; align-items:center; gap:6px;">
        <span class="card-id">#${task.id}</span>
        ${aiBadgeHtml}
      </div>
      <div style="display:flex; align-items:center; gap:6px;">
        <span style="font-size: 0.7rem; color: var(--text-muted);">${task.created_at.substring(0, 10)}</span>
        <button class="btn btn-outline btn-sm" style="color: #f87171; border-color: rgba(248, 113, 113, 0.2); padding: 1px 5px; font-size: 0.75rem;" onclick="deleteTaskDirectly(${task.id})" title="Delete Task">🗑</button>
      </div>
    </div>
    <div class="card-title">${escapeHtml(task.title)}</div>
    ${task.description ? `<div class="card-desc">${escapeHtml(task.description)}</div>` : ''}
    ${rejectionHtml}
    <div class="card-meta">
      <div style="display:flex; align-items:center; gap:6px;">
        <span class="user-tag" title="Created by">By @${escapeHtml(task.created_by)}</span>
        ${assigneeHtml}
      </div>
      <div>
        ${reviewerHtml}
        ${attachmentsHtml}
      </div>
    </div>
    ${actionsHtml ? `<div class="card-actions">${actionsHtml}</div>` : ''}
  `;

  return card;
}

// ----------------- Drag & Drop Handling -----------------

function getDragAfterElement(container, y) {
  const draggableElements = [...container.querySelectorAll('.task-card:not(.dragging)')];
  return draggableElements.reduce((closest, child) => {
    const box = child.getBoundingClientRect();
    const offset = y - box.top - box.height / 2;
    if (offset < 0 && offset > closest.offset) {
      return { offset: offset, element: child };
    } else {
      return closest;
    }
  }, { offset: Number.NEGATIVE_INFINITY }).element;
}

function getOrCreatePlaceholder() {
  let placeholder = document.getElementById("dropPlaceholder");
  if (!placeholder) {
    placeholder = document.createElement("div");
    placeholder.id = "dropPlaceholder";
    placeholder.className = "drop-placeholder";
    placeholder.textContent = "Drop task here";
  }
  return placeholder;
}

function removeDropPlaceholder() {
  const ph = document.getElementById("dropPlaceholder");
  if (ph && ph.parentNode) {
    ph.parentNode.removeChild(ph);
  }
}

function initDragAndDrop() {
  if (dragDropInitialized) return;
  dragDropInitialized = true;

  const colConfigs = [
    { id: "cardsOpen", status: "open" },
    { id: "cardsInProgress", status: "in_progress" },
    { id: "cardsReview", status: "review" },
    { id: "cardsCompleted", status: "completed" }
  ];

  colConfigs.forEach(({ id, status }) => {
    const cardsList = document.getElementById(id);
    if (!cardsList) return;
    const parentCol = cardsList.closest(".column");

    const onDragOver = (e) => {
      if (!draggedTaskId) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = "move";
      if (parentCol) parentCol.classList.add("drag-over");

      const emptyHint = cardsList.querySelector(".empty-col-hint");
      if (emptyHint) emptyHint.style.display = "none";

      const placeholder = getOrCreatePlaceholder();
      const afterElement = getDragAfterElement(cardsList, e.clientY);
      if (afterElement == null) {
        cardsList.appendChild(placeholder);
      } else {
        cardsList.insertBefore(placeholder, afterElement);
      }
    };

    cardsList.addEventListener("dragover", onDragOver);
    if (parentCol) {
      parentCol.addEventListener("dragover", onDragOver);
      parentCol.addEventListener("dragleave", (e) => {
        if (!parentCol.contains(e.relatedTarget)) {
          parentCol.classList.remove("drag-over");
        }
      });
    }

    const onDrop = async (e) => {
      if (!draggedTaskId) return;
      e.preventDefault();
      e.stopPropagation();

      const placeholder = document.getElementById("dropPlaceholder");
      let dropPosition = 0;
      if (placeholder && placeholder.parentNode === cardsList) {
        let idx = 0;
        let found = false;
        for (let child of cardsList.children) {
          if (child === placeholder) {
            dropPosition = idx;
            found = true;
            break;
          }
          if (child.classList && child.classList.contains("task-card") && !child.classList.contains("dragging")) {
            idx++;
          }
        }
        if (!found) dropPosition = idx;
      }

      const taskId = draggedTaskId;
      const targetStatus = status;

      removeDropPlaceholder();
      if (parentCol) parentCol.classList.remove("drag-over");
      document.querySelectorAll(".column").forEach(c => c.classList.remove("drag-over"));

      try {
        const res = await fetch(`/api/v1/tasks/${taskId}/move`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ status: targetStatus, position: dropPosition })
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || "Failed to move task");
        showToast(data.message || "Task moved!", "success");
        await loadBoard();
      } catch (err) {
        console.error("Move error:", err);
        showToast(err.message || "Failed to move task", "error");
        await loadBoard();
      }
    };

    cardsList.addEventListener("drop", onDrop);
    if (parentCol) {
      parentCol.addEventListener("drop", onDrop);
    }
  });

  // Global dragover to prevent default drop animation where needed
  document.addEventListener("dragover", (e) => {
    if (draggedTaskId) {
      const isOverBoard = e.target.closest(".board-container");
      if (!isOverBoard) {
        removeDropPlaceholder();
        document.querySelectorAll(".column").forEach(c => c.classList.remove("drag-over"));
      }
    }
  });
}

// ----------------- Task Actions -----------------

async function claimTask(taskId) {
  try {
    const res = await fetch(`/api/v1/tasks/${taskId}/claim`, { method: "POST" });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail);
    showToast("Task claimed!", "success");
    loadBoard();
  } catch (err) {
    showToast(err.message, "error");
  }
}

async function releaseTask(taskId) {
  try {
    const res = await fetch(`/api/v1/tasks/${taskId}/release`, { method: "POST" });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail);
    showToast("Task released back to Open", "info");
    loadBoard();
  } catch (err) {
    showToast(err.message, "error");
  }
}

async function submitForReview(taskId) {
  try {
    const res = await fetch(`/api/v1/tasks/${taskId}/submit-review`, { method: "POST" });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail);
    showToast("Task submitted for review!", "success");
    loadBoard();
  } catch (err) {
    showToast(err.message, "error");
  }
}

async function approveTask(taskId) {
  try {
    const res = await fetch(`/api/v1/tasks/${taskId}/approve`, { method: "POST" });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail);
    showToast("Task approved and completed!", "success");
    loadBoard();
  } catch (err) {
    showToast(err.message, "error");
  }
}

function openRejectModal(taskId) {
  document.getElementById("rejectTaskId").value = taskId;
  document.getElementById("rejectReason").value = "";
  document.getElementById("rejectModal").style.display = "flex";
}

async function submitReject() {
  const taskId = document.getElementById("rejectTaskId").value;
  const comment = document.getElementById("rejectReason").value.trim();
  if (!comment) {
    showToast("Rejection comment is required", "error");
    return;
  }
  try {
    const res = await fetch(`/api/v1/tasks/${taskId}/reject`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ comment })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail);
    document.getElementById("rejectModal").style.display = "none";
    showToast("Task rejected and returned to Open", "info");
    loadBoard();
  } catch (err) {
    showToast(err.message, "error");
  }
}

async function deleteTaskDirectly(taskId) {
  if (!confirm(`Are you sure you want to permanently delete task #${taskId}?`)) return;
  try {
    const res = await fetch(`/api/v1/tasks/${taskId}`, { method: "DELETE" });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail);
    showToast(`Task #${taskId} deleted`, "info");
    loadBoard();
  } catch (err) {
    showToast(err.message, "error");
  }
}

async function handleDeleteActiveTask() {
  if (!activeTaskModalId) return;
  if (!confirm(`Are you sure you want to permanently delete task #${activeTaskModalId}?`)) return;
  try {
    const res = await fetch(`/api/v1/tasks/${activeTaskModalId}`, { method: "DELETE" });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail);
    document.getElementById("taskModal").style.display = "none";
    showToast(`Task #${activeTaskModalId} deleted`, "info");
    activeTaskModalId = null;
    loadBoard();
  } catch (err) {
    showToast(err.message, "error");
  }
}

// ----------------- Task Details Modal -----------------

async function openTaskModal(taskId) {
  activeTaskModalId = taskId;
  try {
    const res = await fetch(`/api/v1/tasks/${taskId}`);
    if (!res.ok) throw new Error("Task not found");
    const task = await res.json();

    document.getElementById("modalTaskTitle").innerText = `#${task.id}: ${task.title}`;
    document.getElementById("modalTaskDesc").innerText = task.description || "No description provided.";
    document.getElementById("modalTaskStatus").innerText = task.status.toUpperCase();
    document.getElementById("modalTaskStatus").className = `col-badge badge-${task.status}`;

    // Meta details
    let metaText = `Created by @${task.created_by} on ${task.created_at.substring(0, 16).replace('T', ' ')}`;
    if (task.assignee) {
      metaText += ` • Claimed by @${task.assignee}`;
    }
    if (task.reviewed_by) {
      metaText += ` • Verified by @${task.reviewed_by} on ${task.completed_at ? task.completed_at.substring(0, 16).replace('T', ' ') : ''}`;
    }
    document.getElementById("modalTaskMeta").innerText = metaText;

    // AI Permission section
    const aiSection = document.getElementById("modalAiSection");
    const aiBadge = document.getElementById("modalAiStatusBadge");
    const aiToggleBtn = document.getElementById("modalAiToggleBtn");
    if (aiSection && aiBadge && aiToggleBtn) {
      if (aiModeActive && task.status === "open") {
        aiSection.style.display = "flex";
        if (task.ai_enabled) {
          aiBadge.innerText = "Allowed";
          aiBadge.style.background = "rgba(56, 189, 248, 0.15)";
          aiBadge.style.color = "#38bdf8";
          aiBadge.style.border = "1px solid rgba(56, 189, 248, 0.3)";
          aiToggleBtn.innerText = "Disable AI";
          aiToggleBtn.className = "btn btn-outline btn-sm";
        } else {
          aiBadge.innerText = "Disabled";
          aiBadge.style.background = "#334155";
          aiBadge.style.color = "#94a3b8";
          aiBadge.style.border = "none";
          aiToggleBtn.innerText = "Allow AI";
          aiToggleBtn.className = "btn btn-primary btn-sm";
        }
      } else {
        aiSection.style.display = "none";
      }
    }

    // Rejection reason banner
    const rejBanner = document.getElementById("modalTaskRejection");
    if (task.status === "open" && task.rejection_comment) {
      rejBanner.style.display = "block";
      rejBanner.innerHTML = `<strong>⚠️ Rejection History:</strong> ${escapeHtml(task.rejection_comment)}`;
    } else {
      rejBanner.style.display = "none";
    }

    // Attachments
    const attContainer = document.getElementById("modalTaskAttachments");
    attContainer.innerHTML = "";
    if (task.attachments && task.attachments.length > 0) {
      task.attachments.forEach(att => {
        const isImg = att.mime_type && att.mime_type.startsWith("image/");
        const fileUrl = `/api/v1/attachments/${att.id}`;
        const item = document.createElement("div");
        item.className = "attachment-badge";
        if (isImg) {
          item.innerHTML = `
            <a href="${fileUrl}" target="_blank" title="Click to view full size">
              <img src="${fileUrl}" class="image-thumb" alt="${escapeHtml(att.original_filename)}">
            </a>
            <div>
              <a href="${fileUrl}" target="_blank" style="color:var(--text-main); font-weight:500;">${escapeHtml(att.original_filename)}</a>
              <div style="color:var(--text-muted); font-size:0.7rem;">${Math.round(att.file_size / 1024)} KB • @${att.uploader}</div>
            </div>
          `;
        } else {
          item.innerHTML = `
            <span>📄</span>
            <div>
              <a href="${fileUrl}" download="${escapeHtml(att.original_filename)}" style="color:var(--text-main); font-weight:500;">${escapeHtml(att.original_filename)}</a>
              <div style="color:var(--text-muted); font-size:0.7rem;">${Math.round(att.file_size / 1024)} KB • @${att.uploader}</div>
            </div>
          `;
        }
        attContainer.appendChild(item);
      });
    } else {
      attContainer.innerHTML = `<span style="color: var(--text-muted); font-size: 0.8rem;">No attachments yet. Paste (Ctrl+V) or click below to upload.</span>`;
    }

    // Comments / Audit History
    const commContainer = document.getElementById("modalTaskComments");
    commContainer.innerHTML = "";
    if (task.comments && task.comments.length > 0) {
      task.comments.forEach(c => {
        const item = document.createElement("div");
        item.style.fontSize = "0.85rem";
        item.style.borderBottom = "1px solid var(--border-color)";
        item.style.padding = "6px 0";
        const isRej = c.comment_type === "rejection";
        item.innerHTML = `
          <div style="display:flex; justify-content:space-between; color:var(--text-muted); font-size:0.75rem;">
            <span style="font-weight:600; color:${isRej ? '#ef4444' : 'var(--accent-blue)'}">@${escapeHtml(c.author)} ${isRej ? '(Rejection)' : ''}</span>
            <span>${c.created_at.substring(0, 16).replace('T', ' ')}</span>
          </div>
          <div style="margin-top:2px;">${escapeHtml(c.content)}</div>
        `;
        commContainer.appendChild(item);
      });
    }

    document.getElementById("taskModal").style.display = "flex";
  } catch (err) {
    showToast(err.message, "error");
  }
}

async function addCommentToActiveTask() {
  if (!activeTaskModalId) return;
  const input = document.getElementById("newTaskCommentInput");
  const content = input.value.trim();
  if (!content) return;

  try {
    const res = await fetch(`/api/v1/tasks/${activeTaskModalId}/comments`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content })
    });
    if (res.ok) {
      input.value = "";
      openTaskModal(activeTaskModalId);
    }
  } catch (err) {
    showToast("Failed to add comment", "error");
  }
}

// ----------------- Attachments & Ctrl+V Paste -----------------

async function uploadFileToTask(taskId, file) {
  showToast(`Uploading ${file.name}...`, "info");
  const formData = new FormData();
  formData.append("file", file);

  try {
    const res = await fetch(`/api/v1/tasks/${taskId}/attachments`, {
      method: "POST",
      body: formData
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail);
    showToast("File uploaded successfully!", "success");
    if (activeTaskModalId === taskId) {
      openTaskModal(taskId);
    }
    loadBoard();
  } catch (err) {
    showToast(err.message, "error");
  }
}

// Global Ctrl+V handler
window.addEventListener("paste", async (e) => {
  const items = (e.clipboardData || e.originalEvent.clipboardData).items;
  let fileFound = null;
  for (let item of items) {
    if (item.kind === "file") {
      fileFound = item.getAsFile();
      break;
    }
  }
  if (!fileFound) return;

  e.preventDefault();

  // If a task modal is open, attach directly to that task
  if (activeTaskModalId && document.getElementById("taskModal").style.display !== "none") {
    await uploadFileToTask(activeTaskModalId, fileFound);
    return;
  }

  // If new task modal is open, stage it
  if (document.getElementById("createTaskModal").style.display !== "none") {
    stageNewTaskFile(fileFound);
    return;
  }

  // If on main board, open Create Task modal with this file staged
  openCreateTaskModal();
  stageNewTaskFile(fileFound);
  showToast("Image/file pasted into new task!", "info");
});

let stagedFile = null;
function stageNewTaskFile(file) {
  stagedFile = file;
  const preview = document.getElementById("newTaskStagedFile");
  if (preview) {
    preview.style.display = "block";
    preview.innerText = `📎 Attached: ${file.name || 'Pasted File'} (${Math.round(file.size / 1024)} KB)`;
  }
}

async function toggleActiveTaskAi() {
  if (!activeTaskModalId) return;
  await toggleTaskAi(activeTaskModalId);
  openTaskModal(activeTaskModalId);
}

async function toggleTaskAi(taskId) {
  try {
    const res = await fetch(`/api/v1/tasks/${taskId}/ai-toggle`, { method: "POST" });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail);
    showToast(data.message, "info");
    loadBoard();
  } catch (err) {
    showToast(err.message, "error");
  }
}

// ----------------- Create Task -----------------

function openCreateTaskModal() {
  stagedFile = null;
  document.getElementById("newTaskTitle").value = "";
  document.getElementById("newTaskDesc").value = "";
  const aiGroup = document.getElementById("createTaskAiGroup");
  const aiCheckbox = document.getElementById("newTaskAiEnabled");
  if (aiGroup) {
    aiGroup.style.display = aiModeActive ? "block" : "none";
  }
  if (aiCheckbox) {
    aiCheckbox.checked = false;
  }
  const preview = document.getElementById("newTaskStagedFile");
  if (preview) preview.style.display = "none";
  document.getElementById("createTaskModal").style.display = "flex";
}

async function handleCreateTask(e) {
  if (e) e.preventDefault();
  const title = document.getElementById("newTaskTitle").value.trim();
  const description = document.getElementById("newTaskDesc").value.trim();
  const aiEnabled = document.getElementById("newTaskAiEnabled")?.checked ? 1 : 0;
  if (!title) {
    showToast("Title is required", "error");
    return;
  }

  try {
    const res = await fetch("/api/v1/tasks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title, description, ai_enabled: aiEnabled })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail);

    const taskId = data.task.id;
    // If we have a staged file from paste or drop
    if (stagedFile) {
      await uploadFileToTask(taskId, stagedFile);
    }

    document.getElementById("createTaskModal").style.display = "none";
    showToast(`Task #${taskId} created!`, "success");
    loadBoard();
  } catch (err) {
    showToast(err.message, "error");
  }
}

// ----------------- API Keys Management -----------------

async function openApiKeysModal() {
  document.getElementById("apiKeysModal").style.display = "flex";
  document.getElementById("newGeneratedKeyArea").style.display = "none";
  loadApiKeysList();
}

async function loadApiKeysList() {
  try {
    const res = await fetch("/api/v1/keys");
    const data = await res.json();
    const container = document.getElementById("apiKeysList");
    container.innerHTML = "";

    if (!data.keys || data.keys.length === 0) {
      container.innerHTML = `<span style="color:var(--text-muted); font-size:0.85rem;">No active API keys found. Click below to generate one.</span>`;
      return;
    }

    data.keys.forEach(k => {
      const row = document.createElement("div");
      row.style.display = "flex";
      row.style.alignItems = "center";
      row.style.justifyContent = "space-between";
      row.style.padding = "8px 10px";
      row.style.background = "#0f172a";
      row.style.borderRadius = "var(--radius-sm)";
      row.style.border = "1px solid var(--border-color)";
      row.style.fontSize = "0.85rem";

      row.innerHTML = `
        <div>
          <div style="font-weight:600;">${escapeHtml(k.name)} <span style="font-size:0.75rem; color:var(--text-muted); font-weight:normal;">(${k.created_at.substring(0, 10)})</span></div>
          <div style="font-family:monospace; color:var(--accent-blue);">${k.key_prefix}</div>
        </div>
        <button class="btn btn-danger btn-sm" onclick="revokeApiKey('${k.id}')">Revoke</button>
      `;
      container.appendChild(row);
    });
  } catch (err) {
    console.error("Failed to load API keys:", err);
  }
}

async function generateApiKey() {
  const name = document.getElementById("newKeyNameInput").value.trim() || "Web Key";
  try {
    const res = await fetch("/api/v1/keys", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail);

    document.getElementById("newKeyNameInput").value = "";
    const area = document.getElementById("newGeneratedKeyArea");
    const keyVal = document.getElementById("newGeneratedKeyValue");
    keyVal.value = data.api_key;
    area.style.display = "block";

    loadApiKeysList();
    showToast("API Key generated!", "success");
  } catch (err) {
    showToast(err.message, "error");
  }
}

function copyApiKeyToClipboard() {
  const val = document.getElementById("newGeneratedKeyValue").value;
  navigator.clipboard.writeText(val);
  showToast("Copied API key to clipboard!", "success");
}

async function revokeApiKey(keyId) {
  if (!confirm("Are you sure you want to revoke this API key?")) return;
  try {
    const res = await fetch(`/api/v1/keys/${keyId}`, { method: "DELETE" });
    if (res.ok) {
      showToast("API key revoked", "info");
      loadApiKeysList();
    }
  } catch (err) {
    showToast("Failed to revoke key", "error");
  }
}

// ----------------- AI Chat Functions -----------------

function openAiChatModal() {
  const modal = document.getElementById("aiChatModal");
  if (!modal) return;
  modal.style.display = "flex";
  renderAiTaskChips();
}

function renderAiTaskChips() {
  const container = document.getElementById("aiTaskChipsList");
  if (!container) return;
  container.innerHTML = "";

  const allSelected = document.getElementById("aiSelectAllTasks")?.checked;

  if (allTasks.length === 0) {
    container.innerHTML = `<span style="color:var(--text-muted); font-size:0.75rem;">No tasks on board.</span>`;
    return;
  }

  allTasks.forEach(task => {
    const chip = document.createElement("div");
    const isSel = allSelected || selectedAiTaskIds.has(task.id);
    chip.className = `ai-task-chip ${isSel ? 'selected' : ''}`;
    chip.innerHTML = `<span>#${task.id}</span> <span>${escapeHtml(task.title.substring(0, 22))}</span>`;
    chip.onclick = () => {
      if (document.getElementById("aiSelectAllTasks").checked) {
        document.getElementById("aiSelectAllTasks").checked = false;
        selectedAiTaskIds.clear();
      }
      if (selectedAiTaskIds.has(task.id)) {
        selectedAiTaskIds.delete(task.id);
      } else {
        selectedAiTaskIds.add(task.id);
      }
      renderAiTaskChips();
    };
    container.appendChild(chip);
  });
}

function toggleSelectAllAiTasks(checked) {
  if (checked) {
    selectedAiTaskIds.clear();
  }
  renderAiTaskChips();
}

async function sendAiChatMessage() {
  const input = document.getElementById("aiChatInput");
  const prompt = input.value.trim();
  if (!prompt) return;

  input.value = "";
  const messagesBox = document.getElementById("aiChatMessages");

  // Append user message
  const userMsg = document.createElement("div");
  userMsg.className = "ai-msg user";
  userMsg.innerText = prompt;
  messagesBox.appendChild(userMsg);

  // Append typing indicator
  const typingIndicator = document.createElement("div");
  typingIndicator.className = "ai-typing-indicator";
  typingIndicator.id = "aiTypingIndicator";
  typingIndicator.innerHTML = `
    <span>AI Agent thinking</span>
    <span class="ai-dot"></span>
    <span class="ai-dot"></span>
    <span class="ai-dot"></span>
  `;
  messagesBox.appendChild(typingIndicator);
  messagesBox.scrollTop = messagesBox.scrollHeight;

  // Determine task context
  let taskIdsStr = "";
  if (document.getElementById("aiSelectAllTasks")?.checked) {
    taskIdsStr = allTasks.map(t => t.id).join(",");
  } else {
    taskIdsStr = Array.from(selectedAiTaskIds).join(",");
  }

  try {
    const res = await fetch("/api/v1/ai/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt, task_ids: taskIdsStr })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Failed to start AI chat");

    const queryId = data.query_id;
    pollChatQuery(queryId, typingIndicator);
  } catch (err) {
    typingIndicator.remove();
    const errMsg = document.createElement("div");
    errMsg.className = "ai-msg agent";
    errMsg.style.borderColor = "var(--accent-red)";
    errMsg.innerText = `❌ Error: ${err.message}`;
    messagesBox.appendChild(errMsg);
    messagesBox.scrollTop = messagesBox.scrollHeight;
  }
}

async function pollChatQuery(queryId, indicatorEl) {
  const messagesBox = document.getElementById("aiChatMessages");
  const maxAttempts = 60; // 2 minutes
  let attempts = 0;

  const timer = setInterval(async () => {
    attempts++;
    try {
      const res = await fetch(`/api/v1/ai/chat/${queryId}`);
      if (!res.ok) return;
      const data = await res.json();

      if (data.status === "done") {
        clearInterval(timer);
        indicatorEl.remove();
        const respMsg = document.createElement("div");
        respMsg.className = "ai-msg agent";
        respMsg.innerText = data.response || "No response received from agent.";
        messagesBox.appendChild(respMsg);
        messagesBox.scrollTop = messagesBox.scrollHeight;
      } else if (data.status === "failed") {
        clearInterval(timer);
        indicatorEl.remove();
        const respMsg = document.createElement("div");
        respMsg.className = "ai-msg agent";
        respMsg.style.borderColor = "var(--accent-red)";
        respMsg.innerText = `❌ Agent response failed: ${data.response || 'Unknown error'}`;
        messagesBox.appendChild(respMsg);
        messagesBox.scrollTop = messagesBox.scrollHeight;
      } else if (attempts >= maxAttempts) {
        clearInterval(timer);
        indicatorEl.remove();
        const respMsg = document.createElement("div");
        respMsg.className = "ai-msg agent";
        respMsg.innerText = "⏳ AI Agent query timed out. Ensure the autonomous worker is running on server 'andrii'.";
        messagesBox.appendChild(respMsg);
        messagesBox.scrollTop = messagesBox.scrollHeight;
      }
    } catch (e) {
      console.error("Error polling chat query:", e);
    }
  }, 2000);
}

// ----------------- Helpers & Polling -----------------

function escapeHtml(text) {
  if (!text) return "";
  const map = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' };
  return String(text).replace(/[&<>"']/g, m => map[m]);
}

String.prototype.capitalize = function() {
  return this.charAt(0).toUpperCase() + this.slice(1);
};

function startPolling() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(() => {
    if (autoRefresh) {
      loadBoard();
    }
  }, 5000);
}

// Close modals when clicking outside
window.addEventListener("click", (e) => {
  if (e.target.classList.contains("modal-overlay") && e.target.id !== "authModal") {
    e.target.style.display = "none";
  }
});

// Setup profile selection buttons in login modal
document.querySelectorAll(".profile-option").forEach(opt => {
  opt.addEventListener("click", () => {
    document.querySelectorAll(".profile-option").forEach(o => o.classList.remove("selected"));
    opt.classList.add("selected");
  });
});

// Boot
window.addEventListener("DOMContentLoaded", () => {
  checkAuth();
});
