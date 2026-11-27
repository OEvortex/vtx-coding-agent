"""bash actions and agy-shaped output: status, list, send_input, kill."""

import asyncio
import time

import pytest
import pytest_asyncio
from vx.jobs import REGISTRY
from vx.notifications import NOTICES
from vx.tools.bash import BashParams, BashTool


@pytest_asyncio.fixture(autouse=True)
async def clean_state():
    NOTICES.clear()
    REGISTRY.jobs.clear()
    REGISTRY._next = 0
    yield
    await REGISTRY.kill_all()
    REGISTRY.jobs.clear()
    NOTICES.clear()


class TestOutputShape:
    @pytest.mark.asyncio
    async def test_a_successful_run_reads_like_agy(self):
        result = await BashTool().execute(BashParams(command="printf hello"))
        assert result.success
        assert (result.result or "").startswith("The command exited with code 0.\nOutput:\n")
        assert "hello" in (result.result or "")

    @pytest.mark.asyncio
    async def test_a_failure_carries_the_code(self):
        result = await BashTool().execute(BashParams(command="exit 4"))
        assert not result.success
        assert "exited with code 4" in (result.result or "")
        assert "Exit code 4" in (result.ui_summary or "")

    @pytest.mark.asyncio
    async def test_stderr_is_kept_and_labelled(self):
        result = await BashTool().execute(BashParams(command="echo out; echo err >&2"))
        assert "[stderr]" in (result.result or "")

    def test_long_output_is_cut_with_agy_marker(self):
        from vx.tools.bash import _agy_body

        text = "\n".join(f"line{i}" for i in range(5000))
        body, cut = _agy_body(text)
        assert cut
        assert body.startswith("<truncated ")
        assert "line4999" in body


class TestActions:
    @pytest.mark.asyncio
    async def test_list_is_empty_before_anything_runs(self):
        result = await BashTool().execute(BashParams(action="list"))
        assert result.success
        assert "No running tasks." in (result.result or "")

    @pytest.mark.asyncio
    async def test_an_unknown_task_says_so_and_lists_the_known_ones(self):
        result = await BashTool().execute(BashParams(action="status", task_id="j9"))
        assert not result.success
        assert "No task" in (result.result or "")

    @pytest.mark.asyncio
    async def test_background_start_returns_a_task_and_a_log(self):
        result = await BashTool().execute(BashParams(command="sleep 5", background=True))
        assert result.success
        assert "Started j1" in (result.result or "")
        assert "Log:" in (result.result or "")

    @pytest.mark.asyncio
    async def test_status_reports_agy_shape(self):
        await BashTool().execute(BashParams(command="sleep 5", background=True))
        result = await BashTool().execute(BashParams(action="status", task_id="j1"))
        text = result.result or ""
        assert text.startswith("Task: j1")
        assert "Status: RUNNING" in text
        assert "Log: " in text

    @pytest.mark.asyncio
    async def test_list_reports_the_running_task(self):
        await BashTool().execute(BashParams(command="sleep 5", background=True))
        result = await BashTool().execute(BashParams(action="list"))
        assert "Task: j1" in (result.result or "")

    @pytest.mark.asyncio
    async def test_kill_uses_agy_wording_and_stops_it(self):
        await BashTool().execute(BashParams(command="sleep 30", background=True))
        result = await BashTool().execute(BashParams(action="kill", task_id="j1"))
        assert 'Task "j1" cancelled.' in (result.result or "")
        assert REGISTRY.get("j1").status == "stopped"

    @pytest.mark.asyncio
    async def test_send_input_reaches_a_task_waiting_on_stdin(self):
        await BashTool().execute(
            BashParams(command="read answer; echo got:$answer", background=True)
        )
        await asyncio.sleep(0.3)
        result = await BashTool().execute(
            BashParams(action="send_input", task_id="j1", input="yes")
        )
        assert result.success
        assert 'Input sent to task "j1".' in (result.result or "")
        await asyncio.sleep(0.3)
        read = await BashTool().execute(BashParams(action="status", task_id="j1"))
        assert "got:yes" in (read.result or "")

    @pytest.mark.asyncio
    async def test_send_input_needs_text(self):
        await BashTool().execute(BashParams(command="sleep 5", background=True))
        result = await BashTool().execute(BashParams(action="send_input", task_id="j1"))
        assert not result.success
        assert "needs input" in (result.result or "")

    @pytest.mark.asyncio
    async def test_status_and_kill_actions(self):
        await BashTool().execute(BashParams(command="sleep 5", background=True))
        status = await BashTool().execute(BashParams(action="status", task_id="j1"))
        assert "Task: j1" in (status.result or "")
        killed = await BashTool().execute(BashParams(action="kill", task_id="j1"))
        assert "cancelled" in (killed.result or "")

    @pytest.mark.asyncio
    async def test_an_unknown_action_is_refused(self):
        result = await BashTool().execute(BashParams(action="explode"))
        assert not result.success
        assert "Unknown action" in (result.result or "")


class TestPromotion:
    @pytest.mark.asyncio
    async def test_a_command_that_outlives_the_wait_becomes_a_task(self):
        started = time.monotonic()
        result = await BashTool().execute(BashParams(command="sleep 30", wait_ms=600, timeout=30))
        assert time.monotonic() - started < 5
        assert result.success
        assert "still running after 1s" in (result.result or "")
        assert "Task: j1" in (result.result or "")

    @pytest.mark.asyncio
    async def test_a_command_that_finishes_inside_the_wait_reads_like_a_run(self):
        result = await BashTool().execute(BashParams(command="echo quick", wait_ms=5000))
        assert result.success
        assert (result.result or "").startswith("The command exited with code 0.")
        assert "quick" in (result.result or "")

    @pytest.mark.asyncio
    async def test_a_failure_inside_the_wait_keeps_the_code(self):
        result = await BashTool().execute(BashParams(command="exit 9", wait_ms=5000))
        assert not result.success
        assert "exited with code 9" in (result.result or "")


class TestNotices:
    @pytest.mark.asyncio
    async def test_a_finished_task_is_announced_on_the_next_result(self):
        await BashTool().execute(BashParams(command="echo done-now", background=True))
        await asyncio.sleep(0.5)
        result = await BashTool().execute(BashParams(action="list"))
        text = result.result or ""
        assert '<job-notification job="j1" kind="finished">' in text

    @pytest.mark.asyncio
    async def test_a_prompt_is_announced_once(self):
        await BashTool().execute(
            BashParams(command="printf 'Password: '; read x", background=True)
        )
        await asyncio.sleep(0.5)
        first = await BashTool().execute(BashParams(action="list"))
        assert "input_required" in (first.result or "")
        second = await BashTool().execute(BashParams(action="list"))
        assert "input_required" not in (second.result or "")

    @pytest.mark.asyncio
    async def test_a_still_running_notice_fires_for_a_watched_task(self):
        await BashTool().execute(BashParams(command="sleep 30", background=True, timeout_notify=1))
        await asyncio.sleep(1.4)
        result = await BashTool().execute(BashParams(action="list"))
        assert "still_running" in (result.result or "")


class TestSandboxParam:
    @pytest.mark.asyncio
    async def test_a_sandboxed_command_runs_or_explains_itself(self):
        from vx import sandbox

        support = sandbox.support()
        result = await BashTool().execute(BashParams(command="echo inside", sandbox=True))
        if support.available:
            assert result.success
            assert "inside" in (result.result or "")
            assert "sandbox: bwrap" in (result.result or "")
        else:
            assert not result.success
            assert "not available here" in (result.result or "")


class TestTerminalParam:
    @pytest.mark.asyncio
    async def test_two_calls_share_one_shell(self):
        from vx.terminals import TERMINALS

        tool = BashTool()
        try:
            await tool.execute(BashParams(command="cd /tmp", terminal="t1"))
            second = await tool.execute(BashParams(command="pwd", terminal="t1"))
            if TERMINALS.get("t1") is None:
                pytest.skip("no pty on this platform")
            assert "/tmp" in (second.result or "")
            assert "Terminal: t1" in (second.result or "")
        finally:
            await TERMINALS.close_all()

    @pytest.mark.asyncio
    async def test_a_terminal_can_be_read_and_closed_by_action(self):
        from vx.terminals import TERMINALS

        tool = BashTool()
        try:
            await tool.execute(BashParams(command="echo one", terminal="t1"))
            if TERMINALS.get("t1") is None:
                pytest.skip("no pty on this platform")
            status = await tool.execute(BashParams(action="status", task_id="t1"))
            assert "Terminal: t1" in (status.result or "")
            closed = await tool.execute(BashParams(action="kill", task_id="t1"))
            assert 'Terminal "t1" closed.' in (closed.result or "")
        finally:
            await TERMINALS.close_all()
