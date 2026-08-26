import pytest

from vtx.coding_agent.config import Config, config, reset_config, set_config, set_ponytail
from vtx.coding_agent.context import Context
from vtx.coding_agent.prompts import (
    PONYTAIL_PROMPT,
    build_ponytail_section,
    build_system_prompt,
    is_deactivation_command,
)


def test_system_prompt_excludes_ponytail_by_default():
    set_config(Config({}))
    try:
        prompt = build_system_prompt("/tmp", Context("/tmp"))
    finally:
        reset_config()

    assert "Ponytail Mode Active" not in prompt
    assert "lazy senior developer" not in prompt


def test_system_prompt_includes_ponytail_when_enabled():
    set_config(Config({"llm": {"system_prompt": {"ponytail": True}}}))
    try:
        prompt = build_system_prompt("/tmp", Context("/tmp"))
    finally:
        reset_config()

    assert "# Ponytail Mode Active" in prompt
    assert "You are a lazy senior developer" in prompt
    assert "The ladder" in prompt
    assert "Does this need to exist at all?" in prompt


def test_system_prompt_override_ponytail_flag():
    set_config(Config({"llm": {"system_prompt": {"ponytail": False}}}))
    try:
        # Explicitly pass include_ponytail=True
        prompt_with = build_system_prompt("/tmp", Context("/tmp"), include_ponytail=True)
        assert "# Ponytail Mode Active" in prompt_with

        # Explicitly pass include_ponytail=False when config is True
        set_config(Config({"llm": {"system_prompt": {"ponytail": True}}}))
        prompt_without = build_system_prompt("/tmp", Context("/tmp"), include_ponytail=False)
        assert "# Ponytail Mode Active" not in prompt_without
    finally:
        reset_config()


def test_build_ponytail_section():
    section = build_ponytail_section()
    assert section == PONYTAIL_PROMPT
    assert "lazy senior developer" in section


def test_is_deactivation_command():
    assert is_deactivation_command("stop ponytail")
    assert is_deactivation_command("Stop Ponytail")
    assert is_deactivation_command("stop ponytail.")
    assert is_deactivation_command("stop ponytail!")
    assert is_deactivation_command("normal mode")
    assert is_deactivation_command("Normal Mode.")

    assert not is_deactivation_command("please stop ponytail now")
    assert not is_deactivation_command("add normal mode toggle")
    assert not is_deactivation_command("hello")
    assert not is_deactivation_command("")


def test_set_ponytail_persists(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    set_config(Config({}))
    try:
        cfg1 = set_ponytail(True)
        assert cfg1.llm.system_prompt.ponytail is True
        assert config.llm.system_prompt.ponytail is True

        cfg2 = set_ponytail(False)
        assert cfg2.llm.system_prompt.ponytail is False
        assert config.llm.system_prompt.ponytail is False
    finally:
        reset_config()


@pytest.mark.asyncio
async def test_register_hook_before_agent_start():
    from vtx.ai.agent.extensions import BEFORE_AGENT_START, BeforeAgentStartEvent
    from vtx.coding_agent.prompts.ponytail import register

    handlers = {}

    class MockAPI:
        def on(self, event, handler=None):
            if handler is None:

                def dec(fn):
                    handlers[event] = fn
                    return fn

                return dec
            handlers[event] = handler
            return handler

        def register_command(self, name, description, handler):
            pass

    mock_api = MockAPI()
    register(mock_api)

    assert BEFORE_AGENT_START in handlers
    handler = handlers[BEFORE_AGENT_START]

    # When ponytail is disabled: returns None
    set_config(Config({"llm": {"system_prompt": {"ponytail": False}}}))
    try:
        res = await handler(BeforeAgentStartEvent(system_prompt="Base prompt"))
        assert res is None

        # When ponytail is enabled: returns combined system prompt
        set_config(Config({"llm": {"system_prompt": {"ponytail": True}}}))
        res = await handler(BeforeAgentStartEvent(system_prompt="Base prompt"))
        assert res is not None
        assert "Base prompt" in res["system_prompt"]
        assert "Ponytail Mode Active" in res["system_prompt"]
    finally:
        reset_config()


@pytest.mark.asyncio
async def test_register_hook_input_deactivation(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    from vtx.ai.agent.extensions import INPUT, InputEvent
    from vtx.coding_agent.prompts.ponytail import register

    handlers = {}
    notifications = []

    class MockAPI:
        def on(self, event, handler=None):
            if handler is None:

                def dec(fn):
                    handlers[event] = fn
                    return fn

                return dec
            handlers[event] = handler
            return handler

        def register_command(self, name, description, handler):
            pass

        def notify(self, message, level="info"):
            notifications.append(message)

    mock_api = MockAPI()
    register(mock_api)

    assert INPUT in handlers
    input_handler = handlers[INPUT]

    set_config(Config({}))
    try:
        set_ponytail(True)
        assert config.llm.system_prompt.ponytail is True

        # Send deactivation text
        await input_handler(InputEvent(text="stop ponytail"))
        assert config.llm.system_prompt.ponytail is False
        assert any("Ponytail mode turned off" in n for n in notifications)
    finally:
        reset_config()
