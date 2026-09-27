"""A recap is a summary of a *finished* conversation.

The bug: recap only checked whether the parent turn was running, so a session
that had handed its work off to background sub-agents looked idle. Thirty
seconds later it spent an LLM call restating what was already on screen — and
the summary landed in the chat instead of the sub-agent's result reaching the
agent, so the finding was visible to the user and invisible to the model. From
the user's side that reads as "the sub-agent finished and the notification never
arrived", when the notification had worked fine all along.

The gate has to hold at three points, because each one has its own race: when
the timer is armed, when it fires, and after the LLM call is already in flight.
"""

from __future__ import annotations

import pytest

from vtx.tui.app import Vtx
from vtx.tui.goal_agents import REGISTRY
from vtx.tui.recap import RecapMixin


@pytest.fixture
def clean_registry():
    REGISTRY.clear()
    yield
    REGISTRY.clear()


def _start(call_id: str, label: str = "Rate the VTX codebase") -> None:
    REGISTRY.record(call_id, {"kind": "subagent_start", "subagent": "subagent", "label": label})


def _end(call_id: str) -> None:
    REGISTRY.record(call_id, {"kind": "subagent_end", "subagent": "subagent", "turns": 3})


class TestRecapGating:
    def test_subagents_in_flight_is_false_for_an_empty_registry(self) -> None:
        REGISTRY.clear()
        assert RecapMixin._subagents_in_flight(object()) is False

    def test_a_queued_subagent_counts_as_in_flight(self) -> None:
        REGISTRY.clear()
        REGISTRY.record("q", {"kind": "subagent_queued", "subagent": "subagent"})
        assert RecapMixin._subagents_in_flight(object()) is True

    def test_a_finished_subagent_does_not_count(self) -> None:
        REGISTRY.clear()
        _start("a")
        _end("a")
        assert RecapMixin._subagents_in_flight(object()) is False

    @pytest.mark.asyncio
    async def test_recap_timer_is_not_armed_while_a_subagent_runs(
        self, tmp_path, clean_registry
    ) -> None:
        app = Vtx(cwd=str(tmp_path))
        async with app.run_test(size=(100, 30)):
            _start("a")
            app._arm_recap_timer()
            assert app._recap_timer is None, "a live sub-agent must suppress the recap"

            # It is deferred, not lost: once everything lands it arms again.
            _end("a")
            app._arm_recap_timer()
            assert app._recap_timer is not None

    @pytest.mark.asyncio
    async def test_idle_timer_does_not_fire_a_recap_mid_fanout(
        self, tmp_path, clean_registry
    ) -> None:
        """The timer can fire after a sub-agent is dispatched inside the window."""
        app = Vtx(cwd=str(tmp_path))
        drafted: list[str] = []

        async def _fake_generate(reason: str) -> None:
            drafted.append(reason)

        async with app.run_test(size=(100, 30)):
            app._recap_timer = app.set_timer(0.05, app._on_recap_idle)
            # The sub-agent is dispatched *after* the timer was armed.
            _start("a")
            app._generate_and_show_recap = _fake_generate  # type: ignore[method-assign]

            import asyncio

            await asyncio.sleep(0.4)
            assert drafted == [], "recap fired while a sub-agent was still working"

    @pytest.mark.asyncio
    async def test_generation_aborts_if_a_turn_starts_mid_call(
        self, tmp_path, clean_registry
    ) -> None:
        """The third gate: a turn can start after the LLM call is already out."""
        app = Vtx(cwd=str(tmp_path))
        calls: list[object] = []

        class _Ctx:
            def __init__(self) -> None:
                self.messages = ["m"]
                self.broader_context = ""

        app._build_session_recap_context = lambda: (_Ctx(), "key")  # type: ignore[method-assign]

        async def _generate(context, provider):  # pragma: no cover - must not run
            calls.append(context)
            return "recap"

        app._runtime.provider = object()  # type: ignore[attr-defined]
        import vtx.core.recap as recap_mod

        original = recap_mod.generate_recap
        recap_mod.generate_recap = _generate  # type: ignore[assignment]
        try:
            # A sub-agent lands while the recap is deciding whether to run.
            _start("a")
            app._is_running = False
            await app._generate_and_show_recap("idle")
            assert calls == [], "a recap was generated for a session still working"
        finally:
            recap_mod.generate_recap = original  # type: ignore[assignment]

    @pytest.mark.asyncio
    async def test_manual_recap_still_works_with_a_subagent_running(
        self, tmp_path, clean_registry
    ) -> None:
        """An explicit /recap is the user asking for it; do not second-guess."""
        app = Vtx(cwd=str(tmp_path))
        async with app.run_test(size=(100, 30)):
            _start("a")
            app._handle_recap_command()
            # No "cannot recap" refusal: the worker was scheduled.
            assert app._recap_worker is not None


class TestWakeupPreemptsRecap:
    @pytest.mark.asyncio
    async def test_a_wakeup_dismisses_a_pending_recap(self, tmp_path, clean_registry) -> None:
        app = Vtx(cwd=str(tmp_path))
        started: list[str] = []

        async def _fake_run(prompt: str, images=None) -> None:
            started.append(prompt)

        app._run_agent = _fake_run  # type: ignore[method-assign]

        async with app.run_test(size=(100, 30)) as pilot:
            from datetime import UTC, datetime

            from vtx.ai.agent.background import BackgroundTaskRecord

            # Simulate the recap being armed and a worker in flight.
            app._recap_timer = app.set_timer(30.0, app._on_recap_idle)

            record = BackgroundTaskRecord(
                task_id="bg_test",
                description="Rate the VTX codebase",
                prompt="p",
                subagent_type="subagent",
                model=None,
                parent_session_id=None,
                created_at=datetime.now(UTC),
                status="completed",
            )
            app._on_background_task_settled(record)
            await pilot.pause()

            assert started, "the wake-up should have run"
            # The recap timer is gone: a new turn is starting, so a recap of
            # the old conversation is the wrong thing to be drafting.
            assert app._recap_timer is None

    @pytest.mark.asyncio
    async def test_run_agent_clears_running_flag_even_if_it_raises(
        self, tmp_path, clean_registry
    ) -> None:
        """Every gate in the app reads _is_running; it must not get stuck True."""
        app = Vtx(cwd=str(tmp_path))
        async with app.run_test(size=(100, 30)):
            app._is_running = True

            async def _boom(prompt: str, images=None) -> None:
                raise RuntimeError("provider exploded")

            app._run_agent_inner = _boom  # type: ignore[method-assign]
            with pytest.raises(RuntimeError):
                await app._run_agent("go")
            assert app._is_running is False
