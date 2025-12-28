import { activateView, registerViewActivationHook, initialActiveView, requestMainBrowserViewportSync } from './js/layout.js';
import { ensureChatInitialized, ensureBrowserAgentInitialized, ensureOrchestratorInitialized, ensureIotChatInitialized, ensureSchedulerChatInitialized, setChatMode } from './js/chat.js';
import { ensureIotDashboardInitialized } from './js/iot.js';
import { ensureSchedulerAgentInitialized } from './js/scheduler.js';
import { initSettingsModal } from './js/settings.js';
import { refreshAgentStatus } from './js/agent-status.js';

let schedulerWarmupScheduled = false;

function warmupSchedulerResources() {
  if (schedulerWarmupScheduled) return;
  schedulerWarmupScheduled = true;

  const run = () => {
    try {
      // Preload calendar/chat so the Schedule view renders instantly when selected.
      ensureSchedulerAgentInitialized();
      ensureSchedulerChatInitialized({ forceSidebar: false });
    } catch (error) {
      console.warn("Scheduler warmup failed:", error);
    }
  };

  if (typeof window.requestIdleCallback === "function") {
    window.requestIdleCallback(run, { timeout: 1500 });
  } else {
    window.setTimeout(run, 600);
  }
}

registerViewActivationHook(({ view, isBrowserView, isChatView, isIotView, isGeneralView, isSchedulerView }) => {
  const modeMap = {
    browser: 'browser',
    iot: 'iot',
    general: 'orchestrator',
    chat: 'general',
    schedule: 'scheduler',
  };
  setChatMode(modeMap[view] ?? 'general');

  if (isChatView) {
    ensureChatInitialized({ showLoadingSummary: true });
  } else if (!isBrowserView && !isIotView && !isGeneralView) {
    ensureChatInitialized();
  }

  if (isBrowserView) {
    ensureBrowserAgentInitialized({ showLoading: true, forceSidebar: true });
    requestMainBrowserViewportSync({ reloadFallback: true });
  }

  if (isIotView) {
    ensureIotDashboardInitialized({ showLoading: true });
    ensureIotChatInitialized({ forceSidebar: true });
  }

  if (isGeneralView) {
    ensureOrchestratorInitialized({ forceSidebar: true });
  }

  if (isSchedulerView) {
    ensureSchedulerAgentInitialized();
    ensureSchedulerChatInitialized({ forceSidebar: true });
  }
});

initSettingsModal();
activateView(initialActiveView);
warmupSchedulerResources();
refreshAgentStatus();
setInterval(() => refreshAgentStatus({ silent: true }), 30000);
