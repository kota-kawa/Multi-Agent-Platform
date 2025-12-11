import pytest

from multi_agent_app.orchestrator import MultiAgentOrchestrator
from multi_agent_app.errors import OrchestratorError


def _make_orchestrator_stub() -> MultiAgentOrchestrator:
    """Create an orchestrator instance without running __init__ (no LLM needed)."""
    return object.__new__(MultiAgentOrchestrator)


def test_parse_plan_json_code_block():
    orch = _make_orchestrator_stub()
    raw = """
    some text
    ```json
    {"plan_summary": "ok", "tasks": []}
    ```
    """
    parsed = orch._parse_plan(raw)
    assert parsed["plan_summary"] == "ok"
    assert parsed["tasks"] == []


def test_parse_plan_single_quotes_literal():
    orch = _make_orchestrator_stub()
    raw = "{'plan_summary': 'hello', 'tasks': []}"
    parsed = orch._parse_plan(raw)
    assert parsed == {"plan_summary": "hello", "tasks": []}


def test_parse_plan_brace_fallback():
    orch = _make_orchestrator_stub()
    raw = 'please do {"plan_summary":"x","tasks":[]} thanks'
    parsed = orch._parse_plan(raw)
    assert parsed["plan_summary"] == "x"


def test_parse_plan_plain_text_direct_answer():
    orch = _make_orchestrator_stub()
    raw = "そのまま返すテキスト回答"
    parsed = orch._parse_plan(raw)
    assert parsed == {"plan_summary": raw, "tasks": []}


def test_parse_plan_missing_outer_braces():
    orch = _make_orchestrator_stub()
    raw = '"plan_summary":"foo","tasks":[]'
    parsed = orch._parse_plan(raw)
    assert parsed["plan_summary"] == "foo"


def test_parse_plan_raises_on_invalid():
    orch = _make_orchestrator_stub()
    parsed = orch._parse_plan("no json here")
    assert parsed == {"plan_summary": "no json here", "tasks": []}


def test_extract_text_prefers_content_key_for_dict():
    orch = _make_orchestrator_stub()
    content = {"content": '{"plan_summary": "hi"}'}
    assert orch._extract_text(content) == '{"plan_summary": "hi"}'


def test_extract_text_concatenates_text_items():
    orch = _make_orchestrator_stub()
    content = [{"type": "text", "text": "foo"}, {"type": "text", "text": "bar"}]
    assert orch._extract_text(content) == "foobar"
