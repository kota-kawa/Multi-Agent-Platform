import { $ } from "./dom-utils.js";

const settingsBtn = $("#settingsBtn");
const dialog = $("#settingsDialog");
const form = $("#settingsForm");
const closeBtn = $("#settingsCloseBtn");
const refreshBtn = $("#settingsRefreshBtn");
const saveBtn = $("#settingsSaveBtn");
const longTermInput = $("#settingsLongTermMemory");
const shortTermInput = $("#settingsShortTermMemory");
const memoryToggle = $("#settingsMemoryToggle");
const chatCountValue = $("#chatCountValue");
const chatCountNote = $("#chatCountNote");
const statusMessage = $("#settingsStatusMessage");
const historySyncToggle = $("#settingsHistorySyncToggle");
const agentToggleBrowser = $("#agentToggleBrowser");
const agentToggleLifestyle = $("#agentToggleLifestyle");
const agentToggleIot = $("#agentToggleIot");
const agentToggleScheduler = $("#agentToggleScheduler");
const modelSelectOrchestrator = $("#modelSelectOrchestrator");
const modelSelectBrowser = $("#modelSelectBrowser");
const modelSelectLifestyle = $("#modelSelectLifestyle");
const modelSelectIot = $("#modelSelectIot");
const modelSelectScheduler = $("#modelSelectScheduler");
const modelSelectMemory = $("#modelSelectMemory");

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

const CATEGORY_LABELS = {
  general: "全体メモ",
  profile: "プロフィール",
  preference: "好み・こだわり",
  health: "健康",
  work: "仕事/学習",
  hobby: "趣味",
  relationship: "人間関係",
  life: "生活リズム",
  travel: "旅行/移動",
  food: "食事",
};

const CATEGORY_ALIASES = Object.entries(CATEGORY_LABELS).reduce((acc, [key, label]) => {
  acc[key] = [
    key,
    label,
    label.replace("・", ""),
    label.replace("／", "/"),
    label.replace(/[／・]/g, ""),
    key.replace("_", " "),
  ].map((name) => name.toLowerCase());
  return acc;
}, {});

const state = {
  loading: false,
  saving: false,
  modelOptions: [],
};

function tryParseJSON(str) {
  try {
    const o = JSON.parse(str);
    if (o && typeof o === "object") return o;
  } catch (e) {}
  return null;
}

function stripCodeFence(raw) {
  if (typeof raw !== "string") return raw;
  return raw.replace(/^```json\s*/i, "").replace(/```$/i, "").trim();
}

function resolveCategoryAlias(name) {
  if (!name) return null;
  const normalized = name.trim().toLowerCase();
  for (const [key, aliases] of Object.entries(CATEGORY_ALIASES)) {
    if (aliases.includes(normalized)) return key;
  }
  return null;
}

function unwrapCategoryValue(value) {
  if (value == null) return "";
  if (typeof value !== "string") return value;
  const stripped = stripCodeFence(value);
  const parsed = tryParseJSON(stripped);

  if (parsed && typeof parsed === "object") {
    if (parsed.category_summaries && typeof parsed.category_summaries === "object") {
      // Prefer the inner general summary when the value is a nested JSON dump
      if (parsed.category_summaries.general) {
        return unwrapCategoryValue(parsed.category_summaries.general);
      }
      return Object.fromEntries(
        Object.entries(parsed.category_summaries).map(([k, v]) => [k, unwrapCategoryValue(v)])
      );
    }
    return parsed;
  }

  return stripped.trim();
}

function extractCategoriesFromText(text, fallbackCategories) {
  if (!text && fallbackCategories) {
    return normalizeCategories(fallbackCategories);
  }

  const stripped = stripCodeFence(text || "");
  const parsed = tryParseJSON(stripped);

  if (parsed?.category_summaries && typeof parsed.category_summaries === "object") {
    return normalizeCategories(parsed.category_summaries);
  }

  // If it's plain text, treat it as a general summary
  return normalizeCategories(fallbackCategories, stripped);
}

function normalizeCategories(categories, generalFallback = "") {
  const normalized = {};
  const source = categories && typeof categories === "object" ? categories : {};

  Object.entries(source).forEach(([key, rawVal]) => {
    const unwrapped = unwrapCategoryValue(rawVal);
    if (typeof unwrapped === "string") {
      normalized[key] = unwrapped.trim();
    } else if (unwrapped && typeof unwrapped === "object") {
      normalized[key] = JSON.stringify(unwrapped, null, 2);
    }
  });

  if (!Object.keys(normalized).length && generalFallback) {
    normalized.general = generalFallback.trim();
  }

  return normalized;
}

function toBulletBlock(text) {
  const cleaned = (text || "").trim();
  if (!cleaned) return "・（内容なし）";

  const lines = cleaned.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
  if (lines.length > 1) {
    return lines.map((line) => `・${line.replace(/^[・•-]\s*/, "")}`).join("\n");
  }

  // If it's a single paragraph, try to break by Japanese/English sentence delimiters for readability
  const sentences = cleaned.split(/(?<=[。．！!？?])\s+/).filter(Boolean);
  if (sentences.length > 1) {
    return sentences.map((s) => `・${s}`).join("\n");
  }

  return `・${cleaned.replace(/^[・•-]\s*/, "")}`;
}

function renderReadableCategories(categories) {
  const orderedKeys = Object.keys(CATEGORY_LABELS);
  const result = [];

  orderedKeys.forEach((key) => {
    if (!categories[key]) return;
    result.push(
      `${CATEGORY_LABELS[key]}（${key}）\n${toBulletBlock(categories[key])}`
    );
  });

  // Render any additional / unknown categories at the end
  Object.entries(categories).forEach(([key, val]) => {
    if (orderedKeys.includes(key) || !val) return;
    result.push(`${key}\n${toBulletBlock(val)}`);
  });

  return result.join("\n\n").trim();
}

function formatMemoryData(text, categories) {
  const normalized = extractCategoriesFromText(text, categories);
  if (Object.keys(normalized).length) {
    return renderReadableCategories(normalized);
  }
  return (text || "").trim();
}

function parseMemoryText(text) {
  // 1) If the user pasted JSON, keep supporting it.
  const raw = stripCodeFence(text || "");
  const parsedJson = tryParseJSON(raw);
  if (parsedJson?.category_summaries) {
    return normalizeCategories(parsedJson.category_summaries);
  }

  const result = {};
  let currentKey = null;
  let buffer = [];

  const flush = () => {
    if (!buffer.length) return;
    const value = buffer
      .map((line) => line.replace(/^[・•-]\s*/, "").trim())
      .filter(Boolean)
      .join(" ");
    if (!value) return;
    const targetKey = currentKey || "general";
    result[targetKey] = value;
  };

  (raw.split(/\r?\n/) || []).forEach((line) => {
    const headingMatch =
      line.match(/^【(.*?)】\s*$/) ||
      line.match(/^(?:[-*•・#]+\s*)?(.+?)(?:（(.+?)）)?\s*[:：]\s*$/);

    if (headingMatch) {
      flush();
      const primary = headingMatch[1] || "";
      const secondary = headingMatch[2] || "";
      currentKey =
        resolveCategoryAlias(primary) ||
        resolveCategoryAlias(secondary) ||
        primary ||
        secondary ||
        "general";
      buffer = [];
    } else {
      buffer.push(line);
    }
  });

  flush();

  if (!Object.keys(result).length && raw.trim()) {
    result.general = raw.trim();
  }

  return result;
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
    chatCountNote.textContent = safeCount === 0
      ? "履歴はまだありません。"
      : "保存済みのメッセージ総数です。";
  } else {
    chatCountValue.textContent = "-";
    chatCountNote.textContent = "履歴の取得に失敗しました。";
  }
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

async function fetchMemory() {
  const response = await fetch("/api/memory", { method: "GET" });
  if (!response.ok) {
    throw new Error(`メモリの取得に失敗しました (${response.status})`);
  }
  const data = await response.json();
  return {
    longTerm: data?.long_term_memory ?? "",
    shortTerm: data?.short_term_memory ?? "",
    longTermCategories: data?.long_term_categories ?? {},
    shortTermCategories: data?.short_term_categories ?? {},
    enabled: data?.enabled ?? true,
    historySyncEnabled: data?.history_sync_enabled ?? true,
  };
}

async function fetchChatCount() {
  const response = await fetch("/chat_history", { method: "GET" });
  if (!response.ok) {
    throw new Error(`履歴の取得に失敗しました (${response.status})`);
  }
  const data = await response.json();
  if (Array.isArray(data)) {
    return data.length;
  }
  if (data && Array.isArray(data.history)) {
    return data.history.length;
  }
  return 0;
}

async function fetchAgentConnections() {
  const response = await fetch("/api/agent_connections", { method: "GET" });
  if (!response.ok) {
    throw new Error(`エージェント設定の取得に失敗しました (${response.status})`);
  }
  const data = await response.json();
  const source = data?.agents && typeof data.agents === "object" ? data.agents : data;
  return {
    browser: source?.browser ?? DEFAULT_AGENT_CONNECTIONS.browser,
    lifestyle: source?.lifestyle ?? DEFAULT_AGENT_CONNECTIONS.lifestyle,
    iot: source?.iot ?? DEFAULT_AGENT_CONNECTIONS.iot,
    scheduler: source?.scheduler ?? DEFAULT_AGENT_CONNECTIONS.scheduler,
  };
}

async function fetchModelSettings() {
  const response = await fetch("/api/model_settings", { method: "GET" });
  if (!response.ok) {
    throw new Error(`モデル設定の取得に失敗しました (${response.status})`);
  }
  const data = await response.json();
  return {
    selection: data?.selection || {},
    options: data?.options || {},
  };
}

async function loadSettingsData() {
  if (state.loading) return;
  state.loading = true;
  setStatus("データを読み込み中…", "muted");
  refreshBtn?.setAttribute("aria-busy", "true");
  if (refreshBtn) refreshBtn.disabled = true;
  try {
    const [memoryResult, chatCountResult, agentResult, modelResult] = await Promise.allSettled([
      fetchMemory(),
      fetchChatCount(),
      fetchAgentConnections(),
      fetchModelSettings(),
    ]);

    const errors = [];

    if (memoryResult.status === "fulfilled") {
      if (longTermInput) {
        longTermInput.value = formatMemoryData(
          memoryResult.value.longTerm,
          memoryResult.value.longTermCategories
        );
      }
      if (shortTermInput) {
        shortTermInput.value = formatMemoryData(
          memoryResult.value.shortTerm,
          memoryResult.value.shortTermCategories
        );
      }
      if (memoryToggle) {
        memoryToggle.checked = memoryResult.value.enabled;
        updateSwitchAria(memoryToggle);
      }
      if (historySyncToggle) {
        historySyncToggle.checked = memoryResult.value.historySyncEnabled;
        updateSwitchAria(historySyncToggle);
      }
    } else {
      errors.push(memoryResult.reason?.message || "メモリの取得に失敗しました。");
    }

    if (chatCountResult.status === "fulfilled") {
      updateChatCount(chatCountResult.value);
    } else {
      updateChatCount(undefined);
      errors.push(chatCountResult.reason?.message || "履歴の取得に失敗しました。");
    }

    if (agentResult.status === "fulfilled") {
      setAgentConnections(agentResult.value);
    } else {
      setAgentConnections(DEFAULT_AGENT_CONNECTIONS);
      errors.push(agentResult.reason?.message || "エージェント設定の取得に失敗しました。");
    }

    if (modelResult.status === "fulfilled") {
      renderModelOptions(modelResult.value.options);
      setModelSelection(modelResult.value.selection);
    } else {
      renderModelOptions({ providers: [] });
      errors.push(modelResult.reason?.message || "モデル設定の取得に失敗しました。");
    }

    if (errors.length) {
      setStatus(errors[0], "error");
    } else {
      setStatus("最新のデータを読み込みました。", "success");
    }
  } catch (error) {
    console.error("設定データの取得に失敗しました:", error);
    setStatus(error?.message || "設定データの取得に失敗しました。", "error");
    updateChatCount(undefined);
    setAgentConnections(DEFAULT_AGENT_CONNECTIONS);
  } finally {
    state.loading = false;
    refreshBtn?.removeAttribute("aria-busy");
    if (refreshBtn) refreshBtn.disabled = false;
  }
}

async function saveMemory() {
  const payload = {
    long_term_memory: parseMemoryText(longTermInput?.value ?? ""),
    short_term_memory: parseMemoryText(shortTermInput?.value ?? ""),
    enabled: memoryToggle?.checked ?? true,
    history_sync_enabled: historySyncToggle?.checked ?? true,
  };
  const response = await fetch("/api/memory", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw new Error(`保存に失敗しました (${response.status})`);
  }
  return response.json();
}

function setAgentConnections(connections) {
  const merged = { ...DEFAULT_AGENT_CONNECTIONS, ...(connections || {}) };
  Object.entries(agentToggleInputs).forEach(([key, input]) => {
    if (!input) return;
    input.checked = Boolean(merged[key]);
    updateSwitchAria(input);
  });
}

function readAgentConnections() {
  const current = { ...DEFAULT_AGENT_CONNECTIONS };
  Object.entries(agentToggleInputs).forEach(([key, input]) => {
    if (!input) return;
    current[key] = Boolean(input.checked);
    updateSwitchAria(input);
  });
  return current;
}

async function saveAgentConnections(connections) {
  const response = await fetch("/api/agent_connections", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(connections),
  });
  if (!response.ok) {
    throw new Error(`エージェント設定の保存に失敗しました (${response.status})`);
  }
  return response.json();
}

async function saveModelSettings(selection) {
  const response = await fetch("/api/model_settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(selection),
  });
  if (!response.ok) {
    throw new Error(`モデル設定の保存に失敗しました (${response.status})`);
  }
  return response.json();
}

async function saveSettings() {
  const results = await Promise.allSettled([
    saveMemory(),
    saveAgentConnections(readAgentConnections()),
    saveModelSettings(readModelSelection()),
  ]);

  const errors = results
    .filter(result => result.status === "rejected")
    .map(result => result.reason?.message || "保存に失敗しました。");

  if (errors.length) {
    const error = new Error(errors.join(" / "));
    error.messages = errors;
    throw error;
  }
}

function closeDialog() {
  dialog?.close();
}

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

  closeBtn?.addEventListener("click", () => {
    closeDialog();
  });

  dialog.addEventListener("cancel", event => {
    event.preventDefault();
    closeDialog();
  });

  refreshBtn?.addEventListener("click", () => {
    loadSettingsData();
  });

  form.addEventListener("submit", async event => {
    event.preventDefault();
    if (state.saving) return;
    state.saving = true;
    if (saveBtn) {
      saveBtn.disabled = true;
      saveBtn.textContent = "保存中…";
    }
    setStatus("保存しています…", "muted");

    try {
      await saveSettings();
      setStatus("保存しました。", "success");
    } catch (error) {
      console.error("設定の保存に失敗しました:", error);
      const message = error?.messages?.[0] || error?.message || "保存に失敗しました。";
      setStatus(message, "error");
    } finally {
      state.saving = false;
      if (saveBtn) {
        saveBtn.disabled = false;
        saveBtn.textContent = "保存";
      }
    }
  });

  dialog.addEventListener("click", event => {
    if (event.target === dialog) {
      closeDialog();
    }
  });
}
