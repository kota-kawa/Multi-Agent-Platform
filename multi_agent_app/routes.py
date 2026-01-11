"""FastAPI router and HTTP routes for Symphony."""

from __future__ import annotations

import datetime
import json
import logging
import asyncio
from urllib.parse import quote
from typing import Any, Dict, AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import (
    Response,
    JSONResponse,
    StreamingResponse,
    RedirectResponse,
    FileResponse,
)
import httpx

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
from .history import _read_chat_history, _reset_chat_history
from .iot import (
    _build_iot_agent_url,
    _fetch_iot_model_selection,
    _iter_iot_agent_bases,
    _proxy_iot_agent_request,
)
from .lifestyle import (
    _build_lifestyle_url,
    _call_lifestyle,
    _iter_lifestyle_bases,
    _proxy_lifestyle_agent_request,
)
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
from .agent_status import get_agent_status
from .memory_manager import MemoryManager
from .request_context import set_browser_agent_bases, reset_browser_agent_bases

router = APIRouter()


def _format_sse_event(payload: Dict[str, Any]) -> str:
    """Serialise an SSE event line with the payload JSON."""

    event_type = str(payload.get("event") or "message").strip() or "message"
    data = json.dumps(payload, ensure_ascii=False)
    return f"event: {event_type}\ndata: {data}\n\n"


def _flash_messages(request: Request) -> list[str]:
    message = request.query_params.get("flash")
    if message:
        return [message]
    return []


async def _broadcast_model_settings(selection: Dict[str, Any]) -> None:
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

    async with httpx.AsyncClient(timeout=2.0) as client:
        for agent, payload in agent_payloads.items():
            if not payload or not isinstance(payload, dict):
                continue
            iter_bases, build_url = target_builders.get(agent, (None, None))
            if not iter_bases or not build_url:
                continue
            for base in iter_bases():
                if not base or base.startswith("/"):
                    continue
                if "localhost" in base or "127.0.0.1" in base:
                    continue
                url = build_url(base, "model_settings")
                try:
                    resp = await client.post(url, json=payload, headers=headers)
                    if not resp.is_success:
                        logging.warning(
                            "Model settings push to %s failed: %s %s", url, resp.status_code, resp.text
                        )
                except httpx.RequestError as exc:
                    logging.warning("Model settings push to %s skipped (%s)", url, exc)
                except Exception as exc:  # noqa: BLE001
                    logging.warning("Model settings push to %s failed: %s", url, exc)


@router.post("/orchestrator/chat", name="multi_agent_app.orchestrator_chat")
async def orchestrator_chat(request: Request) -> Response:
    """Handle orchestrator chat requests originating from the General view."""

    try:
        payload = await request.json()
    except Exception:
        payload = {}

    message = (payload.get("message") or "").strip()
    if not message:
        return JSONResponse({"error": "メッセージを入力してください。"}, status_code=400)

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

    try:
        orchestrator = _get_orchestrator()
    except OrchestratorError as exc:
        logging.exception("Orchestrator initialisation failed: %s", exc)
        error_message = str(exc)

        async def _error_stream(message_text: str) -> AsyncIterator[str]:
            yield _format_sse_event({"event": "error", "error": message_text})

        return StreamingResponse(
            _error_stream(error_message),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )

    async def _stream() -> AsyncIterator[str]:
        token = set_browser_agent_bases(overrides)
        try:
            async for event in orchestrator.run_stream(message, log_history=log_history):
                yield _format_sse_event(event)
        except OrchestratorError as exc:  # pragma: no cover - defensive
            logging.exception("Orchestrator execution failed: %s", exc)
            yield _format_sse_event({"event": "error", "error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            logging.exception("Unexpected orchestrator failure: %s", exc)
            yield _format_sse_event({"event": "error", "error": "内部エラーが発生しました。"})
        finally:
            reset_browser_agent_bases(token)

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )


@router.post("/rag_answer", name="multi_agent_app.rag_answer")
async def rag_answer(request: Request) -> Any:
    """Proxy the rag_answer endpoint to the Life-Style backend."""

    try:
        payload = await request.json()
    except Exception:
        payload = {}
    question = (payload.get("question") or "").strip()
    if not question:
        return JSONResponse({"error": "質問を入力してください。"}, status_code=400)

    status = await get_agent_status()
    lifestyle_status = (status.get("agents") or {}).get("lifestyle", {})
    if not lifestyle_status.get("available", True):
        message = "Life-Styleエージェントに接続できないため回答できません。"
        return {
            "status": "unavailable",
            "answer": "",
            "message": message,
            "error": lifestyle_status.get("error"),
        }

    try:
        data = await _call_lifestyle("/rag_answer", method="POST", payload={"question": question})
    except LifestyleAPIError as exc:
        logging.exception("Life-Style rag_answer failed: %s", exc)
        message = "Life-Styleエージェントに接続できないため回答できません。"
        return {"status": "unavailable", "answer": "", "message": message, "error": str(exc)}

    return data


@router.get("/conversation_history", name="multi_agent_app.conversation_history")
async def conversation_history() -> Any:
    """Fetch the conversation history from the Life-Style backend."""

    status = await get_agent_status()
    lifestyle_status = (status.get("agents") or {}).get("lifestyle", {})
    if not lifestyle_status.get("available", True):
        message = "Life-Styleエージェントに接続できないため履歴を取得できません。"
        return {
            "status": "unavailable",
            "conversation_history": [],
            "message": message,
            "error": lifestyle_status.get("error"),
        }

    try:
        data = await _call_lifestyle("/conversation_history")
    except LifestyleAPIError as exc:
        logging.exception("Life-Style conversation_history failed: %s", exc)
        message = "Life-Styleエージェントに接続できないため履歴を取得できません。"
        return {"status": "unavailable", "conversation_history": [], "message": message, "error": str(exc)}

    return data


@router.get("/conversation_summary", name="multi_agent_app.conversation_summary")
async def conversation_summary() -> Any:
    """Fetch the conversation summary from the Life-Style backend."""

    status = await get_agent_status()
    lifestyle_status = (status.get("agents") or {}).get("lifestyle", {})
    if not lifestyle_status.get("available", True):
        message = "Life-Styleエージェントに接続できないため要約を取得できません。"
        return {
            "status": "unavailable",
            "summary": "",
            "message": message,
            "error": lifestyle_status.get("error"),
        }

    try:
        data = await _call_lifestyle("/conversation_summary")
    except LifestyleAPIError as exc:
        logging.exception("Life-Style conversation_summary failed: %s", exc)
        message = "Life-Styleエージェントに接続できないため要約を取得できません。"
        return {"status": "unavailable", "summary": "", "message": message, "error": str(exc)}

    return data


@router.post("/reset_history", name="multi_agent_app.reset_history")
async def reset_history() -> Any:
    """Request the Life-Style backend to clear the conversation history."""

    status = await get_agent_status()
    lifestyle_status = (status.get("agents") or {}).get("lifestyle", {})
    if not lifestyle_status.get("available", True):
        message = "Life-Styleエージェントに接続できないため履歴をリセットできません。"
        return {"status": "unavailable", "message": message, "error": lifestyle_status.get("error")}

    try:
        data = await _call_lifestyle("/reset_history", method="POST")
    except LifestyleAPIError as exc:
        logging.exception("Life-Style reset_history failed: %s", exc)
        message = "Life-Styleエージェントに接続できないため履歴をリセットできません。"
        return {"status": "unavailable", "message": message, "error": str(exc)}

    return data


@router.api_route(
    "/lifestyle_agent",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    name="multi_agent_app.proxy_lifestyle_agent",
)
@router.api_route(
    "/lifestyle_agent/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    name="multi_agent_app.proxy_lifestyle_agent",
)
async def proxy_lifestyle_agent(request: Request, path: str = "") -> Response:
    """Forward Life-Style Agent traffic to the upstream service."""

    return await _proxy_lifestyle_agent_request(request, path)


@router.api_route(
    "/iot_agent",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    name="multi_agent_app.proxy_iot_agent",
)
@router.api_route(
    "/iot_agent/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    name="multi_agent_app.proxy_iot_agent",
)
async def proxy_iot_agent(request: Request, path: str = "") -> Response:
    """Forward IoT Agent API requests to the configured upstream service."""

    return await _proxy_iot_agent_request(request, path)


@router.api_route(
    "/scheduler_agent",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    name="multi_agent_app.proxy_scheduler_agent",
)
@router.api_route(
    "/scheduler_agent/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    name="multi_agent_app.proxy_scheduler_agent",
)
async def proxy_scheduler_agent(request: Request, path: str = "") -> Response:
    """Forward Scheduler Agent traffic to the upstream service."""

    return await _proxy_scheduler_agent_request(request, path)


@router.get("/chat_history", name="multi_agent_app.chat_history")
async def chat_history() -> Any:
    """Fetch the entire chat history."""
    return _read_chat_history()


@router.post("/reset_chat_history", name="multi_agent_app.reset_chat_history")
async def reset_chat_history() -> Any:
    """Clear the chat history."""
    _reset_chat_history()
    return {"message": "Chat history cleared successfully."}


@router.get("/memory", name="multi_agent_app.serve_memory_page")
async def serve_memory_page(request: Request) -> Response:
    """Serve the memory management page."""
    templates = request.app.state.templates
    return templates.TemplateResponse("memory.html", {"request": request})


@router.api_route("/api/memory", methods=["GET", "POST"], name="multi_agent_app.api_memory")
async def api_memory(request: Request) -> Any:
    """Handle memory file operations and settings."""
    if request.method == "POST":
        try:
            data = await request.json()
        except Exception:
            data = None
        if data is None:
            return JSONResponse({"error": "Invalid JSON"}, status_code=400)
        
        try:
            # Full structure update (preferred for new UI)
            long_term_full = data.get("long_term_full")
            short_term_full = data.get("short_term_full")
            
            # Legacy text update
            long_term_data = data.get("long_term_memory")
            short_term_data = data.get("short_term_memory")

            if long_term_full is not None:
                lt_mgr = MemoryManager("long_term_memory.json")
                lt_mgr.save_memory(long_term_full)
            elif long_term_data is not None:
                lt_mgr = MemoryManager("long_term_memory.json")
                lt_mgr.replace_with_user_payload(long_term_data)

            if short_term_full is not None:
                st_mgr = MemoryManager("short_term_memory.json")
                st_mgr.save_memory(short_term_full)
            elif short_term_data is not None:
                st_mgr = MemoryManager("short_term_memory.json")
                st_mgr.replace_with_user_payload(short_term_data)

            # Save settings
            save_memory_settings({
                "enabled": data.get("enabled"),
                "history_sync_enabled": data.get("history_sync_enabled"),
                "short_term_ttl_minutes": data.get("short_term_ttl_minutes"),
                "short_term_grace_minutes": data.get("short_term_grace_minutes"),
                "short_term_active_task_hold_minutes": data.get("short_term_active_task_hold_minutes"),
                "short_term_promote_score": data.get("short_term_promote_score"),
                "short_term_promote_importance": data.get("short_term_promote_importance"),
            })
            
            return {"message": "Memory saved successfully."}
        except Exception as exc:
            logging.exception("Failed to save memory: %s", exc)
            return JSONResponse({"error": "Failed to save memory."}, status_code=500)

    try:
        lt_mgr = MemoryManager("long_term_memory.json")
        lt_mem = lt_mgr.load_memory()
        # Return both legacy format and new format for compatibility
        long_term_memory = lt_mem.get("summary_text", "")
        long_term_categories = lt_mem.get("category_summaries", {})
        long_term_titles = lt_mem.get("category_titles", {})
    except Exception:
        lt_mem = {}
        long_term_memory = ""
        long_term_categories = {}
        long_term_titles = {}

    try:
        st_mgr = MemoryManager("short_term_memory.json")
        st_mem = st_mgr.load_memory()
        short_term_memory = st_mem.get("summary_text", "")
        short_term_categories = st_mem.get("category_summaries", {})
        short_term_titles = st_mem.get("category_titles", {})
    except Exception:
        st_mem = {}
        short_term_memory = ""
        short_term_categories = {}
        short_term_titles = {}

    settings = load_memory_settings()

    return {
        "long_term_full": lt_mem,
        "short_term_full": st_mem,
        "long_term_memory": long_term_memory,
        "short_term_memory": short_term_memory,
        "long_term_categories": long_term_categories,
        "short_term_categories": short_term_categories,
        "long_term_titles": long_term_titles,
        "short_term_titles": short_term_titles,
        "enabled": settings.get("enabled", True),
        "history_sync_enabled": settings.get("history_sync_enabled", True),
        "short_term_ttl_minutes": settings.get("short_term_ttl_minutes"),
        "short_term_grace_minutes": settings.get("short_term_grace_minutes"),
        "short_term_active_task_hold_minutes": settings.get("short_term_active_task_hold_minutes"),
        "short_term_promote_score": settings.get("short_term_promote_score"),
        "short_term_promote_importance": settings.get("short_term_promote_importance"),
    }


@router.api_route("/api/agent_connections", methods=["GET", "POST"], name="multi_agent_app.api_agent_connections")
async def api_agent_connections(request: Request) -> Any:
    """Load or persist the agent connection toggles."""
    if request.method == "GET":
        return load_agent_connections()

    try:
        data = await request.json()
    except Exception:
        data = None
    if not isinstance(data, dict):
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    try:
        saved = save_agent_connections(data)
    except Exception as exc:  # noqa: BLE001
        logging.exception("Failed to save agent connection settings: %s", exc)
        return JSONResponse({"error": "設定の保存に失敗しました。"}, status_code=500)

    return saved


@router.get("/api/agent_status", name="multi_agent_app.api_agent_status")
async def api_agent_status() -> Any:
    """Return the latest agent connectivity status."""
    return await get_agent_status(force=True)


@router.api_route("/api/model_settings", methods=["GET", "POST"], name="multi_agent_app.api_model_settings")
async def api_model_settings(request: Request) -> Any:
    """Expose and persist LLM model preferences per agent."""

    if request.method == "GET":
        selection = load_model_settings()
        updates: Dict[str, Dict[str, str]] = {}

        iot_result, scheduler_result = await asyncio.gather(
            _fetch_iot_model_selection(),
            _fetch_scheduler_model_selection(),
            return_exceptions=True,
        )

        iot_selection = None
        scheduler_selection = None
        if isinstance(iot_result, Exception):
            logging.info("Skipping IoT model pull during settings fetch: %s", iot_result)
        else:
            iot_selection = iot_result

        if isinstance(scheduler_result, Exception):
            logging.info("Skipping Scheduler model pull during settings fetch: %s", scheduler_result)
        else:
            scheduler_selection = scheduler_result

        if iot_selection and selection.get("iot") != iot_selection:
            updates["iot"] = iot_selection
        if scheduler_selection and selection.get("scheduler") != scheduler_selection:
            updates["scheduler"] = scheduler_selection
        if updates:
            try:
                selection = save_model_settings({"selection": {**selection, **updates}})
            except Exception as exc:  # noqa: BLE001
                logging.warning("Failed to persist agent model sync: %s", exc)

        return {"selection": selection, "options": get_llm_options()}

    try:
        data = await request.json()
    except Exception:
        data = None
    if not isinstance(data, dict):
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    try:
        saved = save_model_settings(data)
        await _broadcast_model_settings(saved)
    except Exception as exc:  # noqa: BLE001
        logging.exception("Failed to save model settings: %s", exc)
        return JSONResponse({"error": "モデル設定の保存に失敗しました。"}, status_code=500)

    return {"selection": saved, "options": get_llm_options()}



# Scheduler Agent UI Routes
@router.get("/scheduler-ui", name="multi_agent_app.scheduler_index")
async def scheduler_index(request: Request) -> Response:
    today = datetime.date.today()
    try:
        year = int(request.query_params.get("year", today.year))
    except (TypeError, ValueError):
        year = today.year
    try:
        month = int(request.query_params.get("month", today.month))
    except (TypeError, ValueError):
        month = today.month

    templates = request.app.state.templates
    flash_messages = _flash_messages(request)

    try:
        data = await _fetch_calendar_data(year, month)
    except ConnectionError as exc:
        logging.info("Scheduler calendar fetch skipped (agent unavailable): %s", exc)
        status_message = "Scheduler エージェントに接続できないためカレンダーを表示できません。"
        return templates.TemplateResponse(
            "scheduler_index.html",
            {
                "request": request,
                "calendar_data": [],
                "year": year,
                "month": month,
                "today": datetime.date.today(),
                "status_message": status_message,
                "flash_messages": flash_messages,
            },
        )
    
    # Convert ISO format date strings back to datetime.date objects for Jinja
    for week in data['calendar_data']:
        for day_data in week:
            day_data['date'] = datetime.date.fromisoformat(day_data['date'])
    data['today'] = datetime.date.fromisoformat(data['today'])
    
    return templates.TemplateResponse(
        "scheduler_index.html",
        {
            "request": request,
            "calendar_data": data["calendar_data"],
            "year": data["year"],
            "month": data["month"],
            "today": data["today"],
            "status_message": None,
            "flash_messages": flash_messages,
        },
    )

@router.get("/scheduler-ui/calendar_partial", name="multi_agent_app.scheduler_calendar_partial")
async def scheduler_calendar_partial(request: Request) -> Response:
    today = datetime.date.today()
    try:
        year = int(request.query_params.get("year", today.year))
    except (TypeError, ValueError):
        year = today.year
    try:
        month = int(request.query_params.get("month", today.month))
    except (TypeError, ValueError):
        month = today.month

    templates = request.app.state.templates

    try:
        data = await _fetch_calendar_data(year, month)
        
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
        logging.info("Scheduler calendar partial fetch skipped (agent unavailable): %s", exc)
        status_message = "Scheduler エージェントに接続できないためカレンダーを表示できません。"
        return templates.TemplateResponse(
            "scheduler_calendar_partial.html",
            {
                "request": request,
                "calendar_data": [],
                "today": datetime.date.today(),
                "status_message": status_message,
            },
        )
    
    return templates.TemplateResponse(
        "scheduler_calendar_partial.html",
        {
            "request": request,
            "calendar_data": data.get("calendar_data", []),
            "today": data["today"],
            "status_message": None,
        },
    )

@router.api_route("/scheduler-ui/day/{date_str}", methods=["GET", "POST"], name="multi_agent_app.scheduler_day_view")
async def scheduler_day_view(request: Request, date_str: str) -> Response:
    templates = request.app.state.templates
    flash_messages = _flash_messages(request)

    if request.method == "POST":
        try:
            form_data = await request.form()
            await _submit_day_form(date_str, form_data)
            message = "変更を保存しました。"
        except ConnectionError as exc:
            logging.error("Failed to submit day form for %s: %s", date_str, exc)
            message = "変更の保存に失敗しました。Scheduler Agent を確認してください。"
        redirect_url = f"/scheduler-ui/day/{date_str}?flash={quote(message)}"
        return RedirectResponse(redirect_url, status_code=303)

    try:
        data = await _fetch_day_view_data(date_str)
        
        if not isinstance(data, dict):
             raise ValueError("Invalid response format")

        if 'date' in data and isinstance(data['date'], str):
            data['date'] = datetime.date.fromisoformat(data['date'])
        else:
            raise KeyError("Response missing 'date'")

    except (ConnectionError, ValueError, KeyError, TypeError) as exc:
        logging.error("Failed to fetch day view data for %s: %s", date_str, exc)
        return JSONResponse({"error": str(exc)}, status_code=502)
    
    # Convert timeline item dates if necessary (though they are usually just strings)
    for item in data.get('timeline_items', []):
        # Assuming 'log_memo' and 'is_done' might be None or boolean
        if item.get('log_memo') is None:
            item['log_memo'] = ""
        if item.get('is_done') is None:
            item['is_done'] = False

    return templates.TemplateResponse(
        "scheduler_day.html",
        {
            "request": request,
            "date": data["date"],
            "timeline_items": data.get("timeline_items", []),
            "day_log": {"content": data.get("day_log_content")} if data.get("day_log_content") else None,
            "completion_rate": data.get("completion_rate", 0),
            "flash_messages": flash_messages,
        },
    )

@router.get("/scheduler-ui/day/{date_str}/timeline", name="multi_agent_app.scheduler_day_view_timeline")
async def scheduler_day_view_timeline(request: Request, date_str: str) -> Response:
    templates = request.app.state.templates
    try:
        data = await _fetch_day_view_data(date_str)

        if not isinstance(data, dict):
             raise ValueError("Invalid response format")
             
        if 'date' in data and isinstance(data['date'], str):
            data['date'] = datetime.date.fromisoformat(data['date'])
        else:
             raise KeyError("Response missing 'date'")
             
    except (ConnectionError, ValueError, KeyError, TypeError) as exc:
        logging.error("Failed to fetch day view timeline data for %s: %s", date_str, exc)
        return JSONResponse({"error": str(exc)}, status_code=502)
    
    for item in data.get('timeline_items', []):
        if item.get('log_memo') is None:
            item['log_memo'] = ""
        if item.get('is_done') is None:
            item['is_done'] = False

    return templates.TemplateResponse(
        "scheduler_timeline_partial.html",
        {
            "request": request,
            "date": data["date"],
            "timeline_items": data.get("timeline_items", []),
            "completion_rate": data.get("completion_rate", 0),
        },
    )

@router.get("/scheduler-ui/day/{date_str}/log_partial", name="multi_agent_app.scheduler_day_view_log_partial")
async def scheduler_day_view_log_partial(request: Request, date_str: str) -> Response:
    templates = request.app.state.templates
    try:
        data = await _fetch_day_view_data(date_str)
        if not isinstance(data, dict):
            raise ValueError("Invalid response format")
    except (ConnectionError, ValueError, KeyError, TypeError) as exc:
        logging.error("Failed to fetch day view log partial data for %s: %s", date_str, exc)
        return JSONResponse({"error": str(exc)}, status_code=502)

    return templates.TemplateResponse(
        "scheduler_log_partial.html",
        {
            "request": request,
            "day_log": {"content": data.get("day_log_content")} if data.get("day_log_content") else None,
        },
    )

@router.get("/scheduler-ui/routines", name="multi_agent_app.scheduler_routines_list")
async def scheduler_routines_list(request: Request) -> Response:
    templates = request.app.state.templates
    flash_messages = _flash_messages(request)
    try:
        data = await _fetch_routines_data()
        if not isinstance(data, dict):
            raise ValueError("Invalid response format")
    except (ConnectionError, ValueError, KeyError, TypeError) as exc:
        logging.error("Failed to fetch routines data: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=502)

    return templates.TemplateResponse(
        "scheduler_routines.html",
        {"request": request, "routines": data.get("routines", []), "flash_messages": flash_messages},
    )

@router.get("/", name="multi_agent_app.serve_index")
async def serve_index(request: Request) -> Response:
    """Serve the main single-page application."""

    templates = request.app.state.templates
    browser_embed_url = _resolve_browser_embed_url()
    browser_agent_client_base = _resolve_browser_agent_client_base()

    today = datetime.date.today()
    try:
        scheduler_year = int(request.query_params.get("year", today.year))
    except (TypeError, ValueError):
        scheduler_year = today.year
    try:
        scheduler_month = int(request.query_params.get("month", today.month))
    except (TypeError, ValueError):
        scheduler_month = today.month
    scheduler_calendar_data = None
    scheduler_today = today
    scheduler_error = None

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "browser_embed_url": browser_embed_url,
            "browser_agent_client_base": browser_agent_client_base,
            "scheduler_calendar_data": scheduler_calendar_data,
            "scheduler_year": scheduler_year,
            "scheduler_month": scheduler_month,
            "scheduler_today": scheduler_today,
            "scheduler_error": scheduler_error,
        },
    )


@router.get("/{path:path}", name="multi_agent_app.serve_file")
async def serve_file(request: Request, path: str) -> Response:
    """Serve any additional static files that live alongside index.html."""

    if path == "index.html":
        return await serve_index(request)
    base_path = request.app.state.base_dir
    candidate = (base_path / path).resolve()
    if not str(candidate).startswith(str(base_path.resolve())):
        return JSONResponse({"error": "Not Found"}, status_code=404)
    if not candidate.exists() or not candidate.is_file():
        return JSONResponse({"error": "Not Found"}, status_code=404)
    return FileResponse(candidate)
