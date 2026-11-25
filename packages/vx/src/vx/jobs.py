"""Background commands: a job is a name to poll and a terminal to open.

The model starts one with ``bash(background=True)``, reads it by name with
``bash(job="j1")`` and stops it with ``bash(job="j1", kill=True)``. One ``Job``
object feeds every view -- the panel's terminal list, the drawer's output and
the model's poll -- so there is no second copy of a job's state to disagree
with.

A job is not a transcript row: a dev server runs for hours, and rows that age
the moment they land are history, not work in progress. The panel says it is
running; the drawer shows what it is saying.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from vx.tools.bash import (
    _IS_WINDOWS,
    _STREAM_LIMIT_BYTES,
    _get_env,
    _get_spawn_argv,
    _kill_process_tree,
    _sanitize_output,
)

#: Lines kept per job. A dev server's whole life does not fit, and the tail is
#: what anyone reads; the model gets the same window the drawer draws.
MAX_LINES = 2000
#: How often a poll looks for new output before it gives up waiting.
_POLL_SECONDS = 0.05


def _shell_code(code: int | None) -> int | None:
    """A signal death as the shell reports it: SIGKILL is 137, not -9."""
    if code is None:
        return None
    return 128 - code if code < 0 else code


@dataclass
class Job:
    """One command the session left running in the background."""

    id: str
    command: str
    proc: Any
    group: int | None
    owner: str = ""
    status: str = "running"  # running | done | failed | stopped
    started: float = field(default_factory=time.monotonic)
    ended: float | None = None
    exit_code: int | None = None
    lines: deque[str] = field(default_factory=lambda: deque(maxlen=MAX_LINES))
    #: How many lines have ever been appended, and how many the model has read.
    #: Absolute counters, because the buffer drops the oldest lines.
    total: int = 0
    cursor: int = 0
    #: The tail of whichever stream is mid-line, per stream, so a partial write
    #: is completed rather than printed twice.
    partial: dict[str, str] = field(default_factory=dict)

    # -- what every view asks ------------------------------------------------

    def elapsed(self) -> float:
        return (self.ended if self.ended is not None else time.monotonic()) - self.started

    def label(self) -> str:
        """``12s`` while it runs, ``137 · 12s`` once it is over."""
        seconds = f"{self.elapsed():.0f}s"
        if self.status in ("failed", "stopped"):
            return f"{self.exit_code if self.exit_code is not None else 1} · {seconds}"
        return seconds

    def state_text(self) -> str:
        seconds = f"{self.elapsed():.0f}s"
        if self.status == "running":
            return f"running · {seconds}"
        if self.status == "done":
            return f"exited 0 · {seconds}"
        return f"exit {self.exit_code} · {seconds}"

    def tail(self, count: int) -> list[str]:
        if count <= 0:
            return []
        return list(self.lines)[-count:]

    def unread(self) -> int:
        return max(0, self.total - self.cursor)

    def take_unread(self) -> list[str]:
        """The lines the model has not seen yet, and move its cursor past them."""
        pending = min(self.unread(), len(self.lines))
        chunk = list(self.lines)[-pending:] if pending else []
        self.cursor = self.total
        return chunk

    # -- writing ------------------------------------------------------------

    def write(self, stream: str, text: str) -> None:
        """Append what a stream said, completing any line it left half-written."""
        if not text:
            return
        buf = self.partial.get(stream, "") + text
        parts = buf.split("\n")
        partial = parts.pop()
        for line in parts:
            self.lines.append(line.rstrip("\r"))
            self.total += 1
        self.partial[stream] = partial

    def flush(self) -> None:
        """Keep a last line that never got its newline."""
        for stream, partial in list(self.partial.items()):
            if partial.strip():
                self.lines.append(partial.rstrip("\r"))
                self.total += 1
            self.partial[stream] = ""

    def note(self, text: str) -> None:
        self.lines.append(text)
        self.total += 1


class JobRegistry:
    """Every background command this session started."""

    def __init__(self) -> None:
        self.jobs: dict[str, Job] = {}
        self._next = 0
        self._watchers: dict[str, asyncio.Task[None]] = {}

    # -- reads ---------------------------------------------------------------

    def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id.strip())

    def all(self) -> list[Job]:
        return list(self.jobs.values())

    def running(self) -> list[Job]:
        return [job for job in self.jobs.values() if job.status == "running"]

    def latest_running(self) -> Job | None:
        running = self.running()
        return running[-1] if running else None

    def new_id(self) -> str:
        self._next += 1
        return f"j{self._next}"

    # -- lifecycle -----------------------------------------------------------

    async def start(self, command: str, owner: str = "") -> Job | None:
        """Spawn ``command`` as a job. None when the shell could not be found."""
        job_id = self.new_id()
        spawn_argv = _get_spawn_argv(command)
        if spawn_argv is None:
            return None
        proc = await asyncio.create_subprocess_exec(
            *spawn_argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
            env=_get_env(),
            start_new_session=not _IS_WINDOWS,
            limit=_STREAM_LIMIT_BYTES,
        )
        job = Job(
            id=job_id,
            command=command,
            proc=proc,
            group=None if _IS_WINDOWS else proc.pid,
            owner=owner,
        )
        self.jobs[job_id] = job
        self._watchers[job_id] = asyncio.create_task(self._watch(job))
        return job

    async def _watch(self, job: Job) -> None:
        readers = [
            asyncio.create_task(self._read(job, "out", job.proc.stdout)),
            asyncio.create_task(self._read(job, "err", job.proc.stderr)),
        ]
        try:
            code = await job.proc.wait()
        except asyncio.CancelledError:
            for reader in readers:
                reader.cancel()
            raise
        # The command exited; what still holds its pipes is a child it left
        # behind, and the job is over regardless of that child.
        await asyncio.wait(readers, timeout=0.3)
        for reader in readers:
            if not reader.done():
                reader.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await reader
        job.flush()
        job.exit_code = _shell_code(code)
        job.ended = time.monotonic()
        if job.status != "stopped":
            job.status = "done" if code == 0 else "failed"

    async def _read(self, job: Job, stream: str, reader: Any) -> None:
        if reader is None:
            return
        while True:
            try:
                chunk = await reader.readline()
            except (asyncio.LimitOverrunError, ValueError):
                chunk = await reader.read(1 << 16)
            if not chunk:
                return
            job.write(stream, _sanitize_output(chunk.decode("utf-8", errors="replace")))

    async def kill(self, job: Job) -> None:
        """Stop a job's whole process group and record how it went."""
        if job.status != "running":
            return
        job.status = "stopped"
        job.note("[vx] sent SIGTERM to the process group")
        await _kill_process_tree(job.proc, job.group)
        with contextlib.suppress(Exception):
            await self._settle(job)
        job.flush()
        job.ended = job.ended or time.monotonic()
        if job.exit_code is None:
            job.exit_code = _shell_code(job.proc.returncode) or 143
        job.note(f"[vx] {job.id} terminated · exit {job.exit_code}")

    async def _settle(self, job: Job) -> None:
        """Give the watcher a moment to record the exit it is about to see."""
        watcher = self._watchers.get(job.id)
        if watcher is not None and not watcher.done():
            with contextlib.suppress(asyncio.CancelledError, TimeoutError):
                await asyncio.wait_for(asyncio.shield(watcher), 2.0)

    async def kill_all(self) -> None:
        """Nothing outlives the session: no orphaned servers, no stray builds."""
        for job in self.running():
            await self.kill(job)

    async def wait_for(self, job: Job, seconds: float) -> bool:
        """Block until the job says something new or stops; True when it did."""
        deadline = time.monotonic() + max(0.0, seconds)
        while True:
            if job.unread() or job.status != "running":
                return True
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(_POLL_SECONDS)


#: One registry per process: the panel, the drawer and the tool all read it.
REGISTRY = JobRegistry()
