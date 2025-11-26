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

import requests

from .browser import (
    _build_browser_agent_url,
    _canonicalise_browser_agent_base,
    _iter_browser_agent_bases,
    _normalise_browser_base_values,
)
from .config import (
    _resolve_browser_agent_client_base,
    _resolve_browser_embed_url,
)
from .errors import LifestyleAPIError, OrchestratorError
from .iot import _build_iot_agent_url, _iter_iot_agent_bases, _proxy_iot_agent_request
from .lifestyle import _build_lifestyle_url, _call_lifestyle, _iter_lifestyle_bases
from .settings import (
    get_llm_options,
    load_agent_connections,
    load_model_settings,
    load_memory_settings,
    save_agent_connections,
    save_model_settings,
    save_memory_settings,
)
from .orchestrator import _get_orchestrator

bp = Blueprint("multi_agent_app", __name__)


def _format_sse_event(payload: Dict[str, Any]) -> str:
    """Serialise an SSE event line with the payload JSON."""

    event_type = str(payload.get("event") or "message").strip() or "message"
    data = json.dumps(payload, ensure_ascii=False)
    return f"event: {event_type}\ndata: {data}\n\n"


def _broadcast_model_settings(selection: Dict[str, Any]) -> None:
    """Best-effort propagation of model settings to downstream agents without restart."""

    agent_payloads = {
        "browser": selection.get("browser"),
        "lifestyle": selection.get("lifestyle"),
        "iot": selection.get("iot"),
    }

    target_builders = {
        "browser": (_iter_browser_agent_bases, _build_browser_agent_url),
        "lifestyle": (_iter_lifestyle_bases, _build_lifestyle_url),
        "iot": (_iter_iot_agent_bases, _build_iot_agent_url),
    }

    for agent, payload in agent_payloads.items():
        if not payload or not isinstance(payload, dict):
            continue
        iter_bases, build_url = target_builders.get(agent, (None, None))
        if not iter_bases or not build_url:
            continue
        for base in iter_bases():
            if not base or base.startswith("/"):
                continue
            url = build_url(base, "model_settings")
            try:
                resp = requests.post(url, json=payload, timeout=2.0)
                if not resp.ok:
                    logging.warning("Model settings push to %s failed: %s %s", url, resp.status_code, resp.text)
            except requests.exceptions.RequestException as exc:
                logging.warning("Model settings push to %s skipped (%s)", url, exc)
            except Exception as exc:  # noqa: BLE001
                logging.warning("Model settings push to %s failed: %s", url, exc)


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
    """Proxy the rag_answer endpoint to the Life-Assistant backend."""

    payload = request.get_json(silent=True) or {}
    question = (payload.get("question") or "").strip()
    if not question:
        return jsonify({"error": "質問を入力してください。"}), 400

    try:
        data = _call_lifestyle("/rag_answer", method="POST", payload={"question": question})
    except LifestyleAPIError as exc:
        logging.exception("Life-Assistant rag_answer failed: %s", exc)
        return jsonify({"error": str(exc)}), exc.status_code

    return jsonify(data)


@bp.route("/conversation_history", methods=["GET"])
def conversation_history() -> Any:
    """Fetch the conversation history from the Life-Assistant backend."""

    try:
        data = _call_lifestyle("/conversation_history")
    except LifestyleAPIError as exc:
        logging.exception("Life-Assistant conversation_history failed: %s", exc)
        return jsonify({"error": str(exc)}), exc.status_code

    return jsonify(data)


@bp.route("/conversation_summary", methods=["GET"])
def conversation_summary() -> Any:
    """Fetch the conversation summary from the Life-Assistant backend."""

    try:
        data = _call_lifestyle("/conversation_summary")
    except LifestyleAPIError as exc:
        logging.exception("Life-Assistant conversation_summary failed: %s", exc)
        return jsonify({"error": str(exc)}), exc.status_code

    return jsonify(data)


@bp.route("/reset_history", methods=["POST"])
def reset_history() -> Any:
    """Request the Life-Assistant backend to clear the conversation history."""

    try:
        data = _call_lifestyle("/reset_history", method="POST")
    except LifestyleAPIError as exc:
        logging.exception("Life-Assistant reset_history failed: %s", exc)
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
    """Handle memory file operations and settings."""
    if request.method == "POST":
        data = request.get_json()
        if data is None:
            return jsonify({"error": "Invalid JSON"}), 400
        
        # Save content
        with open("long_term_memory.json", "w", encoding="utf-8") as f:
            json.dump({"memory": data.get("long_term_memory", "")}, f, ensure_ascii=False, indent=2)
        with open("short_term_memory.json", "w", encoding="utf-8") as f:
            json.dump({"memory": data.get("short_term_memory", "")}, f, ensure_ascii=False, indent=2)
            
        # Save settings
        save_memory_settings({"enabled": data.get("enabled")})
        
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

    settings = load_memory_settings()

    return jsonify({
        "long_term_memory": long_term_memory,
        "short_term_memory": short_term_memory,
        "enabled": settings.get("enabled", True),
    })


@bp.route("/api/agent_connections", methods=["GET", "POST"])
def api_agent_connections() -> Any:
    """Load or persist the agent connection toggles."""
    if request.method == "GET":
        return jsonify(load_agent_connections())

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Invalid JSON"}), 400

    try:
        saved = save_agent_connections(data)
    except Exception as exc:  # noqa: BLE001
        logging.exception("Failed to save agent connection settings: %s", exc)
        return jsonify({"error": "設定の保存に失敗しました。"}), 500

    return jsonify(saved)


@bp.route("/api/model_settings", methods=["GET", "POST"])
def api_model_settings() -> Any:
    """Expose and persist LLM model preferences per agent."""

    if request.method == "GET":
        return jsonify({"selection": load_model_settings(), "options": get_llm_options()})

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Invalid JSON"}), 400

    try:
        saved = save_model_settings(data)
        _broadcast_model_settings(saved)
    except Exception as exc:  # noqa: BLE001
        logging.exception("Failed to save model settings: %s", exc)
        return jsonify({"error": "モデル設定の保存に失敗しました。"}), 500

    return jsonify({"selection": saved, "options": get_llm_options()})


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
