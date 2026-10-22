"""Side-effecting tools run in model order unless concurrency is explicit."""

import asyncio
import unittest
from types import SimpleNamespace

from agents.utils.tool_util import execute_tools


class ToolOrderTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_preserves_two_edits_to_the_same_resource(self):
        state = {"text": ""}

        class AppendTool:
            async def execute(self, text):
                previous = state["text"]
                await asyncio.sleep(0)
                state["text"] = previous + text
                return state["text"]

        calls = [SimpleNamespace(id=str(index), name="append", input={"text": text}) for index, text in enumerate("AB")]
        results = await execute_tools(calls, {"append": AppendTool()})
        self.assertEqual(state["text"], "AB")
        self.assertEqual([result["content"] for result in results], ["A", "AB"])

    async def test_independent_tools_can_explicitly_run_in_parallel(self):
        started = []
        both_started = asyncio.Event()

        class IndependentTool:
            async def execute(self, value):
                started.append(value)
                if len(started) == 2:
                    both_started.set()
                await asyncio.wait_for(both_started.wait(), timeout=1)
                return value

        calls = [SimpleNamespace(id=str(value), name="read", input={"value": value}) for value in (1, 2)]
        results = await execute_tools(calls, {"read": IndependentTool()}, parallel=True)
        self.assertEqual([result["content"] for result in results], ["1", "2"])


if __name__ == "__main__":
    unittest.main()
