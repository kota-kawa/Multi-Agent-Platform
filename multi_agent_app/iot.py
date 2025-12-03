"""IoT Agent client helpers."""

from __future__ import annotations

import json
import logging
import os
import time
import asyncio
from typing import Any, Dict, List

import requests
from flask import Response, jsonify, request
from mcp.client.sse import sse_client
from mcp import ClientSession

from .config import DEFAULT_IOT_AGENT_BASES, IOT_AGENT_TIMEOUT

# Context fetch should be best-effort to avoid blocking orchestrator planning.
IOT_DEVICE_CONTEXT_TIMEOUT = 5.0
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


def _fetch_iot_model_selection() -> Dict[str, str] | None:
    """Fetch the IoT Agent's current model selection for cross-app sync."""

    bases = _iter_iot_agent_bases()
    if not bases:
        return None

    for base in bases:
        url = _build_iot_agent_url(base, "/api/models")
        try:
            response = requests.get(url, timeout=IOT_AGENT_TIMEOUT)
        except requests.exceptions.RequestException as exc:  # pragma: no cover - network failure
            logging.info("IoT model sync attempt to %s skipped (%s)", url, exc)
            continue

        if not response.ok:
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
            action_names = [
                str(entry.get("name")).strip()
                for entry in action_catalog
                if isinstance(entry, dict) and entry.get("name")
            ]
            action_names = [name for name in action_names if name]
            if action_names:
                lines.append("  Agent predefined actions: " + ", ".join(action_names))

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


def _fetch_iot_device_context() -> str | None:
    """Fetch device information from the IoT Agent for orchestrator prompts using MCP."""

    bases = _iter_iot_agent_bases()
    if not bases:
        logging.info("IoT device context fetch skipped because no agent bases are configured.")
        return None

    async def _fetch_via_mcp(base_url: str):
        sse_url = _build_iot_agent_url(base_url, "/mcp/sse")
        async with sse_client(sse_url, timeout=IOT_DEVICE_CONTEXT_TIMEOUT) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                resources_result = await session.list_resources()
                devices = []
                for res in resources_result.resources:
                    try:
                        content_result = await session.read_resource(res.uri)
                        for content in content_result.contents:
                            if hasattr(content, "text"):
                                devices.append(json.loads(content.text))
                    except Exception as e:
                        logging.warning(f"Failed to read resource {res.uri}: {e}")
                return devices

    for base in bases:
        # Try MCP first
        try:
            # asyncio.run might fail if loop is running, but we are in sync Flask/LangGraph context usually
            devices = asyncio.run(asyncio.wait_for(_fetch_via_mcp(base), timeout=IOT_DEVICE_CONTEXT_TIMEOUT))
            if devices:
                return _format_device_context(devices)
        except Exception as exc:
            logging.info("MCP device fetch failed for %s: %s. Falling back to HTTP API.", base, exc)
            
            # Fallback to Legacy HTTP API
            url = _build_iot_agent_url(base, "/api/devices")
            try:
                response = requests.get(url, timeout=min(IOT_DEVICE_CONTEXT_TIMEOUT, IOT_AGENT_TIMEOUT))
                if response.ok:
                    payload = response.json()
                    devices = payload.get("devices") if isinstance(payload, dict) else None
                    if isinstance(devices, list):
                        return _format_device_context(devices)
            except requests.exceptions.Timeout:
                logging.info("IoT device context fetch timed out for %s", url)
            except Exception as exc:
                logging.info("IoT device context fetch failed for %s: %s", url, exc)

    return None


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
