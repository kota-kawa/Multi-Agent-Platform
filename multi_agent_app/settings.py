"""Helpers for loading and saving operator-managed settings."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, List

from .config import ORCHESTRATOR_MODEL

DEFAULT_AGENT_CONNECTIONS: Dict[str, bool] = {
    "lifestyle": True,
    "browser": True,
    "iot": True,
}

DEFAULT_MODEL_SELECTIONS: Dict[str, Dict[str, str]] = {
    "orchestrator": {"provider": "openai", "model": ORCHESTRATOR_MODEL, "base_url": ""},
    "browser": {"provider": "openai", "model": "gpt-4.1", "base_url": ""},
    "lifestyle": {"provider": "openai", "model": "gpt-4.1", "base_url": ""},
    "iot": {"provider": "openai", "model": "gpt-4.1", "base_url": ""},
}

DEFAULT_MEMORY_SETTINGS: Dict[str, bool] = {
    "enabled": True,
}

LLM_PROVIDERS: Dict[str, Dict[str, Any]] = {
    "openai": {
        "label": "OpenAI",
        "api_key_env": "OPENAI_API_KEY",
        "base_url_env": "OPENAI_BASE_URL",
        "default_base_url": None,
        "models": [
            {"id": "gpt-4.1", "label": "GPT-4.1"},
            {"id": "gpt-5.1", "label": "GPT-5.1"},
            {"id": "gpt-5-mini", "label": "GPT-5 mini"},
        ],
    },
    "gemini": {
        "label": "Gemini (Google)",
        "api_key_env": "GEMINI_API_KEY",
        "base_url_env": "GEMINI_API_BASE",
        "default_base_url": None,
        "models": [
            {"id": "gemini-1.5-flash", "label": "Gemini 1.5 Flash"},
            {"id": "gemini-1.5-pro", "label": "Gemini 1.5 Pro"},
            {"id": "gemini-2.0-flash", "label": "Gemini 2.0 Flash"},
        ],
    },
    "claude": {
        "label": "Claude (Anthropic)",
        "api_key_env": "ANTHROPIC_API_KEY",
        "base_url_env": "ANTHROPIC_API_BASE",
        "default_base_url": None,
        "models": [
            {"id": "claude-3-5-sonnet-20240620", "label": "Claude 3.5 Sonnet"},
            {"id": "claude-3-haiku-20240307", "label": "Claude 3 Haiku"},
            {"id": "claude-3-opus-20240229", "label": "Claude 3 Opus"},
        ],
    },
    "groq": {
        "label": "Groq",
        "api_key_env": "GROQ_API_KEY",
        "base_url_env": "GROQ_API_BASE",
        "default_base_url": "https://api.groq.com/openai/v1",
        "models": [
            {"id": "llama-3.3-70b-versatile", "label": "Llama 3.3 70B (Groq)"},
            {"id": "llama-3.1-8b-instant", "label": "Llama 3.1 8B (Groq)"},
            {"id": "openai/gpt-oss-20b", "label": "GPT-OSS 20B (Groq)"},
            {"id": "meta-llama/llama-4-maverick-17b-128e-instruct", "label": "Llama 4 Maverick 17B (Groq)"},
            {"id": "moonshotai/kimi-k2-instruct-0905", "label": "Kimi K2 Instruct 0905 (Groq)"},
            {"id": "qwen/qwen3-32b", "label": "Qwen3 32B (Groq)"},
        ],
    },
}

_AGENT_CONNECTIONS_FILE = "agent_connections.json"
_MODEL_SETTINGS_FILE = "model_settings.json"
_MEMORY_SETTINGS_FILE = "memory_settings.json"
_REPO_ROOT = Path(__file__).resolve().parents[2]
_AGENT_ENV_PATHS: Dict[str, Path] = {
    "orchestrator": _REPO_ROOT / "Multi-Agent-Platform" / "secrets.env",
    "browser": _REPO_ROOT / "Browser-Agent" / "secrets.env",
    "lifestyle": _REPO_ROOT / "Life-Assistant-Agent" / "secrets.env",
    "iot": _REPO_ROOT / "IoT-Agent" / "secrets.env",
}


def _coerce_bool(value: Any, fallback: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    return fallback


def _merge_connections(raw: Any) -> Dict[str, bool]:
    merged = dict(DEFAULT_AGENT_CONNECTIONS)
    if not isinstance(raw, dict):
        return merged
    source = raw.get("agents") if "agents" in raw else raw
    if not isinstance(source, dict):
        return merged

    for key, default_value in DEFAULT_AGENT_CONNECTIONS.items():
        merged[key] = _coerce_bool(source.get(key), default_value)
    return merged


def load_agent_connections() -> Dict[str, bool]:
    """Load the on/off state for each agent. Defaults to all enabled."""
    try:
        with open(_AGENT_CONNECTIONS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return dict(DEFAULT_AGENT_CONNECTIONS)

    return _merge_connections(data)


def save_agent_connections(payload: Dict[str, Any]) -> Dict[str, bool]:
    """Persist the agent connection toggles to disk."""
    connections = _merge_connections(payload)
    with open(_AGENT_CONNECTIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(connections, f, ensure_ascii=False, indent=2)
    return connections


def _read_env_file(path: Path) -> Dict[str, str]:
    """Parse a simple KEY=VALUE env file into a dict."""

    values: Dict[str, str] = {}
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return values

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
        values[key] = cleaned
    return values


def _load_agent_env(agent: str) -> Dict[str, str]:
    """Return a merged view of environment variables for the given agent."""

    env: Dict[str, str] = {key: value for key, value in os.environ.items()}
    env_path = _AGENT_ENV_PATHS.get(agent)
    if env_path:
        env.update(_read_env_file(env_path))
    return env


def _merge_model_selection(raw: Any) -> Dict[str, Dict[str, str]]:
    """Coerce user-provided model selection into a safe structure."""

    merged = dict(DEFAULT_MODEL_SELECTIONS)
    if not isinstance(raw, dict):
        return merged

    source = raw.get("selection") if "selection" in raw else raw
    if not isinstance(source, dict):
        return merged

    for agent, default_selection in DEFAULT_MODEL_SELECTIONS.items():
        value = source.get(agent) if isinstance(source.get(agent), dict) else {}
        provider = (value.get("provider") or default_selection["provider"]).strip()
        model = (value.get("model") or default_selection["model"]).strip()
        base_url = (value.get("base_url") or default_selection.get("base_url", "") or "").strip()
        provider_meta = LLM_PROVIDERS.get(provider)
        provider_default_base = str(provider_meta.get("default_base_url") or "").strip() if provider_meta else ""
        if not base_url and provider_default_base:
            base_url = provider_default_base

        if not provider_meta:
            merged[agent] = dict(default_selection)
            continue

        valid_models = {m["id"] for m in provider_meta.get("models", [])}
        if model not in valid_models:
            merged[agent] = dict(default_selection)
            continue

        merged[agent] = {"provider": provider, "model": model, "base_url": base_url}
    return merged


def load_model_settings() -> Dict[str, Dict[str, str]]:
    """Load the selected LLM per agent."""

    try:
        with open(_MODEL_SETTINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return dict(DEFAULT_MODEL_SELECTIONS)

    return _merge_model_selection(data)


def save_model_settings(payload: Dict[str, Any]) -> Dict[str, Dict[str, str]]:
    """Persist the model selections to disk."""

    existing = load_model_settings()
    incoming = payload.get("selection") if isinstance(payload, dict) and "selection" in payload else payload

    merged_input: Dict[str, Dict[str, str]] = dict(existing)
    if isinstance(incoming, dict):
        for agent, value in incoming.items():
            if agent not in DEFAULT_MODEL_SELECTIONS or not isinstance(value, dict):
                continue
            merged_input[agent] = {**existing.get(agent, {}), **value}

    selection = _merge_model_selection(merged_input)
    with open(_MODEL_SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(selection, f, ensure_ascii=False, indent=2)
    return selection


def load_memory_settings() -> Dict[str, bool]:
    """Load the memory usage settings."""
    try:
        with open(_MEMORY_SETTINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return dict(DEFAULT_MEMORY_SETTINGS)
    
    return {
        "enabled": _coerce_bool(data.get("enabled"), DEFAULT_MEMORY_SETTINGS["enabled"])
    }


def save_memory_settings(payload: Dict[str, Any]) -> Dict[str, bool]:
    """Persist the memory usage settings."""
    enabled = _coerce_bool(payload.get("enabled"), DEFAULT_MEMORY_SETTINGS["enabled"])
    settings = {"enabled": enabled}
    with open(_MEMORY_SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)
    return settings


def get_llm_options() -> Dict[str, List[Dict[str, Any]]]:
    """Expose provider/model options for the UI."""

    providers: List[Dict[str, Any]] = []
    for provider_id, meta in LLM_PROVIDERS.items():
        providers.append(
            {
                "id": provider_id,
                "label": meta.get("label") or provider_id,
                "models": meta.get("models", []),
            },
        )
    return {"providers": providers}


def resolve_llm_config(agent: str) -> Dict[str, Any]:
    """Return ChatOpenAI-ready config for the given agent's selected model."""

    selection = load_model_settings().get(agent) or DEFAULT_MODEL_SELECTIONS.get(agent)
    if not selection:
        raise ValueError(f"Unknown agent '{agent}' for model resolution.")

    provider_id = selection.get("provider") or ""
    provider_meta = LLM_PROVIDERS.get(provider_id)
    if not provider_meta:
        raise ValueError(f"Unsupported provider '{provider_id}'.")

    model_name = selection.get("model")
    valid_models = {m["id"] for m in provider_meta.get("models", [])}
    if not model_name or model_name not in valid_models:
        raise ValueError(f"モデル '{model_name}' はプロバイダー '{provider_id}' では利用できません。")

    env = _load_agent_env(agent)
    api_key_name = provider_meta.get("api_key_env") or "OPENAI_API_KEY"
    api_key = env.get(api_key_name) or env.get(api_key_name.lower())
    if not api_key:
        raise ValueError(f"{api_key_name} を {agent} の secrets.env に設定してください。")

    base_url_env = provider_meta.get("base_url_env")
    base_url = env.get(base_url_env, "").strip() if base_url_env else ""
    if not base_url:
        base_url = provider_meta.get("default_base_url")

    key_fingerprint = hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:12]
    return {
        "provider": provider_id,
        "model": model_name,
        "api_key": api_key,
        "base_url": base_url,
        "api_key_fingerprint": key_fingerprint,
    }


def validate_model_selection(payload: Dict[str, Any]) -> Dict[str, Dict[str, str]]:
    """Return the validated model selection without persisting it."""

    return _merge_model_selection(payload)
