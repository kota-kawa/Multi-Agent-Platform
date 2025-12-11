"""Browser Agent client helpers."""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Dict, Iterable, List, Tuple
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

_USE_BROWSER_AGENT_MCP = os.environ.get("BROWSER_AGENT_USE_MCP", "0").strip().lower() not in {"0", "false", "no", "off"}
_BROWSER_AGENT_MCP_TOOL = os.environ.get("BROWSER_AGENT_MCP_TOOL", "retry_with_browser_use_agent").strip()
_BROWSER_AGENT_MCP_ARG_KEY = os.environ.get("BROWSER_AGENT_MCP_ARG_KEY", "task").strip() or "task"
_USE_BROWSER_AGENT_HISTORY_MCP = (
    os.environ.get("BROWSER_AGENT_HISTORY_USE_MCP", "1").strip().lower() not in {"0", "false", "no", "off"}
)
_BROWSER_AGENT_MCP_HISTORY_TOOL = (
    os.environ.get("BROWSER_AGENT_MCP_HISTORY_TOOL", "analyze_conversation").strip() or "analyze_conversation"
)
_BROWSER_AGENT_MCP_HISTORY_ARG_KEY = (
    os.environ.get("BROWSER_AGENT_MCP_HISTORY_ARG_KEY", "conversation_history").strip() or "conversation_history"
)


def _running_inside_container() -> bool:
    """Best-effort detection to see if we're running inside a container."""

    if os.path.exists("/.dockerenv"):
        return True
    try:
        with open("/proc/1/cgroup", "rt", encoding="utf-8") as handle:
            content = handle.read()
        return any(marker in content for marker in ("docker", "containerd", "kubepods"))
    except OSError:
        return False


def _browser_agent_timeout(read_timeout: float | None) -> tuple[float, float | None]:
    return (BROWSER_AGENT_CONNECT_TIMEOUT, read_timeout)


def _expand_browser_agent_base(base: str) -> Iterable[str]:
    """Yield the original Browser Agent base along with hostname aliases."""

    yield base

    try:
        parsed = urlparse(base)
    except ValueError:
        return

    hostname = (parsed.hostname or "").lower()
    if not hostname:
        return

    auth = ""
    if parsed.username:
        auth = parsed.username
        if parsed.password:
            auth += f":{parsed.password}"
        auth += "@"
    port = parsed.port
    port_suffix = f":{port}" if port else ""

    if hostname in {"localhost", "127.0.0.1"}:
        alias_port = port or 5005
        alias_netloc = f"{auth}browser-agent"
        if alias_port:
            alias_netloc += f":{alias_port}"
        alias = urlunparse(parsed._replace(netloc=alias_netloc))
        if alias:
            yield alias

    replacements: list[str] = []
    if "_" in hostname:
        replacements.append(hostname.replace("_", "-"))

    if not replacements:
        return

    for replacement in replacements:
        if replacement == hostname:
            continue
        netloc = f"{auth}{replacement}{port_suffix}"
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
    if port is None and host in {"localhost", "127.0.0.1", "browser-agent"}:
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


def _run_with_event_loop(coro_factory):
    """Run an async coroutine with a timeout, even when already inside a loop."""

    try:
        return asyncio.run(asyncio.wait_for(coro_factory(), timeout=BROWSER_AGENT_TIMEOUT))
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(asyncio.wait_for(coro_factory(), timeout=BROWSER_AGENT_TIMEOUT))
        finally:
            loop.close()


def _select_browser_mcp_tool(tools: Iterable[Any]) -> Any | None:
    """Pick a Browser Agent MCP tool that accepts a free-form task string."""

    preferred = None
    for tool in tools or []:
        if getattr(tool, "name", None) == _BROWSER_AGENT_MCP_TOOL:
            preferred = tool
            break
    if preferred is not None:
        return preferred

    for tool in tools or []:
        schema = getattr(tool, "inputSchema", None)
        properties = schema.get("properties") if isinstance(schema, dict) else {}
        if properties and _BROWSER_AGENT_MCP_ARG_KEY in properties:
            return tool

    for tool in tools or []:
        schema = getattr(tool, "inputSchema", None)
        properties = schema.get("properties") if isinstance(schema, dict) else {}
        if not properties:
            continue
        for _, meta in properties.items():
            if isinstance(meta, dict) and meta.get("type") == "string":
                return tool

    return None


def _build_browser_mcp_args(tool: Any, prompt: str) -> Dict[str, Any]:
    """Construct arguments for the selected MCP tool using a string-friendly field."""

    schema = getattr(tool, "inputSchema", None)
    properties = schema.get("properties") if isinstance(schema, dict) else {}

    candidate_keys = [_BROWSER_AGENT_MCP_ARG_KEY] if _BROWSER_AGENT_MCP_ARG_KEY else []
    candidate_keys.extend(["instruction", "prompt", "task", "query", "text"])

    for key in candidate_keys:
        if properties and key in properties:
            return {key: prompt}

    if properties:
        for key, meta in properties.items():
            if isinstance(meta, dict) and meta.get("type") == "string":
                return {key: prompt}

    return {_BROWSER_AGENT_MCP_ARG_KEY or "task": prompt}


def _format_browser_mcp_result(result: Any) -> Dict[str, Any]:
    """Convert an MCP tool result into the Browser Agent payload shape."""

    contents = getattr(result, "content", None) or getattr(result, "contents", None) or []
    text_parts: list[str] = []
    for content in contents:
        text = getattr(content, "text", None)
        if isinstance(text, str) and text.strip():
            text_parts.append(text.strip())

    summary = "\n".join(text_parts).strip()
    if not summary:
        summary = "MCP 経由のブラウザエージェント応答が空でした。"

    return {"run_summary": summary, "messages": [{"role": "assistant", "content": summary}]}


def _call_browser_agent_chat_via_mcp(prompt: str) -> Tuple[Dict[str, Any] | None, List[str]]:
    """Best-effort MCP call to the Browser Agent, returning payload + errors."""

    errors: list[str] = []

    if not _USE_BROWSER_AGENT_MCP:
        return None, errors

    try:
        from mcp import ClientSession
        from mcp.client.sse import sse_client
    except Exception as exc:  # noqa: BLE001
        return None, [f"MCP クライアントの初期化に失敗しました: {exc}"]

    bases = _iter_browser_agent_bases()
    if not bases:
        return None, ["ブラウザエージェントの接続先が設定されていません。"]

    async def _call_tool(base_url: str):
        sse_url = _build_browser_agent_url(base_url, "/mcp/sse")
        async with sse_client(sse_url, timeout=BROWSER_AGENT_TIMEOUT) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                tools_result = await session.list_tools()
                tool = _select_browser_mcp_tool(getattr(tools_result, "tools", None))
                if tool is None:
                    raise BrowserAgentError("MCP 経由で利用できるブラウザエージェントツールが見つかりませんでした。")

                args = _build_browser_mcp_args(tool, prompt)
                call_result = await session.call_tool(getattr(tool, "name", ""), args)
                return _format_browser_mcp_result(call_result)

    for base in bases:
        try:
            result = _run_with_event_loop(lambda: _call_tool(base))
            return result, errors
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{base}: {exc}")
            continue

    return None, errors


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

    if deduped and _running_inside_container():
        loopback_hosts = {"localhost", "127.0.0.1"}
        container_first = [base for base in deduped if (urlparse(base).hostname or "").lower() not in loopback_hosts]
        loopback_rest = [base for base in deduped if (urlparse(base).hostname or "").lower() in loopback_hosts]
        if container_first:
            deduped = container_first + loopback_rest

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

    mcp_result: Dict[str, Any] | None = None
    mcp_errors: list[str] = []

    mcp_result, mcp_errors = _call_browser_agent_history_check_via_mcp(history)
    if mcp_result is not None:
        return mcp_result

    payload = {"history": list(history)}
    try:
        return _post_browser_agent(
            "/api/conversations/review",
            payload,
            timeout=_browser_agent_timeout(BROWSER_AGENT_TIMEOUT),
        )
    except BrowserAgentError as exc:
        if mcp_errors:
            message_lines = [str(exc), "MCP 経由での履歴共有も失敗しました:"]
            message_lines.extend(f"- {error}" for error in mcp_errors)
            raise BrowserAgentError("\n".join(message_lines), status_code=getattr(exc, "status_code", 502)) from exc
        raise


def _parse_browser_history_result_from_mcp(result: Any) -> Dict[str, Any]:
    """Extract a JSON payload from a Browser Agent MCP analyze_conversation result."""

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

    raise BrowserAgentError("ブラウザエージェント MCP analyze_conversation から有効な内容が返りませんでした。")


def _call_browser_agent_history_check_via_mcp(
    history: Iterable[Dict[str, str]],
) -> Tuple[Dict[str, Any] | None, List[str]]:
    """Best-effort MCP call to analyze recent history, with HTTP fallback support."""

    errors: list[str] = []

    if not _USE_BROWSER_AGENT_HISTORY_MCP:
        return None, errors

    try:
        from mcp import ClientSession
        from mcp.client.sse import sse_client
    except Exception as exc:  # noqa: BLE001
        return None, [f"MCP クライアントの初期化に失敗しました: {exc}"]

    bases = _iter_browser_agent_bases()
    if not bases:
        return None, ["ブラウザエージェントの接続先が設定されていません。"]

    history_payload = list(history)

    async def _call_tool(base_url: str):
        sse_url = _build_browser_agent_url(base_url, "/mcp/sse")
        async with sse_client(sse_url, timeout=BROWSER_AGENT_TIMEOUT) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                tools_result = await session.list_tools()
                tool_names = [getattr(tool, "name", "") for tool in getattr(tools_result, "tools", None) or []]
                if _BROWSER_AGENT_MCP_HISTORY_TOOL not in tool_names:
                    raise BrowserAgentError("MCP 経由で利用できる analyze_conversation ツールが見つかりませんでした。")

                result = await session.call_tool(
                    _BROWSER_AGENT_MCP_HISTORY_TOOL,
                    {_BROWSER_AGENT_MCP_HISTORY_ARG_KEY: history_payload},
                )
                return _parse_browser_history_result_from_mcp(result)

    for base in bases:
        try:
            result = _run_with_event_loop(lambda: _call_tool(base))
            return result, errors
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{base}: {exc}")
            continue

    return None, errors


def _call_browser_agent_chat(prompt: str) -> Dict[str, Any]:
    """Call the Browser Agent chat endpoint or MCP tool."""

    mcp_result: Dict[str, Any] | None = None
    mcp_errors: list[str] = []

    if _USE_BROWSER_AGENT_MCP:
        mcp_result, mcp_errors = _call_browser_agent_chat_via_mcp(prompt)
        if mcp_result is not None:
            return mcp_result

    try:
        return _post_browser_agent(
            "/api/chat",
            {"prompt": prompt, "new_task": True, "skip_conversation_review": True},
            timeout=_browser_agent_timeout(BROWSER_AGENT_CHAT_TIMEOUT),
        )
    except BrowserAgentError as exc:
        if mcp_errors:
            message_lines = [str(exc), "MCP 経由での呼び出しも失敗しました:"]
            message_lines.extend(f"- {error}" for error in mcp_errors)
            raise BrowserAgentError("\n".join(message_lines), status_code=getattr(exc, "status_code", 502)) from exc
        raise
