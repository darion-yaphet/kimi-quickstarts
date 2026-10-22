"""Unit tests for Kimi-compatible defaults."""

import os
import unittest
from unittest.mock import MagicMock, patch

from agents.agent import Agent, ModelConfig


class FakeMessages:
    def create(self, **kwargs):
        return kwargs


class FakeClient:
    def __init__(self):
        self.messages = FakeMessages()


class KimiConfigTests(unittest.TestCase):
    def test_client_uses_kimi_endpoint_by_default(self):
        fake_client = MagicMock()
        fake_client.messages.count_tokens.side_effect = RuntimeError
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}, clear=True):
            with patch("agents.agent.Anthropic", return_value=fake_client) as client:
                Agent(name="test", system="test")
        client.assert_called_once_with(
            api_key="test-key",
            base_url="https://api.kimi.com/coding/",
        )

    def test_model_comes_from_environment(self):
        with patch.dict(os.environ, {"ANTHROPIC_MODEL": "k3-256k"}):
            self.assertEqual(ModelConfig().model, "k3-256k")

    def test_extra_headers_are_forwarded_without_injected_beta(self):
        agent = Agent(
            name="test",
            system="test",
            client=FakeClient(),
            message_params={"extra_headers": {"x-test": "value"}},
        )
        params = agent._prepare_message_params()
        self.assertEqual(params["extra_headers"], {"x-test": "value"})
        self.assertNotIn("anthropic-beta", params["extra_headers"])

    def test_prompt_caching_is_disabled_for_portable_defaults(self):
        agent = Agent(name="test", system="test", client=FakeClient())
        self.assertFalse(agent.history.enable_caching)


if __name__ == "__main__":
    unittest.main()
