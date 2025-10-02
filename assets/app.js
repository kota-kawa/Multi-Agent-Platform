/* Single Page UI logic
 * - View switching (ブラウザ / IoT / 要約チャット)
 * - Browser stage (iframe embed / pseudo noVNC)
 * - IoT dashboard (mock devices, live chart, localStorage persist)
 * - Chat + extractive summarizer (pure client-side)
 */

const $ = (q, c = document) => c.querySelector(q);
const $$ = (q, c = document) => Array.from(c.querySelectorAll(q));

const layoutEl = $(".layout");
const sidebarEl = $(".sidebar");
const sidebarToggle = $(".sidebar-toggle");

let sidebarTogglePositionRaf = null;
const updateSidebarTogglePosition = () => {
  if (!layoutEl || !sidebarEl) return;

  const sidebarRect = sidebarEl.getBoundingClientRect();
  const layoutRect = layoutEl.getBoundingClientRect();
  if (sidebarRect.height <= 0) return;

  const offset = sidebarRect.top - layoutRect.top + sidebarRect.height / 2;
  layoutEl.style.setProperty("--sidebar-toggle-top", `${offset}px`);
};

const scheduleSidebarTogglePosition = () => {
  if (!layoutEl || !sidebarEl) return;
  if (sidebarTogglePositionRaf !== null) return;

  sidebarTogglePositionRaf = requestAnimationFrame(() => {
    sidebarTogglePositionRaf = null;
    updateSidebarTogglePosition();
  });
};

/* ---------- View switching ---------- */
const views = {
  general: $("#view-general"),
  browser: $("#view-browser"),
  iot: $("#view-iot"),
  chat: $("#view-chat"),
};

const appTitle = $("#appTitle");
const navButtons = $$(".nav-btn");
navButtons.forEach(btn => {
  btn.addEventListener("click", () => {
    const view = btn.dataset.view;
    navButtons.forEach(b => b.classList.toggle("active", b === btn));
    Object.entries(views).forEach(([k, el]) => el.classList.toggle("active", k === view));
    const titles = {
      general: "一般ビュー",
      browser: "リモートブラウザ",
      iot: "IoT ダッシュボード",
      chat: "要約チャット",
    };
    appTitle.textContent = titles[view] ?? "リモートブラウザ";
    if (view === "browser") {
      setChatMode("browser");
      ensureBrowserAgentInitialized({ showLoading: true });
    } else {
      setChatMode("general");
      if (view === "chat") {
        ensureChatInitialized({ showLoadingSummary: true });
      }
    }
    scheduleSidebarTogglePosition();
  });
});

/* ---------- Sidebar toggle ---------- */
if (layoutEl && sidebarToggle && sidebarEl) {
  const setSidebarCollapsed = collapsed => {
    layoutEl.classList.toggle("sidebar-collapsed", collapsed);
    const label = collapsed ? "サイドバーを表示する" : "サイドバーを折りたたむ";
    sidebarToggle.setAttribute("aria-expanded", String(!collapsed));
    sidebarToggle.setAttribute("aria-label", label);
    sidebarToggle.setAttribute("title", label);
    scheduleSidebarTogglePosition();
  };

  setSidebarCollapsed(false);

  sidebarToggle.addEventListener("click", () => {
    const collapsed = !layoutEl.classList.contains("sidebar-collapsed");
    setSidebarCollapsed(collapsed);
  });

  const mq = window.matchMedia("(max-width: 960px)");
  const handleMq = event => {
    if (event.matches) {
      setSidebarCollapsed(false);
    }
  };

  handleMq(mq);
  if (typeof mq.addEventListener === "function") mq.addEventListener("change", handleMq);
  else if (typeof mq.addListener === "function") mq.addListener(handleMq);

  window.addEventListener("resize", scheduleSidebarTogglePosition);
  window.addEventListener("scroll", scheduleSidebarTogglePosition, { passive: true });

  if (typeof ResizeObserver === "function") {
    const sidebarResizeObserver = new ResizeObserver(scheduleSidebarTogglePosition);
    sidebarResizeObserver.observe(sidebarEl);
  }
}

/* ---------- Browser stage (noVNC 風) ---------- */
const stage = $("#browserStage");
const fullscreenBtn = $("#fullscreenBtn");

let currentIframe = null;

function resolveBrowserEmbedUrl() {
  const sanitize = value => (typeof value === "string" ? value.trim() : "");
  const hasProtocol = value => /^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(value);

  let queryValue = "";
  try {
    queryValue = new URLSearchParams(window.location.search).get("browser_embed_url") || "";
  } catch (_) {
    queryValue = "";
  }

  const sources = [
    sanitize(queryValue),
    sanitize(window.BROWSER_EMBED_URL),
    sanitize(document.querySelector("meta[name='browser-embed-url']")?.content),
  ];

  for (const candidate of sources) {
    if (!candidate) continue;
    if (hasProtocol(candidate)) {
      return candidate;
    }
    try {
      return new URL(candidate, window.location.origin).toString();
    } catch (_) {
      continue;
    }
  }

  return "http://127.0.0.1:7900/?autoconnect=1&resize=scale";
}

const BROWSER_EMBED_URL = resolveBrowserEmbedUrl();

function ensureBrowserIframe() {
  if (!stage) return;
  const url = BROWSER_EMBED_URL;
  if (!url) return;

  let iframe = stage.querySelector("iframe");
  if (!iframe) {
    stage.innerHTML = "";
    iframe = document.createElement("iframe");
    iframe.setAttribute("title", "埋め込みブラウザ");
    iframe.setAttribute("allow", "fullscreen");
    iframe.setAttribute("allowfullscreen", "");
    stage.appendChild(iframe);
  }

  if (iframe.src !== url) {
    iframe.src = url;
  }

  currentIframe = iframe;
}

ensureBrowserIframe();

if (fullscreenBtn) {
  fullscreenBtn.addEventListener("click", () => {
    const el = currentIframe ?? stage;
    if (document.fullscreenElement) document.exitFullscreen();
    else el?.requestFullscreen?.();
  });
}

/* ---------- IoT Dashboard ---------- */

const deviceGrid = $("#deviceGrid");
const resetIoTBtn = $("#resetIoTBtn");
const addDeviceBtn = $("#addDeviceBtn");

const LS_KEY_IOT = "spa_iot_devices_v1";

const ICON_SENSOR = `<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M12 2a3 3 0 0 1 3 3v9.05a4.5 4.5 0 1 1-6 0V5a3 3 0 0 1 3-3zm0 16.5a2.5 2.5 0 0 0 2.5-2.5 2.5 2.5 0 0 0-5 0 2.5 2.5 0 0 0 2.5 2.5z"/><path fill="currentColor" d="M11 6h2v6h-2z"/></svg>`;
const ICON_ACTUATOR = `<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M11 21h-1l1-7H6l7-12h1l-1 7h5l-7 12z"/></svg>`;

function defaultDevices() {
  return [
    { id: crypto.randomUUID(), name: "温度センサー", type: "sensor", unit: "°C", on: true, value: 24.3 },
    { id: crypto.randomUUID(), name: "湿度センサー", type: "sensor", unit: "%", on: true, value: 55.2 },
    { id: crypto.randomUUID(), name: "ランプ", type: "actuator", on: false },
    { id: crypto.randomUUID(), name: "ファン", type: "actuator", on: false },
  ];
}

let devices = loadJSON(LS_KEY_IOT) ?? defaultDevices();
saveJSON(LS_KEY_IOT, devices);

function loadJSON(key) {
  try { return JSON.parse(localStorage.getItem(key)); }
  catch { return null; }
}
function saveJSON(key, val) {
  try { localStorage.setItem(key, JSON.stringify(val)); }
  catch {}
}

function renderDevices() {
  deviceGrid.innerHTML = "";
  devices.forEach(d => {
    const card = document.createElement("div");
    card.className = "device-card";
    card.dataset.id = d.id;
    card.innerHTML = `
      <div class="device-card-header">
        <div class="device-title">
          <span class="device-icon ${d.type}" aria-hidden="true">${getDeviceIcon(d)}</span>
          <div class="device-meta">
            <div class="device-name">${escapeHTML(d.name)}</div>
            <div class="device-type">${d.type === "sensor" ? "センサー" : "アクチュエータ"}</div>
          </div>
        </div>
        <div class="device-tools">
          <button class="icon-btn btn-rename" type="button" title="名称変更" aria-label="名称変更">✎</button>
          <button class="icon-btn btn-delete" type="button" title="削除" aria-label="削除">🗑</button>
        </div>
      </div>
      <div class="device-body">
        <div class="device-stat">
          <span class="device-stat-label">${d.type === "sensor" ? "現在値" : "現在の状態"}</span>
          ${d.type === "sensor"
            ? `<span class="device-reading">${formatReading(d)}</span>`
            : `<span class="device-status-pill ${d.on ? "on" : "off"}"><span class="status-dot ${d.on ? "status-on" : "status-off"}"></span>${d.on ? "ON" : "OFF"}</span>`
          }
        </div>
        <div class="device-controls">
          ${d.type === "sensor"
            ? `<button class="btn subtle btn-calibrate" type="button">校正</button>`
            : `<div class="switch ${d.on ? "on" : ""}" role="switch" aria-checked="${d.on}"></div>`
          }
        </div>
      </div>
    `;
    // events
    if (d.type === "actuator") {
      card.querySelector(".switch").addEventListener("click", () => {
        d.on = !d.on;
        saveJSON(LS_KEY_IOT, devices);
        renderDevices();
      });
    } else {
      // センサー校正：現在値に微調整ノイズ
      card.querySelector(".btn-calibrate").addEventListener("click", () => {
        const noise = (Math.random() - 0.5) * (d.name.includes("温度") ? 0.6 : 2.0);
        d.value = clamp(d.value + noise, d.name.includes("温度") ? -20 : 0, d.name.includes("温度") ? 60 : 100);
        saveJSON(LS_KEY_IOT, devices);
        renderDevices();
      });
    }
    card.querySelector(".btn-rename").addEventListener("click", () => {
      const name = prompt("新しい名前を入力", d.name);
      if (name && name.trim()) {
        d.name = name.trim();
        saveJSON(LS_KEY_IOT, devices);
        renderDevices();
      }
    });
    card.querySelector(".btn-delete").addEventListener("click", () => {
      if (!confirm(`「${d.name}」を削除しますか？`)) return;
      devices = devices.filter(x => x.id !== d.id);
      saveJSON(LS_KEY_IOT, devices);
      renderDevices();
    });

    deviceGrid.appendChild(card);
  });
}

function formatReading(d) {
  if (d.type !== "sensor") return d.on ? "ON" : "OFF";
  return `${d.value.toFixed(1)}${d.unit}`;
}
function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }
function escapeHTML(s) {
  return s.replace(/[&<>"']/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
}

function getDeviceIcon(device) {
  return device.type === "sensor" ? ICON_SENSOR : ICON_ACTUATOR;
}

renderDevices();

/* データ更新（擬似） */
setInterval(() => {
  // センサーの値をゆらぎで更新
  const temp = devices.find(d => d.type==="sensor" && /温度/.test(d.name));
  const hum  = devices.find(d => d.type==="sensor" && /湿度/.test(d.name));
  if (temp) {
    const delta = (Math.random() - 0.5) * 0.4;
    temp.value = clamp(temp.value + delta, -20, 60);
  }
  if (hum) {
    const delta = (Math.random() - 0.5) * 1.6;
    hum.value = clamp(hum.value + delta, 0, 100);
  }
  saveJSON(LS_KEY_IOT, devices);
  renderDevices();
}, 1500);

resetIoTBtn.addEventListener("click", () => {
  if (!confirm("IoT ダッシュボードを初期化しますか？")) return;
  devices = defaultDevices();
  saveJSON(LS_KEY_IOT, devices);
  renderDevices();
});

addDeviceBtn.addEventListener("click", () => {
  const name = prompt("デバイス名（例：CO₂ センサー / ポンプ）");
  if (!name) return;
  const kind = prompt("種類を入力（sensor / actuator）", "sensor");
  const type = (kind || "").toLowerCase() === "actuator" ? "actuator" : "sensor";
  const d = { id: crypto.randomUUID(), name: name.trim(), type, on: false };
  if (type === "sensor") { d.unit = ""; d.value = 0; }
  devices.push(d);
  saveJSON(LS_KEY_IOT, devices);
  renderDevices();
});

/* ---------- Chat + Summarizer (FAQ_Gemini integration) ---------- */

const chatLog = $("#chatLog");
const sidebarChatLog = $("#sidebarChatLog");
const chatInput = $("#chatInput");
const sidebarChatInput = $("#sidebarChatInput");
const chatForm = $("#chatForm");
const sidebarChatForm = $("#sidebarChatForm");
const summaryBox = $("#summaryBox");
const clearChatBtn = $("#clearChatBtn");
const sidebarChatSend = $(".sidebar-chat-send");
const sidebarChatUtilities = $(".sidebar-chat-utilities");
const sidebarPauseBtn = $("#sidebarPauseBtn");
const sidebarResetBtn = $("#sidebarResetBtn");

const SUMMARY_PLACEHOLDER = "左側のチャットでメッセージを送信すると、ここに要約が表示されます。";
const SUMMARY_LOADING_TEXT = "要約を取得しています…";
const INTRO_MESSAGE_TEXT = "ここは要約チャットです。左サイドバーの共通チャットからメッセージを送信すると重要なポイントをここに表示します。";

const chatState = {
  messages: [],
  initialized: false,
  sending: false,
};

let currentChatMode = "general";

const browserChatState = {
  messages: [],
  initialized: false,
  sending: false,
  paused: false,
  agentRunning: false,
  eventSource: null,
  historyAbort: null,
  setupHintShown: false,
};

const BROWSER_AGENT_SETUP_MESSAGE = [
  "ブラウザエージェント用 API を用意して接続する",
  "",
  "/api/history・/api/chat・/api/stream などを提供する別サービスを起動し、その URL を window.BROWSER_AGENT_API_BASE、<meta name=\"browser-agent-api-base\" …>、またはクエリパラメータ ?browser_agent_base=... で指定します。",
].join("\n");

const browserMessageIndex = new Map();

function getIntroMessage() {
  return {
    role: "system",
    text: INTRO_MESSAGE_TEXT,
    ts: Date.now(),
  };
}

chatState.messages = [getIntroMessage()];

function resolveGeminiBase() {
  const sanitize = value => (typeof value === "string" ? value.trim().replace(/\/+$/, "") : "");
  let queryBase = "";
  try {
    queryBase = new URLSearchParams(window.location.search).get("faq_gemini_base") || "";
  } catch (_) {
    queryBase = "";
  }
  const sources = [
    sanitize(queryBase),
    sanitize(window.FAQ_GEMINI_API_BASE),
    sanitize(document.querySelector("meta[name='faq-gemini-api-base']")?.content),
  ];
  for (const src of sources) {
    if (src) return src;
  }
  if (window.location.origin && window.location.origin !== "null") {
    return window.location.origin.replace(/\/+$/, "");
  }
  return "http://localhost:5000";
}

const GEMINI_API_BASE = resolveGeminiBase();

function buildGeminiUrl(path) {
  const normalizedPath = path.startsWith("http") ? path : path.startsWith("/") ? path : `/${path}`;
  if (!GEMINI_API_BASE) return normalizedPath;
  const base = GEMINI_API_BASE.replace(/\/+$/, "");
  if (!base || base === window.location.origin.replace(/\/+$/, "")) {
    return normalizedPath;
  }
  return `${base}${normalizedPath}`;
}

async function geminiRequest(path, { method = "GET", headers = {}, body, signal } = {}) {
  const url = buildGeminiUrl(path);
  const finalHeaders = { ...headers };
  const hasBody = body !== undefined && body !== null;
  const isFormData = typeof FormData !== "undefined" && body instanceof FormData;
  if (hasBody && !isFormData && !finalHeaders["Content-Type"]) {
    finalHeaders["Content-Type"] = "application/json";
  }

  const response = await fetch(url, {
    method,
    headers: finalHeaders,
    body,
    signal,
    mode: "cors",
  });

  const contentType = response.headers.get("content-type") || "";
  const isJson = contentType.includes("application/json");
  let data;
  try {
    data = isJson ? await response.json() : await response.text();
  } catch (_) {
    data = isJson ? {} : "";
  }

  if (!response.ok) {
    const message = typeof data === "string" && data
      ? data
      : (data && typeof data.error === "string")
        ? data.error
        : `${response.status} ${response.statusText}`;
    throw new Error(message);
  }

  return typeof data === "string" ? { message: data } : data;
}

function createMessageElement(message, { compact = false } = {}) {
  const el = document.createElement("div");
  const roleClass = message.role === "user" ? "user" : "system";
  el.className = `msg ${roleClass}`;
  if (compact) el.classList.add("compact");
  if (message.role === "assistant") el.classList.add("assistant");
  if (message.pending) el.classList.add("pending");

  const time = message.ts ? new Date(message.ts).toLocaleString("ja-JP") : "";
  const text = message.text ?? "";
  el.innerHTML = `
      ${escapeHTML(text)}
      ${time ? `<span class="msg-time">${time}</span>` : ""}
    `;
  return el;
}

function renderSidebarMessages(messages) {
  if (!sidebarChatLog) return;
  sidebarChatLog.innerHTML = "";
  const recent = messages.slice(-20);
  recent.forEach(message => {
    sidebarChatLog.appendChild(createMessageElement(message, { compact: true }));
  });
  sidebarChatLog.scrollTop = sidebarChatLog.scrollHeight;
}

function renderGeneralChat({ forceSidebar = false } = {}) {
  if (chatLog) {
    chatLog.innerHTML = "";
    chatState.messages.forEach(message => {
      chatLog.appendChild(createMessageElement(message));
    });
    chatLog.scrollTop = chatLog.scrollHeight;
  }

  if (forceSidebar || currentChatMode === "general") {
    renderSidebarMessages(chatState.messages);
  }
}

function renderBrowserChat({ forceSidebar = false } = {}) {
  if (forceSidebar || currentChatMode === "browser") {
    renderSidebarMessages(browserChatState.messages);
  }
}

function resolveBrowserAgentBase() {
  const sanitize = value => (typeof value === "string" ? value.trim().replace(/\/+$/, "") : "");
  let queryBase = "";
  try {
    queryBase = new URLSearchParams(window.location.search).get("browser_agent_base") || "";
  } catch (_) {
    queryBase = "";
  }
  const sources = [
    sanitize(queryBase),
    sanitize(window.BROWSER_AGENT_API_BASE),
    sanitize(document.querySelector("meta[name='browser-agent-api-base']")?.content),
  ];
  for (const src of sources) {
    if (src) return src;
  }
  if (window.location.origin && window.location.origin !== "null") {
    return window.location.origin.replace(/\/+$/, "");
  }
  return "http://localhost:5005";
}

const BROWSER_AGENT_API_BASE = resolveBrowserAgentBase();

function buildBrowserAgentUrl(path) {
  const normalizedPath = path.startsWith("http") ? path : path.startsWith("/") ? path : `/${path}`;
  if (!BROWSER_AGENT_API_BASE) return normalizedPath;
  const base = BROWSER_AGENT_API_BASE.replace(/\/+$/, "");
  if (!base || base === window.location.origin.replace(/\/+$/, "")) {
    return normalizedPath;
  }
  return `${base}${normalizedPath}`;
}

function shouldShowBrowserAgentSetupHint(error) {
  if (!error) {
    return false;
  }
  const status = typeof error.status === "number" ? error.status : null;
  if (status === 404 || status === 405) {
    return true;
  }
  const base = (BROWSER_AGENT_API_BASE || "").replace(/\/+$/, "");
  const origin = (window.location.origin || "").replace(/\/+$/, "");
  if (!base || base === origin) {
    return true;
  }
  if (!status && error.name === "TypeError") {
    return true;
  }
  return false;
}

async function browserAgentRequest(path, { method = "GET", headers = {}, body, signal } = {}) {
  const url = buildBrowserAgentUrl(path);
  const finalHeaders = { ...headers };
  const hasBody = body !== undefined && body !== null;
  const isFormData = typeof FormData !== "undefined" && body instanceof FormData;
  if (hasBody && !isFormData && !finalHeaders["Content-Type"]) {
    finalHeaders["Content-Type"] = "application/json";
  }

  const response = await fetch(url, {
    method,
    headers: finalHeaders,
    body,
    signal,
    mode: "cors",
  });

  const contentType = response.headers.get("content-type") || "";
  const isJson = contentType.includes("application/json");
  let data;
  try {
    data = isJson ? await response.json() : await response.text();
  } catch (_) {
    data = isJson ? {} : "";
  }

  if (!response.ok) {
    const message = typeof data === "string" && data
      ? data
      : (data && typeof data.error === "string")
        ? data.error
        : `${response.status} ${response.statusText}`;
    const error = new Error(message);
    error.status = response.status;
    throw error;
  }

  const payload = typeof data === "string" ? { message: data } : data;
  return { data: payload, status: response.status };
}

function normalizeBrowserAgentMessage(raw) {
  if (!raw || typeof raw !== "object") {
    return { id: null, role: "system", text: "", ts: Date.now() };
  }
  const role = (raw.role || "").toLowerCase();
  const normalizedRole = role === "user" ? "user" : role === "assistant" ? "assistant" : "system";
  const text = typeof raw.content === "string" ? raw.content : "";
  const parsedTs = raw.timestamp ? Date.parse(raw.timestamp) : Date.now();
  const ts = Number.isFinite(parsedTs) ? parsedTs : Date.now();
  return {
    id: typeof raw.id === "number" ? raw.id : null,
    role: normalizedRole,
    text,
    ts,
  };
}

function setBrowserChatHistory(history, { forceSidebar = false } = {}) {
  const converted = Array.isArray(history) ? history.map(normalizeBrowserAgentMessage) : [];
  browserChatState.messages = converted.length ? converted : [
    {
      id: null,
      role: "system",
      text: "まだメッセージはありません。",
      ts: Date.now(),
    },
  ];
  browserChatState.setupHintShown = false;
  browserMessageIndex.clear();
  browserChatState.messages.forEach((message, index) => {
    if (typeof message.id === "number") {
      browserMessageIndex.set(message.id, index);
    }
  });
  renderBrowserChat({ forceSidebar });
}

function appendBrowserChatMessage(raw) {
  const message = normalizeBrowserAgentMessage(raw);
  if (typeof message.id === "number" && browserMessageIndex.has(message.id)) {
    const existingIndex = browserMessageIndex.get(message.id);
    if (existingIndex !== undefined) {
      browserChatState.messages[existingIndex] = message;
    }
  } else {
    if (typeof message.id === "number") {
      browserMessageIndex.set(message.id, browserChatState.messages.length);
    }
    browserChatState.messages.push(message);
  }
  renderBrowserChat();
}

function updateBrowserChatMessage(raw) {
  const message = normalizeBrowserAgentMessage(raw);
  if (typeof message.id === "number" && browserMessageIndex.has(message.id)) {
    const index = browserMessageIndex.get(message.id);
    if (index !== undefined) {
      browserChatState.messages[index] = message;
      renderBrowserChat();
      return;
    }
  }
  appendBrowserChatMessage(raw);
}

function addBrowserSystemMessage(text, { forceSidebar = false } = {}) {
  if (!text) return;
  browserChatState.messages.push({ id: null, role: "system", text, ts: Date.now() });
  renderBrowserChat({ forceSidebar });
}

function addBrowserAgentSetupHint({ forceSidebar = false, render = true } = {}) {
  if (browserChatState.setupHintShown) return;
  browserChatState.setupHintShown = true;
  browserChatState.messages.push({ id: null, role: "system", text: BROWSER_AGENT_SETUP_MESSAGE, ts: Date.now() });
  if (render) {
    renderBrowserChat({ forceSidebar });
  }
}

function clearBrowserAgentSetupHint() {
  const index = browserChatState.messages.findIndex((message) => message.text === BROWSER_AGENT_SETUP_MESSAGE);
  if (index !== -1) {
    browserChatState.messages.splice(index, 1);
  }
  browserChatState.setupHintShown = false;
}

function updatePauseButtonState(mode = currentChatMode) {
  if (!sidebarPauseBtn) return;
  const showBrowserControls = mode === "browser";
  sidebarPauseBtn.textContent = browserChatState.paused ? "再開" : "一時停止";
  sidebarPauseBtn.setAttribute("aria-pressed", browserChatState.paused ? "true" : "false");
  sidebarPauseBtn.disabled = !showBrowserControls || (!browserChatState.agentRunning && !browserChatState.paused);
}

function updateSidebarControlsForMode(mode) {
  const showBrowserControls = mode === "browser";
  if (sidebarChatUtilities) {
    if (showBrowserControls) sidebarChatUtilities.removeAttribute("hidden");
    else sidebarChatUtilities.setAttribute("hidden", "");
  }
  if (sidebarResetBtn) {
    sidebarResetBtn.disabled = !showBrowserControls;
  }
  if (sidebarChatSend) {
    sidebarChatSend.disabled = showBrowserControls ? browserChatState.sending : false;
  }
  updatePauseButtonState(mode);
}

function handleBrowserStatusEvent(payload) {
  if (!payload || typeof payload !== "object") return;
  if (typeof payload.agent_running === "boolean") {
    browserChatState.agentRunning = payload.agent_running;
    if (!payload.agent_running) {
      browserChatState.paused = false;
    }
    updatePauseButtonState();
  }
}

async function loadBrowserAgentHistory({ showLoading = false, forceSidebar = false } = {}) {
  if (browserChatState.historyAbort) {
    browserChatState.historyAbort.abort();
  }
  const controller = new AbortController();
  browserChatState.historyAbort = controller;
  if (showLoading) {
    browserChatState.messages = [
      { id: null, role: "system", text: "履歴を読み込んでいます…", pending: true, ts: Date.now() },
    ];
    browserMessageIndex.clear();
    renderBrowserChat({ forceSidebar });
  }
  try {
    const { data } = await browserAgentRequest("/api/history", { signal: controller.signal });
    if (controller.signal.aborted) return;
    setBrowserChatHistory(data.messages || [], { forceSidebar });
  } catch (error) {
    if (controller.signal.aborted) return;
    browserChatState.messages = [
      { id: null, role: "system", text: `履歴の取得に失敗しました: ${error.message}`, ts: Date.now() },
    ];
    browserChatState.setupHintShown = false;
    browserMessageIndex.clear();
    if (shouldShowBrowserAgentSetupHint(error)) {
      addBrowserAgentSetupHint({ forceSidebar: true, render: false });
    }
    renderBrowserChat({ forceSidebar });
  } finally {
    if (browserChatState.historyAbort === controller) {
      browserChatState.historyAbort = null;
    }
  }
}

function connectBrowserEventStream() {
  if (typeof EventSource === "undefined") return;
  if (browserChatState.eventSource) return;
  try {
    const streamUrl = buildBrowserAgentUrl("/api/stream");
    const source = new EventSource(streamUrl);
    source.onmessage = event => {
      if (!event?.data) return;
      let parsed;
      try {
        parsed = JSON.parse(event.data);
      } catch (error) {
        console.error("ブラウザエージェントのイベント解析に失敗しました:", error);
        return;
      }
      const { type, payload } = parsed || {};
      if (type === "message") {
        appendBrowserChatMessage(payload);
      } else if (type === "update") {
        updateBrowserChatMessage(payload);
      } else if (type === "reset") {
        browserMessageIndex.clear();
        browserChatState.messages = [];
        renderBrowserChat({ forceSidebar: currentChatMode === "browser" });
        loadBrowserAgentHistory({ forceSidebar: currentChatMode === "browser" });
      } else if (type === "status") {
        handleBrowserStatusEvent(payload);
      }
    };
    source.onerror = () => {
      if (browserChatState.eventSource === source) {
        source.close();
        browserChatState.eventSource = null;
        setTimeout(() => {
          connectBrowserEventStream();
        }, 4000);
      }
    };
    browserChatState.eventSource = source;
  } catch (error) {
    console.error("ブラウザエージェントのイベントストリーム初期化に失敗しました:", error);
  }
}

function ensureBrowserAgentInitialized({ showLoading = false } = {}) {
  connectBrowserEventStream();
  if (!browserChatState.initialized) {
    browserChatState.initialized = true;
    loadBrowserAgentHistory({ showLoading: true, forceSidebar: true });
  } else {
    loadBrowserAgentHistory({ showLoading, forceSidebar: true });
  }
}

async function sendBrowserAgentPrompt(text) {
  if (!text || browserChatState.sending) return;
  connectBrowserEventStream();
  browserChatState.sending = true;
  browserChatState.agentRunning = true;
  browserChatState.paused = false;
  updateSidebarControlsForMode(currentChatMode);
  try {
    const payload = JSON.stringify({ prompt: text });
    const { data } = await browserAgentRequest("/api/chat", { method: "POST", body: payload });
    if (Array.isArray(data.messages)) {
      setBrowserChatHistory(data.messages, { forceSidebar: currentChatMode === "browser" });
    }
    if (typeof data.run_summary === "string" && data.run_summary.trim()) {
      // 既に履歴に含まれているため、ここでは追加しない
    }
  } catch (error) {
    addBrowserSystemMessage(`送信に失敗しました: ${error.message}`, { forceSidebar: currentChatMode === "browser" });
    clearBrowserAgentSetupHint();
    if (shouldShowBrowserAgentSetupHint(error)) {
      addBrowserAgentSetupHint({ forceSidebar: currentChatMode === "browser" });
    }
  } finally {
    browserChatState.sending = false;
    updateSidebarControlsForMode(currentChatMode);
  }
}

function setChatMode(mode) {
  if (mode !== "browser" && mode !== "general") {
    mode = "general";
  }
  if (currentChatMode !== mode) {
    currentChatMode = mode;
  }
  updateSidebarControlsForMode(mode);
  if (mode === "browser") {
    renderBrowserChat({ forceSidebar: true });
  } else {
    renderGeneralChat({ forceSidebar: true });
  }
}

function setChatMessagesFromHistory(history) {
  const now = Date.now();
  const converted = Array.isArray(history)
    ? history.map((entry, idx) => ({
        role: (entry.role || "").toLowerCase() === "user" ? "user" : "assistant",
        text: entry.message ?? "",
        ts: now + idx,
      }))
    : [];
  chatState.messages = [getIntroMessage(), ...converted];
  renderGeneralChat();
}

async function syncConversationHistory({ showLoading = false, force = false } = {}) {
  if (!sidebarChatLog && !chatLog) return;
  if (chatState.sending && !force) {
    return;
  }
  if (showLoading && (!chatState.sending || force)) {
    chatState.messages = [
      getIntroMessage(),
      { role: "system", text: "会話履歴を取得しています…", pending: true, ts: Date.now() },
    ];
    renderGeneralChat();
  }
  try {
    const data = await geminiRequest("/conversation_history");
    if (chatState.sending && !force) {
      return;
    }
    setChatMessagesFromHistory(data.conversation_history);
  } catch (error) {
    console.error("会話履歴の取得に失敗しました:", error);
    if (showLoading && (!chatState.sending || force)) {
      chatState.messages = [
        getIntroMessage(),
        { role: "system", text: `会話履歴の取得に失敗しました: ${error.message}`, ts: Date.now() },
      ];
      renderGeneralChat();
    }
  }
}

function isChatViewActive() {
  return views.chat?.classList.contains("active");
}

async function refreshSummaryBox({ showLoading = false } = {}) {
  if (!summaryBox) return;
  if (showLoading) {
    summaryBox.textContent = SUMMARY_LOADING_TEXT;
  }
  try {
    const data = await geminiRequest("/conversation_summary");
    const summary = (data.summary || "").trim();
    summaryBox.textContent = summary ? summary : SUMMARY_PLACEHOLDER;
  } catch (error) {
    summaryBox.textContent = `要約の取得に失敗しました: ${error.message}`;
  }
}

function ensureChatInitialized({ showLoadingSummary = false } = {}) {
  if (chatState.initialized) {
    syncConversationHistory();
    if (showLoadingSummary) refreshSummaryBox({ showLoading: true });
    else refreshSummaryBox();
    return;
  }
  chatState.initialized = true;
  syncConversationHistory({ showLoading: true });
  refreshSummaryBox({ showLoading: showLoadingSummary });
}

let pendingAssistantMessage = null;

function addUserMessage(text) {
  const message = { role: "user", text, ts: Date.now() };
  chatState.messages.push(message);
  renderGeneralChat();
  return message;
}

function addPendingAssistantMessage() {
  const message = {
    role: "assistant",
    text: "回答を生成しています…",
    pending: true,
  };
  chatState.messages.push(message);
  renderGeneralChat();
  return message;
}

async function sendChatMessage(text) {
  if (!text || chatState.sending) return;
  ensureChatInitialized();
  chatState.sending = true;

  addUserMessage(text);
  pendingAssistantMessage = addPendingAssistantMessage();

  try {
    const payload = JSON.stringify({ question: text });
    const data = await geminiRequest("/rag_answer", { method: "POST", body: payload });
    const answer = (data.answer || "").trim();
    if (pendingAssistantMessage) {
      pendingAssistantMessage.text = answer || "回答が空でした。";
      pendingAssistantMessage.pending = false;
      pendingAssistantMessage.ts = Date.now();
    }
    renderGeneralChat();
    await syncConversationHistory({ force: true });
    if (isChatViewActive()) {
      await refreshSummaryBox({ showLoading: true });
    } else {
      refreshSummaryBox();
    }
  } catch (error) {
    console.error("チャット送信時にエラーが発生しました:", error);
    if (pendingAssistantMessage) {
      pendingAssistantMessage.text = `エラー: ${error.message}`;
      pendingAssistantMessage.pending = false;
      pendingAssistantMessage.ts = Date.now();
      renderGeneralChat();
    }
  } finally {
    chatState.sending = false;
    pendingAssistantMessage = null;
  }
}

if (chatForm) {
  chatForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const value = chatInput.value.trim();
    if (!value) return;
    chatInput.value = "";
    if (sidebarChatInput) sidebarChatInput.value = "";
    if (currentChatMode === "browser") await sendBrowserAgentPrompt(value);
    else await sendChatMessage(value);
  });
}

if (sidebarChatForm) {
  sidebarChatForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const value = sidebarChatInput.value.trim();
    if (!value) return;
    sidebarChatInput.value = "";
    if (chatInput) chatInput.value = "";
    if (currentChatMode === "browser") await sendBrowserAgentPrompt(value);
    else await sendChatMessage(value);
  });
}

if (clearChatBtn) {
  clearChatBtn.addEventListener("click", async () => {
    if (!confirm("チャット履歴をクリアしますか？")) return;
    try {
      await geminiRequest("/reset_history", { method: "POST" });
      chatState.messages = [getIntroMessage()];
      renderGeneralChat({ forceSidebar: true });
      await refreshSummaryBox({ showLoading: true });
    } catch (error) {
      alert(`チャット履歴のクリアに失敗しました: ${error.message}`);
    }
  });
}

if (sidebarPauseBtn) {
  sidebarPauseBtn.addEventListener("click", async () => {
    if (currentChatMode !== "browser") return;
    try {
      if (browserChatState.paused) {
        const { data } = await browserAgentRequest("/api/resume", { method: "POST" });
        if (data && typeof data.status === "string") {
          browserChatState.paused = data.status !== "resumed" ? browserChatState.paused : false;
        } else {
          browserChatState.paused = false;
        }
      } else {
        const { data } = await browserAgentRequest("/api/pause", { method: "POST" });
        if (data && typeof data.status === "string") {
          browserChatState.paused = data.status === "paused";
        } else {
          browserChatState.paused = true;
        }
        browserChatState.agentRunning = true;
      }
    } catch (error) {
      addBrowserSystemMessage(`一時停止操作に失敗しました: ${error.message}`, { forceSidebar: true });
    } finally {
      updatePauseButtonState();
    }
  });
}

if (sidebarResetBtn) {
  sidebarResetBtn.addEventListener("click", async () => {
    if (currentChatMode !== "browser") return;
    if (!confirm("ブラウザエージェントの履歴をリセットしますか？")) return;
    try {
      const { data } = await browserAgentRequest("/api/reset", { method: "POST" });
      browserChatState.paused = false;
      browserChatState.agentRunning = false;
      setBrowserChatHistory(data?.messages || [], { forceSidebar: true });
      updateSidebarControlsForMode(currentChatMode);
    } catch (error) {
      addBrowserSystemMessage(`履歴のリセットに失敗しました: ${error.message}`, { forceSidebar: true });
    }
  });
}

const initialActiveView = document.querySelector(".nav-btn.active")?.dataset.view;
if (initialActiveView === "browser") {
  setChatMode("browser");
  ensureBrowserAgentInitialized({ showLoading: true });
} else {
  setChatMode("general");
}
