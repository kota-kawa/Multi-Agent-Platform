import { $ } from "./dom-utils.js";

const banner = $("#agentStatusBanner");

const AGENT_LABELS = {
  browser: "ブラウザエージェント",
  lifestyle: "Life-Style エージェント",
  iot: "IoT エージェント",
  scheduler: "Scheduler エージェント",
};

const state = {
  agents: {
    browser: { available: null, enabled: true, error: null },
    lifestyle: { available: null, enabled: true, error: null },
    iot: { available: null, enabled: true, error: null },
    scheduler: { available: null, enabled: true, error: null },
  },
  checkedAt: null,
};

function updateBanner() {
  const settingsBanner = $("#settingsAgentStatusBanner");
  const entries = Object.entries(state.agents);
  const disconnected = entries.filter(([, info]) => info.enabled !== false && info.available === false);
  
  if (!disconnected.length) {
    if (banner) {
      banner.hidden = true;
      banner.textContent = "";
    }
    if (settingsBanner) {
      settingsBanner.hidden = true;
      settingsBanner.textContent = "";
    }
    return;
  }

  const names = disconnected.map(([key]) => AGENT_LABELS[key] || key);
  const message = `未接続: ${names.join(" / ")}。接続できているエージェントの機能のみ利用できます。`;
  
  // Top page banner: stay hidden as per user request
  if (banner) {
    banner.textContent = message;
    banner.dataset.kind = "error";
    banner.hidden = true; // Force hidden
  }

  // Settings modal status note: show this one
  if (settingsBanner) {
    settingsBanner.textContent = message;
    settingsBanner.hidden = false;
  }
}

function applyStatusPayload(payload) {
  const agents = payload?.agents && typeof payload.agents === "object" ? payload.agents : {};
  Object.keys(state.agents).forEach((key) => {
    const entry = agents[key];
    if (!entry || typeof entry !== "object") return;
    state.agents[key] = {
      available: entry.available ?? state.agents[key].available,
      enabled: entry.enabled ?? state.agents[key].enabled,
      error: entry.error ?? state.agents[key].error,
    };
  });
  state.checkedAt = payload?.checked_at || state.checkedAt;
  updateBanner();
}

export async function refreshAgentStatus({ silent = false } = {}) {
  try {
    const res = await fetch("/api/agent_status", { method: "GET" });
    if (!res.ok) {
      if (!silent) console.warn("Failed to fetch agent status", res.status);
      return null;
    }
    const data = await res.json();
    applyStatusPayload(data);
    return data;
  } catch (error) {
    if (!silent) console.warn("Failed to fetch agent status", error);
    return null;
  }
}

export function markAgentUnavailable(agent, message) {
  if (!agent || !state.agents[agent]) return;
  state.agents[agent] = {
    ...state.agents[agent],
    available: false,
    error: message || state.agents[agent].error,
  };
  updateBanner();
}

export function markAgentAvailable(agent) {
  if (!agent || !state.agents[agent]) return;
  state.agents[agent] = {
    ...state.agents[agent],
    available: true,
    error: null,
  };
  updateBanner();
}

export function getAgentStatus(agent) {
  if (!agent) return null;
  return state.agents[agent] || null;
}

export function isAgentAvailable(agent) {
  const entry = getAgentStatus(agent);
  if (!entry) return null;
  return entry.available;
}

export function applyAgentStatusPayload(payload) {
  if (!payload) return;
  applyStatusPayload(payload);
}
