"""The composition is load-bearing: the runtime reads its seams from it.

These are the tests that matter most, because they are what distinguish a
framework from a decoration. Each one swaps a service and observes a change in
the real harness, not in a test double.
"""

from __future__ import annotations

from typing import ClassVar

import pytest

from vtx.plugins import PluginHost, boot, plugin
from vtx.plugins.services import PromptService


@pytest.fixture()
def composed(tmp_path, monkeypatch):
    monkeypatch.setattr("vtx.core.paths.get_config_dir", lambda: tmp_path)
    host = boot()
    yield host
    host.dispose()


class _StubTool:
    name = "stub"
    description = "A tool the composition contributed."
    mutating = False


def _runtime(tmp_path, **kwargs):
    from vtx.agent.runtime import ConversationRuntime

    return ConversationRuntime(cwd=str(tmp_path), tools=[], **kwargs)


def test_the_runtime_reads_the_active_composition(tmp_path, composed):
    runtime = _runtime(tmp_path)
    assert runtime.plugins is composed
    assert runtime.plugin_ctx is composed.base
    assert "prompts" in runtime.plugin_ctx


def test_the_runtime_accepts_an_explicit_composition(tmp_path, composed):
    runtime = _runtime(tmp_path, plugins=composed)
    assert runtime.plugins is composed


def test_an_explicit_host_overrides_the_active_one(tmp_path, monkeypatch):
    monkeypatch.setattr("vtx.core.paths.get_config_dir", lambda: tmp_path)
    active = boot()
    try:
        other = PluginHost([], label="other")
        other.mount()
        runtime = _runtime(tmp_path, plugins=other)
        assert runtime.plugins is other
        assert runtime.plugins is not active
        other.dispose()
    finally:
        active.dispose()


def test_the_prompt_comes_from_the_compositions_sections(tmp_path, composed):
    """A row-added prompt section reaches the system prompt the model sees."""
    prompts = composed.base.prompts
    prompts.add_section("be-nice", lambda cwd, tools: "Always say please.")
    runtime = _runtime(tmp_path)
    built = runtime.resolve_system_prompt()
    assert "Always say please." in built


def test_a_composition_without_a_prompt_row_still_builds_a_prompt(tmp_path, monkeypatch):
    """The seam must be optional: an embedder that composes nothing gets the
    built-in assembly, not a crash."""
    monkeypatch.setattr("vtx.core.paths.get_config_dir", lambda: tmp_path)
    monkeypatch.setattr("vtx.plugins.bootstrap._ACTIVE", None)
    runtime = _runtime(tmp_path)
    built = runtime.resolve_system_prompt()
    assert built  # non-empty, produced by the built-in builder
    assert runtime._prompt_service() is None


def test_the_prompt_service_is_whichever_the_composition_provides(tmp_path, composed):
    runtime = _runtime(tmp_path)
    assert runtime._prompt_service() is composed.base.prompts


def test_a_swapped_prompt_service_is_used(tmp_path, monkeypatch):
    monkeypatch.setattr("vtx.core.paths.get_config_dir", lambda: tmp_path)
    host = boot()
    try:
        calls: list[tuple[str, list]] = []

        class RecordingPrompts(PromptService):
            def build(self, cwd, tools=None, **kwargs):
                calls.append((cwd, list(tools or [])))
                return "SWAPPED"

        host.add(
            plugin(name="prompts")(lambda ctx, cfg: ctx.provide("prompts", RecordingPrompts()))
        )
        host.mount()
        runtime = _runtime(tmp_path, plugins=host)
        assert runtime.resolve_system_prompt() == "SWAPPED"
        assert calls and calls[0][0] == str(tmp_path)
    finally:
        host.dispose()


def test_the_loop_is_taken_from_the_composition(tmp_path, composed):
    loop = composed.base.agentLoop
    assert hasattr(loop, "create")
    assert hasattr(loop, "run")
    assert composed.base.agent_loop is loop


def test_a_swapped_loop_builds_the_engine(tmp_path, monkeypatch):
    """``ctx.agentLoop`` is a row: substitute it and ``_new_agent`` uses it."""
    from vtx.agent.loop import Agent
    from vtx.agent.session import Session

    monkeypatch.setattr("vtx.core.paths.get_config_dir", lambda: tmp_path)
    host = boot()
    try:
        seen: list[str] = []

        class RecordingLoop:
            def create(self, provider, tools, session, **kwargs):
                seen.append("created")
                return Agent(provider, tools, session, **kwargs)

            async def run(self, engine, query, **kwargs):
                seen.append("ran")
                return []

        host.add(
            plugin(name="agentLoop")(lambda ctx, cfg: ctx.provide("agentLoop", RecordingLoop()))
        )
        host.mount()
        runtime = _runtime(tmp_path, plugins=host)
        session = Session(session_id="s", cwd=str(tmp_path))
        agent = runtime._new_agent(None, session)
        assert seen == ["created"]
        assert isinstance(agent, Agent)
    finally:
        host.dispose()


def test_a_session_cached_prompt_wins_when_no_agent_is_active(tmp_path, composed):
    """An active agent's instructions are per-session, so a cached prompt from a
    different agent must not be reused; the session cache is used otherwise."""
    from vtx.agent.session import Session

    session = Session.create(str(tmp_path), persist=False, system_prompt="CACHED")
    runtime = _runtime(tmp_path)
    assert runtime.resolve_system_prompt(session) == "CACHED"


def test_an_active_agents_instructions_beat_a_cached_prompt(tmp_path, composed, monkeypatch):
    """The other half of the same rule: switching agents must re-render."""
    from vtx.agent.session import Session

    session = Session.create(str(tmp_path), persist=False, system_prompt="CACHED")
    composed.base.prompts.add_section("marker", lambda cwd, tools: "AGENT-MARK")
    runtime = _runtime(tmp_path)
    monkeypatch.setattr(runtime, "agent_registry", _FakeRegistry(_FakeAgent()))
    assert "AGENT-MARK" in runtime.resolve_system_prompt(session)
    assert "CACHED" not in runtime.resolve_system_prompt(session)


class _FakeAgent:
    instructions = "AGENT-MARK-INSTRUCTIONS"
    instructions_mode = "append"
    skills: ClassVar[list[str]] = []

    def __init__(self):
        self.definition = self

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return "<_FakeAgent>"


class _FakeRegistry:
    def __init__(self, active):
        self._active = active

    @property
    def active(self):
        return self._active

    @property
    def names(self):
        return [self._active.definition.instructions]


def test_the_runtimes_plugins_default_to_the_booted_composition():
    from vtx.agent.runtime import _active_plugin_host
    from vtx.plugins import active_host, boot, reset

    host = boot()
    try:
        assert _active_plugin_host() is host
        assert _active_plugin_host() is active_host()
        reset()
        assert _active_plugin_host() is None
    finally:
        host.dispose()
