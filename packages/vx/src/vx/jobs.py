"""Background commands: a name to poll and a terminal to open.

The model starts one with ``bash(background=True)``, reads it by name with
``bash(action="status")`` and stops it with ``bash(action="kill")``. One ``Job``
object feeds every view -- the panel's terminal list, the drawer's output and
the model's poll -- so there is no second copy of a job's state to disagree
with.

A job is not a transcript row: a dev server runs for hours, and rows that age
the moment they land are history, not work in progress. The panel says it is
running; the drawer shows what it is saying.

Alongside the buffer every job also writes a log file, because a status reply is
a window onto a job and the model eventually needs the whole thing -- the same
reason agy keeps ``.system_generated/tasks/task-N.log`` per task.
"""

from __future__ import annotations

import asyncio
import codecs
import contextlib
import tempfile
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from vx import sandbox as sandbox_mod
from vx.notifications import FINISHED, INPUT_REQUIRED, NOTICES, JobNotice, looks_like_a_prompt
from vx.tools.bash import (
    _IS_WINDOWS,
    _STREAM_LIMIT_BYTES,
    _get_env,
    _get_shell,
    _get_spawn_argv,
    _kill_process_tree,
    _sanitize_output,
)

#: Lines kept per job. A dev server's whole life does not fit, and the tail is
#: what anyone reads; the model gets the same window the drawer draws.
MAX_LINES = 2000
#: How often a poll looks for new output before it gives up waiting.
_POLL_SECONDS = 0.05
#: Where a job's full output lands. One file per job, appended as it speaks.
LOG_DIR = Path(tempfile.gettempdir()) / "vx-jobs"


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
    #: Where the full output goes, and the handle writing it.
    log_path: Path | None = None
    _log: Any = None
    #: How this job was started, so a status reply can say what it ran under.
    sandboxed: bool = False
    wait_ms: int = 0
    #: Set once the job has been reported as waiting for input.
    asked_for_input: str | None = None
    #: The raw tail of everything the streams said, for the prompt heuristic.
    _recent: str = ""

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

    def status_word(self) -> str:
        """The status a tool reply prints, in agy's vocabulary."""
        return {
            "running": "RUNNING",
            "done": "COMPLETED",
            "failed": "FAILED",
            "stopped": "STOPPED",
        }.get(self.status, self.status.upper())

    def head(self, count: int) -> list[str]:
        """The first lines ever written, for the head/tail elision."""
        return list(self.lines)[:count]

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

    def _log_write(self, text: str) -> None:
        if self._log is None or not text:
            return
        with contextlib.suppress(Exception):
            self._log.write(text)
            self._log.flush()

    def write(self, stream: str, text: str) -> None:
        """Append what a stream said, completing any line it left half-written."""
        if not text:
            return
        self._log_write(text)
        buf = self.partial.get(stream, "") + text
        parts = buf.split("\n")
        partial = parts.pop()
        for line in parts:
            self.lines.append(line.rstrip("\r"))
            self.total += 1
        self.partial[stream] = partial
        self._recent = (self._recent + text)[-400:]
        self._note_input_request()

    def _note_input_request(self) -> None:
        """One notice, ever, when the job's output turns into a question.

        Scanned over the raw tail rather than over complete lines: a prompt asks
        its question and then waits, so the line carrying it is precisely the
        one that never gets its newline.
        """
        if self.status != "running" or self.asked_for_input is not None:
            return
        hint = looks_like_a_prompt(self._recent)
        if hint is None:
            return
        self.asked_for_input = hint
        NOTICES.post(
            JobNotice(
                kind=INPUT_REQUIRED,
                job_id=self.id,
                text=(
                    f"Task {self.id} is waiting for input: {hint}\n"
                    f'Answer it with bash(action="send_input", task_id="{self.id}", '
                    f'input="..."), or stop it with action="kill".'
                ),
            )
        )

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
        self._log_write(text + "\n")

    def open_log(self, path: Path) -> None:
        self.log_path = path
        with contextlib.suppress(Exception):
            path.parent.mkdir(parents=True, exist_ok=True)
            self._log = path.open("w", encoding="utf-8", errors="replace")

    def close_log(self) -> None:
        if self._log is not None:
            with contextlib.suppress(Exception):
                self._log.close()
            self._log = None

    # -- input --------------------------------------------------------------

    def can_take_input(self) -> bool:
        return self.status == "running" and getattr(self.proc, "stdin", None) is not None

    def send_input(self, text: str) -> bool:
        """Write raw keystrokes to a running command's stdin. True if written."""
        if not self.can_take_input():
            return False
        if not text.endswith("\n"):
            text += "\n"
        try:
            self.proc.stdin.write(text.encode())
            self.note(f"[vx] input sent: {text.rstrip()}")
            return True
        except Exception:
            return False


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

    async def start(
        self, command: str, owner: str = "", *, sandbox: bool = False, wait_ms: int = 0
    ) -> Job | None:
        """Spawn ``command`` as a job. None when the shell could not be found."""
        job_id = self.new_id()
        argv = self._argv(command, sandbox=sandbox)
        if argv is None:
            return None
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.PIPE,
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
            sandboxed=sandbox,
            wait_ms=wait_ms,
        )
        job.open_log(LOG_DIR / f"{job_id}.log")
        self.jobs[job_id] = job
        self._watchers[job_id] = asyncio.create_task(self._watch(job))
        return job

    @staticmethod
    def _argv(command: str, *, sandbox: bool) -> list[str] | None:
        shell = _get_shell()
        if shell is None:
            return None
        if sandbox:
            return sandbox_mod.build_argv(command, Path.cwd(), shell=shell, network=False)
        return _get_spawn_argv(command)

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
            NOTICES.post(
                JobNotice(
                    kind=FINISHED,
                    job_id=job.id,
                    text=(
                        f"Task {job.id} finished with status {job.status_word()} "
                        f"(exit {job.exit_code}) after {job.elapsed():.0f}s: {job.command}\n"
                        f"Log: {job.log_path}\n" + "\n".join(job.tail(5))
                    ).rstrip(),
                )
            )
        # The footer is for the log file: a status reply is about the command's
        # own output, not about vx narrating its bookkeeping.
        job._log_write(f"\n[vx] {job.id} {job.status} · exit {job.exit_code}\n")
        job.close_log()

    async def _read(self, job: Job, stream: str, reader: Any) -> None:
        if reader is None:
            return
        # ``read`` rather than ``readline``: a prompt is a line that never gets
        # its newline, and that is exactly the line the input-needed notice is
        # about. Decoding is incremental so a split multibyte character is not
        # turned into a replacement glyph mid-stream.
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        while True:
            try:
                chunk = await reader.read(4096)
            except (asyncio.LimitOverrunError, ValueError):
                chunk = await reader.read(1 << 16)
            if not chunk:
                tail = decoder.decode(b"", True)
                if tail:
                    job.write(stream, _sanitize_output(tail))
                return
            job.write(stream, _sanitize_output(decoder.decode(chunk)))

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
        job.close_log()

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
