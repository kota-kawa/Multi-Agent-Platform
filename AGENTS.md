# Repository Guidelines

## Overview
- `multi_agent_app/` packages the Flask blueprint, LangGraph orchestrator, and Browser/IoT/Life-Assistant bridges; `app.py`, `app_module.py`, and `wsgi.py` are thin entrypoints that import `create_app()` from this package.
- LangGraph + `ChatOpenAI` drive `MultiAgentOrchestrator`, which plans, executes, and reviews up to `ORCHESTRATOR_MAX_TASKS` tasks via `/orchestrator/chat`.
- Front-end bundles in `assets/` implement the general/dashboard/browser/chat UI, orchestrator sidebar, and memory editor; HTML templates live in `templates/`.
- Runtime JSON (`chat_history.json`, `short_term_memory.json`, `long_term_memory.json`) act as lightweight stores for transcripts and memories—treat them as ephemeral and avoid noisy diffs.

## Key Modules & Files
- `app.py`, `app_module.py`, `wsgi.py`: runtime entrypoints (Flask CLI, local dev, WSGI) that all call `multi_agent_app.create_app()`.
- `multi_agent_app/__init__.py`: application factory wiring templates/static paths and registering the blueprint from `routes.py`.
- `multi_agent_app/routes.py`: Flask blueprint + HTTP routes (SPA shell, orchestrator SSE endpoint, Life-Assistant/Browser/IoT proxies, chat-history + memory APIs).
- `multi_agent_app/orchestrator.py`: LangGraph-based planner/executor/reviewer plus supporting TypedDicts that define orchestrator state.
- `multi_agent_app/browser.py`, `multi_agent_app/iot.py`, `multi_agent_app/gemini.py`, `multi_agent_app/history.py`, `multi_agent_app/config.py`: helper modules for upstream calls, env/config parsing, chat history propagation, and timeout constants.
- `assets/app.js`: SPA logic (view switching, orchestrator SSE client, Browser Agent stream mirroring, IoT dashboard widgets, shared sidebar chat).
- `assets/memory.js`: fetches/saves short- and long-term memories against `/api/memory`.
- `assets/styles.css`: shared theme, responsive layout, per-view styling (sidebar, browser embed frame, IoT cards, orchestrator panel).
- `templates/index.html`: serves the SPA shell, injects `browser_embed_url`/`browser_agent_client_base` meta tags, loads bundles.
- `templates/memory.html`: dedicated memory management UI backed by `assets/memory.js`.
- `Dockerfile` + `docker-compose.yml`: container build/run (port 5050) and network wiring for the Life-Assistantエージェント（FAQ_Gemini） + Browser Agent services.
- `prompt.txt`: Codex CLI automation scratchpad; keep instructions accurate if it’s used for tooling.

## Runtime Data & Memory Management
- `_append_to_chat_history` maintains `chat_history.json`, appends every user/assistant turn from the一般（オーケストレーター）ビュー, and asynchronously calls `_send_recent_history_to_agents` every five entries to sync the Life-Assistantエージェント（FAQ_Gemini）, Browser Agent, and IoT Agent. 送信スキーマは `{"history": [{"role": "...", "content": "..."}]}` に統一し、各エージェントから `should_reply`/`reply`/`addressed_agents` が返ってきた場合は `[Agent] ...` 形式で `chat_history.json` に追記する。Theチャットビュー (`/rag_answer`) now bypasses this file entirely so only orchestrated conversations persist locally.
- `/chat_history` + `/reset_chat_history` expose the local transcript to the UI; `assets/app.js` also duplicates key steps into sidebar/orchestrator panes.
- `/memory` + `/api/memory` wrap `short_term_memory.json` and `long_term_memory.json`. `POST /api/memory` replaces both files; reads tolerate missing/invalid JSON.
- Never commit large diffs for these JSON files; they are runtime artifacts used for demos/local state only.

## Configuration & Secrets
- `.env` is auto-loaded in `multi_agent_app/config.py` before constants are computed. Required keys include `OPENAI_API_KEY` plus optional overrides such as:
  - `ORCHESTRATOR_MODEL`, `ORCHESTRATOR_MAX_TASKS`
  - `FAQ_GEMINI_API_BASE`, `FAQ_GEMINI_TIMEOUT`（Life-Assistantエージェント向け）
  - `BROWSER_AGENT_API_BASE`, `BROWSER_AGENT_CLIENT_BASE`, `BROWSER_EMBED_URL`
  - `BROWSER_AGENT_CONNECT_TIMEOUT`, `BROWSER_AGENT_TIMEOUT`, `BROWSER_AGENT_STREAM_TIMEOUT`, `BROWSER_AGENT_CHAT_TIMEOUT`
  - `IOT_AGENT_API_BASE`, `IOT_AGENT_TIMEOUT`
- Use comma-delimited strings for multi-endpoint overrides (Browser/Gemini/IoT). `_canonicalise_browser_agent_base` normalizes hostnames; the UI can also send override lists in orchestrator requests.
- Do not bake secrets into source—pass them via env (compose, Docker, local shell).

## Development Workflow
- `python -m venv .venv && source .venv/bin/activate`
- `pip install -r requirements.txt`
- `flask --app app run --debug --port 5050` to serve the SPA + orchestrator locally.
- `docker compose up --build web` binds to port 5050 and injects service URLs for the Life-Assistantエージェント（FAQ_Gemini）/Browser Agent. Ensure the shared `multi_agent_platform_net` exists or set `MULTI_AGENT_NETWORK`.
- The Browser Agent iframe URL exposed to the UI defaults to the embedded noVNC endpoint; override `BROWSER_EMBED_URL` for remote deployments.

## Multi-Agent Orchestrator & APIs
- `MultiAgentOrchestrator` (LangGraph) uses three nodes—`plan`, `execute`, `review`—to iterate through task lists, with SSE events emitted for `plan`, `before_execution`, `browser_init`, `execution_progress`, `after_execution`, and `complete`. Preserve event names/shape; the SPA assumes them.
- `_AGENT_ALIASES` and `_AGENT_DISPLAY_NAMES` map friendly names to `faq`, `browser`, `iot`. Update both when adding agents, and expand the front-end’s `AGENT_TO_VIEW_MAP`.
- Planner prompt allows direct answers (zero tasks) if orchestrator can reply without agents. Review prompt enforces JSON responses driving retry logic (max two retries).
- `/orchestrator/chat` streams responses via SSE. Client payload may include `browser_agent_base(s)` to override Browser Agent hosts for that request.
- Life-Assistantエージェント（FAQ_Gemini）プロキシ: `/rag_answer`, `/conversation_history`, `/conversation_summary`, `/reset_history`—all call `_call_gemini` with per-request logging/error handling. オーケストレーター（一般ビュー）は `/agent_rag_answer` を使ってリモート履歴へ書き込まずに回答を取得する。
- IoT proxies: `/iot_agent/*` forwards method, headers, query, and body to `_proxy_iot_agent_request`. Be careful to keep the allowed header list synced with upstream requirements.

## Browser & IoT Agent Bridges
- Browser Agent helpers:
  - `_iter_browser_agent_bases`, `_expand_browser_agent_base`, `_canonicalise_browser_agent_base` clean up host overrides and add alias hostnames.
  - `_execute_browser_task_with_progress` opens both `/api/stream` (event stream) and `/api/chat` (task execution) concurrently, converts them to orchestrator `execution_progress` events, and tracks `[browser-agent-final]` markers plus `BROWSER_AGENT_FINAL_NOTICE`.
  - `_post_browser_agent` is used for history checks and `chat` API shims outside orchestrator flows.
- IoT Agent helpers:
  - `_call_iot_agent_command` and `_call_iot_agent_conversation_review` wrap JSON bodies for `/api/chat` and `/api/conversations/review`.
  - `_proxy_iot_agent_request` mirrors incoming verbs to `/iot_agent` routes so the SPA can talk to remote IoT APIs without CORS pain.
- Keep timeout constants near the top of `app.py` in sync with real-world deployments; expose new env toggles for any behavior change.

## Front-end SPA Notes
- `assets/app.js` drives the entire UI:
  - Sidebar navigation toggles General / Browser / IoT / Chat panes; the General view can temporarily host other views (proxy container) and surfaces which agent the orchestrator is using.
  - Shared sidebar chat submits commands either directly to Browser Agent (`/browser_agent/...`) or to the orchestrator depending on the selected mode; orchestrator mode consumes the SSE stream and mirrors Browser Agent progress when tasks run headless.
  - Browser view embeds the remote VNC URL from `window.BROWSER_EMBED_URL` and exposes fullscreen controls. IoT view renders mock device cards, charts, and persists switches in `localStorage`.
  - Orchestrator chat stores messages in `orchestratorState`, supports SSE fallback messaging, and displays planner/worker/reviewer blocks with status pills.
- `assets/memory.js` wires the memory editor form; keep API schema aligned with `/api/memory`.
- When adjusting UI state machines (view switching, SSE handling, agent mirrors), update the DOM hooks and CSS modifiers together to avoid stale references.

## Testing Guidelines
- Add `pytest` suites under `tests/` (create it if missing). Mirror module/function names eg. `test_orchestrator.py`, `test_browser_proxy.py`.
- Mock external HTTP calls (`requests.request`) and LangChain clients in tests; exercise success paths, network failures, timeout fallbacks, and SSE formatting helpers such as `_format_sse_event`.
- Include regression tests for `_send_recent_history_to_agents`, `_iter_*_bases` normalizers, and orchestrator plan/execution logic when making changes.
- Run `pytest -q` (or `pytest`) before raising a PR. Document any required external services or env vars for deterministic runs.

## Commit & Pull Request Guidelines
- Prefer Conventional Commits in English (`feat: add browser SSE mirror`). Keep the subject imperatively phrased and under 72 chars.
- PR descriptions should outline context, summarized changes, validation evidence (commands, screenshots), and mention new env vars or config files.
- Call out UI-impacting changes (SPA layout, SSE schema) and backend contract updates so downstream agent repos can adjust.

## Agent Integration Notes
- Add new agent constants and defaults near the other `DEFAULT_*` sections in `app.py`, wire them into `_AGENT_ALIASES`, `_AGENT_DISPLAY_NAMES`, and CLI/env overrides.
- Update `docker-compose.yml` to surface new agent URLs via environment variables so containerized runs stay reproducible.
- Extend the SPA mappings (`AGENT_TO_VIEW_MAP`, `GENERAL_PROXY_AGENT_LABELS`, etc.) plus any status text to surface the new agent’s activity.
- Validate connectivity with `requests` helpers before exposing routes, log warnings instead of crashing, and avoid blocking Flask threads—use background threads like `_send_recent_history_to_agents` when broadcasting updates.
- Whenever you change SSE payloads or Browser/IoT bridge contracts, update both backend helpers and `assets/app.js` handlers in the same commit to keep the UI responsive.
