import { $, escapeHTML } from "./dom-utils.js";
import {
  determineGeneralProxyAgentFromResult,
  setGeneralProxyAgent,
  containsBrowserAgentFinalMarker,
  isGeneralProxyAgentBrowser,
  registerGeneralProxyAgentHook,
  registerGeneralProxyRenderHook,
} from "./layout.js";
import { ensureIotDashboardInitialized, iotAgentRequest, summarizeIotDevices } from "./iot.js";
import { schedulerAgentRequest, ensureSchedulerAgentInitialized } from "./scheduler.js";
import { markAgentAvailable, markAgentUnavailable } from "./agent-status.js";

/* ---------- Chat + Summarizer (Life-Style integration) ---------- */

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
const sidebarPauseIcon = sidebarPauseBtn?.querySelector(".sidebar-chat-control-icon");
const sidebarPauseSr = sidebarPauseBtn?.querySelector(".sr-only");
const sidebarResetIcon = sidebarResetBtn?.querySelector(".sidebar-chat-control-icon");

const ICON_PAUSE = `<svg viewBox="0 0 24 24" focusable="false"><path fill="currentColor" d="M8 5h3v14H8zm5 0h3v14h-3z"/></svg>`;
const ICON_PLAY = `<svg viewBox="0 0 24 24" focusable="false"><path fill="currentColor" d="M8 5.14v13.72a1 1 0 0 0 1.52.85l9.18-6.86a1 1 0 0 0 0-1.7L9.52 4.29A1 1 0 0 0 8 5.14z"/></svg>`;
const ICON_RESET = `<svg viewBox="0 0 24 24" focusable="false"><path fill="currentColor" d="M17.65 6.35A7.95 7.95 0 0 0 12 4a8 8 0 1 0 7.75 10h-2.06A6 6 0 1 1 12 6a5.96 5.96 0 0 1 4.24 1.76L13 11h7V4z"/></svg>`;

if (sidebarPauseIcon) {
  sidebarPauseIcon.innerHTML = ICON_PAUSE;
}

if (sidebarResetIcon) {
  sidebarResetIcon.innerHTML = ICON_RESET;
}

const THINKING_TITLE_TEXT = "AIが考えています";
const THINKING_SUBTITLE_TEXT = "見つけた情報から回答を組み立て中";
const THINKING_MESSAGE_TEXT = `${THINKING_TITLE_TEXT}\u3000${THINKING_SUBTITLE_TEXT}`;

const SUMMARY_PLACEHOLDER = "左側のチャットでメッセージを送信すると、ここに要約が表示されます。";
const SUMMARY_LOADING_TEXT = THINKING_MESSAGE_TEXT;
const INTRO_MESSAGE_TEXT = "ここは要約チャットです。左サイドバーの共通チャットからメッセージを送信すると重要なポイントをここに表示します。";
const ORCHESTRATOR_INTRO_TEXT = "一般ビューではマルチエージェント・オーケストレーターがタスクを計画し、適切なエージェントに指示を送ります。共通チャットからリクエストを入力してください。";
const ORCHESTRATOR_SPEAKER_LABEL = "[Orchestrator]";
const ORCHESTRATOR_LABEL_PATTERN = /^\[orchestrator\]/i;

const chatState = {
  messages: [],
  initialized: false,
  sending: false,
};

const orchestratorState = {
  messages: [],
  initialized: false,
  sending: false,
};

const orchestratorBrowserMirrorState = {
  active: false,
  useSseFallback: false,
  messages: new Map(),
  placeholder: null,
  fallbackTimer: null,
  lastProgressAt: 0,
};

function clearOrchestratorBrowserMirrorMessages({ preserve } = {}) {
  const list = Array.isArray(preserve) ? preserve : preserve ? [preserve] : [];
  const keep = new Set(list.filter(Boolean));
  orchestratorBrowserMirrorState.messages.forEach(message => {
    if (!keep.has(message)) {
      const index = orchestratorState.messages.indexOf(message);
      if (index !== -1) {
        orchestratorState.messages.splice(index, 1);
      }
    }
  });
  orchestratorBrowserMirrorState.messages.clear();
  orchestratorBrowserMirrorState.placeholder = null;
}

let orchestratorBrowserTaskActive = false;

const ORCHESTRATOR_AGENT_LABELS = {
  lifestyle: "Life-Styleエージェント",
  browser: "ブラウザエージェント",
  iot: "IoT エージェント",
  scheduler: "Scheduler エージェント",
};

const IOT_CHAT_GREETING = "こんにちは！登録済みデバイスの状況を確認したり、チャットから指示を送れます。";
const SCHEDULER_CHAT_GREETING = "こんにちは！スケジュールの確認や登録をお手伝いします。予定を教えてください。";

const iotChatState = {
  messages: [],
  history: [],
  initialized: false,
  sending: false,
  paused: false,
};

const schedulerChatState = {
  messages: [],
  history: [],
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
  promptQueue: [],
};

const browserMessageIndex = new Map();

function getIntroMessage() {
  return {
    role: "system",
    text: INTRO_MESSAGE_TEXT,
    ts: Date.now(),
  };
}

chatState.messages = [getIntroMessage()];

function resolveLifestyleBase() {
  const sanitize = value => (typeof value === "string" ? value.trim().replace(/\/+$/, "") : "");
  let queryBase = "";
  try {
    queryBase = new URLSearchParams(window.location.search).get("lifestyle_base") || "";
  } catch (_) {
    queryBase = "";
  }
  const sources = [
    sanitize(queryBase),
    sanitize(window.LIFESTYLE_API_BASE),
    sanitize(document.querySelector("meta[name='lifestyle-api-base']")?.content),
  ];
  for (const src of sources) {
    if (src) return src;
  }
  if (window.location.origin && window.location.origin !== "null") {
    return window.location.origin.replace(/\/+$/, "");
  }
  return "http://localhost:5000";
}

const LIFESTYLE_API_BASE = resolveLifestyleBase();

function buildLifestyleUrl(path) {
  const normalizedPath = path.startsWith("http") ? path : path.startsWith("/") ? path : `/${path}`;
  if (!LIFESTYLE_API_BASE) return normalizedPath;
  const base = LIFESTYLE_API_BASE.replace(/\/+$/, "");
  if (!base || base === window.location.origin.replace(/\/+$/, "")) {
    return normalizedPath;
  }
  return `${base}${normalizedPath}`;
}

async function lifestyleRequest(path, { method = "GET", headers = {}, body, signal } = {}) {
  const url = buildLifestyleUrl(path);
  const finalHeaders = { ...headers };
  const hasBody = body !== undefined && body !== null;
  const isFormData = typeof FormData !== "undefined" && body instanceof FormData;
  if (hasBody && !isFormData && !finalHeaders["Content-Type"]) {
    finalHeaders["Content-Type"] = "application/json";
  }

  let response;
  try {
    response = await fetch(url, {
      method,
      headers: finalHeaders,
      body,
      signal,
      mode: "cors",
    });
  } catch (error) {
    markAgentUnavailable("lifestyle", error?.message || "接続に失敗しました。");
    return { status: "unavailable", message: "Life-Styleエージェントに接続できません。", error: error?.message };
  }

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
    if (response.status >= 500) {
      markAgentUnavailable("lifestyle", message);
      return { status: "unavailable", message: "Life-Styleエージェントに接続できません。", error: message };
    }
    throw new Error(message);
  }

  const payload = typeof data === "string" ? { message: data } : data;
  if (payload && payload.status === "unavailable") {
    markAgentUnavailable("lifestyle", payload.error || payload.message);
    return payload;
  }
  markAgentAvailable("lifestyle");
  return payload;
}

function parseSseEventBlock(block) {
  const lines = block.split("\n");
  let eventType = "message";
  const dataLines = [];

  for (const rawLine of lines) {
    if (!rawLine) continue;
    if (rawLine.startsWith(":")) continue;
    if (rawLine.startsWith("event:")) {
      eventType = rawLine.slice(6).trim() || "message";
    } else if (rawLine.startsWith("data:")) {
      dataLines.push(rawLine.slice(5).trimStart());
    }
  }

  const dataText = dataLines.join("\n");
  let data;
  if (dataText) {
    try {
      data = JSON.parse(dataText);
    } catch (error) {
      console.error("オーケストレーターイベントの解析に失敗しました:", error, dataText);
      data = { raw: dataText };
    }
  } else {
    data = {};
  }

  return { event: eventType || "message", data };
}

async function* orchestratorRequest(message, { signal, view, logHistory } = {}) {
  const payload = { message };
  if (view) {
    payload.view = view;
  }
  if (logHistory === true) {
    payload.log_history = true;
  }
  if (BROWSER_AGENT_API_BASE) {
    payload.browser_agent_base = BROWSER_AGENT_API_BASE;
  }
  if (Array.isArray(BROWSER_AGENT_BASE_HINTS) && BROWSER_AGENT_BASE_HINTS.length) {
    payload.browser_agent_bases = BROWSER_AGENT_BASE_HINTS;
  }

  const response = await fetch("/orchestrator/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal,
  });

  if (!response.ok) {
    let data;
    try {
      data = await response.json();
    } catch (_) {
      try {
        data = await response.text();
      } catch (__) {
        data = "";
      }
    }
    const messageText = typeof data === "string" && data
      ? data
      : (data && typeof data.error === "string")
        ? data.error
        : `${response.status} ${response.statusText}`;
    throw new Error(messageText);
  }

  const reader = response.body?.getReader();
  if (!reader) return;

  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  try {
    while (true) {
      const { value, done } = await reader.read();
      if (value) {
        buffer += decoder.decode(value, { stream: !done });
      } else if (done) {
        buffer += decoder.decode(new Uint8Array(), { stream: false });
      }

      if (!buffer) {
        if (done) return;
        continue;
      }

      buffer = buffer.replace(/\r\n/g, "\n").replace(/\r/g, "\n");

      let separatorIndex;
      while ((separatorIndex = buffer.indexOf("\n\n")) !== -1) {
        const rawEvent = buffer.slice(0, separatorIndex);
        buffer = buffer.slice(separatorIndex + 2);
        if (!rawEvent.trim()) continue;
        const parsed = parseSseEventBlock(rawEvent);
        yield parsed;
        if (parsed.event === "complete" || parsed.event === "error") {
          try {
            await reader.cancel();
          } catch (_) {
            // ignore cancellation errors
          }
          return;
        }
      }

      if (done) {
        const remaining = buffer.trim();
        if (remaining) {
          yield parseSseEventBlock(remaining);
        }
        return;
      }
    }
  } finally {
    try {
      reader.releaseLock();
    } catch (_) {
      // ignore
    }
  }
}

const AGENT_ICONS = {
  orchestrator: `<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 18c-4.41 0-8-3.59-8-8s3.59-8 8-8 8 3.59 8 8-3.59 8-8 8zm-1-13h2v6h-2zm0 8h2v2h-2z"/></svg>`, // Simple Abstract
  browser: `<svg viewBox="0 0 24 24" fill="currentColor"><path d="M20 4H4c-1.1 0-1.99.9-1.99 2L2 18c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2zm-5 14H4v-4h11v4zm0-5H4V9h11v4zm5 5h-4V9h4v9z"/></svg>`,
  iot: `<svg viewBox="0 0 24 24" fill="currentColor"><path d="M9 2h6v2h2v2h2v6h-2v2h-2v2h-6v-2H7v-2H5V6h2V4h2V2zm0 4v2H7v6h2v2h6v-2h2V8h-2V6H9z"/></svg>`,
  scheduler: `<svg viewBox="0 0 24 24" fill="currentColor"><path d="M19 3h-1V1h-2v2H8V1H6v2H5c-1.11 0-2 .9-2 2v14c0 1.1.89 2 2 2h14c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zm0 16H5V8h14v11z"/></svg>`,
  lifestyle: `<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 21.35l-1.45-1.32C5.4 15.36 2 12.28 2 8.5 2 5.42 4.42 3 7.5 3c1.74 0 3.41.81 4.5 2.09C13.09 3.81 14.76 3 16.5 3 19.58 3 22 5.42 22 8.5c0 3.78-3.4 6.86-8.55 11.54L12 21.35z"/></svg>`,
  user: `<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 12c2.21 0 4-1.79 4-4s-1.79-4-4-4-4 1.79-4 4 1.79 4 4 4zm0 2c-2.67 0-8 1.34-8 4v2h16v-2c0-2.66-5.33-4-8-4z"/></svg>`,
  system: `<svg viewBox="0 0 24 24" fill="currentColor"><path d="M11 18h2v-2h-2v2zm1-16C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 18c-4.41 0-8-3.59-8-8s3.59-8 8-8 8 3.59 8 8-3.59 8-8 8zm0-14c-2.21 0-4 1.79-4 4h2c0-1.1.9-2 2-2s2 .9 2 2c0 2-3 1.75-3 5h2c0-2.25 3-2.5 3-5 0-2.21-1.79-4-4-4z"/></svg>`,
  default: `<svg viewBox="0 0 24 24" fill="currentColor"><path d="M20 2H4c-1.1 0-2 .9-2 2v18l4-4h14c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2z"/></svg>`
};

function getAgentIcon(name) {
  const key = Object.keys(AGENT_ICONS).find(k => name.toLowerCase().includes(k)) || "default";
  return AGENT_ICONS[key];
}

function formatMessageTime(ts) {
  if (!ts) return "";
  const date = new Date(ts);
  const now = new Date();
  const isToday = date.getDate() === now.getDate() && date.getMonth() === now.getMonth() && date.getFullYear() === now.getFullYear();
  
  const timeStr = date.toLocaleTimeString('ja-JP', { hour: '2-digit', minute: '2-digit' });
  if (isToday) return timeStr;
  return `${date.getMonth() + 1}/${date.getDate()} ${timeStr}`;
}

function createMessageElement(message, { compact = false } = {}) {
  const el = document.createElement("div");
  const roleClass = message.role === "user" ? "user" : "system";
  el.className = `msg ${roleClass}`;
  if (compact) el.classList.add("compact");
  if (message.role === "assistant") el.classList.add("assistant");
  if (message.pending) el.classList.add("pending");

  let text = message.text ?? "";
  let agentName = message.role === "user" ? "User" : (message.role === "assistant" ? "Assistant" : "System");
  let agentIcon = AGENT_ICONS.default;

  // Parse agent label
  if (message.role !== "user") {
    const match = text.match(/^\[([^\]]+)\]\s*(.*)/s);
    if (match) {
      agentName = match[1];
      text = match[2];
    }
    agentIcon = getAgentIcon(agentName);
  } else {
    agentIcon = AGENT_ICONS.user;
  }
  
  // Normalize specific known names for cleaner display
  if (agentName.toLowerCase().includes("orchestrator")) agentName = "Orchestrator";
  else if (agentName.toLowerCase().includes("life-style")) agentName = "Life-Style";
  else if (agentName.toLowerCase().includes("browser")) agentName = "ブラウザエージェント";
  else if (agentName.toLowerCase().includes("iot")) agentName = "IoT Agent";
  else if (agentName.toLowerCase().includes("scheduler")) agentName = "Scheduler Agent";

  const time = formatMessageTime(message.ts);
  const escapedText = escapeHTML(text);

  if (message.role === "assistant" && message.pending) {
    el.classList.add("thinking");
    el.innerHTML = `
      <div class="thinking-card" role="status">
        <span class="thinking-icon" aria-hidden="true"></span>
        <span class="thinking-labels">
          <span class="thinking-title">${THINKING_TITLE_TEXT}</span>
          <span class="thinking-sub">${THINKING_SUBTITLE_TEXT}</span>
        </span>
      </div>
      ${time ? `<span class="msg-time">${time}</span>` : ""}
    `;
    return el;
  }

  // Rich message structure
  el.innerHTML = `
    <div class="msg-inner">
      <div class="msg-avatar" aria-hidden="true">${agentIcon}</div>
      <div class="msg-content">
        <div class="msg-header">
          <span class="msg-sender">${agentName}</span>
          <span class="msg-time">${time}</span>
        </div>
        <div class="msg-body">${escapedText}</div>
      </div>
    </div>
  `;
  return el;
}

function renderSidebarMessages(messages) {
  if (!sidebarChatLog) return;
  sidebarChatLog.innerHTML = "";
  messages.forEach(message => {
    sidebarChatLog.appendChild(createMessageElement(message, { compact: true }));
  });
  sidebarChatLog.scrollTop = sidebarChatLog.scrollHeight;
}

const ASSISTANT_AGENT_LABEL_SYNONYMS = {
  lifestyle: [
    "life-styleエージェント",
    "life styleエージェント",
    "life-style agent",
    "life style agent",
    "life-style-agent",
    "life style",
    "life-style",
    "qa gemini",
    "qaエージェント",
    "qa エージェント",
    "qa agent",
    "qa-agent",
    "qa",
    "家庭内エージェント",
    "faq gemini",
    "faq",
  ],
  browser: ["browser agent", "ブラウザエージェント"],
  iot: ["iot agent", "iot エージェント", "iotエージェント"],
};

function normalizeAssistantAgentLabel(label) {
  const normalized = typeof label === "string" ? label.trim().toLowerCase() : "";
  if (!normalized) {
    return "";
  }
  for (const [agentKey, synonyms] of Object.entries(ASSISTANT_AGENT_LABEL_SYNONYMS)) {
    if (synonyms.includes(normalized)) {
      return agentKey;
    }
  }
  return normalized;
}

function normalizeAssistantText(value) {
  if (typeof value !== "string") {
    return "";
  }
  const squished = value.replace(/\s+/g, " ").trim();
  if (!squished) {
    return "";
  }
  const match = squished.match(/^\[([^\]]+)\]\s*(.*)$/);
  if (!match) {
    return squished;
  }
  const [, label, body = ""] = match;
  const normalizedBody = body.replace(/\s+/g, " ").trim();
  const normalizedLabel = normalizeAssistantAgentLabel(label);
  return `${normalizedLabel || label.trim().toLowerCase()}:::${normalizedBody}`;
}

function prefixOrchestratorText(text) {
  if (typeof text !== "string") return "";
  const trimmed = text.trim();
  if (!trimmed) return "";
  
  // If it already has a label (starts with [...]), respect it.
  if (/^\[[^\]]+\]/.test(trimmed)) {
    return trimmed;
  }

  return `${ORCHESTRATOR_SPEAKER_LABEL} ${trimmed}`;
}

function getOrchestratorIntroMessage() {
  return {
    role: "assistant",
    text: prefixOrchestratorText(ORCHESTRATOR_INTRO_TEXT),
    ts: Date.now(),
  };
}

let orchestratorPollingInterval = null;

function startOrchestratorPolling() {
  if (orchestratorPollingInterval) return;
  orchestratorPollingInterval = setInterval(() => {
    if (currentChatMode === "orchestrator" && !orchestratorState.sending) {
      fetchChatHistory();
    }
  }, 3000);
}

async function fetchChatHistory() {
  try {
    const response = await fetch("/chat_history");
    if (!response.ok) {
      throw new Error("Failed to fetch chat history");
    }
    const history = await response.json();

    // Check for updates to avoid unnecessary re-renders
    const currentJson = JSON.stringify(history);
    if (orchestratorState.lastHistoryJson === currentJson) {
      return;
    }
    orchestratorState.lastHistoryJson = currentJson;

    const records = Array.isArray(history) ? history : [];
    const now = Date.now();
    const normalisedHistory = records.length === 0
      ? [getOrchestratorIntroMessage()]
      : records.map((item, index) => {
          const rawRole = typeof item?.role === "string" ? item.role.trim().toLowerCase() : "";
          const role = rawRole === "user" ? "user" : rawRole === "assistant" ? "assistant" : "system";
          const text = typeof item?.content === "string"
            ? role === "assistant"
              ? prefixOrchestratorText(item.content)
              : item.content
            : "";
          return { role, text, ts: now + index };
        });

    const localMessages = orchestratorState.messages.filter(message => message && message.local === true);
    if (localMessages.length) {
      const keyFor = message => `${message.role}:::${message.text}`;
      const indexMap = new Map();
      normalisedHistory.forEach((message, index) => {
        const key = keyFor(message);
        const bucket = indexMap.get(key);
        if (bucket) {
          bucket.push(index);
        } else {
          indexMap.set(key, [index]);
        }
      });
      localMessages.forEach(local => {
        const key = keyFor(local);
        const bucket = indexMap.get(key);
        if (bucket && bucket.length) {
          const index = bucket.shift();
          if (index !== undefined) {
            local.ts = normalisedHistory[index].ts;
            local.local = false;
            normalisedHistory[index] = local;
          }
        } else {
          normalisedHistory.push(local);
          indexMap.set(key, []);
        }
      });
    }

    orchestratorState.messages = normalisedHistory;
    renderOrchestratorChat({ forceSidebar: true });
  } catch (error) {
    console.error("Error fetching chat history:", error);
    if (orchestratorState.messages.length === 0) {
      orchestratorState.messages = [getOrchestratorIntroMessage()];
    }
    renderOrchestratorChat({ forceSidebar: true });
  }
}

function renderOrchestratorChat({ forceSidebar = false } = {}) {
  if (forceSidebar || currentChatMode === "orchestrator") {
    renderSidebarMessages(orchestratorState.messages);
  }
}

function resetOrchestratorBrowserMirror() {
  orchestratorBrowserMirrorState.active = false;
  orchestratorBrowserMirrorState.useSseFallback = false;
  orchestratorBrowserMirrorState.messages.clear();
  orchestratorBrowserMirrorState.placeholder = null;
  orchestratorBrowserMirrorState.lastProgressAt = 0;
  if (orchestratorBrowserMirrorState.fallbackTimer) {
    clearTimeout(orchestratorBrowserMirrorState.fallbackTimer);
    orchestratorBrowserMirrorState.fallbackTimer = null;
  }
}

function startOrchestratorBrowserMirror({ placeholder = null } = {}) {
  orchestratorBrowserMirrorState.active = true;
  orchestratorBrowserMirrorState.useSseFallback = true;
  orchestratorBrowserMirrorState.messages.clear();
  orchestratorBrowserMirrorState.placeholder = placeholder || null;
  orchestratorBrowserMirrorState.lastProgressAt = 0;
  if (orchestratorBrowserMirrorState.fallbackTimer) {
    clearTimeout(orchestratorBrowserMirrorState.fallbackTimer);
    orchestratorBrowserMirrorState.fallbackTimer = null;
  }
}

function stopOrchestratorBrowserMirror() {
  resetOrchestratorBrowserMirror();
}

function disableOrchestratorBrowserMirrorFallback() {
  orchestratorBrowserMirrorState.useSseFallback = false;
  orchestratorBrowserMirrorState.lastProgressAt = Date.now();
  if (orchestratorBrowserMirrorState.fallbackTimer) {
    clearTimeout(orchestratorBrowserMirrorState.fallbackTimer);
  }
  orchestratorBrowserMirrorState.fallbackTimer = setTimeout(() => {
    if (!orchestratorBrowserMirrorState.active) return;
    const now = Date.now();
    if (now - orchestratorBrowserMirrorState.lastProgressAt >= 1800) {
      orchestratorBrowserMirrorState.useSseFallback = true;
    }
  }, 2000);
}

function mirrorBrowserMessageToOrchestrator(message) {
  if (!orchestratorBrowserMirrorState.active || !orchestratorBrowserMirrorState.useSseFallback) {
    return;
  }
  if (!message || message.role !== "assistant") return;
  const text = typeof message.text === "string" ? message.text.trim() : "";
  if (!text) return;

  ensureOrchestratorInitialized({ forceSidebar: currentChatMode === "orchestrator" });

  const label = ORCHESTRATOR_AGENT_LABELS.browser || "ブラウザエージェント";
  const formatted = `[${label}] ${text}`;
  const prefixedFormatted = prefixOrchestratorText(formatted);

  const key =
    typeof message.id === "number"
      ? `id:${message.id}`
      : typeof message.ts === "number"
        ? `ts:${message.ts}`
        : `text:${formatted}`;

  let target = orchestratorBrowserMirrorState.messages.get(key) || null;
  if (!target && orchestratorBrowserMirrorState.placeholder) {
    target = orchestratorBrowserMirrorState.placeholder;
    orchestratorBrowserMirrorState.placeholder = null;
  }

  if (target) {
    target.text = prefixedFormatted;
    target.pending = false;
    target.ts = Date.now();
  } else {
    const appended = addOrchestratorAssistantMessage(prefixedFormatted);
    appended.pending = false;
    appended.ts = Date.now();
    target = appended;
  }

  orchestratorBrowserMirrorState.messages.set(key, target);
  moveOrchestratorMessageToEnd(target);

  if (currentChatMode === "orchestrator") {
    renderOrchestratorChat({ forceSidebar: true });
  }
}

export function ensureOrchestratorInitialized({ forceSidebar = false } = {}) {
  if (!orchestratorState.initialized) {
    orchestratorState.initialized = true;
    fetchChatHistory();
    startOrchestratorPolling();
  }
  renderOrchestratorChat({ forceSidebar });
}

function addOrchestratorUserMessage(text) {
  const message = { role: "user", text, ts: Date.now(), local: true };
  orchestratorState.messages.push(message);
  renderOrchestratorChat({ forceSidebar: currentChatMode === "orchestrator" });
  return message;
}

function addOrchestratorAssistantMessage(text, { pending = false } = {}) {
  const message = {
    role: "assistant",
    text: prefixOrchestratorText(text ?? ""),
    pending,
    ts: Date.now(),
  };
  orchestratorState.messages.push(message);
  renderOrchestratorChat({ forceSidebar: currentChatMode === "orchestrator" });
  return message;
}

function moveOrchestratorMessageToEnd(message) {
  if (!message) return;
  const index = orchestratorState.messages.indexOf(message);
  if (index === -1 || index === orchestratorState.messages.length - 1) {
    return;
  }
  orchestratorState.messages.splice(index, 1);
  orchestratorState.messages.push(message);
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

function renderIotChat({ forceSidebar = false } = {}) {
  if (forceSidebar || currentChatMode === "iot") {
    renderSidebarMessages(iotChatState.messages);
  }
}

function renderSchedulerChat({ forceSidebar = false } = {}) {
  if (forceSidebar || currentChatMode === "scheduler") {
    renderSidebarMessages(schedulerChatState.messages);
  }
}

function pushSchedulerMessage(role, text, { pending = false, addToHistory = true } = {}) {
  const normalizedRole = role === "user" ? "user" : "assistant";
  const message = {
    role: normalizedRole,
    text: text ?? "",
    ts: Date.now(),
  };
  if (pending) {
    message.pending = true;
  }
  schedulerChatState.messages.push(message);
  if (addToHistory) {
    schedulerChatState.history.push({ role: normalizedRole, content: message.text });
  }
  return message;
}

function pushIotMessage(role, text, { pending = false, addToHistory = true } = {}) {
  const normalizedRole = role === "user" ? "user" : "assistant";
  const message = {
    role: normalizedRole,
    text: text ?? "",
    ts: Date.now(),
  };
  if (pending) {
    message.pending = true;
  }
  iotChatState.messages.push(message);
  if (addToHistory) {
    iotChatState.history.push({ role: normalizedRole, content: message.text });
  }
  return message;
}

export function ensureIotChatInitialized({ forceSidebar = false } = {}) {
  ensureIotDashboardInitialized();
  if (!iotChatState.initialized) {
    iotChatState.initialized = true;
    iotChatState.messages = [];
    iotChatState.history = [];
    pushIotMessage("assistant", IOT_CHAT_GREETING, { addToHistory: true });
  }
  if (forceSidebar || currentChatMode === "iot") {
    renderIotChat({ forceSidebar });
  }
}

function normalizeSchedulerHistoryEntry(entry) {
  if (!entry || typeof entry !== "object") return null;
  const roleRaw = typeof entry.role === "string" ? entry.role.trim().toLowerCase() : "";
  if (!roleRaw || (roleRaw !== "user" && roleRaw !== "assistant")) return null;
  const content = typeof entry.content === "string" ? entry.content : "";
  const ts = entry.timestamp ? Date.parse(entry.timestamp) : Date.now();
  return {
    role: roleRaw,
    text: content,
    ts: Number.isFinite(ts) ? ts : Date.now(),
  };
}

async function loadSchedulerChatHistory({ showLoading = false, forceSidebar = false } = {}) {
  if (showLoading) {
    schedulerChatState.messages = [
      { role: "assistant", text: THINKING_MESSAGE_TEXT, pending: true, ts: Date.now() },
    ];
    renderSchedulerChat({ forceSidebar });
  }

  try {
    const { data, unavailable } = await schedulerAgentRequest("/api/chat/history");
    if (unavailable || data?.status === "unavailable") {
      schedulerChatState.messages = [
        { role: "assistant", text: `${SCHEDULER_CHAT_GREETING}\n（Scheduler エージェントに接続できません）`, ts: Date.now() },
      ];
      schedulerChatState.history = [];
    } else {
      const entries = Array.isArray(data.history) ? data.history.map(normalizeSchedulerHistoryEntry).filter(Boolean) : [];
      if (entries.length) {
        schedulerChatState.messages = entries;
        schedulerChatState.history = entries.map(entry => ({ role: entry.role, content: entry.text }));
      } else {
        schedulerChatState.messages = [];
        schedulerChatState.history = [];
        pushSchedulerMessage("assistant", SCHEDULER_CHAT_GREETING, { addToHistory: false });
      }
    }
  } catch (error) {
    schedulerChatState.messages = [
      { role: "assistant", text: `${SCHEDULER_CHAT_GREETING}\n（履歴の取得に失敗しました: ${error.message}）`, ts: Date.now() },
    ];
    schedulerChatState.history = [];
  }

  renderSchedulerChat({ forceSidebar });
}

export function ensureSchedulerChatInitialized({ forceSidebar = false } = {}) {
  if (!schedulerChatState.initialized) {
    schedulerChatState.initialized = true;
    loadSchedulerChatHistory({ showLoading: true, forceSidebar });
  } else if (forceSidebar || currentChatMode === "scheduler") {
    renderSchedulerChat({ forceSidebar });
  }
}

const BROWSER_AGENT_BASE_HINTS = (() => {
  const sanitize = value => (typeof value === "string" ? value.trim() : "");
  const entries = new Set();

  const addCandidate = value => {
    if (!value) return;
    const stringValue = sanitize(value);
    if (!stringValue) return;
    stringValue.split(",").forEach(part => {
      const trimmed = sanitize(part);
      if (trimmed) {
        entries.add(trimmed);
      }
    });
  };

  let queryValue = "";
  try {
    queryValue = new URLSearchParams(window.location.search).get("browser_agent_base") || "";
  } catch (_) {
    queryValue = "";
  }

  addCandidate(queryValue);
  addCandidate(window.BROWSER_AGENT_API_BASE);
  const metaContent = document.querySelector("meta[name='browser-agent-api-base']")?.content;
  addCandidate(metaContent);

  return Array.from(entries);
})();

function resolveBrowserAgentBase() {
  const sanitize = value => (typeof value === "string" ? value.trim().replace(/\/+$/, "") : "");
  for (const hint of BROWSER_AGENT_BASE_HINTS) {
    const cleaned = sanitize(hint);
    if (cleaned) {
      return cleaned;
    }
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

async function browserAgentRequest(path, { method = "GET", headers = {}, body, signal } = {}) {
  const url = buildBrowserAgentUrl(path);
  const finalHeaders = { ...headers };
  const hasBody = body !== undefined && body !== null;
  const isFormData = typeof FormData !== "undefined" && body instanceof FormData;
  if (hasBody && !isFormData && !finalHeaders["Content-Type"]) {
    finalHeaders["Content-Type"] = "application/json";
  }

  let response;
  try {
    response = await fetch(url, {
      method,
      headers: finalHeaders,
      body,
      signal,
      mode: "cors",
    });
  } catch (error) {
    markAgentUnavailable("browser", error?.message || "接続に失敗しました。");
    return { data: { status: "unavailable", message: "ブラウザエージェントに接続できません。", error: error?.message }, status: 0, unavailable: true };
  }

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
    if (response.status >= 500) {
      markAgentUnavailable("browser", message);
      return { data: { status: "unavailable", message: "ブラウザエージェントに接続できません。", error: message }, status: response.status, unavailable: true };
    }
    const error = new Error(message);
    error.status = response.status;
    throw error;
  }

  const payload = typeof data === "string" ? { message: data } : data;
  if (payload && payload.status === "unavailable") {
    markAgentUnavailable("browser", payload.error || payload.message);
    return { data: payload, status: response.status, unavailable: true };
  }
  markAgentAvailable("browser");
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
  browserChatState.promptQueue = [];
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
  mirrorBrowserMessageToOrchestrator(message);
}

function updateBrowserChatMessage(raw) {
  const message = normalizeBrowserAgentMessage(raw);
  if (typeof message.id === "number" && browserMessageIndex.has(message.id)) {
    const index = browserMessageIndex.get(message.id);
    if (index !== undefined) {
      browserChatState.messages[index] = message;
      renderBrowserChat();
      mirrorBrowserMessageToOrchestrator(message);
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

function updatePauseButtonState(mode = currentChatMode) {
  if (!sidebarPauseBtn) return;
  const showBrowserControls = mode === "browser" || (mode === "orchestrator" && isGeneralProxyAgentBrowser());
  const label = browserChatState.paused ? "再開" : "一時停止";
  sidebarPauseBtn.setAttribute("aria-pressed", browserChatState.paused ? "true" : "false");
  sidebarPauseBtn.setAttribute("aria-label", label);
  if (sidebarPauseSr) {
    sidebarPauseSr.textContent = label;
  }
  if (sidebarPauseIcon) {
    sidebarPauseIcon.innerHTML = browserChatState.paused ? ICON_PLAY : ICON_PAUSE;
  }
  sidebarPauseBtn.disabled = !showBrowserControls || (!browserChatState.agentRunning && !browserChatState.paused);
}

function updateIotPauseButtonState() {
  if (!sidebarPauseBtn) return;
  const label = iotChatState.paused ? "再開" : "一時停止";
  sidebarPauseBtn.setAttribute("aria-pressed", iotChatState.paused ? "true" : "false");
  sidebarPauseBtn.setAttribute("aria-label", label);
  if (sidebarPauseSr) {
    sidebarPauseSr.textContent = label;
  }
  if (sidebarPauseIcon) {
    sidebarPauseIcon.innerHTML = iotChatState.paused ? ICON_PLAY : ICON_PAUSE;
  }
  sidebarPauseBtn.disabled = false;
}

function updateSidebarControlsForMode(mode) {
  if (sidebarChatUtilities) {
    sidebarChatUtilities.hidden = false;
  }
  if (mode === "browser") {
    if (sidebarResetBtn) {
      sidebarResetBtn.disabled = false;
    }
    if (sidebarChatSend) {
      sidebarChatSend.disabled = browserChatState.paused;
    }
    updatePauseButtonState(mode);
    return;
  }

  if (mode === "iot") {
    if (sidebarResetBtn) {
      sidebarResetBtn.disabled = false;
    }
    if (sidebarChatSend) {
      sidebarChatSend.disabled = iotChatState.paused || iotChatState.sending;
    }
    updateIotPauseButtonState();
    return;
  }

  if (mode === "scheduler") {
    if (sidebarResetBtn) {
      sidebarResetBtn.disabled = false;
    }
    if (sidebarChatSend) {
      sidebarChatSend.disabled = schedulerChatState.sending;
    }
    if (sidebarPauseBtn) {
      sidebarPauseBtn.setAttribute("aria-pressed", "false");
      sidebarPauseBtn.setAttribute("aria-label", "一時停止");
      if (sidebarPauseSr) {
        sidebarPauseSr.textContent = "一時停止";
      }
      if (sidebarPauseIcon) {
        sidebarPauseIcon.innerHTML = ICON_PAUSE;
      }
      sidebarPauseBtn.disabled = true;
    }
    return;
  }

  if (mode === "orchestrator") {
    const isBrowserAgentActive = isGeneralProxyAgentBrowser();
    if (sidebarResetBtn) {
      sidebarResetBtn.disabled = false;
    }
    if (sidebarChatSend) {
      sidebarChatSend.disabled = isBrowserAgentActive ? browserChatState.paused : orchestratorState.sending;
    }
    if (isBrowserAgentActive) {
      updatePauseButtonState(mode);
    } else {
      if (sidebarPauseBtn) {
        sidebarPauseBtn.setAttribute("aria-pressed", "false");
        sidebarPauseBtn.setAttribute("aria-label", "一時停止");
        if (sidebarPauseSr) {
          sidebarPauseSr.textContent = "一時停止";
        }
        if (sidebarPauseIcon) {
          sidebarPauseIcon.innerHTML = ICON_PAUSE;
        }
        sidebarPauseBtn.disabled = true;
      }
    }
    return;
  }

  if (sidebarResetBtn) {
    sidebarResetBtn.disabled = false;
  }
  if (sidebarChatSend) {
    sidebarChatSend.disabled = chatState.sending;
  }
  if (sidebarPauseBtn) {
    sidebarPauseBtn.setAttribute("aria-pressed", "false");
    sidebarPauseBtn.setAttribute("aria-label", "一時停止");
    if (sidebarPauseSr) {
      sidebarPauseSr.textContent = "一時停止";
    }
    if (sidebarPauseIcon) {
      sidebarPauseIcon.innerHTML = ICON_PAUSE;
    }
    sidebarPauseBtn.disabled = true;
  }
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
  if (typeof payload.run_summary === "string") {
    const trimmed = payload.run_summary.trim();
    if (trimmed) {
      const alreadyExists = browserChatState.messages.some(
        message => typeof message.text === "string" && message.text.trim() === trimmed,
      );
      if (!alreadyExists) {
        addBrowserSystemMessage(trimmed, {
          forceSidebar: currentChatMode === "browser",
        });
      }

      // When the orchestrator is steering the Browser Agent from the General view,
      // mirror the agent's own completion summary into the orchestrator chat as a
      // fallback (e.g. if the orchestrator stream misses the final payload).
      const shouldMirrorToOrchestrator =
        orchestratorBrowserMirrorState.active
        && orchestratorBrowserMirrorState.useSseFallback
        && isGeneralProxyAgentBrowser();
      if (shouldMirrorToOrchestrator) {
        mirrorBrowserMessageToOrchestrator({
          role: "assistant",
          text: trimmed,
          ts: Date.now(),
        });
      }

      if (containsBrowserAgentFinalMarker(trimmed)) {
        stopOrchestratorBrowserMirror();
        if (isGeneralProxyAgentBrowser()) {
          setGeneralProxyAgent(null);
        }
      }
    }
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
      { id: null, role: "system", text: THINKING_MESSAGE_TEXT, pending: true, ts: Date.now() },
    ];
    browserMessageIndex.clear();
    renderBrowserChat({ forceSidebar });
  }
  try {
    const { data, unavailable } = await browserAgentRequest("/api/history", { signal: controller.signal });
    if (controller.signal.aborted) return;
    if (unavailable || data?.status === "unavailable") {
      browserChatState.messages = [
        { id: null, role: "system", text: data?.message || "ブラウザエージェントに接続できません。", ts: Date.now() },
      ];
      browserMessageIndex.clear();
      renderBrowserChat({ forceSidebar });
      return;
    }
    setBrowserChatHistory(data.messages || [], { forceSidebar });
  } catch (error) {
    if (controller.signal.aborted) return;
    browserChatState.messages = [
      { id: null, role: "system", text: `履歴の取得に失敗しました: ${error.message}`, ts: Date.now() },
    ];
    browserMessageIndex.clear();
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
        markAgentUnavailable("browser", "イベントストリームに接続できません。");
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

export function ensureBrowserAgentInitialized({ showLoading = false, forceSidebar = false } = {}) {
  connectBrowserEventStream();
  if (!browserChatState.initialized) {
    browserChatState.initialized = true;
    loadBrowserAgentHistory({ showLoading: true, forceSidebar });
  } else {
    loadBrowserAgentHistory({ showLoading, forceSidebar });
  }
}

function resolveBrowserPromptSendOptions() {
  if (browserChatState.agentRunning && browserChatState.sending) {
    return {
      allowDuringSend: true,
      allowQueue: false,
      silentQueue: true,
    };
  }
  return undefined;
}

async function sendBrowserAgentPrompt(text, options = {}) {
  const {
    allowQueue = true,
    queuePriority = "tail",
    silentQueue = false,
    allowDuringSend = false,
    startNewTask = false,
  } = options;

  const prompt = typeof text === "string" ? text.trim() : "";
  if (!prompt) return;

  const isBrowserContextActive = currentChatMode === "browser"
    || (currentChatMode === "orchestrator" && isGeneralProxyAgentBrowser());
  const isFollowUpWhileSending = Boolean(allowDuringSend && browserChatState.sending);

  if (startNewTask && orchestratorBrowserTaskActive) {
    addBrowserSystemMessage(
      "オーケストレーターがブラウザエージェントを使用中のため、新しいタスクを開始できません。",
      { forceSidebar: isBrowserContextActive },
    );
    return;
  }

  const enqueuePrompt = () => {
    if (!allowQueue) {
      return false;
    }
    const entryOptions = { ...options, allowQueue: false, silentQueue: true };
    const entry = { text: prompt, options: entryOptions };
    if (queuePriority === "front") {
      browserChatState.promptQueue.unshift(entry);
    } else {
      browserChatState.promptQueue.push(entry);
    }
    if (!silentQueue) {
      const preview = prompt.length > 80 ? `${prompt.slice(0, 77)}...` : prompt;
      addBrowserSystemMessage(`前の操作が完了したら実行します: ${preview}`, {
        forceSidebar: isBrowserContextActive,
      });
    }
    return true;
  };

  if (browserChatState.sending && !isFollowUpWhileSending) {
    if (enqueuePrompt()) {
      updateSidebarControlsForMode(currentChatMode);
    }
    return;
  }

  connectBrowserEventStream();
  let shouldResetSending = false;
  browserChatState.agentRunning = true;
  browserChatState.paused = false;
  if (!isFollowUpWhileSending) {
    browserChatState.sending = true;
    shouldResetSending = true;
  }
  updateSidebarControlsForMode(currentChatMode);
  try {
    const payload = { prompt };
    if (startNewTask) {
      payload.new_task = true;
    }
    const { data, unavailable } = await browserAgentRequest("/api/chat", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    if (unavailable || data?.status === "unavailable") {
      addBrowserSystemMessage(data?.message || "ブラウザエージェントに接続できません。", { forceSidebar: isBrowserContextActive });
      return;
    }
    if (Array.isArray(data.messages)) {
      setBrowserChatHistory(data.messages, { forceSidebar: currentChatMode === "browser" });
    }
    if (typeof data.run_summary === "string" && data.run_summary.trim()) {
      // 既に履歴に含まれているため、ここでは追加しない
    }
  } catch (error) {
    addBrowserSystemMessage(`送信に失敗しました: ${error.message}`, { forceSidebar: isBrowserContextActive });
  } finally {
    if (shouldResetSending) {
      browserChatState.sending = false;
      updateSidebarControlsForMode(currentChatMode);
      const nextEntry = browserChatState.promptQueue.shift();
      if (nextEntry) {
        setTimeout(() => {
          sendBrowserAgentPrompt(nextEntry.text, nextEntry.options);
        }, 0);
      }
    } else {
      updateSidebarControlsForMode(currentChatMode);
    }
  }
}

async function sendIotChatMessage(text) {
  if (!text || iotChatState.sending || iotChatState.paused) return;
  ensureIotChatInitialized({ forceSidebar: currentChatMode === "iot" });
  iotChatState.sending = true;
  updateSidebarControlsForMode(currentChatMode);

  const userMessage = pushIotMessage("user", text);
  renderIotChat({ forceSidebar: currentChatMode === "iot" });

  const pending = pushIotMessage("assistant", THINKING_MESSAGE_TEXT, { pending: true, addToHistory: false });
  renderIotChat({ forceSidebar: currentChatMode === "iot" });

  try {
    const payload = {
      messages: iotChatState.history.map(entry => ({ role: entry.role, content: entry.content })),
    };
    const { data, unavailable } = await iotAgentRequest("/api/chat", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    if (unavailable || data?.status === "unavailable") {
      pending.text = data?.message || "IoT エージェントに接続できません。";
      pending.pending = false;
      pending.ts = Date.now();
      iotChatState.history.push({ role: "assistant", content: pending.text });
      return;
    }
    let reply = typeof data.reply === "string" ? data.reply.trim() : "";
    if (!reply) {
      reply = summarizeIotDevices() || "了解しました。";
    }
    pending.text = reply;
    pending.pending = false;
    pending.ts = Date.now();
    iotChatState.history.push({ role: "assistant", content: pending.text });
  } catch (error) {
    const fallback = summarizeIotDevices();
    pending.text = fallback || `エラーが発生しました: ${error.message}`;
    pending.pending = false;
    pending.ts = Date.now();
    iotChatState.history.push({ role: "assistant", content: pending.text });
  } finally {
    userMessage.ts = userMessage.ts || Date.now();
    iotChatState.sending = false;
    renderIotChat({ forceSidebar: currentChatMode === "iot" });
    updateSidebarControlsForMode(currentChatMode);
  }
}

async function sendSchedulerChatMessage(text) {
  if (!text || schedulerChatState.sending) return;
  ensureSchedulerChatInitialized({ forceSidebar: currentChatMode === "scheduler" });
  schedulerChatState.sending = true;
  updateSidebarControlsForMode(currentChatMode);

  const userMessage = pushSchedulerMessage("user", text);
  const pending = pushSchedulerMessage("assistant", THINKING_MESSAGE_TEXT, { pending: true, addToHistory: false });
  renderSchedulerChat({ forceSidebar: currentChatMode === "scheduler" });

  try {
    const payload = {
      messages: schedulerChatState.history.map(entry => ({ role: entry.role, content: entry.content })),
    };
    const { data, unavailable } = await schedulerAgentRequest("/api/chat", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    if (unavailable || data?.status === "unavailable") {
      pending.text = data?.message || "Scheduler エージェントに接続できません。";
      pending.pending = false;
      pending.ts = Date.now();
      schedulerChatState.history.push({ role: "assistant", content: pending.text });
      return;
    }
    const reply = typeof data.reply === "string" ? data.reply.trim() : "";
    pending.text = reply || "了解しました。";
    pending.pending = false;
    pending.ts = Date.now();
    schedulerChatState.history.push({ role: "assistant", content: pending.text });
  } catch (error) {
    pending.text = `エラーが発生しました: ${error.message}`;
    pending.pending = false;
    pending.ts = Date.now();
  } finally {
    userMessage.ts = userMessage.ts || Date.now();
    schedulerChatState.sending = false;
    renderSchedulerChat({ forceSidebar: currentChatMode === "scheduler" });
    updateSidebarControlsForMode(currentChatMode);
  }
}

export function setChatMode(mode) {
  if (!{"browser": true, "general": true, "iot": true, "orchestrator": true, "scheduler": true}[mode]) {
    mode = "general";
  }
  if (currentChatMode !== mode) {
    currentChatMode = mode;
  }
  updateSidebarControlsForMode(mode);
  if (mode === "browser") {
    renderBrowserChat({ forceSidebar: true });
  } else if (mode === "iot") {
    ensureIotChatInitialized({ forceSidebar: true });
    renderIotChat({ forceSidebar: true });
  } else if (mode === "scheduler") {
    ensureSchedulerChatInitialized({ forceSidebar: true });
    renderSchedulerChat({ forceSidebar: true });
  } else if (mode === "orchestrator") {
    ensureOrchestratorInitialized({ forceSidebar: true });
    renderOrchestratorChat({ forceSidebar: true });
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
      { role: "system", text: THINKING_MESSAGE_TEXT, pending: true, ts: Date.now() },
    ];
    renderGeneralChat();
  }
  try {
    const data = await lifestyleRequest("/conversation_history");
    if (data?.status === "unavailable") {
      const message = data.message || "Life-Styleエージェントに接続できません。";
      if (showLoading && (!chatState.sending || force)) {
        chatState.messages = [
          getIntroMessage(),
          { role: "system", text: message, ts: Date.now() },
        ];
        renderGeneralChat();
      }
      return;
    }
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
  return document.querySelector("#view-chat")?.classList.contains("active");
}

async function refreshSummaryBox({ showLoading = false } = {}) {
  if (!summaryBox) return;
  if (showLoading) {
    summaryBox.textContent = SUMMARY_LOADING_TEXT;
  }
  try {
    const data = await lifestyleRequest("/conversation_summary");
    if (data?.status === "unavailable") {
      summaryBox.textContent = data.message || "Life-Styleエージェントに接続できません。";
      return;
    }
    const summary = (data.summary || "").trim();
    summaryBox.textContent = summary ? summary : SUMMARY_PLACEHOLDER;
  } catch (error) {
    summaryBox.textContent = `要約の取得に失敗しました: ${error.message}`;
  }
}

export function ensureChatInitialized({ showLoadingSummary = false } = {}) {
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
    text: "",
    pending: true,
  };
  chatState.messages.push(message);
  renderGeneralChat();
  return message;
}

async function sendOrchestratorMessage(text) {
  if (!text || orchestratorState.sending) return;
  ensureOrchestratorInitialized({ forceSidebar: currentChatMode === "orchestrator" });
  orchestratorState.sending = true;
  updateSidebarControlsForMode(currentChatMode);

  const userMessage = addOrchestratorUserMessage(text);
  const planMessage = addOrchestratorAssistantMessage(THINKING_MESSAGE_TEXT, { pending: true });
  resetOrchestratorBrowserMirror();

  const taskMessages = new Map();

  const ensureTaskEntry = taskIndex => {
    if (typeof taskIndex !== "number") return null;
    let entry = taskMessages.get(taskIndex);
    if (!entry || typeof entry !== "object" || entry === null) {
      entry = { placeholder: null, progress: new Map() };
      taskMessages.set(taskIndex, entry);
      return entry;
    }
    if (!(entry.progress instanceof Map)) {
      entry.progress = new Map();
    }
    if (!("placeholder" in entry)) {
      entry.placeholder = null;
    }
    return entry;
  };

  const clearTaskProgressMessages = entry => {
    if (!entry || !(entry.progress instanceof Map)) {
      return;
    }
    entry.progress.forEach(message => {
      if (!message) {
        return;
      }
      const index = orchestratorState.messages.indexOf(message);
      if (index !== -1) {
        orchestratorState.messages.splice(index, 1);
      }
    });
    entry.progress.clear();
  };

  try {
    for await (const { event: eventType, data: payload } of orchestratorRequest(text, {
      view: "general",
      logHistory: true,
    })) {
      const eventData = payload && typeof payload === "object" ? payload : {};

      if (eventType === "plan") {
        const state = eventData.state && typeof eventData.state === "object" ? eventData.state : {};
        const planSummary = typeof state.plan_summary === "string" ? state.plan_summary.trim() : "";
        const tasks = Array.isArray(state.tasks) ? state.tasks : [];
        const hasTasks = tasks.length > 0;
        let textValue = "";
        if (planSummary) {
          textValue = hasTasks ? `計画: ${planSummary}` : planSummary;
        } else if (!hasTasks) {
          textValue = "今回のリクエストでは実行すべきタスクはありませんでした。";
        } else {
          textValue = "計画を作成しました。タスクを実行します…";
        }
        planMessage.text = prefixOrchestratorText(textValue);
        planMessage.pending = false;
        planMessage.ts = Date.now();
        renderOrchestratorChat({ forceSidebar: currentChatMode === "orchestrator" });
        continue;
      }

      if (eventType === "before_execution") {
        // planMessageがまだpending状態の場合は解除する（2つのthinkingインジケーター表示を防ぐ）
        if (planMessage.pending) {
          planMessage.text = prefixOrchestratorText("計画を作成しました。タスクを実行します…");
          planMessage.pending = false;
          planMessage.ts = Date.now();
        }
        const task = eventData.task && typeof eventData.task === "object" ? eventData.task : {};
        const taskIndex = typeof eventData.task_index === "number" ? eventData.task_index : null;
        const agentRaw = typeof task.agent === "string" ? task.agent.trim().toLowerCase() : "";
        const commandText = typeof task.command === "string" ? task.command.trim() : "";
        const agentLabel = agentRaw ? (ORCHESTRATOR_AGENT_LABELS[agentRaw] || agentRaw) : "エージェント";
        const displayText = commandText
          ? `[${agentLabel}] ${commandText}`
          : `[${agentLabel}] タスクを実行しています…`;
        const message = addOrchestratorAssistantMessage(displayText, { pending: true });
        message.ts = Date.now();
        if (taskIndex !== null) {
          const entry = ensureTaskEntry(taskIndex);
          if (entry) {
            entry.placeholder = message;
          }
        }
        if (agentRaw) {
          if (agentRaw === "browser") {
            orchestratorBrowserTaskActive = true;
          }
          setGeneralProxyAgent(agentRaw);
          if (agentRaw === "browser") {
            ensureBrowserAgentInitialized({ showLoading: true });
            startOrchestratorBrowserMirror({ placeholder: message });
            // オーケストレーター側でブラウザエージェントが既に起動しているため、
            // フロントエンドから同じコマンドを再送しない。
          }
        }
        continue;
      }

      if (eventType === "browser_init") {
        // 初期化イベントはクライアント側でハンドオフ済みなので何もしない
        continue;
      }

      if (eventType === "execution_progress") {
        const task = eventData.task && typeof eventData.task === "object" ? eventData.task : {};
        const taskIndex = typeof eventData.task_index === "number" ? eventData.task_index : null;
        const progress = eventData.progress && typeof eventData.progress === "object" ? eventData.progress : {};
        const agentRaw = typeof task.agent === "string" ? task.agent.trim().toLowerCase() : "";
        const agentLabel = agentRaw ? (ORCHESTRATOR_AGENT_LABELS[agentRaw] || agentRaw) : "エージェント";
        const textValue = typeof progress.text === "string" ? progress.text.trim() : "";
        if (!textValue) {
          continue;
        }
        if (agentRaw === "browser") {
          disableOrchestratorBrowserMirrorFallback();
        }
        const messageId = typeof progress.message_id === "number" ? progress.message_id : null;
        const formatted = `[${agentLabel}] ${textValue}`;
        if (taskIndex === null) {
          const fallbackMessage = addOrchestratorAssistantMessage(formatted);
          fallbackMessage.pending = false;
          fallbackMessage.ts = Date.now();
          continue;
        }
        const entry = ensureTaskEntry(taskIndex);
        if (!entry) {
          const fallbackMessage = addOrchestratorAssistantMessage(formatted);
          fallbackMessage.pending = false;
          fallbackMessage.ts = Date.now();
          continue;
        }
        if (!(entry.progress instanceof Map)) {
          entry.progress = new Map();
        }
        const existingProgress = messageId !== null ? entry.progress.get(messageId) : null;
        if (existingProgress) {
          existingProgress.text = formatted;
          existingProgress.pending = false;
          existingProgress.ts = Date.now();
        } else {
          const progressMessage = addOrchestratorAssistantMessage(formatted);
          progressMessage.pending = false;
          progressMessage.ts = Date.now();
          if (messageId !== null) {
            entry.progress.set(messageId, progressMessage);
          }
        }
        renderOrchestratorChat({ forceSidebar: currentChatMode === "orchestrator" });
        continue;
      }

      if (eventType === "after_execution") {
        const task = eventData.task && typeof eventData.task === "object" ? eventData.task : {};
        const taskIndex = typeof eventData.task_index === "number" ? eventData.task_index : null;
        const result = eventData.result && typeof eventData.result === "object" ? eventData.result : {};
        const agentRaw = typeof task.agent === "string" ? task.agent.trim().toLowerCase() : "";
        const agentLabel = agentRaw ? (ORCHESTRATOR_AGENT_LABELS[agentRaw] || agentRaw) : "エージェント";
        const status = typeof result.status === "string" ? result.status : "";
        const isClarification = status === "needs_info";
        const isFinalized = Boolean(result.finalized) || status === "error" || isClarification;
        const responseText = typeof result.response === "string" ? result.response.trim() : "";
        const errorText = typeof result.error === "string" ? result.error.trim() : "";
        const finalText = status === "error"
          ? `[${agentLabel}] ${errorText || "タスクの実行に失敗しました。"}`
          : isClarification
            ? `[${agentLabel}] ${responseText || "このタスクを実行するには追加の指示が必要です。"}`
            : `[${agentLabel}] ${responseText || "タスクを完了しました。"}`;
        const entry = taskIndex !== null ? ensureTaskEntry(taskIndex) : null;
        const existing = entry && entry.placeholder ? entry.placeholder : null;
        const targetMessage = existing || addOrchestratorAssistantMessage(finalText);
        targetMessage.text = prefixOrchestratorText(finalText);
        targetMessage.pending = false;
        targetMessage.ts = Date.now();
        moveOrchestratorMessageToEnd(targetMessage);
        if (entry) {
          entry.placeholder = targetMessage;
        }
        if (agentRaw === "browser") {
          clearTaskProgressMessages(entry);
          clearOrchestratorBrowserMirrorMessages({ preserve: targetMessage });
          orchestratorBrowserTaskActive = false;
          stopOrchestratorBrowserMirror();
          if (isFinalized && isGeneralProxyAgentBrowser()) {
            setGeneralProxyAgent(null);
          }
        }
        renderOrchestratorChat({ forceSidebar: currentChatMode === "orchestrator" });
        continue;
      }

      if (eventType === "error") {
        const errorText = typeof eventData.error === "string" ? eventData.error : "エラーが発生しました。";
        planMessage.text = prefixOrchestratorText(`エラー: ${errorText}`);
        planMessage.pending = false;
        planMessage.ts = Date.now();
        orchestratorBrowserTaskActive = false;
        setGeneralProxyAgent(null);
        clearOrchestratorBrowserMirrorMessages();
        stopOrchestratorBrowserMirror();
        renderOrchestratorChat({ forceSidebar: currentChatMode === "orchestrator" });
        break;
      }

      if (eventType === "complete") {
        const assistantMessages = Array.isArray(eventData.assistant_messages) ? eventData.assistant_messages : [];
        if (assistantMessages.length > 0) {
          const [first, ...rest] = assistantMessages;
          const firstType = typeof first?.type === "string" ? first.type : "";
          const firstText = typeof first?.text === "string" ? first.text.trim() : "";
          if (firstType === "plan" || firstType === "status") {
            if (firstText) {
              planMessage.text = prefixOrchestratorText(firstText);
              planMessage.pending = false;
              planMessage.ts = planMessage.ts || Date.now();
            }
          } else if (firstType === "execution") {
            const planIndex = orchestratorState.messages.indexOf(planMessage);
            if (planIndex !== -1) {
              orchestratorState.messages.splice(planIndex, 1);
            }
          }

          const remaining = firstType === "plan" || firstType === "status" ? rest : assistantMessages;
          remaining.forEach(entry => {
            const textValue = typeof entry?.text === "string" ? entry.text.trim() : "";
            if (!textValue) return;
            const prefixedValue = prefixOrchestratorText(textValue);
            const normalizedValue = normalizeAssistantText(prefixedValue);
            if (!normalizedValue) return;
            const alreadyExists = orchestratorState.messages.some(
              message => message.role === "assistant" && normalizeAssistantText(message.text) === normalizedValue,
            );
            if (!alreadyExists) {
              addOrchestratorAssistantMessage(prefixedValue);
            }
          });
        }
        const state = eventData.state && typeof eventData.state === "object" ? eventData.state : {};
        const finalProxyAgent = determineGeneralProxyAgentFromResult({
          executions: state.executions,
          tasks: state.tasks,
        });
        setGeneralProxyAgent(finalProxyAgent);
        orchestratorBrowserTaskActive = false;
        stopOrchestratorBrowserMirror();
        renderOrchestratorChat({ forceSidebar: currentChatMode === "orchestrator" });
        break;
      }
    }
  } catch (error) {
    planMessage.text = prefixOrchestratorText(`エラー: ${error.message}`);
    planMessage.pending = false;
    planMessage.ts = Date.now();
    orchestratorBrowserTaskActive = false;
    setGeneralProxyAgent(null);
    clearOrchestratorBrowserMirrorMessages();
    stopOrchestratorBrowserMirror();
  } finally {
    if (userMessage) {
      userMessage.local = false;
    }
    userMessage.ts = userMessage.ts || Date.now();
    orchestratorState.sending = false;
    orchestratorBrowserTaskActive = false;
    stopOrchestratorBrowserMirror();
    renderOrchestratorChat({ forceSidebar: currentChatMode === "orchestrator" });
    updateSidebarControlsForMode(currentChatMode);
  }
}

async function sendChatMessage(text) {
  if (!text || chatState.sending) return;
  ensureChatInitialized();
  chatState.sending = true;

  addUserMessage(text);
  pendingAssistantMessage = addPendingAssistantMessage();

  try {
    const payload = JSON.stringify({ question: text });
    const data = await lifestyleRequest("/rag_answer", { method: "POST", body: payload });
    if (data?.status === "unavailable") {
      const fallback = data.message || "Life-Styleエージェントに接続できません。";
      if (pendingAssistantMessage) {
        pendingAssistantMessage.text = fallback;
        pendingAssistantMessage.pending = false;
        pendingAssistantMessage.ts = Date.now();
      }
      renderGeneralChat();
      return;
    }
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

function shouldRouteGeneralInputToBrowserAgent() {
  return (
    currentChatMode === "orchestrator"
    && isGeneralProxyAgentBrowser()
    && orchestratorBrowserTaskActive
  );
}

async function forwardGeneralInputToBrowserAgent(value) {
  if (!value) return;
  ensureOrchestratorInitialized({ forceSidebar: currentChatMode === "orchestrator" });
  addOrchestratorUserMessage(value);
  await sendBrowserAgentPrompt(value, {
    allowQueue: false,
    allowDuringSend: true,
    silentQueue: true,
  });
}

async function handleGeneralModeSubmission(value) {
  if (!value) return;
  if (shouldRouteGeneralInputToBrowserAgent()) {
    await forwardGeneralInputToBrowserAgent(value);
    return;
  }
  if (orchestratorState.sending) {
    if (shouldRouteGeneralInputToBrowserAgent()) {
      await forwardGeneralInputToBrowserAgent(value);
    }
    return;
  }
  await sendOrchestratorMessage(value);
}

function handleGeneralProxyAgentChange({ previousAgent, agent }) {
  if (previousAgent === "browser" && agent !== "browser") {
    stopOrchestratorBrowserMirror();
  }
  updateSidebarControlsForMode(currentChatMode);
}

function handleGeneralProxyRender({ view }) {
  if (!view) return;
  if (view === "browser") {
    ensureBrowserAgentInitialized({ showLoading: true });
    return;
  }
  if (view === "iot") {
    ensureIotDashboardInitialized({ showLoading: true });
    ensureIotChatInitialized({ forceSidebar: currentChatMode === "iot" });
    return;
  }
  if (view === "schedule") {
    ensureSchedulerAgentInitialized();
    ensureSchedulerChatInitialized({ forceSidebar: currentChatMode === "scheduler" });
    return;
  }
  if (view === "chat") {
    ensureChatInitialized({ showLoadingSummary: true });
  }
}

registerGeneralProxyAgentHook(handleGeneralProxyAgentChange);
registerGeneralProxyRenderHook(handleGeneralProxyRender);

if (chatForm) {
  chatForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const value = chatInput.value.trim();
    if (!value) return;
    chatInput.value = "";
    if (sidebarChatInput) sidebarChatInput.value = "";
    if (currentChatMode === "browser") {
      await sendBrowserAgentPrompt(value, resolveBrowserPromptSendOptions());
    } else if (currentChatMode === "iot") {
      await sendIotChatMessage(value);
    } else if (currentChatMode === "scheduler") {
      await sendSchedulerChatMessage(value);
    } else if (currentChatMode === "orchestrator") {
      await handleGeneralModeSubmission(value);
    } else {
      await sendChatMessage(value);
    }
  });
}

if (sidebarChatForm) {
  sidebarChatForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const value = sidebarChatInput.value.trim();
    if (!value) return;
    sidebarChatInput.value = "";
    if (chatInput) chatInput.value = "";
    if (currentChatMode === "browser") {
      await sendBrowserAgentPrompt(value, resolveBrowserPromptSendOptions());
    } else if (currentChatMode === "iot") {
      await sendIotChatMessage(value);
    } else if (currentChatMode === "scheduler") {
      await sendSchedulerChatMessage(value);
    } else if (currentChatMode === "orchestrator") {
      await handleGeneralModeSubmission(value);
    } else {
      await sendChatMessage(value);
    }
  });
}

  if (clearChatBtn) {
  clearChatBtn.addEventListener("click", async () => {
    if (!confirm("チャット履歴をクリアしますか？")) return;
    try {
      await lifestyleRequest("/reset_history", { method: "POST" });
      chatState.messages = [getIntroMessage()];      renderGeneralChat({ forceSidebar: true });
      await refreshSummaryBox({ showLoading: true });
    } catch (error) {
      alert(`チャット履歴のクリアに失敗しました: ${error.message}`);
    }
  });
}

if (sidebarPauseBtn) {
  sidebarPauseBtn.addEventListener("click", async () => {
    const isBrowserContext = currentChatMode === "browser" || (currentChatMode === "orchestrator" && isGeneralProxyAgentBrowser());
    if (isBrowserContext) {
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
      return;
    }

    if (currentChatMode === "iot") {
      iotChatState.paused = !iotChatState.paused;
      updateSidebarControlsForMode(currentChatMode);
      if (!iotChatState.paused) {
        (sidebarChatInput || chatInput)?.focus?.();
      }
    }
  });
}

if (sidebarResetBtn) {
  sidebarResetBtn.addEventListener("click", async () => {
    if (currentChatMode === "browser") {
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
      return;
    }

    if (currentChatMode === "iot") {
      if (!confirm("IoT エージェントのチャット履歴をリセットしますか？")) return;
      iotChatState.messages = [];
      iotChatState.history = [];
      iotChatState.sending = false;
      iotChatState.paused = false;
      ensureIotChatInitialized({ forceSidebar: true });
      updateSidebarControlsForMode(currentChatMode);
      return;
    }

    if (currentChatMode === "scheduler") {
      if (!confirm("Scheduler-Agent のチャット履歴をリセットしますか？")) return;
      try {
        await schedulerAgentRequest("/api/chat/history", { method: "DELETE" });
      } catch (error) {
        alert(`チャット履歴のリセットに失敗しました: ${error.message}`);
      }
      schedulerChatState.messages = [];
      schedulerChatState.history = [];
      schedulerChatState.sending = false;
      ensureSchedulerChatInitialized({ forceSidebar: true });
      updateSidebarControlsForMode(currentChatMode);
      return;
    }

    if (currentChatMode === "orchestrator") {
      if (isGeneralProxyAgentBrowser()) {
        if (!confirm("会話履歴をリセットしますか？ブラウザエージェントは一時停止されます。")) return;
        try {
          await fetch("/reset_chat_history", { method: "POST" });
          try {
            await browserAgentRequest("/api/pause", { method: "POST" });
            browserChatState.paused = true;
          } catch (pauseError) {
            console.warn("Failed to pause browser agent during reset:", pauseError);
          }
          setGeneralProxyAgent(null);
          await fetchChatHistory();
          updateSidebarControlsForMode(currentChatMode);
        } catch (error) {
          console.error("Error resetting chat history:", error);
          alert(`チャット履歴のリセットに失敗しました: ${error.message}`);
        }
      } else {
        if (!confirm("チャット履歴をリセットしますか？")) return;
        try {
          await fetch("/reset_chat_history", { method: "POST" });
          await fetchChatHistory();
        } catch (error) {
          console.error("Error resetting chat history:", error);
          alert(`チャット履歴のリセットに失敗しました: ${error.message}`);
        }
      }
      return;
    }
  });
}
