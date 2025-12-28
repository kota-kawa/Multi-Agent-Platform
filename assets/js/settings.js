import { $, $$ } from "./dom-utils.js";

const settingsBtn = $("#settingsBtn");
const dialog = $("#settingsDialog");
const form = $("#settingsForm");
const closeBtn = $("#settingsCloseBtn");
const refreshBtn = $("#settingsRefreshBtn");
const saveBtn = $("#settingsSaveBtn");
const memoryToggle = $("#settingsMemoryToggle");
const chatCountValue = $("#chatCountValue");
const chatCountNote = $("#chatCountNote");
const statusMessage = $("#settingsStatusMessage");
const historySyncToggle = $("#settingsHistorySyncToggle");

// Agent toggles
const agentToggleBrowser = $("#agentToggleBrowser");
const agentToggleLifestyle = $("#agentToggleLifestyle");
const agentToggleIot = $("#agentToggleIot");
const agentToggleScheduler = $("#agentToggleScheduler");

// Model selects
const modelSelectOrchestrator = $("#modelSelectOrchestrator");
const modelSelectBrowser = $("#modelSelectBrowser");
const modelSelectLifestyle = $("#modelSelectLifestyle");
const modelSelectIot = $("#modelSelectIot");
const modelSelectScheduler = $("#modelSelectScheduler");
const modelSelectMemory = $("#modelSelectMemory");

// Policy inputs (Hidden in UI now, but keeping references for data binding)
const shortTermTtlInput = $("#settingsShortTermTtl");
const shortTermGraceInput = $("#settingsShortTermGrace");
const shortTermActiveHoldInput = $("#settingsShortTermActiveHold");
const shortTermPromoteScoreInput = $("#settingsShortTermPromoteScore");
const shortTermPromoteImportanceInput = $("#settingsShortTermPromoteImportance");

// Memory View Containers (Simplified)
const longTermGrid = $("#settingsLongTermGrid");
const shortTermGrid = $("#settingsShortTermGrid");

const agentToggleInputs = {
  browser: agentToggleBrowser,
  lifestyle: agentToggleLifestyle,
  iot: agentToggleIot,
  scheduler: agentToggleScheduler,
};

const modelSelectInputs = {
  orchestrator: modelSelectOrchestrator,
  browser: modelSelectBrowser,
  lifestyle: modelSelectLifestyle,
  iot: modelSelectIot,
  scheduler: modelSelectScheduler,
  memory: modelSelectMemory,
};

const DEFAULT_AGENT_CONNECTIONS = {
  browser: true,
  lifestyle: true,
  iot: true,
  scheduler: true,
};

const DEFAULT_AGENT_STATUS = {
  browser: { available: null, enabled: true },
  lifestyle: { available: null, enabled: true },
  iot: { available: null, enabled: true },
  scheduler: { available: null, enabled: true },
};

// --- Configured Categories ---

const LONG_TERM_CATEGORIES = [
  'profile',
  'preference',
  'health',
  'work',
  'hobby',
  'relationship',
  'life',
  'travel',
  'food',
  'general',
];

const SHORT_TERM_CATEGORIES = [
  'active_task',
  'pending_questions',
  'recent_entities',
  'emotional_context',
  'general',
];

const CATEGORY_LABELS = {
  // Long Term
  profile: '基本情報',
  preference: '好み・嗜好',
  health: '健康',
  work: '仕事・学業',
  hobby: '趣味',
  relationship: '人間関係',
  life: '生活',
  travel: '旅行',
  food: '食事',
  general: 'その他・メモ',

  // Short Term
  active_task: '現在進行中のタスク',
  pending_questions: '未解決の質問',
  recent_entities: '直近の話題・キーワード',
  emotional_context: '現在の感情・雰囲気',
};

const PLACEHOLDER = {
  profile: '例: 名前は山田太郎。東京在住。30代。エンジニアとして働いている。',
  preference: '例: 返答は簡潔が好き。敬体が好み。長文より箇条書きが助かる。',
  health: '例: 毎日朝にジョギング。カフェイン控えめを希望。',
  work: '例: プロジェクトXの締切は毎週金曜。リモート勤務中心。',
  hobby: '例: ロードバイクと写真が趣味。休日は多摩川沿いを走る。',
  relationship: '例: 佐藤さんとは同僚。田中さんはメンター。',
  life: '例: 早朝型。家事は週末にまとめて行う。',
  travel: '例: 夏に北海道旅行を計画中。温泉が好き。',
  food: '例: 和食とコーヒーが好き。辛すぎる料理は苦手。',
  general: '例: 雑多なメモや、まだ分類できていない情報。',

  active_task: '例: タスク: 旅行の計画を立てる (ステータス: 進行中)',
  pending_questions: '例: 質問: 次回の会議はいつ？\n質問: あのレストランの名前は？',
  recent_entities: '例: キーワード: React, Python, 温泉',
  emotional_context: '例: 気分: 落ち着いている。少し急ぎ。',
};

const state = {
  loading: false,
  saving: false,
  modelOptions: [],
  agentStatus: { ...DEFAULT_AGENT_STATUS },
  memoryValues: {
    long: {},
    short: {},
  },
  memoryFull: {
    long: {},
    short: {},
  },
};

// --- Utilities ---

function formatShortTermValue(category, summaryText, fullMemory) {
  // Logic mirrored from assets/memory.js
  if (category === 'active_task') {
    const task = fullMemory.active_task || {};
    if (task.goal) {
      return `タスク: ${task.goal}\nステータス: ${task.status || 'active'}`;
    }
  }
  if (category === 'pending_questions') {
    const questions = fullMemory.pending_questions || [];
    if (Array.isArray(questions) && questions.length > 0) {
      return questions.map(q => `質問: ${q}`).join('\n');
    }
  }
  if (category === 'recent_entities') {
    const entities = fullMemory.recent_entities || [];
    if (Array.isArray(entities) && entities.length > 0) {
      const names = entities.map(e => e.name).filter(n => n);
      if (names.length > 0) {
        return `キーワード: ${names.join(', ')}`;
      }
    }
  }
  if (category === 'emotional_context') {
    if (fullMemory.emotional_context) {
      return `気分: ${fullMemory.emotional_context}`;
    }
  }

  return summaryText || "";
}

// --- Data Fetching & State ---

async function fetchMemory() {
  const response = await fetch("/api/memory", { method: "GET" });
  if (!response.ok) {
    throw new Error(`メモリの取得に失敗しました (${response.status})`);
  }
  const data = await response.json();
  return {
    longTermCategories: data?.long_term_categories ?? {},
    shortTermCategories: data?.short_term_categories ?? {},
    longTermFull: data?.long_term_full ?? {},
    shortTermFull: data?.short_term_full ?? {},
    enabled: data?.enabled ?? true,
    historySyncEnabled: data?.history_sync_enabled ?? true,
    shortTermTtlMinutes: data?.short_term_ttl_minutes ?? 45,
    shortTermGraceMinutes: data?.short_term_grace_minutes ?? 0,
    shortTermActiveHoldMinutes: data?.short_term_active_task_hold_minutes ?? 0,
    shortTermPromoteScore: data?.short_term_promote_score ?? 2,
    shortTermPromoteImportance: data?.short_term_promote_importance ?? 0.65,
  };
}

function setMemoryData(longCategories, shortCategories, longFull, shortFull) {
  state.memoryValues.long = { ...longCategories };
  state.memoryValues.short = { ...shortCategories };
  state.memoryFull.long = longFull || {};
  state.memoryFull.short = shortFull || {};
}

// --- Rendering: Simplified Text Areas ---

function renderMemoryGrid(type) {
  const grid = type === "long" ? longTermGrid : shortTermGrid;
  const categories = type === "long" ? LONG_TERM_CATEGORIES : SHORT_TERM_CATEGORIES;
  const summaries = state.memoryValues[type] || {};
  const fullMemory = state.memoryFull[type] || {};
  
  if (!grid) return;
  
  // Clear existing
  while (grid.firstChild) {
    grid.removeChild(grid.firstChild);
  }

  categories.forEach(cat => {
    const wrapper = document.createElement('div');
    wrapper.className = 'settings-memory-card';
    
    // Header
    const label = document.createElement('label');
    label.className = "settings-memory-label";
    label.textContent = CATEGORY_LABELS[cat] || cat;

    // Textarea
    const textarea = document.createElement('textarea');
    textarea.className = "form-control settings-memory-input";
    textarea.rows = 3;
    textarea.placeholder = PLACEHOLDER[cat] || '';
    textarea.dataset.category = cat;
    textarea.dataset.memoryType = type;
    
    // Determine initial value
    let initialValue = "";
    if (type === "short" && cat !== "general") {
       initialValue = formatShortTermValue(cat, summaries[cat], fullMemory);
    } else {
       initialValue = summaries[cat] || "";
    }
    textarea.value = initialValue;

    // Bind event
    const updateValue = (e) => {
      state.memoryValues[type][cat] = e.target.value;
    };
    // Use input so the latest text is captured even if the user saves without blurring.
    textarea.addEventListener('input', updateValue);
    textarea.addEventListener('change', updateValue);

    wrapper.appendChild(label);
    wrapper.appendChild(textarea);
    
    grid.appendChild(wrapper);
  });
}

function syncMemoryValuesFromInputs() {
  const inputs = document.querySelectorAll(".settings-memory-input[data-category]");
  inputs.forEach((input) => {
    const category = input.dataset.category;
    if (!category) return;
    const type = input.dataset.memoryType === "short" ? "short" : "long";
    if (!state.memoryValues[type]) state.memoryValues[type] = {};
    state.memoryValues[type][category] = input.value;
  });
}


// --- Main Load/Save ---

async function loadSettingsData() {
  if (state.loading) return;
  state.loading = true;
  setStatus("データを読み込み中…", "muted");
  refreshBtn?.setAttribute("aria-busy", "true");
  if (refreshBtn) refreshBtn.disabled = true;

  try {
    const memoryPromise = fetchMemory();
    const chatCountPromise = fetchChatCount();
    const agentPromise = fetchAgentConnections();
    const modelPromise = fetchModelSettings();
    const agentStatusPromise = fetchAgentStatus();

    const memoryResult = await memoryPromise
      .then((value) => ({ status: "fulfilled", value }))
      .catch((reason) => ({ status: "rejected", reason }));

    const errors = [];

    if (memoryResult.status === "fulfilled") {
        const m = memoryResult.value;
        setMemoryData(m.longTermCategories, m.shortTermCategories, m.longTermFull, m.shortTermFull);
        
        if (memoryToggle) {
            memoryToggle.checked = m.enabled;
            updateSwitchAria(memoryToggle);
        }
        if (historySyncToggle) {
            historySyncToggle.checked = m.historySyncEnabled;
            updateSwitchAria(historySyncToggle);
        }
        if (shortTermTtlInput) shortTermTtlInput.value = m.shortTermTtlMinutes ?? "";
        if (shortTermGraceInput) shortTermGraceInput.value = m.shortTermGraceMinutes ?? "";
        if (shortTermActiveHoldInput) shortTermActiveHoldInput.value = m.shortTermActiveHoldMinutes ?? "";
        if (shortTermPromoteScoreInput) shortTermPromoteScoreInput.value = m.shortTermPromoteScore ?? "";
        if (shortTermPromoteImportanceInput) shortTermPromoteImportanceInput.value = m.shortTermPromoteImportance ?? "";

        renderMemoryGrid("long");
        renderMemoryGrid("short");

    } else {
        errors.push(memoryResult.reason?.message || "メモリ取得エラー");
    }

    const [chatCountResult, agentResult, modelResult, agentStatusResult] = await Promise.allSettled([
      chatCountPromise,
      agentPromise,
      modelPromise,
      agentStatusPromise,
    ]);

    if (chatCountResult.status === "fulfilled") {
      updateChatCount(chatCountResult.value);
    } else {
      updateChatCount(undefined);
    }

    if (agentResult.status === "fulfilled") {
      setAgentConnections(agentResult.value);
    } else {
      setAgentConnections(DEFAULT_AGENT_CONNECTIONS);
    }

    if (agentStatusResult.status === "fulfilled") {
      applyAgentStatus(agentStatusResult.value);
    } else {
      applyAgentStatus({ agents: DEFAULT_AGENT_STATUS });
    }

    if (modelResult.status === "fulfilled") {
      renderModelOptions(modelResult.value.options);
      setModelSelection(modelResult.value.selection);
    } else {
      renderModelOptions({ providers: [] });
    }

    applyModelAvailability();

    if (errors.length) {
      setStatus(errors[0], "error");
    } else {
      setStatus("最新のデータを読み込みました。", "success");
    }

  } catch (error) {
    console.error("設定データの取得に失敗しました:", error);
    setStatus("設定データの取得に失敗しました。", "error");
  } finally {
    state.loading = false;
    refreshBtn?.removeAttribute("aria-busy");
    if (refreshBtn) refreshBtn.disabled = false;
  }
}

async function saveMemory() {
    // Collect updated text values
    // Note: state.memoryValues is updated on 'change' event of textareas.
    syncMemoryValuesFromInputs();
    // We strictly send the category map. The backend will parse it using `replace_with_user_payload` -> `_extract_manual_structure`.
    // This allows natural language updates to structured fields (handled by `_apply_manual_short_term_updates` in backend).
    
    const payload = {
        enabled: memoryToggle?.checked ?? true,
        history_sync_enabled: historySyncToggle?.checked ?? true,
        long_term_memory: state.memoryValues.long,
        short_term_memory: state.memoryValues.short
    };

    // Policy fields
    const ttl = readIntInput(shortTermTtlInput, { min: 5, max: 720 });
    if (typeof ttl === "number") payload.short_term_ttl_minutes = ttl;
    const grace = readIntInput(shortTermGraceInput, { min: 0, max: 240 });
    if (typeof grace === "number") payload.short_term_grace_minutes = grace;
    const hold = readIntInput(shortTermActiveHoldInput, { min: 0, max: 240 });
    if (typeof hold === "number") payload.short_term_active_task_hold_minutes = hold;
    const promoteScore = readIntInput(shortTermPromoteScoreInput, { min: 0, max: 10 });
    if (typeof promoteScore === "number") payload.short_term_promote_score = promoteScore;
    const promoteImportance = readFloatInput(shortTermPromoteImportanceInput, { min: 0, max: 1, precision: 2 });
    if (typeof promoteImportance === "number") payload.short_term_promote_importance = promoteImportance;

    const response = await fetchWithTimeout("/api/memory", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
    });
    if (!response.ok) {
        throw new Error(`保存に失敗しました (${response.status})`);
    }
    return response.json();
}


// --- Generic Helpers (Reused) ---

async function fetchChatCount() { 
  const response = await fetch("/chat_history", { method: "GET" });
  if (!response.ok) throw new Error("History fetch failed");
  const data = await response.json();
  if (Array.isArray(data)) return data.length;
  if (data && Array.isArray(data.history)) return data.history.length;
  return 0; 
}
async function fetchAgentConnections() {
    const response = await fetch("/api/agent_connections", { method: "GET" });
    if (!response.ok) throw new Error("Agent fetch failed");
    const data = await response.json();
    return (data?.agents && typeof data.agents === "object") ? data.agents : data;
}
async function fetchModelSettings() {
    const controller = new AbortController();
    const timeoutId = window.setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch("/api/model_settings", { method: "GET", signal: controller.signal });
      if (!response.ok) throw new Error("Model fetch failed");
      const data = await response.json();
      return { selection: data?.selection || {}, options: data?.options || {} };
    } finally {
      window.clearTimeout(timeoutId);
    }
}
async function fetchAgentStatus() {
    const controller = new AbortController();
    const timeoutId = window.setTimeout(() => controller.abort(), 5000);
    try {
      const response = await fetch("/api/agent_status", { method: "GET", signal: controller.signal });
      if (!response.ok) throw new Error("Agent status fetch failed");
      return response.json();
    } finally {
      window.clearTimeout(timeoutId);
    }
}
async function fetchWithTimeout(url, options = {}, timeoutMs = 15000) {
  const controller = new AbortController();
  const timeoutId = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } finally {
    window.clearTimeout(timeoutId);
  }
}
function setStatus(message, kind = "muted") {
  if (!statusMessage) return;
  statusMessage.textContent = message || "";
  statusMessage.dataset.kind = kind || "muted";
  statusMessage.hidden = !message;
}
function updateSwitchAria(input) {
  if (!input) return;
  input.setAttribute("aria-checked", input.checked ? "true" : "false");
}
function updateChatCount(count) {
  if (!chatCountValue || !chatCountNote) return;
  if (Number.isFinite(count)) {
    const safeCount = Math.max(0, Math.trunc(count));
    chatCountValue.textContent = safeCount.toLocaleString("ja-JP");
    chatCountNote.textContent = safeCount === 0 ? "履歴はまだありません。" : "保存済みのメッセージ総数です。";
  } else {
    chatCountValue.textContent = "-";
    chatCountNote.textContent = "履歴の取得に失敗しました。";
  }
}
function setAgentConnections(connections) {
  const merged = { ...DEFAULT_AGENT_CONNECTIONS };
  if (connections && typeof connections === "object") {
    Object.keys(merged).forEach((key) => {
      if (typeof connections[key] === "boolean") {
        merged[key] = connections[key];
      }
    });
  }
  Object.entries(agentToggleInputs).forEach(([agent, input]) => {
    if (!input) return;
    input.checked = merged[agent];
    updateSwitchAria(input);
  });
}
function readAgentConnections() {
  const connections = {};
  Object.entries(agentToggleInputs).forEach(([agent, input]) => {
    if (!input) return;
    connections[agent] = Boolean(input.checked);
  });
  return connections;
}
async function saveAgentConnections() {
  const connections = readAgentConnections();
  const response = await fetchWithTimeout("/api/agent_connections", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ agents: connections }),
  });
  if (!response.ok) throw new Error(`接続設定の保存に失敗しました (${response.status})`);
  return response.json();
}
async function saveModelSettings() {
  const payload = readModelSelection();
  const response = await fetchWithTimeout("/api/model_settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw new Error(`モデル設定の保存に失敗しました (${response.status})`);
  return response.json();
}
async function saveSettings() {
  const results = await Promise.allSettled([
    saveMemory(),
    saveAgentConnections(),
    saveModelSettings(),
  ]);
  const rejected = results.filter((result) => result.status === "rejected");
  const aborts = rejected.filter((result) => result.reason?.name === "AbortError");
  const nonAbortErrors = rejected.filter((result) => result.reason?.name !== "AbortError");

  if (nonAbortErrors.length) {
    const errors = nonAbortErrors.map(
      (result) => result.reason?.message || "保存に失敗しました。",
    );
    const error = new Error(errors[0]);
    error.messages = errors;
    throw error;
  }

  return { results, timedOut: aborts.length > 0 };
}
function applyAgentStatus(payload) {
  const agents = payload?.agents && typeof payload.agents === "object" ? payload.agents : payload;
  if (!agents || typeof agents !== "object") {
    state.agentStatus = { ...DEFAULT_AGENT_STATUS };
    return;
  }
  const nextStatus = { ...DEFAULT_AGENT_STATUS };
  Object.keys(nextStatus).forEach((key) => {
    const entry = agents[key];
    if (!entry || typeof entry !== "object") return;
    nextStatus[key] = {
      available: entry.available ?? nextStatus[key].available,
      enabled: entry.enabled ?? nextStatus[key].enabled,
    };
  });
  state.agentStatus = nextStatus;
}
function ensureSelectStatusPlaceholder(select, message) {
  if (!select) return;
  const existing = Array.from(select.options || []).find(option => option.dataset?.status === "unavailable");
  if (existing) {
    existing.textContent = message;
    select.value = existing.value;
    return;
  }
  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = message;
  placeholder.dataset.status = "unavailable";
  select.prepend(placeholder);
  select.value = "";
}
function clearSelectStatusPlaceholder(select) {
  if (!select) return;
  Array.from(select.options || []).forEach(option => {
    if (option.dataset?.status === "unavailable") {
      option.remove();
    }
  });
}
function applyModelAvailability() {
  Object.entries(modelSelectInputs).forEach(([agent, select]) => {
    if (!select) return;
    if (agent === "orchestrator" || agent === "memory") {
      return;
    }
    const status = state.agentStatus?.[agent];
    if (status && status.available === false) {
      ensureSelectStatusPlaceholder(select, "起動していません");
      select.disabled = true;
      return;
    }
    clearSelectStatusPlaceholder(select);
    if (state.modelOptions.length) {
      select.disabled = false;
    }
  });
}
function renderModelOptions(options) {
  state.modelOptions = Array.isArray(options?.providers) ? options.providers : [];
  Object.values(modelSelectInputs).forEach(select => {
    if (!select) return;
    select.innerHTML = "";
    if (!state.modelOptions.length) {
      const placeholder = document.createElement("option");
      placeholder.value = "";
      placeholder.textContent = "利用可能なモデルがありません";
      select.appendChild(placeholder);
      select.disabled = true;
      return;
    }
    select.disabled = false;
    state.modelOptions.forEach(provider => {
      const group = document.createElement("optgroup");
      group.label = provider.label || provider.id;
      (provider.models || []).forEach(model => {
        const option = document.createElement("option");
        option.value = `${provider.id}::${model.id}`;
        option.dataset.provider = provider.id;
        option.dataset.model = model.id;
        option.textContent = model.label || model.id;
        group.appendChild(option);
      });
      select.appendChild(group);
    });
  });
}
function setModelSelection(selection) {
  const safeSelection = selection && typeof selection === "object" ? selection : {};
  Object.entries(modelSelectInputs).forEach(([agent, select]) => {
    if (!select) return;
    const value = safeSelection[agent] || {};
    const provider = value.provider || "";
    const model = value.model || "";
    const match = Array.from(select.options || []).find(
      option => option.dataset?.provider === provider && option.dataset?.model === model,
    );
    if (match) {
      select.value = match.value;
    } else if (select.options.length) {
      select.selectedIndex = 0;
    }
  });
}
function readModelSelection() {
  const selection = {};
  Object.entries(modelSelectInputs).forEach(([agent, select]) => {
    if (!select) return;
    const option = select.selectedOptions && select.selectedOptions[0];
    if (!option) return;
    selection[agent] = {
      provider: option.dataset?.provider || "",
      model: option.dataset?.model || option.value,
    };
  });
  return { selection };
}
function readIntInput(input, { min, max } = {}) {
  if (!input) return null;
  const val = parseInt(input.value, 10);
  if (Number.isNaN(val)) return null;
  if (min !== undefined && val < min) return min;
  if (max !== undefined && val > max) return max;
  return val;
}
function readFloatInput(input, { min, max, precision } = {}) {
  if (!input) return null;
  let val = parseFloat(input.value);
  if (Number.isNaN(val)) return null;
  if (min !== undefined && val < min) val = min;
  if (max !== undefined && val > max) val = max;
  if (precision !== undefined) {
    const factor = Math.pow(10, precision);
    val = Math.round(val * factor) / factor;
  }
  return val;
}

function closeDialog() {
  if (!dialog) return;
  if (dialog.open) {
    dialog.close();
  }
  setStatus("", "muted");
}

// --- Initialization ---

export function initSettingsModal() {
  if (!settingsBtn || !dialog || !form) return;

  if (memoryToggle) {
    updateSwitchAria(memoryToggle);
    memoryToggle.addEventListener("change", () => updateSwitchAria(memoryToggle));
  }
  if (historySyncToggle) {
    updateSwitchAria(historySyncToggle);
    historySyncToggle.addEventListener("change", () => updateSwitchAria(historySyncToggle));
  }
  Object.values(agentToggleInputs).forEach(input => {
    if (!input) return;
    updateSwitchAria(input);
    input.addEventListener("change", () => updateSwitchAria(input));
  });

  settingsBtn.addEventListener("click", () => {
    if (!dialog.open) {
      dialog.showModal();
    }
    loadSettingsData();
  });
  closeBtn?.addEventListener("click", () => closeDialog());
  dialog.addEventListener("cancel", event => {
    event.preventDefault();
    closeDialog();
  });
  refreshBtn?.addEventListener("click", () => loadSettingsData());

  form.addEventListener("submit", async event => {
    event.preventDefault();
    if (state.saving) return;
    state.saving = true;
    let savedOk = false;
    if (saveBtn) {
      saveBtn.disabled = true;
      saveBtn.textContent = "保存中…";
    }
    setStatus("保存しています…", "muted");

    try {
      const saveResult = await saveSettings();
      savedOk = true;
      if (saveResult?.timedOut) {
        setStatus("保存しました。（応答が遅延しました）", "success");
      } else {
        setStatus("保存しました。", "success");
      }
      if (saveBtn) {
        saveBtn.textContent = "保存完了";
      }
    } catch (error) {
      console.error("設定の保存に失敗しました:", error);
      const message = error?.messages?.[0] || error?.message || "保存に失敗しました。";
      setStatus(message, "error");
    } finally {
      state.saving = false;
      if (saveBtn) {
        if (savedOk) {
          window.setTimeout(() => {
            if (state.saving) return;
            saveBtn.disabled = false;
            saveBtn.textContent = "保存";
          }, 1200);
        } else {
          saveBtn.disabled = false;
          saveBtn.textContent = "保存";
        }
      }
    }
  });

  dialog.addEventListener("click", event => {
    if (event.target === dialog) {
      closeDialog();
    }
  });
}
