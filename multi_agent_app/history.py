"Chat history helpers shared between routes and the orchestrator."

from __future__ import annotations

import json
import os
import logging
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from .browser import _call_browser_agent_chat, _call_browser_agent_history_check
from .errors import BrowserAgentError, LifestyleAPIError, IotAgentError, SchedulerAgentError
from .lifestyle import _call_lifestyle
from .iot import _call_iot_agent_command, _call_iot_agent_conversation_review
from .scheduler import _call_scheduler_agent_conversation_review
from .settings import load_memory_settings
from .memory_manager import MemoryManager, get_memory_llm
from .agent_status import get_agent_availability

_browser_history_supported = True
_PRIMARY_CHAT_HISTORY_PATH = Path("chat_history.json")
_FALLBACK_CHAT_HISTORY_PATH = Path("var/chat_history.json")
_LEGACY_CHAT_HISTORY_PATHS = [Path("instance/chat_history.json")]

# Consolidation cadence: short-term is updated every turn; long-term is only
# consolidated after a few short-term refreshes to keep roles distinct.
_SHORT_TO_LONG_THRESHOLD = 3
_short_updates_since_last_long = 0
_short_update_lock = threading.Lock()


def _load_chat_history(prefer_fallback: bool = True) -> tuple[List[Dict[str, Any]], Path]:
    """Return chat history and the path it was loaded from with permission-aware fallbacks."""

    candidates = (
        [_FALLBACK_CHAT_HISTORY_PATH, _PRIMARY_CHAT_HISTORY_PATH]
        if prefer_fallback
        else [_PRIMARY_CHAT_HISTORY_PATH, _FALLBACK_CHAT_HISTORY_PATH]
    )
    for legacy_path in _LEGACY_CHAT_HISTORY_PATHS:
        if legacy_path not in candidates:
            candidates.append(legacy_path)
    last_error: Exception | None = None

    for path in candidates:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data, path
            logging.warning("Chat history at %s was not a list. Resetting.", path)
            return [], path
        except FileNotFoundError:
            continue
        except json.JSONDecodeError:
            logging.warning("Chat history JSON invalid at %s; resetting file.", path)
            return [], path
        except PermissionError as exc:
            last_error = exc
            logging.warning("Chat history not readable at %s: %s", path, exc)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            logging.warning("Unexpected error reading chat history at %s: %s", path, exc)

    if last_error:
        logging.warning("Falling back to empty chat history due to previous errors: %s", last_error)

    fallback_path = candidates[0] if candidates else _PRIMARY_CHAT_HISTORY_PATH
    return [], fallback_path


def _write_chat_history(history: List[Dict[str, Any]], preferred_path: Path | None = None) -> Path:
    """Persist chat history to the first writable path, preferring the provided path."""

    candidate_paths: list[Path] = []
    if preferred_path:
        # Avoid noisy failures when the current file exists but is not writable.
        if not (preferred_path.exists() and not os.access(preferred_path, os.W_OK)):
            candidate_paths.append(preferred_path)
    candidate_paths.extend([_FALLBACK_CHAT_HISTORY_PATH, _PRIMARY_CHAT_HISTORY_PATH])

    seen: set[Path] = set()
    last_error: Exception | None = None

    for path in candidate_paths:
        if path in seen:
            continue
        seen.add(path)

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(history, f, ensure_ascii=False, indent=2)

            # Best-effort mirror to other known locations for compatibility.
            mirror_targets = [_PRIMARY_CHAT_HISTORY_PATH, _FALLBACK_CHAT_HISTORY_PATH]
            for mirror in mirror_targets:
                if mirror == path:
                    continue
                try:
                    if mirror.exists() and not os.access(mirror, os.W_OK):
                        continue
                    mirror.parent.mkdir(parents=True, exist_ok=True)
                    with open(mirror, "w", encoding="utf-8") as mf:
                        json.dump(history, mf, ensure_ascii=False, indent=2)
                except Exception as exc:  # noqa: BLE001
                    logging.debug("Skipping mirror write to %s: %s", mirror, exc)

            return path
        except PermissionError as exc:
            last_error = exc
            logging.error("Failed to write chat history to %s: %s", path, exc)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            logging.error("Unexpected error writing chat history to %s: %s", path, exc)

    if last_error:
        raise last_error
    raise RuntimeError("Unable to write chat history to any candidate path.")


def _read_chat_history(limit: int | None = None) -> List[Dict[str, Any]]:
    """Public helper to read chat history with fallbacks."""

    history, _ = _load_chat_history()
    if not isinstance(history, list):
        return []
    if limit is None:
        return history
    return history[-limit:]


def _reset_chat_history() -> None:
    """Reset chat history, preferring the writable fallback path."""

    _write_chat_history([], preferred_path=_FALLBACK_CHAT_HISTORY_PATH)


def _append_agent_reply(agent_label: str, reply: str) -> None:
    """Append an agent reply to chat_history without triggering another broadcast."""

    if not reply:
        return

    safe_label = agent_label.strip() or "Agent"
    content = f"[{safe_label}] {reply}"
    _append_to_chat_history(
        "assistant",
        content,
        broadcast=False,
        metadata={
            "is_conversation_analysis": True,
            "analysis_agent": safe_label,
        },
    )


def _extract_reply(agent_label: str, response: Optional[Dict[str, str]]) -> bool:
    """Extract reply fields from an agent response and log them to history."""

    if not isinstance(response, dict):
        return False

    should_reply = response.get("should_reply")
    reply = response.get("reply") or ""
    addressed_agents = response.get("addressed_agents")

    reply_is_meaningful = bool(reply.strip())
    if should_reply is True and reply_is_meaningful:
        _append_agent_reply(agent_label, reply.strip())
        return True

    execution_reply = response.get("execution_reply") or ""
    if isinstance(execution_reply, str) and execution_reply.strip():
        _append_agent_reply(agent_label, execution_reply.strip())
        return True

    # Allow agents to opt-in even without explicit flag if they provided text.
    if reply_is_meaningful:
        _append_agent_reply(agent_label, reply.strip())
        return True

    # If the model named another agent but forgot to include text, skip.
    if isinstance(addressed_agents, list) and addressed_agents:
        # No free-text to log, so ignore.
        return False

    return False


def _get_memory_llm():
    """Delegate to the shared memory LLM factory."""

    return get_memory_llm()


def _refresh_memory(memory_kind: str, recent_history: List[Dict[str, str]]) -> None:
    """Update short- or long-term memory by reconciling recent history with the current store."""

    global _short_updates_since_last_long  # noqa: PLW0603

    settings = load_memory_settings()
    if not settings.get("enabled", True):
        return

    llm = _get_memory_llm()
    if llm is None:
        return

    normalized_history: List[Dict[str, str]] = [
        {"role": entry.get("role"), "content": entry.get("content")}
        for entry in recent_history
        if isinstance(entry, dict)
        and isinstance(entry.get("role"), str)
        and isinstance(entry.get("content"), str)
        and str(entry.get("content")).strip()
    ]

    if not normalized_history:
        return

    if memory_kind == "short":
        memory_path = "short_term_memory.json"
    else:
        memory_path = "long_term_memory.json"

    manager = MemoryManager(memory_path)
    try:
        snapshot = manager.consolidate_memory(
            normalized_history,
            memory_kind="short" if memory_kind == "short" else "long",
            llm=llm,
        )
        if memory_kind == "short":
            with _short_update_lock:
                _short_updates_since_last_long += 1
        else:
            with _short_update_lock:
                _short_updates_since_last_long = 0
        return snapshot
    except Exception as exc:  # noqa: BLE001
        logging.warning("Memory consolidation (%s) failed: %s", memory_kind, exc)
        return None


def _consolidate_short_into_long(recent_history: List[Dict[str, str]]) -> None:
    """Persist short-term highlights into long-term memory, then reset short memory."""

    global _short_updates_since_last_long  # noqa: PLW0603

    llm = _get_memory_llm()
    if llm is None:
        return

    short_manager = MemoryManager("short_term_memory.json")
    short_snapshot = short_manager.load_memory()

    long_manager = MemoryManager("long_term_memory.json")
    try:
        long_manager.consolidate_memory(
            recent_history,
            memory_kind="long",
            llm=llm,
            short_snapshot=short_snapshot,
        )
        short_manager.reset_short_memory(preserve_active_task=True)
        with _short_update_lock:
            _short_updates_since_last_long = 0
    except Exception as exc:  # noqa: BLE001
        logging.warning("Short->Long consolidation failed: %s", exc)


def _handle_agent_responses(
    responses: Dict[str, Dict[str, Any]],
    normalized_history: List[Dict[str, str]],
    had_reply: bool,
    response_order: List[str],
) -> None:
    """Inspect agent responses and decide whether to act or lightly acknowledge."""

    if not responses and not had_reply:
        return

    action_requests: list[dict[str, Any]] = []

    browser_response = responses.get("Browser")
    if isinstance(browser_response, dict):
        needs_action = browser_response.get("needs_action")
        task_description = browser_response.get("task_description")
        action_taken = browser_response.get("action_taken")
        if needs_action and task_description and not action_taken:
            action_requests.append(
                {
                    "agent": "Browser",
                    "kind": "browser_task",
                    "description": str(task_description),
                }
            )

    iot_response = responses.get("IoT")
    if isinstance(iot_response, dict):
        analysis = iot_response.get("analysis") if isinstance(iot_response.get("analysis"), dict) else {}
        action_required = analysis.get("action_required") if isinstance(analysis, dict) else None
        if action_required is None:
            action_required = iot_response.get("action_required")
        action_taken = bool(iot_response.get("action_taken"))

        suggested_commands = analysis.get("suggested_device_commands") if isinstance(analysis, dict) else None
        executed_commands = analysis.get("executed_commands") if isinstance(analysis, dict) else None
        device_commands = suggested_commands or executed_commands or iot_response.get("device_commands") or []

        execution_reply = iot_response.get("execution_reply")
        if action_taken and isinstance(execution_reply, str) and execution_reply.strip():
            _append_agent_reply("IoT", execution_reply.strip())
            had_reply = True

        if action_required and not action_taken and device_commands:
            commands_summary = "; ".join(
                f"{cmd.get('name') or cmd.get('device_id')}: {cmd}"
                for cmd in device_commands
                if isinstance(cmd, dict)
            )
            action_requests.append(
                {
                    "agent": "IoT",
                    "kind": "iot_commands",
                    "description": commands_summary or "IoTアクションの実行",
                }
            )

    life_response = responses.get("Life-Style")
    if isinstance(life_response, dict):
        needs_help = life_response.get("needs_help")
        question = life_response.get("question")
        if needs_help and isinstance(question, str) and question.strip():
            action_requests.append(
                {
                    "agent": "Life-Style",
                    "kind": "lifestyle_query",
                    "description": question.strip(),
                }
            )

    scheduler_response = responses.get("Scheduler")
    if isinstance(scheduler_response, dict):
        action_taken = scheduler_response.get("action_taken")
        results = scheduler_response.get("results") if isinstance(scheduler_response.get("results"), list) else []
        if action_taken and results:
            summary = "; ".join(str(item) for item in results if item)
            if summary:
                _append_agent_reply("Scheduler", f"スケジュールを更新しました: {summary}")
                had_reply = True
        # Note: _extract_reply for Scheduler is already called in _send_recent_history_to_agents,
        # so we skip it here to avoid duplicate writes to chat_history.
        response_order.append("Scheduler")

    if response_order:
        # Keep action handling order aligned with agent response order.
        order_index = {name: idx for idx, name in enumerate(response_order)}
        action_requests.sort(key=lambda item: order_index.get(item.get("agent"), 999))

    if not action_requests:
        _append_agent_reply("Orchestrator", "了解です。今のところ追加のアクションは不要です。")
        return

    availability = get_agent_availability()
    for request in action_requests:
        agent = request.get("agent")
        kind = request.get("kind")
        description = request.get("description") or ""

        try:
            if kind == "browser_task":
                if not availability.get("browser", True):
                    continue
                result = _call_browser_agent_chat(description)
                summary = result.get("run_summary") if isinstance(result, dict) else None
                message = summary or f"ブラウザエージェントに依頼しました: {description}"
            elif kind == "iot_commands":
                if not availability.get("iot", True):
                    continue
                # Send a concise command to IoT agent chat; IoT agent will interpret.
                prompt = f"以下のIoTアクションを実行してください: {description}"
                result = _call_iot_agent_command(prompt)
                message = result.get("reply") if isinstance(result, dict) else None
                if not message:
                    message = f"IoT Agentに実行を依頼しました: {description}"
            elif kind == "lifestyle_query":
                if not availability.get("lifestyle", True):
                    continue
                result = _call_lifestyle(
                    "/agent_rag_answer",
                    method="POST",
                    payload={"question": description},
                )
                message = result.get("answer") if isinstance(result, dict) else None
                if not message:
                    message = f"Life-Style Agentに問い合わせました: {description}"
            else:
                continue

            _append_agent_reply("Orchestrator", message)
        except (BrowserAgentError, IotAgentError, LifestyleAPIError) as exc:
            logging.warning("Failed to handle agent action (%s): %s", kind, exc)
            _append_agent_reply("Orchestrator", f"{agent} への依頼に失敗しました: {exc}")
        except Exception as exc:  # noqa: BLE001
            logging.exception("Unexpected error while handling agent action")
            _append_agent_reply("Orchestrator", f"{agent} への依頼中に予期しないエラーが発生しました: {exc}")


def _send_recent_history_to_agents(history: List[Dict[str, str]]) -> None:
    """Send the last 5 chat history entries to all agents and capture any replies."""

    recent_history = history[-5:]

    normalized_history: List[Dict[str, str]] = []
    for entry in recent_history:
        role = entry.get("role") if isinstance(entry, dict) else None
        content = entry.get("content") if isinstance(entry, dict) else None
        if not isinstance(role, str) or not isinstance(content, str):
            continue
        normalized_history.append({"role": role, "content": content})

    if not normalized_history:
        return

    payload = {"history": normalized_history}
    responses: Dict[str, Dict[str, Any]] = {}
    response_order: List[str] = []
    had_reply = False

    availability = get_agent_availability()
    if availability.get("lifestyle", True):
        try:
            lifestyle_response = _call_lifestyle(
                "/analyze_conversation",
                method="POST",
                payload=payload,
            )
            responses["Life-Style"] = lifestyle_response if isinstance(lifestyle_response, dict) else {}
            had_reply = _extract_reply("Life-Style", lifestyle_response) or had_reply
            response_order.append("Life-Style")
        except LifestyleAPIError as e:
            logging.warning("Error sending history to Life-Style: %s", e)

    global _browser_history_supported
    if _browser_history_supported and availability.get("browser", True):
        try:
            browser_response = _call_browser_agent_history_check(normalized_history)
            responses["Browser"] = browser_response if isinstance(browser_response, dict) else {}
            had_reply = _extract_reply("Browser", browser_response) or had_reply
            response_order.append("Browser")
        except BrowserAgentError as e:
            if getattr(e, "status_code", None) == 404:
                _browser_history_supported = False
                logging.info(
                    "Browser agent history check endpoint not available. "
                    "Disabling future history check requests."
                )
            else:
                logging.warning("Error sending history to browser agent: %s", e)

    if availability.get("iot", True):
        try:
            iot_response = _call_iot_agent_conversation_review(normalized_history)
            responses["IoT"] = iot_response if isinstance(iot_response, dict) else {}
            had_reply = _extract_reply("IoT", iot_response) or had_reply
            response_order.append("IoT")
        except IotAgentError as e:
            logging.warning("Error sending history to iot agent: %s", e)

    if availability.get("scheduler", True):
        try:
            scheduler_response = _call_scheduler_agent_conversation_review(normalized_history)
            responses["Scheduler"] = scheduler_response if isinstance(scheduler_response, dict) else {}
            had_reply = _extract_reply("Scheduler", scheduler_response) or had_reply
            response_order.append("Scheduler")
        except SchedulerAgentError as e:
            logging.warning("Error sending history to scheduler agent: %s", e)

    _handle_agent_responses(responses, normalized_history, had_reply, response_order)


def _append_to_chat_history(
    role: str, content: str, *, broadcast: bool = True, metadata: Optional[Dict[str, Any]] = None
) -> None:
    """Append a message to the chat history file."""

    extras = metadata if isinstance(metadata, dict) else None
    history, source_path = _load_chat_history()

    next_id = len(history) + 1
    entry: Dict[str, Any] = {"id": next_id, "role": role, "content": content}
    if extras:
        for key, value in extras.items():
            if key in {"id", "role", "content"}:
                continue
            entry[key] = value
    history.append(entry)

    try:
        _write_chat_history(history, preferred_path=source_path)
    except Exception as exc:  # noqa: BLE001
        logging.error("Chat history write failed; message may not persist: %s", exc)
        raise

    total_entries = len(history)
    if total_entries == 0:
        return

    # Keep agents loosely in sync
    if broadcast and total_entries % 5 == 0:
        memory_settings = load_memory_settings()
        if memory_settings.get("history_sync_enabled", True):
            threading.Thread(target=_send_recent_history_to_agents, args=(history,)).start()

    # Short-term memory: refresh every turn using the latest few lines as context
    threading.Thread(target=_refresh_memory, args=("short", history[-6:])).start()

    # Long-term memory: consolidate only after several short updates to avoid homogenization
    with _short_update_lock:
        should_consolidate_long = _short_updates_since_last_long >= _SHORT_TO_LONG_THRESHOLD
    if should_consolidate_long:
        threading.Thread(target=_consolidate_short_into_long, args=(history[-20:],)).start()
