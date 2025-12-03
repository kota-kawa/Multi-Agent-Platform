"""Scheduler Agent client helpers and proxy utilities."""

from __future__ import annotations

import os
import logging
from typing import Any, Dict, Iterable, List

import requests
from flask import Response, jsonify, request

from .config import (
    DEFAULT_SCHEDULER_AGENT_BASES,
    SCHEDULER_AGENT_CONNECT_TIMEOUT,
    SCHEDULER_AGENT_TIMEOUT,
)
from .errors import SchedulerAgentError

_scheduler_agent_preferred_base: str | None = None


def _iter_scheduler_agent_bases() -> list[str]:
    """Return configured Scheduler Agent base URLs in priority order."""

    configured = os.environ.get("SCHEDULER_AGENT_BASE", "")
    candidates: list[str] = []
    if configured:
        candidates.extend(part.strip() for part in configured.split(","))
    candidates.extend(DEFAULT_SCHEDULER_AGENT_BASES)

    deduped: list[str] = []
    seen: set[str] = set()
    for base in candidates:
        if not base:
            continue
        normalized = base.rstrip("/")
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    if _scheduler_agent_preferred_base and _scheduler_agent_preferred_base in deduped:
        preferred = _scheduler_agent_preferred_base
        return [preferred, *[base for base in deduped if base != preferred]]
    return deduped


def _build_scheduler_agent_url(base: str, path: str) -> str:
    """Build an absolute URL to the upstream Scheduler Agent."""

    if not path.startswith("/"):
        path = f"/{path}"
    return f"{base}{path}"


def _proxy_scheduler_agent_request(path: str) -> Response:
    """Proxy the incoming request to the configured Scheduler Agent."""

    global _scheduler_agent_preferred_base

    bases = _iter_scheduler_agent_bases()
    if not bases:
        return jsonify({"error": "Scheduler Agent の接続先が設定されていません。"}), 500

    if request.is_json:
        json_payload = request.get_json(silent=True)
        body_payload = None
    else:
        json_payload = None
        body_payload = request.get_data(cache=False) if request.method in {"POST", "PUT", "PATCH", "DELETE"} else None

    forward_headers: Dict[str, str] = {}
    # Forward auth and content-related headers plus a prefix hint so the Scheduler Agent can build correct URLs.
    forward_headers["X-Forwarded-Prefix"] = "/scheduler_agent"
    for header, value in request.headers.items():
        lowered = header.lower()
        if lowered in {"content-type", "authorization", "accept", "cookie"} or lowered.startswith("x-"):
            forward_headers[header] = value

    connection_errors: list[str] = []
    response = None
    for base in bases:
        url = _build_scheduler_agent_url(base, path)
        try:
            response = requests.request(
                request.method,
                url,
                params=request.args,
                json=json_payload,
                data=body_payload if json_payload is None else None,
                headers=forward_headers,
                timeout=(SCHEDULER_AGENT_CONNECT_TIMEOUT, SCHEDULER_AGENT_TIMEOUT),
            )
        except requests.exceptions.RequestException as exc:  # pragma: no cover - network failure
            connection_errors.append(f"{url}: {exc}")
            continue
        else:
            _scheduler_agent_preferred_base = base
            break

    if response is None:
        message_lines = ["Scheduler Agent への接続に失敗しました。"]
        if connection_errors:
            message_lines.append("試行した URL:")
            message_lines.extend(f"- {error}" for error in connection_errors)
        return jsonify({"error": "\n".join(message_lines)}), 502

    proxy_response = Response(response.content, status=response.status_code)
    excluded_headers = {"content-encoding", "transfer-encoding", "connection", "content-length"}
    for header, value in response.headers.items():
        if header.lower() in excluded_headers:
            continue
        proxy_response.headers[header] = value
    return proxy_response


def _get_first_scheduler_agent_base() -> str | None:
    """Return the first preferred Scheduler Agent base URL."""
    bases = _iter_scheduler_agent_bases()
    return bases[0] if bases else None


def _fetch_scheduler_model_selection() -> Dict[str, str] | None:
    """Fetch the Scheduler Agent's current model selection for cross-app sync."""

    bases = _iter_scheduler_agent_bases()
    if not bases:
        return None

    for base in bases:
        url = _build_scheduler_agent_url(base, "/api/models")
        try:
            response = requests.get(
                url,
                timeout=(SCHEDULER_AGENT_CONNECT_TIMEOUT, SCHEDULER_AGENT_TIMEOUT),
            )
        except requests.exceptions.RequestException as exc:  # pragma: no cover - network failure
            logging.info("Scheduler model sync attempt to %s skipped (%s)", url, exc)
            continue

        if not response.ok:
            logging.info(
                "Scheduler model sync attempt to %s failed: %s %s", url, response.status_code, response.text
            )
            continue

        try:
            payload = response.json()
        except ValueError:
            logging.info("Scheduler model sync attempt to %s returned invalid JSON", url)
            continue

        current = payload.get("current") if isinstance(payload, dict) else None
        if not isinstance(current, dict):
            logging.info("Scheduler model sync attempt to %s missing current selection", url)
            continue

        provider = str(current.get("provider") or "").strip()
        model = str(current.get("model") or "").strip()
        base_url = str(current.get("base_url") or "").strip()
        if not provider or not model:
            logging.info("Scheduler model sync attempt to %s missing provider/model", url)
            continue

        return {"provider": provider, "model": model, "base_url": base_url}

    return None


def _call_scheduler_agent(path: str, method: str = "GET", params: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Make a direct call to the Scheduler Agent API."""
    base = _get_first_scheduler_agent_base()
    if not base:
        raise ConnectionError("Scheduler Agent base URL is not configured.")

    url = _build_scheduler_agent_url(base, path)
    
    headers = {"X-Platform-Propagation": "1"} # Propagate a header if needed for agent logic

    try:
        response = requests.request(
            method,
            url,
            params=params,
            headers=headers,
            timeout=(SCHEDULER_AGENT_CONNECT_TIMEOUT, SCHEDULER_AGENT_TIMEOUT),
        )
        response.raise_for_status() # Raise an exception for HTTP errors
        try:
            return response.json()
        except ValueError as exc:
            raise ConnectionError(f"Scheduler Agent at {url} returned invalid JSON") from exc
    except requests.exceptions.RequestException as exc:
        raise ConnectionError(f"Failed to call Scheduler Agent API at {url}: {exc}") from exc


def _post_scheduler_agent(path: str, payload: Dict[str, Any], *, method: str = "POST") -> Dict[str, Any]:
    """Send a JSON request to the Scheduler Agent and parse the response or raise a helpful error."""

    base = _get_first_scheduler_agent_base()
    if not base:
        raise SchedulerAgentError("Scheduler Agent base URL is not configured.")

    url = _build_scheduler_agent_url(base, path)
    headers = {"Content-Type": "application/json", "X-Platform-Propagation": "1"}

    try:
        response = requests.request(
            method,
            url,
            json=payload,
            headers=headers,
            timeout=(SCHEDULER_AGENT_CONNECT_TIMEOUT, SCHEDULER_AGENT_TIMEOUT),
        )
    except requests.exceptions.RequestException as exc:
        raise SchedulerAgentError(f"Scheduler Agent への接続に失敗しました: {exc}") from exc

    if not response.ok:
        try:
            data = response.json()
            detail = data.get("error") if isinstance(data, dict) else None
        except ValueError:
            detail = None
        message = detail or f"Scheduler Agent からエラー応答を受け取りました: {response.status_code} {response.reason}"
        raise SchedulerAgentError(message, status_code=response.status_code)

    try:
        return response.json()
    except ValueError as exc:  # pragma: no cover - defensive
        raise SchedulerAgentError("Scheduler Agent からの応答を JSON として解析できませんでした。") from exc


def _call_scheduler_agent_chat(command: str) -> Dict[str, Any]:
    """Send a single-shot chat command to the Scheduler Agent."""

    return _post_scheduler_agent(
        "/api/chat",
        {"messages": [{"role": "user", "content": command}]},
    )


def _call_scheduler_agent_conversation_review(
    conversation_history: List[Dict[str, str]],
) -> Dict[str, Any]:
    """Send recent conversation turns to the Scheduler Agent for analysis."""

    return _post_scheduler_agent(
        "/api/conversations/review",
        {"history": conversation_history},
    )


def _fetch_calendar_data(year: int, month: int) -> Dict[str, Any]:
    """Fetch calendar data from Scheduler Agent."""
    return _call_scheduler_agent("/api/calendar", params={"year": year, "month": month})

def _fetch_day_view_data(date_str: str) -> Dict[str, Any]:
    """Fetch day view data from Scheduler Agent."""
    return _call_scheduler_agent(f"/api/day/{date_str}")

def _fetch_routines_data() -> Dict[str, Any]:
    """Fetch routines data from Scheduler Agent."""
    return _call_scheduler_agent("/api/routines")


def _submit_day_form(date_str: str, form_data: Any) -> None:
    """Submit a day form (tasks/logs) to the Scheduler Agent without leaking its URL."""

    base = _get_first_scheduler_agent_base()
    if not base:
        raise ConnectionError("Scheduler Agent base URL is not configured.")

    url = _build_scheduler_agent_url(base, f"/day/{date_str}")

    if hasattr(form_data, "to_dict"):
        # Preserve multi-value fields if any
        payload: Dict[str, Iterable[str] | str] = form_data.to_dict(flat=False)  # type: ignore[assignment]
    else:
        payload = dict(form_data or {})

    try:
        response = requests.post(
            url,
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=(SCHEDULER_AGENT_CONNECT_TIMEOUT, SCHEDULER_AGENT_TIMEOUT),
            allow_redirects=False,
        )
    except requests.exceptions.RequestException as exc:  # pragma: no cover - network failure
        raise ConnectionError(f"Failed to submit day form to Scheduler Agent at {url}: {exc}") from exc

    if response.status_code in {301, 302, 303, 307, 308}:
        # Scheduler Agent responds with a redirect back to its own page; ignore and let the UI refresh locally.
        return

    if not response.ok:
        raise ConnectionError(
            f"Scheduler Agent form submission failed: {response.status_code} {response.reason}"
        )
