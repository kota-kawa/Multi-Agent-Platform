"""Configuration helpers and constants for the Multi-Agent Platform."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path


def _load_env_file(path: str = "secrets.env") -> None:
    """Best-effort env loader so orchestrator can pick up API keys."""

    env_path = Path(path)
    if not env_path.is_file():
        legacy = Path(".env")
        if path == "secrets.env" and legacy.is_file():
            env_path = legacy
        else:
            return

    try:
        content = env_path.read_text(encoding="utf-8")
    except OSError:
        return

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        cleaned = value.strip()
        if (
            (cleaned.startswith('"') and cleaned.endswith('"'))
            or (cleaned.startswith("'") and cleaned.endswith("'"))
        ):
            cleaned = cleaned[1:-1]
        os.environ.setdefault(key, cleaned)


def _current_datetime_line() -> str:
    """Return the timestamp text embedded into system prompts."""
    return datetime.now().strftime("現在の日時ー%Y年%m月%d日%H時%M分")


def _parse_timeout_env(name: str, default: float | None, *, allow_none: bool = False) -> float | None:
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    cleaned = raw_value.strip()
    if not cleaned:
        return default
    if allow_none and cleaned.lower() in {"none", "null"}:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return default


_load_env_file()


DEFAULT_GEMINI_BASES = (
    "http://localhost:5000",
    "http://faq_gemini:5000",
)
GEMINI_TIMEOUT = float(os.environ.get("FAQ_GEMINI_TIMEOUT", "30"))

# Known upstream IoT Agent deployment that should be reachable from public environments.
PUBLIC_IOT_AGENT_BASE = "https://iot-agent.project-kk.com"

DEFAULT_IOT_AGENT_BASES = (
    PUBLIC_IOT_AGENT_BASE,
)
IOT_AGENT_TIMEOUT = float(os.environ.get("IOT_AGENT_TIMEOUT", "30"))

DEFAULT_BROWSER_AGENT_BASES = (
    "http://browser-agent:5005",
    "http://localhost:5005",
)
BROWSER_AGENT_CONNECT_TIMEOUT = float(
    _parse_timeout_env("BROWSER_AGENT_CONNECT_TIMEOUT", 10.0) or 10.0
)
BROWSER_AGENT_TIMEOUT = float(_parse_timeout_env("BROWSER_AGENT_TIMEOUT", 120.0) or 120.0)
BROWSER_AGENT_STREAM_TIMEOUT = _parse_timeout_env(
    "BROWSER_AGENT_STREAM_TIMEOUT", None, allow_none=True
)
BROWSER_AGENT_CHAT_TIMEOUT = _parse_timeout_env(
    "BROWSER_AGENT_CHAT_TIMEOUT", None, allow_none=True
)

DEFAULT_BROWSER_EMBED_URL = (
    "http://127.0.0.1:7900/"
    "vnc_lite.html?autoconnect=1&resize=scale&scale=auto&view_clip=false"
)
DEFAULT_BROWSER_AGENT_CLIENT_BASE = "http://localhost:5005"
BROWSER_AGENT_FINAL_MARKER = "[browser-agent-final]"
BROWSER_AGENT_FINAL_NOTICE = "※ ブラウザエージェントの応答はここで終了です。"

ORCHESTRATOR_MODEL = os.environ.get("ORCHESTRATOR_MODEL", "gpt-4.1")
ORCHESTRATOR_MAX_TASKS = int(os.environ.get("ORCHESTRATOR_MAX_TASKS", "5"))


def _resolve_browser_embed_url() -> str:
    """Return the browser embed URL exposed to the frontend."""

    configured = os.environ.get("BROWSER_EMBED_URL", "").strip()
    if configured:
        return configured
    return DEFAULT_BROWSER_EMBED_URL


def _resolve_browser_agent_client_base() -> str:
    """Return the Browser Agent API base URL for browser clients."""

    configured = os.environ.get("BROWSER_AGENT_CLIENT_BASE", "").strip()
    if configured:
        return configured
    return DEFAULT_BROWSER_AGENT_CLIENT_BASE
