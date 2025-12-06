"""Flask blueprint and HTTP routes for the Multi-Agent Platform."""

from __future__ import annotations

import datetime
import json
import logging
from typing import Any, Dict, Iterator

from flask import (
    Blueprint,
    Response,
    current_app,
    flash,
    g,
    has_request_context,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    stream_with_context,
    url_for,
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
from .iot import (
    _build_iot_agent_url,
    _fetch_iot_model_selection,
    _iter_iot_agent_bases,
    _proxy_iot_agent_request,
)
from .lifestyle import _build_lifestyle_url, _call_lifestyle, _iter_lifestyle_bases
from .scheduler import (
    _proxy_scheduler_agent_request,
    _fetch_calendar_data,
    _fetch_day_view_data,
    _fetch_routines_data,
    _fetch_scheduler_model_selection,
    _iter_scheduler_agent_bases,
    _build_scheduler_agent_url,
    _submit_day_form,
)
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
from .memory_manager import MemoryManager

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
        "scheduler": selection.get("scheduler"),
    }

    target_builders = {
        "browser": (_iter_browser_agent_bases, _build_browser_agent_url),
        "lifestyle": (_iter_lifestyle_bases, _build_lifestyle_url),
        "iot": (_iter_iot_agent_bases, _build_iot_agent_url),
        "scheduler": (_iter_scheduler_agent_bases, _build_scheduler_agent_url),
    }

    headers = {"X-Platform-Propagation": "1"}

    for agent, payload in agent_payloads.items():
        if not payload or not isinstance(payload, dict):
            continue
        iter_bases, build_url = target_builders.get(agent, (None, None))
        if not iter_bases or not build_url:
            continue
        for base in iter_bases():
            if not base or base.startswith("/"):
                continue
            # Skip localhost URLs when running in Docker (they won't resolve)
            if "localhost" in base or "127.0.0.1" in base:
                continue
            url = build_url(base, "model_settings")
            try:
                resp = requests.post(url, json=payload, timeout=2.0, headers=headers)
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
    view_name = str(payload.get("view") or payload.get("source_view") or "").strip().lower()
    log_history_requested = payload.get("log_history") is True
    log_history = log_history_requested or view_name == "general"

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
            for event in orchestrator.run_stream(message, log_history=log_history):
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
    """Proxy the rag_answer endpoint to the Life-Style backend."""

    payload = request.get_json(silent=True) or {}
    question = (payload.get("question") or "").strip()
    if not question:
        return jsonify({"error": "質問を入力してください。"}), 400

    try:
        data = _call_lifestyle("/rag_answer", method="POST", payload={"question": question})
    except LifestyleAPIError as exc:
        logging.exception("Life-Style rag_answer failed: %s", exc)
        return jsonify({"error": str(exc)}), exc.status_code

    return jsonify(data)


@bp.route("/conversation_history", methods=["GET"])
def conversation_history() -> Any:
    """Fetch the conversation history from the Life-Style backend."""

    try:
        data = _call_lifestyle("/conversation_history")
    except LifestyleAPIError as exc:
        logging.exception("Life-Style conversation_history failed: %s", exc)
        return jsonify({"error": str(exc)}), exc.status_code

    return jsonify(data)


@bp.route("/conversation_summary", methods=["GET"])
def conversation_summary() -> Any:
    """Fetch the conversation summary from the Life-Style backend."""

    try:
        data = _call_lifestyle("/conversation_summary")
    except LifestyleAPIError as exc:
        logging.exception("Life-Style conversation_summary failed: %s", exc)
        return jsonify({"error": str(exc)}), exc.status_code

    return jsonify(data)


@bp.route("/reset_history", methods=["POST"])
def reset_history() -> Any:
    """Request the Life-Style backend to clear the conversation history."""

    try:
        data = _call_lifestyle("/reset_history", method="POST")
    except LifestyleAPIError as exc:
        logging.exception("Life-Style reset_history failed: %s", exc)
        return jsonify({"error": str(exc)}), exc.status_code

    return jsonify(data)


@bp.route("/iot_agent", defaults={"path": ""}, methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
@bp.route("/iot_agent/<path:path>", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
def proxy_iot_agent(path: str) -> Response:
    """Forward IoT Agent API requests to the configured upstream service."""

    return _proxy_iot_agent_request(path)


@bp.route("/scheduler_agent", defaults={"path": ""}, methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
@bp.route("/scheduler_agent/<path:path>", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
def proxy_scheduler_agent(path: str) -> Response:
    """Forward Scheduler Agent traffic to the upstream service."""

    return _proxy_scheduler_agent_request(path)


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
        
        try:
            # Save long-term memory summary
            lt_mgr = MemoryManager("long_term_memory.json")
            lt_mem = lt_mgr.load_memory()
            lt_mem["summary_text"] = data.get("long_term_memory", "")
            lt_mgr.save_memory(lt_mem)

            # Save short-term memory summary
            st_mgr = MemoryManager("short_term_memory.json")
            st_mem = st_mgr.load_memory()
            st_mem["summary_text"] = data.get("short_term_memory", "")
            st_mgr.save_memory(st_mem)
            
            # Save settings
            save_memory_settings({"enabled": data.get("enabled")})
            
            return jsonify({"message": "Memory saved successfully."})
        except Exception as exc:
            logging.exception("Failed to save memory: %s", exc)
            return jsonify({"error": "Failed to save memory."}), 500

    try:
        lt_mgr = MemoryManager("long_term_memory.json")
        long_term_memory = lt_mgr.load_memory().get("summary_text", "")
    except Exception:
        long_term_memory = ""

    try:
        st_mgr = MemoryManager("short_term_memory.json")
        short_term_memory = st_mgr.load_memory().get("summary_text", "")
    except Exception:
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
        selection = load_model_settings()
        updates: Dict[str, Dict[str, str]] = {}
        try:
            iot_selection = _fetch_iot_model_selection()
        except Exception as exc:  # noqa: BLE001
            logging.info("Skipping IoT model pull during settings fetch: %s", exc)
            iot_selection = None
        try:
            scheduler_selection = _fetch_scheduler_model_selection()
        except Exception as exc:  # noqa: BLE001
            logging.info("Skipping Scheduler model pull during settings fetch: %s", exc)
            scheduler_selection = None

        if iot_selection and selection.get("iot") != iot_selection:
            updates["iot"] = iot_selection
        if scheduler_selection and selection.get("scheduler") != scheduler_selection:
            updates["scheduler"] = scheduler_selection
        if updates:
            try:
                selection = save_model_settings({"selection": {**selection, **updates}})
            except Exception as exc:  # noqa: BLE001
                logging.warning("Failed to persist agent model sync: %s", exc)

        return jsonify({"selection": selection, "options": get_llm_options()})

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



# Scheduler Agent UI Routes
@bp.route("/scheduler-ui")
def scheduler_index():
    today = datetime.date.today()
    year = request.args.get('year', today.year, type=int)
    month = request.args.get('month', today.month, type=int)

    try:
        data = _fetch_calendar_data(year, month)
    except ConnectionError as exc:
        logging.error("Failed to fetch calendar data: %s", exc)
        return jsonify({"error": str(exc)}), 500
    
    # Convert ISO format date strings back to datetime.date objects for Jinja
    for week in data['calendar_data']:
        for day_data in week:
            day_data['date'] = datetime.date.fromisoformat(day_data['date'])
    data['today'] = datetime.date.fromisoformat(data['today'])
    
    return render_template(
        "scheduler_index.html",
        calendar_data=data['calendar_data'],
        year=data['year'],
        month=data['month'],
        today=data['today']
    )

@bp.route("/scheduler-ui/calendar_partial")
def scheduler_calendar_partial():
    today = datetime.date.today()
    year = request.args.get('year', today.year, type=int)
    month = request.args.get('month', today.month, type=int)

    try:
        data = _fetch_calendar_data(year, month)
        
        if not isinstance(data, dict):
             raise ValueError("Invalid response format")
             
        for week in data.get('calendar_data', []):
            for day_data in week:
                if 'date' in day_data and isinstance(day_data['date'], str):
                    day_data['date'] = datetime.date.fromisoformat(day_data['date'])
        
        if 'today' in data and isinstance(data['today'], str):
            data['today'] = datetime.date.fromisoformat(data['today'])
        else:
            data['today'] = datetime.date.today()
            
    except (ConnectionError, ValueError, KeyError, TypeError) as exc:
        logging.error("Failed to fetch calendar partial data: %s", exc)
        return jsonify({"error": str(exc)}), 502
    
    return render_template(
        "scheduler_calendar_partial.html",
        calendar_data=data.get('calendar_data', []),
        today=data['today']
    )

@bp.route("/scheduler-ui/day/<date_str>", methods=["GET", "POST"])
def scheduler_day_view(date_str):
    if request.method == "POST":
        try:
            _submit_day_form(date_str, request.form)
            flash("変更を保存しました。")
        except ConnectionError as exc:
            logging.error("Failed to submit day form for %s: %s", date_str, exc)
            flash("変更の保存に失敗しました。Scheduler Agent を確認してください。")
        return redirect(url_for("multi_agent_app.scheduler_day_view", date_str=date_str))

    try:
        data = _fetch_day_view_data(date_str)
        
        if not isinstance(data, dict):
             raise ValueError("Invalid response format")

        if 'date' in data and isinstance(data['date'], str):
            data['date'] = datetime.date.fromisoformat(data['date'])
        else:
            raise KeyError("Response missing 'date'")

    except (ConnectionError, ValueError, KeyError, TypeError) as exc:
        logging.error("Failed to fetch day view data for %s: %s", date_str, exc)
        return jsonify({"error": str(exc)}), 502
    
    # Convert timeline item dates if necessary (though they are usually just strings)
    for item in data.get('timeline_items', []):
        # Assuming 'log_memo' and 'is_done' might be None or boolean
        if item.get('log_memo') is None:
            item['log_memo'] = ""
        if item.get('is_done') is None:
            item['is_done'] = False

    return render_template(
        "scheduler_day.html",
        date=data['date'],
        timeline_items=data.get('timeline_items', []),
        day_log={'content': data.get('day_log_content')} if data.get('day_log_content') else None,
        completion_rate=data.get('completion_rate', 0)
    )

@bp.route("/scheduler-ui/day/<date_str>/timeline")
def scheduler_day_view_timeline(date_str):
    try:
        data = _fetch_day_view_data(date_str)

        if not isinstance(data, dict):
             raise ValueError("Invalid response format")
             
        if 'date' in data and isinstance(data['date'], str):
            data['date'] = datetime.date.fromisoformat(data['date'])
        else:
             raise KeyError("Response missing 'date'")
             
    except (ConnectionError, ValueError, KeyError, TypeError) as exc:
        logging.error("Failed to fetch day view timeline data for %s: %s", date_str, exc)
        return jsonify({"error": str(exc)}), 502
    
    for item in data.get('timeline_items', []):
        if item.get('log_memo') is None:
            item['log_memo'] = ""
        if item.get('is_done') is None:
            item['is_done'] = False

    return render_template(
        "scheduler_timeline_partial.html",
        date=data['date'],
        timeline_items=data.get('timeline_items', []),
        completion_rate=data.get('completion_rate', 0)
    )

@bp.route("/scheduler-ui/day/<date_str>/log_partial")
def scheduler_day_view_log_partial(date_str):
    try:
        data = _fetch_day_view_data(date_str)
        if not isinstance(data, dict):
             raise ValueError("Invalid response format")
    except (ConnectionError, ValueError, KeyError, TypeError) as exc:
        logging.error("Failed to fetch day view log partial data for %s: %s", date_str, exc)
        return jsonify({"error": str(exc)}), 502
    
    return render_template(
        "scheduler_log_partial.html",
        day_log={'content': data.get('day_log_content')} if data.get('day_log_content') else None
    )

@bp.route("/scheduler-ui/routines")
def scheduler_routines_list():
    try:
        data = _fetch_routines_data()
        if not isinstance(data, dict):
             raise ValueError("Invalid response format")
    except (ConnectionError, ValueError, KeyError, TypeError) as exc:
        logging.error("Failed to fetch routines data: %s", exc)
        return jsonify({"error": str(exc)}), 502

    return render_template("scheduler_routines.html", routines=data.get('routines', []))

@bp.route("/")
def serve_index() -> Any:
    """Serve the main single-page application."""

    browser_embed_url = _resolve_browser_embed_url()
    browser_agent_client_base = _resolve_browser_agent_client_base()

    # Preload scheduler calendar data so the Scheduler view can render without the embedded iframe.
    today = datetime.date.today()
    scheduler_year = request.args.get("year", today.year, type=int)
    scheduler_month = request.args.get("month", today.month, type=int)
    scheduler_calendar_data = None
    scheduler_today = today
    scheduler_error = None

    try:
        scheduler_data = _fetch_calendar_data(scheduler_year, scheduler_month)
        for week in scheduler_data.get("calendar_data", []):
            for day_data in week:
                day_data["date"] = datetime.date.fromisoformat(day_data["date"])
        scheduler_today = datetime.date.fromisoformat(scheduler_data.get("today", today.isoformat()))
        scheduler_year = scheduler_data.get("year", scheduler_year)
        scheduler_month = scheduler_data.get("month", scheduler_month)
        scheduler_calendar_data = scheduler_data.get("calendar_data")
    except Exception as exc:  # noqa: BLE001
        logging.warning("Scheduler calendar preload skipped: %s", exc)
        scheduler_error = str(exc)

    return render_template(
        "index.html",
        browser_embed_url=browser_embed_url,
        browser_agent_client_base=browser_agent_client_base,
        scheduler_calendar_data=scheduler_calendar_data,
        scheduler_year=scheduler_year,
        scheduler_month=scheduler_month,
        scheduler_today=scheduler_today,
        scheduler_error=scheduler_error,
    )


@bp.route("/<path:path>")
def serve_file(path: str) -> Any:
    """Serve any additional static files that live alongside index.html."""

    if path == "index.html":
        return serve_index()
    base_path = current_app.config.get("APP_BASE_PATH", current_app.root_path)
    return send_from_directory(base_path, path)
