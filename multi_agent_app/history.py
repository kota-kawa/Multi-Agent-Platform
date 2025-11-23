"""Chat history helpers shared between routes and the orchestrator."""

from __future__ import annotations

import json
import logging
import threading
from typing import Any, Dict, List, Optional

from langchain_core.messages import SystemMessage
from langchain_openai import ChatOpenAI

from .browser import _call_browser_agent_chat, _call_browser_agent_history_check
from .errors import BrowserAgentError, GeminiAPIError, IotAgentError
from .gemini import _call_gemini
from .iot import _call_iot_agent_command, _call_iot_agent_conversation_review
from .settings import resolve_llm_config

_browser_history_supported = True
_memory_llm_instance: ChatOpenAI | None = None
_memory_llm_lock = threading.Lock()


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


def _get_memory_llm() -> ChatOpenAI | None:
    """Initialise or reuse an LLM client for memory updates."""

    global _memory_llm_instance
    if _memory_llm_instance is not None:
        return _memory_llm_instance

    with _memory_llm_lock:
        if _memory_llm_instance is not None:
            return _memory_llm_instance
        try:
            config = resolve_llm_config("orchestrator")
            _memory_llm_instance = ChatOpenAI(
                model=config["model"],
                temperature=0.2,
                api_key=config["api_key"],
                base_url=config.get("base_url") or None,
            )
        except Exception as exc:  # noqa: BLE001
            logging.warning("Failed to initialise memory LLM: %s", exc)
            _memory_llm_instance = None
    return _memory_llm_instance


def _load_memory_from_file(path: str) -> str:
    """Read a memory JSON file and return the stored string."""

    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f).get("memory", "") or ""
    except (FileNotFoundError, json.JSONDecodeError):
        return ""


def _save_memory_to_file(path: str, memory_text: str) -> None:
    """Persist the memory text to disk."""

    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"memory": memory_text}, f, ensure_ascii=False, indent=2)
    except Exception as exc:  # noqa: BLE001
        logging.warning("Failed to write memory to %s: %s", path, exc)


def _extract_text(content: Any) -> str:
    """Normalise LangChain response content to text."""

    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text")
                if isinstance(text, str):
                    chunks.append(text)
        return "".join(chunks)
    return str(content)


def _format_history_lines(history: List[Dict[str, str]]) -> str:
    """Render recent history into numbered lines for prompting."""

    lines: list[str] = []
    for idx, entry in enumerate(history, start=1):
        role = entry.get("role") if isinstance(entry, dict) else None
        content = entry.get("content") if isinstance(entry, dict) else None
        if not isinstance(role, str) or not isinstance(content, str):
            continue
        lines.append(f"{idx}. {role}: {content}")
    return "\n".join(lines)


def _build_memory_prompt(kind_label: str, existing_memory: str, recent_history: List[Dict[str, str]]) -> str:
    """Craft a prompt that reconciles existing memory with the latest chat."""

    history_text = _format_history_lines(recent_history)
    existing = existing_memory.strip() or "（これまでのメモなし）"
    return f"""
あなたはユーザーに関する{kind_label}を更新する担当です。

- 直近の会話から得られた事実と既存のメモを照らし合わせ、必要な部分だけを上書き・追記してください。
- 違う内容に更新する場合は「古い情報 -> 新しい情報」のように矢印で差分が分かる形で書き換えてください（例: サッカーが好き -> 野球が好き）。
- 変更不要な情報はそのまま残してください。新しい情報のみの追加も歓迎です。
- 箇条書きで簡潔に記述し、JSON などのラッピングは不要です。
- 情報が不明になった場合は「元の情報 -> 不明」など、状態が変わったことが分かる形で残してください。

現在のメモ:
{existing}

直近の会話履歴（新しい順ではありません）:
{history_text}
""".strip()


def _refresh_memory(memory_kind: str, recent_history: List[Dict[str, str]]) -> None:
    """Update short- or long-term memory by reconciling recent history with the current store."""

    llm = _get_memory_llm()
    if llm is None:
        return

    normalized_history: List[Dict[str, str]] = []
    for entry in recent_history:
        role = entry.get("role") if isinstance(entry, dict) else None
        content = entry.get("content") if isinstance(entry, dict) else None
        if not isinstance(role, str) or not isinstance(content, str):
            continue
        normalized_history.append({"role": role, "content": content})

    if not normalized_history:
        return

    if memory_kind == "short":
        memory_path = "short_term_memory.json"
        label = "短期記憶"
    else:
        memory_path = "long_term_memory.json"
        label = "長期記憶"

    existing_memory = _load_memory_from_file(memory_path)
    prompt = _build_memory_prompt(label, existing_memory, normalized_history)

    try:
        response = llm.invoke([SystemMessage(content=prompt)])
        updated_memory = _extract_text(response.content).strip()
    except Exception as exc:  # noqa: BLE001
        logging.warning("Memory update (%s) failed: %s", memory_kind, exc)
        return

    if not updated_memory:
        logging.info("Memory update (%s) produced empty output; skipping save.", memory_kind)
        return

    _save_memory_to_file(memory_path, updated_memory)


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

    history: List[Dict[str, str]] = []
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
    except FileNotFoundError:
        history = [{"role": role, "content": content}]
        with open("chat_history.json", "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)

    total_entries = len(history)
    if total_entries == 0:
        return

    if broadcast and total_entries % 5 == 0:
        threading.Thread(target=_send_recent_history_to_agents, args=(history,)).start()
    if total_entries % 10 == 0:
        threading.Thread(target=_refresh_memory, args=("short", history[-10:])).start()
    if total_entries % 30 == 0:
        threading.Thread(target=_refresh_memory, args=("long", history[-30:])).start()
