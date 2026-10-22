"""Unit tests for Kimi-compatible custom code and search tools."""

import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

from agents.tools.code_execution import CodeExecutionServerTool
from agents.tools.web_search import WebSearchServerTool


class _FakeProcess:
    def __init__(self, stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0):
        self.stdout = asyncio.StreamReader()
        self.stdout.feed_data(stdout)
        self.stdout.feed_eof()
        self.stderr = asyncio.StreamReader()
        self.stderr.feed_data(stderr)
        self.stderr.feed_eof()
        self.returncode = returncode
        self.killed = False

    async def wait(self) -> int:
        return self.returncode

    def kill(self) -> None:
        self.killed = True


class CodeExecutionToolTests(unittest.IsolatedAsyncioTestCase):
    def test_schema_is_an_ordinary_custom_tool(self):
        tool = CodeExecutionServerTool()
        definition = tool.to_dict()
        self.assertEqual(definition["name"], "code_execution")
        self.assertEqual(definition["input_schema"]["required"], ["code"])
        self.assertNotIn("type", definition)

    def test_docker_command_has_isolation_controls(self):
        command = CodeExecutionServerTool()._command("print(1)", "test-container")
        self.assertIn("--network=none", command)
        self.assertIn("--read-only", command)
        self.assertIn("--cap-drop=ALL", command)
        self.assertIn("--security-opt=no-new-privileges", command)
        self.assertIn("--pull=never", command)
        self.assertEqual(
            command[-5:],
            ["python:3.12-alpine", "python", "-I", "-c", "print(1)"],
        )

    async def test_execute_returns_container_output(self):
        process = _FakeProcess(stdout=b"42\n")
        with (
            patch(
                "agents.tools.code_execution.shutil.which",
                return_value="/usr/bin/docker",
            ),
            patch(
                "agents.tools.code_execution.asyncio.create_subprocess_exec",
                new=AsyncMock(return_value=process),
            ) as create_process,
        ):
            result = await CodeExecutionServerTool().execute("print(6 * 7)")

        command = create_process.await_args.args
        self.assertEqual(command[0:2], ("docker", "run"))
        self.assertIn("print(6 * 7)", command)
        self.assertIn("exit_code: 0", result)
        self.assertIn("42", result)


class WebSearchToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_callable_backend_is_filtered_and_limited(self):
        calls = []

        def backend(**kwargs):
            calls.append(kwargs)
            return [
                {"title": "Allowed", "url": "https://docs.example.com/a"},
                {"title": "Blocked", "url": "https://ads.example.com/b"},
                {"title": "Elsewhere", "url": "https://elsewhere.test/c"},
            ]

        tool = WebSearchServerTool(
            backend=backend,
            allowed_domains=["example.com"],
            blocked_domains=["ads.example.com"],
            max_uses=1,
        )
        results = json.loads(await tool.execute("portable tools", max_results=2))

        self.assertEqual([item["title"] for item in results], ["Allowed"])
        self.assertEqual(calls[0]["query"], "portable tools")
        with self.assertRaisesRegex(RuntimeError, "limit"):
            await tool.execute("second search")

    async def test_missing_backend_fails_clearly(self):
        with patch.dict("os.environ", {}, clear=True):
            tool = WebSearchServerTool()
        with self.assertRaisesRegex(RuntimeError, "No search backend configured"):
            await tool.execute("query")

    def test_schema_is_an_ordinary_custom_tool(self):
        definition = WebSearchServerTool(backend=lambda **_: []).to_dict()
        self.assertEqual(definition["name"], "web_search")
        self.assertNotIn("type", definition)


if __name__ == "__main__":
    unittest.main()
