/* Single Page UI logic
 * - View switching (ブラウザ / IoT / 要約チャット)
 * - Browser stage (iframe embed / pseudo noVNC)
 * - IoT dashboard (mock devices, live chart, localStorage persist)
 * - Chat + extractive summarizer (pure client-side)
 */

const $ = (q, c = document) => c.querySelector(q);
const $$ = (q, c = document) => Array.from(c.querySelectorAll(q));

function escapeHTML(value) {
  return String(value ?? "").replace(/[&<>"']/g, match => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[match]);
}

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
const generalDefaultContent = $("#generalDefaultContent");
const generalProxyStatus = $("#generalProxyStatus");
const generalProxyContainer = $("#generalProxyContainer");
const generalViewPanel = views.general?.querySelector(".general-view") ?? null;

let generalBrowserSurface = null;
let generalBrowserStage = null;
let generalBrowserFullscreenBtn = null;

let pendingFullscreenRequest = null;

function clearPendingFullscreenRequest() {
  if (!pendingFullscreenRequest) return;
  const { listeners } = pendingFullscreenRequest;
  if (Array.isArray(listeners)) {
    listeners.forEach(({ type, handler }) => {
      if (!type || typeof handler !== "function") return;
      document.removeEventListener(type, handler);
    });
  }
  pendingFullscreenRequest = null;
}

function scheduleFullscreenRetry(target) {
  if (!target || typeof target.requestFullscreen !== "function") {
    return;
  }

  clearPendingFullscreenRequest();

  const handler = () => {
    clearPendingFullscreenRequest();
    requestFullscreenWithFallback(target);
  };

  const listeners = [
    { type: "pointerdown", handler },
    { type: "keydown", handler },
  ];

  listeners.forEach(({ type, handler }) => {
    document.addEventListener(type, handler, { once: true });
  });

  pendingFullscreenRequest = { element: target, listeners };
}

function requestFullscreenWithFallback(target) {
  if (!target || typeof target.requestFullscreen !== "function") {
    return;
  }

  if (document.fullscreenElement === target) {
    clearPendingFullscreenRequest();
    return;
  }

  if (document.fullscreenEnabled === false) {
    return;
  }

  clearPendingFullscreenRequest();

  let requestResult;
  try {
    requestResult = target.requestFullscreen();
  } catch (_error) {
    scheduleFullscreenRetry(target);
    return;
  }

  if (requestResult && typeof requestResult.then === "function") {
    requestResult
      .then(() => {
        clearPendingFullscreenRequest();
      })
      .catch(() => {
        scheduleFullscreenRetry(target);
      });
  }
}

document.addEventListener("fullscreenchange", () => {
  if (document.fullscreenElement) {
    clearPendingFullscreenRequest();
  }
});

document.addEventListener("fullscreenerror", () => {
  clearPendingFullscreenRequest();
});

const viewPlacements = new Map();
const AGENT_TO_VIEW_MAP = { browser: "browser", iot: "iot", faq: "chat", chat: "chat" };
const GENERAL_PROXY_AGENT_LABELS = {
  faq: "FAQ Gemini",
  browser: "ブラウザエージェント",
  iot: "IoT エージェント",
  chat: "要約チャット",
};
const GENERAL_PROXY_VIEW_LABELS = {
  browser: "リモートブラウザ",
  chat: "要約チャット",
  iot: "IoT ダッシュボード",
};

let generalProxyTargetView = null;
let generalProxyAgentKey = null;
let generalProxyViewKey = null;
let currentViewKey = document.querySelector(".nav-btn.active")?.dataset.view || "browser";

function resolveAgentToView(agentKey) {
  if (typeof agentKey !== "string") return null;
  const normalized = agentKey.trim().toLowerCase();
  if (!normalized) return null;
  return AGENT_TO_VIEW_MAP[normalized] || null;
}

function ensureViewPlacement(viewEl) {
  if (!viewEl) return null;
  let placement = viewPlacements.get(viewEl);
  if (!placement) {
    placement = {
      parent: viewEl.parentElement,
      placeholder: document.createComment(`placeholder:${viewEl.id || ""}`),
    };
    viewPlacements.set(viewEl, placement);
  }
  return placement;
}

function restoreView(viewKey) {
  if (viewKey === "browser") {
    deactivateGeneralBrowserProxy();
    return;
  }

  const viewEl = views[viewKey];
  if (!viewEl) return;
  const placement = viewPlacements.get(viewEl);
  if (!placement) return;
  const { parent, placeholder } = placement;
  if (!parent) return;
  if (placeholder.parentNode) {
    placeholder.parentNode.replaceChild(viewEl, placeholder);
  } else {
    parent.appendChild(viewEl);
  }
  viewEl.classList.remove("general-proxy-active");
}

function moveViewToGeneral(viewKey) {
  if (viewKey === "browser") {
    if (generalProxyViewKey && generalProxyViewKey !== viewKey) {
      restoreView(generalProxyViewKey);
      generalProxyViewKey = null;
    }
    activateGeneralBrowserProxy();
    generalProxyViewKey = viewKey;
    return;
  }

  const viewEl = views[viewKey];
  if (!generalProxyContainer || !viewEl) return;
  if (generalProxyViewKey && generalProxyViewKey !== viewKey) {
    restoreView(generalProxyViewKey);
    generalProxyViewKey = null;
  }
  const placement = ensureViewPlacement(viewEl);
  if (!placement || !placement.parent) return;
  if (viewEl.parentElement !== generalProxyContainer) {
    placement.parent.replaceChild(placement.placeholder, viewEl);
    generalProxyContainer.appendChild(viewEl);
  }
  viewEl.classList.add("general-proxy-active");
  generalProxyViewKey = viewKey;
}

function clearGeneralProxy() {
  if (generalProxyViewKey) {
    restoreView(generalProxyViewKey);
    generalProxyViewKey = null;
  }
  if (generalProxyContainer) {
    generalProxyContainer.innerHTML = "";
  }
}

function updateGeneralViewProxy() {
  const shouldShowProxy = currentViewKey === "general" && Boolean(generalProxyTargetView);
  if (shouldShowProxy) {
    moveViewToGeneral(generalProxyTargetView);
  } else {
    clearGeneralProxy();
  }

  if (generalViewPanel) {
    generalViewPanel.classList.toggle("general-view--has-proxy", shouldShowProxy);
  }
  if (generalDefaultContent) {
    generalDefaultContent.hidden = shouldShowProxy;
  }
  if (generalProxyContainer) {
    generalProxyContainer.hidden = !shouldShowProxy;
  }
  if (generalProxyStatus) {
    if (shouldShowProxy && generalProxyAgentKey) {
      const agentLabel = GENERAL_PROXY_AGENT_LABELS[generalProxyAgentKey] || generalProxyAgentKey;
      const viewLabel = GENERAL_PROXY_VIEW_LABELS[generalProxyTargetView] || agentLabel;
      const labelText = agentLabel && viewLabel && agentLabel !== viewLabel
        ? `オーケストレーターは現在「${agentLabel}」（${viewLabel}）を使用しています。`
        : `オーケストレーターは現在「${agentLabel || viewLabel}」を使用しています。`;
      generalProxyStatus.textContent = `${labelText}下のビューで進行状況を確認できます。`;
      generalProxyStatus.hidden = false;
    } else {
      generalProxyStatus.hidden = true;
      generalProxyStatus.textContent = "";
    }
  }

  if (!shouldShowProxy) {
    return;
  }

  if (generalProxyTargetView === "browser") {
    ensureBrowserAgentInitialized({ showLoading: true });
    activateGeneralBrowserProxy();
    requestGeneralBrowserViewportSync({ reloadFallback: true });
    requestGeneralBrowserAutoFullscreen();
  } else if (generalProxyTargetView === "iot") {
    ensureIotDashboardInitialized({ showLoading: true });
    ensureIotChatInitialized({ forceSidebar: true });
  } else if (generalProxyTargetView === "chat") {
    ensureChatInitialized({ showLoadingSummary: true });
  }
}

function setGeneralProxyAgent(agentKey) {
  const normalizedAgent = typeof agentKey === "string" ? agentKey.trim().toLowerCase() : "";
  const targetView = resolveAgentToView(normalizedAgent);
  generalProxyAgentKey = targetView ? normalizedAgent : null;
  generalProxyTargetView = targetView;
  updateGeneralViewProxy();
}

function determineGeneralProxyAgentFromResult(result) {
  if (!result || typeof result !== "object") return null;
  const executions = Array.isArray(result.executions) ? result.executions : [];
  for (let index = executions.length - 1; index >= 0; index -= 1) {
    const agent = executions[index]?.agent;
    if (typeof agent === "string" && agent.trim()) {
      return agent.trim().toLowerCase();
    }
  }
  const tasks = Array.isArray(result.tasks) ? result.tasks : [];
  const nextTask = tasks.find(task => typeof task?.agent === "string" && task.agent.trim());
  return nextTask ? nextTask.agent.trim().toLowerCase() : null;
}

function activateView(viewKey) {
  const target = Object.prototype.hasOwnProperty.call(views, viewKey) ? viewKey : "browser";
  currentViewKey = target;
  navButtons.forEach(button => {
    button.classList.toggle("active", button.dataset.view === target);
  });
  Object.entries(views).forEach(([key, el]) => {
    if (!el) return;
    el.classList.toggle("active", key === target);
  });
  const titles = {
    general: "一般ビュー",
    browser: "リモートブラウザ",
    iot: "IoT ダッシュボード",
    chat: "要約チャット",
  };
  if (appTitle) {
    appTitle.textContent = titles[target] ?? "リモートブラウザ";
  }

  const isBrowserView = target === "browser";
  const isChatView = target === "chat";
  const isIotView = target === "iot";
  const isGeneralView = target === "general";

  if (isChatView) {
    ensureChatInitialized({ showLoadingSummary: true });
  } else if (!isBrowserView && !isIotView && !isGeneralView) {
    ensureChatInitialized();
  }
  if (isBrowserView) {
    ensureBrowserAgentInitialized({ showLoading: true });
    requestMainBrowserViewportSync({ reloadFallback: true });
  }
  if (isIotView) {
    ensureIotDashboardInitialized({ showLoading: true });
    ensureIotChatInitialized({ forceSidebar: true });
  }
  if (isGeneralView) {
    ensureOrchestratorInitialized({ forceSidebar: true });
  }

  const modeMap = {
    browser: "browser",
    iot: "iot",
    general: "orchestrator",
    chat: "general",
  };
  setChatMode(modeMap[target] ?? "general");
  updateGeneralViewProxy();
  if (target === "general") {
    requestGeneralBrowserAutoFullscreen();
  }
  scheduleSidebarTogglePosition();
}

navButtons.forEach(btn => {
  btn.addEventListener("click", () => {
    activateView(btn.dataset.view);
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
const browserStage = $("#browserStage");
const browserFullscreenBtn = $("#fullscreenBtn");

const noVncControllers = new Set();
let generalBrowserController = null;

const ALLOWED_RESIZE_VALUES = new Set(["scale", "remote", "off"]);
const DEFAULT_NOVNC_PARAMS = {
  autoconnect: "1",
  resize: "scale",
  scale: "auto",
};

function normalizeBrowserEmbedUrl(value) {
  if (!value) return value;

  try {
    const url = new URL(value, window.location.origin);
    const params = url.searchParams;

    const resizeValue = params.get("resize");
    if (!resizeValue || !ALLOWED_RESIZE_VALUES.has(resizeValue)) {
      params.set("resize", DEFAULT_NOVNC_PARAMS.resize);
    }

    if (!params.has("scale") || !params.get("scale")) {
      params.set("scale", DEFAULT_NOVNC_PARAMS.scale);
    }

    if (!params.has("autoconnect") || !params.get("autoconnect")) {
      params.set("autoconnect", DEFAULT_NOVNC_PARAMS.autoconnect);
    }

    return url.toString();
  } catch (_error) {
    return value;
  }
}

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
      return normalizeBrowserEmbedUrl(candidate);
    }
    try {
      const absolute = new URL(candidate, window.location.origin).toString();
      return normalizeBrowserEmbedUrl(absolute);
    } catch (_) {
      continue;
    }
  }

  return normalizeBrowserEmbedUrl("http://127.0.0.1:7900/?autoconnect=1&resize=scale&scale=auto");
}

const BROWSER_EMBED_URL = resolveBrowserEmbedUrl();

function reloadBrowserIframeWithCacheBust(iframe) {
  if (!iframe) return;
  const base = iframe.src || BROWSER_EMBED_URL;
  try {
    const url = new URL(base, window.location.origin);
    url.searchParams.set("_ts", Date.now().toString(36));
    iframe.src = url.toString();
  } catch (_error) {
    iframe.src = base;
  }
}

function createNoVncController({ stage, fullscreenButton, context = "default" } = {}) {
  if (!stage) return null;

  const state = {
    iframe: null,
    origin: "*",
    deferredRaf: null,
    deferredReloadFallback: false,
    stageResizeObserver: null,
    stageResizeRaf: null,
  };

  const controller = {
    context,
    ensureIframe,
    requestSync,
    sync,
    reload,
    matchesWindow,
    getStage: () => stage,
    getIframe: () => state.iframe,
  };

  function ensureIframe() {
    if (!stage || !BROWSER_EMBED_URL) {
      return null;
    }

    let iframe = stage.querySelector("iframe");
    const titleSuffix = context === "general-proxy" ? " (一般ビュー)" : "";
    if (!iframe) {
      stage.innerHTML = "";
      iframe = document.createElement("iframe");
      iframe.setAttribute("title", `埋め込みブラウザ${titleSuffix}`);
      iframe.setAttribute("allow", "fullscreen");
      iframe.addEventListener("load", () => {
        controller.requestSync();
      });
      stage.appendChild(iframe);
    }

    if (iframe.src !== BROWSER_EMBED_URL) {
      iframe.src = BROWSER_EMBED_URL;
    }

    try {
      const parsed = new URL(iframe.src, window.location.origin);
      state.origin = parsed.origin || "*";
    } catch (_error) {
      state.origin = "*";
    }

    state.iframe = iframe;
    controller.requestSync({ reloadFallback: true });
    return iframe;
  }

  function sync({ reloadFallback = false } = {}) {
    if (!state.iframe) {
      ensureIframe();
      if (!state.iframe) {
        return;
      }
    }

    const rect = stage?.getBoundingClientRect?.();
    const width = Math.round((rect && rect.width) || stage?.clientWidth || 0);
    const height = Math.round((rect && rect.height) || stage?.clientHeight || 0);
    if (width <= 0 || height <= 0) {
      if (reloadFallback) {
        controller.requestSync();
      }
      return;
    }

    const payload = {
      source: "multi-agent-platform",
      type: "novnc.viewport.sync",
      width,
      height,
      stageWidth: Math.round(stage?.clientWidth || width),
      stageHeight: Math.round(stage?.clientHeight || height),
      devicePixelRatio: Number(window.devicePixelRatio) || 1,
      innerWidth: typeof window.innerWidth === "number" ? window.innerWidth : width,
      innerHeight: typeof window.innerHeight === "number" ? window.innerHeight : height,
      timestamp: Date.now(),
      context,
    };

    let posted = false;
    try {
      state.iframe.contentWindow?.postMessage(payload, state.origin || "*");
      posted = true;
    } catch (_error) {
      posted = false;
    }

    if (reloadFallback && !posted) {
      controller.reload();
    }
  }

  function requestSync({ reloadFallback = false } = {}) {
    state.deferredReloadFallback = state.deferredReloadFallback || reloadFallback;
    if (state.deferredRaf !== null) {
      return;
    }

    state.deferredRaf = requestAnimationFrame(() => {
      state.deferredRaf = requestAnimationFrame(() => {
        const shouldReload = state.deferredReloadFallback;
        state.deferredReloadFallback = false;
        state.deferredRaf = null;
        controller.sync({ reloadFallback: shouldReload });
      });
    });
  }

  function reload() {
    const iframe = state.iframe;
    if (!iframe) {
      return;
    }

    const lastReload = Number(iframe.dataset?.novncReloadTs || "0");
    const now = Date.now();
    if (!lastReload || now - lastReload > 1500) {
      if (iframe.dataset) {
        iframe.dataset.novncReloadTs = String(now);
      }
      reloadBrowserIframeWithCacheBust(iframe);
    }
  }

  function matchesWindow(win) {
    return Boolean(state.iframe && win === state.iframe.contentWindow);
  }

  if (typeof ResizeObserver === "function") {
    state.stageResizeObserver = new ResizeObserver(entries => {
      if (!entries || entries.length === 0) return;
      const entry = entries[0];
      const { width, height } = entry.contentRect || {};
      const roundedWidth = Math.round(width || 0);
      const roundedHeight = Math.round(height || 0);
      if (roundedWidth <= 0 || roundedHeight <= 0) return;

      if (state.stageResizeRaf !== null) {
        cancelAnimationFrame(state.stageResizeRaf);
        state.stageResizeRaf = null;
      }

      state.stageResizeRaf = requestAnimationFrame(() => {
        state.stageResizeRaf = null;
        controller.requestSync();
      });
    });
    state.stageResizeObserver.observe(stage);
  }

  if (fullscreenButton) {
    fullscreenButton.addEventListener("click", () => {
      const el = state.iframe ?? stage;
      if (!el) return;
      if (document.fullscreenElement) document.exitFullscreen();
      else el.requestFullscreen?.();
    });
  }

  noVncControllers.add(controller);
  return controller;
}

const mainBrowserController = createNoVncController({
  stage: browserStage,
  fullscreenButton: browserFullscreenBtn,
  context: "browser-view",
});

if (mainBrowserController) {
  mainBrowserController.ensureIframe();
}

window.addEventListener("message", event => {
  const data = event?.data;
  if (!data || typeof data !== "object") {
    return;
  }

  const { type } = data;
  if (typeof type !== "string") {
    return;
  }

  const normalizedType = type.toLowerCase();
  if (
    normalizedType === "novnc.viewport.request" ||
    normalizedType === "novnc.viewport.requestsync" ||
    normalizedType === "novnc.viewport.ready" ||
    normalizedType === "novnc.ready"
  ) {
    let handled = false;
    for (const controller of noVncControllers) {
      if (!controller) continue;
      if (!event.source || controller.matchesWindow(event.source)) {
        controller.requestSync();
        handled = true;
      }
    }
    if (!handled) {
      for (const controller of noVncControllers) {
        controller?.requestSync();
      }
    }
    return;
  }

  if (
    normalizedType === "novnc.viewport.reload" || normalizedType === "novnc.reload"
  ) {
    for (const controller of noVncControllers) {
      if (!controller) continue;
      if (!event.source || controller.matchesWindow(event.source)) {
        controller.reload();
      }
    }
  }
});

function ensureGeneralBrowserProxyElements() {
  if (generalBrowserSurface && generalBrowserStage && generalBrowserFullscreenBtn) {
    return {
      surface: generalBrowserSurface,
      stage: generalBrowserStage,
      fullscreenBtn: generalBrowserFullscreenBtn,
    };
  }

  const surface = document.createElement("div");
  surface.className = "no-vnc-surface general-browser-surface";
  surface.hidden = true;

  const stage = document.createElement("div");
  stage.className = "stage";
  stage.id = "generalBrowserStage";
  stage.setAttribute("role", "region");
  stage.setAttribute("aria-label", "リモートブラウザ (一般ビュー)");

  const fallback = document.createElement("p");
  fallback.className = "stage-fallback";
  fallback.textContent = "リモートブラウザを読み込んでいます…";
  stage.appendChild(fallback);

  const toolbar = document.createElement("div");
  toolbar.className = "browser-toolbar";

  const fullscreenBtn = document.createElement("button");
  fullscreenBtn.type = "button";
  fullscreenBtn.id = "generalFullscreenBtn";
  fullscreenBtn.className = "btn subtle";
  fullscreenBtn.title = "フルスクリーン";
  fullscreenBtn.setAttribute("aria-label", "フルスクリーン");
  fullscreenBtn.textContent = "⤢";

  toolbar.appendChild(fullscreenBtn);

  surface.appendChild(stage);
  surface.appendChild(toolbar);

  generalBrowserSurface = surface;
  generalBrowserStage = stage;
  generalBrowserFullscreenBtn = fullscreenBtn;

  return { surface, stage, fullscreenBtn };
}

function ensureGeneralNoVncController() {
  if (!generalBrowserStage || !generalBrowserFullscreenBtn) {
    const elements = ensureGeneralBrowserProxyElements();
    if (!elements.stage || !elements.fullscreenBtn) {
      return null;
    }
  }

  if (!generalBrowserController) {
    generalBrowserController = createNoVncController({
      stage: generalBrowserStage,
      fullscreenButton: generalBrowserFullscreenBtn,
      context: "general-proxy",
    });
  }

  return generalBrowserController;
}

function activateGeneralBrowserProxy() {
  if (!generalProxyContainer) {
    return;
  }

  const { surface } = ensureGeneralBrowserProxyElements();
  if (!surface) {
    return;
  }

  if (surface.parentElement !== generalProxyContainer) {
    generalProxyContainer.innerHTML = "";
    generalProxyContainer.appendChild(surface);
  }

  surface.hidden = false;

  const controller = ensureGeneralNoVncController();
  controller?.ensureIframe();
}

function deactivateGeneralBrowserProxy() {
  if (!generalProxyContainer || !generalBrowserSurface) {
    return;
  }

  if (generalBrowserSurface.parentElement === generalProxyContainer) {
    generalProxyContainer.removeChild(generalBrowserSurface);
  }

  generalBrowserSurface.hidden = true;
}

function requestMainBrowserViewportSync({ reloadFallback = false } = {}) {
  if (!mainBrowserController) {
    return;
  }
  mainBrowserController.requestSync({ reloadFallback });
}

function requestGeneralBrowserViewportSync({ reloadFallback = false } = {}) {
  const controller = ensureGeneralNoVncController();
  if (!controller) {
    return;
  }
  controller.ensureIframe();
  if (generalBrowserSurface?.isConnected) {
    controller.requestSync({ reloadFallback });
  }
}

function requestGeneralBrowserAutoFullscreen() {
  if (currentViewKey !== "general") {
    return;
  }
  if (generalProxyTargetView !== "browser") {
    return;
  }
  if (document.fullscreenElement) {
    return;
  }

  const controller = ensureGeneralNoVncController();
  if (!controller) {
    return;
  }

  const iframe = typeof controller.getIframe === "function" ? controller.getIframe() : null;
  const stage = typeof controller.getStage === "function" ? controller.getStage() : null;
  const target = iframe || stage || generalBrowserSurface;
  if (!target) {
    return;
  }

  if (generalBrowserSurface?.hidden) {
    return;
  }

  requestFullscreenWithFallback(target);
}

/* ---------- IoT Dashboard ---------- */

const deviceGrid = $("#deviceGrid");
const iotNotice = $("#iotNotice");
const registerDeviceBtn = $("#registerDeviceBtn");
const refreshDevicesBtn = $("#refreshDevicesBtn");

const registerDialog = $("#iotRegisterDialog");
const registerForm = $("#iotRegisterForm");
const registerIdInput = $("#iotRegisterId");
const registerNameInput = $("#iotRegisterName");
const registerNoteInput = $("#iotRegisterNote");
const registerMessageEl = $("#iotRegisterMessage");
const registerCancelBtn = $("#iotRegisterCancel");
const registerSubmitBtn = $("#iotRegisterSubmit");

const IOT_DEVICE_ICON = `<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="5" width="18" height="14" rx="3" stroke="currentColor" stroke-width="1.6" fill="none" /><path d="M7 9h10M7 13h6" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" /></svg>`;

const IOT_FETCH_INTERVAL = 6000;

const PUBLIC_IOT_AGENT_BASE = "https://iot-agent.project-kk.com";

const REGISTER_MESSAGE_DEFAULT = registerMessageEl?.textContent.trim() || "エッジデバイスで使用する識別子を入力し、必要に応じて表示名やメモを設定してください。";

const iotState = {
  devices: [],
  fetching: false,
  initialized: false,
  pollTimer: null,
};

let lastRegisteredDevice = null;

function resolveIotAgentBase() {
  const sanitize = value => (typeof value === "string" ? value.trim().replace(/\/+$/, "") : "");
  let queryBase = "";
  try {
    queryBase = new URLSearchParams(window.location.search).get("iot_agent_base") || "";
  } catch (_) {
    queryBase = "";
  }
  const sources = [
    sanitize(queryBase),
    sanitize(window.IOT_AGENT_API_BASE),
    sanitize(document.querySelector("meta[name='iot-agent-api-base']")?.content),
  ];
  for (const base of sources) {
    if (base) return base;
  }
  if (window.location.origin && window.location.origin !== "null") {
    return `${window.location.origin.replace(/\/+$/, "")}/iot_agent`;
  }
  if (PUBLIC_IOT_AGENT_BASE) {
    return PUBLIC_IOT_AGENT_BASE;
  }
  return "/iot_agent";
}

const IOT_AGENT_API_BASE = resolveIotAgentBase();

function buildIotAgentUrl(path) {
  if (!path) {
    return IOT_AGENT_API_BASE || "/iot_agent";
  }
  if (/^https?:/i.test(path)) {
    return path;
  }
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  const base = IOT_AGENT_API_BASE || "";
  if (!base) {
    return normalizedPath;
  }
  if (/^https?:/i.test(base)) {
    return `${base.replace(/\/+$/, "")}${normalizedPath}`;
  }
  return `${base.replace(/\/+$/, "")}${normalizedPath}` || normalizedPath;
}

async function iotAgentRequest(path, { method = "GET", headers = {}, body, signal } = {}) {
  const url = buildIotAgentUrl(path);
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

function showIotNotice(message, kind = "info") {
  if (!iotNotice) return;
  iotNotice.hidden = false;
  iotNotice.textContent = message;
  iotNotice.dataset.kind = kind;
}

function hideIotNotice() {
  if (!iotNotice) return;
  iotNotice.hidden = true;
  iotNotice.textContent = "";
  delete iotNotice.dataset.kind;
}

function iotDisplayName(device) {
  if (!device) return "";
  const meta = device.meta || {};
  const candidates = [meta.display_name, meta.note, meta.label, meta.location];
  for (const candidate of candidates) {
    if (typeof candidate === "string" && candidate.trim()) {
      return candidate.trim();
    }
  }
  return device.device_id;
}

function formatIotTimestamp(ts) {
  if (!ts && ts !== 0) return "-";
  const date = new Date(ts * 1000);
  if (Number.isNaN(date.getTime())) {
    return String(ts);
  }
  return date.toLocaleString("ja-JP", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

function formatIotRelativeTime(ts) {
  if (!ts && ts !== 0) return "未記録";
  const date = new Date(ts * 1000);
  if (Number.isNaN(date.getTime())) {
    return String(ts);
  }
  const diff = Date.now() - date.getTime();
  if (diff < 0) return formatIotTimestamp(ts);
  const sec = Math.floor(diff / 1000);
  if (sec < 5) return "たった今";
  if (sec < 60) return `${sec}秒前`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}分前`;
  const hours = Math.floor(min / 60);
  if (hours < 24) return `${hours}時間前`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}日前`;
  return formatIotTimestamp(ts);
}

function formatIotMetaValue(value) {
  if (value === null) return "null";
  if (value === undefined) return "-";
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "number") return String(value);
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value);
  } catch (_) {
    return String(value);
  }
}

function createIotStat(label, value) {
  const wrapper = document.createElement("div");
  wrapper.className = "device-stat";
  const labelEl = document.createElement("div");
  labelEl.className = "device-stat__label";
  labelEl.textContent = label;
  const valueEl = document.createElement("div");
  valueEl.className = "device-stat__value";
  const textValue = value == null ? "-" : String(value);
  valueEl.textContent = textValue;
  valueEl.title = textValue;
  wrapper.appendChild(labelEl);
  wrapper.appendChild(valueEl);
  return wrapper;
}

function createCollapsibleText(text, { maxLength = 180 } = {}) {
  const str = text == null ? "" : String(text);
  const wrapper = document.createElement("div");
  wrapper.className = "collapsible-text";
  const content = document.createElement("div");
  content.className = "collapsible-text__content";
  content.textContent = str;
  content.title = str;
  wrapper.appendChild(content);

  if (str.length <= maxLength) {
    wrapper.dataset.state = "expanded";
    return wrapper;
  }

  const fullText = str;
  const truncated = fullText.slice(0, maxLength).trimEnd() + "…";
  let collapsed = true;

  const toggleBtn = document.createElement("button");
  toggleBtn.type = "button";
  toggleBtn.className = "collapsible-text__toggle";
  toggleBtn.textContent = "もっと見る";
  toggleBtn.setAttribute("aria-expanded", "false");

  const applyState = () => {
    if (collapsed) {
      content.textContent = truncated;
      wrapper.dataset.state = "collapsed";
      toggleBtn.textContent = "もっと見る";
      toggleBtn.setAttribute("aria-expanded", "false");
      toggleBtn.setAttribute("aria-label", "全文を表示");
    } else {
      content.textContent = fullText;
      wrapper.dataset.state = "expanded";
      toggleBtn.textContent = "閉じる";
      toggleBtn.setAttribute("aria-expanded", "true");
      toggleBtn.setAttribute("aria-label", "折りたたむ");
    }
  };

  toggleBtn.addEventListener("click", () => {
    collapsed = !collapsed;
    applyState();
  });

  wrapper.appendChild(toggleBtn);
  applyState();
  return wrapper;
}

function renderIotCapabilities(capabilities) {
  if (!Array.isArray(capabilities) || capabilities.length === 0) {
    return null;
  }
  const names = [];
  for (const cap of capabilities) {
    if (cap && typeof cap.name === "string" && cap.name.trim()) {
      names.push(cap.name.trim());
    }
  }
  if (!names.length) {
    return null;
  }
  const section = document.createElement("div");
  section.className = "device-section";
  const label = document.createElement("div");
  label.className = "device-section__label";
  label.textContent = "提供機能";
  section.appendChild(label);
  const list = document.createElement("div");
  list.className = "device-section__body";
  const maxChips = 8;
  names.slice(0, maxChips).forEach(name => {
    const chip = document.createElement("span");
    chip.className = "capability-badge";
    chip.textContent = name;
    list.appendChild(chip);
  });
  if (names.length > maxChips) {
    const rest = document.createElement("span");
    rest.className = "capability-badge";
    rest.textContent = `+${names.length - maxChips}`;
    rest.title = names.slice(maxChips).join(", ");
    list.appendChild(rest);
  }
  section.appendChild(list);
  return section;
}

function renderIotLastResult(result) {
  if (!result || typeof result !== "object") return null;
  const section = document.createElement("div");
  section.className = "device-section";
  const label = document.createElement("div");
  label.className = "device-section__label";
  label.textContent = "最後のジョブ";
  section.appendChild(label);

  const box = document.createElement("div");
  box.className = "device-last-result";

  const statusLine = document.createElement("div");
  statusLine.className = "device-last-result__meta";
  const statusText = result.ok ? "成功" : "失敗";
  const statusParts = [`ステータス: ${statusText}`];
  if (result.job_id) {
    statusParts.push(`ジョブID: ${result.job_id}`);
  }
  if (result.completed_at) {
    statusParts.push(`完了: ${formatIotTimestamp(result.completed_at)}`);
  }
  statusLine.textContent = statusParts.join(" / ");
  box.appendChild(statusLine);

  if (Object.prototype.hasOwnProperty.call(result, "return_value")) {
    const returnLine = document.createElement("div");
    returnLine.appendChild(createCollapsibleText(formatIotMetaValue(result.return_value)));
    box.appendChild(returnLine);
  }
  if (result.error || result.message) {
    const errorLine = document.createElement("div");
    errorLine.appendChild(createCollapsibleText(result.error || result.message));
    box.appendChild(errorLine);
  }
  if (result.output) {
    const outputLine = document.createElement("div");
    outputLine.appendChild(createCollapsibleText(formatIotMetaValue(result.output)));
    box.appendChild(outputLine);
  }
  section.appendChild(box);
  return section;
}

function renderIotDevices() {
  if (!deviceGrid) return;
  deviceGrid.innerHTML = "";

  if (!iotState.devices.length) {
    const empty = document.createElement("div");
    empty.className = "device-empty";
    empty.innerHTML = "<p>登録されたデバイスがありません。</p><p>右上の「デバイス登録」から登録してください。</p>";
    deviceGrid.appendChild(empty);
    return;
  }

  iotState.devices.forEach(device => {
    const card = document.createElement("article");
    card.className = "device-card";
    card.dataset.deviceId = device.device_id;

    const header = document.createElement("div");
    header.className = "device-card-header";

    const summary = document.createElement("div");
    summary.className = "device-summary";
    const icon = document.createElement("div");
    icon.className = "device-icon";
    icon.innerHTML = IOT_DEVICE_ICON;
    summary.appendChild(icon);

    const metaWrap = document.createElement("div");
    metaWrap.className = "device-meta";
    const nameEl = document.createElement("div");
    nameEl.className = "device-name";
    nameEl.textContent = iotDisplayName(device);
    const idEl = document.createElement("div");
    idEl.className = "device-id";
    idEl.textContent = device.device_id;
    metaWrap.appendChild(nameEl);
    metaWrap.appendChild(idEl);
    summary.appendChild(metaWrap);

    header.appendChild(summary);

    const actions = document.createElement("div");
    actions.className = "device-actions";

    const renameBtn = document.createElement("button");
    renameBtn.type = "button";
    renameBtn.className = "icon-btn";
    renameBtn.dataset.action = "rename";
    renameBtn.dataset.deviceId = device.device_id;
    renameBtn.title = "名称変更";
    renameBtn.setAttribute("aria-label", `${iotDisplayName(device)} の名前を変更`);
    renameBtn.textContent = "✎";
    actions.appendChild(renameBtn);

    const deleteBtn = document.createElement("button");
    deleteBtn.type = "button";
    deleteBtn.className = "icon-btn";
    deleteBtn.dataset.action = "delete";
    deleteBtn.dataset.deviceId = device.device_id;
    deleteBtn.title = "デバイスを削除";
    deleteBtn.setAttribute("aria-label", `${iotDisplayName(device)} を削除`);
    deleteBtn.textContent = "🗑";
    actions.appendChild(deleteBtn);

    header.appendChild(actions);
    card.appendChild(header);

    const body = document.createElement("div");
    body.className = "device-body";

    const stats = document.createElement("div");
    stats.className = "device-stats";
    stats.appendChild(createIotStat("最終アクセス", formatIotRelativeTime(device.last_seen)));
    stats.appendChild(createIotStat("登録日時", formatIotTimestamp(device.registered_at)));
    const queueDepth = Number.isFinite(Number(device.queue_depth)) ? `${Number(device.queue_depth)}件` : "-";
    stats.appendChild(createIotStat("待機ジョブ", queueDepth));
    body.appendChild(stats);

    const capabilities = renderIotCapabilities(device.capabilities);
    if (capabilities) {
      body.appendChild(capabilities);
    }
    const lastResult = renderIotLastResult(device.last_result);
    if (lastResult) {
      body.appendChild(lastResult);
    }

    card.appendChild(body);
    deviceGrid.appendChild(card);
  });
}

async function fetchIotDevices({ silent = false } = {}) {
  if (iotState.fetching) return;
  iotState.fetching = true;
  try {
    const { data } = await iotAgentRequest("/api/devices");
    if (Array.isArray(data.devices)) {
      iotState.devices = data.devices;
    } else {
      iotState.devices = [];
    }
    renderIotDevices();
    if (iotNotice?.dataset.kind === "error") {
      hideIotNotice();
    }
  } catch (error) {
    console.error("Failed to fetch devices", error);
    if (!silent) {
      showIotNotice(`デバイス一覧の取得に失敗しました: ${error.message}`, "error");
    }
  } finally {
    iotState.fetching = false;
  }
}

async function updateIotDeviceDisplayName(deviceId, displayName) {
  const payload = { display_name: displayName || null };
  const { data } = await iotAgentRequest(`/api/devices/${encodeURIComponent(deviceId)}/name`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
  return data?.device || null;
}

async function deleteIotDevice(deviceId) {
  await iotAgentRequest(`/api/devices/${encodeURIComponent(deviceId)}`, {
    method: "DELETE",
  });
}

function updateLocalDevice(updated) {
  if (!updated) return;
  const index = iotState.devices.findIndex(device => device.device_id === updated.device_id);
  if (index !== -1) {
    iotState.devices[index] = updated;
  }
}

function setRegisterMessage(message, kind = "info") {
  if (!registerMessageEl) return;
  registerMessageEl.textContent = message;
  registerMessageEl.className = "dialog-message";
  if (kind === "error") {
    registerMessageEl.classList.add("error");
  } else if (kind === "success") {
    registerMessageEl.classList.add("success");
  }
}

function resetRegisterDialog() {
  registerForm?.reset();
  if (registerSubmitBtn) {
    registerSubmitBtn.disabled = false;
    registerSubmitBtn.textContent = "登録";
  }
  setRegisterMessage(REGISTER_MESSAGE_DEFAULT);
}

async function handleRegisterSubmit(event) {
  event.preventDefault();
  if (!registerSubmitBtn) return;

  const deviceId = registerIdInput ? registerIdInput.value.trim() : "";
  const displayNameInput = registerNameInput ? registerNameInput.value.trim() : "";
  const note = registerNoteInput ? registerNoteInput.value.trim() : "";

  if (!deviceId) {
    setRegisterMessage("デバイスIDを入力してください。", "error");
    registerIdInput?.focus();
    return;
  }

  const payload = {
    device_id: deviceId,
    capabilities: [],
    meta: { registered_via: "dashboard" },
    approved: true,
  };

  if (displayNameInput) {
    payload.meta.display_name = displayNameInput;
  }
  if (note) {
    payload.meta.note = note;
  }

  registerSubmitBtn.disabled = true;
  registerSubmitBtn.textContent = "登録中…";
  setRegisterMessage("サーバーへ登録しています…");

  try {
    const { data } = await iotAgentRequest("/api/devices/register", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    const registeredId = typeof data?.device_id === "string" ? data.device_id : deviceId;
    const registeredDevice = data?.device && typeof data.device === "object" ? data.device : null;
    lastRegisteredDevice = {
      id: registeredId,
      name: registeredDevice ? iotDisplayName(registeredDevice) : displayNameInput || registeredId,
    };
    setRegisterMessage(`デバイス ${lastRegisteredDevice.name} を登録しました。`, "success");
    registerDialog?.close("success");
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    setRegisterMessage(`登録に失敗しました: ${message}`, "error");
  } finally {
    registerSubmitBtn.disabled = false;
    registerSubmitBtn.textContent = "登録";
  }
}

function startIotPolling() {
  if (iotState.pollTimer !== null) return;
  iotState.pollTimer = window.setInterval(() => {
    fetchIotDevices({ silent: true });
  }, IOT_FETCH_INTERVAL);
}

function ensureIotDashboardInitialized({ showLoading = false } = {}) {
  if (!iotState.initialized) {
    iotState.initialized = true;
    fetchIotDevices();
    startIotPolling();
    return;
  }
  if (showLoading) {
    fetchIotDevices();
  }
}

if (registerDeviceBtn && registerDialog) {
  registerDeviceBtn.addEventListener("click", () => {
    resetRegisterDialog();
    registerDialog.showModal();
    setTimeout(() => registerIdInput?.focus(), 50);
  });
}

if (registerCancelBtn && registerDialog) {
  registerCancelBtn.addEventListener("click", () => {
    registerDialog.close("cancel");
  });
}

if (registerForm) {
  registerForm.addEventListener("submit", handleRegisterSubmit);
}

if (registerDialog) {
  registerDialog.addEventListener("close", () => {
    if (registerDialog.returnValue === "success" && lastRegisteredDevice) {
      const label = lastRegisteredDevice.name || lastRegisteredDevice.id;
      const suffix = lastRegisteredDevice.name && lastRegisteredDevice.name !== lastRegisteredDevice.id
        ? ` (ID: ${lastRegisteredDevice.id})`
        : "";
      showIotNotice(`デバイス「${label}」${suffix}を登録しました。エッジデバイスをオンラインにするとジョブの取得を開始できます。`, "success");
      fetchIotDevices({ silent: false });
    }
    lastRegisteredDevice = null;
    resetRegisterDialog();
  });
}

if (refreshDevicesBtn) {
  refreshDevicesBtn.addEventListener("click", () => {
    fetchIotDevices();
  });
}

if (deviceGrid) {
  deviceGrid.addEventListener("click", async event => {
    const target = event.target instanceof Element ? event.target.closest("button[data-action]") : null;
    if (!target) return;
    const action = target.dataset.action;
    const deviceId = target.dataset.deviceId;
    if (!action || !deviceId) return;
    event.preventDefault();

    if (action === "rename") {
      const device = iotState.devices.find(d => d.device_id === deviceId);
      const currentName = device?.meta?.display_name && typeof device.meta.display_name === "string"
        ? device.meta.display_name
        : "";
      const promptLabel = currentName || iotDisplayName(device) || deviceId;
      const newName = window.prompt(`「${promptLabel}」の新しい名前を入力してください。`, currentName);
      if (newName === null) return;
      const trimmed = newName.trim();
      if (trimmed === (currentName || "").trim()) return;
      try {
        const updatedDevice = await updateIotDeviceDisplayName(deviceId, trimmed);
        if (updatedDevice) {
          updateLocalDevice(updatedDevice);
          renderIotDevices();
          showIotNotice(`デバイス名を「${iotDisplayName(updatedDevice)}」に更新しました。`, "success");
          fetchIotDevices({ silent: true });
        } else {
          throw new Error("更新後のデバイス情報が取得できませんでした。");
        }
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        showIotNotice(`名前の更新に失敗しました: ${message}`, "error");
      }
      return;
    }

    if (action === "delete") {
      const device = iotState.devices.find(d => d.device_id === deviceId);
      const label = iotDisplayName(device) || deviceId;
      const confirmed = window.confirm(`デバイス「${label}」を削除しますか？\nジョブキューや履歴も失われます。`);
      if (!confirmed) return;
      try {
        await deleteIotDevice(deviceId);
        iotState.devices = iotState.devices.filter(d => d.device_id !== deviceId);
        renderIotDevices();
        showIotNotice(`デバイス「${label}」を削除しました。`, "success");
        fetchIotDevices({ silent: true });
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        showIotNotice(`デバイスの削除に失敗しました: ${message}`, "error");
      }
    }
  });
}

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

const SUMMARY_PLACEHOLDER = "左側のチャットでメッセージを送信すると、ここに要約が表示されます。";
const SUMMARY_LOADING_TEXT = "要約を取得しています…";
const INTRO_MESSAGE_TEXT = "ここは要約チャットです。左サイドバーの共通チャットからメッセージを送信すると重要なポイントをここに表示します。";
const ORCHESTRATOR_INTRO_TEXT = "一般ビューではマルチエージェント・オーケストレーターがタスクを計画し、適切なエージェントに指示を送ります。共通チャットからリクエストを入力してください。";

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

const ORCHESTRATOR_AGENT_LABELS = {
  faq: "FAQ Gemini",
  browser: "ブラウザエージェント",
  iot: "IoT エージェント",
};

const IOT_CHAT_GREETING = "こんにちは！登録済みデバイスの状況を確認したり、チャットから指示を送れます。";

const iotChatState = {
  messages: [],
  history: [],
  initialized: false,
  sending: false,
  paused: false,
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

async function* orchestratorRequest(message, { signal } = {}) {
  const response = await fetch("/orchestrator/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message }),
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

function createMessageElement(message, { compact = false } = {}) {
  const el = document.createElement("div");
  const roleClass = message.role === "user" ? "user" : "system";
  el.className = `msg ${roleClass}`;
  if (compact) el.classList.add("compact");
  if (message.role === "assistant") el.classList.add("assistant");
  if (message.pending) el.classList.add("pending");

  const time = message.ts ? new Date(message.ts).toLocaleString("ja-JP") : "";
  const text = message.text ?? "";
  const escapedText = escapeHTML(text);

  if (message.role === "assistant" && message.pending) {
    el.classList.add("thinking");
    el.innerHTML = `
      <div class="thinking-header">
        <span class="thinking-orb" aria-hidden="true"></span>
        <span class="thinking-labels">
          <span class="thinking-title">AI が考えています</span>
          <span class="thinking-sub">見つけた情報から回答を組み立て中…</span>
        </span>
      </div>
      <div class="thinking-body">
        <p class="thinking-text">${escapedText || "回答を生成しています…"}</p>
        <div class="thinking-steps" aria-hidden="true">
          <span></span>
          <span></span>
          <span></span>
        </div>
      </div>
      ${time ? `<span class="msg-time">${time}</span>` : ""}
    `;
    return el;
  }

  el.innerHTML = `
      ${escapedText}
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

function getOrchestratorIntroMessage() {
  return {
    role: "assistant",
    text: ORCHESTRATOR_INTRO_TEXT,
    ts: Date.now(),
  };
}

function renderOrchestratorChat({ forceSidebar = false } = {}) {
  if (forceSidebar || currentChatMode === "orchestrator") {
    renderSidebarMessages(orchestratorState.messages);
  }
}

function ensureOrchestratorInitialized({ forceSidebar = false } = {}) {
  if (!orchestratorState.initialized) {
    orchestratorState.initialized = true;
    orchestratorState.messages = [getOrchestratorIntroMessage()];
  }
  renderOrchestratorChat({ forceSidebar });
}

function addOrchestratorUserMessage(text) {
  const message = { role: "user", text, ts: Date.now() };
  orchestratorState.messages.push(message);
  renderOrchestratorChat({ forceSidebar: currentChatMode === "orchestrator" });
  return message;
}

function addOrchestratorAssistantMessage(text, { pending = false } = {}) {
  const message = {
    role: "assistant",
    text: text ?? "",
    pending,
    ts: Date.now(),
  };
  orchestratorState.messages.push(message);
  renderOrchestratorChat({ forceSidebar: currentChatMode === "orchestrator" });
  return message;
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

function summarizeIotDevices() {
  if (!iotState.devices.length) {
    return "登録済みのデバイスはありません。";
  }
  const summaries = iotState.devices.map(device => {
    const caps = Array.isArray(device.capabilities)
      ? device.capabilities.map(cap => cap?.name).filter(Boolean)
      : [];
    const capText = caps.length ? `（機能: ${caps.join(", ")})` : "";
    return `${iotDisplayName(device)}${capText}`;
  });
  return summaries.join(" / ");
}

function ensureIotChatInitialized({ forceSidebar = false } = {}) {
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

function updatePauseButtonState(mode = currentChatMode) {
  if (!sidebarPauseBtn) return;
  const showBrowserControls = mode === "browser";
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
      sidebarChatSend.disabled = browserChatState.sending;
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

  if (mode === "orchestrator") {
    if (sidebarResetBtn) {
      sidebarResetBtn.disabled = true;
    }
    if (sidebarChatSend) {
      sidebarChatSend.disabled = orchestratorState.sending;
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

  if (sidebarResetBtn) {
    sidebarResetBtn.disabled = true;
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
  } finally {
    browserChatState.sending = false;
    updateSidebarControlsForMode(currentChatMode);
  }
}

async function sendIotChatMessage(text) {
  if (!text || iotChatState.sending || iotChatState.paused) return;
  ensureIotChatInitialized({ forceSidebar: currentChatMode === "iot" });
  iotChatState.sending = true;
  updateSidebarControlsForMode(currentChatMode);

  const userMessage = pushIotMessage("user", text);
  renderIotChat({ forceSidebar: currentChatMode === "iot" });

  const pending = pushIotMessage("assistant", "応答を待っています…", { pending: true, addToHistory: false });
  renderIotChat({ forceSidebar: currentChatMode === "iot" });

  try {
    const payload = {
      messages: iotChatState.history.map(entry => ({ role: entry.role, content: entry.content })),
    };
    const { data } = await iotAgentRequest("/api/chat", {
      method: "POST",
      body: JSON.stringify(payload),
    });
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

function setChatMode(mode) {
  if (!{"browser": true, "general": true, "iot": true, "orchestrator": true}[mode]) {
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

async function sendOrchestratorMessage(text) {
  if (!text || orchestratorState.sending) return;
  ensureOrchestratorInitialized({ forceSidebar: currentChatMode === "orchestrator" });
  orchestratorState.sending = true;
  updateSidebarControlsForMode(currentChatMode);

  const userMessage = addOrchestratorUserMessage(text);
  const planMessage = addOrchestratorAssistantMessage("タスクを計画しています…", { pending: true });
  setGeneralProxyAgent(null);

  const taskMessages = new Map();

  try {
    for await (const { event: eventType, data: payload } of orchestratorRequest(text)) {
      const eventData = payload && typeof payload === "object" ? payload : {};

      if (eventType === "plan") {
        const state = eventData.state && typeof eventData.state === "object" ? eventData.state : {};
        const planSummary = typeof state.plan_summary === "string" ? state.plan_summary.trim() : "";
        const tasks = Array.isArray(state.tasks) ? state.tasks : [];
        const textValue = planSummary
          ? `計画: ${planSummary}`
          : tasks.length === 0
            ? "今回のリクエストでは実行すべきタスクはありませんでした。"
            : "計画を作成しました。タスクを実行します…";
        planMessage.text = textValue;
        planMessage.pending = false;
        planMessage.ts = Date.now();
        renderOrchestratorChat({ forceSidebar: currentChatMode === "orchestrator" });
        continue;
      }

      if (eventType === "before_execution") {
        const task = eventData.task && typeof eventData.task === "object" ? eventData.task : {};
        const taskIndex = typeof eventData.task_index === "number" ? eventData.task_index : null;
        const agentRaw = typeof task.agent === "string" ? task.agent.trim().toLowerCase() : "";
        const commandText = typeof task.command === "string" ? task.command.trim() : "";
        const agentLabel = agentRaw ? (ORCHESTRATOR_AGENT_LABELS[agentRaw] || agentRaw) : "エージェント";
        if (agentRaw) {
          setGeneralProxyAgent(agentRaw);
          if (agentRaw === "browser") {
            ensureBrowserAgentInitialized({ showLoading: true });
            if (commandText) {
              await sendBrowserAgentPrompt(commandText);
            }
          }
        }
        const displayText = commandText
          ? `[${agentLabel}] ${commandText}`
          : `[${agentLabel}] タスクを実行しています…`;
        const message = addOrchestratorAssistantMessage(displayText, { pending: true });
        message.ts = Date.now();
        if (taskIndex !== null) {
          taskMessages.set(taskIndex, message);
        }
        continue;
      }

      if (eventType === "browser_init") {
        // 初期化イベントはクライアント側でハンドオフ済みなので何もしない
        continue;
      }

      if (eventType === "after_execution") {
        const task = eventData.task && typeof eventData.task === "object" ? eventData.task : {};
        const taskIndex = typeof eventData.task_index === "number" ? eventData.task_index : null;
        const result = eventData.result && typeof eventData.result === "object" ? eventData.result : {};
        const agentRaw = typeof task.agent === "string" ? task.agent.trim().toLowerCase() : "";
        const agentLabel = agentRaw ? (ORCHESTRATOR_AGENT_LABELS[agentRaw] || agentRaw) : "エージェント";
        const status = typeof result.status === "string" ? result.status : "";
        const responseText = typeof result.response === "string" ? result.response.trim() : "";
        const errorText = typeof result.error === "string" ? result.error.trim() : "";
        const finalText = status === "error"
          ? `[${agentLabel}] ${errorText || "タスクの実行に失敗しました。"}`
          : `[${agentLabel}] ${responseText || "タスクを完了しました。"}`;
        const existing = taskIndex !== null ? taskMessages.get(taskIndex) : null;
        const targetMessage = existing || addOrchestratorAssistantMessage(finalText);
        targetMessage.text = finalText;
        targetMessage.pending = false;
        targetMessage.ts = Date.now();
        if (!existing && taskIndex !== null) {
          taskMessages.set(taskIndex, targetMessage);
        }
        renderOrchestratorChat({ forceSidebar: currentChatMode === "orchestrator" });
        continue;
      }

      if (eventType === "error") {
        const errorText = typeof eventData.error === "string" ? eventData.error : "エラーが発生しました。";
        planMessage.text = `エラー: ${errorText}`;
        planMessage.pending = false;
        planMessage.ts = Date.now();
        setGeneralProxyAgent(null);
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
              planMessage.text = firstText;
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
            const alreadyExists = orchestratorState.messages.some(message => message.role === "assistant" && message.text === textValue);
            if (!alreadyExists) {
              addOrchestratorAssistantMessage(textValue);
            }
          });
        }
        const state = eventData.state && typeof eventData.state === "object" ? eventData.state : {};
        const finalProxyAgent = determineGeneralProxyAgentFromResult({
          executions: state.executions,
          tasks: state.tasks,
        });
        setGeneralProxyAgent(finalProxyAgent);
        renderOrchestratorChat({ forceSidebar: currentChatMode === "orchestrator" });
        break;
      }
    }
  } catch (error) {
    planMessage.text = `エラー: ${error.message}`;
    planMessage.pending = false;
    planMessage.ts = Date.now();
    setGeneralProxyAgent(null);
  } finally {
    userMessage.ts = userMessage.ts || Date.now();
    orchestratorState.sending = false;
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
    else if (currentChatMode === "iot") await sendIotChatMessage(value);
    else if (currentChatMode === "orchestrator") await sendOrchestratorMessage(value);
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
    else if (currentChatMode === "iot") await sendIotChatMessage(value);
    else if (currentChatMode === "orchestrator") await sendOrchestratorMessage(value);
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
    if (currentChatMode === "browser") {
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
    }
  });
}

const initialActiveView = document.querySelector(".nav-btn.active")?.dataset.view || "browser";
activateView(initialActiveView);
