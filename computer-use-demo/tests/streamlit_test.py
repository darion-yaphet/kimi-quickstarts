from types import SimpleNamespace
from unittest import mock

import pytest
from anthropic.types import TextBlockParam
from streamlit.testing.v1 import AppTest

from computer_use_demo.streamlit import (
    INTERRUPT_TEXT,
    Sender,
    maybe_add_interruption_blocks,
    validate_auth,
)


@pytest.fixture
def streamlit_app(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    monkeypatch.delenv("KIMI_MODEL", raising=False)
    monkeypatch.delenv("API_PROVIDER", raising=False)
    return AppTest.from_file("computer_use_demo/streamlit.py")


def test_streamlit(streamlit_app: AppTest):
    streamlit_app.run()
    streamlit_app.text_input[1].set_value("sk-ant-0000000000000").run()
    with mock.patch("computer_use_demo.loop.sampling_loop") as patch:
        streamlit_app.chat_input[0].set_value("Hello").run()
        assert patch.called
        assert patch.call_args.kwargs["messages"] == [
            {
                "role": Sender.USER,
                "content": [TextBlockParam(text="Hello", type="text")],
            }
        ]
        assert not streamlit_app.exception


@pytest.mark.parametrize(
    ("model", "expected_modes"),
    [
        # Undated alias of an extended-only model: adaptive must not be offered.
        ("claude-sonnet-4-5", ["off", "extended"]),
        # Adaptive-only models: the manual thinking budget must not be offered.
        ("claude-opus-4-7", ["adaptive", "off"]),
        ("claude-opus-4-8", ["adaptive", "off"]),
        # Bedrock form of a known family resolves to that family's config.
        ("anthropic.claude-sonnet-4-5-20250929-v1:0", ["off", "extended"]),
        # Unknown model: every mode is selectable, defaulting to off.
        ("some-unknown-model", ["off", "adaptive", "extended"]),
    ],
)
def test_thinking_modes_follow_selected_model(
    streamlit_app: AppTest, model: str, expected_modes: list[str]
):
    streamlit_app.run()
    provider = next(r for r in streamlit_app.radio if r.label == "API Provider")
    provider.set_value("anthropic").run()
    streamlit_app.text_input[0].set_value(model).run()
    thinking_radios = [r for r in streamlit_app.radio if r.label == "Thinking"]
    assert len(thinking_radios) == 1
    assert list(thinking_radios[0].options) == [m.capitalize() for m in expected_modes]
    assert streamlit_app.session_state["thinking_mode"] == expected_modes[0]
    assert not streamlit_app.exception


def test_kimi_provider_uses_compatible_defaults(streamlit_app: AppTest):
    streamlit_app.run()
    provider = next(r for r in streamlit_app.radio if r.label == "API Provider")
    provider.set_value("kimi").run()

    assert streamlit_app.session_state["provider"] == "kimi"
    assert streamlit_app.session_state["model"] == "kimi-for-coding"
    assert streamlit_app.session_state["thinking_mode"] == "off"
    assert streamlit_app.session_state["tool_version"] == "computer_use_20250124"
    assert streamlit_app.session_state["token_efficient_tools_beta"] is False
    assert any(field.label == "Kimi API Key" for field in streamlit_app.text_input)
    assert not any(r.label == "Tool Versions" for r in streamlit_app.radio)
    assert not any(r.label == "Thinking" for r in streamlit_app.radio)
    assert not any(
        checkbox.label == "Enable token-efficient tools beta"
        for checkbox in streamlit_app.checkbox
    )

    model = next(field for field in streamlit_app.text_input if field.label == "Model")
    model.set_value("k3").run()
    assert streamlit_app.session_state["thinking_mode"] == "off"
    assert streamlit_app.session_state["tool_version"] == "computer_use_20250124"
    assert not any(r.label == "Thinking" for r in streamlit_app.radio)
    assert not streamlit_app.exception


def test_kimi_auth_requires_existing_api_key():
    from computer_use_demo.loop import APIProvider

    assert validate_auth(APIProvider.KIMI, "") == (
        "Enter your Kimi API key in the sidebar to continue."
    )
    assert validate_auth(APIProvider.KIMI, "sk-kimi-test") is None


def test_kimi_does_not_load_persisted_anthropic_key(monkeypatch):
    from computer_use_demo.streamlit import _load_api_key

    monkeypatch.delenv("KIMI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with mock.patch("computer_use_demo.streamlit.load_from_storage") as load:
        load.side_effect = lambda name: "legacy-secret" if name == "api_key" else None
        assert _load_api_key("kimi") == ""
        load.assert_called_once_with("kimi_api_key")


def test_switch_provider_clears_conversation(streamlit_app: AppTest):
    streamlit_app.run()
    streamlit_app.session_state["messages"] = [
        {"role": "user", "content": "private history"}
    ]
    provider = next(r for r in streamlit_app.radio if r.label == "API Provider")
    provider.set_value("anthropic").run()
    assert streamlit_app.session_state["messages"] == []
    assert not streamlit_app.exception


def test_interruption_blocks_keep_toolset_name():
    """Healing an interrupted turn must stamp toolset_name on the results of
    toolset member calls and leave it off plain tool calls, or the next
    request is rejected."""
    state = SimpleNamespace(
        in_sampling_loop=True,
        tools={},
        messages=[
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Clicking, then listing files."},
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "left_click",
                        "input": {"coordinate": [1, 2]},
                        "toolset_name": "computer",
                    },
                    {
                        "type": "tool_use",
                        "id": "toolu_2",
                        "name": "bash",
                        "input": {"command": "ls"},
                    },
                ],
            }
        ],
    )
    with mock.patch("computer_use_demo.streamlit.st.session_state", state):
        blocks = maybe_add_interruption_blocks()

    member, plain, text = blocks
    assert member["tool_use_id"] == "toolu_1"
    assert member["is_error"] is True
    assert member["toolset_name"] == "computer"
    assert plain["tool_use_id"] == "toolu_2"
    assert "toolset_name" not in plain
    assert text == {"type": "text", "text": INTERRUPT_TEXT}
    assert set(state.tools) == {"toolu_1", "toolu_2"}
