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

// Policy inputs
const shortTermTtlInput = $("#settingsShortTermTtl");
const shortTermGraceInput = $("#settingsShortTermGrace");
const shortTermActiveHoldInput = $("#settingsShortTermActiveHold");
const shortTermPromoteScoreInput = $("#settingsShortTermPromoteScore");
const shortTermPromoteImportanceInput = $("#settingsShortTermPromoteImportance");

// Memory View Containers
const longTermGrid = $("#settingsLongTermGrid");
const shortTermGrid = $("#settingsShortTermGrid");
const longTermEmpty = $("#settingsLongTermEmpty");
const shortTermEmpty = $("#settingsShortTermEmpty");

// New Views
const longTermSlotsView = $("#settingsLongTermSlotsView");
const longTermSlotsBody = $("#settingsLongTermSlotsBody");
const longTermSlotsEmpty = $("#settingsLongTermSlotsEmpty");
const longTermProfileView = $("#settingsLongTermProfileView");
const longTermProfileFields = $("#settingsLongTermProfileFields");
const longTermJsonView = $("#settingsLongTermJsonView");
const longTermJsonTextarea = $("#settingsLongTermJson");

const shortTermSlotsView = $("#settingsShortTermSlotsView");
const shortTermSlotsBody = $("#settingsShortTermSlotsBody");
const shortTermSlotsEmpty = $("#settingsShortTermSlotsEmpty");
const shortTermContextView = $("#settingsShortTermContextView");
const shortTermJsonView = $("#settingsShortTermJsonView");
const shortTermJsonTextarea = $("#settingsShortTermJson");

// Profile specific inputs
const profileLikesInput = $("#settingsProfileLikes");
const profileDislikesInput = $("#settingsProfileDislikes");

// Context specific inputs
const contextActiveTaskInput = $("#settingsContextActiveTask");
const contextQuestionsInput = $("#settingsContextQuestions");
const contextEmotionInput = $("#settingsContextEmotion");

const longTermAddBtn = $("#settingsLongTermAdd");
const shortTermAddBtn = $("#settingsShortTermAdd");
const longTermAddSlotBtn = $("#settingsLongTermAddSlot");
const shortTermAddSlotBtn = $("#settingsShortTermAddSlot");

const longTermCardView = $("#settingsLongTermCardView");
const shortTermCardView = $("#settingsShortTermCardView");

const memoryTabs = $$("[data-memory-tab]");

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

const DEFAULT_CATEGORY_ORDER = Object.keys(CATEGORY_LABELS);

const CATEGORY_HINTS = {
  profile: "例: 氏名・年齢・居住地などの基本情報。",
  preference: "例: 敬体での返信が好み。辛すぎる料理は避けたい。",
  health: "例: 高血圧のため減塩。アレルギーや服薬メモ。",
  work: "例: 週次レポートが金曜締切。現在の担当プロジェクト。",
  hobby: "例: 写真と俳句が趣味。週末は散策。",
  relationship: "例: 家族構成やよく話す相手のメモ。",
  life: "例: 早起き。午前中に散歩。生活リズムのメモ。",
  travel: "例: 次の旅行計画・よく行く場所。",
  food: "例: 減塩希望。蕎麦アレルギー。好きな料理。",
  general: "例: 今日の気分や覚えておきたいトピック。",
};

const memoryViewState = {
  long: "cards",
  short: "cards",
};

const state = {
  loading: false,
  saving: false,
  modelOptions: [],
  memoryValues: {
    long: {},
    short: {},
  },
  memoryTitles: {
    long: {},
    short: {},
  },
  memoryFull: {
    long: {},
    short: {},
  },
};

// --- Utilities ---

function tryParseJSON(str) {
  try {
    const o = JSON.parse(str);
    if (o && typeof o === "object") return o;
  } catch (e) {}
  return null;
}

function prettifyCategoryKey(key) {
  if (!key) return "メモ";
  const cleaned = key.replace(/[_-]+/g, " ").trim();
  if (!cleaned) return "メモ";
  return cleaned.charAt(0).toUpperCase() + cleaned.slice(1);
}

function deriveCategoryTitle(category, titlesMap = {}) {
  const stored = titlesMap?.[category];
  if (stored && stored.trim()) return stored.trim();
  if (CATEGORY_LABELS[category]) return CATEGORY_LABELS[category];
  return prettifyCategoryKey(category);
}

function getMemoryElements(type) {
  if (type === "short") {
    return {
      grid: shortTermGrid,
      empty: shortTermEmpty,
      cardView: shortTermCardView,
      slotsView: shortTermSlotsView,
      slotsBody: shortTermSlotsBody,
      slotsEmpty: shortTermSlotsEmpty,
      contextView: shortTermContextView,
      jsonView: shortTermJsonView,
      jsonTextarea: shortTermJsonTextarea,
      addBtn: shortTermAddBtn,
      addSlotBtn: shortTermAddSlotBtn,
    };
  }
  return {
    grid: longTermGrid,
    empty: longTermEmpty,
    cardView: longTermCardView,
    slotsView: longTermSlotsView,
    slotsBody: longTermSlotsBody,
    slotsEmpty: longTermSlotsEmpty,
    profileView: longTermProfileView,
    jsonView: longTermJsonView,
    jsonTextarea: longTermJsonTextarea,
    addBtn: longTermAddBtn,
    addSlotBtn: longTermAddSlotBtn,
  };
}

// --- Data Fetching & State ---

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
    longTermTitles: data?.long_term_titles ?? {},
    shortTermTitles: data?.short_term_titles ?? {},
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

function setMemoryData(type, summaryText, categories, titles, fullData) {
  // Legacy Summary State
  state.memoryValues[type] = { ...categories };
  state.memoryTitles[type] = { ...titles };
  
  // Full Structure State
  state.memoryFull[type] = fullData || {};

  // If summaries are missing but we have full data, maybe we should auto-generate?
  // For now, trust what the API sends.
}

// --- Rendering: Cards (Summary) ---

function createMemoryCardElement(type, category, rawValue = "", displayTitle = "") {
  const friendlyTitle = displayTitle || deriveCategoryTitle(category, state.memoryTitles[type]);
  const card = document.createElement("div");
  card.className = "settings-memory-card";
  card.dataset.memoryType = type;
  card.dataset.category = category;

  const header = document.createElement("div");
  header.className = "settings-memory-card__header";

  const badge = document.createElement("span");
  badge.className = "settings-memory-card__badge";
  badge.textContent = CATEGORY_LABELS[category] ? "推奨カテゴリ" : "カスタムカテゴリ";

  const titleWrap = document.createElement("div");
  titleWrap.className = "settings-memory-card__title-wrap";
  
  const title = document.createElement("p");
  title.className = "settings-memory-card__title";
  title.textContent = friendlyTitle;
  titleWrap.appendChild(title);

  header.appendChild(badge);
  header.appendChild(titleWrap);

  const actions = document.createElement("div");
  actions.className = "settings-memory-card__actions";
  const removeBtn = document.createElement("button");
  removeBtn.type = "button";
  removeBtn.className = "settings-memory-card__remove";
  removeBtn.textContent = "削除";
  removeBtn.addEventListener("click", () => {
    delete state.memoryValues[type][category];
    if (state.memoryFull[type].category_summaries) {
      delete state.memoryFull[type].category_summaries[category];
    }
    rebuildMemoryCards(type);
  });
  actions.appendChild(removeBtn);
  header.appendChild(actions);

  const displayField = document.createElement("label");
  displayField.className = "settings-memory-card__display";
  const displayInput = document.createElement("input");
  displayInput.type = "text";
  displayInput.value = friendlyTitle;
  displayInput.addEventListener("input", () => {
    state.memoryTitles[type][category] = displayInput.value;
    title.textContent = displayInput.value || deriveCategoryTitle(category);
  });
  displayField.appendChild(document.createTextNode("表示名: "));
  displayField.appendChild(displayInput);

  const textarea = document.createElement("textarea");
  textarea.className = "settings-memory-card__control";
  textarea.placeholder = CATEGORY_HINTS[category] || "";
  textarea.value = rawValue;
  textarea.addEventListener("input", () => {
    state.memoryValues[type][category] = textarea.value;
    // Also update full structure if possible
    if (!state.memoryFull[type].category_summaries) state.memoryFull[type].category_summaries = {};
    state.memoryFull[type].category_summaries[category] = textarea.value;
  });

  card.appendChild(header);
  card.appendChild(displayField);
  card.appendChild(textarea);
  return card;
}

function rebuildMemoryCards(type) {
  const { grid, empty } = getMemoryElements(type);
  if (!grid) return;
  clearElement(grid);
  
  const values = state.memoryValues[type];
  const titles = state.memoryTitles[type];
  const keys = Object.keys(values);
  
  // Sort: Default categories first
  const sortedKeys = [...keys].sort((a, b) => {
    const idxA = DEFAULT_CATEGORY_ORDER.indexOf(a);
    const idxB = DEFAULT_CATEGORY_ORDER.indexOf(b);
    if (idxA !== -1 && idxB !== -1) return idxA - idxB;
    if (idxA !== -1) return -1;
    if (idxB !== -1) return 1;
    return a.localeCompare(b);
  });

  sortedKeys.forEach(key => {
    const card = createMemoryCardElement(type, key, values[key], titles[key]);
    grid.appendChild(card);
  });

  if (empty) empty.hidden = keys.length > 0;
}

function addMemoryCategory(type) {
  let idx = 1;
  let key = `custom_${idx}`;
  while (state.memoryValues[type][key]) {
    idx++;
    key = `custom_${idx}`;
  }
  state.memoryValues[type][key] = "";
  rebuildMemoryCards(type);
}


// --- Rendering: Slots (Facts) ---

function renderSlotsView(type) {
  const { slotsBody, slotsEmpty } = getMemoryElements(type);
  if (!slotsBody) return;
  
  clearElement(slotsBody);
  const slots = state.memoryFull[type]?.slots || [];
  
  if (slots.length === 0) {
    if (slotsEmpty) slotsEmpty.hidden = false;
    return;
  }
  if (slotsEmpty) slotsEmpty.hidden = true;

  slots.forEach((slot, index) => {
    const tr = document.createElement("tr");
    
    // Label Cell
    const tdLabel = document.createElement("td");
    const inputLabel = document.createElement("input");
    inputLabel.className = "form-control form-control--sm";
    inputLabel.value = slot.label || slot.id;
    inputLabel.addEventListener("change", (e) => {
      slot.label = e.target.value;
      // Also update ID if it was auto-generated? No, ID should be stable ideally, but for manual edits it's tricky.
      // Let's keep ID stable or update it if it matches old label.
    });
    tdLabel.appendChild(inputLabel);
    tr.appendChild(tdLabel);

    // Value Cell
    const tdValue = document.createElement("td");
    const inputValue = document.createElement("input");
    inputValue.className = "form-control form-control--sm";
    // Handle complex values by stringifying
    if (typeof slot.current_value === 'object') {
        inputValue.value = JSON.stringify(slot.current_value);
    } else {
        inputValue.value = slot.current_value || "";
    }
    inputValue.addEventListener("change", (e) => {
       slot.current_value = e.target.value;
       slot.last_updated = new Date().toISOString();
    });
    tdValue.appendChild(inputValue);
    tr.appendChild(tdValue);

    // Category Cell
    const tdCat = document.createElement("td");
    const selectCat = document.createElement("select");
    selectCat.className = "form-control form-control--sm";
    // Add default options + custom
    const cats = new Set([...DEFAULT_CATEGORY_ORDER, slot.category || "general"]);
    cats.forEach(c => {
        const opt = document.createElement("option");
        opt.value = c;
        opt.textContent = CATEGORY_LABELS[c] || c;
        if (c === slot.category) opt.selected = true;
        selectCat.appendChild(opt);
    });
    selectCat.addEventListener("change", (e) => {
        slot.category = e.target.value;
    });
    tdCat.appendChild(selectCat);
    tr.appendChild(tdCat);

    // Action Cell
    const tdAction = document.createElement("td");
    const btnDel = document.createElement("button");
    btnDel.className = "btn ghost sm text-danger";
    btnDel.innerHTML = "&times;";
    btnDel.title = "削除";
    btnDel.addEventListener("click", () => {
        slots.splice(index, 1);
        renderSlotsView(type); // Re-render
    });
    tdAction.appendChild(btnDel);
    tr.appendChild(tdAction);

    slotsBody.appendChild(tr);
  });
}

function addNewSlot(type) {
    if (!state.memoryFull[type].slots) state.memoryFull[type].slots = [];
    const id = `manual_${Date.now()}`;
    state.memoryFull[type].slots.unshift({
        id: id,
        label: "新しい事実",
        current_value: "",
        category: "general",
        confidence: 1.0,
        source: "manual_editor",
        last_updated: new Date().toISOString(),
        verified: true
    });
    renderSlotsView(type);
}


// --- Rendering: Profile (Long Term) ---

function renderProfileView() {
    const container = longTermProfileFields;
    if (!container) return;
    clearElement(container);

    const profile = state.memoryFull.long?.user_profile || {};
    
    // Standard fields
    const fields = [
        { key: "name", label: "名前" },
        { key: "age", label: "年齢" },
        { key: "location", label: "居住地" },
        { key: "occupation", label: "職業" },
        { key: "hobby", label: "趣味 (概要)" }
    ];

    fields.forEach(f => {
        const div = document.createElement("div");
        div.className = "form-row";
        const label = document.createElement("label");
        label.className = "form-label";
        label.textContent = f.label;
        const input = document.createElement("input");
        input.className = "form-control";
        input.value = profile[f.key] || "";
        input.addEventListener("change", (e) => {
            if (!state.memoryFull.long.user_profile) state.memoryFull.long.user_profile = {};
            state.memoryFull.long.user_profile[f.key] = e.target.value;
        });
        div.appendChild(label);
        div.appendChild(input);
        container.appendChild(div);
    });

    // Preferences
    const prefs = state.memoryFull.long?.preferences || {};
    if (profileLikesInput) {
        profileLikesInput.value = (prefs.likes || []).join("\n");
        profileLikesInput.onchange = () => {
             if (!state.memoryFull.long.preferences) state.memoryFull.long.preferences = {};
             state.memoryFull.long.preferences.likes = profileLikesInput.value.split("\n").filter(x => x.trim());
        };
    }
    if (profileDislikesInput) {
        profileDislikesInput.value = (prefs.dislikes || []).join("\n");
        profileDislikesInput.onchange = () => {
             if (!state.memoryFull.long.preferences) state.memoryFull.long.preferences = {};
             state.memoryFull.long.preferences.dislikes = profileDislikesInput.value.split("\n").filter(x => x.trim());
        };
    }
}


// --- Rendering: Context (Short Term) ---

function renderContextView() {
    const mem = state.memoryFull.short || {};
    
    if (contextActiveTaskInput) {
        contextActiveTaskInput.value = JSON.stringify(mem.active_task || {}, null, 2);
        contextActiveTaskInput.onchange = () => {
            try {
                mem.active_task = JSON.parse(contextActiveTaskInput.value);
            } catch(e) {
                // Ignore or warn
            }
        };
    }

    if (contextQuestionsInput) {
        contextQuestionsInput.value = (mem.pending_questions || []).join("\n");
        contextQuestionsInput.onchange = () => {
            mem.pending_questions = contextQuestionsInput.value.split("\n").filter(x => x.trim());
        };
    }

    if (contextEmotionInput) {
        contextEmotionInput.value = mem.emotional_context || "";
        contextEmotionInput.onchange = () => {
            mem.emotional_context = contextEmotionInput.value;
        };
    }
}


// --- Rendering: JSON ---

function renderJsonView(type) {
    const { jsonTextarea } = getMemoryElements(type);
    if (!jsonTextarea) return;
    const data = state.memoryFull[type] || {};
    jsonTextarea.value = JSON.stringify(data, null, 2);
}

function syncJsonFromTextarea(type) {
    const { jsonTextarea } = getMemoryElements(type);
    if (!jsonTextarea) return;
    try {
        const parsed = JSON.parse(jsonTextarea.value);
        state.memoryFull[type] = parsed;
        
        // Also sync back to summaries/values for Cards view
        if (parsed.category_summaries) {
             state.memoryValues[type] = parsed.category_summaries;
        }
        if (parsed.category_titles) {
             state.memoryTitles[type] = parsed.category_titles;
        }

    } catch (e) {
        console.warn("Invalid JSON in textarea", e);
    }
}


// --- View Switching ---

function switchMemoryView(type, nextView) {
    if (!type || !nextView) return;
    
    // 1. Sync FROM current view to State
    const currentView = memoryViewState[type];
    if (currentView === "json") {
        syncJsonFromTextarea(type);
    }
    // Note: Slots, Profile, Context update state 'onchange', so no explicit sync needed here usually, 
    // unless we want to force blur.

    // 2. Hide all views for this type
    const els = getMemoryElements(type);
    if (els.cardView) els.cardView.hidden = true;
    if (els.slotsView) els.slotsView.hidden = true;
    if (els.profileView) els.profileView.hidden = true;
    if (els.contextView) els.contextView.hidden = true;
    if (els.jsonView) els.jsonView.hidden = true;
    
    // Hide buttons initially
    if (els.addBtn) els.addBtn.hidden = true;
    if (els.addSlotBtn) els.addSlotBtn.hidden = true;

    // 3. Show target view & Render
    if (nextView === "cards") {
        if (els.cardView) els.cardView.hidden = false;
        if (els.addBtn) els.addBtn.hidden = false;
        rebuildMemoryCards(type);
    } else if (nextView === "slots") {
        if (els.slotsView) els.slotsView.hidden = false;
        if (els.addSlotBtn) els.addSlotBtn.hidden = false;
        renderSlotsView(type);
    } else if (nextView === "profile") {
        if (els.profileView) els.profileView.hidden = false;
        renderProfileView();
    } else if (nextView === "context") {
        if (els.contextView) els.contextView.hidden = false;
        renderContextView();
    } else if (nextView === "json") {
        if (els.jsonView) els.jsonView.hidden = false;
        renderJsonView(type);
    }

    memoryViewState[type] = nextView;
    setActiveMemoryTab(type, nextView);
}

function setActiveMemoryTab(type, view) {
  memoryTabs.forEach((tab) => {
    if (tab.dataset.memoryTab !== type) return;
    const isActive = tab.dataset.view === view;
    tab.classList.toggle("is-active", isActive);
    tab.setAttribute("aria-selected", isActive ? "true" : "false");
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
    const [memoryResult, chatCountResult, agentResult, modelResult] = await Promise.allSettled([
      fetchMemory(),
      fetchChatCount(),
      fetchAgentConnections(),
      fetchModelSettings(),
    ]);

    const errors = [];

    if (memoryResult.status === "fulfilled") {
        const m = memoryResult.value;
        setMemoryData("long", m.longTerm, m.longTermCategories, m.longTermTitles, m.longTermFull);
        setMemoryData("short", m.shortTerm, m.shortTermCategories, m.shortTermTitles, m.shortTermFull);
        
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

        // Refresh current views
        switchMemoryView("long", memoryViewState.long);
        switchMemoryView("short", memoryViewState.short);

    } else {
        errors.push(memoryResult.reason?.message || "メモリ取得エラー");
    }

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

    if (modelResult.status === "fulfilled") {
      renderModelOptions(modelResult.value.options);
      setModelSelection(modelResult.value.selection);
    } else {
      renderModelOptions({ providers: [] });
    }

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
    // Sync JSON views if active
    if (memoryViewState.long === "json") syncJsonFromTextarea("long");
    if (memoryViewState.short === "json") syncJsonFromTextarea("short");

    const payload = {
        enabled: memoryToggle?.checked ?? true,
        history_sync_enabled: historySyncToggle?.checked ?? true,
        long_term_full: state.memoryFull.long,
        short_term_full: state.memoryFull.short
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


// --- Generic Helpers ---

async function fetchChatCount() { /* ... reused ... */ 
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
    const response = await fetch("/api/model_settings", { method: "GET" });
    if (!response.ok) throw new Error("Model fetch failed");
    const data = await response.json();
    return { selection: data?.selection || {}, options: data?.options || {} };
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
function clearElement(el) {
  if (!el) return;
  while (el.firstChild) {
    el.removeChild(el.firstChild);
  }
}

// ... Reused Helper Functions from original file (RenderModelOptions, etc) ...
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

  memoryTabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      const type = tab.dataset.memoryTab;
      const view = tab.dataset.view;
      switchMemoryView(type, view);
    });
  });

  longTermAddBtn?.addEventListener("click", () => addMemoryCategory("long"));
  shortTermAddBtn?.addEventListener("click", () => addMemoryCategory("short"));
  longTermAddSlotBtn?.addEventListener("click", () => addNewSlot("long"));
  shortTermAddSlotBtn?.addEventListener("click", () => addNewSlot("short"));

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