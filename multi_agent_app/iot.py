"""IoT Agent client helpers."""

from __future__ import annotations

import os
from typing import Any, Dict, List

import requests
from flask import Response, jsonify, request

from .config import DEFAULT_IOT_AGENT_BASES, IOT_AGENT_TIMEOUT
from .errors import IotAgentError


def _iter_iot_agent_bases() -> list[str]:
    """Return configured IoT Agent base URLs in priority order."""

    configured = os.environ.get("IOT_AGENT_API_BASE", "")
    candidates: list[str] = []
    if configured:
        candidates.extend(part.strip() for part in configured.split(","))
    candidates.extend(DEFAULT_IOT_AGENT_BASES)

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
    return deduped


def _build_iot_agent_url(base: str, path: str) -> str:
    """Build an absolute URL to the upstream IoT Agent API."""

    if not path.startswith("/"):
        path = f"/{path}"
    return f"{base}{path}"


def _post_iot_agent(path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Send a JSON payload to the IoT Agent and return the JSON response."""

    bases = _iter_iot_agent_bases()
    if not bases:
        raise IotAgentError("IoT Agent API の接続先が設定されていません。", status_code=500)

    connection_errors: list[str] = []
    last_exception: Exception | None = None
    response = None
    for base in bases:
        url = _build_iot_agent_url(base, path)
        try:
            response = requests.post(url, json=payload, timeout=IOT_AGENT_TIMEOUT)
        except requests.exceptions.RequestException as exc:  # pragma: no cover - network failure
            connection_errors.append(f"{url}: {exc}")
            last_exception = exc
            continue
        else:
            break

    if response is None:
        message_lines = ["IoT Agent API への接続に失敗しました。"]
        if connection_errors:
            message_lines.append("試行した URL:")
            message_lines.extend(f"- {error}" for error in connection_errors)
        raise IotAgentError("\n".join(message_lines)) from last_exception

    try:
        data = response.json()
    except ValueError:
        data = None

    if not response.ok:
        message = data.get("error") if isinstance(data, dict) else None
        if not message:
            message = response.text or f"{response.status_code} {response.reason}"
        raise IotAgentError(message, status_code=response.status_code)

    if not isinstance(data, dict):
        raise IotAgentError("IoT Agent API から不正なレスポンス形式が返されました。", status_code=502)

    return data


def _call_iot_agent_command(command: str) -> Dict[str, Any]:
    """Send a chat-style command to the IoT Agent and return the JSON payload."""

    return _post_iot_agent(
        "/api/chat",
        {"messages": [{"role": "user", "content": command}]},
    )


def _call_iot_agent_chat(command: str) -> Dict[str, Any]:
    """Backward-compatible alias for `_call_iot_agent_command`."""

    return _call_iot_agent_command(command)


def _call_iot_agent_conversation_review(
    conversation_history: List[Dict[str, str]]
) -> Dict[str, Any]:
    """Send conversation history to the IoT Agent review endpoint."""

    return _post_iot_agent(
        "/api/conversations/review",
        {"history": conversation_history},
    )


def _proxy_iot_agent_request(path: str) -> Response:
    """Proxy the incoming request to the configured IoT Agent API."""

    bases = _iter_iot_agent_bases()
    if not bases:
        return jsonify({"error": "IoT Agent API の接続先が設定されていません。"}), 500

    if request.is_json:
        json_payload = request.get_json(silent=True)
        body_payload = None
    else:
        json_payload = None
        body_payload = request.get_data(cache=False) if request.method in {"POST", "PUT", "PATCH", "DELETE"} else None

    forward_headers: Dict[str, str] = {}
    for header, value in request.headers.items():
        lowered = header.lower()
        if lowered in {"content-type", "authorization", "accept", "cookie"} or lowered.startswith("x-"):
            forward_headers[header] = value

    connection_errors: list[str] = []
    response = None
    for base in bases:
        url = _build_iot_agent_url(base, path)
        try:
            response = requests.request(
                request.method,
                url,
                params=request.args,
                json=json_payload,
                data=body_payload if json_payload is None else None,
                headers=forward_headers,
                timeout=IOT_AGENT_TIMEOUT,
            )
        except requests.exceptions.RequestException as exc:  # pragma: no cover - network failure
            connection_errors.append(f"{url}: {exc}")
            continue
        else:
            break

    if response is None:
        message_lines = ["IoT Agent API への接続に失敗しました。"]
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
