"""Flask application that serves the SPA and proxies FAQ_Gemini APIs."""

from __future__ import annotations

import logging
import os
from typing import Any, Dict

import requests
from flask import Flask, Response, jsonify, request, send_from_directory


DEFAULT_GEMINI_BASES = (
    "http://localhost:5000",
    "http://faq_gemini:5000",
)
GEMINI_TIMEOUT = float(os.environ.get("FAQ_GEMINI_TIMEOUT", "30"))

DEFAULT_IOT_AGENT_BASES = (
    "http://localhost:5005",
    "http://iot_agent:5005",
)
IOT_AGENT_TIMEOUT = float(os.environ.get("IOT_AGENT_TIMEOUT", "30"))


app = Flask(__name__, static_folder="assets", static_url_path="/assets")
logging.basicConfig(level=logging.INFO)


class GeminiAPIError(RuntimeError):
    """Raised when the upstream FAQ_Gemini API responds with an error."""

    def __init__(self, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.status_code = status_code


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


def _build_gemini_url(base: str, path: str) -> str:
    """Build an absolute URL to the upstream FAQ_Gemini API."""

    if not path.startswith("/"):
        path = f"/{path}"
    return f"{base}{path}"


def _build_iot_agent_url(base: str, path: str) -> str:
    """Build an absolute URL to the upstream IoT Agent API."""

    if not path.startswith("/"):
        path = f"/{path}"
    return f"{base}{path}"


def _call_gemini(path: str, *, method: str = "GET", payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Call the upstream FAQ_Gemini API and return the JSON payload."""

    bases = _iter_gemini_bases()
    if not bases:
        raise GeminiAPIError("FAQ_Gemini API の接続先が設定されていません。", status_code=500)

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
        message_lines = ["FAQ_Gemini API への接続に失敗しました。"]
        if connection_errors:
            message_lines.append("試行した URL:")
            message_lines.extend(f"- {error}" for error in connection_errors)
        message = "\n".join(message_lines)
        raise GeminiAPIError(message) from last_exception

    try:
        data = response.json()
    except ValueError:  # pragma: no cover - unexpected upstream response
        data = {"error": response.text or "Unexpected response from FAQ_Gemini API."}

    if not response.ok:
        message = data.get("error") if isinstance(data, dict) else None
        if not message:
            message = response.text or f"{response.status_code} {response.reason}"
        raise GeminiAPIError(message, status_code=response.status_code)

    if not isinstance(data, dict):
        raise GeminiAPIError("FAQ_Gemini API から不正なレスポンス形式が返されました。", status_code=502)

    return data


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


@app.route("/rag_answer", methods=["POST"])
def rag_answer() -> Any:
    """Proxy the rag_answer endpoint to the FAQ_Gemini backend."""

    payload = request.get_json(silent=True) or {}
    question = (payload.get("question") or "").strip()
    if not question:
        return jsonify({"error": "質問を入力してください。"}), 400

    try:
        data = _call_gemini("/rag_answer", method="POST", payload={"question": question})
    except GeminiAPIError as exc:
        logging.exception("FAQ_Gemini rag_answer failed: %s", exc)
        return jsonify({"error": str(exc)}), exc.status_code

    return jsonify(data)


@app.route("/conversation_history", methods=["GET"])
def conversation_history() -> Any:
    """Fetch the conversation history from the FAQ_Gemini backend."""

    try:
        data = _call_gemini("/conversation_history")
    except GeminiAPIError as exc:
        logging.exception("FAQ_Gemini conversation_history failed: %s", exc)
        return jsonify({"error": str(exc)}), exc.status_code

    return jsonify(data)


@app.route("/conversation_summary", methods=["GET"])
def conversation_summary() -> Any:
    """Fetch the conversation summary from the FAQ_Gemini backend."""

    try:
        data = _call_gemini("/conversation_summary")
    except GeminiAPIError as exc:
        logging.exception("FAQ_Gemini conversation_summary failed: %s", exc)
        return jsonify({"error": str(exc)}), exc.status_code

    return jsonify(data)


@app.route("/reset_history", methods=["POST"])
def reset_history() -> Any:
    """Request the FAQ_Gemini backend to clear the conversation history."""

    try:
        data = _call_gemini("/reset_history", method="POST")
    except GeminiAPIError as exc:
        logging.exception("FAQ_Gemini reset_history failed: %s", exc)
        return jsonify({"error": str(exc)}), exc.status_code

    return jsonify(data)


@app.route("/iot_agent", defaults={"path": ""}, methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
@app.route("/iot_agent/<path:path>", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
def proxy_iot_agent(path: str) -> Response:
    """Forward IoT Agent API requests to the configured upstream service."""

    return _proxy_iot_agent_request(path)


@app.route("/")
def serve_index() -> Any:
    """Serve the main single-page application."""

    return send_from_directory(app.root_path, "index.html")


@app.route("/<path:path>")
def serve_file(path: str) -> Any:
    """Serve any additional static files that live alongside index.html."""

    return send_from_directory(app.root_path, path)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5050)
