# Project Context: Symphony

## Overview
This project is **Symphony** built with **FastAPI**, **LangGraph**, and **OpenAI**. It serves as an orchestrator that coordinates tasks across specialized agents:
*   **Life-Style Agent** (Lifestyle/schedule management)
*   **Browser Agent** (Web automation via Selenium/Playwright/etc.)
*   **IoT Agent** (Smart home control)

The core application is a FastAPI web server providing a Single Page Application (SPA) frontend and an SSE-based API for the orchestrator.

## Architecture
*   **Backend:** Python 3, FastAPI (Router in `multi_agent_app`).
*   **Orchestrator:** LangGraph based (`multi_agent_app/orchestrator.py`). It plans, executes, and reviews tasks.
*   **Frontend:** Vanilla JS + HTML/CSS (`assets/`, `templates/`). No build step required.
*   **Communication:**
    *   **Client-Server:** REST + Server-Sent Events (SSE).
    *   **Inter-Agent:** HTTP requests (proxied by the FastAPI backend).
*   **State:**
    *   **Ephemeral:** `chat_history.json`, `short_term_memory.json`, `long_term_memory.json`. These are runtime artifacts and should generally be ignored in git commits.
    *   **Configuration:** `secrets.env` (API keys), Environment variables (Service URLs).

## Key Files & Directories
*   `app.py`: Main entry point.
*   `multi_agent_app/`: Core application logic.
    *   `orchestrator.py`: The brain of the system (LangGraph definition).
    *   `routes.py`: API endpoints.
    *   `config.py`: Configuration loader (`secrets.env`).
*   `assets/`: Frontend static files (`app.js` drives the UI).
*   `templates/`: Jinja2 HTML templates.
*   `AGENTS.md`: Detailed system documentation (Reference this for deep dives).
*   `docker-compose.yml`: Service orchestration.

## Setup & Execution
### Prerequisites
*   Python 3.10+
*   Docker & Docker Compose (optional but recommended)
*   `secrets.env` file with `OPENAI_API_KEY`.

### Local Development
```bash
# 1. Create and activate venv
python -m venv .venv
source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Run the application (default port 5050)
uvicorn app:app --host 0.0.0.0 --port 5050 --reload
```

### Docker
```bash
# Build and run the web service
docker compose up --build web
```
*Note: The container expects an external network `multi_agent_platform_net` or will fallback to default if configured.*

## Conventions
*   **Language:** The user prefers interaction in **Japanese**, but code/docs are primarily in English.
*   **Commits:** Conventional Commits (e.g., `feat: ...`, `fix: ...`).
*   **Testing:** `pytest` (Tests located in `tests/`).
*   **Style:** Follow PEP 8 for Python.
*   **Memory Files:** Do not manually edit or commit `*.json` memory files unless debugging specific persistence issues.

## Environment Variables
Important variables defined in `multi_agent_app/config.py` and `docker-compose.yml`:
*   `OPENAI_API_KEY`: Required.
*   `LIFESTYLE_API_BASE`: URL for the Life-Style Agent.
*   `BROWSER_AGENT_API_BASE`: URL for the Browser Agent.
*   `IOT_AGENT_API_BASE`: URL for the IoT Agent.
*   `UVICORN_RELOAD`: Set to "1" for auto-reload in development.

## Critical Instruction / 重要指示
どれだけ時間とコストをかけてもよいので、確実に目標が達成されるようにしてほしい。
