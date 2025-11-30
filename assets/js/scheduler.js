import { $ } from "./dom-utils.js";

const schedulerInline = $("#schedulerInline");
const schedulerFallback = $("#schedulerCalendarFallback");
const schedulerRefreshBtn = $("#schedulerCalendarRefresh");
const schedulerMonthLabel = $("#schedulerMonthLabel");
const calendarSlot = schedulerInline?.querySelector("[data-calendar-slot]");
const inlinePlaceholder = schedulerInline?.querySelector(".scheduler-inline__placeholder");
const prevMonthBtn = schedulerInline?.querySelector("[data-action='prev-month']");
const nextMonthBtn = schedulerInline?.querySelector("[data-action='next-month']");

function sanitizeBase(value) {
  return typeof value === "string" ? value.trim().replace(/\/+$/, "") : "";
}

export function resolveSchedulerAgentBase() {
  let queryBase = "";
  try {
    queryBase = new URLSearchParams(window.location.search).get("scheduler_agent_base") || "";
  } catch (_) {
    queryBase = "";
  }

  const sources = [
    sanitizeBase(queryBase),
    sanitizeBase(window.SCHEDULER_AGENT_BASE),
    sanitizeBase(document.querySelector("meta[name='scheduler-agent-api-base']")?.content),
  ];

  for (const base of sources) {
    if (base) return base;
  }

  if (window.location.origin && window.location.origin !== "null") {
    return `${window.location.origin.replace(/\/+$/, "")}/scheduler_agent`;
  }

  return "http://localhost:5010";
}

const SCHEDULER_AGENT_BASE = resolveSchedulerAgentBase();

export function buildSchedulerAgentUrl(path = "") {
  if (!path) {
    return SCHEDULER_AGENT_BASE || "/scheduler_agent";
  }
  if (/^https?:/i.test(path)) {
    return path;
  }
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  const base = SCHEDULER_AGENT_BASE || "";
  if (!base) return normalizedPath;
  if (/^https?:/i.test(base)) {
    return `${base.replace(/\/+$/, "")}${normalizedPath}`;
  }
  return `${base.replace(/\/+$/, "")}${normalizedPath}` || normalizedPath;
}

export async function schedulerAgentRequest(path, { method = "GET", headers = {}, body, signal } = {}) {
  const url = buildSchedulerAgentUrl(path);
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
    mode: /^https?:/i.test(url) ? "cors" : "same-origin",
    credentials: /^https?:/i.test(url) ? "include" : "same-origin",
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
    error.data = data;
    throw error;
  }

  return { data: typeof data === "string" ? { message: data } : data, status: response.status };
}

function showFallback(message) {
  if (!schedulerFallback) return;
  schedulerFallback.textContent = message || schedulerFallback.textContent || "";
  schedulerFallback.hidden = false;
}

function hideFallback() {
  if (!schedulerFallback) return;
  schedulerFallback.hidden = true;
}

function getYearMonth() {
  const now = new Date();
  const year = parseInt(schedulerInline?.dataset.year ?? "", 10);
  const month = parseInt(schedulerInline?.dataset.month ?? "", 10);
  return {
    year: Number.isFinite(year) ? year : now.getFullYear(),
    month: Number.isFinite(month) ? month : now.getMonth() + 1,
  };
}

function setYearMonth(year, month) {
  if (!schedulerInline) return;
  schedulerInline.dataset.year = String(year);
  schedulerInline.dataset.month = String(month);
  if (schedulerMonthLabel) {
    schedulerMonthLabel.textContent = `${year}年 ${month}月`;
  }
}

function adjustMonth(year, month, delta) {
  const nextMonth = month + delta;
  if (nextMonth > 12) return { year: year + 1, month: 1 };
  if (nextMonth < 1) return { year: year - 1, month: 12 };
  return { year, month: nextMonth };
}

function setLoading(isLoading) {
  if (schedulerRefreshBtn) {
    schedulerRefreshBtn.disabled = isLoading;
    schedulerRefreshBtn.classList.toggle("is-loading", isLoading);
  }
  if (prevMonthBtn) prevMonthBtn.disabled = isLoading;
  if (nextMonthBtn) nextMonthBtn.disabled = isLoading;
}

async function fetchCalendarPartial(year, month) {
  const url = `/scheduler-ui/calendar_partial?year=${year}&month=${month}&t=${Date.now()}`;
  const res = await fetch(url, { headers: { "X-Requested-With": "fetch" } });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    const message = text || `HTTP ${res.status}`;
    throw new Error(message);
  }
  return res.text();
}

async function refreshInlineCalendar({ year, month, delta } = {}) {
  if (!schedulerInline || !calendarSlot) return;
  const current = getYearMonth();
  let targetYear = Number.isFinite(year) ? year : current.year;
  let targetMonth = Number.isFinite(month) ? month : current.month;

  if (typeof delta === "number") {
    const adjusted = adjustMonth(targetYear, targetMonth, delta);
    targetYear = adjusted.year;
    targetMonth = adjusted.month;
  }

  setLoading(true);
  hideFallback();

  try {
    const html = await fetchCalendarPartial(targetYear, targetMonth);
    const temp = document.createElement("div");
    temp.innerHTML = html;
    const newGrid = temp.querySelector("#calendar-grid");
    if (!newGrid) {
      throw new Error("カレンダーの描画に失敗しました。");
    }
    const currentGrid = calendarSlot.querySelector("#calendar-grid");
    if (currentGrid) {
      currentGrid.replaceWith(newGrid);
    } else {
      calendarSlot.appendChild(newGrid);
    }
    schedulerInline.dataset.hasData = "1";
    if (inlinePlaceholder) {
      inlinePlaceholder.hidden = true;
    }
    setYearMonth(targetYear, targetMonth);
  } catch (error) {
    showFallback(error.message || "カレンダーの更新に失敗しました。");
  } finally {
    setLoading(false);
  }
}

let inlineBound = false;
function bindInlineScheduler() {
  if (inlineBound || !schedulerInline) return;
  inlineBound = true;

  if (prevMonthBtn) {
    prevMonthBtn.addEventListener("click", () => {
      refreshInlineCalendar({ delta: -1 });
    });
  }
  if (nextMonthBtn) {
    nextMonthBtn.addEventListener("click", () => {
      refreshInlineCalendar({ delta: 1 });
    });
  }
  if (schedulerRefreshBtn) {
    schedulerRefreshBtn.addEventListener("click", () => {
      refreshInlineCalendar();
    });
  }

  if (schedulerInline.dataset.hasData !== "1") {
    refreshInlineCalendar();
  }
}

export function ensureSchedulerAgentInitialized({ reload = false } = {}) {
  bindInlineScheduler();
  if (reload) {
    refreshInlineCalendar();
  }
}
