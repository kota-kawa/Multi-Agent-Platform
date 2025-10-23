"""Flask application that serves the SPA and proxies FAQ_Gemini APIs."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Literal, TypedDict
from urllib.parse import urlparse, urlunparse

import requests
from flask import Flask, Response, jsonify, request, send_from_directory, stream_with_context
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph


def _load_env_file(path: str = ".env") -> None:
    """Best-effort .env loader so orchestrator can pick up API keys."""

    env_path = Path(path)
    if not env_path.is_file():
        return

    try:
        content = env_path.read_text(encoding="utf-8")
    except OSError:
        return

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        cleaned = value.strip()
        if (
            (cleaned.startswith('"') and cleaned.endswith('"'))
            or (cleaned.startswith("'") and cleaned.endswith("'"))
        ):
            cleaned = cleaned[1:-1]
        os.environ.setdefault(key, cleaned)


_load_env_file()


DEFAULT_GEMINI_BASES = (
    "http://localhost:5000",
    "http://faq_gemini:5000",
)
GEMINI_TIMEOUT = float(os.environ.get("FAQ_GEMINI_TIMEOUT", "30"))

# Known upstream IoT Agent deployment that should be reachable from public environments.
PUBLIC_IOT_AGENT_BASE = "https://iot-agent.project-kk.com"

DEFAULT_IOT_AGENT_BASES = (
    PUBLIC_IOT_AGENT_BASE,
)
IOT_AGENT_TIMEOUT = float(os.environ.get("IOT_AGENT_TIMEOUT", "30"))

DEFAULT_BROWSER_AGENT_BASES = (
    "http://localhost:5005",
    "http://browser_agent:5005",
)
BROWSER_AGENT_TIMEOUT = float(os.environ.get("BROWSER_AGENT_TIMEOUT", "120"))

ORCHESTRATOR_MODEL = os.environ.get("ORCHESTRATOR_MODEL", "gpt-4.1-2025-04-14")
ORCHESTRATOR_MAX_TASKS = int(os.environ.get("ORCHESTRATOR_MAX_TASKS", "5"))


app = Flask(__name__, static_folder="assets", static_url_path="/assets")
logging.basicConfig(level=logging.INFO)


class GeminiAPIError(RuntimeError):
    """Raised when the upstream FAQ_Gemini API responds with an error."""

    def __init__(self, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.status_code = status_code


class BrowserAgentError(RuntimeError):
    """Raised when the Browser Agent request cannot be completed."""

    def __init__(self, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.status_code = status_code


class IotAgentError(RuntimeError):
    """Raised when the IoT Agent request fails."""

    def __init__(self, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.status_code = status_code


class OrchestratorError(RuntimeError):
    """Raised when the orchestrator cannot complete a request."""


def _format_sse_event(payload: Dict[str, Any]) -> str:
    """Serialise an SSE event line with the payload JSON."""

    event_type = str(payload.get("event") or "message").strip() or "message"
    data = json.dumps(payload, ensure_ascii=False)
    return f"event: {event_type}\ndata: {data}\n\n"


def _iter_gemini_bases() -> list[str]:
    """Return the configured FAQ_Gemini base URLs in priority order."""

    configured = os.environ.get("FAQ_GEMINI_API_BASE", "")
    candidates: list[str] = []
    if configured:
        candidates.extend(part.strip() for part in configured.split(","))
    candidates.extend(DEFAULT_GEMINI_BASES)

    deduped: list[str] = []
    seen: set[str] = set()
    for base in candidates:
        if not base:
            continue
        normalized = base.rstrip("/")
        if normalized.startswith("/"):
            # Avoid proxying to self
            continue
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


def _iter_iot_agent_bases() -> list[str]:
    """Return configured IoT Agent base URLs in priority order."""

    configured = os.environ.get("IOT_AGENT_API_BASE", "")
    candidates: list[str] = []
    if configured:
        candidates.extend(part.strip() for part in configured.split(","))
    candidates.extend(DEFAULT_IOT_AGENT_BASES)

    deduped: list[str] = []
    seen: set[str] = set()
    for base in candidates:
        if not base:
            continue
        normalized = base.rstrip("/")
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


def _build_gemini_url(base: str, path: str) -> str:
    """Build an absolute URL to the upstream FAQ_Gemini API."""

    if not path.startswith("/"):
        path = f"/{path}"
    return f"{base}{path}"


def _build_iot_agent_url(base: str, path: str) -> str:
    """Build an absolute URL to the upstream IoT Agent API."""

    if not path.startswith("/"):
        path = f"/{path}"
    return f"{base}{path}"


def _expand_browser_agent_base(base: str) -> Iterable[str]:
    """Yield the original Browser Agent base along with hostname aliases."""

    yield base

    try:
        parsed = urlparse(base)
    except ValueError:
        return

    hostname = parsed.hostname or ""
    if not hostname:
        return

    replacements: list[str] = []
    if "_" in hostname:
        replacements.append(hostname.replace("_", "-"))
    if "-" in hostname:
        replacements.append(hostname.replace("-", "_"))

    if not replacements:
        return

    auth = ""
    if parsed.username:
        auth = parsed.username
        if parsed.password:
            auth += f":{parsed.password}"
        auth += "@"
    port = f":{parsed.port}" if parsed.port else ""

    for replacement in replacements:
        if replacement == hostname:
            continue
        netloc = f"{auth}{replacement}{port}"
        alias = urlunparse(parsed._replace(netloc=netloc))
        if alias:
            yield alias


def _iter_browser_agent_bases() -> list[str]:
    """Return configured Browser Agent base URLs in priority order."""

    configured = os.environ.get("BROWSER_AGENT_API_BASE", "")
    candidates: list[str] = []
    if configured:
        candidates.extend(part.strip() for part in configured.split(","))
    candidates.extend(DEFAULT_BROWSER_AGENT_BASES)

    deduped: list[str] = []
    seen: set[str] = set()
    for base in candidates:
        if not base:
            continue
        normalized = base.rstrip("/")
        if normalized.startswith("/"):
            # Avoid proxying to self
            continue
        for expanded in _expand_browser_agent_base(normalized):
            candidate = expanded.rstrip("/")
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            deduped.append(candidate)
    return deduped


def _build_browser_agent_url(base: str, path: str) -> str:
    """Build an absolute URL to the Browser Agent API."""

    if not path.startswith("/"):
        path = f"/{path}"
    return f"{base}{path}"


def _call_gemini(path: str, *, method: str = "GET", payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Call the upstream FAQ_Gemini API and return the JSON payload."""

    bases = _iter_gemini_bases()
    if not bases:
        raise GeminiAPIError("FAQ_Gemini API の接続先が設定されていません。", status_code=500)

    connection_errors: list[str] = []
    last_exception: Exception | None = None
    response = None
    for base in bases:
        url = _build_gemini_url(base, path)
        try:
            response = requests.request(method, url, json=payload, timeout=GEMINI_TIMEOUT)
        except requests.exceptions.RequestException as exc:  # pragma: no cover - network failure
            connection_errors.append(f"{url}: {exc}")
            last_exception = exc
            continue
        else:
            break

    if response is None:
        message_lines = ["FAQ_Gemini API への接続に失敗しました。"]
        if connection_errors:
            message_lines.append("試行した URL:")
            message_lines.extend(f"- {error}" for error in connection_errors)
        message = "\n".join(message_lines)
        raise GeminiAPIError(message) from last_exception

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


def _call_browser_agent_chat(prompt: str) -> Dict[str, Any]:
    """Send a chat request to the Browser Agent and return the JSON payload."""

    bases = _iter_browser_agent_bases()
    if not bases:
        raise BrowserAgentError("Browser Agent API の接続先が設定されていません。", status_code=500)

    connection_errors: list[str] = []
    last_exception: Exception | None = None
    response = None
    for base in bases:
        url = _build_browser_agent_url(base, "/api/chat")
        try:
            response = requests.post(
                url,
                json={"prompt": prompt, "new_task": True},
                timeout=BROWSER_AGENT_TIMEOUT,
            )
        except requests.exceptions.RequestException as exc:  # pragma: no cover - network failure
            connection_errors.append(f"{url}: {exc}")
            last_exception = exc
            continue
        else:
            break

    if response is None:
        if connection_errors:
            logging.warning(
                "Browser Agent API connection attempts failed: %s",
                "; ".join(connection_errors),
            )
        hint = (
            "BROWSER_AGENT_API_BASE 環境変数で有効なエンドポイントを設定するか、"
            "ブラウザエージェントサービスを起動してください。"
        )
        raise BrowserAgentError(
            "ブラウザエージェントに接続できませんでした。 " + hint,
        ) from last_exception

    try:
        data = response.json()
    except ValueError as exc:  # pragma: no cover - unexpected upstream response
        raise BrowserAgentError("Browser Agent API から不正なレスポンス形式が返されました。") from exc

    if not response.ok:
        message = data.get("error") if isinstance(data, dict) else None
        if not message:
            message = response.text or f"{response.status_code} {response.reason}"
        raise BrowserAgentError(message, status_code=response.status_code)

    if not isinstance(data, dict):
        raise BrowserAgentError("Browser Agent API から不正なレスポンス形式が返されました。", status_code=502)

    return data


def _call_iot_agent_chat(command: str) -> Dict[str, Any]:
    """Send a chat request to the IoT Agent and return the JSON payload."""

    bases = _iter_iot_agent_bases()
    if not bases:
        raise IotAgentError("IoT Agent API の接続先が設定されていません。", status_code=500)

    payload = {"messages": [{"role": "user", "content": command}]}
    connection_errors: list[str] = []
    last_exception: Exception | None = None
    response = None
    for base in bases:
        url = _build_iot_agent_url(base, "/api/chat")
        try:
            response = requests.post(url, json=payload, timeout=IOT_AGENT_TIMEOUT)
        except requests.exceptions.RequestException as exc:  # pragma: no cover - network failure
            connection_errors.append(f"{url}: {exc}")
            last_exception = exc
            continue
        else:
            break

    if response is None:
        message_lines = ["IoT Agent API への接続に失敗しました。"]
        if connection_errors:
            message_lines.append("試行した URL:")
            message_lines.extend(f"- {error}" for error in connection_errors)
        message = "\n".join(message_lines)
        raise IotAgentError(message) from last_exception

    try:
        data = response.json()
    except ValueError as exc:  # pragma: no cover - unexpected upstream response
        raise IotAgentError("IoT Agent API から不正なレスポンス形式が返されました。") from exc

    if not response.ok:
        message = data.get("error") if isinstance(data, dict) else None
        if not message:
            message = response.text or f"{response.status_code} {response.reason}"
        raise IotAgentError(message, status_code=response.status_code)

    if not isinstance(data, dict):
        raise IotAgentError("IoT Agent API から不正なレスポンス形式が返されました。", status_code=502)

    return data


def _proxy_iot_agent_request(path: str) -> Response:
    """Proxy the incoming request to the configured IoT Agent API."""

    bases = _iter_iot_agent_bases()
    if not bases:
        return jsonify({"error": "IoT Agent API の接続先が設定されていません。"}), 500

    if request.is_json:
        json_payload = request.get_json(silent=True)
        body_payload = None
    else:
        json_payload = None
        body_payload = request.get_data(cache=False) if request.method in {"POST", "PUT", "PATCH", "DELETE"} else None

    forward_headers: Dict[str, str] = {}
    for header, value in request.headers.items():
        lowered = header.lower()
        if lowered in {"content-type", "authorization", "accept", "cookie"} or lowered.startswith("x-"):
            forward_headers[header] = value

    connection_errors: list[str] = []
    response = None
    for base in bases:
        url = _build_iot_agent_url(base, path)
        try:
            response = requests.request(
                request.method,
                url,
                params=request.args,
                json=json_payload,
                data=body_payload if json_payload is None else None,
                headers=forward_headers,
                timeout=IOT_AGENT_TIMEOUT,
            )
        except requests.exceptions.RequestException as exc:  # pragma: no cover - network failure
            connection_errors.append(f"{url}: {exc}")
            continue
        else:
            break

    if response is None:
        message_lines = ["IoT Agent API への接続に失敗しました。"]
        if connection_errors:
            message_lines.append("試行した URL:")
            message_lines.extend(f"- {error}" for error in connection_errors)
        return jsonify({"error": "\n".join(message_lines)}), 502

    proxy_response = Response(response.content, status=response.status_code)
    excluded_headers = {"content-encoding", "transfer-encoding", "connection", "content-length"}
    for header, value in response.headers.items():
        if header.lower() in excluded_headers:
            continue
        proxy_response.headers[header] = value
    return proxy_response


AgentName = Literal["faq", "browser", "iot"]


class TaskSpec(TypedDict):
    agent: AgentName
    command: str


class ExecutionResult(TypedDict, total=False):
    agent: AgentName
    command: str
    status: Literal["success", "error"]
    response: str | None
    error: str | None


class OrchestratorState(TypedDict, total=False):
    user_input: str
    plan_summary: str | None
    raw_plan: Dict[str, Any] | None
    tasks: List[TaskSpec]
    executions: List[ExecutionResult]
    current_index: int


class MultiAgentOrchestrator:
    """LangGraph-based orchestrator that routes work to specialised agents."""

    _AGENT_ALIASES = {
        "faq": "faq",
        "faq_gemini": "faq",
        "gemini": "faq",
        "knowledge": "faq",
        "knowledge_base": "faq",
        "docs": "faq",
        "browser": "browser",
        "browser_agent": "browser",
        "web": "browser",
        "web_agent": "browser",
        "navigator": "browser",
        "iot": "iot",
        "iot_agent": "iot",
        "device": "iot",
    }

    _AGENT_DISPLAY_NAMES = {
        "faq": "FAQ Gemini",
        "browser": "ブラウザエージェント",
        "iot": "IoT エージェント",
    }

    _PLANNER_PROMPT = """
あなたはマルチエージェントシステムのオーケストレーターです。与えられたユーザーの依頼を読み、実行すべきタスクを分析して下さい。

- 利用可能なエージェント:
  - "faq": FAQ_Gemini のナレッジベースに質問できます。
  - "browser": ブラウザ自動化エージェントで Web を閲覧・操作できます。
  - "iot": IoT エージェントを通じてデバイスの状態確認や操作ができます。
- 出力は JSON オブジェクトのみで、追加の説明やマークダウンを含めてはいけません。
- JSON には必ず次のキーを含めてください:
  - "plan_summary": 実行方針を 1 文でまとめた文字列。
  - "tasks": タスクの配列。各要素は {{"agent": <上記のいずれか>, "command": <エージェントに渡す命令>}} です。
- タスク数は 0〜{max_tasks} 件の範囲に収めてください。不要なタスクは作成しないでください。
- エージェントで対応できない内容はタスクを生成せず、plan_summary でその旨を説明してください。
""".strip()

    def __init__(self) -> None:
        if not os.environ.get("OPENAI_API_KEY"):
            raise OrchestratorError("OPENAI_API_KEY が .env に設定されていません。")

        try:
            self._llm = ChatOpenAI(model=ORCHESTRATOR_MODEL, temperature=0.1)
        except Exception as exc:  # noqa: BLE001
            raise OrchestratorError(f"LangGraph LLM の初期化に失敗しました: {exc}") from exc

        self._graph = self._build_graph()

    def _build_graph(self) -> Any:
        graph: StateGraph[OrchestratorState] = StateGraph(OrchestratorState)
        graph.add_node("plan", self._plan_node)
        graph.add_node("execute", self._execute_node)
        graph.add_edge("plan", "execute")
        graph.add_conditional_edges("execute", self._continue_or_end, {"continue": "execute", "end": END})
        graph.set_entry_point("plan")
        return graph.compile()

    def _continue_or_end(self, state: OrchestratorState) -> str:
        tasks = state.get("tasks") or []
        index = state.get("current_index", 0)
        return "continue" if index < len(tasks) else "end"

    def _plan_node(self, state: OrchestratorState) -> OrchestratorState:
        user_input = state.get("user_input", "")
        if not user_input:
            raise OrchestratorError("オーケストレーターに渡された入力が空でした。")

        prompt = self._PLANNER_PROMPT.format(max_tasks=ORCHESTRATOR_MAX_TASKS)
        messages = [SystemMessage(content=prompt), HumanMessage(content=user_input)]

        try:
            response = self._llm.invoke(messages)
        except Exception as exc:  # noqa: BLE001
            raise OrchestratorError(f"プラン生成に失敗しました: {exc}") from exc

        raw_content = response.content
        plan_text = self._extract_text(raw_content)
        plan_data = self._parse_plan(plan_text)

        tasks = self._normalise_tasks(plan_data.get("tasks"))
        plan_summary = str(plan_data.get("plan_summary") or plan_data.get("plan") or "").strip()

        return {
            "user_input": user_input,
            "plan_summary": plan_summary,
            "raw_plan": plan_data,
            "tasks": tasks,
            "executions": [],
            "current_index": 0,
        }

    def _execute_node(self, state: OrchestratorState) -> OrchestratorState:
        tasks = state.get("tasks") or []
        index = state.get("current_index", 0)
        executions = list(state.get("executions") or [])

        if index >= len(tasks):
            return {"executions": executions, "current_index": index}

        task = tasks[index]
        result = self._execute_task(task)
        executions.append(result)

        return {"executions": executions, "current_index": index + 1, "tasks": tasks}

    def _extract_text(self, content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            pieces: list[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text = item.get("text")
                    if isinstance(text, str):
                        pieces.append(text)
            return "".join(pieces)
        return str(content)

    def _parse_plan(self, raw: str) -> Dict[str, Any]:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:  # noqa: PERF203
            raise OrchestratorError("プラン応答の JSON 解析に失敗しました。") from exc

        if not isinstance(parsed, dict):
            raise OrchestratorError("プラン応答の形式が不正です。")
        return parsed

    def _normalise_tasks(self, raw_tasks: Any) -> List[TaskSpec]:
        tasks: List[TaskSpec] = []
        if not isinstance(raw_tasks, Iterable):
            return tasks

        for item in raw_tasks:
            if not isinstance(item, dict):
                continue
            agent_raw = str(item.get("agent") or "").strip().lower()
            command = str(item.get("command") or "").strip()
            if not agent_raw or not command:
                continue
            agent = self._AGENT_ALIASES.get(agent_raw)
            if not agent:
                continue
            tasks.append({"agent": agent, "command": command})
            if len(tasks) >= ORCHESTRATOR_MAX_TASKS:
                break
        return tasks

    def _execute_task(self, task: TaskSpec) -> ExecutionResult:
        agent = task["agent"]
        command = task["command"]
        if agent == "faq":
            try:
                data = _call_gemini("/rag_answer", method="POST", payload={"question": command})
            except GeminiAPIError as exc:
                return {
                    "agent": agent,
                    "command": command,
                    "status": "error",
                    "response": None,
                    "error": str(exc),
                }
            answer = str(data.get("answer") or "").strip() or "FAQ_Gemini から回答が得られませんでした。"
            return {
                "agent": agent,
                "command": command,
                "status": "success",
                "response": answer,
                "error": None,
            }

        if agent == "browser":
            try:
                data = _call_browser_agent_chat(command)
            except BrowserAgentError as exc:
                return {
                    "agent": agent,
                    "command": command,
                    "status": "error",
                    "response": None,
                    "error": str(exc),
                }
            summary = str(data.get("run_summary") or "").strip()
            if not summary:
                messages = data.get("messages")
                summary = self._summarise_browser_messages(messages)
            if not summary:
                summary = "ブラウザエージェントからの応答を取得できませんでした。"
            return {
                "agent": agent,
                "command": command,
                "status": "success",
                "response": summary,
                "error": None,
            }

        if agent == "iot":
            try:
                data = _call_iot_agent_chat(command)
            except IotAgentError as exc:
                return {
                    "agent": agent,
                    "command": command,
                    "status": "error",
                    "response": None,
                    "error": str(exc),
                }
            reply = str(data.get("reply") or "").strip()
            if not reply:
                reply = "IoT エージェントからの応答が空でした。"
            return {
                "agent": agent,
                "command": command,
                "status": "success",
                "response": reply,
                "error": None,
            }

        return {
            "agent": agent,
            "command": command,
            "status": "error",
            "response": None,
            "error": f"未対応のエージェント種別です: {agent}",
        }

    def _summarise_browser_messages(self, messages: Any) -> str:
        if not isinstance(messages, list):
            return ""
        for item in reversed(messages):
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "").lower()
            if role != "assistant":
                continue
            content = item.get("content") or item.get("text")
            if isinstance(content, str) and content.strip():
                return content.strip()
        return ""

    def _format_assistant_messages(
        self,
        plan_summary: str | None,
        executions: List[ExecutionResult],
    ) -> List[Dict[str, Any]]:
        messages: List[Dict[str, Any]] = []
        if plan_summary:
            messages.append({"type": "plan", "text": f"計画: {plan_summary}"})

        for result in executions:
            agent_label = self._AGENT_DISPLAY_NAMES.get(result["agent"], result["agent"])
            if result.get("status") == "success":
                body = result.get("response") or "タスクを完了しました。"
            else:
                body = result.get("error") or "タスクの実行に失敗しました。"
            messages.append(
                {
                    "type": "execution",
                    "agent": result["agent"],
                    "status": result.get("status"),
                    "text": f"[{agent_label}] {body}",
                }
            )

        if not messages:
            messages.append({"type": "status", "text": "今回のリクエストでは実行すべきタスクはありませんでした。"})

        return messages

    def _snapshot_state(self, state: OrchestratorState) -> Dict[str, Any]:
        tasks_raw = state.get("tasks") or []
        executions_raw = state.get("executions") or []
        tasks = [
            {"agent": task.get("agent"), "command": task.get("command")}
            for task in tasks_raw
            if isinstance(task, dict)
        ]
        executions = [
            {
                "agent": entry.get("agent"),
                "command": entry.get("command"),
                "status": entry.get("status"),
                "response": entry.get("response"),
                "error": entry.get("error"),
            }
            for entry in executions_raw
            if isinstance(entry, dict)
        ]
        return {
            "plan_summary": state.get("plan_summary") or "",
            "raw_plan": state.get("raw_plan"),
            "tasks": tasks,
            "executions": executions,
            "current_index": state.get("current_index", 0),
        }

    def _event_payload(
        self,
        event_type: str,
        state: OrchestratorState,
        **extras: Any,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"event": event_type, "state": self._snapshot_state(state)}
        payload.update(extras)
        return payload

    def run_stream(self, user_input: str) -> Iterator[Dict[str, Any]]:
        state: OrchestratorState = {
            "user_input": user_input,
            "plan_summary": None,
            "raw_plan": None,
            "tasks": [],
            "executions": [],
            "current_index": 0,
        }

        plan_state = self._plan_node(state)
        state.update(plan_state)
        tasks = list(state.get("tasks") or [])
        state["tasks"] = tasks
        state["executions"] = list(state.get("executions") or [])
        state["current_index"] = 0

        yield self._event_payload("plan", state)

        for index, task in enumerate(tasks):
            state["current_index"] = index
            yield self._event_payload("before_execution", state, task_index=index, task=task)

            if task["agent"] == "browser":
                yield self._event_payload("browser_init", state, task_index=index, task=task)

            result = self._execute_task(task)

            executions = state.get("executions")
            if not isinstance(executions, list):
                executions = []
                state["executions"] = executions
            executions.append(result)
            state["current_index"] = index + 1

            yield self._event_payload(
                "after_execution",
                state,
                task_index=index,
                task=task,
                result=result,
            )

        plan_summary = state.get("plan_summary") or ""
        executions = state.get("executions") or []
        assistant_messages = self._format_assistant_messages(plan_summary, executions)
        yield self._event_payload(
            "complete",
            state,
            assistant_messages=assistant_messages,
        )

    def run(self, user_input: str) -> Dict[str, Any]:
        final_event: Dict[str, Any] | None = None
        for event in self.run_stream(user_input):
            final_event = event

        if not final_event or final_event.get("event") != "complete":
            raise OrchestratorError("オーケストレーターの実行が完了しませんでした。")

        final_state = final_event.get("state") or {}
        plan_summary = final_state.get("plan_summary") or ""
        tasks = final_state.get("tasks") or []
        executions = final_state.get("executions") or []
        assistant_messages = final_event.get("assistant_messages") or self._format_assistant_messages(
            plan_summary,
            executions,
        )

        return {
            "plan_summary": plan_summary,
            "tasks": tasks,
            "executions": executions,
            "assistant_messages": assistant_messages,
        }


_orchestrator_service: MultiAgentOrchestrator | None = None


def _get_orchestrator() -> MultiAgentOrchestrator:
    global _orchestrator_service
    if _orchestrator_service is None:
        try:
            _orchestrator_service = MultiAgentOrchestrator()
        except OrchestratorError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise OrchestratorError(f"オーケストレーターの初期化に失敗しました: {exc}") from exc
    return _orchestrator_service


@app.route("/orchestrator/chat", methods=["POST"])
def orchestrator_chat() -> Any:
    """Handle orchestrator chat requests originating from the General view."""

    payload = request.get_json(silent=True) or {}
    message = (payload.get("message") or "").strip()
    if not message:
        return jsonify({"error": "メッセージを入力してください。"}), 400

    try:
        orchestrator = _get_orchestrator()
    except OrchestratorError as exc:
        logging.exception("Orchestrator initialisation failed: %s", exc)

        def _error_stream() -> Iterator[str]:
            yield _format_sse_event({"event": "error", "error": str(exc)})

        return Response(
            stream_with_context(_error_stream()),
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


@app.route("/iot_agent", defaults={"path": ""}, methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
@app.route("/iot_agent/<path:path>", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
def proxy_iot_agent(path: str) -> Response:
    """Forward IoT Agent API requests to the configured upstream service."""

    return _proxy_iot_agent_request(path)


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
