"""Chat history helpers shared between routes and the orchestrator."""

from __future__ import annotations

import json
import logging
import threading
from typing import Any, Dict, List, Optional

from .browser import _call_browser_agent_chat, _call_browser_agent_history_check
from .errors import BrowserAgentError, GeminiAPIError, IotAgentError
from .gemini import _call_gemini
from .iot import _call_iot_agent_command, _call_iot_agent_conversation_review

_browser_history_supported = True


def _append_agent_reply(agent_label: str, reply: str) -> None:
    """Append an agent reply to chat_history without triggering another broadcast."""

    if not reply:
        return

    safe_label = agent_label.strip() or "Agent"
    content = f"[{safe_label}] {reply}"
    _append_to_chat_history("assistant", content, broadcast=False)


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

    # Allow agents to opt-in even without explicit flag if they provided text.
    if reply_is_meaningful:
        _append_agent_reply(agent_label, reply.strip())
        return True

    # If the model named another agent but forgot to include text, skip.
    if isinstance(addressed_agents, list) and addressed_agents:
        # No free-text to log, so ignore.
        return False

    return False


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
        action_required = iot_response.get("action_required")
        device_commands = iot_response.get("device_commands") or []
        if action_required and device_commands:
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

    life_response = responses.get("Life-Assistant")
    if isinstance(life_response, dict):
        needs_help = life_response.get("needs_help")
        question = life_response.get("question")
        if needs_help and isinstance(question, str) and question.strip():
            action_requests.append(
                {
                    "agent": "Life-Assistant",
                    "kind": "faq_query",
                    "description": question.strip(),
                }
            )

    if response_order:
        # Keep action handling order aligned with agent response order.
        order_index = {name: idx for idx, name in enumerate(response_order)}
        action_requests.sort(key=lambda item: order_index.get(item.get("agent"), 999))

    if not action_requests:
        _append_agent_reply("Orchestrator", "了解です。今のところ追加のアクションは不要です。")
        return

    for request in action_requests:
        agent = request.get("agent")
        kind = request.get("kind")
        description = request.get("description") or ""

        try:
            if kind == "browser_task":
                result = _call_browser_agent_chat(description)
                summary = result.get("run_summary") if isinstance(result, dict) else None
                message = summary or f"Browser Agentに依頼しました: {description}"
            elif kind == "iot_commands":
                # Send a concise command to IoT agent chat; IoT agent will interpret.
                prompt = f"以下のIoTアクションを実行してください: {description}"
                result = _call_iot_agent_command(prompt)
                message = result.get("reply") if isinstance(result, dict) else None
                if not message:
                    message = f"IoT Agentに実行を依頼しました: {description}"
            elif kind == "faq_query":
                result = _call_gemini(
                    "/agent_rag_answer",
                    method="POST",
                    payload={"question": description},
                )
                message = result.get("answer") if isinstance(result, dict) else None
                if not message:
                    message = f"Life-Assistant Agentに問い合わせました: {description}"
            else:
                continue

            _append_agent_reply("Orchestrator", message)
        except (BrowserAgentError, IotAgentError, GeminiAPIError) as exc:
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

    try:
        gemini_response = _call_gemini(
            "/analyze_conversation",
            method="POST",
            payload=payload,
        )
        responses["Life-Assistant"] = gemini_response if isinstance(gemini_response, dict) else {}
        had_reply = _extract_reply("Life-Assistant", gemini_response) or had_reply
        response_order.append("Life-Assistant")
    except GeminiAPIError as e:
        logging.warning("Error sending history to gemini: %s", e)

    global _browser_history_supported
    if _browser_history_supported:
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

    try:
        iot_response = _call_iot_agent_conversation_review(normalized_history)
        responses["IoT"] = iot_response if isinstance(iot_response, dict) else {}
        had_reply = _extract_reply("IoT", iot_response) or had_reply
        response_order.append("IoT")
    except IotAgentError as e:
        logging.warning("Error sending history to iot agent: %s", e)

    _handle_agent_responses(responses, normalized_history, had_reply, response_order)


def _append_to_chat_history(role: str, content: str, *, broadcast: bool = True) -> None:
    """Append a message to the chat history file."""

    try:
        with open("chat_history.json", "r+", encoding="utf-8") as f:
            try:
                history = json.load(f)
            except json.JSONDecodeError:
                history = []
            history.append({"role": role, "content": content})
            f.seek(0)
            json.dump(history, f, ensure_ascii=False, indent=2)
            f.truncate()
            if broadcast and len(history) > 0 and len(history) % 5 == 0:
                threading.Thread(target=_send_recent_history_to_agents, args=(history,)).start()
    except FileNotFoundError:
        with open("chat_history.json", "w", encoding="utf-8") as f:
            json.dump([{"role": role, "content": content}], f, ensure_ascii=False, indent=2)
