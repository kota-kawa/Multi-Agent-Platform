"Chat history helpers shared between routes and the orchestrator."

from __future__ import annotations

import json
import logging
import re
import threading
from typing import Any, Dict, List, Optional

from langchain_core.messages import SystemMessage
from langchain_openai import ChatOpenAI

from .browser import _call_browser_agent_chat, _call_browser_agent_history_check
from .errors import BrowserAgentError, LifestyleAPIError, IotAgentError, SchedulerAgentError
from .lifestyle import _call_lifestyle
from .iot import _call_iot_agent_command, _call_iot_agent_conversation_review
from .scheduler import _call_scheduler_agent_conversation_review
from .settings import resolve_llm_config
from .memory_manager import MemoryManager

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


def _extract_json_payload(response_text: str) -> str:
    """Pull JSON string out of a response, tolerating code fences."""

    json_match = re.search(r"```(?:json)?\s*(.*?)\s*```", response_text, re.DOTALL)
    if json_match:
        return json_match.group(1)
    return response_text


def _coerce_memory_diff(response_text: str, memory_kind: str) -> dict[str, Any]:
    """Ensure memory diffs are JSON objects, even if the LLM replied with text."""

    json_str = _extract_json_payload(response_text)
    try:
        parsed = json.loads(json_str)
    except json.JSONDecodeError:
        logging.warning("Memory update (%s) returned non-JSON; wrapping raw text.", memory_kind)
        return {"summary_text": response_text, "operations": []}

    if not isinstance(parsed, dict):
        logging.warning("Memory update (%s) returned JSON that is not an object; coercing.", memory_kind)
        summary_text = (
            parsed if isinstance(parsed, (str, int, float, bool)) else json.dumps(parsed, ensure_ascii=False)
        )
        return {"summary_text": str(summary_text), "operations": []}

    summary_text = parsed.get("summary_text")
    if not isinstance(summary_text, str):
        summary_text = response_text
    operations = parsed.get("operations")
    if not isinstance(operations, list):
        operations = []

    parsed["summary_text"] = summary_text
    parsed["operations"] = operations
    return parsed


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


def _build_memory_prompt(kind_label: str, current_memory: Dict[str, Any], recent_history: List[Dict[str, str]]) -> str:
    """Craft a prompt that reconciles existing memory with the latest chat, requesting JSON diffs."""

    history_text = _format_history_lines(recent_history)
    # Serialize existing memory to JSON for the prompt, ensuring it's readable
    memory_json = json.dumps(current_memory, ensure_ascii=False, indent=2)

    return f"""
あなたはユーザーに関する{kind_label}を更新する担当です。
現在保持している記憶データと、直近の会話履歴をもとに、記憶の更新差分（JSON）を作成してください。

### 現在の記憶データ
```json
{memory_json}
```

### 直近の会話履歴
{history_text}

### 指示
1. **summary_textの更新**: 
   - 会話の全体的な要約を更新してください。古い情報を保持しつつ、新しい文脈を反映してください。
2. **スロット（slots）の更新**:
   - ユーザーの属性、好み、現在の状態、計画などの重要な事実を抽出してスロットに入れてください。
   - 既存のスロットの値が変わった場合は更新してください。
   - **重要な変更**（好みの変化、決定事項の変更など）の場合、`log_change: true` とし、`reason`（理由）を記述してください。些細な更新では `false` にしてください。
   - 新しい事実が見つかった場合、新しい `slot_id` でスロットを追加してください。その際、`label`（日本語のラベル）と `category`（カテゴリ）も推測して指定してください。
3. **出力形式**:
   - **JSON形式のみ**を出力してください。Markdownのコードブロック（```json ... ```）で囲んでください。
   - 以下のスキーマに従ってください:

```json
{{
  "summary_text": "更新後の要約テキスト",
  "operations": [
    {{
      "op": "set_slot",
      "slot_id": "unique_id_string",
      "value": "値（文字列、数値など）",
      "log_change": true,
      "reason": "変更の理由（log_changeがtrueの場合必須）",
      "label": "表示用ラベル（新規作成時必須）",
      "category": "travel/food/work/etc（新規作成時必須）",
      "confidence": 0.9
    }}
  ]
}}
```
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

    manager = MemoryManager(memory_path)
    current_memory = manager.load_memory()
    
    prompt = _build_memory_prompt(label, current_memory, normalized_history)

    try:
        response = llm.invoke([SystemMessage(content=prompt)])
        response_text = _extract_text(response.content).strip()

        diff = _coerce_memory_diff(response_text, memory_kind)
        manager.apply_diff(diff)
    except Exception as exc:  # noqa: BLE001
        logging.warning("Memory update (%s) failed: %s", memory_kind, exc)
        return


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

    life_response = responses.get("Life-Assistant")
    if isinstance(life_response, dict):
        needs_help = life_response.get("needs_help")
        question = life_response.get("question")
        if needs_help and isinstance(question, str) and question.strip():
            action_requests.append(
                {
                    "agent": "Life-Assistant",
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
        had_reply = _extract_reply("Scheduler", scheduler_response) or had_reply
        response_order.append("Scheduler")

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
            elif kind == "lifestyle_query":
                result = _call_lifestyle(
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

    try:
        lifestyle_response = _call_lifestyle(
            "/analyze_conversation",
            method="POST",
            payload=payload,
        )
        responses["Life-Assistant"] = lifestyle_response if isinstance(lifestyle_response, dict) else {}
        had_reply = _extract_reply("Life-Assistant", lifestyle_response) or had_reply
        response_order.append("Life-Assistant")
    except LifestyleAPIError as e:
        logging.warning("Error sending history to Life-Assistant: %s", e)

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

    try:
        scheduler_response = _call_scheduler_agent_conversation_review(normalized_history)
        responses["Scheduler"] = scheduler_response if isinstance(scheduler_response, dict) else {}
        had_reply = _extract_reply("Scheduler", scheduler_response) or had_reply
        response_order.append("Scheduler")
    except SchedulerAgentError as e:
        logging.warning("Error sending history to scheduler agent: %s", e)

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
