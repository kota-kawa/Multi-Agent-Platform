"""Scheduler Agent client helpers and proxy utilities."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any, Dict, Iterable, List

import requests
from requests.adapters import HTTPAdapter
from flask import Response, jsonify, request

from .config import (
    DEFAULT_SCHEDULER_AGENT_BASES,
    SCHEDULER_AGENT_CONNECT_TIMEOUT,
    SCHEDULER_AGENT_TIMEOUT,
    SCHEDULER_MODEL_SYNC_CONNECT_TIMEOUT,
    SCHEDULER_MODEL_SYNC_TIMEOUT,
)
from .errors import SchedulerAgentError

_scheduler_agent_preferred_base: str | None = None
_host_failure_cache: Dict[str, float] = {}
_HOST_FAILURE_COOLDOWN = 60.0  # seconds

def _is_host_down(base_url: str) -> bool:
    """Check if the host is marked as down in the cache."""
    last_failure = _host_failure_cache.get(base_url)
    if last_failure is None:
        return False
    if time.time() - last_failure < _HOST_FAILURE_COOLDOWN:
        return True
    del _host_failure_cache[base_url]
    return False

def _mark_host_down(base_url: str):
    """Mark the host as down."""
    _host_failure_cache[base_url] = time.time()

def _mark_host_up(base_url: str):
    """Mark the host as up (remove from failure cache)."""
    if base_url in _host_failure_cache:
        del _host_failure_cache[base_url]

_USE_SCHEDULER_AGENT_MCP = os.environ.get("SCHEDULER_AGENT_USE_MCP", "1").strip().lower() not in {"0", "false", "no", "off"}
_SCHEDULER_AGENT_MCP_TOOL = os.environ.get("SCHEDULER_AGENT_MCP_TOOL", "manage_schedule").strip() or "manage_schedule"
_SCHEDULER_AGENT_MCP_CONVERSATION_TOOL = (
    os.environ.get("SCHEDULER_AGENT_MCP_CONVERSATION_TOOL", "analyze_conversation").strip() or "analyze_conversation"
)


def _get_no_retry_session() -> requests.Session:
    """Return a requests Session with max_retries=0 to fail fast."""
    session = requests.Session()
    adapter = HTTPAdapter(max_retries=0)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session



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
        return jsonify({
            "status": "unavailable",
            "error": "Scheduler Agent の接続先が設定されていません。",
        })

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

    # Filter out known down hosts, but if all are down, try them all to allow recovery.
    candidates = [b for b in bases if not _is_host_down(b)]
    if not candidates:
        candidates = bases

    with _get_no_retry_session() as session:
        for base in candidates:
            url = _build_scheduler_agent_url(base, path)
            try:
                response = session.request(
                    request.method,
                    url,
                    params=request.args,
                    json=json_payload,
                    data=body_payload if json_payload is None else None,
                    headers=forward_headers,
                    timeout=(SCHEDULER_AGENT_CONNECT_TIMEOUT, SCHEDULER_AGENT_TIMEOUT),
                )
            except requests.exceptions.RequestException as exc:  # pragma: no cover - network failure
                _mark_host_down(base)
                connection_errors.append(f"{url}: {exc}")
                continue
            else:
                _mark_host_up(base)
                _scheduler_agent_preferred_base = base
                break

    if response is None:
        message_lines = ["Scheduler Agent への接続に失敗しました。"]
        if connection_errors:
            message_lines.append("試行した URL:")
            message_lines.extend(f"- {error}" for error in connection_errors)
        return jsonify({
            "status": "unavailable",
            "error": "\n".join(message_lines),
        })

    proxy_response = Response(response.content, status=response.status_code)
    excluded_headers = {"content-encoding", "transfer-encoding", "connection", "content-length"}
    for header, value in response.headers.items():
        if header.lower() in excluded_headers:
            continue
        proxy_response.headers[header] = value
    return proxy_response


def _get_first_scheduler_agent_base() -> str | None:
    """Return the first preferred Scheduler Agent base URL, prioritizing available ones."""
    bases = _iter_scheduler_agent_bases()
    if not bases:
        return None

    # Try to find a base that is not marked as down
    for base in bases:
        if not _is_host_down(base):
            return base

    # If all are down, return the first one as a fallback
    return bases[0]


def _fetch_scheduler_model_selection() -> Dict[str, str] | None:
    """Fetch the Scheduler Agent's current model selection for cross-app sync."""

    bases = _iter_scheduler_agent_bases()
    if not bases:
        return None

    # Filter out known down hosts, but if all are down, try them all to allow recovery.
    candidates = [b for b in bases if not _is_host_down(b)]
    if not candidates:
        candidates = bases

    with _get_no_retry_session() as session:
        for base in candidates:
            url = _build_scheduler_agent_url(base, "/api/models")
            try:
                response = session.get(
                    url,
                    timeout=(SCHEDULER_MODEL_SYNC_CONNECT_TIMEOUT, SCHEDULER_MODEL_SYNC_TIMEOUT),
                )
            except requests.exceptions.RequestException as exc:  # pragma: no cover - network failure
                _mark_host_down(base)
                logging.debug("Scheduler model sync attempt to %s skipped (%s)", url, exc)
                continue
            else:
                _mark_host_up(base)

            if not response.ok:
                logging.debug(
                    "Scheduler model sync attempt to %s failed: %s %s", url, response.status_code, response.text
                )
                continue

            try:
                payload = response.json()
            except ValueError:
                logging.debug("Scheduler model sync attempt to %s returned invalid JSON", url)
                continue

            current = payload.get("current") if isinstance(payload, dict) else None
            if not isinstance(current, dict):
                logging.debug("Scheduler model sync attempt to %s missing current selection", url)
                continue

            provider = str(current.get("provider") or "").strip()
            model = str(current.get("model") or "").strip()
            base_url = str(current.get("base_url") or "").strip()
            if not provider or not model:
                logging.debug("Scheduler model sync attempt to %s missing provider/model", url)
                continue

            return {"provider": provider, "model": model, "base_url": base_url}

    return None


def _call_scheduler_agent(path: str, method: str = "GET", params: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Make a direct call to the Scheduler Agent API."""
    base = _get_first_scheduler_agent_base()
    if not base:
        raise ConnectionError("Scheduler Agent base URL is not configured.")

    url = _build_scheduler_agent_url(base, path)
    
    headers = {"X-Platform-Propagation": "1"}  # Propagate a header if needed for agent logic

    try:
        with _get_no_retry_session() as session:
            response = session.request(
                method,
                url,
                params=params,
                headers=headers,
                timeout=(SCHEDULER_AGENT_CONNECT_TIMEOUT, SCHEDULER_AGENT_TIMEOUT),
            )
        response.raise_for_status()  # Raise an exception for HTTP errors
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
        with _get_no_retry_session() as session:
            response = session.request(
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


def _run_scheduler_mcp_with_timeout(coro_factory):
    """Run an async MCP coroutine with a timeout, even when already inside a loop."""

    try:
        return asyncio.run(asyncio.wait_for(coro_factory(), timeout=SCHEDULER_AGENT_TIMEOUT))
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(asyncio.wait_for(coro_factory(), timeout=SCHEDULER_AGENT_TIMEOUT))
        finally:
            loop.close()


def _format_scheduler_mcp_result(result: Any) -> Dict[str, Any]:
    """Convert an MCP tool result into the Scheduler Agent payload shape."""

    contents = getattr(result, "content", None) or getattr(result, "contents", None) or []
    text_parts: list[str] = []
    for item in contents:
        text = getattr(item, "text", None)
        if isinstance(text, str) and text.strip():
            text_parts.append(text.strip())

    reply = "\n".join(text_parts).strip()
    if not reply:
        reply = "Scheduler エージェントからの応答が空でした。"

    return {"reply": reply}


def _call_scheduler_agent_chat_via_mcp(command: str) -> tuple[Dict[str, Any] | None, list[str]]:
    """Best-effort MCP call to the Scheduler Agent."""

    errors: list[str] = []

    if not _USE_SCHEDULER_AGENT_MCP:
        return None, errors

    try:
        from mcp import ClientSession
        from mcp.client.sse import sse_client
    except Exception as exc:  # noqa: BLE001
        return None, [f"MCP クライアントの初期化に失敗しました: {exc}"]

    base = _get_first_scheduler_agent_base()
    if not base:
        return None, ["Scheduler Agent base URL is not configured."]

    async def _call_tool():
        sse_url = _build_scheduler_agent_url(base, "/mcp/sse")
        async with sse_client(sse_url, timeout=SCHEDULER_AGENT_TIMEOUT) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()

                tools_result = await session.list_tools()
                tool_names = [getattr(tool, "name", "") for tool in getattr(tools_result, "tools", None) or []]
                if _SCHEDULER_AGENT_MCP_TOOL not in tool_names:
                    raise SchedulerAgentError(
                        f"MCP ツール {_SCHEDULER_AGENT_MCP_TOOL} が Scheduler Agent で見つかりませんでした。"
                    )

                result = await session.call_tool(_SCHEDULER_AGENT_MCP_TOOL, {"instruction": command})
                return _format_scheduler_mcp_result(result)

    try:
        return _run_scheduler_mcp_with_timeout(_call_tool), errors
    except Exception as exc:  # noqa: BLE001
        errors.append(f"{base}: {exc}")
        return None, errors


def _call_scheduler_agent_chat_via_http(command: str) -> Dict[str, Any]:
    """Fallback HTTP call to Scheduler Agent chat endpoint."""

    payload = {"messages": [{"role": "user", "content": command}]}
    return _post_scheduler_agent("/api/chat", payload)


def _call_scheduler_agent_chat(command: str) -> Dict[str, Any]:
    """Send a single-shot chat command to the Scheduler Agent via MCP with HTTP fallback."""

    mcp_result: Dict[str, Any] | None = None
    mcp_errors: list[str] = []

    mcp_result, mcp_errors = _call_scheduler_agent_chat_via_mcp(command)
    if mcp_result is not None:
        return mcp_result

    try:
        return _call_scheduler_agent_chat_via_http(command)
    except SchedulerAgentError as exc:
        if mcp_errors:
            message_lines = [str(exc), "MCP 経由での呼び出しも失敗しました:"]
            message_lines.extend(f"- {error}" for error in mcp_errors)
            raise SchedulerAgentError(
                "\n".join(message_lines),
                status_code=getattr(exc, "status_code", 502),
            ) from exc
        raise


def _call_scheduler_agent_conversation_review(
    conversation_history: List[Dict[str, str]],
) -> Dict[str, Any]:
    """Send recent conversation turns to the Scheduler Agent for analysis."""

    mcp_result: Dict[str, Any] | None = None
    mcp_errors: list[str] = []

    mcp_result, mcp_errors = _call_scheduler_agent_conversation_review_via_mcp(conversation_history)
    if mcp_result is not None:
        return mcp_result

    try:
        return _post_scheduler_agent(
            "/api/conversations/review",
            {"history": conversation_history},
        )
    except SchedulerAgentError as exc:
        if mcp_errors:
            message_lines = [str(exc), "MCP 経由での会話同期も失敗しました:"]
            message_lines.extend(f"- {error}" for error in mcp_errors)
            raise SchedulerAgentError(
                "\n".join(message_lines),
                status_code=getattr(exc, "status_code", 502),
            ) from exc
        raise


def _parse_scheduler_history_mcp_result(result: Any) -> Dict[str, Any]:
    """Extract JSON payload from Scheduler Agent MCP analyze_conversation."""

    contents = getattr(result, "content", None) or getattr(result, "contents", None) or []
    for content in contents:
        text = getattr(content, "text", None)
        if not isinstance(text, str) or not text.strip():
            continue
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed

    raise SchedulerAgentError("Scheduler Agent MCP analyze_conversation が空の応答を返しました。")


def _call_scheduler_agent_conversation_review_via_mcp(
    conversation_history: List[Dict[str, str]],
) -> tuple[Dict[str, Any] | None, list[str]]:
    """Best-effort MCP call for conversation review."""

    errors: list[str] = []

    if not _USE_SCHEDULER_AGENT_MCP:
        return None, errors

    try:
        from mcp import ClientSession
        from mcp.client.sse import sse_client
    except Exception as exc:  # noqa: BLE001
        return None, [f"MCP クライアントの初期化に失敗しました: {exc}"]

    bases = _iter_scheduler_agent_bases()
    if not bases:
        return None, ["Scheduler Agent の接続先が設定されていません。"]

    async def _call_tool(base: str):
        sse_url = _build_scheduler_agent_url(base, "/mcp/sse")
        async with sse_client(sse_url, timeout=SCHEDULER_AGENT_TIMEOUT) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools_result = await session.list_tools()
                tool_names = [getattr(tool, "name", "") for tool in getattr(tools_result, "tools", None) or []]
                if _SCHEDULER_AGENT_MCP_CONVERSATION_TOOL not in tool_names:
                    raise SchedulerAgentError("MCP ツール analyze_conversation が Scheduler Agent で見つかりませんでした。")

                result = await session.call_tool(
                    _SCHEDULER_AGENT_MCP_CONVERSATION_TOOL,
                    {"conversation_history": conversation_history},
                )
                return _parse_scheduler_history_mcp_result(result)

    for base in bases:
        try:
            result = _run_scheduler_mcp_with_timeout(lambda: _call_tool(base))
            return result, errors
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{base}: {exc}")
            continue

    return None, errors


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
        with _get_no_retry_session() as session:
            response = session.post(
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
