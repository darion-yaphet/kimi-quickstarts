"""Regression tests for the autonomous agent lifecycle."""

import asyncio
import json
import sys
import types
from pathlib import Path


class _SdkPlaceholder:
    def __init__(self, *args, **kwargs):
        pass


sdk_stub = types.ModuleType("claude_code_sdk")
sdk_stub.ClaudeSDKClient = _SdkPlaceholder
sdk_stub.ClaudeCodeOptions = _SdkPlaceholder
sys.modules.setdefault("claude_code_sdk", sdk_stub)

sdk_types_stub = types.ModuleType("claude_code_sdk.types")
sdk_types_stub.HookMatcher = _SdkPlaceholder
sys.modules.setdefault("claude_code_sdk.types", sdk_types_stub)

import agent


class StubClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False


def _write_initialization_artifacts(project_dir: Path) -> None:
    features = [
        {
            "category": "functional",
            "description": f"Feature {index}",
            "steps": ["Run the feature", "Verify the result"],
            "passes": False,
        }
        for index in range(200)
    ]
    (project_dir / "feature_list.json").write_text(
        json.dumps(features), encoding="utf-8"
    )
    (project_dir / "init.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (project_dir / "README.md").write_text("# Generated project\n", encoding="utf-8")


def test_retries_initializer_until_required_artifacts_are_valid(
    tmp_path, monkeypatch
):
    prompts = []

    async def fake_run_agent_session(client, prompt, project_dir):
        prompts.append(prompt)
        if len(prompts) == 2:
            _write_initialization_artifacts(project_dir)
        return "continue", ""

    async def no_delay(_seconds):
        return None

    monkeypatch.setattr(agent, "create_client", lambda *_args: StubClient())
    monkeypatch.setattr(agent, "run_agent_session", fake_run_agent_session)
    monkeypatch.setattr(agent.asyncio, "sleep", no_delay)
    monkeypatch.setattr(agent, "get_initializer_prompt", lambda: "initialize")
    monkeypatch.setattr(agent, "get_coding_prompt", lambda: "code")
    monkeypatch.setattr(agent, "copy_spec_to_project", lambda _path: None)
    monkeypatch.setattr(agent, "print_session_header", lambda *_args: None)
    monkeypatch.setattr(agent, "print_progress_summary", lambda *_args: None)

    asyncio.run(
        agent.run_autonomous_agent(
            project_dir=tmp_path,
            model="test-model",
            max_iterations=3,
        )
    )

    assert prompts == ["initialize", "initialize", "code"]


def test_completed_features_do_not_invalidate_initialization(tmp_path):
    _write_initialization_artifacts(tmp_path)
    feature_list_path = tmp_path / "feature_list.json"
    features = json.loads(feature_list_path.read_text(encoding="utf-8"))
    features[0]["passes"] = True
    feature_list_path.write_text(json.dumps(features), encoding="utf-8")

    assert agent.initialization_artifacts_are_valid(tmp_path)

