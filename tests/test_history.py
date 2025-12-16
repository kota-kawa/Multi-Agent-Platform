import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

# Ensure multi_agent_app can be imported
sys.path.append(os.getcwd())

from multi_agent_app.history import (  # noqa: E402
    _FALLBACK_CHAT_HISTORY_PATH,
    _PRIMARY_CHAT_HISTORY_PATH,
    _append_to_chat_history,
    _read_chat_history,
    _reset_chat_history,
)


class TestHistory(unittest.TestCase):
    def setUp(self):
        self.chat_history_paths = [_PRIMARY_CHAT_HISTORY_PATH, _FALLBACK_CHAT_HISTORY_PATH]
        self.memory_settings_path = Path("memory_settings.json")
        for path in self.chat_history_paths:
            try:
                path.unlink()
            except (FileNotFoundError, PermissionError):
                pass
        try:
            self.memory_settings_path.unlink()
        except FileNotFoundError:
            pass
        _reset_chat_history()

    def tearDown(self):
        for path in self.chat_history_paths:
            try:
                path.unlink()
            except (FileNotFoundError, PermissionError):
                pass
        try:
            self.memory_settings_path.unlink()
        except FileNotFoundError:
            pass
        _reset_chat_history()

    @patch('multi_agent_app.history._send_recent_history_to_agents')
    @patch('multi_agent_app.history._refresh_memory')
    def test_append_message_with_id(self, mock_refresh_memory, mock_send_recent_history_to_agents):
        _append_to_chat_history("user", "Hello, world!")
        history = _read_chat_history()
        self.assertEqual(len(history), 1)
        self.assertIn("id", history[0])
        self.assertEqual(history[0]["id"], 1)
        self.assertEqual(history[0]["role"], "user")
        self.assertEqual(history[0]["content"], "Hello, world!")

        _append_to_chat_history("assistant", "Hi there!")
        history = _read_chat_history()
        self.assertEqual(len(history), 2)
        self.assertEqual(history[1]["id"], 2)
        self.assertEqual(history[1]["role"], "assistant")
        self.assertEqual(history[1]["content"], "Hi there!")

        _append_to_chat_history("user", "How are you?")
        history = _read_chat_history()
        self.assertEqual(len(history), 3)
        self.assertEqual(history[2]["id"], 3)
        self.assertEqual(history[2]["role"], "user")
        self.assertEqual(history[2]["content"], "How are you?")

    @patch('multi_agent_app.history._send_recent_history_to_agents')
    @patch('multi_agent_app.history._refresh_memory')
    def test_append_message_to_empty_file(self, mock_refresh_memory, mock_send_recent_history_to_agents):
        _append_to_chat_history("user", "First message.")
        history = _read_chat_history()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["role"], "user")
        self.assertEqual(history[0]["content"], "First message.")

    @patch('multi_agent_app.history._send_recent_history_to_agents')
    @patch('multi_agent_app.history._refresh_memory')
    def test_json_decode_error_handling(self, mock_refresh_memory, mock_send_recent_history_to_agents):
        with open(_PRIMARY_CHAT_HISTORY_PATH, "w", encoding="utf-8") as f:
            f.write("これは不正なJSONです")

        _append_to_chat_history("user", "New message after bad JSON.")
        history = _read_chat_history()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["role"], "user")
        self.assertEqual(history[0]["content"], "New message after bad JSON.")

    @patch('multi_agent_app.history._send_recent_history_to_agents')
    @patch('multi_agent_app.history._refresh_memory')
    def test_history_sync_toggle_disables_sending(self, mock_refresh_memory, mock_send_recent_history_to_agents):
        with open(self.memory_settings_path, "w", encoding="utf-8") as f:
            json.dump({"history_sync_enabled": False}, f, ensure_ascii=False)

        for idx in range(5):
            _append_to_chat_history("user", f"message {idx}")

        mock_send_recent_history_to_agents.assert_not_called()


if __name__ == '__main__':
    unittest.main()
