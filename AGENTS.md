# Repository Guidelines

## Project Structure & Module Organization
This repo hosts the Flask orchestrator for the multi-agent platform. `app.py` contains the HTTP routes, agent orchestration graph, and shared utility helpers. Front-end bundles reside in `assets/` (JavaScript, CSS) and server-rendered templates in `templates/`. Runtime state lives in `chat_history.json`, `short_term_memory.json`, and `long_term_memory.json`; treat them as ephemeral and avoid large diffs. Use `.env` for local config, and rely on `Dockerfile` plus `docker-compose.yml` for containerization. For sizable backend features, create dedicated modules or packages instead of swelling `app.py`.

## Build, Test, and Development Commands
- `python -m venv .venv && source .venv/bin/activate`: create and enter an isolated environment.
- `pip install -r requirements.txt`: install Flask, LangChain, and supporting SDKs.
- `flask --app app run --debug --port 5050`: launch the development server with auto-reload.
- `docker compose up --build web`: rebuild and start the containerized stack with the shared agent network.

## Coding Style & Naming Conventions
Follow PEP 8 with 4-space indentation and snake_case for Python symbols. Preserve the existing use of type hints and favor small, composable functions for orchestrator logic. Log agent interactions with `logging`, not `print`. Front-end modules in `assets/` use camelCase for functions; keep filenames concise and avoid spaces or hyphens.

## Testing Guidelines
Add `pytest` suites under a top-level `tests/` package (create it if missing). Mirror feature names in test modules—for example, `test_recent_history.py` for `_send_recent_history_to_agents`. Validate both happy paths and failure handling for agent calls. Run `pytest` (optionally `pytest -q`) before every pull request and document required external services or fixtures.

## Commit & Pull Request Guidelines
Recent history mixes Conventional Commit syntax (`feat:`, `fix:`) with concise Japanese summaries. Prefer Conventional Commits in English so automation can parse changes. Keep summaries imperative and under 72 characters. Pull requests should include context, a short changelog, validation notes (commands or screenshots), and links to related issues. Highlight configuration updates or new environment variables in the description.

## Agent Integration Notes
Default agent endpoints and timeouts sit near the top of `app.py`. Override them locally via `.env` (`FAQ_GEMINI_API_BASE`, `BROWSER_AGENT_API_BASE`, `IOT_AGENT_TIMEOUT`) rather than editing source. When wiring a new agent, add its constants alongside the existing defaults, validate connectivity with `requests`, and expose its base URL through `docker-compose.yml` so containerized runs stay reproducible.
