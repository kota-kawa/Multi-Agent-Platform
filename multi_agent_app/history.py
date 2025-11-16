"""Chat history helpers shared between routes and the orchestrator."""

from __future__ import annotations

import json
import logging
import threading
from typing import Dict, List

from .browser import _call_browser_agent_history_check
from .errors import BrowserAgentError, GeminiAPIError, IotAgentError
from .gemini import _call_gemini
from .iot import _call_iot_agent_conversation_review

_browser_history_supported = True


def _send_recent_history_to_agents(history: List[Dict[str, str]]) -> None:
    """Send the last 5 chat history entries to all agents."""

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

    gemini_history = []
    for entry in normalized_history:
        role = entry["role"].strip()
        role_lower = role.lower()
        if role_lower == "user":
            mapped_role = "User"
        elif role_lower == "assistant":
            mapped_role = "AI"
        else:
            mapped_role = role or "System"
        gemini_history.append({"role": mapped_role, "message": entry["content"]})

    try:
        _call_gemini(
            "/analyze_conversation",
            method="POST",
            payload={"conversation_history": gemini_history},
        )
    except GeminiAPIError as e:
        logging.warning("Error sending history to gemini: %s", e)

    global _browser_history_supported
    if _browser_history_supported:
        try:
            _call_browser_agent_history_check(normalized_history)
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
        _call_iot_agent_conversation_review(normalized_history)
    except IotAgentError as e:
        logging.warning("Error sending history to iot agent: %s", e)


def _append_to_chat_history(role: str, content: str) -> None:
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
            if len(history) > 0 and len(history) % 5 == 0:
                threading.Thread(target=_send_recent_history_to_agents, args=(history,)).start()
    except FileNotFoundError:
        with open("chat_history.json", "w", encoding="utf-8") as f:
            json.dump([{"role": role, "content": content}], f, ensure_ascii=False, indent=2)
