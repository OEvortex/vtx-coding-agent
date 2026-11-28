"""Persistent terminals: one shell, many commands, state that survives.

The bash tool normally runs each command in a fresh process tree: predictable,
and useless the moment a command needs the one before it. ``cd src && export
PYTHONPATH=... && pytest`` works because it is one command line; ``cd src`` and
then ``pytest`` in the next tool call does not, and neither does a REPL, an
``ssh`` session, or anything that asks a question before it starts.

A :class:`Terminal` is one shell attached to a PTY, kept for the session, driven
by a sentinel: the command is written, followed by ``printf '\036VX-EXIT:%d\036'``,
and everything up to that sentinel is the command's output with its exit code
attached. That is agy's persistent terminal (``terminalId`` + ``SendTerminalInput``)
with a smaller protocol. Every bash call runs in one of these, so plain ``bash``
means the same shell every time and ``cd`` needs no second thought.

Echo is off: a PTY echoes what is written to it, and an echoed command line in
the output reads as if the command printed itself.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import shutil
import signal
import subprocess
import termios
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from vx.tools.bash import _IS_WINDOWS, _get_env, _get_shell, _sanitize_output

#: Marks the end of a command in the stream: ``\036VX-EXIT:<code>\036``.
SENTINEL_RE = re.compile(r"\x1e\s*VX-EXIT:(-?\d+)\s*\x1e")
SENTINEL_PRINTF = "printf '\\036VX-EXIT:%d\\036' $?"

#: Commands that would take the shell down with them, and so run in a subshell.
_DOOMED_RE = re.compile(r"^\s*(exit|exec|logout)\b")

#: A shell that reads commands from a pty without drawing a prompt. bash with
#: readline off and an empty PS1 prints exactly what the command printed, which
#: is the whole reason the sentinel can be found at all; zsh and friends echo
#: their own input and paint a prompt around it.
_QUIET_FLAGS = ("--norc", "--noprofile", "--noediting")
_QUIET_ENV = {"PS1": "", "PS2": "", "PROMPT": "", "RPROMPT": ""}


def _resolve_shell() -> tuple[str, tuple[str, ...]] | None:
    """The shell to keep open, and the flags that keep it quiet."""
    bash = shutil.which("bash") or ("/bin/bash" if os.path.exists("/bin/bash") else None)
    if bash:
        return bash, _QUIET_FLAGS
    shell = _get_shell()
    if shell is None:
        return None
    return shell, ()


@dataclass
class TerminalOutput:
    """What one command in a terminal produced."""

    text: str
    exit_code: int | None
    state: str  # running | done
    timed_out: bool = False
    aborted: bool = False

    @property
    def finished(self) -> bool:
        return self.exit_code is not None


@dataclass
class Terminal:
    """One long-lived shell attached to a PTY."""

    id: str
    cwd: Path = field(default_factory=Path.cwd)
    shell: str | None = None
    proc: Any = None
    _master: int | None = None
    _buf: str = ""
    _state: str = "running"  # running | closed
    _started: float = field(default_factory=time.monotonic)
    _changed: asyncio.Event = field(default_factory=asyncio.Event)
    #: The loop the pty reader was registered on. A shell outlives the loop that
    #: opened it (a closed app, a new test), and a reader on a dead loop never
    #: delivers the sentinel: the next caller would wait for output forever.
    _loop: Any = None

    # -- lifecycle ----------------------------------------------------------

    async def start(self) -> bool:
        """Spawn the shell. False when there is no shell or no pty."""
        if _IS_WINDOWS or self.proc is not None:
            return self.proc is not None
        resolved = _resolve_shell()
        if resolved is None:
            return False
        shell, flags = resolved
        self.shell = shell
        master, slave = os.openpty()
        _quiet_pty(slave)
        self.proc = subprocess.Popen(
            [shell, *flags],
            stdin=slave,
            stdout=slave,
            stderr=slave,
            cwd=str(self.cwd),
            env={**_get_env(), "BASH_ENV": "/dev/null", "ENV": "/dev/null", **_QUIET_ENV},
            start_new_session=True,
            close_fds=True,
        )
        os.close(slave)
        self._master = master
        self._loop = asyncio.get_running_loop()
        self._loop.add_reader(master, self._on_readable)
        return True

    def _pending(self) -> str:
        """Output that has arrived and not been returned yet."""
        return self._buf

    def _stopped(self) -> bool:
        """Whether the shell died mid-command; a method, so nothing narrows it."""
        return self._state != "running"

    def live_here(self) -> bool:
        """Whether this shell's reader belongs to the loop that is running now."""
        if self._state != "running" or self.proc is None or self._master is None:
            return False
        try:
            return self._loop is asyncio.get_running_loop()
        except RuntimeError:
            return False

    def _on_readable(self) -> None:
        try:
            chunk = os.read(self._master, 1 << 16) if self._master is not None else b""
        except OSError:
            chunk = b""
        if not chunk:
            self._state = "closed"
            self._changed.set()
            return
        # Raw, unsanitized: the sentinel is made of control characters, and a
        # sanitizer that runs first eats it.
        self._buf += chunk.decode("utf-8", errors="replace")
        self._changed.set()

    async def close(self) -> None:
        """Stop the shell and everything it started."""
        self._state = "closed"
        if self._master is not None:
            with contextlib.suppress(Exception):
                asyncio.get_running_loop().remove_reader(self._master)
            with contextlib.suppress(OSError):
                os.close(self._master)
            self._master = None
        if self.proc is not None:
            with contextlib.suppress(Exception):
                os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
            with contextlib.suppress(Exception):
                self.proc.wait(timeout=2)
            self.proc = None
        self._changed.set()

    # -- running ------------------------------------------------------------

    async def run(
        self,
        command: str,
        *,
        wait_ms: int = 0,
        timeout: float = 180.0,
        on_output: Callable[[str], None] | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> TerminalOutput:
        """Run ``command`` in this shell and read it up to the sentinel.

        Output arrives live through ``on_output`` as the pty produces it, so a
        long build shows up in the transcript while it runs rather than all at
        once at the end.
        """
        if self._state != "running" or self.proc is None:
            return TerminalOutput("", None, "closed")
        self._buf = ""
        self.write(_compose(command))
        deadline = time.monotonic() + max(0.0, timeout)
        soft = time.monotonic() + max(0.0, wait_ms / 1000.0) if wait_ms else deadline
        while True:
            self._changed.clear()
            parsed = self._take()
            if parsed is not None:
                return parsed
            if self._stopped():
                # The command took the shell with it (`exit`, `exec`, a crash):
                # no sentinel is ever coming.
                return TerminalOutput(self._take_buffer(), None, "closed")
            if on_output is not None and self._pending():
                chunk = self._take_buffer()
                if chunk:
                    with contextlib.suppress(Exception):
                        on_output(chunk)
            now = time.monotonic()
            if cancel_event is not None and cancel_event.is_set():
                return await self._interrupt()
            if now >= min(soft, deadline):
                if now >= deadline:
                    self.write("\x03")  # Ctrl-C: the pty's own interrupt
                    await asyncio.sleep(0.2)
                    parsed = self._take()
                    if parsed is not None:
                        parsed.timed_out = True
                        return parsed
                    return TerminalOutput(self._take_buffer(), None, "running", timed_out=True)
                return TerminalOutput(self._take_buffer(), None, "running")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._changed.wait(), min(0.05, deadline - now))

    async def _interrupt(self, grace: float = 0.6) -> TerminalOutput:
        """Ctrl-C the foreground command and collect what it left behind.

        The sentinel still lands -- the shell prints it once the command dies --
        so the buffer is not left poisoned for the next call.
        """
        self.write("\x03")
        end = time.monotonic() + grace
        while time.monotonic() < end:
            parsed = self._take()
            if parsed is not None:
                parsed.aborted = True
                return parsed
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._changed.wait(), 0.05)
        parsed = self._take()
        if parsed is not None:
            parsed.aborted = True
            return parsed
        return TerminalOutput(self._take_buffer(), None, "running", aborted=True)

    def write(self, text: str) -> bool:
        """Raw keystrokes into the shell: an answer, a Ctrl-C, a heredoc."""
        if self._master is None or self._state != "running":
            return False
        try:
            os.write(self._master, text.encode())
            return True
        except OSError:
            return False

    async def read_new(self, *, wait_ms: int = 0) -> TerminalOutput:
        """Whatever has arrived since the last read, sentinel or not."""
        if wait_ms:
            await asyncio.sleep(min(wait_ms / 1000.0, 5.0))
        parsed = self._take()
        if parsed is not None:
            return parsed
        return TerminalOutput(self._take_buffer(), None, self._state)

    # -- parsing ------------------------------------------------------------

    def _take(self) -> TerminalOutput | None:
        """Split the buffer at the first sentinel, if there is one."""
        match = SENTINEL_RE.search(self._buf)
        if match is None:
            return None
        text = self._buf[: match.start()]
        self._buf = self._buf[match.end() :]
        return TerminalOutput(_trim(_sanitize_output(text)), int(match.group(1)), "done")

    def _take_buffer(self) -> str:
        text, self._buf = self._buf, ""
        return _trim(_sanitize_output(text))

    def elapsed(self) -> float:
        return time.monotonic() - self._started

    def status_word(self) -> str:
        return "RUNNING" if self._state == "running" else "CLOSED"


def _trim(text: str) -> str:
    return text.lstrip("\n").rstrip("\n")


def _compose(command: str) -> str:
    """The command line, with its sentinel, in one write.

    The sentinel goes on the *same* line as the command, because a command that
    reads stdin (``read x``, a password prompt, a REPL) would otherwise eat the
    next line — which is the sentinel — and then wait forever for a human.
    Joined with ``;`` the sentinel only runs once the command has finished,
    whatever it did in between.

    Two shapes cannot be joined: a trailing ``&`` (``cmd & ; printf`` is a syntax
    error) and a heredoc, whose body must own its lines.
    """
    body = command.rstrip("\n")
    if not body:
        return f"true; {SENTINEL_PRINTF}\n"
    if _DOOMED_RE.match(body):
        # `exit 3` would close the shell and leave no exit code to report; in a
        # subshell it reports 3 and the shell is still there for the next call.
        return f"( {body} ); {SENTINEL_PRINTF}\n"
    if "<<" in body or body.endswith("&"):
        return f"{body}\n{SENTINEL_PRINTF}\n"
    return f"{body}; {SENTINEL_PRINTF}\n"


def _quiet_pty(slave: int) -> None:
    """Turn off echo so the command line does not answer itself."""
    with contextlib.suppress(Exception):
        attrs = termios.tcgetattr(slave)
        attrs[3] &= ~termios.ECHO  # lflags
        attrs[3] &= ~termios.ECHONL
        termios.tcsetattr(slave, termios.TCSANOW, attrs)


class TerminalRegistry:
    """Every persistent terminal this session opened, by the name the model chose."""

    def __init__(self) -> None:
        self.terminals: dict[str, Terminal] = {}

    def get(self, terminal_id: str) -> Terminal | None:
        return self.terminals.get(terminal_id.strip())

    def all(self) -> list[Terminal]:
        return list(self.terminals.values())

    async def get_or_create(self, terminal_id: str) -> Terminal | None:
        terminal = self.get(terminal_id)
        if terminal is not None:
            if terminal.live_here():
                return terminal
            # Opened under a loop that is gone: it could never answer again.
            await terminal.close()
            self.terminals.pop(terminal.id, None)
        terminal = Terminal(id=terminal_id.strip(), cwd=Path.cwd())
        if not await terminal.start():
            return None
        self.terminals[terminal.id] = terminal
        return terminal

    async def close(self, terminal_id: str) -> bool:
        terminal = self.get(terminal_id)
        if terminal is None:
            return False
        await terminal.close()
        self.terminals.pop(terminal.id, None)
        return True

    async def close_all(self) -> None:
        for terminal in list(self.terminals.values()):
            await terminal.close()
        self.terminals.clear()


#: One registry per process, like the job registry.
TERMINALS = TerminalRegistry()
