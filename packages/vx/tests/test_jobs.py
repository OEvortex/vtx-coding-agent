"""Background jobs: a name to poll, a buffer to read once, a group to kill."""

import asyncio

import pytest
from vx.jobs import Job, JobRegistry, _shell_code


def make_job(status: str = "running", exit_code: int | None = None) -> Job:
    return Job(
        id="j1", command="sleep 1", proc=None, group=None, status=status, exit_code=exit_code
    )


def test_signal_death_reads_as_the_shell_reports_it():
    assert _shell_code(0) == 0
    assert _shell_code(3) == 3
    assert _shell_code(-9) == 137
    assert _shell_code(None) is None


class TestBuffer:
    def test_partial_lines_are_completed_not_repeated(self):
        job = make_job()
        job.write("out", "hel")
        job.write("out", "lo\nwor")
        job.write("out", "ld\n")
        assert list(job.lines) == ["hello", "world"]

    def test_a_last_line_without_a_newline_still_counts(self):
        job = make_job()
        job.write("out", "no newline")
        job.flush()
        assert list(job.lines) == ["no newline"]

    def test_streams_keep_their_own_tail(self):
        job = make_job()
        job.write("out", "a")
        job.write("err", "b")
        job.write("out", "\n")
        job.write("err", "\n")
        assert list(job.lines) == ["a", "b"]

    def test_a_job_is_read_once(self):
        job = make_job()
        job.write("out", "one\ntwo\n")
        assert job.unread() == 2
        assert job.take_unread() == ["one", "two"]
        assert job.unread() == 0
        assert job.take_unread() == []

    def test_reading_drops_what_the_buffer_has_forgotten(self):
        job = make_job()
        job.write("out", "".join(f"{i}\n" for i in range(10)))
        assert job.take_unread()[-1] == "9"


class TestLabels:
    def test_running_shows_only_time(self):
        job = make_job()
        assert job.label().endswith("s")
        assert job.state_text().startswith("running · ")

    def test_failure_shows_the_code(self):
        job = make_job(status="failed", exit_code=2)
        assert job.label().startswith("2 · ")
        assert job.state_text().startswith("exit 2 · ")

    def test_success_reads_as_exited(self):
        job = make_job(status="done", exit_code=0)
        assert job.state_text().startswith("exited 0 · ")


class TestRegistry:
    @pytest.mark.asyncio
    async def test_start_names_the_job_and_reads_its_output(self):
        registry = JobRegistry()
        job = await registry.start("printf 'hello\\n'")
        assert job is not None
        assert job.id == "j1"
        assert registry.get("j1") is job
        await registry.wait_for(job, 5)
        assert job.take_unread() == ["hello"]

    @pytest.mark.asyncio
    async def test_a_command_that_exits_is_done_not_running(self):
        registry = JobRegistry()
        job = await registry.start("exit 3")
        assert job is not None
        await registry.wait_for(job, 5)
        assert job.status == "failed"
        assert job.exit_code == 3

    @pytest.mark.asyncio
    async def test_wait_returns_when_output_arrives(self):
        registry = JobRegistry()
        job = await registry.start("sleep 0.2; echo late")
        assert job is not None
        assert await registry.wait_for(job, 5) is True
        assert "late" in job.take_unread()

    @pytest.mark.asyncio
    async def test_wait_gives_up_and_says_nothing_new(self):
        registry = JobRegistry()
        job = await registry.start("sleep 5")
        assert job is not None
        assert await registry.wait_for(job, 0.2) is False
        await registry.kill_all()

    @pytest.mark.asyncio
    async def test_kill_stops_the_group_and_records_it(self):
        registry = JobRegistry()
        job = await registry.start("while true; do echo tick; sleep 0.05; done")
        assert job is not None
        await asyncio.sleep(0.2)
        await registry.kill(job)
        assert job.status == "stopped"
        assert job.exit_code == 137
        assert any("terminated" in line for line in job.lines)

    @pytest.mark.asyncio
    async def test_kill_all_leaves_nothing_running(self):
        registry = JobRegistry()
        await registry.start("sleep 5")
        await registry.start("sleep 5")
        assert len(registry.running()) == 2
        await registry.kill_all()
        assert registry.running() == []

    @pytest.mark.asyncio
    async def test_a_killed_job_is_not_reported_twice(self):
        registry = JobRegistry()
        job = await registry.start("sleep 5")
        assert job is not None
        await registry.kill(job)
        terminated = sum("[vx] j1 terminated" in line for line in job.lines)
        await registry.kill(job)
        assert sum("[vx] j1 terminated" in line for line in job.lines) == terminated
