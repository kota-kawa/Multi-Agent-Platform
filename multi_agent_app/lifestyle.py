"""Life-Assistant (Lifestyle) client helpers."""

from __future__ import annotations

import os
from typing import Any, Dict, List

import requests

from .config import DEFAULT_LIFESTYLE_BASES, LIFESTYLE_TIMEOUT
from .errors import LifestyleAPIError


def _iter_lifestyle_bases() -> list[str]:
    """Return the configured Life-Assistant base URLs in priority order."""

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
    """Build an absolute URL to the upstream Life-Assistant API."""

    if not path.startswith("/"):
        path = f"/{path}"
    return f"{base}{path}"


def _call_lifestyle(path: str, *, method: str = "GET", payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Call the upstream Life-Assistant API and return the JSON payload."""

    bases = _iter_lifestyle_bases()
    if not bases:
        raise LifestyleAPIError("Life-Assistantエージェント API の接続先が設定されていません。", status_code=500)

    connection_errors: list[str] = []
    last_exception: Exception | None = None
    response = None
    for base in bases:
        url = _build_lifestyle_url(base, path)
        try:
            response = requests.request(method, url, json=payload, timeout=LIFESTYLE_TIMEOUT)
        except requests.exceptions.RequestException as exc:  # pragma: no cover - network failure
            connection_errors.append(f"{url}: {exc}")
            last_exception = exc
            continue
        else:
            break

    if response is None:
        message_lines = ["Life-Assistantエージェント API への接続に失敗しました。"]
        if connection_errors:
            message_lines.append("試行した URL:")
            message_lines.extend(f"- {error}" for error in connection_errors)
        message = "\n".join(message_lines)
        raise LifestyleAPIError(message) from last_exception

    try:
        data = response.json()
    except ValueError:  # pragma: no cover - unexpected upstream response
        data = {"error": response.text or "Unexpected response from Life-Assistantエージェント API."}

    if not response.ok:
        message = data.get("error") if isinstance(data, dict) else None
        if not message:
            message = response.text or f"{response.status_code} {response.reason}"
        raise LifestyleAPIError(message, status_code=response.status_code)

    if not isinstance(data, dict):
        raise LifestyleAPIError("Life-Assistantエージェント API から不正なレスポンス形式が返されました。", status_code=502)

    return data