"""FAQ_Gemini (Life-Assistantエージェント) client helpers."""

from __future__ import annotations

import os
from typing import Any, Dict, List

import requests

from .config import DEFAULT_GEMINI_BASES, GEMINI_TIMEOUT
from .errors import GeminiAPIError


def _iter_gemini_bases() -> list[str]:
    """Return the configured FAQ_Gemini base URLs in priority order."""

    configured = os.environ.get("FAQ_GEMINI_API_BASE", "")
    candidates: list[str] = []
    if configured:
        candidates.extend(part.strip() for part in configured.split(","))
    candidates.extend(DEFAULT_GEMINI_BASES)

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


def _build_gemini_url(base: str, path: str) -> str:
    """Build an absolute URL to the upstream FAQ_Gemini API."""

    if not path.startswith("/"):
        path = f"/{path}"
    return f"{base}{path}"


def _call_gemini(path: str, *, method: str = "GET", payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Call the upstream FAQ_Gemini API and return the JSON payload."""

    bases = _iter_gemini_bases()
    if not bases:
        raise GeminiAPIError("Life-Assistantエージェント（FAQ_Gemini）API の接続先が設定されていません。", status_code=500)

    connection_errors: list[str] = []
    last_exception: Exception | None = None
    response = None
    for base in bases:
        url = _build_gemini_url(base, path)
        try:
            response = requests.request(method, url, json=payload, timeout=GEMINI_TIMEOUT)
        except requests.exceptions.RequestException as exc:  # pragma: no cover - network failure
            connection_errors.append(f"{url}: {exc}")
            last_exception = exc
            continue
        else:
            break

    if response is None:
        message_lines = ["Life-Assistantエージェント（FAQ_Gemini）API への接続に失敗しました。"]
        if connection_errors:
            message_lines.append("試行した URL:")
            message_lines.extend(f"- {error}" for error in connection_errors)
        message = "\n".join(message_lines)
        raise GeminiAPIError(message) from last_exception

    try:
        data = response.json()
    except ValueError:  # pragma: no cover - unexpected upstream response
        data = {"error": response.text or "Unexpected response from Life-Assistantエージェント（FAQ_Gemini）API."}

    if not response.ok:
        message = data.get("error") if isinstance(data, dict) else None
        if not message:
            message = response.text or f"{response.status_code} {response.reason}"
        raise GeminiAPIError(message, status_code=response.status_code)

    if not isinstance(data, dict):
        raise GeminiAPIError("Life-Assistantエージェント（FAQ_Gemini）API から不正なレスポンス形式が返されました。", status_code=502)

    return data
