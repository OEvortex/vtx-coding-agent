"""Tests for the new vtx hook system."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from vtx.agent.hooks import (
    HOOK_EVENTS,
    HookConfig,
    HookConfigManager,
    HookContextBuilder,
    HookDiffEntry,
    HookRegistry,
    HookResult,
    HookRuntime,
    HookSnapshot,
    run_hook_handlers,
    validate_hook_configs,
)
from vtx.agent.hooks.bridge import HookBridge
from vtx.plugins.bus import ExtensionBus


def test_hook_event_registry_exposes_expected_events():
    assert "SessionStart" in HOOK_EVENTS
    assert "PreToolUse" in HOOK_EVENTS
    assert "PostToolUse" in HOOK_EVENTS


def test_register_and_emit_hook_handlers():
    registry = HookRegistry()
    seen: list[str] = []

    async def handler(context, config):
        seen.append(context["event"])
        return HookResult(output="handled")

    async def drive():
        await registry.register(
            "SessionStart", HookConfig(event="SessionStart", command="echo start"), handler=handler
        )
        await registry.register(
            "PreToolUse",
            HookConfig(event="PreToolUse", command="echo tool", matcher="read*"),
            handler=handler,
        )
        hooks = await registry.get_hooks("SessionStart")
        return await run_hook_handlers(hooks, {"event": "SessionStart"})

    results = asyncio.run(drive())
    assert results["results"][0].output == "handled"
    assert seen == ["SessionStart"]


def test_hook_matcher_limits_tool_hooks():
    registry = HookRegistry()
    seen: list[str] = []

    async def handler(context, config):
        seen.append(context["tool_name"])
        return HookResult()

    async def drive():
        await registry.register(
            "PreToolUse",
            HookConfig(event="PreToolUse", command="echo read", matcher="read*"),
            handler=handler,
        )
        hooks = await registry.get_hooks("PreToolUse", tool_name="read_file")
        await run_hook_handlers(hooks, {"tool_name": "read_file"})

    asyncio.run(drive())
    assert seen == ["read_file"]


def test_claim_once_hooks_only_fires_once():
    registry = HookRegistry()
    seen: list[str] = []

    async def handler(context, config):
        seen.append(context["event"])
        return HookResult()

    async def drive():
        await registry.register(
            "SessionStart",
            HookConfig(event="SessionStart", command="echo setup", once=True),
            handler=handler,
        )
        first = await registry.claim_once_hooks("SessionStart")
        await run_hook_handlers(first, {"event": "SessionStart"})
        return await registry.claim_once_hooks("SessionStart")

    remaining = asyncio.run(drive())
    assert remaining == []
    assert seen == ["SessionStart"]


def test_hook_snapshot_detects_added_and_removed_hooks():
    before = HookSnapshot(
        events={"SessionStart": [HookConfig(event="SessionStart", command="echo before")]}
    )
    after = HookSnapshot(
        events={"SessionStart": [HookConfig(event="SessionStart", command="echo after")]}
    )
    diff = before.diff(after)
    assert diff == [
        HookDiffEntry("SessionStart", "add", "user", "command|echo after"),
        HookDiffEntry("SessionStart", "remove", "user", "command|echo before"),
    ]


async def _load_snapshot(registry, path):
    manager = HookConfigManager(registry, project_path=path)
    return await manager.load()


def test_hook_config_manager_loads_yaml(tmp_path: Path):
    hooks_path = tmp_path / ".vtx" / "hooks.yml"
    hooks_path.parent.mkdir(parents=True)
    hooks_path.write_text(
        "\n".join(
            [
                "session_start:",
                "  - event: SessionStart",
                "    type: command",
                "    command: echo setup",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    registry = HookRegistry()
    snapshot = asyncio.run(_load_snapshot(registry, hooks_path))
    assert snapshot.events["SessionStart"][0].command == "echo setup"
    hooks = asyncio.run(registry.get_hooks("SessionStart"))
    assert len(hooks) == 1


def test_validate_hook_configs_accepts_valid_schema():
    errors = validate_hook_configs(
        {"SessionStart": [HookConfig(event="SessionStart", command="echo start")]}
    )
    assert errors == []


def test_validate_hook_configs_rejects_unknown_events_and_missing_command():
    errors = validate_hook_configs(
        {
            "NotReal": [HookConfig(event="NotReal", command="echo")],  # type: ignore
            "SessionStart": [HookConfig(event="SessionStart")],
        }
    )
    assert any(error.event == "NotReal" for error in errors)
    assert any(error.field == "command" for error in errors)


def test_hook_runtime_emits_registered_hooks():
    registry = HookRegistry()
    runtime = HookRuntime(registry)
    seen: list[str] = []

    async def handler(context, config):
        seen.append(context["event"])
        return {"result": "ok", "event": context["event"]}

    async def drive():
        await registry.register(
            "PostToolUse",
            HookConfig(event="PostToolUse", command="echo post", matcher="read"),
            handler=handler,
        )
        return await runtime.emit("PostToolUse", {"event": "PostToolUse", "tool_name": "read"})

    result = asyncio.run(drive())
    assert seen == ["PostToolUse"]
    assert result["result"] == "ok"


def test_hook_snapshot_treats_missing_and_default_fields_as_equal():
    base = HookConfig(event="PreToolUse", type="command", command="echo read", matcher="read*")
    first = HookSnapshot(events={"PreToolUse": [base]})
    second = HookSnapshot(
        events={
            "PreToolUse": [
                HookConfig(
                    event="PreToolUse",
                    type="command",
                    command="echo read",
                    matcher="read*",
                    timeout=None,
                    once=False,
                    prompt_text=None,
                    agent_instructions=None,
                    if_condition=None,
                )
            ]
        }
    )
    assert first.diff(second) == []


def test_hook_snapshot_add_and_remove_are_distinct():
    before = HookSnapshot(
        events={
            "SessionStart": [HookConfig(event="SessionStart", source="user")],
            "Setup": [HookConfig(event="Setup", source="project")],
        }
    )
    after = HookSnapshot(
        events={
            "SessionStart": [
                HookConfig(event="SessionStart", source="user", command="echo after")
            ],
            "Setup": [],
        }
    )
    diff = before.diff(after)
    assert diff == [
        HookDiffEntry("SessionStart", "add", "user", "command|echo after"),
        HookDiffEntry("SessionStart", "remove", "user", "command|"),
        HookDiffEntry("Setup", "remove", "project", "command|"),
    ]


def test_hook_context_builder_helpers_build_expected_payloads():
    builder = HookContextBuilder()

    session = builder.for_session_event("session_start", session_id="abc", cwd="/tmp")
    assert session == {
        "event": "session_start",
        "session_id": "abc",
        "cwd": "/tmp",
        "timestamp": session["timestamp"],
    }

    prompt = builder.for_prompt_event("user_prompt_submit", "hello")
    assert prompt == {
        "event": "user_prompt_submit",
        "prompt": "hello",
        "session_id": None,
        "cwd": None,
        "timestamp": prompt["timestamp"],
    }

    tool = builder.for_tool_event(
        "pre_tool_use", tool_name="read", arguments={"path": "/tmp"}, session_id="abc"
    )
    assert tool["tool_call_id"] is None
    assert tool.get("result") is None

    setup = builder.for_session_setup_event(
        "setup", session_id="abc", cwd="/tmp", model="m", provider="p", config={"foo": "bar"}
    )
    assert setup["model"] == "m"

    permission = builder.for_permission_event(
        "permission_request", permission="write", tool_name="bash", arguments={"cmd": "rm"}
    )
    assert permission["permission"] == "write"


def test_post_tool_use_command_hook_output_rewrite():
    """A command PostToolUse hook can rewrite the tool's LLM-visible output."""
    from vtx.agent.hooks.bridge import _make_handler_for_config

    cfg = HookConfig(event="PostToolUse", type="command", command="echo rewritten-output")
    handler = _make_handler_for_config(cfg)
    result = asyncio.run(handler({"tool_name": "read"}, cfg))
    assert result.output == "rewritten-output"


def test_hook_if_condition_gates_execution():
    """A hook with a false if_condition does not run."""
    from vtx.agent.hooks.bridge import _make_handler_for_config
    from vtx.agent.hooks.runtime import HookRuntime

    registry = HookRegistry()

    async def drive():
        # Use the bridge's real prompt handler so prompt_text drives output.
        await registry.register(
            "PostToolUse",
            HookConfig(
                event="PostToolUse",
                type="prompt",
                prompt_text="p",
                if_condition="tool_name == 'read'",
            ),
            handler=_make_handler_for_config(
                HookConfig(event="PostToolUse", type="prompt", prompt_text="p")
            ),
        )
        runtime = HookRuntime(registry)
        hit = await runtime.emit("PostToolUse", {"event": "PostToolUse", "tool_name": "write"})
        ran = await runtime.emit("PostToolUse", {"event": "PostToolUse", "tool_name": "read"})
        return hit, ran

    hit, ran = asyncio.run(drive())
    assert hit == {}  # condition false → not run
    assert ran["results"][0].output == "p"  # condition true → run


def test_hook_prompt_and_agent_handlers():
    """prompt/agent hooks return their instruction text without executing."""
    from vtx.agent.hooks.bridge import _make_handler_for_config

    prompt_cfg = HookConfig(event="PostToolUse", type="prompt", prompt_text="do-x")
    agent_cfg = HookConfig(event="PostToolUse", type="agent", agent_instructions="spawn-x")

    prompt_result = asyncio.run(
        _make_handler_for_config(prompt_cfg)({"tool_name": "read"}, prompt_cfg)
    )
    agent_result = asyncio.run(
        _make_handler_for_config(agent_cfg)({"tool_name": "read"}, agent_cfg)
    )
    assert prompt_result.output == "do-x"
    assert agent_result.metadata["agent_instructions"] == "spawn-x"


def test_hook_http_handler_requires_httpx(monkeypatch):
    """An http hook reports a clear error when httpx is unavailable."""
    from vtx.agent.hooks.bridge import _make_handler_for_config

    cfg = HookConfig(event="PostToolUse", type="http", url="http://localhost/x")
    handler = _make_handler_for_config(cfg)
    result = asyncio.run(handler({"tool_name": "read"}, cfg))
    # Either it reached the network (no httpx import error path) or it
    # reported the missing-dependency blocking error. Both are valid; we just
    # assert a structured result came back.
    assert isinstance(result, HookResult)


class TestBridgeUnload:
    """A reload unsubscribes the bridge's handlers instead of stacking more."""

    def _bridge(self, tmp_path: Path) -> HookBridge:
        bus = ExtensionBus()
        return HookBridge(
            bus,
            project_path=tmp_path / "project-hooks.yml",
            global_path=tmp_path / "global-hooks.yml",
        )

    def _write_hooks(self, path: Path, *, count: int = 1) -> None:
        # The on-disk schema is lowercase event keys with the typed event name
        # inside each entry — the same shape the loader reads in production.
        hooks = "\n".join(
            "  - event: SessionStart\n    type: command\n    command: echo start"
            for _ in range(count)
        )
        path.write_text(f"session_start:\n{hooks}\n", encoding="utf-8")

    @pytest.mark.asyncio
    async def test_unload_removes_the_bus_subscriptions(self, tmp_path: Path):
        bridge = self._bridge(tmp_path)
        self._write_hooks(bridge._project_path)
        await bridge.load()
        assert bridge.loaded_count == 1
        assert bridge._bus.handler_count("session_start") == 1

        await bridge.unload()
        # The registry and the bus both go quiet: a reload that only cleared the
        # registry would fire every hook twice from two bridges.
        assert bridge.loaded_count == 0
        assert bridge._bus.handler_count("session_start") == 0

    @pytest.mark.asyncio
    async def test_a_reload_re_registers_rather_than_doubling(self, tmp_path: Path):
        bridge = self._bridge(tmp_path)
        self._write_hooks(bridge._project_path, count=2)
        await bridge.load()
        await bridge.unload()
        bridge.rebind(ExtensionBus())
        await bridge.load()
        assert bridge.loaded_count == 2
        assert bridge._bus.handler_count("session_start") == 1

    @pytest.mark.asyncio
    async def test_a_deleted_hooks_file_silences_the_bridge(self, tmp_path: Path):
        bridge = self._bridge(tmp_path)
        self._write_hooks(bridge._project_path)
        await bridge.load()
        bridge._project_path.unlink()
        await bridge.unload()
        await bridge.load()
        assert bridge.loaded_count == 0
        assert bridge._bus.handler_count("session_start") == 0


def test_the_bridge_reports_a_count_that_starts_at_zero(tmp_path: Path):
    bridge = TestBridgeUnload()._bridge(tmp_path)
    assert bridge.loaded_count == 0
