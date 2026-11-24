"""Bash tool for vx — run shell commands."""

import asyncio
import contextlib
import os
import re
import signal
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

from ctx.agent.tools.base import BaseTool
from ctx.protocol.types import ToolResult
from pydantic import BaseModel, Field

DEFAULT_TIMEOUT = 180
MAX_OUTPUT_BYTES = 50 * 1024
MAX_OUTPUT_LINES = 2000
_SUBPROCESS_DRAIN_TIMEOUT_SECONDS = 1.0

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


async def _kill_process_tree(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return
    try:
        if _IS_WINDOWS:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        await proc.wait()
    except (ProcessLookupError, PermissionError, OSError):
        pass


def _write_full_output_to_temp(output: str) -> str:
    fd, path = tempfile.mkstemp(prefix="vx-bash-", suffix=".log")
    try:
        os.write(fd, output.encode("utf-8"))
    finally:
        os.close(fd)
    return path


class BashParams(BaseModel):
    command: str = Field(description="Command line string to execute")
    timeout: int = Field(
        description=f"Timeout in seconds (default {DEFAULT_TIMEOUT})", default=DEFAULT_TIMEOUT
    )


class BashTool(BaseTool):
    name = "bash"
    tool_icon = "$"
    params = BashParams
    mutating = True
    prompt_guidelines = (
        "bash for git/builds/tests/scripts and searching (rg, grep, find)",
        "not for reading or editing files — use read/apply_patch",
    )
    description = (
        "Run a shell command in the cwd (builds, tests, git, package managers, and "
        "searching with rg/grep/find). "
        f"Output truncated to last {MAX_OUTPUT_LINES} lines/{MAX_OUTPUT_BYTES // 1024}KB "
        "(full saved to temp log). Not for reading or editing files — use read/apply_patch."
    )

    def format_call(self, params: BashParams) -> str:
        return params.command

    async def execute(
        self,
        params: BashParams,
        cancel_event: asyncio.Event | None = None,
        inline_output: bool = False,
        on_output: Callable[[str], None] | None = None,
    ) -> ToolResult:
        if not params.command.strip():
            msg = "Command cannot be empty"
            return ToolResult(success=False, result=msg, ui_summary=f"[red]{msg}[/red]")

        command = params.command
        cwd = Path.cwd()
        if not cwd.exists():
            return ToolResult(
                success=False, ui_summary=f"[red]Working directory does not exist: {cwd}[/red]"
            )

        proc = None
        try:
            spawn_argv = _get_spawn_argv(command)
            if spawn_argv is None:
                proc = await asyncio.create_subprocess_shell(
                    command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    stdin=asyncio.subprocess.DEVNULL,
                    env=_get_env(),
                    start_new_session=not _IS_WINDOWS,
                )
            else:
                proc = await asyncio.create_subprocess_exec(
                    *spawn_argv,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    stdin=asyncio.subprocess.DEVNULL,
                    env=_get_env(),
                    start_new_session=not _IS_WINDOWS,
                )

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
                        chunk = await reader.readline()
                        if not chunk:
                            break
                        dest.append(chunk)
                        if on_output and callable(on_output):
                            with contextlib.suppress(Exception):
                                text = _sanitize_output(chunk.decode("utf-8", errors="replace"))
                                if text:
                                    on_output(text)

                async def _stream_all() -> tuple[bytes, bytes]:
                    await asyncio.gather(
                        _stream_pipe(proc.stdout, stdout_chunks),
                        _stream_pipe(proc.stderr, stderr_chunks),
                        proc.wait(),
                    )
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
                        for task in pending:
                            task.cancel()
                            with contextlib.suppress(asyncio.CancelledError):
                                await task
                        await _kill_process_tree(proc)
                        return ToolResult(
                            success=False,
                            ui_summary=f"[red]Command timed out after {params.timeout}s[/red]",
                        )

                    if cancel_wait in done and cancel_event.is_set():
                        await _kill_process_tree(proc)
                        if not comm_task.done():
                            with contextlib.suppress(asyncio.CancelledError, TimeoutError):
                                await asyncio.wait_for(
                                    asyncio.shield(comm_task), _SUBPROCESS_DRAIN_TIMEOUT_SECONDS
                                )
                            if not comm_task.done():
                                comm_task.cancel()
                                with contextlib.suppress(asyncio.CancelledError):
                                    await comm_task
                        return ToolResult(
                            success=False,
                            result="Command aborted",
                            ui_summary="[yellow]Command aborted by user[/yellow]",
                        )

                    for task in pending:
                        task.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await task

                    stdout_bytes, stderr_bytes = comm_task.result()
                else:
                    stdout_bytes, stderr_bytes = await asyncio.wait_for(
                        comm_task, timeout=params.timeout
                    )

            except TimeoutError:
                await _kill_process_tree(proc)
                if comm_task is not None and not comm_task.done():
                    with contextlib.suppress(asyncio.CancelledError, TimeoutError):
                        await asyncio.wait_for(
                            asyncio.shield(comm_task), _SUBPROCESS_DRAIN_TIMEOUT_SECONDS
                        )
                    if not comm_task.done():
                        comm_task.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await comm_task
                return ToolResult(
                    success=False,
                    ui_summary=f"[red]Command timed out after {params.timeout}s[/red]",
                )

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

            trunc = _truncate_tail(full_output)
            color_lines = full_output_color.split("\n")
            color_tail = "\n".join(color_lines[-trunc.lines_kept :]) if full_output_color else ""
            if trunc.truncated:
                marker = (
                    f"\n\n[output truncated to last {trunc.lines_kept} lines "
                    f"of {trunc.total_lines}"
                )
                if inline_output:
                    marker += "]"
                else:
                    temp_file_path = _write_full_output_to_temp(full_output)
                    marker += f"; full output: {temp_file_path}]"
                trunc.content += marker
                if color_tail:
                    color_tail += marker

            result_text = trunc.content or "(no output)"

            if proc.returncode == 0:
                non_empty_lines = [
                    line for line in (trunc.content or "").split("\n") if line.strip()
                ]
                if len(non_empty_lines) <= 1:
                    summary_line = (trunc.content or "").strip().replace("\n", " ")
                    return ToolResult(success=True, result=result_text, ui_summary=summary_line)
                return ToolResult(success=True, result=result_text)
            else:
                return ToolResult(
                    success=False,
                    result=result_text,
                    ui_summary=f"[red]Exit code {proc.returncode}[/red]",
                )

        except Exception as e:
            msg = f"Error running command: {e}"
            return ToolResult(success=False, result=msg, ui_summary=f"[red]{msg}[/red]")
        finally:
            if proc is not None:
                await _kill_process_tree(proc)
