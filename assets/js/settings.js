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
const agentToggleBrowser = $("#agentToggleBrowser");
const agentToggleFaq = $("#agentToggleFaq");
const agentToggleIot = $("#agentToggleIot");
const modelSelectOrchestrator = $("#modelSelectOrchestrator");
const modelSelectBrowser = $("#modelSelectBrowser");
const modelSelectFaq = $("#modelSelectFaq");
const modelSelectIot = $("#modelSelectIot");

const agentToggleInputs = {
  browser: agentToggleBrowser,
  faq: agentToggleFaq,
  iot: agentToggleIot,
};

const modelSelectInputs = {
  orchestrator: modelSelectOrchestrator,
  browser: modelSelectBrowser,
  faq: modelSelectFaq,
  iot: modelSelectIot,
};

const DEFAULT_AGENT_CONNECTIONS = {
  browser: true,
  faq: true,
  iot: true,
};

const state = {
  loading: false,
  saving: false,
  modelOptions: [],
};

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
    enabled: data?.enabled ?? true,
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
    faq: source?.faq ?? DEFAULT_AGENT_CONNECTIONS.faq,
    iot: source?.iot ?? DEFAULT_AGENT_CONNECTIONS.iot,
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
        longTermInput.value = memoryResult.value.longTerm;
      }
      if (shortTermInput) {
        shortTermInput.value = memoryResult.value.shortTerm;
      }
      if (memoryToggle) {
        memoryToggle.checked = memoryResult.value.enabled;
        updateSwitchAria(memoryToggle);
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
    long_term_memory: longTermInput?.value ?? "",
    short_term_memory: shortTermInput?.value ?? "",
    enabled: memoryToggle?.checked ?? true,
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
