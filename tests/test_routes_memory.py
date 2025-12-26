from typing import Any, List, Tuple

from multi_agent_app import routes as routes_module


def test_api_memory_post_synchronises_all_fields(client, monkeypatch):
    recorded_calls: List[Tuple[str, Any]] = []

    class DummyManager:
        def __init__(self, file_path: str) -> None:
            self.file_path = file_path

        def replace_with_user_payload(self, payload: Any) -> None:
            recorded_calls.append((self.file_path, payload))

    saved_settings: List[dict[str, Any]] = []

    def fake_save_memory_settings(payload: dict[str, Any]) -> dict[str, Any]:
        saved_settings.append(payload)
        return payload

    monkeypatch.setattr(routes_module, "MemoryManager", DummyManager)
    monkeypatch.setattr(routes_module, "save_memory_settings", fake_save_memory_settings)

    response = client.post(
        "/api/memory",
        json={
            "long_term_memory": {"profile": "Profile memo"},
            "short_term_memory": "Short memo",
            "enabled": False,
            "history_sync_enabled": True,
        },
    )

    assert response.status_code == 200
    assert response.get_json()["message"] == "Memory saved successfully."
    assert recorded_calls == [
        ("long_term_memory.json", {"profile": "Profile memo"}),
        ("short_term_memory.json", "Short memo"),
    ]
    assert len(saved_settings) == 1
    assert saved_settings[0]["enabled"] is False
    assert saved_settings[0]["history_sync_enabled"] is True
