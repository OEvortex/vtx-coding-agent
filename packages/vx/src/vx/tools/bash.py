"""Bash tool for vx — run shell commands, and manage the ones still running.

Two ways to run something:

* ``bash(command=...)`` — one persistent shell per agent, blocked on until the
  command exits or ``timeout`` runs out. Because the shell is the same one every
  time, ``cd``, exports and an activated venv survive between calls.
* ``bash(command=..., background=True)`` — a job immediately, for dev servers and
  watchers that must not hold a shell.

``wait_ms`` stops waiting after N ms and hands back the shell's id.

And four actions on the jobs and terminals those produced, in agy's vocabulary:
``status``, ``list``, ``send_input``, ``kill``.

Everything a job has to say before it is asked — finished, stalled on a prompt,
or still running — arrives as a ``<job-notification>`` block at the head of the
next tool result.
"""

import asyncio
import contextlib
import itertools
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from ctx.agent.tools.base import BaseTool
from ctx.protocol.types import ToolResult
from pydantic import BaseModel, Field

from vx.notifications import drain_text

DEFAULT_TIMEOUT = 180
MAX_OUTPUT_BYTES = 50 * 1024
MAX_OUTPUT_LINES = 2000
_SUBPROCESS_DRAIN_TIMEOUT_SECONDS = 1.0
#: How long to keep reading after the command itself has exited. A daemon left
#: in the background inherits the pipe and never closes it; waiting for that is
#: waiting for a command the user already finished.
_PIPE_GRACE_SECONDS = 0.4
#: A single line can be a minified blob or a base64 image, so the reader's line
#: limit has to be well past the default 64 KiB.
_STREAM_LIMIT_BYTES = 8 * 1024 * 1024
#: How often to look for the child's exit. Cheap, and it does not depend on the
#: pipes, which is the whole point.
_EXIT_POLL_SECONDS = 0.02

_IS_WINDOWS: bool = sys.platform == "win32"
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]|\x1b\][^\x07]*\x07|\x1b[()][AB012]")


def _get_env() -> dict[str, str]:
    return {
        **os.environ,
        "CI": "true",
        "TERM": "xterm-256color",
        "FORCE_COLOR": "1",
        "CLICOLOR_FORCE": "1",
        "PY_COLORS": "1",
        "GIT_PAGER": "cat",
        "PAGER": "cat",
    }


def _get_shell() -> str | None:
    if _IS_WINDOWS:
        program_files = os.environ.get("PROGRAMFILES", "")
        program_files_x86 = os.environ.get("PROGRAMFILES(X86)", "")
        paths = [
            os.path.join(program_files, "Git", "bin", "bash.exe"),
            os.path.join(program_files_x86, "Git", "bin", "bash.exe"),
        ]
        for path in paths:
            if path and os.path.exists(path):
                return path
        return None
    return os.environ.get("SHELL") or "/bin/bash"


def _get_spawn_argv(command: str) -> list[str] | None:
    shell = _get_shell()
    if shell is None:
        return None
    return [shell, "-c", command]


def _sanitize_output(text: str) -> str:
    text = _ANSI_ESCAPE_RE.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "")
    text = "".join(c for c in text if c >= " " or c in "\t\n")
    return text


_ANSI_SEQ_RE = re.compile(
    r"\x1b\[[0-9;?]*[a-zA-Z]"
    r"|\x1b\][^\x07]*\x07"
    r"|\x1b[()][AB012]"
    r"|\x1b[^\x1b]"
)


def _keep_sgr(match) -> str:
    seq = match.group(0)
    if seq.startswith("\x1b[") and seq.endswith("m"):
        return seq
    return ""


def _sanitize_color_output(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "")
    text = _ANSI_SEQ_RE.sub(_keep_sgr, text)
    text = "".join(c for c in text if c >= " " or c in "\t\n" or c == "\x1b")
    return text


class TruncationResult:
    def __init__(self, content: str, truncated: bool, lines_kept: int, total_lines: int):
        self.content = content
        self.truncated = truncated
        self.lines_kept = lines_kept
        self.total_lines = total_lines


def _truncate_tail(text: str) -> TruncationResult:
    lines = text.split("\n")
    total_lines = len(lines)

    if total_lines <= MAX_OUTPUT_LINES and len(text.encode("utf-8")) <= MAX_OUTPUT_BYTES:
        return TruncationResult(text, False, total_lines, total_lines)

    output_lines: list[str] = []
    output_bytes = 0

    for i in range(total_lines - 1, -1, -1):
        line = lines[i]
        encoded_line = line.encode("utf-8")
        line_bytes = len(encoded_line) + (1 if output_lines else 0)

        if output_bytes + line_bytes > MAX_OUTPUT_BYTES:
            if not output_lines:
                output_lines.append(encoded_line[-MAX_OUTPUT_BYTES:].decode("utf-8", "ignore"))
            break
        if len(output_lines) >= MAX_OUTPUT_LINES:
            break

        output_lines.insert(0, line)
        output_bytes += line_bytes

    return TruncationResult("\n".join(output_lines), True, len(output_lines), total_lines)


def _elided(dropped: int, body: str) -> str:
    return f"<truncated {dropped} lines>\n{body}"


def _agy_body(text: str) -> tuple[str, bool]:
    """The output block as agy writes it: the elision marker, then the tail.

    Returns the body and whether it was cut. The caller decides where the full
    output went and appends that pointer itself.
    """
    trunc = _truncate_tail(text)
    if not trunc.truncated:
        return trunc.content, False
    dropped = trunc.total_lines - trunc.lines_kept
    return _elided(dropped, trunc.content), True


def _exit_block(exit_code: int | None, output: str) -> str:
    """``The command exited with code N.`` + the output agy shows under it."""
    if exit_code is None:
        head = "The command was interrupted."
    else:
        head = f"The command exited with code {exit_code}."
    if not output:
        return head
    return f"{head}\nOutput:\n{output}"


async def _wait_exit(proc: asyncio.subprocess.Process, timeout: float | None = None) -> bool:
    """Wait for the child to be reaped, ignoring who still holds its pipes.

    ``Process.wait()`` looks like the obvious way to know a command finished,
    and it is a trap: asyncio only resolves it once *every* pipe is closed, so a
    command that leaves a daemon in the background -- ``npm run dev &``,
    ``sleep 300 &``, a server started by a test -- blocks it until the timeout.
    That is what a transcript stuck on "running bash" for three minutes was.
    ``returncode`` is set the moment the child is reaped, so that is the clock.
    """
    deadline = None if timeout is None else time.monotonic() + timeout
    while proc.returncode is None:
        if deadline is not None and time.monotonic() > deadline:
            return False
        await asyncio.sleep(_EXIT_POLL_SECONDS)
    return True


async def _kill_process_tree(proc: asyncio.subprocess.Process, group: int | None = None) -> None:
    """Kill the child and everything it started, then wait for it to be gone.

    The group id is the child's own pid (it was started in a new session), and
    it has to be used even after the child is reaped: a command that leaves a
    daemon behind exits, and ``getpgid`` on a dead pid then fails while the
    group -- the daemon -- is still there.
    """
    try:
        if _IS_WINDOWS:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
        else:
            os.killpg(group if group is not None else os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    with contextlib.suppress(Exception):
        await _wait_exit(proc, timeout=2.0)


async def _cancel_all(tasks: Iterable[asyncio.Task]) -> None:
    for task in tasks:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def _drain_comm(comm_task: asyncio.Task) -> None:
    """Give a finished command's pipes a moment, then cancel whatever is left."""
    if comm_task.done():
        return
    with contextlib.suppress(asyncio.CancelledError, TimeoutError):
        await asyncio.wait_for(asyncio.shield(comm_task), _SUBPROCESS_DRAIN_TIMEOUT_SECONDS)
    if not comm_task.done():
        comm_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await comm_task


def _write_full_output_to_temp(output: str) -> str:
    fd, path = tempfile.mkstemp(prefix="vx-bash-", suffix=".log")
    try:
        os.write(fd, output.encode("utf-8"))
    finally:
        os.close(fd)
    return path


#: A poll hands the model the tail, not a job's whole life: the drawer is where
#: output is read, and this is what the model needs to decide the next move.
JOB_READ_LINES = 200


class BashParams(BaseModel):
    action: str = Field(
        default="",
        description=(
            "Empty runs `command`. 'status' reads a task, 'list' names them, 'send_input' "
            "answers a waiting one, 'kill' stops it."
        ),
    )
    command: str = Field(default="", description="The command line; required unless action is set")
    timeout: int = Field(
        description=f"Timeout in seconds (default {DEFAULT_TIMEOUT})", default=DEFAULT_TIMEOUT
    )
    wait_ms: int = Field(
        default=0,
        description=(
            "Wait this long, then hand back a task id; 0 blocks until it exits or `timeout`. "
            "A command that outlives the wait keeps running and notifies you."
        ),
    )
    background: bool = Field(
        default=False,
        description=(
            "Start as a task (j1, j2…) and return at once: dev servers, watchers, anything slow."
        ),
    )
    task_id: str = Field(
        default="", description="Task or terminal id status/kill/send_input act on"
    )
    input: str = Field(default="", description="Text to deliver to that task or terminal.")

    def resolved_action(self) -> str:
        """The action, normalized; empty means run ``command``."""
        return self.action.strip().lower()

    def target(self) -> str:
        return self.task_id.strip()


#: Shell names, one per bash tool: t1 is this agent's, t2 the next agent's.
_SHELL_IDS = itertools.count(1)


class BashTool(BaseTool):
    name = "bash"
    tool_icon = "$"
    params = BashParams
    mutating = True
    #: The persistent shell this instance drives (``t1``), allocated on first use.
    _terminal: str | None = None
    description = (
        "Run a shell command (builds, tests, git, package managers) in one persistent shell "
        "per agent, so cd, exports and an activated venv survive between calls. "
        f"Output truncated to last {MAX_OUTPUT_LINES} lines/{MAX_OUTPUT_BYTES // 1024}KB "
        "(full saved to a log). Not for reading/listing (use read), searching "
        "(use search), editing (use apply_patch), or fetching URLs (use web). "
        "Long-lived work goes in the background: background=true returns a task id, then "
        "action=status/send_input/kill reads, answers or stops it, and action=list reports "
        "them. The fields below carry the rest."
    )

    def format_call(self, params: BashParams) -> str:
        action = params.resolved_action()
        if action == "list":
            return "tasks"
        if action:
            extra = f" · {params.input}" if params.input else ""
            return f"{action} {params.target()}{extra}".strip()
        prefix = "background: " if params.background else ""
        suffix = f" · wait {params.wait_ms}ms" if params.wait_ms else ""
        return f"{prefix}{params.command}{suffix}"

    # -- entry --------------------------------------------------------------

    async def execute(
        self,
        params: BashParams,
        cancel_event: asyncio.Event | None = None,
        inline_output: bool = False,
        on_output: Callable[[str], None] | None = None,
    ) -> ToolResult:
        action = params.resolved_action()
        if action:
            return await self._dispatch(action, params)
        if params.background:
            return await self._start_task(params)
        return await self._run_in_terminal(params, cancel_event=cancel_event, on_output=on_output)

    # -- actions ------------------------------------------------------------

    async def _dispatch(self, action: str, params: BashParams) -> ToolResult:
        if action == "list":
            return self._list()
        if action not in ("status", "kill", "send_input"):
            return self._fail(f"Unknown action “{action}”; use status, list, send_input or kill")
        if action == "send_input" and not params.input:
            return self._fail("send_input needs input")
        if not params.target():
            return self._fail(f"The {action} action needs a task_id")
        from vx.jobs import REGISTRY
        from vx.terminals import TERMINALS

        target = params.target()
        job = REGISTRY.get(target)
        terminal = TERMINALS.get(target)
        if job is None and terminal is None:
            return self._unknown_target(target)
        if job is not None:
            return await self._job_action(action, job, params)
        return await self._terminal_action(action, terminal, params)

    def _list(self) -> ToolResult:
        from vx.jobs import REGISTRY
        from vx.terminals import TERMINALS

        blocks: list[str] = []
        for job in REGISTRY.all():
            blocks.append(self._job_status_block(job, tail=0))
        for terminal in TERMINALS.all():
            blocks.append(self._terminal_status_block(terminal))
        if not blocks:
            msg = "No running tasks."
            return ToolResult(success=True, result=self._with_notices(msg), ui_summary="no tasks")
        body = "\n\n".join(blocks)
        return ToolResult(
            success=True,
            result=self._with_notices(body),
            ui_summary=f"{len(REGISTRY.all())} tasks · {len(TERMINALS.all())} terminals",
        )

    def _unknown_target(self, target: str) -> ToolResult:
        from vx.jobs import REGISTRY
        from vx.terminals import TERMINALS

        known = ", ".join([job.id for job in REGISTRY.all()] + [t.id for t in TERMINALS.all()])
        return self._fail(
            f'No task “{target}”; known: {known or "none"}. Use bash(action="list").'
        )

    async def _job_action(self, action: str, job, params: BashParams) -> ToolResult:
        from vx.jobs import REGISTRY

        if action == "kill":
            await REGISTRY.kill(job)
            tail = "\n".join(job.tail(20))
            msg = f'Task "{job.id}" cancelled.'
            body = f"{msg}\nStatus: {job.status_word()}"
            if job.exit_code is not None:
                body += f" (exit {job.exit_code})"
            if tail:
                body += f"\n\n{tail}"
            return ToolResult(
                success=True, result=self._with_notices(body), ui_summary=f"[yellow]{msg}[/yellow]"
            )
        if action == "send_input":
            if not job.send_input(params.input):
                return self._fail(f"Task “{job.id}” is not running, so it cannot take input")
            msg = f'Input sent to task "{job.id}".'
            return ToolResult(
                success=True, result=self._with_notices(msg), ui_summary=f"{job.id} · input sent"
            )
        return await self._job_status(job, params)

    async def _job_status(self, job, params: BashParams) -> ToolResult:
        from vx.jobs import REGISTRY

        if params.wait_ms:
            await REGISTRY.wait_for(job, params.wait_ms / 1000.0)
        lines = job.take_unread()
        dropped = 0
        if len(lines) > JOB_READ_LINES:
            dropped = len(lines) - JOB_READ_LINES
            lines = lines[-JOB_READ_LINES:]
        body = "\n".join(lines)
        if dropped:
            body = _elided(dropped, body)
        if not body:
            body = "(no new output)"
        result = self._job_status_block(job, tail=0, body=body)
        summary = f"{job.id} · {job.status_word().lower()} · {job.elapsed():.0f}s"
        if job.unread():
            summary += f" · {job.unread()} unread"
        return ToolResult(success=True, result=self._with_notices(result), ui_summary=summary)

    @staticmethod
    def _job_status_block(job, *, tail: int = 0, body: str | None = None) -> str:
        """agy's task block: what it is, how it is doing, where its log lives."""
        out = f"Task: {job.id}\nStatus: {job.status_word()}"
        if job.exit_code is not None:
            out += f" (exit {job.exit_code})"
        out += f"\nLog: {job.log_path if job.log_path else '(in memory)'}"
        if body is None and tail:
            body = "\n".join(job.tail(tail))
        if body:
            out += f"\nLog output:\n{body}"
        return out

    async def _terminal_action(self, action: str, terminal, params: BashParams) -> ToolResult:
        from vx.terminals import TERMINALS

        if action == "kill":
            await TERMINALS.close(terminal.id)
            msg = f'Terminal "{terminal.id}" closed.'
            return ToolResult(
                success=True, result=self._with_notices(msg), ui_summary=f"[yellow]{msg}[/yellow]"
            )
        if action == "send_input":
            text = params.input if params.input.endswith("\n") else params.input + "\n"
            if not terminal.write(text):
                return self._fail(f'Terminal "{terminal.id}" is closed')
            msg = f'Input sent to terminal "{terminal.id}".'
            return ToolResult(
                success=True,
                result=self._with_notices(msg),
                ui_summary=f"{terminal.id} · input sent",
            )
        out = await terminal.read_new(wait_ms=params.wait_ms)
        body = out.text or "(no new output)"
        result = self._terminal_status_block(terminal, body=body)
        return ToolResult(
            success=True,
            result=self._with_notices(result),
            ui_summary=f"{terminal.id} · {terminal.status_word().lower()}",
        )

    @staticmethod
    def _terminal_status_block(terminal, *, body: str | None = None) -> str:
        """The terminal's status block: identity, state, and output when given."""
        out = (
            f"Terminal: {terminal.id}\n"
            f"Status: {terminal.status_word()}\n"
            f"Running for: {terminal.elapsed():.0f}s"
        )
        if body:
            out += f"\nLog output:\n{body}"
        return out

    # -- agentap ------------------------------------------------------------

    async def _run_in_terminal(
        self,
        params: BashParams,
        *,
        cancel_event: asyncio.Event | None = None,
        on_output: Callable[[str], None] | None = None,
    ) -> ToolResult:
        """Run the command in this tool's own shell: the default, so state survives."""
        from vx.terminals import TERMINALS

        if not params.command.strip():
            return self._fail("Command cannot be empty")
        if _IS_WINDOWS:
            return await self._run_fresh(
                params, cancel_event=cancel_event, inline_output=False, on_output=on_output
            )
        terminal = await TERMINALS.get_or_create(self._shell_id())
        if terminal is None:
            return await self._run_fresh(
                params, cancel_event=cancel_event, inline_output=False, on_output=on_output
            )
        out = await terminal.run(
            params.command,
            wait_ms=params.wait_ms,
            timeout=params.timeout,
            on_output=on_output,
            cancel_event=cancel_event,
        )
        body, cut = _agy_body(out.text)
        if cut:
            path = _write_full_output_to_temp(out.text)
            body += f"\n\nFull output: {path}"
        if out.aborted:
            return ToolResult(
                success=False,
                result=f"Command aborted{('\n' + body) if body else ''}",
                ui_summary="[yellow]Command aborted by user[/yellow]",
            )
        if out.state == "closed":
            return ToolResult(
                success=False,
                result=(
                    "The command ended the shell, so it has no exit code"
                    f"{(': ' + body) if body else ''}. The next bash call opens a new one."
                ),
                ui_summary="[yellow]shell ended by the command[/yellow]",
            )
        if out.timed_out:
            return ToolResult(
                success=False,
                result=f"Command timed out after {params.timeout}s{('\n' + body) if body else ''}",
                ui_summary=f"[yellow]timed out after {params.timeout}s[/yellow]",
            )
        if out.state == "running":
            result = (
                f"The command is still running in task {terminal.id}: {params.command}\n"
                f"Output so far:\n{body}\n"
                f'Read it again with bash(action="status", task_id="{terminal.id}"), '
                f'stop it with bash(action="kill", task_id="{terminal.id}").'
            )
            return ToolResult(
                success=True,
                result=self._with_notices(result),
                ui_summary=f"{terminal.id} · still running",
            )
        result = _exit_block(out.exit_code, body)
        code = out.exit_code if out.exit_code is not None else 1
        summary = f"{terminal.id} · done" if code == 0 else f"Exit code {code} · {terminal.id}"
        return ToolResult(
            success=code == 0,
            result=self._with_notices(result),
            ui_summary=summary if code == 0 else f"[red]{summary}[/red]",
        )

    def _shell_id(self) -> str:
        """This tool's shell name: one per bash tool, so agents do not share a cd."""
        if self._terminal is None:
            self._terminal = f"t{next(_SHELL_IDS)}"
        return self._terminal

    async def _start_job(self, params: BashParams) -> Any | ToolResult:
        """Validate and start the registry job, or return the refusal."""
        from vx.jobs import REGISTRY

        if not params.command.strip():
            return self._fail("Command cannot be empty")
        job = await REGISTRY.start(params.command, wait_ms=params.wait_ms)
        if job is None:
            return self._fail("No shell to run the command in")
        return job

    async def _start_task(self, params: BashParams) -> ToolResult:
        started = await self._start_job(params)
        if isinstance(started, ToolResult):
            return started
        job = started
        kind = "Task"
        result = (
            f"Started {job.id}: {params.command}\n"
            f"Log: {job.log_path}\n"
            f'Read it with bash(action="status", task_id="{job.id}"), answer it with '
            f'bash(action="send_input", task_id="{job.id}", input="..."), stop it with '
            f'bash(action="kill", task_id="{job.id}").'
        )
        return ToolResult(
            success=True,
            result=self._with_notices(result),
            ui_summary=f"[green]{kind} {job.id}[/green]",
        )

    async def _run_fresh(
        self,
        params: BashParams,
        *,
        cancel_event: asyncio.Event | None,
        inline_output: bool,
        on_output: Callable[[str], None] | None,
    ) -> ToolResult:
        """Run in a fresh process: the fallback when this host has no pty."""
        return await self._run_foreground(
            params, cancel_event=cancel_event, inline_output=inline_output, on_output=on_output
        )

    async def _run_foreground(
        self,
        params: BashParams,
        *,
        cancel_event: asyncio.Event | None,
        inline_output: bool,
        on_output: Callable[[str], None] | None,
    ) -> ToolResult:
        if not params.command.strip():
            return self._fail("Command cannot be empty")

        command = params.command
        cwd = Path.cwd()
        if not cwd.exists():
            return ToolResult(
                success=False, ui_summary=f"[red]Working directory does not exist: {cwd}[/red]"
            )

        proc = None
        #: The child's process group, kept from before it could exit.
        group: int | None = None
        try:
            if True:
                spawn_argv = _get_spawn_argv(command)
                if spawn_argv is None:
                    proc = await asyncio.create_subprocess_shell(
                        command,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        stdin=asyncio.subprocess.DEVNULL,
                        env=_get_env(),
                        start_new_session=not _IS_WINDOWS,
                        limit=_STREAM_LIMIT_BYTES,
                    )
                else:
                    proc = await asyncio.create_subprocess_exec(
                        *spawn_argv,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        stdin=asyncio.subprocess.DEVNULL,
                        env=_get_env(),
                        start_new_session=not _IS_WINDOWS,
                        limit=_STREAM_LIMIT_BYTES,
                    )

            # The child leads its own group, so the group outlives the child and
            # can be cleaned up even when the child exited first.
            group = None if _IS_WINDOWS else proc.pid
            if (
                hasattr(proc, "stdout")
                and hasattr(proc, "stderr")
                and proc.stdout is not None
                and proc.stderr is not None
            ):
                stdout_chunks: list[bytes] = []
                stderr_chunks: list[bytes] = []

                async def _stream_pipe(
                    reader: asyncio.StreamReader | None, dest: list[bytes]
                ) -> None:
                    if not reader:
                        return
                    while True:
                        try:
                            chunk = await reader.readline()
                        except (asyncio.LimitOverrunError, ValueError):
                            # Longer than the stream limit: take what is
                            # buffered instead of failing the whole call.
                            chunk = await reader.read(1 << 16)
                        if not chunk:
                            break
                        dest.append(chunk)
                        if on_output and callable(on_output):
                            with contextlib.suppress(Exception):
                                text = _sanitize_output(chunk.decode("utf-8", errors="replace"))
                                if text:
                                    on_output(text)

                async def _stream_all() -> tuple[bytes, bytes]:
                    pipes = [
                        asyncio.create_task(_stream_pipe(proc.stdout, stdout_chunks)),
                        asyncio.create_task(_stream_pipe(proc.stderr, stderr_chunks)),
                    ]
                    await _wait_exit(proc)
                    # The command is done. Whatever still holds these pipes is
                    # a child it left behind, and the caller is waiting on the
                    # command, not on the daemon.
                    await asyncio.wait(pipes, timeout=_PIPE_GRACE_SECONDS)
                    for task in pipes:
                        if not task.done():
                            task.cancel()
                            with contextlib.suppress(asyncio.CancelledError):
                                await task
                    return b"".join(stdout_chunks), b"".join(stderr_chunks)

                comm_task = asyncio.create_task(_stream_all())
            else:
                comm_task = asyncio.create_task(proc.communicate())

            try:
                if cancel_event:
                    cancel_wait = asyncio.create_task(cancel_event.wait())
                    done, pending = await asyncio.wait(
                        [comm_task, cancel_wait],
                        timeout=params.timeout,
                        return_when=asyncio.FIRST_COMPLETED,
                    )

                    if not done:
                        await _cancel_all(pending)
                        await _kill_process_tree(proc, group)
                        return self._timeout(params)

                    if cancel_wait in done and cancel_event.is_set():
                        await _kill_process_tree(proc, group)
                        await _drain_comm(comm_task)
                        return ToolResult(
                            success=False,
                            result="Command aborted",
                            ui_summary="[yellow]Command aborted by user[/yellow]",
                        )

                    await _cancel_all(pending)

                    stdout_bytes, stderr_bytes = comm_task.result()
                else:
                    stdout_bytes, stderr_bytes = await asyncio.wait_for(
                        comm_task, timeout=params.timeout
                    )

            except TimeoutError:
                await _kill_process_tree(proc, group)
                await _drain_comm(comm_task)
                return self._timeout(params)

            stdout_raw = stdout_bytes.decode("utf-8", errors="replace")
            stderr_raw = stderr_bytes.decode("utf-8", errors="replace")
            stdout = _sanitize_output(stdout_raw)
            stderr = _sanitize_output(stderr_raw)
            stdout_color = _sanitize_color_output(stdout_raw)
            stderr_color = _sanitize_color_output(stderr_raw)

            def _merge(so: str, se: str) -> str:
                if not so and not se:
                    return ""
                result = so or ""
                if se:
                    if result:
                        result += f"\n[stderr]\n{se}"
                    else:
                        result = f"[stderr]\n{se}"
                return result.rstrip()

            full_output = _merge(stdout, stderr)
            full_output_color = _merge(stdout_color, stderr_color)

            body, cut = _agy_body(full_output)
            body_lines = len(body.split("\n"))
            color_lines = full_output_color.split("\n")
            color_tail = "\n".join(color_lines[-body_lines:]) if full_output_color else ""
            if cut:
                marker = f"\n\nFull output: {_write_full_output_to_temp(full_output)}"
                if inline_output:
                    marker = "]"
                body += marker
                if color_tail:
                    color_tail += marker

            result_text = _exit_block(proc.returncode, body)

            return ToolResult(
                success=proc.returncode == 0,
                result=self._with_notices(result_text),
                ui_summary=self._summary_for(proc.returncode, body),
            )

        except Exception as e:
            return self._fail(f"Error running command: {e}")
        finally:
            if proc is not None:
                with contextlib.suppress(Exception):
                    await _kill_process_tree(proc, group)

    # -- results ------------------------------------------------------------

    @staticmethod
    def _fail(msg: str) -> ToolResult:
        return ToolResult(success=False, result=msg, ui_summary=f"[red]{msg}[/red]")

    @staticmethod
    def _timeout(params: BashParams) -> ToolResult:
        return ToolResult(
            success=False, ui_summary=f"[red]Command timed out after {params.timeout}s[/red]"
        )

    @staticmethod
    def _summary_for(exit_code: int | None, body: str) -> str:
        if exit_code != 0:
            return f"[red]Exit code {exit_code}[/red]"
        non_empty = [line for line in body.split("\n") if line.strip()]
        if len(non_empty) <= 1:
            return body.strip().replace("\n", " ")
        return ""

    @staticmethod
    def _with_notices(text: str) -> str:
        """Anything a job said while this call was being built comes first."""
        notices = drain_text()
        if not notices:
            return text
        return f"{notices}\n\n{text}"
