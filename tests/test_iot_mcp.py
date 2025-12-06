from __future__ import annotations

import pytest

import multi_agent_app.iot as iot
from multi_agent_app.errors import IotAgentError


def test_call_iot_agent_command_uses_mcp(monkeypatch):
    calls: list[tuple[str, str]] = []

    monkeypatch.setattr(iot, "_iter_iot_agent_bases", lambda: ["http://iot-agent"])

    def fake_execute(command: str, base_url: str) -> dict:
        calls.append((command, base_url))
        return {"reply": "ok"}

    monkeypatch.setattr(iot, "_execute_iot_agent_via_mcp_sync", fake_execute)

    result = iot._call_iot_agent_command("turn on the living room lights")

    assert result == {"reply": "ok"}
    assert calls == [("turn on the living room lights", "http://iot-agent")]


def test_call_iot_agent_command_does_not_fallback_to_http(monkeypatch):
    monkeypatch.setattr(iot, "_iter_iot_agent_bases", lambda: ["http://iot-agent"])

    def fail_execute(command: str, base_url: str) -> dict:
        raise IotAgentError("MCP failed")

    monkeypatch.setattr(iot, "_execute_iot_agent_via_mcp_sync", fail_execute)

    called_http = False

    def fail_http(*_: object, **__: object) -> dict:
        nonlocal called_http
        called_http = True
        raise AssertionError("HTTP fallback should not be invoked")

    monkeypatch.setattr(iot, "_post_iot_agent", fail_http)

    with pytest.raises(IotAgentError) as excinfo:
        iot._call_iot_agent_command("turn on the living room lights")

    assert "HTTP API にはフォールバックしませんでした" in str(excinfo.value)
    assert called_http is False
