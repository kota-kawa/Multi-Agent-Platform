"""Agent connectivity checks and status helpers."""

from __future__ import annotations

import os
import time
import logging
from typing import Any, Dict, Tuple

import httpx

from .browser import _build_browser_agent_url, _iter_browser_agent_bases
from .iot import _build_iot_agent_url, _iter_iot_agent_bases
from .lifestyle import _build_lifestyle_url, _iter_lifestyle_bases
from .scheduler import _build_scheduler_agent_url, _iter_scheduler_agent_bases
from .settings import load_agent_connections

_STATUS_TTL_SECONDS = float(os.environ.get("AGENT_STATUS_TTL_SECONDS", "15"))
_STATUS_TIMEOUT_SECONDS = float(os.environ.get("AGENT_STATUS_TIMEOUT_SECONDS", "2.5"))

_status_cache: Dict[str, Any] = {"ts": 0.0, "payload": None}


async def _probe_base(client: httpx.AsyncClient, build_url, base: str) -> Tuple[bool, str | None]:
    url = build_url(base, "/")
    try:
        response = await client.get(url)
    except httpx.RequestError as exc:
        return False, str(exc)
    return True, f"{response.status_code}"


async def _resolve_agent_status(
    agent: str,
    bases: list[str],
    build_url,
    client: httpx.AsyncClient,
) -> Dict[str, Any]:
    if not bases:
        return {
            "available": False,
            "base": None,
            "error": "接続先が設定されていません。",
        }

    last_error = None
    for base in bases:
        ok, detail = await _probe_base(client, build_url, base)
        if ok:
            return {
                "available": True,
                "base": base,
                "error": None,
            }
        last_error = detail

    return {
        "available": False,
        "base": bases[0] if bases else None,
        "error": last_error or "接続に失敗しました。",
    }


async def get_agent_status(*, force: bool = False) -> Dict[str, Any]:
    """Return cached agent availability and connection details."""

    now = time.monotonic()
    cached = _status_cache.get("payload")
    if not force and cached and now - float(_status_cache.get("ts") or 0.0) < _STATUS_TTL_SECONDS:
        return cached

    try:
        async with httpx.AsyncClient(timeout=_STATUS_TIMEOUT_SECONDS) as client:
            lifestyle_status = await _resolve_agent_status(
                "lifestyle", _iter_lifestyle_bases(), _build_lifestyle_url, client
            )
            browser_status = await _resolve_agent_status(
                "browser", _iter_browser_agent_bases(), _build_browser_agent_url, client
            )
            iot_status = await _resolve_agent_status(
                "iot", _iter_iot_agent_bases(), _build_iot_agent_url, client
            )
            scheduler_status = await _resolve_agent_status(
                "scheduler", _iter_scheduler_agent_bases(), _build_scheduler_agent_url, client
            )
    except Exception as exc:  # noqa: BLE001 - defensive
        logging.warning("Failed to compute agent status: %s", exc)
        # Return a safe fallback with unknown status to avoid crashing the UI.
        return {
            "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "agents": {
                "browser": {"available": False, "base": None, "error": str(exc)},
                "lifestyle": {"available": False, "base": None, "error": str(exc)},
                "iot": {"available": False, "base": None, "error": str(exc)},
                "scheduler": {"available": False, "base": None, "error": str(exc)},
            },
        }

    connections = load_agent_connections()
    payload = {
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "agents": {
            "browser": {**browser_status, "enabled": bool(connections.get("browser", True))},
            "lifestyle": {**lifestyle_status, "enabled": bool(connections.get("lifestyle", True))},
            "iot": {**iot_status, "enabled": bool(connections.get("iot", True))},
            "scheduler": {**scheduler_status, "enabled": bool(connections.get("scheduler", True))},
        },
    }

    _status_cache["ts"] = now
    _status_cache["payload"] = payload
    return payload


async def get_agent_availability(*, force: bool = False) -> Dict[str, bool]:
    status = await get_agent_status(force=force)
    agents = status.get("agents", {}) if isinstance(status, dict) else {}
    availability: Dict[str, bool] = {}
    for agent_key, entry in agents.items():
        available = bool(entry.get("available"))
        availability[agent_key] = available
    return availability
