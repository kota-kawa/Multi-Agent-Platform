import { $ } from "./dom-utils.js";

const schedulerInline = $("#schedulerInline");
const schedulerFallback = $("#schedulerCalendarFallback");
const schedulerRefreshBtn = $("#schedulerCalendarRefresh");
const schedulerMonthLabel = $("#schedulerMonthLabel");
const calendarSlot = schedulerInline?.querySelector("[data-calendar-slot]");
const inlinePlaceholder = schedulerInline?.querySelector(".scheduler-inline__placeholder");
const prevMonthBtn = schedulerInline?.querySelector("[data-action='prev-month']");
const nextMonthBtn = schedulerInline?.querySelector("[data-action='next-month']");

// Day view panel elements
const schedulerCalendarPanel = $("#schedulerCalendarPanel");
const schedulerDayPanel = $("#schedulerDayPanel");
const schedulerDayBackBtn = $("#schedulerDayBackBtn");
const schedulerDayContent = $("#schedulerDayContent");

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

// Day view functions
function formatWeekday(dateStr) {
  const date = new Date(dateStr);
  const weekdays = ["日曜日", "月曜日", "火曜日", "水曜日", "木曜日", "金曜日", "土曜日"];
  return weekdays[date.getDay()];
}

function formatDate(dateStr) {
  const date = new Date(dateStr);
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}.${month}.${day}`;
}

function renderDayViewContent(data) {
  const { date, timeline_items, completion_rate, day_log_content } = data;
  
  let timelineHtml = "";
  if (timeline_items && timeline_items.length > 0) {
    const timelineItemsHtml = timeline_items.map(item => {
      const isDone = item.is_done || item.log_done;
      const memo = item.log_memo || "";
      const categoryClass = item.step_category ? `badge-${item.step_category.toLowerCase()}` : "badge-other";
      
      return `
        <div class="scheduler-day-timeline-item ${isDone ? 'is-done' : ''}">
          <div class="scheduler-day-timeline-dot"></div>
          <div class="scheduler-day-timeline-time">${item.time}</div>
          <div class="scheduler-day-timeline-card">
            <div class="scheduler-day-timeline-header">
              <div>
                <span class="scheduler-day-badge ${categoryClass}">${item.step_category || 'Other'}</span>
                <h5 class="scheduler-day-task-name">${item.step_name}</h5>
                <small class="scheduler-day-routine-name">
                  <i class="bi bi-collection me-1"></i>${item.routine_name}
                </small>
              </div>
              <div class="scheduler-day-status">
                ${isDone 
                  ? '<i class="bi bi-check-circle-fill text-success"></i>' 
                  : '<i class="bi bi-circle text-muted"></i>'}
              </div>
            </div>
            ${memo ? `<div class="scheduler-day-memo"><i class="bi bi-chat-left-text me-1"></i>${memo}</div>` : ''}
          </div>
        </div>
      `;
    }).join("");

    timelineHtml = `
      <div class="scheduler-day-schedule-card">
        <div class="scheduler-day-schedule-header">
          <div>
            <h6 class="scheduler-day-weekday">${formatWeekday(date)}</h6>
            <h2 class="scheduler-day-date">${formatDate(date)}</h2>
          </div>
          <div class="scheduler-day-completion">
            <div class="scheduler-day-completion-rate">${completion_rate}%</div>
            <small>完了率</small>
          </div>
        </div>
        <hr class="scheduler-day-divider">
        <div class="scheduler-day-timeline">
          ${timelineItemsHtml}
        </div>
      </div>
    `;
  } else {
    timelineHtml = `
      <div class="scheduler-day-schedule-card">
        <div class="scheduler-day-schedule-header">
          <div>
            <h6 class="scheduler-day-weekday">${formatWeekday(date)}</h6>
            <h2 class="scheduler-day-date">${formatDate(date)}</h2>
          </div>
        </div>
        <hr class="scheduler-day-divider">
        <div class="scheduler-day-empty">
          <i class="bi bi-calendar-check"></i>
          <h4>タスクがありません</h4>
          <p>この日にはスケジュールされたタスクがありません。</p>
        </div>
      </div>
    `;
  }

  const logHtml = `
    <div class="scheduler-day-log-card">
      <div class="scheduler-day-log-header">
        <h5><i class="bi bi-journal-text me-2"></i>日報</h5>
        <small>今日の記録・感想</small>
      </div>
      <div class="scheduler-day-log-content">
        ${day_log_content 
          ? `<p class="scheduler-day-log-text">${day_log_content.replace(/\n/g, '<br>')}</p>` 
          : '<p class="scheduler-day-log-empty">日報は記録されていません。</p>'}
      </div>
    </div>
  `;

  return timelineHtml + logHtml;
}

async function fetchDayViewData(dateStr) {
  const url = buildSchedulerAgentUrl(`/api/day/${dateStr}`);
  const res = await fetch(url, { headers: { "X-Requested-With": "fetch" } });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    let errorMessage = `HTTP ${res.status}`;
    try {
      const json = JSON.parse(text);
      if (json.error) errorMessage = json.error;
    } catch (_) {
      if (text) errorMessage = text;
    }
    throw new Error(errorMessage);
  }
  return res.json();
}

function showDayView() {
  if (schedulerCalendarPanel) schedulerCalendarPanel.hidden = true;
  if (schedulerDayPanel) schedulerDayPanel.hidden = false;
}

function hideDayView() {
  if (schedulerDayPanel) schedulerDayPanel.hidden = true;
  if (schedulerCalendarPanel) schedulerCalendarPanel.hidden = false;
}

function showDayViewLoading() {
  if (schedulerDayContent) {
    schedulerDayContent.innerHTML = `
      <div class="scheduler-day-view__loading">
        <div class="spinner-border" role="status">
          <span class="visually-hidden">読み込み中...</span>
        </div>
        <p>データを読み込んでいます...</p>
      </div>
    `;
  }
}

function showDayViewError(message) {
  if (schedulerDayContent) {
    schedulerDayContent.innerHTML = `
      <div class="scheduler-day-view__error">
        <i class="bi bi-exclamation-triangle"></i>
        <h4>読み込みに失敗しました</h4>
        <p>${message || 'データの取得中にエラーが発生しました。'}</p>
        <button class="btn subtle" onclick="window.closeSchedulerDayView()">カレンダーに戻る</button>
      </div>
    `;
  }
}

async function openSchedulerDayView(dateStr) {
  showDayView();
  showDayViewLoading();

  try {
    const data = await fetchDayViewData(dateStr);
    if (schedulerDayContent) {
      schedulerDayContent.innerHTML = renderDayViewContent(data);
    }
  } catch (error) {
    console.error("Failed to load day view:", error);
    showDayViewError(error.message);
  }
}

function closeSchedulerDayView() {
  hideDayView();
  // Refresh calendar to reflect any changes
  refreshInlineCalendar();
}

// Expose functions globally for onclick handlers
window.openSchedulerDayView = openSchedulerDayView;
window.closeSchedulerDayView = closeSchedulerDayView;

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
  if (schedulerDayBackBtn) {
    schedulerDayBackBtn.addEventListener("click", closeSchedulerDayView);
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
