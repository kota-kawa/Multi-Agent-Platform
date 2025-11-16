import { $, $$ } from "./dom-utils.js";

/* Single Page UI logic
 * - View switching (ブラウザ / IoT / 要約チャット)
 * - Browser stage (iframe embed / pseudo noVNC)
 * - IoT dashboard (mock devices, live chart, localStorage persist)
 * - Chat + extractive summarizer (pure client-side)
 */

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

const viewPlacements = new Map();
const AGENT_TO_VIEW_MAP = { browser: "browser", iot: "iot", faq: "chat", qa: "chat", chat: "chat" };
const GENERAL_PROXY_AGENT_LABELS = {
  faq: "QAエージェント",
  qa: "QAエージェント",
  browser: "ブラウザエージェント",
  iot: "IoT エージェント",
  chat: "要約チャット",
};
const GENERAL_PROXY_VIEW_LABELS = {
  browser: "リモートブラウザ",
  chat: "要約チャット",
  iot: "IoT ダッシュボード",
};
const BROWSER_AGENT_FINAL_MARKER = "[browser-agent-final]";

export function containsBrowserAgentFinalMarker(text) {
  return typeof text === "string" && text.includes(BROWSER_AGENT_FINAL_MARKER);
}

let generalProxyTargetView = null;
let generalProxyAgentKey = null;
let generalProxyViewKey = null;
export const initialActiveView = document.querySelector(".nav-btn.active")?.dataset.view || "general";
let currentViewKey = initialActiveView;
let viewActivationHook = null;
let generalProxyRenderHook = null;
let generalProxyAgentHook = null;

export function registerViewActivationHook(handler) {
  viewActivationHook = typeof handler === "function" ? handler : null;
}

export function registerGeneralProxyRenderHook(handler) {
  generalProxyRenderHook = typeof handler === "function" ? handler : null;
}

export function registerGeneralProxyAgentHook(handler) {
  generalProxyAgentHook = typeof handler === "function" ? handler : null;
}

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
    if (typeof generalProxyRenderHook === "function") {
      generalProxyRenderHook({
        agent: generalProxyAgentKey,
        view: null,
        currentView: currentViewKey,
      });
    }
    return;
  }

  if (generalProxyTargetView === "browser") {
    activateGeneralBrowserProxy();
    requestGeneralBrowserViewportSync({ reloadFallback: true });
  }

  if (typeof generalProxyRenderHook === "function") {
    generalProxyRenderHook({
      agent: generalProxyAgentKey,
      view: generalProxyTargetView,
      currentView: currentViewKey,
    });
  }
}

export function isGeneralProxyAgentBrowser() {
  return generalProxyAgentKey === "browser";
}

export function setGeneralProxyAgent(agentKey) {
  const normalizedAgent = typeof agentKey === "string" ? agentKey.trim().toLowerCase() : "";
  const targetView = resolveAgentToView(normalizedAgent);
  const previousAgent = generalProxyAgentKey;
  generalProxyAgentKey = targetView ? normalizedAgent : null;
  generalProxyTargetView = targetView;
  if (typeof generalProxyAgentHook === "function") {
    generalProxyAgentHook({
      previousAgent,
      agent: generalProxyAgentKey,
      targetView: generalProxyTargetView,
    });
  }
  updateGeneralViewProxy();
}

export function determineGeneralProxyAgentFromResult(result) {
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

export function activateView(viewKey) {
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

  if (typeof viewActivationHook === "function") {
    viewActivationHook({
      view: target,
      isBrowserView,
      isChatView,
      isIotView,
      isGeneralView,
    });
  }
  updateGeneralViewProxy();
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
  view_clip: "false",
};

function normalizeBrowserEmbedUrl(value) {
  if (!value) return value;

  try {
    const url = new URL(value, window.location.origin);
    const params = url.searchParams;

    for (const [key, defaultValue] of Object.entries(DEFAULT_NOVNC_PARAMS)) {
      const currentValue = params.get(key);
      if (key === "resize") {
        if (!currentValue || !ALLOWED_RESIZE_VALUES.has(currentValue)) {
          params.set(key, defaultValue);
        }
        continue;
      }

      if (key === "view_clip") {
        if (currentValue?.toLowerCase() !== defaultValue) {
          params.set(key, defaultValue);
        }
        continue;
      }

      if (!currentValue) {
        params.set(key, defaultValue);
      }
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

  return normalizeBrowserEmbedUrl(
    "http://127.0.0.1:7900/vnc_lite.html?autoconnect=1&resize=scale&scale=auto&view_clip=false",
  );
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

export function requestMainBrowserViewportSync({ reloadFallback = false } = {}) {
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
