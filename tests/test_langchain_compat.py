import json

from multi_agent_app import orchestrator as orchestrator_module
from multi_agent_app import memory_manager
from multi_agent_app import iot as iot_module


class _TextObj:
    def __init__(self, text):
        self.text = text


class _ContentObj:
    def __init__(self, content):
        self.content = content


class _BlockObj:
    def __init__(self, text):
        self.text = text


class _ContentBlocksObj:
    def __init__(self, blocks):
        self.content_blocks = blocks


class _ToolCallObj:
    def __init__(self, name=None, args=None, arguments=None):
        self.name = name
        self.args = args
        self.arguments = arguments


def _orchestrator_instance():
    # Bypass __init__ to avoid external LLM config.
    return orchestrator_module.MultiAgentOrchestrator.__new__(
        orchestrator_module.MultiAgentOrchestrator
    )


def test_orchestrator_extract_text_handles_string():
    inst = _orchestrator_instance()
    assert inst._extract_text("hello") == "hello"


def test_orchestrator_extract_text_handles_text_attr():
    inst = _orchestrator_instance()
    assert inst._extract_text(_TextObj("from-text")) == "from-text"


def test_orchestrator_extract_text_handles_content_blocks():
    inst = _orchestrator_instance()
    msg = _ContentBlocksObj([
        {"type": "output_text", "output_text": "one"},
        {"type": "text", "text": "two"},
    ])
    assert inst._extract_text(msg) == "onetwo"


def test_orchestrator_extract_text_handles_list_dicts():
    inst = _orchestrator_instance()
    content = [
        {"type": "text", "text": "alpha"},
        {"type": "output_text", "output_text": "beta"},
    ]
    assert inst._extract_text(content) == "alphabeta"


def test_orchestrator_extract_text_handles_dict_content():
    inst = _orchestrator_instance()
    assert inst._extract_text({"content": "payload"}) == "payload"


def test_memory_extract_text_supports_blocks_and_text():
    assert memory_manager._extract_text(_TextObj("hi")) == "hi"
    assert memory_manager._extract_text(_ContentObj("payload")) == "payload"
    assert memory_manager._extract_text([{"type": "text", "text": "a"}]) == "a"
    blocks = _ContentBlocksObj([
        {"type": "output_text", "output_text": "x"},
        _BlockObj("y"),
    ])
    assert memory_manager._extract_text(blocks) == "xy"


def test_iot_normalise_tool_call_dict_and_object():
    name, args = iot_module._normalise_tool_call(
        {"name": "do", "args": '{"foo": 1}'}
    )
    assert name == "do"
    assert args == {"foo": 1}

    name, args = iot_module._normalise_tool_call(_ToolCallObj("run", {"bar": 2}))
    assert name == "run"
    assert args == {"bar": 2}

    name, args = iot_module._normalise_tool_call(
        _ToolCallObj("fallback", None, arguments=json.dumps({"ok": True}))
    )
    assert name == "fallback"
    assert args == {"ok": True}


def test_iot_normalise_tool_call_invalid_args():
    name, args = iot_module._normalise_tool_call({"name": "noop", "args": "not-json"})
    assert name == "noop"
    assert args == {}


def test_iot_extract_llm_text_variants():
    assert iot_module._extract_llm_text(_TextObj("text")) == "text"
    assert iot_module._extract_llm_text(_ContentObj("content")) == "content"
    blocks = _ContentBlocksObj([
        {"type": "output_text", "output_text": "a"},
        {"type": "text", "text": "b"},
    ])
    assert iot_module._extract_llm_text(blocks) == "ab"
    content_list = [{"type": "text", "text": "c"}, "d"]
    assert iot_module._extract_llm_text(_ContentObj(content_list)) == "cd"
