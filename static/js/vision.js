(() => {
  const chatEl = document.getElementById("visionChat");
  const loadingEl = document.getElementById("visionLoading");
  const inputEl = document.getElementById("visionInput");
  const sendBtn = document.getElementById("visionSendBtn");
  const clearBtn = document.getElementById("visionClearBtn");
  const planBtn = document.getElementById("visionPlanBtn");
  const summarizeBtn = document.getElementById("visionSummarizeBtn");
  const focusBtn = document.getElementById("visionFocusBtn");
  const fileInputEl = document.getElementById("visionFileInput");
  const uploadSummarizeBtn = document.getElementById("visionUploadSummarizeBtn");
  const fileMetaEl = document.getElementById("visionFileMeta");
  const subjectEl = document.getElementById("visionSubject");
  const studyTimeEl = document.getElementById("visionStudyTime");

  if (!chatEl || !inputEl || !sendBtn) {
    return;
  }

  let requestInFlight = false;
  let selectedFile = null;
  let currentChatId = "";

  const actionButtons = [sendBtn, clearBtn, planBtn, summarizeBtn, focusBtn, uploadSummarizeBtn].filter(Boolean);

  function autoScrollChat() {
    chatEl.scrollTop = chatEl.scrollHeight;
  }

  function addMessage(role, text) {
    const node = document.createElement("div");
    node.className = `vision-msg ${role === "user" ? "vision-msg-user" : "vision-msg-ai"}`;
    node.textContent = text;
    chatEl.appendChild(node);
    autoScrollChat();
  }

  function setLoadingState(isLoading) {
    requestInFlight = isLoading;
    if (loadingEl) {
      loadingEl.hidden = !isLoading;
    }
    actionButtons.forEach((btn) => {
      btn.disabled = isLoading;
    });
    if (fileInputEl) {
      fileInputEl.disabled = isLoading;
    }
  }

  function getFocusScore() {
    const gauge = document.getElementById("focusValue");
    if (!gauge) {
      return null;
    }
    const parsed = Number(gauge.textContent || 0);
    if (!Number.isFinite(parsed)) {
      return null;
    }
    return Math.max(0, Math.min(100, Math.round(parsed)));
  }

  function getSubject() {
    return String(subjectEl?.value || "General Study Help").trim();
  }

  function getStudyTime() {
    return String(studyTimeEl?.value || "").trim();
  }

  function ensureInputForAction(action, messageText) {
    if (messageText) {
      return messageText;
    }
    if (action === "focus_help") {
      return "I can't focus.";
    }
    if (action === "study_plan") {
      const subject = getSubject();
      const studyTime = getStudyTime();
      return `Create a study plan for ${subject} with ${studyTime || "my available time"}.`;
    }
    return "";
  }

  async function postJsonWithTimeout(url, payload, timeoutMs = 70000) {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
    try {
      return await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        signal: controller.signal,
      });
    } finally {
      clearTimeout(timeoutId);
    }
  }

  function generateFallbackChatId() {
    if (window.crypto?.randomUUID) {
      return window.crypto.randomUUID().replace(/-/g, "");
    }
    return `chat_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`;
  }

  async function ensureChatSession(forceNew = false) {
    if (!forceNew && currentChatId) {
      return currentChatId;
    }
    try {
      const response = await postJsonWithTimeout("/vision-ai/session/new", {}, 12000);
      const data = await response.json().catch(() => ({}));
      if (response.ok && data.chat_id) {
        currentChatId = String(data.chat_id);
        return currentChatId;
      }
    } catch (error) {
      // Fall through to local id generation.
    }
    currentChatId = generateFallbackChatId();
    return currentChatId;
  }

  async function sendVisionMessage(action = "chat") {
    if (requestInFlight) {
      return;
    }

    const rawText = String(inputEl.value || "").trim();
    const message = ensureInputForAction(action, rawText);
    const subject = getSubject();
    const studyTime = getStudyTime();
    const focusScore = getFocusScore();

    if (!message && action !== "summarize") {
      addMessage("ai", "Please enter a question so I can help.");
      return;
    }

    if (action === "summarize" && !rawText) {
      addMessage("ai", "Paste text in the input box, then click Summarize.");
      return;
    }

    await ensureChatSession(false);
    addMessage("user", message);
    setLoadingState(true);

    const payload = {
      chat_id: currentChatId,
      action,
      message,
      subject,
      study_time: studyTime,
      focus_score: focusScore,
    };

    if (action === "summarize") {
      payload.text = rawText;
    }

    try {
      const response = await postJsonWithTimeout("/vision-ai", payload);

      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        const errorMessage = data.error || "Vision is unavailable right now.";
        addMessage("ai", `Error: ${errorMessage}`);
        return;
      }

      if (data.chat_id) {
        currentChatId = String(data.chat_id);
      }
      addMessage("ai", String(data.reply || "No response generated."));
      if (action !== "summarize") {
        inputEl.value = "";
      }
    } catch (error) {
      if (error?.name === "AbortError") {
        addMessage("ai", "Vision is taking too long. Please try a shorter prompt or retry.");
      } else {
        addMessage("ai", "Network error while contacting Vision. Please retry.");
      }
    } finally {
      setLoadingState(false);
      inputEl.focus();
    }
  }

  function refreshFileMeta() {
    if (!fileMetaEl) {
      return;
    }
    if (!selectedFile) {
      fileMetaEl.textContent = "No file selected.";
      return;
    }
    const sizeKb = Math.max(1, Math.round((selectedFile.size || 0) / 1024));
    fileMetaEl.textContent = `Selected: ${selectedFile.name} (${sizeKb} KB)`;
  }

  async function summarizeUploadedFile() {
    if (requestInFlight) {
      return;
    }
    if (!selectedFile) {
      addMessage("ai", "Select a PDF, TXT, or MD file first.");
      return;
    }

    addMessage("user", `Summarize file: ${selectedFile.name}`);
    setLoadingState(true);

    const form = new FormData();
    form.append("file", selectedFile);
    form.append("subject", getSubject());
    const focusScore = getFocusScore();
    if (focusScore !== null && Number.isFinite(focusScore)) {
      form.append("focus_score", String(focusScore));
    }

    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 90000);

    try {
      const response = await fetch("/vision-ai/upload", {
        method: "POST",
        body: form,
        signal: controller.signal,
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        const errorMessage = data.error || "Unable to summarize file.";
        addMessage("ai", `Error: ${errorMessage}`);
        return;
      }
      addMessage("ai", String(data.reply || "No summary generated."));
    } catch (error) {
      if (error?.name === "AbortError") {
        addMessage("ai", "File summary timed out. Try a smaller file or retry.");
      } else {
        addMessage("ai", "File upload failed. Please retry.");
      }
    } finally {
      clearTimeout(timeoutId);
      setLoadingState(false);
      inputEl.focus();
    }
  }

  async function clearChat() {
    await ensureChatSession(true);
    chatEl.innerHTML = "";
    addMessage("ai", "Hi, I am Vision. Ask me anything, generate a study plan, summarize notes, or ask for focus help.");
    inputEl.focus();
  }

  sendBtn.addEventListener("click", () => {
    sendVisionMessage("chat");
  });

  planBtn?.addEventListener("click", () => {
    sendVisionMessage("study_plan");
  });

  summarizeBtn?.addEventListener("click", () => {
    sendVisionMessage("summarize");
  });

  focusBtn?.addEventListener("click", () => {
    sendVisionMessage("focus_help");
  });

  uploadSummarizeBtn?.addEventListener("click", () => {
    summarizeUploadedFile();
  });

  fileInputEl?.addEventListener("change", () => {
    const file = fileInputEl.files && fileInputEl.files.length ? fileInputEl.files[0] : null;
    selectedFile = file || null;
    refreshFileMeta();
  });

  clearBtn?.addEventListener("click", () => {
    clearChat();
  });

  inputEl.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      sendVisionMessage("chat");
    }
  });

  refreshFileMeta();
  clearChat();
})();
