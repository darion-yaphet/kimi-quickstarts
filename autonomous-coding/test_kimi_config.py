"""Tests for the Kimi-to-Claude-Code environment bridge."""

import importlib.util
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


def load_client_module():
    """Load client.py with lightweight SDK stubs for configuration tests."""
    sdk = types.ModuleType("claude_code_sdk")
    sdk.ClaudeCodeOptions = object
    sdk.ClaudeSDKClient = object
    sdk_types = types.ModuleType("claude_code_sdk.types")
    sdk_types.HookMatcher = object
    security = types.ModuleType("security")
    security.bash_security_hook = object()

    module_path = Path(__file__).with_name("client.py")
    spec = importlib.util.spec_from_file_location("kimi_client_config", module_path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(
        sys.modules,
        {
            "claude_code_sdk": sdk,
            "claude_code_sdk.types": sdk_types,
            "security": security,
        },
    ):
        spec.loader.exec_module(module)
    return module


class KimiEnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.client = load_client_module()

    def test_default_model_sets_all_claude_code_tiers_and_1m_context(self):
        with patch.dict(os.environ, {}, clear=True):
            self.client.configure_kimi_environment("kimi-for-coding")
            self.assertEqual(
                os.environ["ANTHROPIC_BASE_URL"],
                "https://api.kimi.com/coding/",
            )
            for variable in self.client.MODEL_ENV_VARS:
                self.assertEqual(os.environ[variable], "kimi-for-coding")
            self.assertEqual(
                os.environ["CLAUDE_CODE_MAX_CONTEXT_TOKENS"], "1048576"
            )

    def test_user_overrides_are_preserved(self):
        custom = {
            "ANTHROPIC_BASE_URL": "https://api.kimi.ai/coding/",
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": "kimi-for-coding-highspeed",
            "CLAUDE_CODE_MAX_CONTEXT_TOKENS": "12345",
        }
        with patch.dict(os.environ, custom, clear=True):
            self.client.configure_kimi_environment("k3-256k")
            for key, value in custom.items():
                self.assertEqual(os.environ[key], value)
            self.assertEqual(os.environ["ANTHROPIC_MODEL"], "k3-256k")
            self.assertEqual(
                os.environ["CLAUDE_CODE_AUTO_COMPACT_WINDOW"], "262144"
            )

    def test_stale_claude_environment_is_replaced(self):
        previous = {
            "ANTHROPIC_BASE_URL": "https://api.anthropic.com/",
            "ANTHROPIC_MODEL": "claude-sonnet-4-5",
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": "claude-haiku-4-5",
        }
        with patch.dict(os.environ, previous, clear=True):
            self.client.configure_kimi_environment("kimi-for-coding")
            self.assertEqual(os.environ["ANTHROPIC_BASE_URL"], self.client.DEFAULT_BASE_URL)
            self.assertEqual(os.environ["ANTHROPIC_MODEL"], "kimi-for-coding")
            self.assertEqual(os.environ["ANTHROPIC_DEFAULT_HAIKU_MODEL"], "kimi-for-coding")


if __name__ == "__main__":
    unittest.main()
