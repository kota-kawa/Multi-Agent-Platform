/* Single Page UI logic
 * - View switching (ブラウザ / IoT / 要約チャット)
 * - Browser stage (iframe embed / pseudo noVNC)
 * - IoT dashboard (mock devices, live chart, localStorage persist)
 * - Chat + extractive summarizer (pure client-side)
 */

const $ = (q, c = document) => c.querySelector(q);
const $$ = (q, c = document) => Array.from(c.querySelectorAll(q));

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
navButtons.forEach(btn => {
  btn.addEventListener("click", () => {
    const view = btn.dataset.view;
    navButtons.forEach(b => b.classList.toggle("active", b === btn));
    Object.entries(views).forEach(([k, el]) => el.classList.toggle("active", k === view));
    const titles = {
      general: "一般ビュー",
      browser: "リモートブラウザ",
      iot: "IoT ダッシュボード",
      chat: "要約チャット",
    };
    appTitle.textContent = titles[view] ?? "リモートブラウザ";
    scheduleSidebarTogglePosition();
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
const urlInput = $("#urlInput");
const connectBtn = $("#connectBtn");
const stage = $("#browserStage");
const stageConnect = $("#stageConnect");
const backBtn = $("#backBtn");
const forwardBtn = $("#forwardBtn");
const reloadBtn = $("#reloadBtn");
const fullscreenBtn = $("#fullscreenBtn");

let currentIframe = null;

function connectBrowser() {
  const url = urlInput.value.trim();
  if (!url) return;

  // 既存の iframe を除去
  if (currentIframe) {
    currentIframe.remove();
    currentIframe = null;
  }
  // iframe を生成（クロスオリジンはブラウザ側の CSP でブロックされる場合あり）
  const ifr = document.createElement("iframe");
  ifr.src = url;
  ifr.setAttribute("title", "埋め込みブラウザ");
  stage.classList.remove("stage--placeholder");
  stage.innerHTML = ""; // プレースホルダをクリア
  stage.appendChild(ifr);
  currentIframe = ifr;
  connectBtn.textContent = "切断";
}

function disconnectBrowser() {
  stage.innerHTML = `
    <div class="novnc-logo" aria-hidden="true">noVNC</div>
    <button id="stageConnect" class="btn ghost large" type="button">接続</button>
    <p class="hint">上の入力欄で URL を変更できます（同一オリジンでない場合は埋め込みがブロックされることがあります）。</p>
  `;
  currentIframe = null;
  connectBtn.textContent = "接続";
  // 復活したボタンにイベントを付与
  $("#stageConnect").addEventListener("click", connectBrowser);
}

connectBtn.addEventListener("click", () => {
  if (currentIframe) disconnectBrowser();
  else connectBrowser();
});
stageConnect.addEventListener("click", connectBrowser);

backBtn.addEventListener("click", () => {
  try { currentIframe?.contentWindow?.history?.back(); } catch (_) {}
});
forwardBtn.addEventListener("click", () => {
  try { currentIframe?.contentWindow?.history?.forward(); } catch (_) {}
});
reloadBtn.addEventListener("click", () => {
  try { currentIframe?.contentWindow?.location?.reload(); } catch (_) {}
});
fullscreenBtn.addEventListener("click", () => {
  const el = currentIframe ?? stage;
  if (document.fullscreenElement) document.exitFullscreen();
  else el.requestFullscreen?.();
});

/* ---------- IoT Dashboard ---------- */

const deviceGrid = $("#deviceGrid");
const resetIoTBtn = $("#resetIoTBtn");
const addDeviceBtn = $("#addDeviceBtn");

const LS_KEY_IOT = "spa_iot_devices_v1";

const ICON_SENSOR = `<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M12 2a3 3 0 0 1 3 3v9.05a4.5 4.5 0 1 1-6 0V5a3 3 0 0 1 3-3zm0 16.5a2.5 2.5 0 0 0 2.5-2.5 2.5 2.5 0 0 0-5 0 2.5 2.5 0 0 0 2.5 2.5z"/><path fill="currentColor" d="M11 6h2v6h-2z"/></svg>`;
const ICON_ACTUATOR = `<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M11 21h-1l1-7H6l7-12h1l-1 7h5l-7 12z"/></svg>`;

function defaultDevices() {
  return [
    { id: crypto.randomUUID(), name: "温度センサー", type: "sensor", unit: "°C", on: true, value: 24.3 },
    { id: crypto.randomUUID(), name: "湿度センサー", type: "sensor", unit: "%", on: true, value: 55.2 },
    { id: crypto.randomUUID(), name: "ランプ", type: "actuator", on: false },
    { id: crypto.randomUUID(), name: "ファン", type: "actuator", on: false },
  ];
}

let devices = loadJSON(LS_KEY_IOT) ?? defaultDevices();
saveJSON(LS_KEY_IOT, devices);

function loadJSON(key) {
  try { return JSON.parse(localStorage.getItem(key)); }
  catch { return null; }
}
function saveJSON(key, val) {
  try { localStorage.setItem(key, JSON.stringify(val)); }
  catch {}
}

function renderDevices() {
  deviceGrid.innerHTML = "";
  devices.forEach(d => {
    const card = document.createElement("div");
    card.className = "device-card";
    card.dataset.id = d.id;
    card.innerHTML = `
      <div class="device-card-header">
        <div class="device-title">
          <span class="device-icon ${d.type}" aria-hidden="true">${getDeviceIcon(d)}</span>
          <div class="device-meta">
            <div class="device-name">${escapeHTML(d.name)}</div>
            <div class="device-type">${d.type === "sensor" ? "センサー" : "アクチュエータ"}</div>
          </div>
        </div>
        <div class="device-tools">
          <button class="icon-btn btn-rename" type="button" title="名称変更" aria-label="名称変更">✎</button>
          <button class="icon-btn btn-delete" type="button" title="削除" aria-label="削除">🗑</button>
        </div>
      </div>
      <div class="device-body">
        <div class="device-stat">
          <span class="device-stat-label">${d.type === "sensor" ? "現在値" : "現在の状態"}</span>
          ${d.type === "sensor"
            ? `<span class="device-reading">${formatReading(d)}</span>`
            : `<span class="device-status-pill ${d.on ? "on" : "off"}"><span class="status-dot ${d.on ? "status-on" : "status-off"}"></span>${d.on ? "ON" : "OFF"}</span>`
          }
        </div>
        <div class="device-controls">
          ${d.type === "sensor"
            ? `<button class="btn subtle btn-calibrate" type="button">校正</button>`
            : `<div class="switch ${d.on ? "on" : ""}" role="switch" aria-checked="${d.on}"></div>`
          }
        </div>
      </div>
    `;
    // events
    if (d.type === "actuator") {
      card.querySelector(".switch").addEventListener("click", () => {
        d.on = !d.on;
        saveJSON(LS_KEY_IOT, devices);
        renderDevices();
      });
    } else {
      // センサー校正：現在値に微調整ノイズ
      card.querySelector(".btn-calibrate").addEventListener("click", () => {
        const noise = (Math.random() - 0.5) * (d.name.includes("温度") ? 0.6 : 2.0);
        d.value = clamp(d.value + noise, d.name.includes("温度") ? -20 : 0, d.name.includes("温度") ? 60 : 100);
        saveJSON(LS_KEY_IOT, devices);
        renderDevices();
      });
    }
    card.querySelector(".btn-rename").addEventListener("click", () => {
      const name = prompt("新しい名前を入力", d.name);
      if (name && name.trim()) {
        d.name = name.trim();
        saveJSON(LS_KEY_IOT, devices);
        renderDevices();
      }
    });
    card.querySelector(".btn-delete").addEventListener("click", () => {
      if (!confirm(`「${d.name}」を削除しますか？`)) return;
      devices = devices.filter(x => x.id !== d.id);
      saveJSON(LS_KEY_IOT, devices);
      renderDevices();
    });

    deviceGrid.appendChild(card);
  });
}

function formatReading(d) {
  if (d.type !== "sensor") return d.on ? "ON" : "OFF";
  return `${d.value.toFixed(1)}${d.unit}`;
}
function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }
function escapeHTML(s) {
  return s.replace(/[&<>"']/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
}

function getDeviceIcon(device) {
  return device.type === "sensor" ? ICON_SENSOR : ICON_ACTUATOR;
}

renderDevices();

/* データ更新（擬似） */
setInterval(() => {
  // センサーの値をゆらぎで更新
  const temp = devices.find(d => d.type==="sensor" && /温度/.test(d.name));
  const hum  = devices.find(d => d.type==="sensor" && /湿度/.test(d.name));
  if (temp) {
    const delta = (Math.random() - 0.5) * 0.4;
    temp.value = clamp(temp.value + delta, -20, 60);
  }
  if (hum) {
    const delta = (Math.random() - 0.5) * 1.6;
    hum.value = clamp(hum.value + delta, 0, 100);
  }
  saveJSON(LS_KEY_IOT, devices);
  renderDevices();
}, 1500);

resetIoTBtn.addEventListener("click", () => {
  if (!confirm("IoT ダッシュボードを初期化しますか？")) return;
  devices = defaultDevices();
  saveJSON(LS_KEY_IOT, devices);
  renderDevices();
});

addDeviceBtn.addEventListener("click", () => {
  const name = prompt("デバイス名（例：CO₂ センサー / ポンプ）");
  if (!name) return;
  const kind = prompt("種類を入力（sensor / actuator）", "sensor");
  const type = (kind || "").toLowerCase() === "actuator" ? "actuator" : "sensor";
  const d = { id: crypto.randomUUID(), name: name.trim(), type, on: false };
  if (type === "sensor") { d.unit = ""; d.value = 0; }
  devices.push(d);
  saveJSON(LS_KEY_IOT, devices);
  renderDevices();
});

/* ---------- Chat + Summarizer ---------- */

const chatLog = $("#chatLog");
const sidebarChatLog = $("#sidebarChatLog");
const chatInput = $("#chatInput");
const sidebarChatInput = $("#sidebarChatInput");
const chatForm = $("#chatForm");
const sidebarChatForm = $("#sidebarChatForm");
const summaryBox = $("#summaryBox");
const clearChatBtn = $("#clearChatBtn");
const SUMMARY_PLACEHOLDER = "左側のチャットでメッセージを送信すると、ここに要約が表示されます。";

function updateSummaryBox() {
  if (!summaryBox) return;
  const hasSummarizable = messages.some(m => m.role !== "system");
  if (!hasSummarizable) {
    summaryBox.textContent = SUMMARY_PLACEHOLDER;
    return;
  }
  const summary = summarizeMessages(messages).trim();
  summaryBox.textContent = summary ? summary : SUMMARY_PLACEHOLDER;
}

const LS_KEY_CHAT = "spa_chat_messages_v1";
let messages = loadJSON(LS_KEY_CHAT) ?? [];

function ensureIntroMessage() {
  if (messages.length === 0) {
    messages.push({
      role: "system",
      text: "ここは要約チャットです。左サイドバーの共通チャットからメッセージを送信すると重要なポイントをここに表示します。",
      ts: Date.now()
    });
    saveJSON(LS_KEY_CHAT, messages);
  }
}

function createMessageElement(m) {
  const el = document.createElement("div");
  el.className = `msg ${m.role}`;
  el.innerHTML = `
      ${escapeHTML(m.text)}
      <span class="msg-time">${new Date(m.ts).toLocaleString("ja-JP")}</span>
    `;
  return el;
}

function renderChat() {
  if (chatLog) {
    chatLog.innerHTML = "";
    messages.forEach(m => {
      chatLog.appendChild(createMessageElement(m));
    });
    chatLog.scrollTop = chatLog.scrollHeight;
  }

  if (sidebarChatLog) {
    sidebarChatLog.innerHTML = "";
    const recent = messages.slice(-20);
    recent.forEach(m => {
      const el = createMessageElement(m);
      el.classList.add("compact");
      sidebarChatLog.appendChild(el);
    });
    sidebarChatLog.scrollTop = sidebarChatLog.scrollHeight;
  }
}

function pushUserMessage(text) {
  messages.push({ role: "user", text, ts: Date.now() });
  saveJSON(LS_KEY_CHAT, messages);
  renderChat();
  updateSummaryBox();
}

ensureIntroMessage();
renderChat();
updateSummaryBox();

if (chatForm) {
  chatForm.addEventListener("submit", (e) => {
    e.preventDefault();
    const text = chatInput.value.trim();
    if (!text) return;
    pushUserMessage(text);
    chatInput.value = "";
    if (sidebarChatInput) sidebarChatInput.value = "";
  });
}

sidebarChatForm.addEventListener("submit", (e) => {
  e.preventDefault();
  const text = sidebarChatInput.value.trim();
  if (!text) return;
  pushUserMessage(text);
  sidebarChatInput.value = "";
  if (chatInput) chatInput.value = "";
});

clearChatBtn.addEventListener("click", () => {
  if (!confirm("チャット履歴をクリアしますか？")) return;
  messages = [];
  saveJSON(LS_KEY_CHAT, messages);
  ensureIntroMessage();
  renderChat();
  updateSummaryBox();
});

/* --- Simple Extractive Summarizer (JP friendly heuristic) --- */
function summarizeMessages(msgs, maxSentences = 6) {
  const text = msgs.filter(m => m.role !== "system").map(m => m.text).join("。") + "。";
  if (!text.trim()) return "";

  // 文分割（句点・疑問符・感嘆符・改行）
  const sentences = text.split(/(?<=[。！？\?！\n])\s*/).map(s => s.trim()).filter(Boolean);

  // ストップワード（日本語の頻出助詞・助動詞など簡易版）
  const stop = new Set("の こと もの です ます する した して に は が を と で から まで より へ も のに そして また ので ため たり だ が です。 ます。 する。".split(/\s+/));

  // 単語出現頻度（極めて単純な形態素もどき：全角・半角記号除去、漢字ひらカナ・英数）
  const terms = {};
  sentences.forEach(s => {
    const words = s.replace(/[^\p{Script=Hiragana}\p{Script=Katakana}\p{Script=Han}A-Za-z0-9]/gu, " ")
                   .split(/\s+/).filter(w => w && !stop.has(w));
    words.forEach(w => { terms[w] = (terms[w] || 0) + 1; });
  });

  // 文スコア：出現頻度合計 + 長さペナルティ
  const scored = sentences.map((s, i) => {
    const words = s.replace(/[^\p{Script=Hiragana}\p{Script=Katakana}\p{Script=Han}A-Za-z0-9]/gu, " ")
                   .split(/\s+/).filter(Boolean);
    const score = words.reduce((acc, w) => acc + (terms[w] || 0), 0) / Math.sqrt(words.length + 1);
    // 先頭・末尾バイアス（導入・結論が入りやすいように）
    const bias = (i === 0 ? 0.8 : 0) + (i === sentences.length - 1 ? 0.5 : 0);
    return { i, s, score: score + bias };
  });

  scored.sort((a,b)=>b.score-a.score);
  const top = scored.slice(0, Math.min(maxSentences, scored.length))
                    .sort((a,b)=>a.i-b.i)
                    .map(x=>x.s);

  // 箇条書き風に整形
  return "▼ 要点\n" + top.map(s => `・${s.replace(/\s+/g," ").trim()}`).join("\n");
}

