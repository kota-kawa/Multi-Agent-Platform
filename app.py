"""Flask application that serves the SPA and proxies FAQ_Gemini APIs."""

from __future__ import annotations

import json
import logging
import os
import queue
import threading
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Literal, TypedDict, cast
from urllib.parse import urlparse, urlunparse

import requests
from flask import (
    Flask,
    Response,
    g,
    has_request_context,
    jsonify,
    render_template,
    request,
    send_from_directory,
    stream_with_context,
)
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
    "http://browser-agent:5005",
    "http://localhost:5005",
)
BROWSER_AGENT_TIMEOUT = float(os.environ.get("BROWSER_AGENT_TIMEOUT", "120"))

DEFAULT_BROWSER_EMBED_URL = (
    "http://127.0.0.1:7900/"
    "vnc_lite.html?autoconnect=1&resize=scale&scale=auto&view_clip=false"
)
DEFAULT_BROWSER_AGENT_CLIENT_BASE = "http://localhost:5005"

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


def _append_to_chat_history(role: str, content: str):
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
    except FileNotFoundError:
        with open("chat_history.json", "w", encoding="utf-8") as f:
            json.dump([{"role": role, "content": content}], f, ensure_ascii=False, indent=2)


def _format_sse_event(payload: Dict[str, Any]) -> str:
    """Serialise an SSE event line with the payload JSON."""

    event_type = str(payload.get("event") or "message").strip() or "message"
    data = json.dumps(payload, ensure_ascii=False)
    return f"event: {event_type}\ndata: {data}\n\n"


def _resolve_browser_embed_url() -> str:
    """Return the browser embed URL exposed to the frontend."""

    configured = os.environ.get("BROWSER_EMBED_URL", "").strip()
    if configured:
        return configured
    return DEFAULT_BROWSER_EMBED_URL


def _resolve_browser_agent_client_base() -> str:
    """Return the Browser Agent API base URL for browser clients."""

    configured = os.environ.get("BROWSER_AGENT_CLIENT_BASE", "").strip()
    if configured:
        return configured
    return DEFAULT_BROWSER_AGENT_CLIENT_BASE


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


def _normalise_browser_base_values(values: Any) -> list[str]:
    """Return a flat list of browser agent base URL strings from client payloads."""

    cleaned: list[str] = []

    def _consume(value: Any) -> None:
        if value is None:
            return
        if isinstance(value, str):
            parts = [part.strip() for part in value.split(",")]
            cleaned.extend(part for part in parts if part)
            return
        if isinstance(value, Iterable):
            for item in value:
                _consume(item)

    _consume(values)
    return cleaned


def _iter_browser_agent_bases() -> list[str]:
    """Return configured Browser Agent base URLs in priority order."""

    configured = os.environ.get("BROWSER_AGENT_API_BASE", "")
    candidates: list[str] = []
    if has_request_context():
        overrides = getattr(g, "browser_agent_bases", None)
        if overrides:
            if isinstance(overrides, list):
                candidates.extend(overrides)
            else:  # Defensive fallback
                candidates.extend(_normalise_browser_base_values(overrides))
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


def _extract_browser_error_message(response: requests.Response, default_message: str) -> str:
    try:
        data = response.json()
    except ValueError:
        data = None

    if isinstance(data, dict):
        message = data.get("error")
        if isinstance(message, str) and message.strip():
            return message.strip()

    text = response.text.strip()
    if text:
        return text

    return default_message


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
    review_status: Literal["ok", "retry"] | None
    review_reason: str | None


class OrchestratorState(TypedDict, total=False):
    user_input: str
    plan_summary: str | None
    raw_plan: Dict[str, Any] | None
    tasks: List[TaskSpec]
    executions: List[ExecutionResult]
    current_index: int
    retry_counts: Dict[int, int]


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
        "faq": "家庭内エージェント",
        "browser": "ブラウザエージェント",
        "iot": "IoT エージェント",
    }

    MAX_RETRIES = 2

    _REVIEWER_PROMPT = """
あなたはマルチエージェントシステムのオーケストレーターで、エージェントの実行結果をレビューする役割を担っています。

- ユーザーの当初の依頼とエージェントの実行結果を比較し、結果が依頼内容を満たしているか確認してください。
- 結果が不十分な場合、エージェントに再実行を指示できます。
- 出力は JSON オブジェクトのみで、追加の説明やマークダウンを含めてはいけません。
- JSON には必ず次のキーを含めてください:
  - "review_status": "ok" または "retry" のいずれかの文字列。
  - "review_reason": "ok" の場合は簡単な承認理由、"retry" の場合は再実行を指示する具体的な理由や修正点を記述した文字列。

レビュー対象の情報:
- ユーザーの依頼: {user_input}
- エージェント名: {agent_name}
- 実行コマンド: {command}
- 実行結果: {result}
""".strip()

    _PLANNER_PROMPT = """
あなたはマルチエージェントシステムのオーケストレーターです。与えられたユーザーの依頼を読み、実行すべきタスクを分析して下さい。

- 利用可能なエージェント:
  - "faq": 家庭内の出来事や家電の専門家エージェントで、IoTなどに対してナレッジベースに質問できます。
  - "browser": ブラウザ自動化エージェントで Web を閲覧・操作できます。
  - "iot": IoT エージェントを通じてデバイスの状態確認や操作ができます。
- 出力は JSON オブジェクトのみで、追加の説明やマークダウンを含めてはいけません。
- JSON には必ず次のキーを含めてください:
  - "plan_summary": 実行方針を 1 文でまとめた文字列。
  - "tasks": タスクの配列。各要素は {{"agent": <上記のいずれか>, "command": <エージェントに渡す命令>}} です。
- タスク数は 0〜{max_tasks} 件の範囲に収めてください。不要なタスクは作成しないでください。
- エージェントで対応できない内容はタスクを生成せず、plan_summary でその旨を説明してください。
- ユーザーの意図が不明確または曖昧な場合はタスクを生成せず、plan_summary で確認が必要な旨を伝え、必要な情報を質問してください。
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
        graph.add_node("review", self._review_node)
        graph.add_edge("plan", "execute")
        graph.add_edge("execute", "review")
        graph.add_conditional_edges("review", self._continue_or_end, {"continue": "execute", "end": END})
        graph.set_entry_point("plan")
        return graph.compile()

    def _continue_or_end(self, state: OrchestratorState) -> str:
        tasks = state.get("tasks") or []
        index = state.get("current_index", 0)
        return "continue" if index < len(tasks) else "end"

    def _review_node(self, state: OrchestratorState) -> OrchestratorState:
        executions = list(state.get("executions") or [])
        if not executions:
            return state

        last_execution = executions[-1]
        if last_execution.get("status") == "error":
            return state

        user_input = state["user_input"]
        agent_name = self._AGENT_DISPLAY_NAMES.get(last_execution["agent"], last_execution["agent"])
        command = last_execution["command"]
        result = last_execution.get("response") or last_execution.get("error") or "結果なし"

        prompt = self._REVIEWER_PROMPT.format(
            user_input=user_input,
            agent_name=agent_name,
            command=command,
            result=result,
        )
        messages = [SystemMessage(content=prompt)]

        try:
            response = self._llm.invoke(messages)
            review_text = self._extract_text(response.content)
            review_data = self._parse_plan(review_text)
        except (OrchestratorError, Exception) as exc:
            logging.warning("Review LLM call failed, defaulting to 'ok': %s", exc)
            review_data = {"review_status": "ok", "review_reason": "レビューに失敗したため自動承認されました。"}

        review_status = "ok" if review_data.get("review_status") == "ok" else "retry"
        review_reason = str(review_data.get("review_reason") or "").strip()
        last_execution["review_status"] = review_status
        last_execution["review_reason"] = review_reason

        if review_status == "retry":
            index = state["current_index"] - 1
            retry_counts = state.get("retry_counts") or {}
            count = retry_counts.get(index, 0)
            if count < self.MAX_RETRIES:
                retry_counts[index] = count + 1
                state["current_index"] = index  # Re-run the same task
            state["retry_counts"] = retry_counts

        return {"executions": executions, **state}

    def _plan_node(self, state: OrchestratorState) -> OrchestratorState:
        user_input = state.get("user_input", "")
        if not user_input:
            raise OrchestratorError("オーケストレーターに渡された入力が空でした。")

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

        try:
            with open("chat_history.json", "r", encoding="utf-8") as f:
                history = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            history = []

        recent_history = history[-10:]
        history_prompt = "\n".join([f"{msg['role']}: {msg['content']}" for msg in recent_history])

        prompt = self._PLANNER_PROMPT.format(max_tasks=ORCHESTRATOR_MAX_TASKS)
        if long_term_memory:
            prompt += "\n\nユーザーの特性:\n" + long_term_memory
        if short_term_memory:
            prompt += "\n\nユーザーの最近の動向:\n" + short_term_memory
        prompt += "\n\n以下は直近の会話履歴です:\n" + history_prompt
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
            "retry_counts": {},
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

    def _browser_result_from_payload(
        self,
        command: str,
        payload: Dict[str, Any],
        fallback_summary: str | None = None,
    ) -> ExecutionResult:
        summary = str(payload.get("run_summary") or "").strip()
        if not summary:
            summary = self._summarise_browser_messages(payload.get("messages"))
        if not summary and fallback_summary:
            summary = fallback_summary.strip()
        if not summary:
            summary = "ブラウザエージェントからの応答を取得できませんでした。"
        return {
            "agent": "browser",
            "command": command,
            "status": "success",
            "response": summary,
            "error": None,
        }

    def _browser_error_result(self, command: str, error: Exception) -> ExecutionResult:
        return {
            "agent": "browser",
            "command": command,
            "status": "error",
            "response": None,
            "error": str(error),
        }

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
                return self._browser_error_result(command, exc)
            return self._browser_result_from_payload(command, data)

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

    def _execute_browser_task_with_progress(self, task: TaskSpec) -> Iterator[Dict[str, Any]]:
        command = task["command"]

        try:
            yield from self._iter_browser_agent_progress(command)
        except BrowserAgentError as exc:
            logging.warning("Streaming browser execution failed, falling back to summary only: %s", exc)
            try:
                data = _call_browser_agent_chat(command)
            except BrowserAgentError as fallback_exc:
                yield {
                    "type": "result",
                    "result": self._browser_error_result(command, fallback_exc),
                }
                return

            yield {
                "type": "result",
                "result": self._browser_result_from_payload(command, data),
            }

    def _iter_browser_agent_progress(self, command: str) -> Iterator[Dict[str, Any]]:
        last_error: BrowserAgentError | None = None
        for base in _iter_browser_agent_bases():
            try:
                for event in self._iter_browser_agent_progress_for_base(base, command):
                    yield event
                return
            except BrowserAgentError as exc:
                logging.warning("Browser agent streaming attempt failed for %s: %s", base, exc)
                last_error = exc
                continue

        if last_error is not None:
            raise last_error
        raise BrowserAgentError("ブラウザエージェントへの接続に失敗しました。")

    def _iter_browser_agent_progress_for_base(
        self,
        base: str,
        command: str,
    ) -> Iterator[Dict[str, Any]]:
        history_url = _build_browser_agent_url(base, "/api/history")
        try:
            history_response = requests.get(history_url, timeout=BROWSER_AGENT_TIMEOUT)
        except requests.exceptions.RequestException as exc:
            raise BrowserAgentError(f"ブラウザエージェントの履歴取得に失敗しました: {exc}") from exc

        if not history_response.ok:
            message = _extract_browser_error_message(
                history_response,
                "ブラウザエージェントの履歴取得に失敗しました。",
            )
            raise BrowserAgentError(message, status_code=history_response.status_code)

        try:
            history_data = history_response.json()
        except ValueError as exc:
            raise BrowserAgentError("ブラウザエージェントの履歴レスポンスを解析できませんでした。") from exc

        initial_baseline_id = -1
        messages = history_data.get("messages") if isinstance(history_data, dict) else []
        if isinstance(messages, list):
            for entry in messages:
                if not isinstance(entry, dict):
                    continue
                msg_id = entry.get("id")
                if isinstance(msg_id, int) and msg_id > initial_baseline_id:
                    initial_baseline_id = msg_id

        event_queue: "queue.Queue[Dict[str, Any]]" = queue.Queue()
        stop_event = threading.Event()
        stream_ready = threading.Event()
        stream_status: Dict[str, Any] = {"ok": False, "error": None}
        response_holder: Dict[str, requests.Response] = {}

        stream_url = _build_browser_agent_url(base, "/api/stream")
        chat_url = _build_browser_agent_url(base, "/api/chat")

        def _stream_worker() -> None:
            response: requests.Response | None = None
            try:
                response = requests.get(stream_url, stream=True, timeout=BROWSER_AGENT_TIMEOUT)
            except requests.exceptions.RequestException as exc:
                stream_status["error"] = BrowserAgentError(
                    f"ブラウザエージェントのイベントストリームに接続できませんでした: {exc}",
                )
                stream_ready.set()
                return

            response_holder["response"] = response
            if not response.ok:
                stream_status["error"] = BrowserAgentError(
                    _extract_browser_error_message(
                        response,
                        "ブラウザエージェントのイベントストリームへの接続に失敗しました。",
                    ),
                    status_code=response.status_code,
                )
                stream_ready.set()
                response.close()
                return

            stream_status["ok"] = True
            stream_ready.set()

            event_type = "message"
            data_lines: list[str] = []
            try:
                for raw_line in response.iter_lines(decode_unicode=True):
                    if stop_event.is_set():
                        break
                    if raw_line == "":
                        if data_lines:
                            data_text = "\n".join(data_lines)
                            event_queue.put({"kind": "stream_data", "event": event_type, "data": data_text})
                            data_lines = []
                            event_type = "message"
                        continue
                    if raw_line.startswith(":"):
                        continue
                    if raw_line.startswith("event:"):
                        event_type = raw_line[6:].strip() or "message"
                    elif raw_line.startswith("data:"):
                        data_lines.append(raw_line[5:].lstrip())
            except requests.exceptions.RequestException as exc:
                event_queue.put(
                    {
                        "kind": "stream_error",
                        "error": BrowserAgentError(
                            f"ブラウザエージェントのイベントストリームでエラーが発生しました: {exc}",
                        ),
                    }
                )
            finally:
                event_queue.put({"kind": "stream_closed"})
                response.close()

        def _chat_worker() -> None:
            try:
                response = requests.post(
                    chat_url,
                    json={"prompt": command, "new_task": True},
                    timeout=BROWSER_AGENT_TIMEOUT,
                )
            except requests.exceptions.RequestException as exc:
                event_queue.put(
                    {
                        "kind": "chat_error",
                        "error": BrowserAgentError(
                            f"ブラウザエージェントの呼び出しに失敗しました: {exc}",
                        ),
                    }
                )
                event_queue.put({"kind": "chat_complete"})
                return

            try:
                data = response.json()
            except ValueError:
                data = None

            if not response.ok:
                message = _extract_browser_error_message(
                    response,
                    "ブラウザエージェントの呼び出しに失敗しました。",
                )
                event_queue.put(
                    {
                        "kind": "chat_error",
                        "error": BrowserAgentError(message, status_code=response.status_code),
                    }
                )
                event_queue.put({"kind": "chat_complete"})
                return

            if not isinstance(data, dict):
                event_queue.put(
                    {
                        "kind": "chat_error",
                        "error": BrowserAgentError(
                            "ブラウザエージェントから不正なレスポンス形式が返されました。",
                            status_code=response.status_code,
                        ),
                    }
                )
                event_queue.put({"kind": "chat_complete"})
                return

            event_queue.put({"kind": "chat_result", "data": data})
            event_queue.put({"kind": "chat_complete"})

        stream_thread = threading.Thread(target=_stream_worker, daemon=True)
        stream_thread.start()

        if not stream_ready.wait(timeout=5):
            stop_event.set()
            stored_response = response_holder.get("response")
            if stored_response is not None:
                stored_response.close()
            raise BrowserAgentError("ブラウザエージェントのイベントストリーム初期化がタイムアウトしました。")

        if stream_status.get("error"):
            error_obj = stream_status["error"]
            if isinstance(error_obj, BrowserAgentError):
                raise error_obj
            raise BrowserAgentError(str(error_obj))

        chat_thread = threading.Thread(target=_chat_worker, daemon=True)
        chat_thread.start()

        progress_messages: Dict[Any, str] = {}
        anon_counter = 0
        latest_summary = ""
        chat_result: Dict[str, Any] | None = None
        chat_error: BrowserAgentError | None = None
        stream_finished = False
        stream_failed = False
        chat_finished = False

        def _stop_stream() -> None:
            stop_event.set()
            stored = response_holder.get("response")
            if stored is not None:
                try:
                    stored.close()
                except Exception:  # pragma: no cover - defensive close
                    pass

        try:
            while True:
                try:
                    item = event_queue.get(timeout=0.5)
                except queue.Empty:
                    if chat_finished and (stream_finished or stream_failed):
                        break
                    continue

                kind = item.get("kind")
                if kind == "stream_data":
                    data_text = item.get("data") or ""
                    if not data_text:
                        continue
                    try:
                        payload = json.loads(data_text)
                    except json.JSONDecodeError:
                        logging.debug("Failed to decode browser stream payload: %s", data_text)
                        continue
                    if not isinstance(payload, dict):
                        continue
                    event_type = str(payload.get("type") or "")
                    body = payload.get("payload")
                    if event_type in {"message", "update"} and isinstance(body, dict):
                        msg_id_raw = body.get("id")
                        role = str(body.get("role") or "").lower()
                        content = body.get("content") or body.get("text")
                        if not isinstance(content, str):
                            continue
                        text = content.strip()
                        if not text:
                            continue
                        if role == "user":
                            continue
                        if isinstance(msg_id_raw, int):
                            if msg_id_raw <= initial_baseline_id and msg_id_raw not in progress_messages:
                                continue
                            message_key: Any = msg_id_raw
                        else:
                            message_key = f"anon-{anon_counter}"
                            anon_counter += 1
                        previous_text = progress_messages.get(message_key)
                        if previous_text == text:
                            continue
                        mode = "update" if previous_text is not None else "append"
                        progress_messages[message_key] = text
                        yield {
                            "type": "progress",
                            "text": text,
                            "role": role or "assistant",
                            "message_id": msg_id_raw if isinstance(msg_id_raw, int) else None,
                            "mode": mode,
                        }
                    elif event_type == "status" and isinstance(body, dict):
                        summary_text = body.get("run_summary")
                        if isinstance(summary_text, str) and summary_text.strip():
                            latest_summary = summary_text.strip()
                        if body.get("agent_running") is False:
                            stream_finished = True
                elif kind == "stream_error":
                    error = item.get("error")
                    logging.warning("Browser agent stream error: %s", error)
                    stream_failed = True
                    stream_finished = True
                elif kind == "stream_closed":
                    stream_finished = True
                elif kind == "chat_result":
                    chat_result = item.get("data") or {}
                    chat_finished = True
                    _stop_stream()
                elif kind == "chat_error":
                    error = item.get("error")
                    chat_error = error if isinstance(error, BrowserAgentError) else BrowserAgentError(str(error))
                    chat_finished = True
                    _stop_stream()
                elif kind == "chat_complete":
                    chat_finished = True

                if chat_error is not None:
                    break
                if chat_finished and stream_finished:
                    break
        finally:
            _stop_stream()
            stream_thread.join(timeout=1.0)
            chat_thread.join(timeout=1.0)

        if chat_error is not None:
            raise chat_error

        if chat_result is None or not isinstance(chat_result, dict):
            raise BrowserAgentError("ブラウザエージェントからの応答を取得できませんでした。")

        yield {
            "type": "result",
            "result": self._browser_result_from_payload(command, chat_result, fallback_summary=latest_summary),
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
        _append_to_chat_history("user", user_input)
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

            if task["agent"] == "browser":
                result: ExecutionResult | None = None
                for event in self._execute_browser_task_with_progress(task):
                    event_type = event.get("type")
                    if event_type == "progress":
                        yield self._event_payload(
                            "execution_progress",
                            state,
                            task_index=index,
                            task=task,
                            progress=event,
                        )
                    elif event_type == "result":
                        maybe_result = event.get("result")
                        if isinstance(maybe_result, dict):
                            result = cast(ExecutionResult, maybe_result)
                if result is None:
                    result = self._browser_error_result(
                        task["command"],
                        BrowserAgentError("ブラウザエージェントからの結果を取得できませんでした。"),
                    )
            else:
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

        for msg in assistant_messages:
            _append_to_chat_history("assistant", msg.get("text", ""))

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

    overrides: list[str] = []
    overrides.extend(_normalise_browser_base_values(payload.get("browser_agent_base")))
    overrides.extend(_normalise_browser_base_values(payload.get("browser_agent_bases")))
    if has_request_context():
        g.browser_agent_bases = overrides

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

    _append_to_chat_history("user", question)

    try:
        data = _call_gemini("/rag_answer", method="POST", payload={"question": question})
    except GeminiAPIError as exc:
        logging.exception("FAQ_Gemini rag_answer failed: %s", exc)
        return jsonify({"error": str(exc)}), exc.status_code

    if "answer" in data:
        _append_to_chat_history("assistant", data["answer"])

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


@app.route("/chat_history", methods=["GET"])
def chat_history() -> Any:
    """Fetch the entire chat history."""
    try:
        with open("chat_history.json", "r", encoding="utf-8") as f:
            history = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        history = []
    return jsonify(history)


@app.route("/reset_chat_history", methods=["POST"])
def reset_chat_history() -> Any:
    """Clear the chat history."""
    try:
        with open("chat_history.json", "w", encoding="utf-8") as f:
            json.dump([], f)
    except FileNotFoundError:
        pass  # File doesn't exist, nothing to clear
    return jsonify({"message": "Chat history cleared successfully."})


@app.route("/memory")
def serve_memory_page() -> Any:
    """Serve the memory management page."""
    return render_template("memory.html")


@app.route("/api/memory", methods=["GET", "POST"])
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



@app.route("/")
def serve_index() -> Any:
    """Serve the main single-page application."""

    browser_embed_url = _resolve_browser_embed_url()
    browser_agent_client_base = _resolve_browser_agent_client_base()
    return render_template(
        "index.html",
        browser_embed_url=browser_embed_url,
        browser_agent_client_base=browser_agent_client_base,
    )


@app.route("/<path:path>")
def serve_file(path: str) -> Any:
    """Serve any additional static files that live alongside index.html."""

    if path == "index.html":
        return serve_index()
    return send_from_directory(app.root_path, path)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5050)
