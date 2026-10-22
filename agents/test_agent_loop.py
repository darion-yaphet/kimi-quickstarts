"""Bounded tool execution and protocol-complete conversation history."""

import unittest
from types import SimpleNamespace

from agents.agent import Agent, ModelConfig


class RecordingTool:
    name = "record"

    def __init__(self):
        self.calls = []

    def to_dict(self):
        return {"name": self.name, "description": "Record a value", "input_schema": {"type": "object", "properties": {"value": {"type": "integer"}}}}

    async def execute(self, value):
        self.calls.append(value)
        return str(value)


class Responses:
    def __init__(self, tool_turns):
        self.tool_turns = tool_turns
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        turn = len(self.calls)
        content = (
            [SimpleNamespace(type="tool_use", id=f"tool-{turn}", name="record", input={"value": turn})]
            if turn <= self.tool_turns else [SimpleNamespace(type="text", text="Done")]
        )
        return SimpleNamespace(content=content, usage=SimpleNamespace(input_tokens=20, output_tokens=2))


class AgentLoopTests(unittest.IsolatedAsyncioTestCase):
    def make_agent(self, tool_turns, limit):
        responses = Responses(tool_turns)
        tool = RecordingTool()
        agent = Agent("test", "test", tools=[tool], config=ModelConfig(max_tool_turns=limit), client=SimpleNamespace(messages=responses))
        return agent, tool, responses

    async def test_final_answer_is_allowed_after_tool_budget(self):
        agent, tool, responses = self.make_agent(2, 2)
        result = await agent.run_async("Run two steps")
        self.assertEqual(result.content[0].text, "Done")
        self.assertEqual(tool.calls, [1, 2])
        self.assertEqual(len(responses.calls), 3)
        self.assertEqual(responses.calls[-1]["tool_choice"], {"type": "none"})

    async def test_repeated_tool_calls_stop_without_extra_execution(self):
        agent, tool, responses = self.make_agent(10, 1)
        with self.assertRaisesRegex(RuntimeError, "tool.*limit"):
            await agent.run_async("Keep calling")
        self.assertEqual(tool.calls, [1])
        self.assertEqual(len(responses.calls), 2)
        result = agent.history.messages[-1]["content"][0]
        self.assertEqual(result["tool_use_id"], "tool-2")
        self.assertTrue(result["is_error"])

    def test_invalid_tool_budgets_are_rejected(self):
        for limit in (0, -1, True, 1.5, "2"):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                ModelConfig(max_tool_turns=limit)


if __name__ == "__main__":
    unittest.main()
