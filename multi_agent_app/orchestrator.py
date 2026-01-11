"""LangGraph-powered Multi-Agent orchestrator implementation."""

from __future__ import annotations

import ast
import asyncio
import json
import logging
import re
import threading
import time
from typing import Any, Dict, Iterable, List, Literal, TypedDict, cast, AsyncIterator

import httpx
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_anthropic import ChatAnthropic
from langgraph.graph import END, StateGraph

from multi_agent_app.config import BROWSER_AGENT_CONNECT_TIMEOUT
from .browser import (
    _browser_agent_timeout,
    _build_browser_agent_url,
    _call_browser_agent_chat,
    _call_browser_agent_chat_via_mcp,
    _extract_browser_error_message,
    _iter_browser_agent_bases,
    _USE_BROWSER_AGENT_MCP,
)
from .config import (
    BROWSER_AGENT_CHAT_TIMEOUT,
    BROWSER_AGENT_FINAL_MARKER,
    BROWSER_AGENT_FINAL_NOTICE,
    BROWSER_AGENT_STREAM_TIMEOUT,
    ORCHESTRATOR_MAX_TASKS,
    _current_datetime_line,
)
from .errors import (
    BrowserAgentError,
    LifestyleAPIError,
    IotAgentError,
    OrchestratorError,
    SchedulerAgentError,
)
from .lifestyle import _call_lifestyle
from .history import _append_to_chat_history, _read_chat_history
from .iot import _call_iot_agent_command, _count_iot_devices, _fetch_iot_device_context
from .scheduler import _call_scheduler_agent_chat
from .settings import load_agent_connections, resolve_llm_config, load_memory_settings
from .memory_manager import MemoryManager, get_memory_llm
from .agent_status import get_agent_availability


class TaskSpec(TypedDict):
    """Specification describing the agent and command to run."""

    agent: Literal["lifestyle", "browser", "iot", "scheduler"]
    command: str


class ExecutionResult(TypedDict, total=False):
    """Result payload returned by agent executions."""

    agent: Literal["lifestyle", "browser", "iot", "scheduler"]
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
    session_history: List[Dict[str, Any]]

class MultiAgentOrchestrator:
    """LangGraph-based orchestrator that routes work to specialised agents."""

    _AGENT_ALIASES = {
        "faq": "lifestyle",
        "qa": "lifestyle",
        "qa_agent": "lifestyle",
        "qa-agent": "lifestyle",
        "faq_gemini": "lifestyle",
        "gemini": "lifestyle",
        "lifestyle": "lifestyle",
        "life-style": "lifestyle",
        "life_style": "lifestyle",
        "knowledge": "lifestyle",
        "knowledge_base": "lifestyle",
        "docs": "lifestyle",
        "browser": "browser",
        "browser_agent": "browser",
        "web": "browser",
        "web_agent": "browser",
        "navigator": "browser",
        "iot": "iot",
        "iot_agent": "iot",
        "device": "iot",
        "scheduler": "scheduler",
        "scheduler_agent": "scheduler",
        "schedule": "scheduler",
        "calendar": "scheduler",
        "task": "scheduler",
    }

    _AGENT_DISPLAY_NAMES = {
        "lifestyle": "Life-Styleエージェント",
        "browser": "ブラウザエージェント",
        "iot": "IoT エージェント",
        "scheduler": "Scheduler エージェント",
    }

    _ORCHESTRATOR_LABEL = "[Orchestrator]"

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
"""

    _PLANNER_PROMPT = """
現在の日時ー{current_datetime}

【重要：記憶情報の取り扱い】
- 提供される「ユーザーの特性（長期記憶）」や「ユーザーの最近の動向（短期記憶）」は、あくまで**参考情報**です。
- これら過去の記憶情報だけに基づいて、ユーザーから明示的な指示がないタスクを勝手に開始してはいけません。
- 最優先すべきは**直近のユーザー入力**です。記憶情報は、直近のユーザー入力を理解し補助するためだけに使用してください。
-エージェントのみが使用でき、googleカレンダーやnotionなどの外部サービスは使うことができません。

あなたはマルチエージェントシステムのオーケストレーターです。ユーザーの依頼を読み、まず次の二択を厳密に判定してください。
1) **直接回答モード**: エージェントを使わなくても十分に答えられる場合は、計画もタスクも作らず `plan_summary` にユーザーへの最終回答をそのまま書く（挨拶や前置きだけにしない）。`tasks` は必ず空配列にする。
2) **計画・割当モード**: 外部操作・最新情報・デバイス制御・記録など、エージェントを使うことが不可欠または明確に有利な場合に限りタスクを設計して割り当てる。
この判定結果を JSON に正確に反映し、Markdown や余計な文章は出力しないでください。

- 利用可能なエージェント:
  - "lifestyle"（Life-Styleエージェント）:
    - **役割**: 家庭内の生活全般の知識（料理、掃除、家電、メンタルヘルスなど）に関する質問回答、相談、雑談を担当します。RAG（検索拡張生成）を用いて内部知識ベースから回答を生成します。
    - **重要**: 「献立」「レシピ」「食事の提案」など、食事に関する依頼は**必ず**このエージェントに割り当ててください。
    - **非対応**: ユーザーの個別の予定管理、日報の記録、外部サイトの操作、IoT機器の制御は**行いません**。「覚えておいて」などの記憶依頼も、予定や記録としての側面が強い場合はSchedulerへ、事実としての記憶なら長期記憶へ（ここではタスク化しない）となります。
  - "browser"（Browserエージェント）:
    - **役割**: Webブラウザを自動操作して、最新情報の検索、Webサイトの閲覧、フォーム入力などを行います。
    - **使い分け**: 内部知識（過去の学習データ）で完結する質問には lifestyle を使用し、天気、ニュース、最新価格、特定店舗の予約など**リアルタイム情報**や**外部サイト操作**が必要な場合にこちらを使用します。
    - **補完**: 検索エンジンやサイトが指定されていない場合は、Yahoo! JAPANなどをデフォルトとしてコマンドを作成してください。
  - "iot"（IoTエージェント）:
    - **役割**: IoTデバイス（ライト、ブザー、モーター、カメラ、センサーなど）の制御と状態確認を行います。
    - **方針**: ユーザーの意図が推測可能な場合は、**確認質問をせずに**積極的に実行コマンドを発行してください。
      - デバイスが特定できないが1台しかない場合 → そのデバイスIDを対象とする。
      - パラメータ（時間や強度）が不明 → 一般的なデフォルト値（例: 5秒）を設定する。
      - 周囲の状況を確認したいと言われたら、raspi4のカメラを使います。
  - "scheduler"（Schedulerエージェント）:
    - **役割**: ユーザーのスケジュール管理、タスク管理、日報（日記・メモ）の記録・更新・参照を担当します。「予定を入れて」「タスクに追加して」「日報を書いて」「今日の予定は？」などの依頼は必ずこのエージェントを選択します。
    - **仕様**:
      - **日付省略時**: 日付が明示されていない場合は**「今日」**を対象としてコマンドを作成してください。
      - **保存先**: **外部カレンダー（Google Calendar等）や外部メモアプリ（Notion等）との連携機能はありません。** ユーザーが「Googleカレンダーに入れて」と指示しても、外部連携はできないため、自動的に**エージェント内部のデータベース**に登録するコマンドを作成してください。その際、ユーザーに「Googleカレンダーは使えませんがよろしいですか？」と確認する必要はありません。
    - **重要**: **lifestyle エージェントは記録機能を持たないため、記録・保存・スケジュールに関する依頼は必ず scheduler に割り当ててください。**

- 出力は JSON オブジェクトのみで、追加の説明やマークダウンを含めてはいけません。
- JSON には必ず次のキーを含めてください:
  - "plan_summary": 実行方針または直接回答を 1 文でまとめた文字列。
  - "tasks": タスクの配列。各要素は {{"agent": <上記のいずれか>, "command": <エージェントに渡す命令>}} です。
- タスク数は 0〜{max_tasks} 件の範囲に収めてください。不要なタスクは作成しないでください。
  - 直接回答モードの場合は `tasks` を空にし、plan_summary にはユーザーへの最終回答本文を入れてください（「回答します」だけで終わらせない）。
  - エージェントを使わずに回答できる場合（一般的な知識質問や現在日時のような確認など）は、このモードを選びます。
  - エージェントタスクは「外部操作や最新データ取得が必要・明確に有利」なケースだけに限定し、迷ったら直接回答モードを選択してください。
  - エージェントで対応できない内容の場合もタスクを生成せず、plan_summary でその旨やユーザーへ伝えるべき情報を説明してください。
- plan_summary やタスク説明では、ユーザーが求める具体的な内容を必ず書き切り、件数を指定された場合はその数ちょうどの候補を詳細（例: 献立名や理由）付きで提示してください。「〜を提案します」「〜を確認します」などの宣言だけで回答を終わらせてはいけません。
- 実行予定の宣言だけで終わらないでください。「〜を実行します」「〜をします」「確認します」など未来形の宣言だけを書かず、必ず実際にタスクを作成して実行するか、タスクを作成しない場合はその理由とユーザーへの具体的な回答や追加質問を plan_summary に含めてください。
- **最優先事項（曖昧さへの対応と質問の抑制）:**
    - **ユーザーへの質問は極力避けてください。** ユーザーの入力は最初の1回で完結させることが理想です。
    - ユーザーの依頼が曖昧または情報不足であっても、可能な限りユーザーの意図や文脈を予測し、質問をせずにタスクを実行してください。「たぶんこういうことだろう」と合理的に推測できる場合は、その推測に基づいてエージェントへの命令を作成してください。
    - 例: 「電気を消して」と言われたが場所が不明な場合 → 文脈から推測するか、家中の主要な電気を消すコマンドを発行するなど、気を利かせた対応をする。
    - 例: 「日報を書いて」と言われたが保存先アプリが不明な場合 → デフォルトの Scheduler エージェント内部DBを使用し、保存先についての質問はしない。
    - **例外（質問すべきケース）:**
        - 物理的な配送や金銭的な取引など、取り返しがつかない操作において、住所や決済情報など**不可欠かつ推測不可能な重要情報**が欠落している場合に限り、`plan_summary` でユーザーに質問してください。
        - 質問する場合も、必要最小限の項目（最大3つまで）に絞り、ユーザーの負担を減らしてください。
- 上記の確認を経て、ユーザーの意図が完全に明確になった場合にのみ、エージェントのタスクを作成してください。最新情報や検証が必要な内容は、ブラウザやIoTなどの専門エージェントに任せてください。

【計画策定の思考プロセス】
計画を立てる際、および実行結果を受けて次のステップを検討する際は、常に以下の3点を整理して考え、plan_summary やタスク構成に反映させてください。
● Facts（事実）： これまでに確認された確定情報。
● Guesses（推測）： 現段階での仮説。
● Plan（計画）： 残りのタスクを実行するための計画。
特にエージェントからの実行結果を受け取った後の再計画では、何が「事実」として確定し、そこからどのような「推測」が成り立つかを踏まえて、次の「計画」を具体化してください。

【タスク command の作成ルール】
- **具体的かつシンプルに**: ユーザーの抽象的な要求を、具体的で誰にでもわかる明快な自然言語の指示文に変換してください。
  - 悪い例: "データ取得"
  - 良い例: "ブラウザでYahoo!ニュースを開き、トップニュースの見出しを3件取得して"
- **幻覚の禁止**: エージェントが実際に持っていない機能やコマンドを捏造しないでください。
- **自然言語**: JSON形式やプログラムコード（`func(arg)`）ではなく、平易な日本語の文章で指示してください。
- **補完**: 必要な情報（時間、件数、検索語句など）が不足している場合は、文脈から常識的なデフォルト値を補い、文章の中に自然に組み込んでください（例: 「ブザーを5秒間鳴らして」）。
- agent別ヒント:
  - browser: 目的のページ/検索キーワード/取得項目/件数を含める。結果の条件（例: 最新、公式、上位3件）を指示。
  - iot: device_id を明示（1台のみならそのIDをセット）、実行コマンド名と引数（例: duration=5.0, pattern='notify'）を具体的に書く。
  - scheduler: 日付・時刻・タイムゾーン前提（未指定なら今日/現在のTZ）を埋め、予定タイトル/日報タイトルと状態変更を明記。日報やタスクの登録・更新・完了は必ず scheduler に発行する。外部サービス（Google等）への言及はコマンドに含めず、「予定を登録して」のようにシンプルにする。
  - lifestyle: 質問内容や制約（人数・予算・時間帯など）を含め、要望の粒度を指定。日報や予定管理は担当しない。
"""

    _ACTIONABILITY_PROMPT = """
あなたはマルチエージェント・オーケストレーターの安全管理者です。渡されたエージェント種別とコマンドが、そのまま実行できるだけの具体性を持っているか確認してください。

- 対応できない、または情報不足なら、JSON で {{"status": "needs_info", "message": "<不足している情報や確認すべき点を簡潔に日本語で列挙。ユーザーに尋ねる具体的な質問を含める（最大3つまで）>"}} を返してください。
- 十分に実行可能なら、JSON で {{"status": "ok"}} のみを返してください。
- **質問は「不可欠な情報が欠けている時のみ」。推測できる要素はデフォルトを即採用し、質問はしない。**
- ブラウザタスクのデフォルト: サイト未指定→Yahoo! JAPAN、ニュースカテゴリ不明→主要トピック、件数不明→3件、日本語で実行。Google指定は禁止（明示された場合のみ）。
- IoTタスクのデフォルト:
  - デバイス特定不可だが1台のみ→そのデバイスと仮定。
  - パラメータ不明→duration=5.0秒などの一般的数値を採用。
  - **IoTエージェントは自律的に補完して実行するため、致命的な情報欠落（例: 操作そのものが不明）以外は `needs_info` にしないこと。**
- 取り返しのつかない操作（購入/予約/決済/送金/ログイン/個人情報入力など）で必須情報が欠ける場合だけ needs_info にする。
- Markdown や箇条書き記号は使わず、文章だけで短く書いてください。
- {agent_name} の役割: {agent_capability}
- 入力コマンド: {command}
"""

    _AGENT_CAPABILITIES = {
        "browser": "Web検索・フォーム入力・クリックなどのブラウザ操作。どのサイト/URLで何をするか、完了条件、入力値が必要。",
        "lifestyle": "生活全般のQ&Aとレシピ/家電の相談を担当。予定管理や日報更新などの記録タスクは扱わない。",
        "iot": "登録済みデバイスの状態確認と操作。対象デバイス名/場所、希望する操作（オン/オフ・調整値）や時刻が必要。",
        "scheduler": "予定やタスク、日報の確認・作成・更新・完了処理を担当。いつ・何を・どの時間帯に追加/更新するかを具体的に指示してください。日報への書き込みもここで行う。",
    }

    _IOT_ACTION_KEYWORDS = (
        "buzzer",
        "ブザー",
        "鳴ら",
        "led",
        "ライト",
        "点灯",
        "消灯",
        "モーター",
        "サーボ",
        "回して",
        "カメラ",
        "撮影",
        "写真",
        "demo",
        "オン",
        "オフ",
    )
    _BROWSER_RISK_KEYWORDS = (
        "購入",
        "注文",
        "予約",
        "決済",
        "支払",
        "支払い",
        "送金",
        "振込",
        "課金",
        "チャージ",
        "申し込み",
        "申込",
        "契約",
        "解約",
        "登録",
        "ログイン",
        "サインイン",
        "会員",
        "クレジット",
        "カード",
        "個人情報",
        "住所",
        "電話番号",
    )

    def __init__(self, llm_config: Dict[str, Any] | None = None) -> None:
        try:
            resolved_config = llm_config or resolve_llm_config("orchestrator")
        except Exception as exc:  # noqa: BLE001
            raise OrchestratorError(f"オーケストレーター用の LLM 設定を読み込めませんでした: {exc}") from exc

        api_key = resolved_config.get("api_key")
        if not api_key:
            raise OrchestratorError("オーケストレーター用の API キーが設定されていません。")

        try:
            model_name = resolved_config["model"]
            provider = resolved_config.get("provider", "openai")
            base_url = resolved_config.get("base_url") or None

            # o1 models and gpt-5 (in some environments) only support temperature=1
            is_fixed_temp_model = model_name.startswith("o1-") or model_name.startswith("gpt-5")
            temperature = 1 if is_fixed_temp_model else 0.1

            if provider == "gemini":
                self._llm = ChatGoogleGenerativeAI(
                    model=model_name,
                    temperature=temperature,
                    google_api_key=api_key,
                )
            elif provider == "claude":
                self._llm = ChatAnthropic(
                    model=model_name,
                    temperature=temperature,
                    api_key=api_key,
                    base_url=base_url,
                )
            else:
                self._llm = ChatOpenAI(
                    model=model_name,
                    temperature=temperature,
                    api_key=api_key,
                    base_url=base_url,
                )
        except Exception as exc:  # noqa: BLE001
            raise OrchestratorError(f"LangGraph LLM の初期化に失敗しました: {exc}") from exc

        self._llm_config = resolved_config
        self._graph = self._build_graph()

    def _build_graph(self) -> Any:
        graph: StateGraph[OrchestratorState] = StateGraph(OrchestratorState)
        graph.add_node("plan", self._plan_node_graph_sync)
        graph.add_node("execute", self._execute_node_sync)
        graph.add_node("review", self._review_node_sync)
        graph.add_conditional_edges("plan", self._plan_or_end, {"execute": "execute", "end": END})
        graph.add_edge("execute", "review")
        graph.add_edge("review", "plan")
        graph.set_entry_point("plan")
        return graph.compile()

    @staticmethod
    def _run_async(coro):
        """Run an async coroutine from sync contexts."""

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)

        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    def _plan_node_sync(self, state: OrchestratorState, *, incremental: bool = False) -> OrchestratorState:
        return self._run_async(self._plan_node(state, incremental=incremental))

    def _plan_node_graph_sync(self, state: OrchestratorState) -> OrchestratorState:
        incremental = bool(state.get("executions"))
        return self._run_async(self._plan_node(state, incremental=incremental))

    def _execute_node_sync(self, state: OrchestratorState) -> OrchestratorState:
        return self._run_async(self._execute_node(state))

    def _review_node_sync(self, state: OrchestratorState) -> OrchestratorState:
        return self._run_async(self._review_node(state))

    def _continue_or_end(self, state: OrchestratorState) -> str:
        tasks = state.get("tasks") or []
        index = state.get("current_index", 0)
        return "continue" if index < len(tasks) else "end"

    def _plan_or_end(self, state: OrchestratorState) -> str:
        tasks = state.get("tasks") or []
        return "execute" if tasks else "end"

    async def _review_node(self, state: OrchestratorState) -> OrchestratorState:
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
            response = await asyncio.to_thread(self._llm.invoke, messages)
            review_text = self._extract_text(response)
            review_data = self._parse_plan(review_text)
        except (OrchestratorError, Exception) as exc:  # noqa: BLE001
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

    def _execution_context_for_prompt(self, executions: List[ExecutionResult]) -> str:
        """Render completed execution results for the planner prompt."""

        lines: list[str] = []
        for idx, item in enumerate(executions, start=1):
            agent = item.get("agent") or "agent"
            agent_label = self._AGENT_DISPLAY_NAMES.get(agent, agent)
            command = (item.get("command") or "").strip()
            status = item.get("status") or "unknown"
            outcome = str(item.get("response") or item.get("error") or "").strip() or "結果なし"
            if len(outcome) > 300:
                outcome = outcome[:300] + "..."
            header_bits = []
            if command:
                header_bits.append(f"command={command}")
            header_bits.append(f"status={status}")
            header = " / ".join(header_bits)
            lines.append(f"{idx}. [{agent_label}] {header}\n   {outcome}")
        return "\n".join(lines)

    def _compact_execution_text(self, text: str, *, max_chars: int = 420) -> str:
        cleaned = (text or "").strip()
        if not cleaned:
            return ""
        marker = "【使用したファイル】"
        if marker in cleaned:
            cleaned = cleaned.split(marker, 1)[0].strip()
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if len(cleaned) > max_chars:
            cleaned = cleaned[:max_chars].rstrip() + "..."
        return cleaned

    def _extract_price_from_text(self, text: str) -> str:
        cleaned = text or ""
        if not cleaned:
            return ""
        match = re.search(r"¥\s*([0-9][0-9,\\.]+)", cleaned)
        if match:
            return match.group(1)
        match = re.search(r"([0-9][0-9,\\.]+)\s*円", cleaned)
        if match:
            return match.group(1)
        match = re.search(r"([0-9][0-9,\\.]+)\s*JPY", cleaned, re.IGNORECASE)
        if match:
            return match.group(1)
        return ""

    def _execution_placeholders(self, executions: List[ExecutionResult]) -> Dict[str, str]:
        latest_by_agent: Dict[str, str] = {}
        last_success = ""
        for result in reversed(executions or []):
            if result.get("status") != "success":
                continue
            response = str(result.get("response") or "").strip()
            if not response:
                continue
            if not last_success:
                last_success = response
            agent = str(result.get("agent") or "").strip()
            if agent and agent not in latest_by_agent:
                latest_by_agent[agent] = response

        lifestyle_text = self._compact_execution_text(latest_by_agent.get("lifestyle", ""))
        browser_text = self._compact_execution_text(latest_by_agent.get("browser", ""))
        iot_text = self._compact_execution_text(latest_by_agent.get("iot", ""))
        scheduler_text = self._compact_execution_text(latest_by_agent.get("scheduler", ""))
        last_text = self._compact_execution_text(last_success)
        price_value = self._extract_price_from_text(latest_by_agent.get("browser", ""))

        placeholders = {
            "advice": lifestyle_text,
            "life": lifestyle_text,
            "life_result": lifestyle_text,
            "life_response": lifestyle_text,
            "lifestyle": lifestyle_text,
            "lifestyle_result": lifestyle_text,
            "lifestyle_response": lifestyle_text,
            "browser": browser_text,
            "browser_result": browser_text,
            "browser_response": browser_text,
            "iot": iot_text,
            "iot_result": iot_text,
            "iot_response": iot_text,
            "scheduler": scheduler_text,
            "scheduler_result": scheduler_text,
            "scheduler_response": scheduler_text,
            "result": last_text,
            "last_result": last_text,
            "summary": self._compact_execution_text(last_success, max_chars=200),
        }
        if price_value:
            placeholders["price"] = price_value
        elif browser_text:
            placeholders["price"] = browser_text
        return {key: value for key, value in placeholders.items() if value}

    def _execution_context_for_task_command(self, executions: List[ExecutionResult]) -> str:
        if not executions:
            return ""
        latest_by_agent: Dict[str, str] = {}
        for result in reversed(executions or []):
            if result.get("status") != "success":
                continue
            agent = str(result.get("agent") or "").strip()
            if not agent or agent in latest_by_agent:
                continue
            response = str(result.get("response") or "").strip()
            if response:
                latest_by_agent[agent] = self._compact_execution_text(response, max_chars=280)
        if not latest_by_agent:
            return ""

        lines = ["【これまでの実行結果】"]
        ordered_agents = ["lifestyle", "scheduler", "browser", "iot"]
        used = set()
        for agent in ordered_agents:
            if agent in latest_by_agent:
                label = self._AGENT_DISPLAY_NAMES.get(agent, agent)
                lines.append(f"- {label}: {latest_by_agent[agent]}")
                used.add(agent)
        for agent, summary in latest_by_agent.items():
            if agent in used:
                continue
            label = self._AGENT_DISPLAY_NAMES.get(agent, agent)
            lines.append(f"- {label}: {summary}")

        return "\n".join(lines)

    def _apply_execution_placeholders(self, text: str, executions: List[ExecutionResult]) -> str:
        if not text or "[" not in text:
            return text
        replacements = self._execution_placeholders(executions)
        if not replacements:
            return text
        updated = text
        price_value = replacements.get("price")
        if price_value:
            updated = re.sub(r"¥?\[price\]", price_value, updated)

        def _replace(match: re.Match[str]) -> str:
            key = match.group(1).strip().lower()
            value = replacements.get(key)
            return value if value else match.group(0)

        return re.sub(r"\[([a-zA-Z0-9_-]+)\]", _replace, updated)

    def _apply_execution_results_to_tasks(
        self,
        tasks: List[TaskSpec],
        executions: List[ExecutionResult],
    ) -> List[TaskSpec]:
        if not tasks or not executions:
            return tasks
        context_block = self._execution_context_for_task_command(executions)
        updated: List[TaskSpec] = []
        for task in tasks:
            if not isinstance(task, dict):
                continue
            command = str(task.get("command") or "")
            updated_command = self._apply_execution_placeholders(command, executions)
            replaced = updated_command != command
            if context_block and not replaced and "【これまでの実行結果】" not in updated_command:
                updated_command = updated_command.rstrip() + "\n\n" + context_block
            updated.append({**task, "command": updated_command})
        return updated

    def _pending_tasks_for_prompt(
        self,
        tasks: List[TaskSpec],
        executions: List[ExecutionResult],
    ) -> List[TaskSpec]:
        if not tasks:
            return []
        completed = {
            (res.get("agent"), (res.get("command") or "").strip())
            for res in executions
            if res.get("status") == "success"
        }
        pending: List[TaskSpec] = []
        for task in tasks:
            if not isinstance(task, dict):
                continue
            agent = task.get("agent")
            command = (task.get("command") or "").strip()
            if (agent, command) in completed:
                continue
            pending.append(task)
        return pending

    def _tasks_context_for_prompt(self, tasks: List[TaskSpec]) -> str:
        lines: list[str] = []
        for idx, task in enumerate(tasks, start=1):
            agent = task.get("agent") or "agent"
            agent_label = self._AGENT_DISPLAY_NAMES.get(agent, agent)
            command = (task.get("command") or "").strip() or "内容が空のタスク"
            lines.append(f"{idx}. [{agent_label}] {command}")
        return "\n".join(lines)

    async def _plan_node(self, state: OrchestratorState, *, incremental: bool = False) -> OrchestratorState:
        user_input = state.get("user_input", "")
        if not user_input:
            raise OrchestratorError("オーケストレーターに渡された入力が空でした。")

        agent_connections = state.get("agent_connections") or load_agent_connections()
        availability = await get_agent_availability()
        enabled_agents = [
            agent for agent, enabled in agent_connections.items()
            if enabled and availability.get(agent, True)
        ]
        disabled_agents = [agent for agent, enabled in agent_connections.items() if not enabled]
        disconnected_agents = [
            agent for agent, enabled in agent_connections.items()
            if enabled and not availability.get(agent, True)
        ]
        state["agent_connections"] = agent_connections

        previous_plan_summary = str(state.get("plan_summary") or "").strip()
        previous_executions: List[ExecutionResult] = list(state.get("executions") or [])
        previous_tasks: List[TaskSpec] = list(state.get("tasks") or [])
        pending_tasks = self._pending_tasks_for_prompt(previous_tasks, previous_executions)

        device_context: str | None = None
        if agent_connections.get("iot"):
            try:
                device_context = await _fetch_iot_device_context()
            except Exception as exc:  # noqa: BLE001 - best-effort enrichment
                logging.info("Failed to fetch IoT device context for planner prompt: %s", exc)

        memory_settings = load_memory_settings()
        memory_enabled = memory_settings.get("enabled", True)
        long_term_memory = ""
        short_term_memory = ""

        if memory_enabled:
            try:
                lt_mgr = MemoryManager("long_term_memory.json")
                long_term_memory = lt_mgr.get_formatted_memory()
            except Exception as exc:
                logging.warning("Failed to load long-term memory: %s", exc)
                long_term_memory = ""

            try:
                st_mgr = MemoryManager("short_term_memory.json")
                short_term_memory = st_mgr.get_formatted_memory()
            except Exception as exc:
                logging.warning("Failed to load short-term memory: %s", exc)
                short_term_memory = ""

        history_for_prompt = state.get("session_history") or []
        if not history_for_prompt:
            history_for_prompt = self._history_from_last_user_turn(self._load_recent_chat_history(limit=20))
        history_entries = self._normalise_history_entries(history_for_prompt)
        history_prompt = "\n".join([f"{msg['role']}: {msg['content']}" for msg in history_entries])

        prompt = self._planner_prompt(enabled_agents, disabled_agents, device_context)
        execution_context = self._execution_context_for_prompt(previous_executions)
        prompt += "\n\nこれまでの実行結果:\n"
        prompt += execution_context or "まだタスクは実行されていません。"
        if pending_tasks:
            prompt += "\n\n未完了タスク一覧:\n"
            prompt += self._tasks_context_for_prompt(pending_tasks)
        if incremental:
            if previous_plan_summary:
                prompt += "\n\n直前の計画要約:\n" + previous_plan_summary
            prompt += (
                "\n\n【重要：再計画の指示】\n"
                "1. **情報の引継ぎ**: 直前のタスク実行結果（上記の「これまでの実行結果」）を読み取り、次に実行するタスクの command にその内容を具体的に反映させてください。\n"
                "   - 例: Lifestyleエージェントが提案した献立名を、Schedulerの登録内容に書き写す。\n"
                "   - 例: Browserエージェントが検索したURLや店名を、次のタスクの入力として使う。\n"
                "2. **未完了タスクの更新**: 「未完了タスク一覧」にあるタスクは原則として維持しつつ、情報が更新された場合は command を書き換えて再定義してください。不要になった場合は、plan_summary に理由を書いて停止してください。\n"
                "3. **重複の禁止**: 完了済み(success)のタスクは出力に含めないでください。\n"
                "4. **無限ループの防止**: エージェントの実行結果が改善せず、同じようなエラーや結果が繰り返されていると判断した場合、あるいは2回以上ほとんど同じ出力が直近の会話履歴に確認された場合は、新たなタスクを生成せずに停止してください。その場合、plan_summary に停止理由と現在の状況をユーザーに報告する形で記述してください。\n"
            )
        if long_term_memory:
            prompt += "\n\nユーザーの特性:\n" + long_term_memory
        if short_term_memory:
            prompt += "\n\nユーザーの最近の動向:\n" + short_term_memory
        prompt += "\n\n以下は直近の会話履歴です:\n" + history_prompt
        messages = [SystemMessage(content=prompt), HumanMessage(content=user_input)]

        plan_data: Dict[str, Any] | None = None
        last_plan_text: str | None = None
        for attempt in range(3):
            try:
                response = await asyncio.to_thread(self._llm.invoke, messages)
                plan_text = self._extract_text(response)
                last_plan_text = plan_text
                plan_data = self._parse_plan(plan_text)
                break
            except Exception as exc:  # noqa: BLE001
                logging.warning("Plan generation attempt %d failed: %s", attempt + 1, exc)
                if attempt == 2:
                    # Final fallback: treat the raw text as a direct answer with no tasks
                    if last_plan_text:
                        logging.error("Planner JSON parse failed; falling back to direct answer text.")
                        plan_data = {"plan_summary": last_plan_text.strip(), "tasks": []}
                        break
                    if isinstance(exc, OrchestratorError):
                        raise exc
                    raise OrchestratorError(f"プラン生成に失敗しました: {exc}") from exc

        raw_tasks = plan_data.get("tasks")
        tasks = self._normalise_tasks(raw_tasks, allowed_agents=enabled_agents)
        plan_summary = str(plan_data.get("plan_summary") or plan_data.get("plan") or "").strip()
        if incremental and pending_tasks and not tasks:
            logging.warning("Planner returned no tasks despite pending tasks; continuing with pending tasks.")
            tasks = pending_tasks
            if not plan_summary or plan_summary == previous_plan_summary:
                plan_summary = "未完了のタスクがあるため継続します。"
            plan_data["tasks"] = tasks
        skipped_agents: set[str] = set()
        if isinstance(raw_tasks, Iterable):
            for item in raw_tasks:
                if not isinstance(item, dict):
                    continue
                agent_raw = str(item.get("agent") or "").strip().lower()
                canonical = self._AGENT_ALIASES.get(agent_raw)
                if canonical and canonical in disabled_agents:
                    skipped_agents.add(canonical)
        notices: list[str] = []
        if skipped_agents:
            skipped_labels = [self._AGENT_DISPLAY_NAMES.get(agent, agent) for agent in sorted(skipped_agents)]
            notices.append("接続がオフのため次のエージェントタスクをスキップしました: " + ", ".join(skipped_labels))
        if disconnected_agents:
            disconnected_labels = [self._AGENT_DISPLAY_NAMES.get(agent, agent) for agent in sorted(disconnected_agents)]
            notices.append("接続できないため次のエージェントは使用しません: " + ", ".join(disconnected_labels))
        if notices:
            notice_text = "\n".join(notices)
            plan_summary = f"{plan_summary}\n\n{notice_text}" if plan_summary else notice_text

        return {
            "user_input": user_input,
            "plan_summary": plan_summary,
            "raw_plan": plan_data,
            "tasks": tasks,
            "executions": previous_executions if incremental else [],
            "current_index": 0,
            "retry_counts": state.get("retry_counts") or {},
            "agent_connections": agent_connections,
        }

    async def _execute_node(self, state: OrchestratorState) -> OrchestratorState:
        tasks = list(state.get("tasks") or [])
        tasks = self._apply_execution_results_to_tasks(tasks, list(state.get("executions") or []))
        state["tasks"] = tasks
        index = state.get("current_index", 0)
        executions = list(state.get("executions") or [])

        if index >= len(tasks):
            return {"executions": executions, "current_index": index}

        task = tasks[index]
        result = await self._execute_task(task)
        executions.append(result)

        return {"executions": executions, "current_index": index + 1, "tasks": tasks}

    def _extract_text(self, content: Any) -> str:
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        text_attr = getattr(content, "text", None)
        if isinstance(text_attr, str):
            return text_attr
        content_blocks = getattr(content, "content_blocks", None)
        if isinstance(content_blocks, list):
            return self._extract_text(content_blocks)
        if hasattr(content, "content"):
            return self._extract_text(getattr(content, "content"))
        if isinstance(content, list):
            pieces: list[str] = []
            for item in content:
                if isinstance(item, str):
                    pieces.append(item)
                    continue
                if isinstance(item, dict):
                    block_type = item.get("type") or item.get("block_type")
                    text = item.get("text") or item.get("output_text") or item.get("content")
                    if isinstance(text, str) and (block_type in {None, "text", "output_text"} or "text" in item):
                        pieces.append(text)
                    continue
                item_text = getattr(item, "text", None)
                if isinstance(item_text, str):
                    pieces.append(item_text)
            return "".join(pieces)
        if isinstance(content, dict):
            # Avoid Python repr (single quotes) which breaks JSON parsing
            for key in ("text", "output_text", "content"):
                value = content.get(key)
                if isinstance(value, str):
                    return value
            if "content" in content:
                return self._extract_text(content["content"])
            try:
                return json.dumps(content, ensure_ascii=False)
            except Exception:  # noqa: BLE001
                return str(content)
        return str(content)

    def _parse_plan(self, raw: Any) -> Dict[str, Any]:
        def try_parse(text: str) -> Dict[str, Any] | None:
            if not isinstance(text, str) or not text.strip():
                return None
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                pass
            # Try removing trailing commas
            try:
                sanitized = re.sub(r",\s*([\]}])", r"\1", text)
                return json.loads(sanitized)
            except json.JSONDecodeError:
                pass
            # Handle Python-style dicts with single quotes
            try:
                literal = ast.literal_eval(text)
                if isinstance(literal, dict):
                    return literal
            except Exception:  # noqa: BLE001
                pass
            return None

        if isinstance(raw, dict):
            return raw

        raw_str = raw if isinstance(raw, str) else str(raw)
        raw_str = raw_str.strip()

        # Heuristic 0: 完全なプレーンテキストで波括弧が無く、plan_summary/tasks キーも含まない場合はそのまま直接回答として扱う
        if "{" not in raw_str and "}" not in raw_str and "plan_summary" not in raw_str and "tasks" not in raw_str:
            return {"plan_summary": raw_str, "tasks": []}

        # Attempt 1: Direct parsing
        parsed = try_parse(raw_str)
        if isinstance(parsed, dict):
            return parsed

        # Heuristic 1: LLM が外側の `{` や `}` を落としたケースを補正
        if '"plan_summary"' in raw_str and '"tasks"' in raw_str:
            candidate = raw_str
            if not candidate.lstrip().startswith("{"):
                candidate = "{" + candidate
            if not candidate.rstrip().endswith("}"):
                candidate = candidate + "}"
            parsed = try_parse(candidate)
            if isinstance(parsed, dict):
                return parsed

        # Heuristic 2: 先頭が { で始まらないが、どこかに { がある場合は最初の { から末尾 } まで切り出す
        if '{' in raw_str and '}' in raw_str:
            first = raw_str.find('{')
            last = raw_str.rfind('}')
            inner = raw_str[first : last + 1]
            parsed = try_parse(inner)
            if isinstance(parsed, dict):
                return parsed

        # Heuristic 3: plan_summary/tasks が含まれるが JSON でない場合は、キーと値を簡易抽出して補完
        if "plan_summary" in raw_str and "tasks" in raw_str:
            try:
                ps_match = re.search(r'"plan_summary"\\s*:\\s*"([^"]+)"', raw_str)
                plan_summary = ps_match.group(1).strip() if ps_match else raw_str
                tasks_match = re.search(r'"tasks"\\s*:\\s*(\\[.*?\\])', raw_str)
                tasks_str = tasks_match.group(1) if tasks_match else "[]"
                tasks = try_parse(tasks_str) if tasks_str else []
                if not isinstance(tasks, list):
                    tasks = []
                return {"plan_summary": plan_summary, "tasks": tasks}
            except Exception:  # noqa: BLE001
                pass

        # Attempt 2: Extract from Markdown code blocks
        code_block_pattern = r"```(?:json)?\s*(.*?)\s*```"
        match = re.search(code_block_pattern, raw_str, re.DOTALL)
        if match:
            block_content = match.group(1)
            parsed = try_parse(block_content)
            if isinstance(parsed, dict):
                return parsed
            
            # Try finding braces inside the block
            brace_pattern = r"(\{.*\})"
            brace_match = re.search(brace_pattern, block_content, re.DOTALL)
            if brace_match:
                parsed = try_parse(brace_match.group(1))
                if isinstance(parsed, dict):
                    return parsed

        # Attempt 3: Extract from first '{' to last '}' in the whole text
        brace_pattern = r"(\{.*\})"
        match = re.search(brace_pattern, raw_str, re.DOTALL)
        if match:
            parsed = try_parse(match.group(1))
            if isinstance(parsed, dict):
                return parsed

        # If all attempts fail
        logging.error(f"JSON Parse Failed. Raw output:\n{raw_str}")
        raise OrchestratorError("プラン応答の JSON 解析に失敗しました。")

    def _planner_prompt(self, enabled_agents: List[str], disabled_agents: List[str], device_context: str | None) -> str:
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
        if device_context is not None:
            prompt += "\n\n利用可能なIoTデバイス情報:\n" + (device_context or "No devices are currently registered.")
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

    @staticmethod
    def _has_browser_final_marker(text: str) -> bool:
        """Return True if the text contains an explicit final marker from the Browser Agent."""

        cleaned = text or ""
        return bool(cleaned) and (
            BROWSER_AGENT_FINAL_MARKER in cleaned or BROWSER_AGENT_FINAL_NOTICE in cleaned or "最終報告:" in cleaned
        )

    @staticmethod
    def _latest_message_id(messages: Any) -> int:
        """Return the largest numeric message id from a Browser Agent history list."""

        if not isinstance(messages, list):
            return -1
        latest = -1
        for entry in messages:
            if not isinstance(entry, dict):
                continue
            message_id = entry.get("id")
            if isinstance(message_id, int) and message_id > latest:
                latest = message_id
        return latest

    async def _poll_browser_history_summary(
        self,
        base: str,
        since_id: int | None = None,
        timeout: float = 900.0,
        interval: float = 1.0,
    ) -> str:
        """Best-effort polling of Browser Agent history when streaming is unavailable.

        This fetches `/api/history` repeatedly until a new assistant message that
        looks like a final report appears, or the timeout elapses.
        """

        deadline = time.monotonic() + timeout
        history_url = _build_browser_agent_url(base, "/api/history")
        last_seen = since_id if isinstance(since_id, int) else -1
        latest_summary = ""

        async with httpx.AsyncClient(timeout=_browser_agent_timeout(10.0)) as client:
            while time.monotonic() < deadline:
                try:
                    response = await client.get(history_url)
                    if not response.is_success:
                        break
                    data = response.json()
                except Exception as exc:  # noqa: BLE001
                    logging.debug("Browser history poll failed: %s", exc)
                    await asyncio.sleep(interval)
                    continue

                messages = data.get("messages") if isinstance(data, dict) else None
                summary = self._summarise_browser_messages(messages)
                latest_id = self._latest_message_id(messages)

                if summary:
                    if self._has_browser_final_marker(summary):
                        return summary
                    if latest_id > last_seen or summary != latest_summary:
                        latest_summary = summary

                if latest_id > last_seen:
                    last_seen = latest_id

                await asyncio.sleep(interval)

        # Timed out without a final marker; return the latest summary we observed (if any).
        return latest_summary

    async def _fetch_browser_history_snapshot(self, base: str) -> tuple[int, str]:
        """Return (latest_message_id, latest_assistant_summary) from Browser Agent history."""

        history_url = _build_browser_agent_url(base, "/api/history")
        try:
            async with httpx.AsyncClient(timeout=_browser_agent_timeout(10.0)) as client:
                response = await client.get(history_url)
                if not response.is_success:
                    return -1, ""
                data = response.json()
        except Exception:  # noqa: BLE001 - best effort
            return -1, ""

        messages = data.get("messages") if isinstance(data, dict) else None
        latest_id = self._latest_message_id(messages)
        summary = self._summarise_browser_messages(messages)
        return latest_id, summary

    def _browser_result_from_payload(
        self,
        command: str,
        payload: Dict[str, Any],
        fallback_summary: str | None = None,
    ) -> ExecutionResult:
        run_summary = str(payload.get("run_summary") or "").strip()
        messages_summary = self._summarise_browser_messages(payload.get("messages"))
        fallback = fallback_summary.strip() if fallback_summary else ""

        # Prioritize sources that contain the final report marker
        summary = ""

        # First priority: stream fallback with final report (most reliable when both containers run)
        if fallback and self._has_browser_final_marker(fallback):
            summary = fallback
        # Second priority: HTTP response run_summary with final report
        elif run_summary and self._has_browser_final_marker(run_summary):
            summary = run_summary
        # Third priority: messages summary with final report
        elif messages_summary and self._has_browser_final_marker(messages_summary):
            summary = messages_summary
        # Fourth priority: any non-empty run_summary from HTTP response
        elif run_summary:
            summary = run_summary
        # Fifth priority: stream fallback
        elif fallback:
            summary = fallback
        # Last resort: messages summary
        elif messages_summary:
            summary = messages_summary

        summary = self._condense_browser_summary(summary)
        if not summary:
            summary = "ブラウザエージェントからの応答を取得できませんでした。"
        finalized = any(
            self._has_browser_final_marker(text)
            for text in (
                run_summary,
                fallback,
                messages_summary,
            )
        )
        if self._browser_payload_indicates_failure(payload, summary):
            return {
                "agent": "browser",
                "command": command,
                "status": "error",
                "response": None,
                "error": summary,
                "finalized": finalized,
            }
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

    def _browser_payload_indicates_failure(self, payload: Dict[str, Any], summary: str) -> bool:
        if isinstance(payload, dict):
            success = payload.get("success")
            if isinstance(success, bool) and not success:
                return True
            status = payload.get("status")
            if isinstance(status, str) and status.strip().lower() in {"error", "failed", "failure"}:
                return True

        lowered = (summary or "").lower()
        failure_phrases = (
            "success: false",
            "取得できませんでした",
            "失敗しました",
            "失敗",
            "エラー",
            "不正なレスポンス",
        )
        return any(phrase in lowered for phrase in failure_phrases)

    def _browser_result_is_failure(self, result: ExecutionResult) -> bool:
        if result.get("status") != "success":
            return True
        response = str(result.get("response") or "")
        return self._browser_payload_indicates_failure({}, response)

    def _browser_fallback_command(self, command: str) -> str | None:
        if not command:
            return None
        if "検索" in command:
            return None
        lowered = command.lower()
        if "yahoo" not in lowered and "ヤフー" not in command:
            return None

        term = ""
        quoted = re.search(r"[\"“”]([^\"“”]+)[\"“”]", command)
        if quoted:
            term = quoted.group(1).strip()
        else:
            bracketed = re.search(r"『([^』]+)』", command)
            if bracketed:
                term = bracketed.group(1).strip()
        if not term:
            if "アメリカ" in command:
                term = "アメリカ ニュース"
            elif "米国" in command:
                term = "米国 ニュース"
            else:
                term = "ニュース"

        return (
            f"ブラウザでYahoo!ニュースの検索で「{term}」を検索し、"
            "検索結果の上位3件の見出しとURLを取得して。カテゴリページの遷移は不要です。"
        )

    async def _augment_browser_payload_with_history(self, payload: Dict[str, Any], *, timeout: float = 900.0) -> str:
        """Fetch the latest history summary as a fallback when the payload lacks a final marker."""

        try:
            run_summary = str(payload.get("run_summary") or "").strip()
            messages_summary = self._summarise_browser_messages(payload.get("messages"))
            if self._has_browser_final_marker(run_summary) or self._has_browser_final_marker(messages_summary):
                return ""
        except Exception:  # noqa: BLE001 - defensive
            pass

        bases = _iter_browser_agent_bases()
        if not bases:
            return ""

        # Use the first reachable base; polling already returns quickly when history is short.
        return await self._poll_browser_history_summary(bases[0], timeout=timeout, interval=1.0)

    def _browser_error_result(self, command: str, error: Exception) -> ExecutionResult:
        return {
            "agent": "browser",
            "command": command,
            "status": "error",
            "response": None,
            "error": str(error),
            "finalized": False,
        }

    def _execution_error_result(self, agent: str, command: str, error: Exception) -> ExecutionResult:
        return {
            "agent": agent,
            "command": command,
            "status": "error",
            "response": None,
            "error": str(error),
        }

    def _iot_action_is_clear(self, command: str) -> bool:
        """Heuristic to decide if an IoT command is actionable without clarification."""

        if not command:
            return False

        lowered = command.lower()
        return any(keyword in lowered or keyword in command for keyword in self._IOT_ACTION_KEYWORDS)

    def _browser_action_is_high_risk(self, command: str) -> bool:
        """Return True when the browser task involves irreversible or sensitive actions."""

        if not command:
            return False

        lowered = command.lower()
        return any(keyword in lowered or keyword in command for keyword in self._BROWSER_RISK_KEYWORDS)

    async def _assess_actionability(self, task: TaskSpec) -> Dict[str, str]:
        """Ask the LLM whether the given task is actionable for the target agent."""

        agent = task.get("agent")
        command = str(task.get("command") or "").strip()
        if not agent or not command:
            return {"status": "needs_info", "message": "実行コマンドが空です。もう一度入力してください。"}

        if agent == "iot":
            # IoT は「迷いなく実行」を優先。デバイスが複数でも基本は実行に進む。
            try:
                device_count = await _count_iot_devices()
            except Exception as exc:  # noqa: BLE001 - best-effort
                logging.debug("Failed to count IoT devices for actionability check: %s", exc)
                device_count = None

            if self._iot_action_is_clear(command):
                return {"status": "ok", "message": ""}
            if device_count is None or device_count >= 1:
                return {"status": "ok", "message": ""}

        agent_name = self._AGENT_DISPLAY_NAMES.get(agent, agent)
        capability = self._AGENT_CAPABILITIES.get(agent, "")
        prompt = self._ACTIONABILITY_PROMPT.format(
            agent_name=agent_name,
            agent_capability=capability,
            command=command,
        )
        messages = [
            SystemMessage(content=prompt),
            HumanMessage(content="上記のタスクの実行可能性を判定してJSONで回答してください。"),
        ]

        try:
            response = await asyncio.to_thread(self._llm.invoke, messages)
            text = self._extract_text(response)
            data = self._parse_plan(text)
        except Exception as exc:  # noqa: BLE001
            logging.warning("Actionability check failed for %s: %s", agent, exc)
            return {"status": "ok"}

        status = str(data.get("status") or "").strip().lower()
        message = str(data.get("message") or "").strip()
        if agent == "browser" and status == "needs_info" and not self._browser_action_is_high_risk(command):
            status = "ok"
            message = ""
        if status not in {"ok", "needs_info"}:
            status = "ok"
        return {"status": status, "message": message}

    async def _maybe_request_clarification(self, task: TaskSpec) -> ExecutionResult | None:
        """Return a clarification result when the task is not actionable."""

        assessment = await self._assess_actionability(task)
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

    async def _execute_task(self, task: TaskSpec) -> ExecutionResult:
        agent = task["agent"]
        command = task["command"]

        try:
            clarification = await self._maybe_request_clarification(task)
            if clarification is not None:
                return clarification

            availability = await get_agent_availability()
            if not availability.get(agent, True):
                agent_label = self._AGENT_DISPLAY_NAMES.get(agent, agent)
                return {
                    "agent": agent,
                    "command": command,
                    "status": "success",
                    "response": f"{agent_label} に接続できないため、このタスクはスキップしました。",
                    "error": None,
                    "finalized": True,
                }

            if agent == "lifestyle":
                try:
                    data = await _call_lifestyle("/agent_rag_answer", method="POST", payload={"question": command})
                except LifestyleAPIError as exc:
                    return self._execution_error_result(agent, command, exc)
                answer = str(data.get("answer") or "").strip() or "Life-Styleエージェントから回答が得られませんでした。"
                return {
                    "agent": agent,
                    "command": command,
                    "status": "success",
                    "response": answer,
                    "error": None,
                }

            if agent == "browser":
                try:
                    data = await _call_browser_agent_chat(command)
                except BrowserAgentError as exc:
                    return self._browser_error_result(command, exc)
                # agent-relay returns synchronous results, no need for additional polling
                result = self._browser_result_from_payload(command, data, fallback_summary="")
                if self._browser_result_is_failure(result):
                    fallback_command = self._browser_fallback_command(command)
                    if fallback_command:
                        try:
                            fallback_data = await _call_browser_agent_chat(fallback_command)
                        except BrowserAgentError as exc:
                            return self._browser_error_result(fallback_command, exc)
                        result = self._browser_result_from_payload(fallback_command, fallback_data, fallback_summary="")
                return result

            if agent == "iot":
                try:
                    data = await _call_iot_agent_command(command)
                except IotAgentError as exc:
                    return self._execution_error_result(agent, command, exc)
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

            if agent == "scheduler":
                try:
                    data = await _call_scheduler_agent_chat(command)
                except SchedulerAgentError as exc:
                    return self._execution_error_result(agent, command, exc)
                reply = str(data.get("reply") or data.get("message") or "").strip()
                if not reply:
                    reply = "Scheduler エージェントからの応答が空でした。"
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
        except Exception as exc:  # noqa: BLE001
            logging.exception("Unexpected error while executing %s task: %s", agent, exc)
            return self._execution_error_result(agent, command, exc)

    async def _execute_browser_task_with_progress(self, task: TaskSpec) -> AsyncIterator[Dict[str, Any]]:
        command = task["command"]
        try:
            clarification = await self._maybe_request_clarification(task)
            if clarification is not None:
                yield {"type": "result", "result": clarification}
                return

            availability = await get_agent_availability()
            if not availability.get("browser", True):
                agent_label = self._AGENT_DISPLAY_NAMES.get("browser", "browser")
                yield {
                    "type": "result",
                    "result": {
                        "agent": "browser",
                        "command": command,
                        "status": "success",
                        "response": f"{agent_label} に接続できないため、このタスクはスキップしました。",
                        "error": None,
                        "finalized": True,
                    },
                }
                return

            if _USE_BROWSER_AGENT_MCP:
                mcp_result, mcp_errors = await _call_browser_agent_chat_via_mcp(command)
                if mcp_result is not None:
                    fallback_summary = await self._augment_browser_payload_with_history(mcp_result, timeout=60.0)
                    yield {
                        "type": "result",
                        "result": self._browser_result_from_payload(command, mcp_result, fallback_summary=fallback_summary),
                    }
                    return
                if mcp_errors:
                    logging.info("Browser Agent MCP execution failed, falling back to HTTP: %s", "; ".join(mcp_errors))

            try:
                async for event in self._iter_browser_agent_progress(command):
                    if event.get("type") != "result":
                        yield event
                        continue
                    result = event.get("result")
                    if not isinstance(result, dict):
                        yield event
                        continue
                    if self._browser_result_is_failure(result):
                        fallback_command = self._browser_fallback_command(command)
                        if fallback_command:
                            try:
                                fallback_data = await _call_browser_agent_chat(fallback_command)
                            except BrowserAgentError as fallback_exc:
                                yield {"type": "result", "result": self._browser_error_result(fallback_command, fallback_exc)}
                                return
                            fallback_result = self._browser_result_from_payload(
                                fallback_command,
                                fallback_data,
                                fallback_summary="",
                            )
                            yield {"type": "result", "result": fallback_result}
                            return
                    yield event
            except BrowserAgentError as exc:
                logging.warning("Streaming browser execution failed, falling back to summary only: %s", exc)
                try:
                    data = await _call_browser_agent_chat(command)
                except BrowserAgentError as fallback_exc:
                    yield {"type": "result", "result": self._browser_error_result(command, fallback_exc)}
                else:
                    result = self._browser_result_from_payload(command, data, fallback_summary="")
                    if self._browser_result_is_failure(result):
                        fallback_command = self._browser_fallback_command(command)
                        if fallback_command:
                            try:
                                fallback_data = await _call_browser_agent_chat(fallback_command)
                            except BrowserAgentError as fallback_exc:
                                yield {"type": "result", "result": self._browser_error_result(fallback_command, fallback_exc)}
                                return
                            result = self._browser_result_from_payload(
                                fallback_command,
                                fallback_data,
                                fallback_summary="",
                            )
                    yield {"type": "result", "result": result}
                return
        except Exception as exc:  # noqa: BLE001
            logging.exception("Unexpected error while executing browser task: %s", exc)
            yield {"type": "result", "result": self._browser_error_result(command, exc)}
            return

    async def _iter_browser_agent_progress(self, command: str) -> AsyncIterator[Dict[str, Any]]:
        last_error: BrowserAgentError | None = None
        for base in _iter_browser_agent_bases():
            try:
                async for event in self._iter_browser_agent_progress_for_base(base, command):
                    yield event
                return
            except BrowserAgentError as exc:
                logging.warning("Browser agent streaming attempt failed for %s: %s", base, exc)
                last_error = exc
                continue

        if last_error is not None:
            raise last_error
        raise BrowserAgentError("ブラウザエージェントへの接続に失敗しました。")

    async def _iter_browser_agent_progress_for_base(
        self,
        base: str,
        command: str,
    ) -> AsyncIterator[Dict[str, Any]]:
        event_queue: "asyncio.Queue[Dict[str, Any]]" = asyncio.Queue()
        stop_event = asyncio.Event()
        stream_ready = asyncio.Event()
        stream_status: Dict[str, Any] = {"ok": False, "error": None}

        stream_url = _build_browser_agent_url(base, "/api/stream")
        chat_url = _build_browser_agent_url(base, "/api/chat")
        baseline_last_id, baseline_summary = await self._fetch_browser_history_snapshot(base)

        async def _stream_worker() -> None:
            try:
                async with httpx.AsyncClient(timeout=_browser_agent_timeout(BROWSER_AGENT_STREAM_TIMEOUT)) as client:
                    async with client.stream("GET", stream_url) as response:
                        if not response.is_success:
                            stream_status["error"] = BrowserAgentError(
                                _extract_browser_error_message(
                                    response,
                                    "ブラウザエージェントのイベントストリームへの接続に失敗しました。",
                                ),
                                status_code=response.status_code,
                            )
                            stream_ready.set()
                            await event_queue.put({"kind": "stream_error", "error": stream_status["error"]})
                            return

                        stream_status["ok"] = True
                        stream_ready.set()

                        event_type = "message"
                        data_lines: list[str] = []
                        async for raw_line in response.aiter_lines():
                            if stop_event.is_set():
                                break
                            if raw_line == "":
                                if data_lines:
                                    data_text = "\n".join(data_lines)
                                    await event_queue.put(
                                        {"kind": "stream_data", "event": event_type, "data": data_text}
                                    )
                                    data_lines = []
                                    event_type = "message"
                                continue
                            if raw_line.startswith(":"):
                                continue
                            if raw_line.startswith("event:"):
                                event_type = raw_line[6:].strip() or "message"
                            elif raw_line.startswith("data:"):
                                data_lines.append(raw_line[5:].lstrip())
            except httpx.RequestError as exc:
                stream_status["error"] = BrowserAgentError(
                    f"ブラウザエージェントのイベントストリームに接続できませんでした: {exc}",
                )
                stream_ready.set()
                await event_queue.put({"kind": "stream_error", "error": stream_status["error"]})
            except asyncio.CancelledError:
                pass
            except Exception as exc:  # noqa: BLE001
                logging.exception("Unexpected error while consuming browser agent stream: %s", exc)
                await event_queue.put(
                    {
                        "kind": "stream_error",
                        "error": BrowserAgentError(
                            "ブラウザエージェントのイベントストリームで予期しないエラーが発生しました。",
                        ),
                    }
                )
            finally:
                await event_queue.put({"kind": "stream_closed"})

        async def _chat_worker() -> None:
            try:
                async with httpx.AsyncClient(timeout=_browser_agent_timeout(BROWSER_AGENT_CHAT_TIMEOUT)) as client:
                    response = await client.post(
                        chat_url,
                        json={"prompt": command, "new_task": True, "skip_conversation_review": True},
                    )
            except httpx.RequestError as exc:
                await event_queue.put(
                    {
                        "kind": "chat_error",
                        "error": BrowserAgentError(
                            f"ブラウザエージェントの呼び出しに失敗しました: {exc}",
                        ),
                    }
                )
                await event_queue.put({"kind": "chat_complete"})
                return

            try:
                data = response.json()
            except ValueError:
                data = None

            if not response.is_success:
                message = _extract_browser_error_message(
                    response,
                    "ブラウザエージェントの呼び出しに失敗しました。",
                )
                await event_queue.put(
                    {
                        "kind": "chat_error",
                        "error": BrowserAgentError(message, status_code=response.status_code),
                    }
                )
                await event_queue.put({"kind": "chat_complete"})
                return

            if not isinstance(data, dict):
                await event_queue.put(
                    {
                        "kind": "chat_error",
                        "error": BrowserAgentError(
                            "ブラウザエージェントから不正なレスポンス形式が返されました。",
                            status_code=response.status_code,
                        ),
                    }
                )
                await event_queue.put({"kind": "chat_complete"})
                return

            await event_queue.put({"kind": "chat_result", "data": data})
            await event_queue.put({"kind": "chat_complete"})

        stream_task = asyncio.create_task(_stream_worker())
        chat_task = asyncio.create_task(_chat_worker())

        stream_init_timeout = min(BROWSER_AGENT_CONNECT_TIMEOUT or 10.0, 6.0)
        stream_pre_failed = False
        try:
            await asyncio.wait_for(stream_ready.wait(), timeout=stream_init_timeout)
        except asyncio.TimeoutError:
            stream_status["error"] = BrowserAgentError("ブラウザエージェントのイベントストリーム初期化がタイムアウトしました。")
            stream_pre_failed = True
        if stream_status.get("error"):
            stream_pre_failed = True
        if stream_pre_failed:
            stop_event.set()
            if not stream_task.done():
                stream_task.cancel()

        progress_messages: Dict[Any, str] = {}
        anon_counter = 0
        latest_summary = ""
        chat_result: Dict[str, Any] | None = None
        chat_error: BrowserAgentError | None = None
        stream_finished = stream_pre_failed
        stream_failed = stream_pre_failed
        chat_finished = False
        chat_finished_at: float | None = None
        chat_indicates_running = False
        history_poll_summary = ""
        stream_has_new_message = False
        chat_has_new_messages = False

        def _stop_stream() -> None:
            stop_event.set()
            if not stream_task.done():
                stream_task.cancel()

        try:
            while True:
                try:
                    item = await asyncio.wait_for(event_queue.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    if chat_finished and (stream_finished or stream_failed):
                        break
                    if chat_finished and not stream_finished and not stream_failed:
                        if chat_indicates_running:
                            continue
                        if chat_finished_at is None:
                            chat_finished_at = time.monotonic()
                        elif time.monotonic() - chat_finished_at > 5.0:
                            logging.warning(
                                "Browser agent stream did not terminate after chat completion; forcing shutdown."
                            )
                            stream_failed = True
                            stream_finished = True
                            _stop_stream()
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
                            if msg_id_raw <= baseline_last_id:
                                continue
                            message_key = msg_id_raw
                            stream_has_new_message = True
                        else:
                            if not stream_has_new_message:
                                continue
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
                            cleaned_summary = summary_text.strip()
                            if stream_has_new_message or cleaned_summary != baseline_summary:
                                latest_summary = cleaned_summary
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
                    if isinstance(chat_result, dict):
                        chat_indicates_running = bool(chat_result.get("agent_running"))
                    else:
                        chat_indicates_running = False
                    if not chat_indicates_running:
                        chat_finished_at = chat_finished_at or time.monotonic()
                    chat_messages = chat_result.get("messages") if isinstance(chat_result, dict) else None
                    chat_last_id = self._latest_message_id(chat_messages)
                    if chat_last_id > baseline_last_id:
                        chat_has_new_messages = True
                elif kind == "chat_error":
                    error = item.get("error")
                    chat_error = error if isinstance(error, BrowserAgentError) else BrowserAgentError(str(error))
                    chat_finished = True
                    chat_finished_at = chat_finished_at or time.monotonic()
                    _stop_stream()
                elif kind == "chat_complete":
                    chat_finished = True
                    chat_finished_at = chat_finished_at or time.monotonic()

                if chat_error is not None:
                    break
                if chat_finished and stream_finished:
                    break
        finally:
            if chat_result is not None:
                need_poll = (
                    stream_failed
                    or not self._has_browser_final_marker(latest_summary)
                    or (baseline_last_id >= 0 and not chat_has_new_messages)
                )
                if need_poll:
                    base_last_id = baseline_last_id
                    history_poll_summary = await self._poll_browser_history_summary(
                        base,
                        since_id=base_last_id,
                        timeout=900.0,
                        interval=2.0,
                    )
            _stop_stream()
            await asyncio.gather(stream_task, chat_task, return_exceptions=True)

        if chat_error is not None:
            raise chat_error

        if chat_result is None or not isinstance(chat_result, dict):
            raise BrowserAgentError("ブラウザエージェントからの応答を取得できませんでした。")

        http_run_summary = str(chat_result.get("run_summary") or "").strip()
        if baseline_summary and not chat_has_new_messages and http_run_summary == baseline_summary:
            http_run_summary = ""
        fallback_summary = history_poll_summary or latest_summary
        if baseline_summary and not stream_has_new_message and fallback_summary == baseline_summary:
            fallback_summary = ""
        if not http_run_summary and not latest_summary and not history_poll_summary:
            logging.warning(
                "Browser agent returned empty run_summary. HTTP: %r, Stream: %r, Poll: %r, Messages count: %d",
                http_run_summary,
                latest_summary,
                history_poll_summary,
                len(chat_result.get("messages") or []),
            )
        if baseline_last_id >= 0 and not chat_has_new_messages and not fallback_summary and not http_run_summary:
            raise BrowserAgentError("ブラウザエージェントが前回の結果を返した可能性があります。再試行します。")

        yield {
            "type": "result",
            "result": self._browser_result_from_payload(command, chat_result, fallback_summary=fallback_summary),
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

        # Try to extract content between "最終報告:" and "最終URL:"
        start_marker = "最終報告:"
        end_marker = "最終URL:"

        start_index = cleaned.find(start_marker)
        if start_index != -1:
            end_index = cleaned.find(end_marker, start_index)
            if end_index != -1:
                report_part = cleaned[start_index:end_index]
            else:
                report_part = cleaned[start_index:]

            # Remove the "最終報告:" prefix itself
            report_content = report_part[len(start_marker) :].strip()
            if report_content:
                return report_content

        lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
        if not lines:
            return cleaned

        for line in lines:
            if "ステップでエージェントが実行されました" in line:
                continue
            if line.startswith("※"):
                continue
            return line

        return lines[0]

    @classmethod
    def _prepend_orchestrator_label(cls, text: str) -> str:
        """Ensure orchestrator-facing messages carry a consistent prefix."""

        cleaned = text.strip() if isinstance(text, str) else ""
        if not cleaned:
            return ""
        if cleaned.lower().startswith("[orchestrator]"):
            return cleaned
        stripped = re.sub(r"^\[[^\]]+\]\s*", "", cleaned).strip()
        body = stripped or cleaned
        return f"{cls._ORCHESTRATOR_LABEL} {body}"

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

    @staticmethod
    def _normalise_history_entries(history: Iterable[Dict[str, Any]]) -> List[Dict[str, str]]:
        """Filter history entries down to role/content pairs."""

        entries: List[Dict[str, str]] = []
        for entry in history or []:
            if not isinstance(entry, dict):
                continue
            role = entry.get("role")
            content = entry.get("content")
            if isinstance(role, str) and isinstance(content, str):
                entries.append({"role": role, "content": content})
        return entries

    def _history_from_last_user_turn(self, history: List[Dict[str, Any]]) -> List[Dict[str, str]]:
        """Return history entries starting from the latest user turn."""

        entries = self._normalise_history_entries(history)
        if not entries:
            return []

        last_user_idx = None
        for idx in range(len(entries) - 1, -1, -1):
            if entries[idx].get("role") == "user":
                last_user_idx = idx
                break

        if last_user_idx is None:
            return entries
        return entries[last_user_idx:]

    @staticmethod
    def _append_session_history_entry(state: OrchestratorState, role: str, content: str) -> None:
        """Append a message to the in-memory session history."""

        if not content:
            return
        history = state.get("session_history") or []
        entry = {"role": role, "content": content}
        history.append(entry)
        state["session_history"] = history

    def _load_recent_chat_history(self, limit: int = 30) -> List[Dict[str, Any]]:
        """Return the most recent chat history entries (best-effort)."""

        history = _read_chat_history(limit=limit)
        if not isinstance(history, list):
            return []
        return history

    def _initial_session_history(self, user_input: str, log_history: bool) -> List[Dict[str, Any]]:
        """Seed per-run history so the planner sees the full conversation for this run."""

        if log_history:
            _append_to_chat_history("user", user_input, broadcast=True)
            loaded_history = self._load_recent_chat_history(limit=20)
            session_history = self._history_from_last_user_turn(loaded_history)
            if not session_history or session_history[-1].get("content") != user_input:
                session_history.append({"role": "user", "content": user_input})
            return session_history

        return [{"role": "user", "content": user_input}]

    def _ensure_previous_result_logged(self, expected_text: str | None) -> List[Dict[str, Any]]:
        """Make sure the previous task result exists in chat history before continuing."""

        history = self._load_recent_chat_history()
        if not expected_text:
            return history

        found = any(
            isinstance(entry, dict)
            and str(entry.get("content") or "").strip() == expected_text
            for entry in history
        )
        if found:
            return history

        _append_to_chat_history("assistant", expected_text, broadcast=True)
        return self._load_recent_chat_history()

    def _log_execution_result_to_history(self, result: ExecutionResult) -> str:
        """Append a formatted execution result to chat history."""

        text = self._execution_result_text(result)

        # Best-effort de-duplication: avoid writing the same execution line twice,
        # but guarantee the final Browser Agent summary is persisted to chat_history.json
        # so the General view sidebar always shows it.
        try:
            history = self._load_recent_chat_history(limit=40)
            already_logged = any(
                isinstance(entry, dict)
                and str(entry.get("content") or "").strip() == text
                for entry in history
            )
        except Exception:  # noqa: BLE001 - fallback to always write
            already_logged = False

        if not already_logged:
            _append_to_chat_history("assistant", text, broadcast=True)

        return text

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

    def _plan_history_entry(self, plan_summary: str | None, tasks: List[TaskSpec]) -> str:
        """Compose a chat_history entry describing the orchestrator's plan."""

        lines: list[str] = []
        summary = (plan_summary or "").strip()
        if summary:
            if tasks:
                lines.append(f"計画: {summary}")
            else:
                # When no tasks are scheduled, treat the summary as the final answer without prepending 「計画」
                lines.append(summary)
        if tasks:
            lines.append("タスク一覧:")
            for idx, task in enumerate(tasks, start=1):
                agent = task.get("agent") or "agent"
                agent_label = self._AGENT_DISPLAY_NAMES.get(agent, agent)
                command = (task.get("command") or "").strip()
                command_text = command or "内容が空のタスク"
                lines.append(f"{idx}. [{agent_label}] {command_text}")

        if not lines:
            return ""

        return self._prepend_orchestrator_label("\n".join(lines))

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

    def _run_memory_consolidation(
        self,
        llm_client: Any,
        short_history: List[Dict[str, str]],
        long_history: List[Dict[str, str]],
    ) -> None:
        """Execute memory consolidation for short- and long-term stores."""

        try:
            short_snapshot = None
            if short_history:
                short_snapshot = MemoryManager("short_term_memory.json").consolidate_memory(
                    short_history,
                    memory_kind="short",
                    llm=llm_client,
                )

            # Consolidate to long-term only when short-term has accumulated enough context
            if short_snapshot and len(short_history) >= 6:
                MemoryManager("long_term_memory.json").consolidate_memory(
                    long_history or short_history,
                    memory_kind="long",
                    llm=llm_client,
                    short_snapshot=short_snapshot,
                )
                MemoryManager("short_term_memory.json").reset_short_memory(preserve_active_task=True)
        except Exception as exc:  # noqa: BLE001
            logging.warning("Background memory consolidation failed: %s", exc)

    def _trigger_memory_consolidation(self, session_history: List[Dict[str, Any]]) -> None:
        """Kick off background memory consolidation without delaying the main response."""

        memory_settings = load_memory_settings()
        if not memory_settings.get("enabled", True):
            return

        normalized = self._normalise_history_entries(session_history)
        if not normalized:
            return

        # Keep windows small to avoid token bloat and mirror existing cadence
        short_history = normalized[-10:]
        long_history = normalized[-30:]

        llm_client = get_memory_llm()
        if llm_client is None:
            logging.debug("Skipping memory consolidation: LLM client not available.")
            return

        threading.Thread(
            target=self._run_memory_consolidation,
            args=(llm_client, short_history, long_history),
            daemon=True,
        ).start()

    async def run_stream(self, user_input: str, *, log_history: bool = False) -> AsyncIterator[Dict[str, Any]]:
        session_history = self._initial_session_history(user_input, log_history)
        agent_connections = load_agent_connections()
        state: OrchestratorState = {
            "user_input": user_input,
            "plan_summary": None,
            "raw_plan": None,
            "tasks": [],
            "executions": [],
            "current_index": 0,
            "agent_connections": agent_connections,
            "session_history": session_history,
        }

        plan_state = await self._plan_node(state)
        state.update(plan_state)
        state["tasks"] = list(state.get("tasks") or [])
        state["executions"] = list(state.get("executions") or [])
        state["current_index"] = 0

        logged_history_texts: List[str] = []
        plan_history_entry = self._plan_history_entry(state.get("plan_summary"), state["tasks"])
        if plan_history_entry:
            self._append_session_history_entry(state, "assistant", plan_history_entry)
            if log_history:
                _append_to_chat_history("assistant", plan_history_entry, broadcast=True)
                logged_history_texts.append(plan_history_entry)

        yield self._event_payload("plan", state)

        executions: List[ExecutionResult] = []

        while True:
            tasks = list(state.get("tasks") or [])
            tasks = self._apply_execution_results_to_tasks(tasks, executions)
            state["tasks"] = tasks
            current_index = state.get("current_index", 0)
            if current_index >= len(tasks):
                break
            task_spec = tasks[current_index]
            task_run_index = len(executions)
            state["current_index"] = task_run_index

            history_context: List[Dict[str, Any]] = []
            if log_history:
                last_recorded = logged_history_texts[-1] if logged_history_texts else None
                history_context = self._ensure_previous_result_logged(last_recorded)

            yield self._event_payload(
                "before_execution",
                state,
                task_index=task_run_index,
                task=task_spec,
                history_context=history_context,
            )

            result: ExecutionResult | None = None

            if task_spec["agent"] == "browser":
                yield self._event_payload(
                    "browser_init",
                    state,
                    task_index=task_run_index,
                    task=task_spec,
                    history_context=history_context,
                )

                async for event in self._execute_browser_task_with_progress(task_spec):
                    etype = event.get("type")
                    if etype == "progress":
                        yield self._event_payload(
                            "execution_progress",
                            state,
                            task_index=task_run_index,
                            task=task_spec,
                            progress=event,
                            history_context=history_context,
                        )
                    elif etype == "result":
                        maybe_result = event.get("result")
                        if isinstance(maybe_result, dict):
                            result = cast(ExecutionResult, maybe_result)

                if result is None:
                    result = self._browser_error_result(
                        task_spec["command"],
                        BrowserAgentError("ブラウザエージェントからの結果を取得できませんでした。"),
                    )
            else:
                result = await self._execute_task(task_spec)

            executions.append(result)
            state["executions"] = executions
            state["current_index"] = len(executions)

            execution_text = self._execution_result_text(result)
            if log_history:
                execution_text = self._log_execution_result_to_history(result)
                logged_history_texts.append(execution_text)
            self._append_session_history_entry(state, "assistant", execution_text)

            yield self._event_payload(
                "after_execution",
                state,
                task_index=task_run_index,
                task=task_spec,
                result=result,
                history_context=history_context,
            )

            if result.get("status") == "needs_info":
                clarification = (result.get("response") or result.get("error") or "").strip()
                request_text = (
                    f"追加の情報が必要です。以下の質問に回答してください: {clarification}"
                    if clarification
                    else "追加の情報が必要です。上記の質問に回答してください。"
                )
                state["plan_summary"] = request_text
                state["tasks"] = []
                state["current_index"] = len(executions)
                orchestrator_text = self._prepend_orchestrator_label(request_text)
                self._append_session_history_entry(state, "assistant", orchestrator_text)
                if log_history:
                    _append_to_chat_history("assistant", orchestrator_text, broadcast=True)
                    logged_history_texts.append(orchestrator_text)
                yield self._event_payload("plan", state, incremental=True)
                break

            # Re-plan after every execution so the next agent receives the latest context.
            replan_input: OrchestratorState = {
                "user_input": user_input,
                "plan_summary": state.get("plan_summary"),
                "raw_plan": state.get("raw_plan"),
                "tasks": tasks,
                "executions": executions,
                "current_index": len(executions),
                "retry_counts": state.get("retry_counts") or {},
                "agent_connections": agent_connections,
                "session_history": state.get("session_history") or [],
            }
            replan_state = await self._plan_node(replan_input, incremental=True)
            state.update(replan_state)
            state["executions"] = executions
            state["current_index"] = 0

            if executions:
                completed = {
                    (res.get("agent"), (res.get("command") or "").strip())
                    for res in executions
                    if res.get("status") == "success"
                }
                state["tasks"] = [
                    task
                    for task in state.get("tasks") or []
                    if (task.get("agent"), (task.get("command") or "").strip()) not in completed
                ]
            state["tasks"] = self._apply_execution_results_to_tasks(state.get("tasks") or [], executions)
            state["current_index"] = 0

            new_plan_history = self._plan_history_entry(state.get("plan_summary"), state.get("tasks") or [])
            if new_plan_history:
                session_history = state.get("session_history") or []
                last_session_text = (
                    session_history[-1].get("content") if session_history and isinstance(session_history[-1], dict) else None
                )
                already_logged = (
                    bool(logged_history_texts and new_plan_history == logged_history_texts[-1])
                    or new_plan_history == last_session_text
                )
                if not already_logged:
                    self._append_session_history_entry(state, "assistant", new_plan_history)
                if log_history and not already_logged:
                    _append_to_chat_history("assistant", new_plan_history, broadcast=True)
                    logged_history_texts.append(new_plan_history)

            yield self._event_payload("plan", state, incremental=True)

        plan_summary = state.get("plan_summary") or ""
        plan_summary = self._apply_execution_placeholders(plan_summary, executions)
        state["plan_summary"] = plan_summary
        assistant_messages = self._format_assistant_messages(plan_summary, executions)
        if log_history:
            updated_messages = []
            for message in assistant_messages:
                text = str(message.get("text") or "")
                # Only prepend [Orchestrator] label to Orchestrator's own messages (plan/status).
                # Execution results already have their specific agent label (e.g. [Browser Agent]).
                if message.get("type") in ("plan", "status"):
                    text = self._prepend_orchestrator_label(text)
                updated_messages.append({**message, "text": text})
            assistant_messages = updated_messages

        if log_history:
            already_logged = bool(logged_history_texts)
            if not already_logged:
                for msg in assistant_messages:
                    text = msg.get("text")
                    if not isinstance(text, str) or not text.strip():
                        continue
                    _append_to_chat_history("assistant", text, broadcast=True)

        # Kick off memory consolidation without blocking the SSE stream.
        session_history_for_memory = state.get("session_history") or []
        if session_history_for_memory:
            self._trigger_memory_consolidation(session_history_for_memory)

        yield self._event_payload(
            "complete",
            state,
            assistant_messages=assistant_messages,
        )

    async def run(self, user_input: str, *, log_history: bool = False) -> Dict[str, Any]:
        final_event: Dict[str, Any] | None = None
        async for event in self.run_stream(user_input, log_history=log_history):
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
