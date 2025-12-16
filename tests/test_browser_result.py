import os
import sys

# Ensure multi_agent_app package is importable when running tests directly.
sys.path.append(os.getcwd())

from multi_agent_app.config import BROWSER_AGENT_FINAL_MARKER
from multi_agent_app.orchestrator import MultiAgentOrchestrator


def test_browser_result_prefers_stream_final_summary_over_ack() -> None:
    """Streamed run_summary with a final marker should override chat ACK text."""

    orchestrator = MultiAgentOrchestrator()
    command = "ブラウザで株価を取得して"

    chat_ack = "フォローアップの指示を受け付けました。"
    stream_summary = (
        "✅ 1ステップでエージェントが実行されました（結果: 成功）。\n"
        "最終報告: 株価を取得しました。\n"
        "最終URL: https://example.com/\n"
        f"{BROWSER_AGENT_FINAL_MARKER}"
    )

    result = orchestrator._browser_result_from_payload(
        command,
        {"run_summary": chat_ack, "messages": []},
        fallback_summary=stream_summary,
    )

    assert result["finalized"] is True
    assert "株価を取得しました。" in (result["response"] or "")


def test_browser_result_handles_english_success_message() -> None:
    """Ensure English completion summaries still surface to the user."""

    orchestrator = MultiAgentOrchestrator()
    command = "Open example.com"

    summary = "✅ Task completed successfully\n[browser-agent-final]"
    result = orchestrator._browser_result_from_payload(
        command,
        {"run_summary": summary, "messages": []},
    )

    assert result["status"] == "success"
    assert result["finalized"] is True
    assert "Task completed successfully" in (result["response"] or "")
