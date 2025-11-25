"""LangGraph-powered Multi-Agent orchestrator implementation."""

from __future__ import annotations

import json
import logging
import queue
import threading
from typing import Any, Dict, Iterable, Iterator, List, Literal, TypedDict, cast

import requests
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph

from .browser import (
    _browser_agent_timeout,
    _build_browser_agent_url,
    _call_browser_agent_chat,
    _extract_browser_error_message,
    _iter_browser_agent_bases,
)
from .config import (
    BROWSER_AGENT_CHAT_TIMEOUT,
    BROWSER_AGENT_FINAL_MARKER,
    BROWSER_AGENT_FINAL_NOTICE,
    BROWSER_AGENT_STREAM_TIMEOUT,
    BROWSER_AGENT_TIMEOUT,
    ORCHESTRATOR_MAX_TASKS,
    _current_datetime_line,
)
from .errors import BrowserAgentError, GeminiAPIError, IotAgentError, OrchestratorError
from .gemini import _call_gemini
from .history import _append_to_chat_history
from .iot import _call_iot_agent_command
from .settings import load_agent_connections, resolve_llm_config, load_memory_settings


class TaskSpec(TypedDict):
    """Specification describing the agent and command to run."""

    agent: Literal["faq", "browser", "iot"]
    command: str


class ExecutionResult(TypedDict, total=False):
    """Result payload returned by agent executions."""

    agent: Literal["faq", "browser", "iot"]
    command: str
    status: Literal["success", "error", "needs_info"]
    response: str | None
    error: str | None
    review_status: Literal["ok", "retry"]
    review_reason: str
    finalized: bool


class OrchestratorState(TypedDict, total=False):
    """State passed through the LangGraph orchestration pipeline."""

    user_input: str
    plan_summary: str | None
    raw_plan: Dict[str, Any] | None
    tasks: List[TaskSpec]
    executions: List[ExecutionResult]
    current_index: int
    retry_counts: Dict[int, int]
    agent_connections: Dict[str, bool]

class MultiAgentOrchestrator:
    """LangGraph-based orchestrator that routes work to specialised agents."""

    _AGENT_ALIASES = {
        "faq": "faq",
        "qa": "faq",
        "qa_agent": "faq",
        "qa-agent": "faq",
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
        "faq": "Life-Assistantエージェント",
        "browser": "ブラウザエージェント",
        "iot": "IoT エージェント",
    }

    MAX_RETRIES = 2

    _REVIEWER_PROMPT = """
現在の日時ー{current_datetime}

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
現在の日時ー{current_datetime}

あなたはマルチエージェントシステムのオーケストレーターです。与えられたユーザーの依頼を読み、実行すべきタスクを分析して下さい。

- 利用可能なエージェント:
  - "faq"（Life-Assistantエージェント）: 家庭内の出来事や料理、家電、人間関係に詳しい専門家エージェントで、IoTなどに対してナレッジベースに質問できます。
  - "browser": ブラウザ自動化エージェントで Web を閲覧・操作できます。
  - "iot": IoT エージェントを通じてデバイスの状態確認や操作ができます。
- 出力は JSON オブジェクトのみで、追加の説明やマークダウンを含めてはいけません。
- JSON には必ず次のキーを含めてください:
  - "plan_summary": 実行方針を 1 文でまとめた文字列。
  - "tasks": タスクの配列。各要素は {{"agent": <上記のいずれか>, "command": <エージェントに渡す命令>}} です。
- タスク数は 0〜{max_tasks} 件の範囲に収めてください。不要なタスクは作成しないでください。
        - エージェントを使わずに回答できる場合（一般的な知識質問や現在日時のような確認など）は、タスクを生成せずに plan_summary へ直接回答を書いてください。その際は「一般的な知識として回答します」のようなメタ文のみを書かず、ユーザーへ伝えるべき具体的な回答や説明まで含めてください。
        - ただし、少しでもエージェントを活用する余地がある場合は、自分で回答するよりもエージェントのタスク実行を優先し、得られた結果を plan_summary で伝えてください。
        - エージェントで対応できない内容の場合もタスクを生成せず、plan_summary でその旨やユーザーへ伝えるべき情報を説明してください。
- plan_summary やタスク説明では、ユーザーが求める具体的な内容を必ず書き切り、件数を指定された場合はその数ちょうどの候補を詳細（例: 献立名や理由）付きで提示してください。「〜を提案します」「〜を確認します」などの宣言だけで回答を終わらせてはいけません。
- **最優先事項:** ユーザーの依頼が少しでも不明確または曖昧な場合は、いかなるタスクも生成してはいけません。代わりに、`plan_summary` を使って、曖昧な点を具体的に指摘し、明確化するための質問をユーザーに投げかけてください。例えば、「最新の情報を知りたいですか？」「いくつ提案すればよろしいですか？」のように、具体的な選択肢や確認事項を提示してください。
- 上記の確認を経て、ユーザーの意図が完全に明確になった場合にのみ、エージェントのタスクを作成してください。最新情報や検証が必要な内容は、ブラウザやIoTなどの専門エージェントに任せてください。
""".strip()

    _ACTIONABILITY_PROMPT = """
あなたはマルチエージェント・オーケストレーターの安全管理者です。渡されたエージェント種別とコマンドが、そのまま実行できるだけの具体性を持っているか確認してください。

- 対応できない、または情報不足なら、JSON で {{"status": "needs_info", "message": "<不足している情報や確認すべき点を簡潔に日本語で列挙。ユーザーに尋ねる具体的な質問を含める>"}} を返してください。
- 十分に実行可能なら、JSON で {{"status": "ok"}} のみを返してください。
- Markdown や箇条書き記号は使わず、文章だけで短く書いてください。
- {agent_name} の役割: {agent_capability}
- 入力コマンド: {command}
""".strip()

    _AGENT_CAPABILITIES = {
        "browser": "Web検索・フォーム入力・クリックなどのブラウザ操作。どのサイト/URLで何をするか、完了条件、入力値が必要。",
        "faq": "生活全般のQ&Aとレシピ/家電の相談。質問内容、制約（人数・予算・アレルギー・時間帯など）が明確であるほど良い。",
        "iot": "登録済みデバイスの状態確認と操作。対象デバイス名/場所、希望する操作（オン/オフ・調整値）や時刻が必要。",
    }

    def __init__(self, llm_config: Dict[str, Any] | None = None) -> None:
        try:
            resolved_config = llm_config or resolve_llm_config("orchestrator")
        except Exception as exc:  # noqa: BLE001
            raise OrchestratorError(f"オーケストレーター用の LLM 設定を読み込めませんでした: {exc}") from exc

        api_key = resolved_config.get("api_key")
        if not api_key:
            raise OrchestratorError("オーケストレーター用の API キーが設定されていません。")

        try:
            self._llm = ChatOpenAI(
                model=resolved_config["model"],
                temperature=0.1,
                api_key=api_key,
                base_url=resolved_config.get("base_url") or None,
            )
        except Exception as exc:  # noqa: BLE001
            raise OrchestratorError(f"LangGraph LLM の初期化に失敗しました: {exc}") from exc

        self._llm_config = resolved_config
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
        if last_execution.get("status") in {"error", "needs_info"}:
            return state

        user_input = state["user_input"]
        agent_name = self._AGENT_DISPLAY_NAMES.get(last_execution["agent"], last_execution["agent"])
        command = last_execution["command"]
        result = last_execution.get("response") or last_execution.get("error") or "結果なし"

        timestamp_line = _current_datetime_line()
        prompt = self._REVIEWER_PROMPT.format(
            current_datetime=timestamp_line,
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

        agent_connections = state.get("agent_connections") or load_agent_connections()
        enabled_agents = [agent for agent, enabled in agent_connections.items() if enabled]
        disabled_agents = [agent for agent, enabled in agent_connections.items() if not enabled]
        state["agent_connections"] = agent_connections

        memory_settings = load_memory_settings()
        memory_enabled = memory_settings.get("enabled", True)
        long_term_memory = ""
        short_term_memory = ""

        if memory_enabled:
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

        prompt = self._planner_prompt(enabled_agents, disabled_agents)
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

        raw_tasks = plan_data.get("tasks")
        tasks = self._normalise_tasks(raw_tasks, allowed_agents=enabled_agents)
        plan_summary = str(plan_data.get("plan_summary") or plan_data.get("plan") or "").strip()
        skipped_agents: set[str] = set()
        if isinstance(raw_tasks, Iterable):
            for item in raw_tasks:
                if not isinstance(item, dict):
                    continue
                agent_raw = str(item.get("agent") or "").strip().lower()
                canonical = self._AGENT_ALIASES.get(agent_raw)
                if canonical and canonical in disabled_agents:
                    skipped_agents.add(canonical)
        if skipped_agents:
            skipped_labels = [self._AGENT_DISPLAY_NAMES.get(agent, agent) for agent in sorted(skipped_agents)]
            notice = "接続がオフのため次のエージェントタスクをスキップしました: " + ", ".join(skipped_labels)
            plan_summary = f"{plan_summary}\n\n{notice}" if plan_summary else notice

        return {
            "user_input": user_input,
            "plan_summary": plan_summary,
            "raw_plan": plan_data,
            "tasks": tasks,
            "executions": [],
            "current_index": 0,
            "retry_counts": {},
            "agent_connections": agent_connections,
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

    def _planner_prompt(self, enabled_agents: List[str], disabled_agents: List[str]) -> str:
        prompt = self._PLANNER_PROMPT.format(
            max_tasks=ORCHESTRATOR_MAX_TASKS,
            current_datetime=_current_datetime_line(),
        )
        enabled_labels = [self._AGENT_DISPLAY_NAMES.get(key, key) for key in enabled_agents]
        if enabled_labels:
            prompt += "\n\n現在利用可能なエージェント: " + ", ".join(enabled_labels)
        if disabled_agents:
            disabled_labels = [self._AGENT_DISPLAY_NAMES.get(key, key) for key in disabled_agents]
            prompt += "\n\n現在接続がオフのエージェント: " + ", ".join(disabled_labels)
            prompt += "。これらのエージェントを使うタスクは生成せず、必要なら他の手段で回答してください。"
        else:
            prompt += "\n\nすべてのエージェントが利用可能です。"
        return prompt

    def _normalise_tasks(self, raw_tasks: Any, *, allowed_agents: Iterable[str] | None = None) -> List[TaskSpec]:
        tasks: List[TaskSpec] = []
        allowed = set(allowed_agents) if allowed_agents is not None else None
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
            if allowed is not None and agent not in allowed:
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
        summary = self._condense_browser_summary(summary)
        if not summary:
            summary = "ブラウザエージェントからの応答を取得できませんでした。"
        finalized = BROWSER_AGENT_FINAL_MARKER in (payload.get("run_summary") or "")
        labeled_summary = summary
        if labeled_summary and not labeled_summary.startswith("最終結果:"):
            labeled_summary = f"最終結果: {labeled_summary}"
        return {
            "agent": "browser",
            "command": command,
            "status": "success",
            "response": labeled_summary,
            "error": None,
            "finalized": finalized,
        }

    def _browser_error_result(self, command: str, error: Exception) -> ExecutionResult:
        return {
            "agent": "browser",
            "command": command,
            "status": "error",
            "response": None,
            "error": str(error),
            "finalized": False,
        }

    def _assess_actionability(self, task: TaskSpec) -> Dict[str, str]:
        """Ask the LLM whether the given task is actionable for the target agent."""

        agent = task.get("agent")
        command = str(task.get("command") or "").strip()
        if not agent or not command:
            return {"status": "needs_info", "message": "実行コマンドが空です。もう一度入力してください。"}

        agent_name = self._AGENT_DISPLAY_NAMES.get(agent, agent)
        capability = self._AGENT_CAPABILITIES.get(agent, "")
        prompt = self._ACTIONABILITY_PROMPT.format(
            agent_name=agent_name,
            agent_capability=capability,
            command=command,
        )
        messages = [SystemMessage(content=prompt)]

        try:
            response = self._llm.invoke(messages)
            text = self._extract_text(response.content)
            data = self._parse_plan(text)
        except Exception as exc:  # noqa: BLE001
            logging.warning("Actionability check failed for %s: %s", agent, exc)
            return {"status": "ok"}

        status = str(data.get("status") or "").strip().lower()
        message = str(data.get("message") or "").strip()
        if status not in {"ok", "needs_info"}:
            status = "ok"
        return {"status": status, "message": message}

    def _maybe_request_clarification(self, task: TaskSpec) -> ExecutionResult | None:
        """Return a clarification result when the task is not actionable."""

        assessment = self._assess_actionability(task)
        if assessment.get("status") != "needs_info":
            return None

        message = assessment.get("message") or ""
        cleaned_message = message.strip() or "指示が曖昧なため実行できません。必要な条件を教えてください。"

        return {
            "agent": task.get("agent") or "agent",
            "command": task.get("command") or "",
            "status": "needs_info",
            "response": cleaned_message,
            "error": None,
            "finalized": True,
        }

    def _execute_task(self, task: TaskSpec) -> ExecutionResult:
        agent = task["agent"]
        command = task["command"]

        clarification = self._maybe_request_clarification(task)
        if clarification is not None:
            return clarification

        if agent == "faq":
            try:
                data = _call_gemini("/agent_rag_answer", method="POST", payload={"question": command})
            except GeminiAPIError as exc:
                return {
                    "agent": agent,
                    "command": command,
                    "status": "error",
                    "response": None,
                    "error": str(exc),
                }
            answer = str(data.get("answer") or "").strip() or "Life-Assistantエージェントから回答が得られませんでした。"
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
                data = _call_iot_agent_command(command)
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

        clarification = self._maybe_request_clarification(task)
        if clarification is not None:
            yield {
                "type": "result",
                "result": clarification,
            }
            return

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
            history_response = requests.get(
                history_url, timeout=_browser_agent_timeout(BROWSER_AGENT_TIMEOUT)
            )
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

        messages = history_data.get("messages") if isinstance(history_data, dict) else []

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
                response = requests.get(
                    stream_url,
                    stream=True,
                    timeout=_browser_agent_timeout(BROWSER_AGENT_STREAM_TIMEOUT),
                )
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
            except (requests.exceptions.RequestException, AttributeError) as exc:
                event_queue.put(
                    {
                        "kind": "stream_error",
                        "error": BrowserAgentError(
                            f"ブラウザエージェントのイベントストリームでエラーが発生しました: {exc}",
                        ),
                    }
                )
            except Exception as exc:  # noqa: BLE001
                logging.exception("Unexpected error while consuming browser agent stream: %s", exc)
                event_queue.put(
                    {
                        "kind": "stream_error",
                        "error": BrowserAgentError(
                            "ブラウザエージェントのイベントストリームで予期しないエラーが発生しました。",
                        ),
                    }
                )
            finally:
                event_queue.put({"kind": "stream_closed"})
                if response is not None:
                    try:
                        response.close()
                    except Exception:  # noqa: BLE001
                        pass

        def _chat_worker() -> None:
            try:
                response = requests.post(
                    chat_url,
                    json={"prompt": command, "new_task": True},
                    timeout=_browser_agent_timeout(BROWSER_AGENT_CHAT_TIMEOUT),
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
                            message_key = msg_id_raw
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
                    elif event_type == "reset":
                        progress_messages.clear()
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

    def _condense_browser_summary(self, summary: str) -> str:
        """Return a user-facing browser summary without step counts or notices."""

        cleaned = (summary or "").strip()
        if not cleaned:
            return ""
        cleaned = cleaned.replace(BROWSER_AGENT_FINAL_MARKER, "").strip()
        if BROWSER_AGENT_FINAL_NOTICE in cleaned:
            cleaned = cleaned.replace(BROWSER_AGENT_FINAL_NOTICE, "").strip()

        lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
        if not lines:
            return cleaned

        for line in lines:
            if line.startswith("最終報告:"):
                report = line.split(":", 1)[1].strip()
                if report:
                    return report

        for line in lines:
            if "ステップでエージェントが実行されました" in line:
                continue
            if line.startswith("※"):
                continue
            return line

        return lines[0]

    def _execution_result_text(self, result: ExecutionResult) -> str:
        agent_name = str(result.get("agent") or "agent")
        agent_label = self._AGENT_DISPLAY_NAMES.get(agent_name, agent_name)
        status = result.get("status")
        if status == "success":
            body = result.get("response") or "タスクを完了しました。"
        elif status == "needs_info":
            body = result.get("response") or "実行に必要な追加情報を入力してください。"
        else:
            body = result.get("error") or "タスクの実行に失敗しました。"
        return f"[{agent_label}] {body}"

    def _format_assistant_messages(
        self,
        plan_summary: str | None,
        executions: List[ExecutionResult],
    ) -> List[Dict[str, Any]]:
        messages: List[Dict[str, Any]] = []
        has_executions = bool(executions)
        if plan_summary:
            if has_executions:
                messages.append({"type": "plan", "text": f"計画: {plan_summary}"})
            else:
                messages.append({"type": "status", "text": plan_summary})

        for result in executions:
            messages.append(
                {
                    "type": "execution",
                    "agent": result["agent"],
                    "status": result.get("status"),
                    "text": self._execution_result_text(result),
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
        agent_connections = load_agent_connections()
        state: OrchestratorState = {
            "user_input": user_input,
            "plan_summary": None,
            "raw_plan": None,
            "tasks": [],
            "executions": [],
            "current_index": 0,
            "agent_connections": agent_connections,
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

        has_browser_execution = any(
            isinstance(result, dict) and result.get("agent") == "browser" for result in executions
        )
        if has_browser_execution:
            logged_browser_output = False
            for result in reversed(executions):
                if isinstance(result, dict) and result.get("agent") == "browser":
                    _append_to_chat_history("assistant", self._execution_result_text(result))
                    logged_browser_output = True
                    break
            if not logged_browser_output:
                for msg in reversed(assistant_messages):
                    text = msg.get("text")
                    if isinstance(text, str) and text.strip():
                        _append_to_chat_history("assistant", text)
                        break
        else:
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


def _llm_signature(config: Dict[str, Any]) -> tuple[str, str, str, str]:
    """Return a lightweight signature to detect LLM setting changes."""

    return (
        str(config.get("provider") or ""),
        str(config.get("model") or ""),
        str(config.get("base_url") or ""),
        str(config.get("api_key_fingerprint") or ""),
    )


_orchestrator_service: MultiAgentOrchestrator | None = None
_orchestrator_signature: tuple[str, str, str, str] | None = None


def _get_orchestrator() -> MultiAgentOrchestrator:
    global _orchestrator_service, _orchestrator_signature

    try:
        llm_config = resolve_llm_config("orchestrator")
        signature = _llm_signature(llm_config)
    except Exception as exc:  # noqa: BLE001
        raise OrchestratorError(str(exc)) from exc

    if _orchestrator_service is None or _orchestrator_signature != signature:
        try:
            _orchestrator_service = MultiAgentOrchestrator(llm_config=llm_config)
            _orchestrator_signature = signature
        except OrchestratorError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise OrchestratorError(f"オーケストレーターの初期化に失敗しました: {exc}") from exc
    return _orchestrator_service
