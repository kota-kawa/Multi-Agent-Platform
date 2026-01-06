# Polyphony 

<img src="assets/icons/Polyphony-Logo.png" width="800px">


FastAPI と LangGraph を組み合わせ、計画・実行・レビューを行うマルチエージェントタスクをシングルページ UI へ逐次配信するリファレンススタックです。オーケストレーター、Browser/IoT/Life-Assistant ブリッジ、各種ダッシュボードを同梱しています。
This project combines FastAPI and LangGraph to plan, execute, and review multi-agent tasks while streaming their progress to a single-page UI. It bundles the orchestrator, Browser/IoT/Life-Assistant bridges, and dashboards into one reference stack.

## 目次 / Table of Contents
1. 概要 / Overview
2. 特長 / Features
3. リポジトリ構成 / Repository Layout
4. アーキテクチャ / Architecture
5. セットアップ / Getting Started
6. 設定リファレンス / Configuration Reference
7. オーケストレーターとエージェントの流れ / Orchestrator & Agent Flows
8. フロントエンド体験 / Front-end Experience
9. ランタイムデータとメモリ / Runtime Data & Memory
10. HTTP ルート / HTTP Routes
11. テスト / Testing
12. トラブルシューティング / Troubleshooting & Tips

## 概要 / Overview
Polyphony は `multi_agent_app` FastAPI ルーターを中心に、LangGraph + ChatOpenAI で動作するオーケストレーターと Browser/IoT/Life-Assistant ブリッジを提供します。`assets/` と `templates/` に格納された SPA がオーケストレーターイベントを可視化し、Browser Agent 埋め込み、IoT ウィジェット、共有チャット、メモリエディタを提供します。
Polyphony exposes the `multi_agent_app` FastAPI router that hosts a LangGraph + ChatOpenAI orchestrator plus Browser/IoT/Life-Assistant bridges. The SPA bundles in `assets/` and `templates/` mirror orchestrator events, embed the Browser Agent, and surface IoT widgets, shared chat, and memory editors.

## 特長 / Features
- LangGraph 駆動の `MultiAgentOrchestrator` が `plan → execute → review` ループと SSE (`plan`, `before_execution`, `browser_init`, `execution_progress`, `after_execution`, `complete`) を提供します。
- LangGraph-driven `MultiAgentOrchestrator` exposes `plan → execute → review` loops with SSE events (`plan`, `before_execution`, `browser_init`, `execution_progress`, `after_execution`, `complete`).
- Browser・IoT・Lifestyle・Scheduler エージェントをホストオーバーライド可能な形で差し替えられ、エイリアス/表示名マップを共有します。
- Browser, IoT, Lifestyle, and Scheduler agents are pluggable via host overrides that share alias/display-name maps.
- SPA は General / Browser / IoT / Chat ペインを持ち、Browser Agent ミラー表示とオーケストレーターサイドバーを備えます。
- The SPA contains General, Browser, IoT, and Chat panes with Browser Agent mirroring plus an orchestrator sidebar.
- 短期/長期メモリは 10/30 ターンごとに再生成され、履歴は自動的にリモートエージェントへ同期されます。
- Short- and long-term memories regenerate every 10/30 turns with automatic sync to remote agents.
- Docker + Compose による再現性の高い動作例（ポート 5050）と外部 Browser/Life-Assistant サービス連携を提供します。
- Docker + Compose workflows (port 5050) showcase reproducible demos with external Browser/Life-Assistant services.
- `chat_history.json`, `short_term_memory.json`, `long_term_memory.json` はランタイムアーティファクトとして扱い、コミットを避けます。
- `chat_history.json`, `short_term_memory.json`, and `long_term_memory.json` are runtime artifacts and should not be committed.

## リポジトリ構成 / Repository Layout
- `app.py` / `app_module.py` / `wsgi.py`: いずれも `multi_agent_app.create_app()` を呼び出す各種エントリポイントです。
- `app.py`, `app_module.py`, `wsgi.py`: thin entry points that import `multi_agent_app.create_app()` for CLI/local/WSGI.
- `multi_agent_app/__init__.py`: アプリケーションファクトリでテンプレート/アセットパスを配線し、ブループリントを登録します。
- `multi_agent_app/__init__.py`: application factory wiring template/static paths and registering the router.
- `multi_agent_app/routes.py`: FastAPI ルーター、SSE、Browser/IoT/Life-Assistant プロキシ、チャット履歴/メモリ API を定義します。
- `multi_agent_app/routes.py`: defines the FastAPI router, SSE endpoints, Browser/IoT/Life-Assistant proxies, and chat-history/memory APIs.
- `multi_agent_app/orchestrator.py`: LangGraph プランナー/実行/レビューワーとオーケストレータ状態 TypedDict を管理します。
- `multi_agent_app/orchestrator.py`: houses the LangGraph planner/executor/reviewer and orchestrator state TypedDicts.
- `multi_agent_app/browser.py`, `iot.py`, `lifestyle.py`, `scheduler.py`, `history.py`, `config.py`: 各エージェントブリッジ・上流ヘルパー・設定解析・タイムアウト定義です。
- `multi_agent_app/browser.py`, `iot.py`, `lifestyle.py`, `scheduler.py`, `history.py`, `config.py`: agent bridges, upstream helpers, config parsing, and timeout constants.
- `assets/app.js`, `assets/memory.js`, `assets/styles.css`: SPA のナビゲーション、SSE クライアント、Browser 埋め込み、IoT ウィジェット、メモリ編集、テーマを実装します。
- `assets/app.js`, `assets/memory.js`, `assets/styles.css`: SPA logic for navigation, SSE, Browser embed, IoT widgets, memory editor, and theming.
- `templates/index.html`, `templates/memory.html`: SPA シェルとして Browser Embed メタデータを注入し、バンドルを読み込みます。
- `templates/index.html`, `templates/memory.html`: SPA shells injecting Browser embed metadata and loading bundles.
- `Dockerfile`, `docker-compose.yml`: コンテナビルドと実行、共有ネットワーク `multi_agent_platform_net` を設定します。
- `Dockerfile`, `docker-compose.yml`: define container build/run steps and the shared `multi_agent_platform_net`.
- `tests/`: Pytest によるオーケストレーター初期化、プラン解析、Browser ブリッジ、履歴/メモリ、設定テストを配置します。
- `tests/`: pytest suites covering orchestrator init, plan parsing, browser bridges, history/memory helpers, and settings.
- `prompt.txt`, `view_prompt_*`, `AGENTS.md`: CLI オートメーション用プロンプトや詳細な開発者ガイドです。
- `prompt.txt`, `view_prompt_*`, `AGENTS.md`: prompt scratchpads and contributor instructions.
- `chat_history.json`, `short_term_memory.json`, `long_term_memory.json`: ランタイムデータであり、差分が大きくならないよう注意します。
- `chat_history.json`, `short_term_memory.json`, `long_term_memory.json`: runtime data files; avoid noisy diffs.

## アーキテクチャ / Architecture
### バックエンド (FastAPI + LangGraph)
### Backend (FastAPI + LangGraph)
- `create_app()` が `routes.py` のブループリントを公開し、SPA と JSON API を提供します。
- `create_app()` exposes the router from `routes.py`, serving the SPA and JSON APIs.
- `MultiAgentOrchestrator` は LangGraph + `ChatOpenAI` を用い、`ORCHESTRATOR_MAX_TASKS` とエージェントオーバーライドを尊重してタスクを管理します。
- `MultiAgentOrchestrator` uses LangGraph + `ChatOpenAI` to manage tasks while honoring `ORCHESTRATOR_MAX_TASKS` and agent overrides.
- SSE エンドポイントがライフサイクルイベントをストリームし、UI はプラン/実行/レビューをリアルタイムに描画します。
- SSE endpoints stream lifecycle events so the UI can render plan/execution/review timelines in real time.

### エージェントブリッジ / Agent Bridges
- Browser Agent 用ヘルパーはホストリスト正規化やエイリアス展開を行い、`/api/stream` と `/api/chat` を同時オープンして進捗をオーケストレーターイベントへ変換します。
- Browser Agent helpers normalize host lists, expand aliases, and open `/api/stream` plus `/api/chat` concurrently, translating progress into orchestrator events.
- IoT Agent ヘルパーは MCP サーバー経由でコマンドを実行し、`/iot_agent/*` ルートを通して HTTP 動詞をプロキシします。
- IoT Agent helpers execute commands via the MCP server and mirror HTTP verbs through `/iot_agent/*` routes.
- Life-Assistant (Lifestyle) プロキシは `/rag_answer` などのエンドポイントを提供し、オーケストレーター専用の `/agent_rag_answer` でリモート履歴への書き込みを防ぎます。
- Life-Assistant (Lifestyle) proxies expose `/rag_answer` and other endpoints, plus `/agent_rag_answer` so orchestrator calls avoid mutating remote history.

### フロントエンド SPA / Front-end SPA
- `assets/app.js` がサイドバーナビゲーション、SSE 処理、Browser Agent ミラー、IoT ダッシュボード、オーケストレーターチャット状態を制御します。
- `assets/app.js` drives sidebar navigation, SSE handling, Browser Agent mirroring, IoT dashboard widgets, and orchestrator chat state.
- Browser ビューは `window.BROWSER_EMBED_URL`（既定: ローカル noVNC）を埋め込み、フルスクリーン制御を備えます。
- The Browser view embeds `window.BROWSER_EMBED_URL` (default: local noVNC) with fullscreen controls.
- IoT ビューはモックデバイス/チャートを描画し、`localStorage` にスイッチ状態を保持します。
- The IoT view renders mock devices/charts and persists switch states in `localStorage`.
- 共有サイドバーチャットはモードに応じて Browser Agent ルートまたはオーケストレーターへ送信します。
- Shared sidebar chat sends to either Browser Agent routes or the orchestrator depending on mode.

### ストレージ / Storage & Runtime Data
- 軽量な JSON ストアがオーケストレートされた会話と再生成メモリを保持し、頻繁に再生成されるため実質的にエフェメラルです。
- Lightweight JSON stores track orchestrated conversations and regenerated memories; they refresh often and remain ephemeral.
- `_append_to_chat_history` は 5 エントリごとに Life-Assistant, Browser, IoT エージェントへ同期し、`[Agent] ...` 形式で応答を追記します。
- `_append_to_chat_history` syncs every five entries to Life-Assistant, Browser, and IoT agents, appending `[Agent] ...` replies as needed.

## セットアップ / Getting Started
### 前提条件 / Prerequisites
- Python 3.11 以上
- Docker と Docker Compose
- `secrets.env` (または `.env`) に `OPENAI_API_KEY` 等の資格情報
- Browser・IoT・Life-Assistant サービスへの接続（既定では `multi_agent_platform_net` 上を想定）
- Python 3.11+
- Docker and Docker Compose
- Credentials such as `OPENAI_API_KEY` stored in `secrets.env` (or `.env`)
- Optional access to Browser, IoT, and Life-Assistant services (default Compose wiring uses `multi_agent_platform_net`)

### ローカル開発手順 / Local Development
1. `python -m venv .venv && source .venv/bin/activate`
   Activate a virtual environment.
2. `pip install -r requirements.txt`
   Install dependencies.
3. `secrets.env`（または `.env`）を用意し、`OPENAI_API_KEY` や必要なエージェント設定を追加します。
   Prepare `secrets.env` (or `.env`) with `OPENAI_API_KEY` and any agent overrides.
4. 必要に応じて `export UVICORN_RELOAD=1` を設定します。
   Optionally export `UVICORN_RELOAD=1`.
5. `uvicorn app:app --host 0.0.0.0 --port 5050 --reload` を実行します。
   Run `uvicorn app:app --host 0.0.0.0 --port 5050 --reload`.
6. ブラウザで http://localhost:5050 を開き、各ビューを操作します。
   Open http://localhost:5050 and explore each view.

### Docker Compose
1. 共有ネットワークを確認: `docker network create multi_agent_platform_net`（既存ならスキップ）。
   Ensure the shared network exists: `docker network create multi_agent_platform_net` (no-op if it already exists).
2. `docker compose up --build web`
   Run `docker compose up --build web`.
3. 必要なら環境変数でリモートエージェント URL を上書きします。コンテナはポート 5050 を公開し、リポジトリをマウントします。
   Override remote agent URLs via env/Compose overrides as needed; the container exposes port 5050 and mounts the repo for live reloads.

## 設定リファレンス / Configuration Reference
`multi_agent_app/config.py` は `secrets.env` を優先的に読み込み（フォールバックで `.env`）、共通キーは以下の通りです。
`multi_agent_app/config.py` loads `secrets.env` first (with `.env` fallback). Common keys include:
- `OPENAI_API_KEY`（必須） / `OPENAI_API_KEY` (required)
- `ORCHESTRATOR_MODEL`, `ORCHESTRATOR_MAX_TASKS`
- `LIFESTYLE_API_BASE`, `LIFESTYLE_TIMEOUT`
- `BROWSER_AGENT_API_BASE`, `BROWSER_AGENT_CLIENT_BASE`, `BROWSER_EMBED_URL`, `BROWSER_AGENT_CONNECT_TIMEOUT`, `BROWSER_AGENT_TIMEOUT`, `BROWSER_AGENT_STREAM_TIMEOUT`, `BROWSER_AGENT_CHAT_TIMEOUT`
- `IOT_AGENT_API_BASE`, `IOT_AGENT_TIMEOUT`
- `MULTI_AGENT_NETWORK`（Compose ネットワーク上書き） / `MULTI_AGENT_NETWORK` (Compose network override)
- Browser/Life-Assistant/IoT 用ホストリストはカンマ区切り文字列で渡し、サーバー側で正規化されます。
- Browser/Life-Assistant/IoT host overrides accept comma-separated strings and are normalized server side.
- 秘密情報をハードコードせず、必ず環境変数で渡してください。
- Never hardcode secrets; always inject them via environment variables.

## オーケストレーターとエージェントの流れ / Orchestrator & Agent Flows
- プランナーは直接回答（タスク 0 件）またはタスクリスト生成を選択できます。
- The planner may answer directly (zero tasks) or emit a task list.
- レビューワーの JSON 応答により最大 2 回のリトライが制御され、失敗時は UI へ通知されます。
- Reviewer JSON responses control up to two retries before surfacing failures to the UI.
- `_AGENT_ALIASES` と `_AGENT_DISPLAY_NAMES` は `general`, `browser`, `iot`, `scheduler`, `lifestyle` をマッピングし、新エージェント追加時は SPA の `AGENT_TO_VIEW_MAP` も更新します。
- `_AGENT_ALIASES` and `_AGENT_DISPLAY_NAMES` map `general`, `browser`, `iot`, `scheduler`, `lifestyle`; update them plus the SPA `AGENT_TO_VIEW_MAP` when adding agents.
- Browser オーバーライドは `browser_agent_base(s)` として渡され、`_iter_browser_agent_bases` と `_canonicalise_browser_agent_base` で正規化されます。
- Browser overrides travel as `browser_agent_base(s)` and are normalized via `_iter_browser_agent_bases` and `_canonicalise_browser_agent_base`.
- `_execute_browser_task_with_progress` は `[browser-agent-final]` マーカーと `BROWSER_AGENT_FINAL_NOTICE` を含む進捗 SSE を生成します。
- `_execute_browser_task_with_progress` emits SSE updates with `[browser-agent-final]` markers and the `BROWSER_AGENT_FINAL_NOTICE` message.

## フロントエンド体験 / Front-end Experience
- General ビューはオーケストレーターのチャットとステータスタイムラインを表示し、Browser ビューはリモートデスクトップを埋め込みます。
- The General view shows orchestrator chat plus status timeline, while the Browser view embeds the remote desktop.
- IoT ビューはデバイスカードやチャートを描画し、Chat ビューは Browser Agent への直接指示に使います。
- The IoT view renders device cards/charts, and the Chat view sends direct commands to the Browser Agent.
- サイドバーチャットは Browser Agent の進捗を Browser ビュー非表示時にも共有します。
- Sidebar chat mirrors Browser Agent progress even if the Browser view is hidden.
- `assets/memory.js` は `/api/memory` を介して短期・長期メモリを取得/置換し、`templates/memory.html` を駆動します。
- `assets/memory.js` drives `templates/memory.html`, fetching/replacing memories via `/api/memory`.

## ランタイムデータとメモリ / Runtime Data & Memory
- `chat_history.json` はオーケストレーター経由の会話のみ保持し、`/rag_answer` ビューは別 path で扱います。
- `chat_history.json` stores only orchestrated conversations; the `/rag_answer` view bypasses it.
- 短期メモリは 10 件ごと、長期メモリは 30 件ごとに「以前 -> 更新後」の差分形式で保存されます。
- Short-term memory regenerates every 10 turns and long-term every 30, storing diffs as `previous -> updated` entries.
- `/chat_history` と `/reset_chat_history` がトランスクリプトを公開し、`/memory` と `/api/memory` がメモリファイルを読み書きします。
- `/chat_history` and `/reset_chat_history` expose transcripts, while `/memory` and `/api/memory` wrap the memory files.
- JSON ファイルはデモ用途のためコミットしないでください。
- Avoid committing these JSON files; treat them as demo artifacts.

## HTTP ルート / HTTP Routes
- `/`（SPA シェル）、`/memory`（専用メモリ UI）。
- `/` (SPA shell) and `/memory` (memory UI).
- `/orchestrator/chat` は SSE でプラン/実行/レビューイベントを配信します。
- `/orchestrator/chat` streams plan/execution/review events via SSE.
- `/rag_answer`, `/agent_rag_answer`, `/conversation_history`, `/conversation_summary`, `/reset_history` は Life-Assistant エージェントをプロキシします。
- `/rag_answer`, `/agent_rag_answer`, `/conversation_history`, `/conversation_summary`, `/reset_history` proxy the Life-Assistant agent.
- `/browser_agent/*` は Browser Agent API をミラーし、`/iot_agent/*` は IoT サービスへの CORS 回避プロキシです。
- `/browser_agent/*` mirrors Browser Agent APIs and `/iot_agent/*` proxies IoT services to avoid CORS issues.
- `/chat_history`, `/reset_chat_history`, `/api/memory` がローカル状態を提供します。
- `/chat_history`, `/reset_chat_history`, `/api/memory` expose local state.

## テスト / Testing
- 依存関係をインストール後、`pytest -q`（または `pytest`）を実行して主要ユースケースを確認します。
- After installing dependencies, run `pytest -q` (or `pytest`) to validate core flows.
- 外部 HTTP (`httpx.AsyncClient`) や LangChain クライアントはモックし、新機能追加時は `tests/` 以下にモジュール名と揃えたスイートを作成してください。
- Mock external HTTP (`httpx.AsyncClient`) and LangChain clients; add suites under `tests/` mirroring module names when extending coverage.
- 失敗を見つけた場合は再現手順・期待値を README または PR で共有します。
- Document failures with reproduction steps and expectations in the README or PR when necessary.

## トラブルシューティング / Troubleshooting & Tips
- FastAPI 起動前に `secrets.env` が読み込まれているか確認し、`OPENAI_API_KEY` が欠けていると LangChain でエラーになります。
- Ensure `secrets.env` loads before FastAPI starts; missing `OPENAI_API_KEY` causes LangChain errors.
- Browser/IoT サービスをリモートで動かす場合は DNS を確認するか、環境変数またはオーケストレータリクエストでホストを上書きします。
- When Browser/IoT services run remotely, verify DNS or override hosts via env or orchestrator requests.
- `app.py` のタイムアウト定数は実環境に合わせ、変更時は環境変数でトグルを公開してください。
- Keep timeout constants in `app.py` aligned with deployments and expose new toggles via env before changing behavior.
- SSE ペイロードやエージェントブリッジ契約を更新する際は、バックエンドと `assets/app.js` を同時に変更して UI の非同期を防ぎます。
- Update backend helpers and `assets/app.js` together when changing SSE payloads or agent bridge contracts to avoid UI desyncs.
- ブロードキャスト処理は `_send_recent_history_to_agents` のようにバックグラウンドスレッドを用いて FastAPI のイベントループを塞がないようにします。
- Use background threads (as `_send_recent_history_to_agents` does) to avoid blocking the FastAPI event loop during broadcasts.

## License
This project is licensed under the MIT License - see the [LICENSE.md](LICENSE.md) file for details.
