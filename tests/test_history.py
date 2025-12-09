import unittest
import os
import json
import sys
from unittest.mock import patch

# Ensure multi_agent_app can be imported
sys.path.append(os.getcwd())

from multi_agent_app.history import _append_to_chat_history

class TestHistory(unittest.TestCase):
    def setUp(self):
        self.chat_history_path = "chat_history.json"
        self.memory_settings_path = "memory_settings.json"
        # テスト実行前に既存のchat_history.jsonをクリーンアップ
        if os.path.exists(self.chat_history_path):
            os.remove(self.chat_history_path)
        if os.path.exists(self.memory_settings_path):
            os.remove(self.memory_settings_path)

    def tearDown(self):
        # テスト実行後にchat_history.jsonをクリーンアップ
        if os.path.exists(self.chat_history_path):
            os.remove(self.chat_history_path)
        if os.path.exists(self.memory_settings_path):
            os.remove(self.memory_settings_path)

    def _read_chat_history(self):
        if not os.path.exists(self.chat_history_path):
            return []
        with open(self.chat_history_path, "r", encoding="utf-8") as f:
            return json.load(f)

    @patch('multi_agent_app.history._send_recent_history_to_agents')
    @patch('multi_agent_app.history._refresh_memory')
    def test_append_message_with_id(self, mock_refresh_memory, mock_send_recent_history_to_agents):
        # 最初のメッセージを追加
        _append_to_chat_history("user", "Hello, world!")
        history = self._read_chat_history()
        self.assertEqual(len(history), 1)
        self.assertIn("id", history[0])
        self.assertEqual(history[0]["id"], 1)
        self.assertEqual(history[0]["role"], "user")
        self.assertEqual(history[0]["content"], "Hello, world!")

        # 2番目のメッセージを追加
        _append_to_chat_history("assistant", "Hi there!")
        history = self._read_chat_history()
        self.assertEqual(len(history), 2)
        self.assertIn("id", history[1])
        self.assertEqual(history[1]["id"], 2)
        self.assertEqual(history[1]["role"], "assistant")
        self.assertEqual(history[1]["content"], "Hi there!")

        # 3番目のメッセージを追加
        _append_to_chat_history("user", "How are you?")
        history = self._read_chat_history()
        self.assertEqual(len(history), 3)
        self.assertIn("id", history[2])
        self.assertEqual(history[2]["id"], 3)
        self.assertEqual(history[2]["role"], "user")
        self.assertEqual(history[2]["content"], "How are you?")

    @patch('multi_agent_app.history._send_recent_history_to_agents')
    @patch('multi_agent_app.history._refresh_memory')
    def test_append_message_to_empty_file(self, mock_refresh_memory, mock_send_recent_history_to_agents):
        # ファイルが存在しない状態でメッセージを追加
        _append_to_chat_history("user", "First message.")
        history = self._read_chat_history()
        self.assertEqual(len(history), 1)
        self.assertIn("id", history[0])
        self.assertEqual(history[0]["id"], 1)
        self.assertEqual(history[0]["role"], "user")
        self.assertEqual(history[0]["content"], "First message.")

    @patch('multi_agent_app.history._send_recent_history_to_agents')
    @patch('multi_agent_app.history._refresh_memory')
    def test_json_decode_error_handling(self, mock_refresh_memory, mock_send_recent_history_to_agents):
        # 不正なJSONファイルを作成
        with open(self.chat_history_path, "w", encoding="utf-8") as f:
            f.write("これは不正なJSONです")

        _append_to_chat_history("user", "New message after bad JSON.")
        history = self._read_chat_history()
        self.assertEqual(len(history), 1)
        self.assertIn("id", history[0])
        self.assertEqual(history[0]["id"], 1)
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
