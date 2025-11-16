import { activateView, registerViewActivationHook, initialActiveView, requestMainBrowserViewportSync } from './js/layout.js';
import { ensureChatInitialized, ensureBrowserAgentInitialized, ensureOrchestratorInitialized, ensureIotChatInitialized, setChatMode } from './js/chat.js';
import { ensureIotDashboardInitialized } from './js/iot.js';

registerViewActivationHook(({ view, isBrowserView, isChatView, isIotView, isGeneralView }) => {
  const modeMap = {
    browser: 'browser',
    iot: 'iot',
    general: 'orchestrator',
    chat: 'general',
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
});

activateView(initialActiveView);
