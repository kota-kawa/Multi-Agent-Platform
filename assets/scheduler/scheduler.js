/* Scheduler Agent UI enhancements (IoT-Agent chat & model selection parity) */

const $ = (sel, parent = document) => parent.querySelector(sel);
const nowTime = () => {
  const d = new Date();
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  return `${hh}:${mm}`;
};
const escapeHtml = (s) =>
  String(s).replace(/[&<>"']/g, (m) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[m]
  ));

function withSchedulerApiPrefix(path = "/") {
  const normalized = path.startsWith("/") ? path : `/${path}`;
  return `/scheduler_agent${normalized}`.replace(/\/{2,}/g, "/");
}

function stripPrefixFromPath(path) {
  if (!proxyPrefix) return path || "/";
  const cleaned = proxyPrefix.startsWith("/") ? proxyPrefix : `/${proxyPrefix}`;
  if (path && path.startsWith(cleaned)) {
    const stripped = path.slice(cleaned.length);
    return stripped.startsWith("/") ? stripped : `/${stripped || ""}`;
  }
  return path || "/";
}

/** ---------- モデル選択 ---------- */
const DEFAULT_MODEL = { provider: "openai", model: "gpt-4.1", base_url: "" };
let availableModels = [];
let currentModel = { ...DEFAULT_MODEL };

const modelSelectEl = $("#modelSelect");

function populateModelSelect(){
  if(!modelSelectEl) return;
  modelSelectEl.innerHTML = "";

  const options = availableModels.length
    ? availableModels
    : [{ ...DEFAULT_MODEL, label: "Default (OpenAI gpt-4.1)" }];

  options.forEach((m) => {
    const option = document.createElement("option");
    option.value = `${m.provider}:${m.model}`;
    option.textContent = m.label || `${m.provider}:${m.model}`;
    if(m.provider === currentModel.provider && m.model === currentModel.model){
      option.selected = true;
    }
    modelSelectEl.appendChild(option);
  });
}

async function loadModelOptions(){
  if(!modelSelectEl) return;

  try{
    const res = await fetch(withSchedulerApiPrefix("/api/models"), { cache: "no-store" });
    if(!res.ok){
      throw new Error(`HTTP ${res.status}`);
    }
    const data = await res.json();
    if(Array.isArray(data.models)){
      availableModels = data.models.filter(
        (m) => m && m.provider && m.model
      );
    }
    const current = data.current;
    if(current && typeof current === "object" && current.provider && current.model){
      currentModel = {
        provider: current.provider,
        model: current.model,
        base_url: typeof current.base_url === "string" ? current.base_url : "",
      };
    }
  }catch(err){
    console.error("Failed to load model options", err);
    availableModels = [];
    currentModel = { ...DEFAULT_MODEL };
  }

  populateModelSelect();
}

async function handleModelChange(){
  if(!modelSelectEl) return;
  const fallbackValue = `${DEFAULT_MODEL.provider}:${DEFAULT_MODEL.model}`;
  const [providerRaw, modelRaw] = (modelSelectEl.value || fallbackValue).split(":");
  const provider = providerRaw || DEFAULT_MODEL.provider;
  const model = modelRaw || DEFAULT_MODEL.model;
  const baseUrl = typeof currentModel.base_url === "string" ? currentModel.base_url : "";
  currentModel = { provider, model, base_url: baseUrl };

  try{
    const res = await fetch(withSchedulerApiPrefix("/model_settings"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(currentModel),
    });
    if(!res.ok){
      throw new Error(`HTTP ${res.status}`);
    }
    console.log("Model updated successfully:", currentModel);
  }catch(err){
    console.error("Failed to update model:", err);
    alert(`モデルの更新に失敗しました: ${err.message}`);
  }
}

if(modelSelectEl){
  modelSelectEl.addEventListener("change", handleModelChange);
}

/** ---------- チャット ---------- */
const logEl = $("#chatLog");
const formEl = $("#chatForm");
const inputEl = $("#chatInput");
const sendBtn = $("#sendBtn");
const pauseBtn = $("#pauseBtn");
const chatResetBtn = $("#chatResetBtn");
const INITIAL_GREETING = "こんにちは！スケジューラーの確認やタスク登録をお手伝いします。やりたいことを日本語で教えてください。";
let isPaused = false;
let isSending = false;
const chatHistory = [];

function updateChatControls(){
  if(!sendBtn || !inputEl) return;
  const disableSend = isPaused || isSending;
  sendBtn.disabled = disableSend;
  inputEl.disabled = isPaused;
  if(pauseBtn){
    pauseBtn.classList.toggle("is-active", isPaused);
    pauseBtn.setAttribute("aria-pressed", String(isPaused));
  }
}

function pushMessage(role, text, timestamp = null) {
  chatHistory.push({ role, content: text });
  const timeDisplay = timestamp ? timestamp : nowTime();
  const item = document.createElement("div");
  item.className = `message message--${role}`;
  item.innerHTML = `
    <div class="message__avatar">${role === "user" ? "👤" : "🤖"}</div>
    <div>
      <div class="message__bubble">${escapeHtml(text)}</div>
      <div class="message__meta">${role === "user" ? "あなた" : "LLM"} ・ ${timeDisplay}</div>
    </div>
  `;
  logEl.appendChild(item);
  logEl.scrollTop = logEl.scrollHeight;
}

async function loadChatHistory() {
  try {
    const res = await fetch(withSchedulerApiPrefix("/api/chat/history"));
    if (!res.ok) return; // If failed, just start fresh
    const data = await res.json();
    
    if (data.history && data.history.length > 0) {
      // Clear initial greeting if any (though we haven't added it yet in this flow)
      logEl.innerHTML = "";
      chatHistory.length = 0; 
      
      data.history.forEach(msg => {
        // Format timestamp from ISO to HH:MM
        let timeStr = "";
        try {
          const d = new Date(msg.timestamp);
          const hh = String(d.getHours()).padStart(2, "0");
          const mm = String(d.getMinutes()).padStart(2, "0");
          timeStr = `${hh}:${mm}`;
        } catch (e) {
          timeStr = nowTime();
        }
        pushMessage(msg.role, msg.content, timeStr);
      });
    } else {
      pushMessage("assistant", INITIAL_GREETING);
    }
  } catch (err) {
    console.error("Failed to load chat history", err);
    pushMessage("assistant", INITIAL_GREETING);
  }
}

async function requestAssistantResponse(){
  const payload = {
    messages: chatHistory.map(({ role, content }) => ({ role, content })),
  };

  const res = await fetch(withSchedulerApiPrefix("/api/chat"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  if(!res.ok){
    const errText = await res.text();
    throw new Error(errText || `HTTP ${res.status}`);
  }

  return await res.json();
}

async function refreshView(modifiedIds) {
  const currentPath = window.location.pathname; // This will be like /scheduler-ui or /scheduler-ui/day/YYYY-MM-DD
  const search = window.location.search;
  const timestamp = Date.now();
  
  // Helper to highlight elements
  const highlightIds = (ids) => {
    if (Array.isArray(ids)) {
        ids.forEach(id => {
            const el = document.getElementById(id);
            if (el) {
                el.classList.remove('flash-highlight'); // Reset to allow replay
                void el.offsetWidth; // trigger reflow
                el.classList.add('flash-highlight');
                setTimeout(() => el.classList.remove('flash-highlight'), 2000);
                // Scroll into view if needed, but maybe skip for better UX
            }
        });
    }
  };

  // 1. Calendar View Refresh (Index)
  // Check if currentPath is /scheduler-ui or /scheduler-ui/index
  // Note: /scheduler-ui/index is handled by scheduler_index function which returns scheduler_index.html, not partial.
  // The partial is for AJAX calls.
  if (currentPath === '/scheduler-ui') { // Only for main calendar view
      try {
          const separator = search ? '&' : '?';
          // Call the Multi-Agent-Platform's partial calendar route
          const url = `/scheduler-ui/calendar_partial${search}${separator}t=${timestamp}`;
          
          const res = await fetch(url);
          if (res.ok) {
              const html = await res.text();
              const grid = document.getElementById('calendar-grid');
              if (grid) {
                  const temp = document.createElement('div');
                  temp.innerHTML = html;
                  const newGrid = temp.firstElementChild;
                  grid.replaceWith(newGrid);
              }
          }
      } catch (err) {
          console.error("Calendar refresh failed:", err);
      }
      return; 
  }

  // 2. Day View Refresh
  if (currentPath.startsWith('/scheduler-ui/day/')) {
      // Refresh Timeline
      try {
          // Call the Multi-Agent-Platform's partial timeline route
          const url = `${currentPath}/timeline${search ? search + '&' : '?'}t=${timestamp}`;
          
          const res = await fetch(url);
          if (res.ok) {
              const html = await res.text();
              const container = document.getElementById('schedule-container');
              if (container) {
                  const temp = document.createElement('div');
                  temp.innerHTML = html;
                  const newContainer = temp.firstElementChild;
                  container.replaceWith(newContainer);
              }
          }
      } catch (err) {
          console.error("Timeline refresh failed:", err);
      }

      // Refresh Daily Log
      try {
          // Call the Multi-Agent-Platform's partial log route
          const url = `${currentPath}/log_partial${search ? search + '&' : '?'}t=${timestamp}`;
          
          const res = await fetch(url);
          if (res.ok) {
              const html = await res.text();
              const wrapper = document.getElementById('daily-log-wrapper');
              if (wrapper) {
                  wrapper.innerHTML = html;
              }
          }
      } catch (err) {
          console.error("Log refresh failed:", err);
      }

      // Apply highlights after DOM updates
      setTimeout(() => highlightIds(modifiedIds), 50);
  }
}

if(formEl){
  formEl.addEventListener("submit", async (e) => {
    e.preventDefault();
    if(isPaused || isSending) return;
    const text = inputEl.value.trim();
    if(!text) return;
    pushMessage("user", text);
    inputEl.value = "";
    isSending = true;
    updateChatControls();

    try{
      const data = await requestAssistantResponse();
      const reply = typeof data.reply === "string" ? data.reply : "";
      const cleanReply = reply && reply.trim();
      pushMessage("assistant", cleanReply || "了解しました。");
      
      if (data.should_refresh) {
          await refreshView(data.modified_ids);
      }
    }catch(err){
      pushMessage("assistant", `エラーが発生しました: ${err.message}`);
    }finally{
      isSending = false;
      updateChatControls();
    }
  });
}

if(pauseBtn){
  pauseBtn.addEventListener("click", () => {
    isPaused = !isPaused;
    updateChatControls();
    if(!isPaused){
      inputEl.focus();
    }
  });
}

if(chatResetBtn){
  chatResetBtn.addEventListener("click", async () => {
    if(!confirm("チャット履歴を削除しますか？")) return;
    
    try {
      await fetch(withSchedulerApiPrefix("/api/chat/history"), { method: "DELETE" });
    } catch (e) {
      console.error("Failed to clear history", e);
    }
    
    logEl.innerHTML = "";
    chatHistory.length = 0;
    pushMessage("assistant", INITIAL_GREETING);
    isPaused = false;
    isSending = false;
    updateChatControls();
  });
}

(async function init(){
  await loadModelOptions();
  await handleModelChange();
  // pushMessage("assistant", INITIAL_GREETING); // Moved to loadChatHistory logic
  await loadChatHistory();
  updateChatControls();
})();
