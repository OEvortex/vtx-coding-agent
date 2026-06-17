"""Tests for the built-in ``Task`` tool.

These tests cover the public surface of that tool: param
validation, sub-agent resolution, the parent-context handoff via
``vtx.dispatcher``, and the LLM-facing result contract (final text
only, no metadata leak, silent truncation).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

import vtx.tools.task as _mod
from vtx.tools.task import (
    MAX_RESULT_CHARS,
    SubagentSpec,
    TaskParams,
    TaskTool,
    _format_tokens,
    _format_transcript,
    _resolve_subagent_spec,
)

# ---------------------------------------------------------------------------
# Pydantic params
# ---------------------------------------------------------------------------


class TestTaskParamsValidation:
    def test_minimal(self):
        p = TaskParams(description="Find the auth bug", prompt="Look at the login flow.")
        assert p.subagent_type == "general-purpose"
        assert p.model is None

    def test_all_fields(self):
        p = TaskParams(
            description="x" * 5, prompt="y" * 5, subagent_type="Explore", model="gpt-5.5"
        )
        assert p.subagent_type == "Explore"
        assert p.model == "gpt-5.5"

    def test_description_required(self):
        with pytest.raises(ValidationError):
            TaskParams(description="", prompt="x")

    def test_description_max_length(self):
        TaskParams(description="x" * 128, prompt="y")
        with pytest.raises(ValidationError):
            TaskParams(description="x" * 129, prompt="y")

    def test_prompt_required(self):
        with pytest.raises(ValidationError):
            TaskParams(description="x", prompt="")

    def test_subagent_type_default(self):
        p = TaskParams(description="x", prompt="y")
        assert p.subagent_type == "general-purpose"


# ---------------------------------------------------------------------------
# Sub-agent resolution
# ---------------------------------------------------------------------------


class TestResolveSubagentSpec:
    def test_user_defined_agent_wins(self):
        from vtx.agents import AgentDef, AgentRegistry, LoadedAgent

        loaded = LoadedAgent(
            definition=AgentDef(name="reviewer", description="Custom reviewer"), path=Path("/r.py")
        )
        reg = AgentRegistry()
        reg.agents = [loaded]

        spec = _resolve_subagent_spec("reviewer", reg)
        assert spec.name == "reviewer"
        assert spec.description == "Custom reviewer"

    def test_unknown_type_falls_back_to_general_purpose(self):
        spec = _resolve_subagent_spec("does-not-exist", None)
        assert spec.name == "general-purpose"

    def test_empty_type_falls_back_to_general_purpose(self):
        spec = _resolve_subagent_spec("", None)
        assert spec.name == "general-purpose"

    def test_no_registry_falls_back_to_preset(self):
        spec = _resolve_subagent_spec("Plan", None)
        assert spec.name == "Plan"

    def test_preset_used_when_registry_empty(self):
        spec = _resolve_subagent_spec("Explore", None)
        assert spec.name == "Explore"


# ---------------------------------------------------------------------------
# Dispatcher context (the vtx platform feature the extension reads)
# ---------------------------------------------------------------------------


class TestParentContext:
    def teardown_method(self):
        from vtx.dispatcher import set_context

        set_context(None)

    def test_default_is_none(self):
        from vtx.dispatcher import get_context, set_context

        set_context(None)
        assert get_context() is None

    def test_set_and_get(self):
        from vtx.dispatcher import DispatcherContext, get_context, set_context

        ctx = DispatcherContext(
            provider=object(),
            model="m",
            model_provider="p",
            base_url=None,
            thinking_level="high",
            agent_registry=None,
            cwd=".",
        )
        set_context(ctx)
        assert get_context() is ctx

    def test_set_none_clears(self):
        from vtx.dispatcher import DispatcherContext, get_context, set_context

        set_context(
            DispatcherContext(
                provider=object(),
                model="m",
                model_provider=None,
                base_url=None,
                thinking_level=None,
                agent_registry=None,
                cwd=".",
            )
        )
        set_context(None)
        assert get_context() is None


# ---------------------------------------------------------------------------
# Synchronous execution paths
# ---------------------------------------------------------------------------


class _FakeProvider:
    """Minimal stand-in for ``BaseProvider`` so we don't need a real LLM."""

    def __init__(self) -> None:
        from vtx.llm.base import ProviderConfig

        self.config = ProviderConfig(model="m", session_id="")


@dataclass
class _FakeRunResult:
    """Stand-in for :class:`SubagentRunResult` so tests don't need a real run."""

    final_text: str = ""
    turns: int = 0
    usage: Any = None
    stop_reason: Any = None
    transcript: list[str] = field(default_factory=list)
    error: str | None = None
    session_id: str | None = None

    def __post_init__(self):
        if self.usage is None:
            from vtx.core.types import Usage

            self.usage = Usage()
        if self.stop_reason is None:
            from vtx.core.types import StopReason

            self.stop_reason = StopReason.STOP


def _install_dispatcher_ctx() -> None:
    from vtx.dispatcher import DispatcherContext, set_context

    set_context(
        DispatcherContext(
            provider=_FakeProvider(),
            model="m",
            model_provider="fake",
            base_url=None,
            thinking_level="high",
            agent_registry=None,
            cwd=".",
        )
    )


class TestTaskToolExecute:
    def teardown_method(self):
        from vtx.dispatcher import set_context

        set_context(None)

    def test_no_dispatcher_context(self):
        from vtx.dispatcher import set_context

        # Force the no-context branch even if another test leaked one.
        set_context(None)
        tool = TaskTool()
        result = asyncio.run(tool.execute(TaskParams(description="x", prompt="y")))
        assert result.success is False
        assert (
            "parent context" in (result.result or "").lower()
            or "dispatcher" in (result.result or "").lower()
        )

    def test_execute_with_subagent_failure(self, monkeypatch):
        """When the sub-agent fails, the tool returns a failure result
        with the error in ``ui_summary`` and a transcript hint in
        ``ui_details``."""

        _install_dispatcher_ctx()

        async def _boom(*args, **kwargs):
            return _FakeRunResult(final_text="", error="upstream 500", transcript=["  → bash"])

        monkeypatch.setattr(_mod, "_run_subagent", _boom)

        tool = TaskTool()
        result = asyncio.run(tool.execute(TaskParams(description="x", prompt="y")))
        assert result.success is False
        assert "upstream 500" in (result.result or "")
        assert "→ bash" in (result.ui_details or "")

    def test_execute_returns_final_text(self, monkeypatch):
        from vtx.core.types import StopReason, Usage

        _install_dispatcher_ctx()

        async def _ok(*args, **kwargs):
            return _FakeRunResult(
                final_text="Here is the answer.",
                turns=3,
                usage=Usage(input_tokens=10, output_tokens=20),
                stop_reason=StopReason.STOP,
                session_id="abc12345",
            )

        monkeypatch.setattr(_mod, "_run_subagent", _ok)

        tool = TaskTool()
        result = asyncio.run(
            tool.execute(TaskParams(description="x", prompt="y", subagent_type="Explore"))
        )
        assert result.success is True
        assert result.result == "Here is the answer."
        assert "3 turns" in (result.ui_summary or "")
        assert "Explore" in (result.ui_details or "")
        assert "abc12345" in (result.ui_details or "")

    def test_execute_truncates_long_text_silently(self, monkeypatch):
        """A sub-agent that returns >MAX_RESULT_CHARS gets silently
        truncated to MAX_RESULT_CHARS in the LLM-facing result. The
        truncation marker must NOT appear in ``result``."""
        _install_dispatcher_ctx()

        huge = "A" * (MAX_RESULT_CHARS + 1000)

        async def _ok(*args, **kwargs):
            return _FakeRunResult(final_text=huge, turns=1, session_id="s")

        monkeypatch.setattr(_mod, "_run_subagent", _ok)

        tool = TaskTool()
        result = asyncio.run(tool.execute(TaskParams(description="x", prompt="y")))
        assert result.success is True
        assert result.result is not None
        assert len(result.result) == MAX_RESULT_CHARS
        assert "truncated" not in (result.result or "")

    def test_execute_does_not_leak_metadata_to_llm(self, monkeypatch):
        from vtx.core.types import StopReason, Usage

        _install_dispatcher_ctx()

        async def _ok(*args, **kwargs):
            return _FakeRunResult(
                final_text="Here is the answer.",
                turns=3,
                usage=Usage(input_tokens=10, output_tokens=20),
                stop_reason=StopReason.STOP,
                session_id="abc12345",
            )

        monkeypatch.setattr(_mod, "_run_subagent", _ok)

        tool = TaskTool()
        result = asyncio.run(
            tool.execute(TaskParams(description="x", prompt="y", subagent_type="Explore"))
        )
        assert result.success is True
        assert result.result == "Here is the answer."
        forbidden = (
            "turns",
            "tokens",
            "abc12345",
            "Explore",
            "session",
            "model",
            "general-purpose",
        )
        for needle in forbidden:
            assert needle.lower() not in (result.result or "").lower(), (
                f"result leaked {needle!r}: {result.result!r}"
            )

    def test_execute_progress_callback_runs(self, monkeypatch):
        from vtx.dispatcher import DispatcherContext, set_context

        seen: list[tuple[str, dict]] = []

        async def _ok(*args, **kwargs):
            pc = kwargs.get("progress_callback")
            tool_call_id = kwargs.get("tool_call_id")
            if pc is not None and tool_call_id is not None:
                pc(tool_call_id, {"kind": "subagent_start", "subagent": "x"})
            return _FakeRunResult(final_text="done", turns=1, session_id="s")

        monkeypatch.setattr(_mod, "_run_subagent", _ok)
        ctx = DispatcherContext(
            provider=_FakeProvider(),
            model="m",
            model_provider="fake",
            base_url=None,
            thinking_level="high",
            agent_registry=None,
            cwd=".",
            progress_callback=lambda tid, ev: seen.append((tid, ev)),
        )
        set_context(ctx)

        tool = TaskTool()
        asyncio.run(tool.execute(TaskParams(description="x", prompt="y")))
        assert any(ev.get("kind") == "subagent_start" for _, ev in seen)

    def test_run_subagent_concatenates_all_text_parts_in_final_turn(self, monkeypatch):
        """``_run_subagent`` must return the FULL final text the model
        generated after its last tool call — every ``TextContent``
        part in the final turn concatenated, with ``ThinkingContent``
        filtered out, and earlier mid-run turns' text discarded.
        """
        from vtx.core.types import (
            AssistantMessage,
            StopReason,
            TextContent,
            ThinkingContent,
            Usage,
        )
        from vtx.dispatcher import DispatcherContext

        _install_dispatcher_ctx()

        final_msg = AssistantMessage(
            content=[
                TextContent(text="Looking at the repo, "),
                ThinkingContent(thinking="(reasoning)"),
                TextContent(text="the auth bug is in login.py:42."),
            ],
            usage=Usage(input_tokens=10, output_tokens=20),
            stop_reason=StopReason.STOP,
        )

        class _FakeSubAgent:
            def __init__(self, *args, **kwargs):
                pass

            async def run(self, *args, **kwargs):
                from vtx.events import AgentEndEvent, TurnEndEvent

                mid_turn = TurnEndEvent(
                    turn=1,
                    stop_reason=StopReason.TOOL_USE,
                    assistant_message=AssistantMessage(
                        content=[TextContent(text="Let me check.")],
                        stop_reason=StopReason.TOOL_USE,
                    ),
                )
                final_turn = TurnEndEvent(
                    turn=2, stop_reason=StopReason.STOP, assistant_message=final_msg
                )
                yield mid_turn
                yield final_turn
                yield AgentEndEvent(stop_reason=StopReason.STOP)

        class _StubSession:
            id = "sub-session-12345"

        # Drive the real ``_run_subagent`` with stubbed session /
        # provider / system-prompt / tool-list builders.
        monkeypatch.setattr(_mod, "_build_subagent_tool_list", lambda *a, **kw: [])
        monkeypatch.setattr(_mod, "_build_subagent_system_prompt", lambda *a, **kw: "system")
        monkeypatch.setattr(_mod, "_create_subagent_session", lambda *a, **kw: _StubSession())
        monkeypatch.setattr(_mod, "_resolve_api_and_base_url", lambda *a, **kw: ("openai", None))
        monkeypatch.setattr("vtx.runtime.create_provider", lambda *a, **kw: _FakeProvider())
        monkeypatch.setattr("vtx.loop.Agent", lambda *a, **kw: _FakeSubAgent(*a, **kw))

        real_ctx = DispatcherContext(
            provider=_FakeProvider(),
            model="m",
            model_provider="fake",
            base_url=None,
            thinking_level="high",
            agent_registry=None,
            cwd=".",
        )
        spec = SubagentSpec(name="general-purpose", description="x", max_turns=10)

        async def main():
            return await _mod._run_subagent(
                parent_ctx=real_ctx,
                spec=spec,
                prompt="p",
                cancel_event=None,
                model_override=None,
                progress_callback=None,
                tool_call_id="t1",
            )

        sub_result = asyncio.run(main())
        assert sub_result.final_text == ("Looking at the repo, the auth bug is in login.py:42.")
        assert "Let me check." not in sub_result.final_text
        assert sub_result.turns == 2
        assert sub_result.stop_reason == StopReason.STOP


# ---------------------------------------------------------------------------
# Sub-agent system prompt: final-answer directive
# ---------------------------------------------------------------------------


class TestSubagentSystemPrompt:
    def test_directive_is_appended_to_base_prompt(self):
        from vtx.dispatcher import DispatcherContext

        ctx = DispatcherContext(
            provider=object(),
            model="m",
            model_provider="p",
            base_url=None,
            thinking_level="high",
            agent_registry=None,
            cwd=".",
            system_prompt="You are the base identity.",
        )
        spec = SubagentSpec(name="general-purpose", description="x")
        out = _mod._build_subagent_system_prompt(ctx, spec, tools=[])
        assert "You are the base identity." in out
        assert "isolated sub-agent" in out
        assert "Return ONLY your final answer" in out

    def test_directive_added_when_spec_replaces_base(self):
        from vtx.dispatcher import DispatcherContext

        ctx = DispatcherContext(
            provider=object(),
            model="m",
            model_provider="p",
            base_url=None,
            thinking_level="high",
            agent_registry=None,
            cwd=".",
            system_prompt="You are the base identity.",
        )
        spec = SubagentSpec(
            name="custom",
            description="x",
            instructions="Custom instructions only.",
            instructions_mode="replace",
        )
        out = _mod._build_subagent_system_prompt(ctx, spec, tools=[])
        assert "You are the base identity." not in out
        assert "Custom instructions only." in out
        assert "Return ONLY your final answer" in out

    def test_spec_instructions_preserved_alongside_directive(self):
        from vtx.dispatcher import DispatcherContext

        ctx = DispatcherContext(
            provider=object(),
            model="m",
            model_provider="p",
            base_url=None,
            thinking_level="high",
            agent_registry=None,
            cwd=".",
            system_prompt="base",
        )
        spec = SubagentSpec(
            name="Explore",
            description="x",
            instructions="You are a read-only exploration agent.",
            instructions_mode="append",
        )
        out = _mod._build_subagent_system_prompt(ctx, spec, tools=[])
        assert "base" in out
        assert "You are a read-only exploration agent." in out
        assert "Return ONLY your final answer" in out
        directive_pos = out.index("Return ONLY your final answer")
        spec_pos = out.index("You are a read-only exploration agent.")
        base_pos = out.index("base")
        assert base_pos < spec_pos < directive_pos


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


class TestFormatTokens:
    def test_small(self):
        assert _format_tokens(0) == "0t"
        assert _format_tokens(999) == "999t"

    def test_thousands(self):
        assert _format_tokens(1500) == "1.5k"

    def test_ten_thousands(self):
        assert _format_tokens(15_000) == "15k"


class TestFormatTranscript:
    def test_empty(self):
        out = _format_transcript([])
        assert "no tool calls" in out

    def test_with_header(self):
        out = _format_transcript(["→ read"], header="sub-agent: x")
        assert out.startswith("sub-agent: x")

    def test_truncates_long_transcript(self):
        transcript = [f"→ tool_{i}" for i in range(500)]
        out = _format_transcript(transcript)
        assert "more" in out
        assert "tool_0" in out
        assert "tool_499" not in out
