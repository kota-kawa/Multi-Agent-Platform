"""Flask blueprint and HTTP routes for the Multi-Agent Platform."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Iterator

from flask import (
    Blueprint,
    Response,
    current_app,
    g,
    has_request_context,
    jsonify,
    render_template,
    request,
    send_from_directory,
    stream_with_context,
)

from .browser import _canonicalise_browser_agent_base, _normalise_browser_base_values
from .config import _resolve_browser_agent_client_base, _resolve_browser_embed_url
from .errors import GeminiAPIError, OrchestratorError
from .gemini import _call_gemini
from .iot import _proxy_iot_agent_request
from .orchestrator import _get_orchestrator

bp = Blueprint("multi_agent_app", __name__)


def _format_sse_event(payload: Dict[str, Any]) -> str:
    """Serialise an SSE event line with the payload JSON."""

    event_type = str(payload.get("event") or "message").strip() or "message"
    data = json.dumps(payload, ensure_ascii=False)
    return f"event: {event_type}\ndata: {data}\n\n"


@bp.route("/orchestrator/chat", methods=["POST"])
def orchestrator_chat() -> Any:
    """Handle orchestrator chat requests originating from the General view."""

    payload = request.get_json(silent=True) or {}
    message = (payload.get("message") or "").strip()
    if not message:
        return jsonify({"error": "メッセージを入力してください。"}), 400

    overrides: list[str] = []
    overrides.extend(_normalise_browser_base_values(payload.get("browser_agent_base")))
    overrides.extend(_normalise_browser_base_values(payload.get("browser_agent_bases")))
    overrides = [value for value in overrides if value]
    service_default = _canonicalise_browser_agent_base("http://browser-agent:5005")
    if service_default and service_default not in overrides:
        overrides.append(service_default)
    if has_request_context():
        g.browser_agent_bases = overrides

    try:
        orchestrator = _get_orchestrator()
    except OrchestratorError as exc:
        logging.exception("Orchestrator initialisation failed: %s", exc)
        error_message = str(exc)

        def _error_stream(message: str) -> Iterator[str]:
            yield _format_sse_event({"event": "error", "error": message})

        return Response(
            stream_with_context(_error_stream(error_message)),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )

    def _stream() -> Iterator[str]:
        try:
            for event in orchestrator.run_stream(message):
                yield _format_sse_event(event)
        except OrchestratorError as exc:  # pragma: no cover - defensive
            logging.exception("Orchestrator execution failed: %s", exc)
            yield _format_sse_event({"event": "error", "error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            logging.exception("Unexpected orchestrator failure: %s", exc)
            yield _format_sse_event({"event": "error", "error": "内部エラーが発生しました。"})

    return Response(
        stream_with_context(_stream()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )


@bp.route("/rag_answer", methods=["POST"])
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


@bp.route("/conversation_history", methods=["GET"])
def conversation_history() -> Any:
    """Fetch the conversation history from the FAQ_Gemini backend."""

    try:
        data = _call_gemini("/conversation_history")
    except GeminiAPIError as exc:
        logging.exception("FAQ_Gemini conversation_history failed: %s", exc)
        return jsonify({"error": str(exc)}), exc.status_code

    return jsonify(data)


@bp.route("/conversation_summary", methods=["GET"])
def conversation_summary() -> Any:
    """Fetch the conversation summary from the FAQ_Gemini backend."""

    try:
        data = _call_gemini("/conversation_summary")
    except GeminiAPIError as exc:
        logging.exception("FAQ_Gemini conversation_summary failed: %s", exc)
        return jsonify({"error": str(exc)}), exc.status_code

    return jsonify(data)


@bp.route("/reset_history", methods=["POST"])
def reset_history() -> Any:
    """Request the FAQ_Gemini backend to clear the conversation history."""

    try:
        data = _call_gemini("/reset_history", method="POST")
    except GeminiAPIError as exc:
        logging.exception("FAQ_Gemini reset_history failed: %s", exc)
        return jsonify({"error": str(exc)}), exc.status_code

    return jsonify(data)


@bp.route("/iot_agent", defaults={"path": ""}, methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
@bp.route("/iot_agent/<path:path>", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
def proxy_iot_agent(path: str) -> Response:
    """Forward IoT Agent API requests to the configured upstream service."""

    return _proxy_iot_agent_request(path)


@bp.route("/chat_history", methods=["GET"])
def chat_history() -> Any:
    """Fetch the entire chat history."""
    try:
        with open("chat_history.json", "r", encoding="utf-8") as f:
            history = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        history = []
    return jsonify(history)


@bp.route("/reset_chat_history", methods=["POST"])
def reset_chat_history() -> Any:
    """Clear the chat history."""
    try:
        with open("chat_history.json", "w", encoding="utf-8") as f:
            json.dump([], f)
    except FileNotFoundError:
        pass  # File doesn't exist, nothing to clear
    return jsonify({"message": "Chat history cleared successfully."})


@bp.route("/memory")
def serve_memory_page() -> Any:
    """Serve the memory management page."""
    return render_template("memory.html")


@bp.route("/api/memory", methods=["GET", "POST"])
def api_memory() -> Any:
    """Handle memory file operations."""
    if request.method == "POST":
        data = request.get_json()
        if data is None:
            return jsonify({"error": "Invalid JSON"}), 400
        with open("long_term_memory.json", "w", encoding="utf-8") as f:
            json.dump({"memory": data.get("long_term_memory", "")}, f, ensure_ascii=False, indent=2)
        with open("short_term_memory.json", "w", encoding="utf-8") as f:
            json.dump({"memory": data.get("short_term_memory", "")}, f, ensure_ascii=False, indent=2)
        return jsonify({"message": "Memory saved successfully."})

    try:
        with open("long_term_memory.json", "r", encoding="utf-8") as f:
            long_term_memory = json.load(f).get("memory", "")
    except (FileNotFoundError, json.JSONDecodeError):
        long_term_memory = ""

    try:
        with open("short_term_memory.json", "r", encoding="utf-8") as f:
            short_term_memory = json.load(f).get("memory", "")
    except (FileNotFoundError, json.JSONDecodeError):
        short_term_memory = ""

    return jsonify({
        "long_term_memory": long_term_memory,
        "short_term_memory": short_term_memory,
    })


@bp.route("/")
def serve_index() -> Any:
    """Serve the main single-page application."""

    browser_embed_url = _resolve_browser_embed_url()
    browser_agent_client_base = _resolve_browser_agent_client_base()
    return render_template(
        "index.html",
        browser_embed_url=browser_embed_url,
        browser_agent_client_base=browser_agent_client_base,
    )


@bp.route("/<path:path>")
def serve_file(path: str) -> Any:
    """Serve any additional static files that live alongside index.html."""

    if path == "index.html":
        return serve_index()
    base_path = current_app.config.get("APP_BASE_PATH", current_app.root_path)
    return send_from_directory(base_path, path)
