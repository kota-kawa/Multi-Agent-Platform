"""Browser Agent client helpers."""

from __future__ import annotations

import os
from typing import Any, Dict, Iterable
from urllib.parse import urlparse, urlunparse

import requests
from flask import g, has_request_context

from .config import (
    BROWSER_AGENT_CHAT_TIMEOUT,
    BROWSER_AGENT_CONNECT_TIMEOUT,
    BROWSER_AGENT_TIMEOUT,
    DEFAULT_BROWSER_AGENT_BASES,
)
from .errors import BrowserAgentError


def _browser_agent_timeout(read_timeout: float | None) -> tuple[float, float | None]:
    return (BROWSER_AGENT_CONNECT_TIMEOUT, read_timeout)


def _expand_browser_agent_base(base: str) -> Iterable[str]:
    """Yield the original Browser Agent base along with hostname aliases."""

    yield base

    try:
        parsed = urlparse(base)
    except ValueError:
        return

    hostname = parsed.hostname or ""
    if not hostname:
        return

    replacements: list[str] = []
    if "_" in hostname:
        replacements.append(hostname.replace("_", "-"))

    if not replacements:
        return

    auth = ""
    if parsed.username:
        auth = parsed.username
        if parsed.password:
            auth += f":{parsed.password}"
        auth += "@"
    port = f":{parsed.port}" if parsed.port else ""

    for replacement in replacements:
        if replacement == hostname:
            continue
        netloc = f"{auth}{replacement}{port}"
        alias = urlunparse(parsed._replace(netloc=netloc))
        if alias:
            yield alias


def _canonicalise_browser_agent_base(value: str) -> str:
    """Normalise Browser Agent base URLs and remap localhost aliases."""

    trimmed = value.strip()
    if not trimmed:
        return ""

    candidate = trimmed
    if "://" not in candidate:
        candidate = f"http://{candidate}"

    try:
        parsed = urlparse(candidate)
    except ValueError:
        return ""

    scheme = parsed.scheme or "http"
    host = (parsed.hostname or "").strip().lower()
    if not host:
        return ""

    username = parsed.username or ""
    password = parsed.password or ""
    auth = ""
    if username:
        auth = username
        if password:
            auth += f":{password}"
        auth += "@"

    port = parsed.port
    if host in {"localhost", "127.0.0.1"}:
        host = "browser-agent"
        if port is None:
            port = 5005

    netloc = f"{auth}{host}"
    if port is not None:
        netloc += f":{port}"

    path = parsed.path if parsed.path not in ("", "/") else ""
    canonical = urlunparse((scheme, netloc, path, "", "", ""))
    return canonical.rstrip("/")


def _normalise_browser_base_values(values: Any) -> list[str]:
    """Return a flat list of browser agent base URL strings from client payloads."""

    cleaned: list[str] = []

    def _consume(value: Any) -> None:
        if value is None:
            return
        if isinstance(value, str):
            parts = [part.strip() for part in value.split(",")]
            for part in parts:
                if not part:
                    continue
                canonical = _canonicalise_browser_agent_base(part)
                if canonical:
                    cleaned.append(canonical)
            return
        if isinstance(value, Iterable):
            for item in value:
                _consume(item)

    _consume(values)
    return cleaned


def _iter_browser_agent_bases() -> list[str]:
    """Return configured Browser Agent base URLs in priority order."""

    configured = os.environ.get("BROWSER_AGENT_API_BASE", "")
    candidates: list[str] = []
    if has_request_context():
        overrides = getattr(g, "browser_agent_bases", None)
        if overrides:
            if isinstance(overrides, list):
                for value in overrides:
                    if not isinstance(value, str):
                        continue
                    canonical = _canonicalise_browser_agent_base(value)
                    if canonical:
                        candidates.append(canonical)
            else:  # Defensive fallback
                candidates.extend(_normalise_browser_base_values(overrides))
    if configured:
        for part in configured.split(","):
            canonical = _canonicalise_browser_agent_base(part)
            if canonical:
                candidates.append(canonical)
    for default_base in DEFAULT_BROWSER_AGENT_BASES:
        canonical = _canonicalise_browser_agent_base(default_base)
        if canonical:
            candidates.append(canonical)

    deduped: list[str] = []
    seen: set[str] = set()
    for base in candidates:
        if not base:
            continue
        normalized = base.rstrip("/")
        if normalized.startswith("/"):
            # Avoid proxying to self
            continue
        for expanded in _expand_browser_agent_base(normalized):
            candidate = expanded.rstrip("/")
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            deduped.append(candidate)
    return deduped


def _build_browser_agent_url(base: str, path: str) -> str:
    """Build an absolute URL to the Browser Agent API."""

    if not path.startswith("/"):
        path = f"/{path}"
    return f"{base}{path}"


def _extract_browser_error_message(response: requests.Response, default_message: str) -> str:
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        if "detail" in payload:
            detail = payload["detail"]
            if isinstance(detail, list):
                parts = []
                for part in detail:
                    if isinstance(part, dict):
                        msg = part.get("msg")
                        if isinstance(msg, str):
                            parts.append(msg)
                if parts:
                    return "; ".join(parts)
            elif isinstance(detail, str):
                return detail
        if "error" in payload:
            error_message = payload["error"]
            if isinstance(error_message, str):
                return error_message
    if response.text:
        return response.text
    if response.reason:
        return f"{response.status_code} {response.reason}"
    return default_message


def _post_browser_agent(path: str, payload: Dict[str, Any], *, timeout: float | tuple[float, float | None]):
    """Send a JSON payload to the Browser Agent and return JSON response."""

    connection_errors: list[str] = []
    last_exception: Exception | None = None
    response: requests.Response | None = None

    for base in _iter_browser_agent_bases():
        url = _build_browser_agent_url(base, path)
        try:
            response = requests.post(url, json=payload, timeout=timeout)
        except requests.exceptions.RequestException as exc:  # pragma: no cover - network failure
            connection_errors.append(f"{url}: {exc}")
            last_exception = exc
            continue
        else:
            break

    if response is None:
        message_lines = ["ブラウザエージェント API への接続に失敗しました。"]
        if connection_errors:
            message_lines.append("試行した URL:")
            message_lines.extend(f"- {error}" for error in connection_errors)
        raise BrowserAgentError("\n".join(message_lines)) from last_exception

    try:
        data = response.json()
    except ValueError:
        data = None

    if not response.ok:
        message = _extract_browser_error_message(
            response,
            "ブラウザエージェントでエラーが発生しました。",
        )
        raise BrowserAgentError(message, status_code=response.status_code)

    if not isinstance(data, dict):
        raise BrowserAgentError("ブラウザエージェントから不正なレスポンス形式が返されました。")

    return data


def _call_browser_agent_history_check(history: Iterable[Dict[str, str]]) -> Dict[str, Any]:
    """Call the Browser Agent history check endpoint."""

    payload = {"messages": list(history)}
    return _post_browser_agent(
        "/api/history/check",
        payload,
        timeout=_browser_agent_timeout(BROWSER_AGENT_TIMEOUT),
    )


def _call_browser_agent_chat(prompt: str) -> Dict[str, Any]:
    """Call the Browser Agent chat endpoint."""

    return _post_browser_agent(
        "/api/chat",
        {"prompt": prompt, "new_task": True},
        timeout=_browser_agent_timeout(BROWSER_AGENT_CHAT_TIMEOUT),
    )
