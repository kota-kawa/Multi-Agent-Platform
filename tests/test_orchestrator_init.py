import unittest
from unittest.mock import patch
import sys
import os

# Ensure multi_agent_app can be imported
sys.path.append(os.getcwd())

# We need to handle potential import errors if dependencies are missing in the environment running the test
try:
    from multi_agent_app.orchestrator import MultiAgentOrchestrator
except ImportError:
    MultiAgentOrchestrator = None

class TestOrchestratorInit(unittest.TestCase):
    def setUp(self):
        if MultiAgentOrchestrator is None:
            self.skipTest("multi_agent_app dependencies not available")

    @patch('multi_agent_app.orchestrator.resolve_llm_config')
    @patch('multi_agent_app.orchestrator.ChatOpenAI')
    @patch('multi_agent_app.orchestrator.MultiAgentOrchestrator._build_graph')
    def test_temperature_logic(self, mock_build_graph, mock_chat, mock_resolve):
        # Case 1: gpt-4 (normal)
        mock_resolve.return_value = {"model": "gpt-4", "api_key": "dummy", "base_url": None}
        MultiAgentOrchestrator()
        _, kwargs = mock_chat.call_args
        self.assertEqual(kwargs['temperature'], 0.1, "Expected 0.1 for gpt-4")

        # Case 2: o1-preview (reasoning)
        mock_resolve.return_value = {"model": "o1-preview", "api_key": "dummy", "base_url": None}
        MultiAgentOrchestrator()
        _, kwargs = mock_chat.call_args
        self.assertEqual(kwargs['temperature'], 1, "Expected 1 for o1-preview")

        # Case 3: gpt-5-mini (reasoning/fixed)
        mock_resolve.return_value = {"model": "gpt-5-mini", "api_key": "dummy", "base_url": None}
        MultiAgentOrchestrator()
        _, kwargs = mock_chat.call_args
        self.assertEqual(kwargs['temperature'], 1, "Expected 1 for gpt-5-mini")

        # Case 4: gpt-4o (normal)
        mock_resolve.return_value = {"model": "gpt-4o", "api_key": "dummy", "base_url": None}
        MultiAgentOrchestrator()
        _, kwargs = mock_chat.call_args
        self.assertEqual(kwargs['temperature'], 0.1, "Expected 0.1 for gpt-4o")

if __name__ == '__main__':
    unittest.main()
