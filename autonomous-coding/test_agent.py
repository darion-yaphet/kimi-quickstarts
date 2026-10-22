"""Regression tests for the autonomous agent lifecycle."""

import asyncio
import importlib
import json
import sys
import types
from pathlib import Path

import pytest


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

agent = importlib.import_module("agent")
autonomous_agent_demo = importlib.import_module("autonomous_agent_demo")


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


def _set_feature_passes(
    project_dir: Path, value: bool, index: int | None = None
) -> None:
    feature_list_path = project_dir / "feature_list.json"
    features = json.loads(feature_list_path.read_text(encoding="utf-8"))
    if index is None:
        for feature in features:
            feature["passes"] = value
    else:
        features[index]["passes"] = value
    feature_list_path.write_text(json.dumps(features), encoding="utf-8")


def _patch_quiet_runtime(monkeypatch) -> None:
    async def no_delay(_seconds):
        return None

    monkeypatch.setattr(agent.asyncio, "sleep", no_delay)
    monkeypatch.setattr(agent, "get_initializer_prompt", lambda: "initialize")
    monkeypatch.setattr(agent, "get_coding_prompt", lambda: "code")
    monkeypatch.setattr(agent, "copy_spec_to_project", lambda _path: None)
    monkeypatch.setattr(agent, "print_session_header", lambda *_args: None)
    monkeypatch.setattr(agent, "print_progress_summary", lambda *_args: None)


def test_retries_initializer_until_required_artifacts_are_valid(
    tmp_path, monkeypatch
):
    prompts = []

    async def fake_run_agent_session(client, prompt, project_dir):
        prompts.append(prompt)
        if len(prompts) == 2:
            _write_initialization_artifacts(project_dir)
        return "continue", ""

    monkeypatch.setattr(agent, "create_client", lambda *_args: StubClient())
    monkeypatch.setattr(agent, "run_agent_session", fake_run_agent_session)
    _patch_quiet_runtime(monkeypatch)

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


def test_invalid_empty_or_truncated_feature_lists_are_not_complete(tmp_path):
    feature_list_path = tmp_path / "feature_list.json"
    truncated = json.dumps(
        [
            {
                "category": "functional",
                "description": "Only feature",
                "steps": ["Verify"],
                "passes": True,
            }
        ]
    )

    for invalid_content in ("[]", "{}", "not json", truncated):
        feature_list_path.write_text(invalid_content, encoding="utf-8")
        assert not agent.all_features_are_complete(tmp_path)


def test_retry_limit_cli_value_must_be_positive(monkeypatch):
    assert autonomous_agent_demo.positive_int("2") == 2
    with pytest.raises(
        autonomous_agent_demo.argparse.ArgumentTypeError, match="at least 1"
    ):
        autonomous_agent_demo.positive_int("0")

    monkeypatch.setattr(
        sys,
        "argv",
        ["autonomous_agent_demo.py", "--max-consecutive-failures", "4"],
    )
    assert autonomous_agent_demo.parse_args().max_consecutive_failures == 4


def test_completed_project_exits_without_creating_client(tmp_path, monkeypatch):
    _write_initialization_artifacts(tmp_path)
    _set_feature_passes(tmp_path, True)
    client_calls = 0

    def unexpected_client(*_args):
        nonlocal client_calls
        client_calls += 1
        raise AssertionError("completed projects must not create a paid session")

    monkeypatch.setattr(agent, "create_client", unexpected_client)
    _patch_quiet_runtime(monkeypatch)

    asyncio.run(
        agent.run_autonomous_agent(tmp_path, "test-model", max_iterations=1)
    )

    assert client_calls == 0


def test_stops_after_last_feature_passes(tmp_path, monkeypatch):
    _write_initialization_artifacts(tmp_path)
    _set_feature_passes(tmp_path, True)
    _set_feature_passes(tmp_path, False, index=-1)
    prompts = []

    async def complete_last_feature(client, prompt, project_dir):
        prompts.append(prompt)
        _set_feature_passes(project_dir, True, index=-1)
        return "continue", ""

    monkeypatch.setattr(agent, "create_client", lambda *_args: StubClient())
    monkeypatch.setattr(agent, "run_agent_session", complete_last_feature)
    _patch_quiet_runtime(monkeypatch)

    asyncio.run(
        agent.run_autonomous_agent(tmp_path, "test-model", max_iterations=2)
    )

    assert prompts == ["code"]


def test_sdk_errors_stop_at_consecutive_failure_budget(
    tmp_path, monkeypatch, capsys
):
    calls = 0

    def failing_client(*_args):
        nonlocal calls
        calls += 1
        raise RuntimeError("SDK unavailable")

    monkeypatch.setattr(agent, "create_client", failing_client)
    _patch_quiet_runtime(monkeypatch)

    asyncio.run(
        agent.run_autonomous_agent(
            tmp_path,
            "test-model",
            max_iterations=10,
            max_consecutive_failures=2,
        )
    )

    assert calls == 2
    assert "Reached consecutive failure limit (2)" in capsys.readouterr().out


def test_incomplete_project_does_not_stop_early(tmp_path, monkeypatch):
    _write_initialization_artifacts(tmp_path)
    _set_feature_passes(tmp_path, True)
    _set_feature_passes(tmp_path, False, index=-1)
    prompts = []

    async def leave_incomplete(client, prompt, project_dir):
        prompts.append(prompt)
        return "continue", ""

    monkeypatch.setattr(agent, "create_client", lambda *_args: StubClient())
    monkeypatch.setattr(agent, "run_agent_session", leave_incomplete)
    _patch_quiet_runtime(monkeypatch)

    asyncio.run(
        agent.run_autonomous_agent(
            tmp_path,
            "test-model",
            max_iterations=1,
        )
    )

    assert prompts == ["code"]


def test_incomplete_initializer_stops_at_failure_budget(tmp_path, monkeypatch):
    prompts = []

    async def incomplete_initializer(client, prompt, project_dir):
        prompts.append(prompt)
        return "continue", ""

    monkeypatch.setattr(agent, "create_client", lambda *_args: StubClient())
    monkeypatch.setattr(agent, "run_agent_session", incomplete_initializer)
    _patch_quiet_runtime(monkeypatch)

    asyncio.run(
        agent.run_autonomous_agent(
            tmp_path,
            "test-model",
            max_iterations=10,
            max_consecutive_failures=2,
        )
    )

    assert prompts == ["initialize", "initialize"]

