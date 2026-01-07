"""IoT Agent client helpers."""

from __future__ import annotations

import json
import logging
import os
import time
import asyncio
from typing import Any, Dict, List

import httpx
from fastapi import Request
from fastapi.responses import Response, JSONResponse
from mcp.client.sse import sse_client
from mcp import ClientSession

from langchain_openai import ChatOpenAI
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage

from .config import (
    DEFAULT_IOT_AGENT_BASES,
    IOT_AGENT_TIMEOUT,
    IOT_MODEL_SYNC_TIMEOUT,
    PUBLIC_IOT_AGENT_BASE,
)
from .settings import resolve_llm_config

# Context fetch should be best-effort to avoid blocking orchestrator planning.
from .errors import IotAgentError

IOT_DEVICE_CONTEXT_TIMEOUT = float(os.environ.get("IOT_DEVICE_CONTEXT_TIMEOUT", "8.0"))
IOT_MCP_SSE_TIMEOUT = float(os.environ.get("IOT_MCP_SSE_TIMEOUT", "15.0"))
IOT_MCP_COMMAND_TIMEOUT = float(os.environ.get("IOT_MCP_COMMAND_TIMEOUT", "45.0"))
_USE_IOT_AGENT_HISTORY_MCP = os.environ.get("IOT_AGENT_HISTORY_USE_MCP", "1").strip().lower() not in {"0", "false", "no", "off"}
_IOT_AGENT_MCP_CONVERSATION_TOOL = os.environ.get("IOT_AGENT_MCP_CONVERSATION_TOOL", "analyze_conversation").strip() or "analyze_conversation"
# Skip MCP for external HTTPS endpoints to avoid SSE connection issues
_SKIP_MCP_FOR_EXTERNAL = os.environ.get("IOT_SKIP_MCP_FOR_EXTERNAL", "1").strip().lower() not in {"0", "false", "no", "off"}


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


def _is_external_endpoint(base: str) -> bool:
    """Check if the endpoint is an external HTTPS endpoint (not localhost/docker)."""
    if not base:
        return False
    normalized = base.lower()
    # External endpoints typically use HTTPS and are not localhost/docker-internal
    if normalized.startswith("https://"):
        return True
    # Check for public domain patterns
    if "project-kk.com" in normalized:
        return True
    return False


async def _post_iot_agent(path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Send a JSON payload to the IoT Agent and return the JSON response."""

    bases = _iter_iot_agent_bases()
    if not bases:
        raise IotAgentError("IoT Agent API の接続先が設定されていません。", status_code=500)

    connection_errors: list[str] = []
    last_exception: Exception | None = None
    response: httpx.Response | None = None
    async with httpx.AsyncClient(timeout=IOT_AGENT_TIMEOUT) as client:
        for base in bases:
            url = _build_iot_agent_url(base, path)
            try:
                response = await client.post(url, json=payload)
            except httpx.RequestError as exc:  # pragma: no cover - network failure
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

    if not response.is_success:
        message = data.get("error") if isinstance(data, dict) else None
        if not message:
            message = response.text or f"{response.status_code} {response.reason_phrase}"
        raise IotAgentError(message, status_code=response.status_code)

    if not isinstance(data, dict):
        raise IotAgentError("IoT Agent API から不正なレスポンス形式が返されました。", status_code=502)

    return data


async def _fetch_iot_model_selection() -> Dict[str, str] | None:
    """Fetch the IoT Agent's current model selection for cross-app sync."""

    bases = _iter_iot_agent_bases()
    if not bases:
        return None

    async with httpx.AsyncClient(timeout=IOT_MODEL_SYNC_TIMEOUT) as client:
        for base in bases:
            url = _build_iot_agent_url(base, "/api/models")
            try:
                response = await client.get(url)
            except httpx.RequestError as exc:  # pragma: no cover - network failure
                logging.info("IoT model sync attempt to %s skipped (%s)", url, exc)
                continue

            if not response.is_success:
                logging.info(
                    "IoT model sync attempt to %s failed: %s %s", url, response.status_code, response.text
                )
                continue

            try:
                payload = response.json()
            except ValueError:
                logging.info("IoT model sync attempt to %s returned invalid JSON", url)
                continue

            current = payload.get("current") if isinstance(payload, dict) else None
            if not isinstance(current, dict):
                logging.info("IoT model sync attempt to %s missing current selection", url)
                continue

            provider = str(current.get("provider") or "").strip()
            model = str(current.get("model") or "").strip()
            base_url = str(current.get("base_url") or "").strip()
            if not provider or not model:
                logging.info("IoT model sync attempt to %s missing provider/model", url)
                continue

            return {"provider": provider, "model": model, "base_url": base_url}

    return None


async def _count_iot_devices() -> int | None:
    """Return the number of registered IoT devices, or None if unavailable."""

    bases = _iter_iot_agent_bases()
    if not bases:
        return None

    async with httpx.AsyncClient(timeout=IOT_DEVICE_CONTEXT_TIMEOUT) as client:
        for base in bases:
            url = _build_iot_agent_url(base, "/api/devices")
            try:
                response = await client.get(url)
            except httpx.RequestError:
                continue

            if not response.is_success:
                continue

            try:
                payload = response.json()
            except ValueError:
                continue

            devices = payload.get("devices") if isinstance(payload, dict) else None
            if isinstance(devices, list):
                return len(devices)

    return None


def _format_device_context(devices: List[Dict[str, Any]]) -> str:
    """Convert IoT Agent device payloads into a planner-friendly context block."""

    if not devices:
        return "No devices are currently registered."

    def _format_timestamp(value: Any) -> str:
        try:
            return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(value)))
        except Exception:  # noqa: BLE001 - defensive
            return "-"

    lines: list[str] = []
    for device in devices:
        if not isinstance(device, dict):
            continue
        device_id = str(device.get("device_id") or "").strip() or "unknown-device"
        lines.append(f"Device ID: {device_id}")

        meta = device.get("meta") if isinstance(device.get("meta"), dict) else {}
        display_name = meta.get("display_name")
        if isinstance(display_name, str) and display_name.strip():
            lines.append(f"  Friendly name: {display_name.strip()}")

        role = meta.get("role") or meta.get("device_role")
        if isinstance(role, str) and role.strip():
            lines.append(f"  Role tag: {role.strip()}")

        action_catalog = device.get("action_catalog") if isinstance(device.get("action_catalog"), list) else []
        if action_catalog:
            lines.append("  Agent predefined actions:")
            for entry in action_catalog:
                if not isinstance(entry, dict):
                    continue
                name = str(entry.get("name") or "").strip()
                if not name:
                    continue
                desc = str(entry.get("description") or "").strip()
                params = entry.get("params") if isinstance(entry.get("params"), list) else []
                if params:
                    param_desc = ", ".join(
                        f"{p.get('name')} ({p.get('type', 'unknown')})"
                        + (
                            f" default={json.dumps(p.get('default'), ensure_ascii=False)}"
                            if p.get("default") is not None
                            else ""
                        )
                        for p in params
                        if isinstance(p, dict) and p.get("name")
                    )
                else:
                    param_desc = "no parameters"
                if desc:
                    lines.append(f"    - {name}: {desc} | params: {param_desc}")
                else:
                    lines.append(f"    - {name} | params: {param_desc}")

        queue_depth = device.get("queue_depth")
        if queue_depth is not None:
            lines.append(f"  Queue depth: {queue_depth}")

        registered_at = device.get("registered_at")
        last_seen = device.get("last_seen")
        if registered_at:
            lines.append("  Registered at: " + _format_timestamp(registered_at))
        if last_seen:
            lines.append("  Last seen: " + _format_timestamp(last_seen))

        capabilities = device.get("capabilities") if isinstance(device.get("capabilities"), list) else []
        lines.append("  Capabilities:")
        for capability in capabilities:
            if not isinstance(capability, dict):
                continue
            name = str(capability.get("name") or "").strip()
            if not name:
                continue
            description = str(capability.get("description") or "").strip()
            params = capability.get("params") if isinstance(capability.get("params"), list) else []
            if params:
                param_desc = ", ".join(
                    f"{param.get('name')} ({param.get('type', 'unknown')})"
                    + (
                        f" default={json.dumps(param.get('default'), ensure_ascii=False)}"
                        if param.get("default") is not None
                        else ""
                    )
                    for param in params
                    if isinstance(param, dict) and param.get("name")
                )
            else:
                param_desc = "no parameters"
            if description:
                lines.append(f"    - {name}: {description} | params: {param_desc}")
            else:
                lines.append(f"    - {name} | params: {param_desc}")

        last_result = device.get("last_result")
        if isinstance(last_result, dict) and last_result:
            summary = {
                "job_id": last_result.get("job_id"),
                "ok": last_result.get("ok"),
                "return_value": last_result.get("return_value"),
            }
            lines.append("  Most recent result: " + json.dumps(summary, ensure_ascii=False, default=str))

        lines.append("")

    return "\n".join(lines).strip()


async def _fetch_iot_device_context() -> str | None:
    """Fetch device information from the IoT Agent for orchestrator prompts.
    
    For external HTTPS endpoints, directly use HTTP API to avoid SSE connection issues.
    For local/docker endpoints, try MCP first with fallback to HTTP API.
    """

    bases = _iter_iot_agent_bases()
    if not bases:
        logging.info("IoT device context fetch skipped because no agent bases are configured.")
        return None

    async def _fetch_via_mcp(base_url: str):
        sse_url = _build_iot_agent_url(base_url, "/mcp/sse")
        async with sse_client(sse_url, timeout=IOT_MCP_SSE_TIMEOUT) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                devices = []

                # Prefer the aggregated MCP tool if available (more robust for multi-device setups)
                try:
                    tools_result = await session.list_tools()
                    tool_names = [getattr(t, "name", "") for t in getattr(tools_result, "tools", None) or []]
                    if "get_device_list" in tool_names:
                        tool_res = await session.call_tool("get_device_list", {})
                        for content in tool_res.contents:
                            if hasattr(content, "text") and content.text:
                                try:
                                    parsed = json.loads(content.text)
                                    if isinstance(parsed, list):
                                        devices.extend(parsed)
                                except json.JSONDecodeError:
                                    logging.debug("Failed to parse get_device_list payload")
                        if devices:
                            return devices
                except Exception as exc:  # noqa: BLE001 - best-effort
                    logging.debug("MCP get_device_list failed for %s: %s", base_url, exc)

                # Fallback: read per-device resources
                try:
                    resources_result = await session.list_resources()
                except Exception as exc:  # noqa: BLE001
                    logging.debug("MCP list_resources failed for %s: %s", base_url, exc)
                    return []

                for res in resources_result.resources:
                    try:
                        content_result = await session.read_resource(res.uri)
                        for content in content_result.contents:
                            if hasattr(content, "text") and content.text:
                                devices.append(json.loads(content.text))
                    except Exception as exc:
                        logging.warning("Failed to read resource %s: %s", res.uri, exc)
                return devices

    async def _fetch_via_http(base_url: str) -> list | None:
        """Fetch devices via HTTP API (more reliable for external endpoints)."""
        url = _build_iot_agent_url(base_url, "/api/devices")
        try:
            async with httpx.AsyncClient(timeout=IOT_DEVICE_CONTEXT_TIMEOUT) as client:
                response = await client.get(url)
                if response.is_success:
                    payload = response.json()
                    devices = payload.get("devices") if isinstance(payload, dict) else None
                    if isinstance(devices, list):
                        return devices
        except httpx.ReadTimeout:
            logging.debug("IoT device context fetch timed out for %s", url)
        except httpx.RequestError as exc:
            logging.debug("IoT device context fetch failed for %s: %s", url, exc)
        return None

    for base in bases:
        is_external = _is_external_endpoint(base)
        
        # For external HTTPS endpoints, skip MCP and use HTTP directly
        if is_external and _SKIP_MCP_FOR_EXTERNAL:
            logging.debug("Using HTTP API for external IoT endpoint: %s", base)
            devices = await _fetch_via_http(base)
            if devices:
                return _format_device_context(devices)
            continue
        
        # For local/docker endpoints, try MCP first with fallback to HTTP
        try:
            devices = await asyncio.wait_for(_fetch_via_mcp(base), timeout=IOT_MCP_SSE_TIMEOUT)
            if devices:
                return _format_device_context(devices)
        except Exception as exc:
            logging.debug("MCP device fetch failed for %s: %s. Falling back to HTTP API.", base, exc)

            devices = await _fetch_via_http(base)
            if devices:
                return _format_device_context(devices)

    return None


def _init_iot_llm() -> Any:
    """Initialize the LLM based on the 'iot' configuration in settings."""
    try:
        resolved_config = resolve_llm_config("iot")
    except Exception as exc:
        raise IotAgentError(f"IoT LLM configuration failed: {exc}") from exc

    api_key = resolved_config.get("api_key")
    if not api_key:
        raise IotAgentError("IoT Agent API Key not configured")

    model_name = resolved_config["model"]
    provider = resolved_config.get("provider", "openai")
    base_url = resolved_config.get("base_url") or None
    temperature = 0.0  # Precise tools

    if provider == "gemini":
        return ChatGoogleGenerativeAI(
            model=model_name,
            temperature=temperature,
            google_api_key=api_key,
        )
    elif provider == "claude":
        return ChatAnthropic(
            model=model_name,
            temperature=temperature,
            api_key=api_key,
            base_url=base_url,
        )
    else:
        return ChatOpenAI(
            model=model_name,
            temperature=temperature,
            api_key=api_key,
            base_url=base_url,
        )


def _normalise_tool_call(tool_call: Any) -> tuple[str | None, Dict[str, Any]]:
    """Normalise tool call payloads across LangChain versions."""

    name = None
    args: Any = {}
    if isinstance(tool_call, dict):
        name = tool_call.get("name")
        args = tool_call.get("args")
    else:
        name = getattr(tool_call, "name", None)
        args = getattr(tool_call, "args", None)
        if args is None:
            args = getattr(tool_call, "arguments", None)

    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            args = {}
    if not isinstance(args, dict):
        args = {}

    return name, args


def _extract_llm_text(message: Any) -> str:
    """Extract text content from an LLM response across message formats."""

    if message is None:
        return ""
    text_attr = getattr(message, "text", None)
    if isinstance(text_attr, str):
        return text_attr
    content_blocks = getattr(message, "content_blocks", None)
    if isinstance(content_blocks, list):
        parts: list[str] = []
        for block in content_blocks:
            block_text = getattr(block, "text", None)
            if isinstance(block_text, str):
                parts.append(block_text)
            elif isinstance(block, dict):
                text = block.get("text") or block.get("output_text") or block.get("content")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
                continue
            if isinstance(item, dict):
                text = item.get("text") or item.get("output_text") or item.get("content")
                if isinstance(text, str):
                    parts.append(text)
                continue
            item_text = getattr(item, "text", None)
            if isinstance(item_text, str):
                parts.append(item_text)
        return "".join(parts)
    return str(content)


async def _execute_via_mcp(command: str, base_url: str) -> Dict[str, Any]:
    """Execute the command using MCP tools and a local LLM."""

    sse_url = _build_iot_agent_url(base_url, "/mcp/sse")

    async with sse_client(sse_url, timeout=IOT_MCP_COMMAND_TIMEOUT) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            try:
                tools_result = await session.list_tools()
            except Exception as exc:
                raise IotAgentError(f"MCP ツールの取得に失敗しました: {exc}") from exc

            mcp_tools = tools_result.tools
            if not mcp_tools:
                raise IotAgentError("IoT Agent から MCP ツールが公開されていません。")

            llm = _init_iot_llm()

            lc_tools = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.inputSchema,
                    },
                }
                for tool in mcp_tools
            ]

            llm_with_tools = llm.bind_tools(lc_tools)

            system_prompt = (
                "You are an IoT assistant. You have access to the following tools to control devices. "
                "Select the most appropriate tool and arguments to fulfill the user's request. "
                "IMPORTANT RULES: "
                "1. If the task uniquely maps to one device/command, execute immediately WITHOUT asking for clarification. "
                "2. If only ONE device is registered, always use it without asking. "
                "3. When the action is clear (e.g., 'ring buzzer', 'turn on LED', 'take photo'), execute immediately. "
                "4. Use default values for missing parameters (e.g., duration=5.0 seconds). "
                "5. Only ask for clarification when multiple devices have the SAME capability and you truly cannot infer which one to use. "
                "If no tool is appropriate, reply with a message explaining why."
            )

            messages = [
                SystemMessage(content=system_prompt),
                HumanMessage(content=command),
            ]

            try:
                response = await llm_with_tools.ainvoke(messages)
            except Exception as exc:
                raise IotAgentError(f"IoT LLM の実行に失敗しました: {exc}") from exc

            tool_calls = getattr(response, "tool_calls", None) or []
            if tool_calls:
                tool_name, tool_args = _normalise_tool_call(tool_calls[0])
                if not tool_name:
                    raise IotAgentError("IoT LLM のツール呼び出しに tool 名が含まれていません。")
                logging.info("Executing MCP tool %s with args %s", tool_name, tool_args)

                try:
                    result = await session.call_tool(tool_name, tool_args)
                except Exception as exc:
                    raise IotAgentError(
                        f"MCP ツール {tool_name} の呼び出しに失敗しました: {exc}"
                    ) from exc

                text_res: list[str] = []
                for content in result.content:
                    if hasattr(content, "text") and content.text:
                        text_res.append(content.text)

                final_reply = "\n".join(text_res).strip()
                return {"reply": final_reply or "ツールは結果を返しましたが空のレスポンスでした。"}

            # No tool called; bubble the model content back.
            reply_text = _extract_llm_text(response).strip()
            return {"reply": reply_text or "IoT エージェントからの応答が空でした。"}


async def _execute_via_http_chat(command: str, base_url: str) -> Dict[str, Any]:
    """Execute a command via the HTTP /api/chat endpoint (for external endpoints)."""
    url = _build_iot_agent_url(base_url, "/api/chat")
    try:
        async with httpx.AsyncClient(timeout=IOT_AGENT_TIMEOUT) as client:
            response = await client.post(
                url,
                json={"messages": [{"role": "user", "content": command}]},
            )
        if response.is_success:
            return response.json()
        error_msg = response.text or f"{response.status_code} {response.reason_phrase}"
        raise IotAgentError(f"HTTP API エラー: {error_msg}", status_code=response.status_code)
    except httpx.RequestError as exc:
        raise IotAgentError(f"HTTP API 接続エラー: {exc}") from exc


async def _call_iot_agent_command(command: str) -> Dict[str, Any]:
    """Send a command to the IoT Agent.
    
    For external HTTPS endpoints, use HTTP API directly for reliability.
    For local/docker endpoints, use MCP with fallback to HTTP API.
    """

    bases = _iter_iot_agent_bases()
    if not bases:
        raise IotAgentError("IoT Agent API の接続先が設定されていません。", status_code=500)

    errors: list[str] = []
    skipped_http_fallback = False
    for base in bases:
        is_external = _is_external_endpoint(base)
        
        # For external HTTPS endpoints, use HTTP API directly
        if is_external and _SKIP_MCP_FOR_EXTERNAL:
            logging.debug("Using HTTP API for external IoT command: %s", base)
            try:
                return await _execute_via_http_chat(command, base)
            except IotAgentError as exc:
                message = f"{base} (HTTP): {exc}"
                errors.append(message)
                logging.warning("HTTP execution failed for %s: %s", base, exc)
                continue
        
        # For local/docker endpoints, try MCP first
        try:
            return await _execute_via_mcp(command, base)
        except IotAgentError as exc:
            message = f"{base} (MCP): {exc}"
            errors.append(message)
            logging.warning("MCP execution failed for %s: %s", base, exc)
            skipped_http_fallback = True
            continue
        except Exception as exc:  # pragma: no cover - defensive guard
            message = f"{base}: {exc}"
            errors.append(message)
            logging.exception("Unexpected MCP execution failure for %s", base)
            continue

    details = "\n".join(f"- {error}" for error in errors) if errors else "- 理由不明のエラー"
    if skipped_http_fallback:
        details += "\n- HTTP API にはフォールバックしませんでした（MCP 専用エンドポイント）"
    raise IotAgentError("IoT Agent コマンドを実行できませんでした。\n" + details)


async def _call_iot_agent_chat(command: str) -> Dict[str, Any]:
    """Backward-compatible alias for `_call_iot_agent_command`."""

    return await _call_iot_agent_command(command)


async def _call_iot_agent_conversation_review(
    conversation_history: List[Dict[str, str]]
) -> Dict[str, Any]:
    """Send conversation history to the IoT Agent review endpoint."""

    mcp_result: Dict[str, Any] | None = None
    mcp_errors: list[str] = []

    mcp_result, mcp_errors = await _call_iot_agent_conversation_review_via_mcp(conversation_history)
    if mcp_result is not None:
        return mcp_result

    try:
        return await _post_iot_agent(
            "/api/conversations/review",
            {"history": conversation_history},
        )
    except IotAgentError as exc:
        if mcp_errors:
            message_lines = [str(exc), "MCP 経由での会話同期も失敗しました:"]
            message_lines.extend(f"- {error}" for error in mcp_errors)
            raise IotAgentError(
                "\n".join(message_lines),
                status_code=getattr(exc, "status_code", 502),
            ) from exc
        raise


def _parse_iot_history_mcp_result(result: Any) -> Dict[str, Any]:
    """Extract JSON payload from IoT Agent MCP analyze_conversation."""

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

    raise IotAgentError("IoT Agent MCP analyze_conversation が空の応答を返しました。")


async def _call_iot_agent_conversation_review_async(
    conversation_history: List[Dict[str, str]], base_url: str
) -> Dict[str, Any]:
    """Execute the IoT Agent analyze_conversation MCP tool."""

    sse_url = _build_iot_agent_url(base_url, "/mcp/sse")
    async with sse_client(sse_url, timeout=IOT_AGENT_TIMEOUT) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools_result = await session.list_tools()
            tool_names = [getattr(tool, "name", "") for tool in getattr(tools_result, "tools", None) or []]
            if _IOT_AGENT_MCP_CONVERSATION_TOOL not in tool_names:
                raise IotAgentError("IoT Agent に analyze_conversation MCP ツールが見つかりませんでした。")

            result = await session.call_tool(
                _IOT_AGENT_MCP_CONVERSATION_TOOL,
                {"conversation_history": conversation_history},
            )
            return _parse_iot_history_mcp_result(result)


async def _call_iot_agent_conversation_review_via_mcp(
    conversation_history: List[Dict[str, str]],
) -> tuple[Dict[str, Any] | None, list[str]]:
    """Best-effort MCP call for conversation review with fallback to HTTP.
    
    For external HTTPS endpoints, skip MCP and return None to trigger HTTP fallback.
    """

    errors: list[str] = []

    if not _USE_IOT_AGENT_HISTORY_MCP:
        return None, errors

    bases = _iter_iot_agent_bases()
    if not bases:
        return None, ["IoT Agent API の接続先が設定されていません。"]

    for base in bases:
        if _is_external_endpoint(base) and _SKIP_MCP_FOR_EXTERNAL:
            logging.debug("Skipping MCP for external endpoint %s in conversation review", base)
            continue

        try:
            result = await _call_iot_agent_conversation_review_async(conversation_history, base)
            return result, errors
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{base}: {exc}")
            continue

    return None, errors


async def _proxy_iot_agent_request(request: Request, path: str) -> Response:
    """Proxy the incoming request to the configured IoT Agent API."""

    bases = _iter_iot_agent_bases()
    if not bases:
        return JSONResponse(
            {"status": "unavailable", "error": "IoT Agent API の接続先が設定されていません。"}
        )

    json_payload = None
    body_payload = None
    content_type = request.headers.get("content-type", "").lower()
    if "application/json" in content_type:
        try:
            json_payload = await request.json()
        except Exception:
            json_payload = None
    elif request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        body_payload = await request.body()

    forward_headers: Dict[str, str] = {}
    for header, value in request.headers.items():
        lowered = header.lower()
        if lowered in {"content-type", "authorization", "accept", "cookie"} or lowered.startswith("x-"):
            forward_headers[header] = value

    connection_errors: list[str] = []
    response: httpx.Response | None = None
    async with httpx.AsyncClient(timeout=IOT_AGENT_TIMEOUT) as client:
        for base in bases:
            url = _build_iot_agent_url(base, path)
            try:
                response = await client.request(
                    request.method,
                    url,
                    params=request.query_params,
                    json=json_payload,
                    content=body_payload if json_payload is None else None,
                    headers=forward_headers,
                )
            except httpx.RequestError as exc:  # pragma: no cover - network failure
                connection_errors.append(f"{url}: {exc}")
                continue
            else:
                break

    if response is None:
        message_lines = ["IoT Agent API への接続に失敗しました。"]
        if connection_errors:
            message_lines.append("試行した URL:")
            message_lines.extend(f"- {error}" for error in connection_errors)
        return JSONResponse({"status": "unavailable", "error": "\n".join(message_lines)})

    proxy_response = Response(response.content, status_code=response.status_code)
    excluded_headers = {"content-encoding", "transfer-encoding", "connection", "content-length"}
    for header, value in response.headers.items():
        if header.lower() in excluded_headers:
            continue
        proxy_response.headers[header] = value
    return proxy_response
