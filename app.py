"""Flask application that serves the SPA and proxies FAQ_Gemini APIs."""

from __future__ import annotations

import logging
import os
from typing import Any, Dict

import requests
from flask import Flask, jsonify, request, send_from_directory


DEFAULT_GEMINI_BASE = "http://localhost:5000"
GEMINI_TIMEOUT = float(os.environ.get("FAQ_GEMINI_TIMEOUT", "30"))


app = Flask(__name__, static_folder="assets", static_url_path="/assets")
logging.basicConfig(level=logging.INFO)


class GeminiAPIError(RuntimeError):
    """Raised when the upstream FAQ_Gemini API responds with an error."""

    def __init__(self, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.status_code = status_code


def _resolve_gemini_base() -> str:
    """Resolve the upstream FAQ_Gemini base URL from the environment."""

    base = os.environ.get("FAQ_GEMINI_API_BASE", DEFAULT_GEMINI_BASE).strip()
    if not base:
        raise GeminiAPIError("FAQ_GEMINI_API_BASE が設定されていません。", status_code=500)
    return base.rstrip("/")


def _build_gemini_url(path: str) -> str:
    """Build an absolute URL to the upstream FAQ_Gemini API."""

    base = _resolve_gemini_base()
    if not path.startswith("/"):
        path = f"/{path}"
    return f"{base}{path}"


def _call_gemini(path: str, *, method: str = "GET", payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Call the upstream FAQ_Gemini API and return the JSON payload."""

    url = _build_gemini_url(path)
    try:
        response = requests.request(method, url, json=payload, timeout=GEMINI_TIMEOUT)
    except requests.exceptions.RequestException as exc:  # pragma: no cover - network failure
        raise GeminiAPIError(f"FAQ_Gemini API への接続に失敗しました: {exc}") from exc

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
