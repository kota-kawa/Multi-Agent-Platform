"""Life-Style (Lifestyle) client helpers."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Dict, List

import httpx
from mcp import ClientSession
from mcp.client.sse import sse_client

from .config import DEFAULT_LIFESTYLE_BASES, LIFESTYLE_TIMEOUT
from .errors import LifestyleAPIError

_USE_LIFESTYLE_MCP = os.environ.get("LIFESTYLE_USE_MCP", "1").strip().lower() not in {"0", "false", "no", "off"}


def _iter_lifestyle_bases() -> list[str]:
    """Return the configured Life-Style base URLs in priority order."""

    configured = os.environ.get("LIFESTYLE_API_BASE", "")
    candidates: list[str] = []
    if configured:
        candidates.extend(part.strip() for part in configured.split(","))
    candidates.extend(DEFAULT_LIFESTYLE_BASES)

    deduped: list[str] = []
    seen: set[str] = set()
    for base in candidates:
        if not base:
            continue
        normalized = base.rstrip("/")
        if normalized.startswith("/"):
            # Avoid proxying to self
            continue
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


def _build_lifestyle_url(base: str, path: str) -> str:
    """Build an absolute URL to the upstream Life-Style API."""

    if not path.startswith("/"):
        path = f"/{path}"
    return f"{base}{path}"


def _first_text_content(contents: list[Any]) -> str | None:
    """Extract the first text payload from MCP content blocks."""

    for content in contents:
        text = getattr(content, "text", None)
        if isinstance(text, str) and text.strip():
            return text
    return None


async def _call_lifestyle_tool_via_mcp(
    bases: list[str], tool_name: str, arguments: Dict[str, Any]
) -> tuple[Dict[str, Any] | None, list[str]]:
    """Best-effort MCP call for Life-Style tools, with detailed error capture."""

    errors: list[str] = []

    async def _call_tool(base_url: str):
        sse_url = _build_lifestyle_url(base_url, "/mcp/sse")
        async with sse_client(sse_url, timeout=LIFESTYLE_TIMEOUT) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments)
                text = _first_text_content(result.content)
                if not text:
                    raise LifestyleAPIError("Life-Style MCP call returned empty content.", status_code=502)
                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError as exc:  # pragma: no cover - defensive
                    raise LifestyleAPIError("Life-Style MCP call returned non-JSON response.", status_code=502) from exc
                if not isinstance(parsed, dict):
                    raise LifestyleAPIError("Life-Style MCP call returned unexpected format.", status_code=502)
                return parsed

    for base in bases:
        try:
            result = await asyncio.wait_for(_call_tool(base), timeout=LIFESTYLE_TIMEOUT)
            return result, errors
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{base}: {exc}")
            continue

    return None, errors


async def _call_lifestyle(path: str, *, method: str = "GET", payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Call the upstream Life-Style API and return the JSON payload."""

    bases = _iter_lifestyle_bases()
    if not bases:
        raise LifestyleAPIError("Life-Styleエージェント API の接続先が設定されていません。", status_code=500)

    mcp_errors: list[str] = []
    if _USE_LIFESTYLE_MCP:
        tool_name = None
        tool_args: Dict[str, Any] = {}
        if path in {"/rag_answer", "/agent_rag_answer"}:
            question = ""
            if payload:
                question = str(payload.get("question") or "").strip()
            tool_name = "rag_answer"
            tool_args = {"question": question, "persist_history": path != "/agent_rag_answer"}
        elif path == "/analyze_conversation":
            history_payload = []
            if payload:
                history_payload = payload.get("conversation_history") or payload.get("history") or []
            tool_name = "analyze_conversation"
            tool_args = {"conversation_history": history_payload}

        if tool_name:
            result, mcp_errors = await _call_lifestyle_tool_via_mcp(bases, tool_name, tool_args)
            if result is not None:
                return result

    connection_errors: list[str] = []
    last_exception: Exception | None = None
    response: httpx.Response | None = None
    async with httpx.AsyncClient(timeout=LIFESTYLE_TIMEOUT) as client:
        for base in bases:
            url = _build_lifestyle_url(base, path)
            try:
                response = await client.request(method, url, json=payload)
            except httpx.RequestError as exc:  # pragma: no cover - network failure
                connection_errors.append(f"{url}: {exc}")
                last_exception = exc
                continue
            else:
                break

    if response is None:
        message_lines = ["Life-Styleエージェント API への接続に失敗しました。"]
        if connection_errors:
            message_lines.append("試行した URL:")
            message_lines.extend(f"- {error}" for error in connection_errors)
        if mcp_errors:
            message_lines.append("MCP 経由の呼び出し失敗:")
            message_lines.extend(f"- {error}" for error in mcp_errors)
        message = "\n".join(message_lines)
        raise LifestyleAPIError(message) from last_exception

    try:
        data = response.json()
    except ValueError:  # pragma: no cover - unexpected upstream response
        data = {"error": response.text or "Unexpected response from Life-Styleエージェント API."}

    if not response.is_success:
        message = data.get("error") if isinstance(data, dict) else None
        if not message:
            message = response.text or f"{response.status_code} {response.reason_phrase}"
        raise LifestyleAPIError(message, status_code=response.status_code)

    if not isinstance(data, dict):
        raise LifestyleAPIError("Life-Styleエージェント API から不正なレスポンス形式が返されました。", status_code=502)

    return data
