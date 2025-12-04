import unittest
from unittest.mock import patch

from multi_agent_app import settings


class ResolveLLMConfigAliasTests(unittest.TestCase):
    @patch("multi_agent_app.settings._load_agent_env")
    @patch("multi_agent_app.settings.load_model_settings")
    def test_claude_aliases_are_respected(self, mock_load_model_settings, mock_load_agent_env):
        mock_load_model_settings.return_value = {
            "orchestrator": {"provider": "claude", "model": "claude-haiku-4-5", "base_url": ""}
        }
        mock_load_agent_env.return_value = {
            "CLAUDE_API_KEY": "alias-api-key",
            "CLAUDE_API_BASE": "https://claude.example.com",
        }

        config = settings.resolve_llm_config("orchestrator")

        self.assertEqual(config["api_key"], "alias-api-key")
        self.assertEqual(config["base_url"], "https://claude.example.com")
        self.assertEqual(config["provider"], "claude")


if __name__ == "__main__":
    unittest.main()
