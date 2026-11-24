"""The vx TUI: an fx-style terminal front end for the ctx agent runtime.

The frame is built bottom-up the way fx builds it: transcript, ``─`` rule,
composer, ``─`` rule, hint row. Overlays (approval, questions, pickers) take
the composer's place instead of printing into the transcript.
"""

from __future__ import annotations

import os
import re
import signal
import time
from collections import deque
from contextlib import suppress
from typing import TYPE_CHECKING, Any

import vx.tools
from vx import attachments as attach
from vx import history as prompt_history
from vx import prefs, titles
from vx.atpath import expand_paths, list_candidates, parse_at
from vx.brand import VERSION
from vx.permissions import BUILTIN_MODES, PermissionRuntime
from vx.tui import ansi, blocks, footer, theme
from vx.tui.canvas import Painter
from vx.tui.keys import KeyDecoder
from vx.tui.terminal import Terminal
from vx.tui.viewport import Viewport

if TYPE_CHECKING:
    # Annotation only: these modules pull ctx's agent graph (plus aiohttp) in,
    # and the shell has to answer a key before that finishes loading.
    from ctx.agent.runtime import ConversationRuntime
    from ctx.core import AskUserEvent, ToolApprovalEvent

    from vx.commands import CommandsMixin as _CommandBase
else:
    # The real mixin is grafted onto the class on the first ``/`` (see
    # ``_graft_commands``); importing it here would cost ~600 ms of ctx.
    _CommandBase = object

MAX_INPUT_LINES = 8

#: Bound in ``run``: importing asyncio before the first frame would put ~85 ms
#: of module loading in front of the keyboard.
asyncio: Any = None


class _RunDone:
    pass


#: Classes whose command mixins are already grafted.
_GRAFTED: set[type] = set()


def _graft_commands(cls: type, mixin: type) -> None:
    """Copy a command mixin's methods onto ``cls``, first time only.

    Names the shell already defines win: ``CommandSupport`` declares stubs for
    ``_notice``/``_draw`` and friends, and those must not overwrite the real
    implementations.
    """
    if cls in _GRAFTED:
        return
    for name in dir(mixin):
        if name.startswith("__"):
            continue
        if name not in cls.__dict__:
            setattr(cls, name, getattr(mixin, name))
    _GRAFTED.add(cls)


class VxApp(_CommandBase):
    """The shell. Slash commands live in ``vx.commands`` and load on first use."""

    def __init__(
        self,
        *,
        cwd: str,
        model: str | None = None,
        provider: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        resume_session: str | None = None,
        continue_recent: bool = False,
        openai_compat_auth_mode: str | None = None,
        anthropic_compat_auth_mode: str | None = None,
        terminal: Terminal | None = None,
        painter: Painter | None = None,
        boot_args: Any = None,
    ) -> None:
        self.cwd = cwd
        #: argv for the deferred plugin boot (see ``_init_runtime``).
        self._boot_args = boot_args
        self._init_kwargs: dict[str, Any] = dict(
            model=model,
            provider=provider,
            api_key=api_key,
            base_url=base_url,
            resume_session=resume_session,
            continue_recent=continue_recent,
            openai_compat_auth_mode=openai_compat_auth_mode,
            anthropic_compat_auth_mode=anthropic_compat_auth_mode,
        )
        theme.apply_theme()
        # A caller can hand over a terminal it already put in raw mode and
        # painted (vx.tui.splash), so the shell is on screen before ctx loads.
        self.term = terminal or Terminal()
        self._term_ready = terminal is not None
        self.viewport = Viewport()
        # The scrollback is ours to bound: the alternate screen gives the
        # terminal none of its own, so this is vx's history-limit.
        with suppress(Exception):
            self.viewport.set_history_limit(prefs.history_rows())
        self.painter = painter or Painter()
        self.decoder = KeyDecoder()

        # Composer
        self._input = ""
        self._cursor = 0
        self._history: list[str] = []
        self._history_idx: int | None = None
        self._history_draft = ""
        #: Images the next prompt will carry, pasted or named with ``@``.
        self._attachments: list[attach.Attachment] = []
        #: The last killed text, so ctrl+y can bring it back.
        self._kill_ring = ""
        #: Composer edits, for ctrl+z.
        self._undo_stack: list[tuple[str, int]] = []

        # Agent
        self.runtime: ConversationRuntime | None = None
        self._loaded: Any = None
        self._is_running = False
        self._run_label = "working"
        self._run_started = 0.0
        self._cancel_event: asyncio.Event | None = None
        self._pending_queue: deque[str] = deque()
        #: Filled in ``run``: asyncio costs ~85 ms and none of it is needed to
        #: paint the frame or read a key.
        self._events: Any = None
        self._live_assistant: blocks.Assistant | None = None
        self._live_thinking: blocks.Thinking | None = None
        self._thinking_started: float | None = None
        self._tool_blocks: dict[str, blocks.ToolStatusBlock] = {}
        self._tool_started: dict[str, float] = {}
        self._tool_groups: dict[str, blocks.ToolGroup] = {}
        #: The group the running turn is still adding calls to. fx groups a
        #: run of calls, so anything else in the transcript ends it.
        self._live_group: blocks.ToolGroup | None = None
        #: The calls the running turn ran and finished, which its recap sums up.
        self._turn_calls: list[blocks.ToolStatusBlock] = []
        self._finished = 0
        self._turn_usage = {"in": 0, "out": 0}
        #: Rows the last frame gave the transcript, which bounds scrolling.
        self._body_rows = 0
        #: (first visible row, cursor it was paged for). The cursor travelling
        #: away means the window follows it again, so typing never pages you.
        self._input_scroll: tuple[int, int] | None = None
        self._last_ctrl_c = 0.0
        self._usage = {"in": 0, "out": 0}
        self._context_total: int | None = None
        self._context_used = 0
        self._stats_at = 0.0

        # Overlays
        self._approval: dict[str, Any] | None = None
        self._ask: dict[str, Any] | None = None
        self._menu: dict[str, Any] | None = None
        self._permissions_panel_scroll = 0
        self._prompt: dict[str, Any] | None = None
        self._activity: str = ""

        # Transcript search (ctrl+f). The query lives here; the matches and
        # highlight spans live on the viewport so a redraw cannot lose them.
        self._searching = False
        self._search = ""
        self._search_cursor = 0
        #: The last painted frame, for hit-testing OSC 8 links under the mouse.
        self._frame: list[str] = []

        self._exiting = False
        self._ticker_task: asyncio.Task | None = None
        #: Session ids already asked for a title, and the call that is running.
        self._titled: set[str] = set()
        self._title_task: asyncio.Task | None = None
        self._boot_task: asyncio.Task | None = None
        self._ready: Any = None

        # Permission runtime (vx-native, inspired by fx)
        self._permission_runtime = PermissionRuntime()

    # ==================================================================
    # lifecycle
    # ==================================================================

    async def run(self) -> int:
        global asyncio

        import asyncio

        self._events = asyncio.Queue()
        self._ready = asyncio.Event()
        loop = asyncio.get_running_loop()
        self._ticker_task = loop.create_task(self._ticker())
        try:
            await self._startup()
            await self._main_loop()
        finally:
            if self._ticker_task:
                self._ticker_task.cancel()
            if self._title_task is not None:
                self._title_task.cancel()
            if self._boot_task is not None:
                self._boot_task.cancel()
            if self.runtime is not None:
                with suppress(Exception):
                    await self.runtime.close()
            self.term.exit()
        return 0

    async def _startup(self) -> None:
        if not self._term_ready:
            self.term.enter()
        self.painter.reset()
        loop = asyncio.get_running_loop()
        loop.add_reader(self.term._fd, self._on_stdin)
        signal.signal(signal.SIGWINCH, lambda *_: self._on_resize())
        from vx.cli import trace

        trace("keyboard live")
        self._welcome()
        # Paint and start reading keys before the runtime loads: ctx's context
        # load (skills, MCP, model limits) costs over a second.
        self._draw(force=True)
        self._boot_task = loop.create_task(self._boot())

    def _prune_old_sessions(self) -> None:
        """Housekeeping in the background: done sessions cost nothing to keep.

        The session this process is using is protected by path, so a run that
        starts on an old transcript cannot have its own file pulled out from
        under it.
        """
        from ctx.core.session_retention import prune_in_background

        session = getattr(self.runtime, "session", None)
        path = getattr(session, "session_file", None)
        prune_in_background(protect=[path] if path is not None else [])

    def _load_prompt_history(self) -> None:
        """Fill the composer's history off the boot path.

        ``ctx.core`` exports the event types, so importing ``ctx.core.store`` to
        read 50 prompt strings pulls ctx.protocol and aiohttp first (~300 ms).
        Nothing needs the history until an arrow key is pressed.
        """
        from vx import history as prompt_history

        try:
            entries = prompt_history.load()
        except Exception:
            return
        self._history = entries + self._history if self._history else entries

    async def _boot(self) -> None:
        try:
            await self._init_runtime()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._notice(f"startup failed: {exc}", tone="error")
        finally:
            with suppress(Exception):
                self._prune_old_sessions()
            with suppress(Exception):
                self._load_prompt_history()
            from vx.cli import trace

            trace("runtime ready")
            self._ready.set()
            self.viewport.touch()
            if self._pending_queue and not self._is_running:
                self._enqueue_or_run(self._pending_queue.popleft())

    def _welcome(self) -> None:
        self.viewport.add(blocks.Welcome(version=VERSION))

    async def _init_runtime(self) -> None:
        # Everything ctx lives behind this point: the module graph alone costs
        # most of a second, and the frame above is already painted and reading
        # keys by the time this runs.
        if self._boot_args is not None:
            from vx.cli import BootError, boot_plugin_graph, trace

            try:
                boot_plugin_graph(self._boot_args, cwd=self.cwd)
            except BootError as exc:
                self._notice(f"startup failed: {exc}", tone="error")
                raise
            finally:
                trace("plugin graph loaded")

        from ctx.agent.agents import AgentRegistry, load_all_agents
        from ctx.agent.runtime import ConversationRuntime
        from ctx.core.config import config
        from ctx.plugins.extensions import load_for_runtime

        k = self._init_kwargs
        tools = vx.tools.register_all()
        try:
            self._loaded = load_for_runtime(cwd=self.cwd)
            for err in self._loaded.errors:
                self._notice(f"extension error: {err}", tone="warning")
            bus = self._loaded.bus
            ext_list = list(self._loaded.extensions)
        except Exception:
            self._loaded = None
            bus = None
            ext_list = []

        registry = AgentRegistry()
        with suppress(Exception):
            loaded_agents, agent_errors = load_all_agents(cwd=self.cwd)
            registry.agents = loaded_agents
            registry.errors = agent_errors
            for err in agent_errors:
                self._notice(f"agent error: {err}", tone="warning")

        self.runtime = ConversationRuntime(
            cwd=self.cwd,
            model=k["model"],
            model_provider=k["provider"],
            api_key=k["api_key"],
            base_url=k["base_url"],
            tools=tools,
            openai_compat_auth_mode=k["openai_compat_auth_mode"] or "auto",
            anthropic_compat_auth_mode=k["anthropic_compat_auth_mode"] or "auto",
            extensions=bus,
            agent_registry=registry,
            active_agent=registry.active,
            agent_extensions=ext_list,
        )
        if self._loaded is not None:
            with suppress(Exception):
                self.runtime.set_loaded_extensions(self._loaded)

        from vx.cli import trace

        trace("runtime constructed")
        result = self.runtime.initialize(
            resume_session=k["resume_session"], continue_recent=k["continue_recent"]
        )
        trace("runtime initialized")
        with suppress(Exception):
            # ctx's config carries the theme the user last chose; the splash
            # painted with the default before ctx was importable.
            theme.apply_named(config.ui.theme)
        with suppress(Exception):
            from ctx.ai.models import get_model

            info = get_model(
                getattr(self.runtime, "model", "") or "",
                getattr(self.runtime, "model_provider", None),
            )
            self._context_total = getattr(info, "context_window", None) if info else None
        if result.provider_error:
            self._notice(f"provider error: {result.provider_error}", tone="error")
        elif self.runtime.provider is None:
            self._notice(
                "no provider configured — set an API key or run `ctx auth` "
                "(vx shares the ctx config in ~/.ctx)",
                tone="warning",
            )
        else:
            self._render_session_history()

        with suppress(RuntimeError):
            asyncio.get_running_loop().create_task(self._connect_mcp())

    async def _connect_mcp(self) -> None:
        rt = self.runtime
        if rt is None:
            return
        try:
            tools = await rt.connect_mcp(startup_wait_seconds=5.0)
            if tools:
                self._notice(f"{len(tools)} MCP tools connected")
        except Exception:
            pass

    # -- history -------------------------------------------------------

    def _render_session_history(self) -> None:
        session = self.runtime.session if self.runtime else None
        if session is None:
            return
        # ctx replays every active entry, so a resumed session shows the whole
        # conversation; clipping it here was what hid older turns.
        self._render_history_messages(list(session.messages))

    def _render_history_messages(self, msgs: list[Any]) -> None:
        """Replay a session's messages as the live transcript would draw them.

        fx projects a whole session through the same grouping it uses while
        running, so an old turn reads like a new one: a run of calls shares a
        header and its results, instead of each call standing alone.
        """
        group: blocks.ToolGroup | None = None
        by_call_id: dict[str, blocks.ToolStatusBlock] = {}
        from ctx.protocol.types import (
            AssistantMessage,
            TextContent,
            ThinkingContent,
            ToolCall,
            ToolResultMessage,
            UserMessage,
        )

        for msg in msgs:
            if isinstance(msg, UserMessage):
                group = None
                content = msg.content
                if isinstance(content, str):
                    text = content
                    images = 0
                else:
                    text = " ".join(p.text for p in content if isinstance(p, TextContent))
                    images = sum(1 for p in content if type(p).__name__ == "ImageContent")
                if text.strip() or images:
                    chip = f"{images} image{'s' if images != 1 else ''}" if images else ""
                    self.viewport.add(blocks.UserTurn(text=text, attachments=chip))
                continue
            if isinstance(msg, AssistantMessage):
                thinking = "".join(
                    p.thinking for p in msg.content if isinstance(p, ThinkingContent)
                )
                text = "".join(p.text for p in msg.content if isinstance(p, TextContent))
                # ctx replays thinking above the reply; keeping it means a
                # resumed session still carries the reasoning, not just the
                # answer (ctrl+o prints it).
                if thinking.strip():
                    # Replayed reasoning is already finished, so it arrives folded.
                    self.viewport.add(blocks.Thinking(text=thinking, streaming=False))
                    group = None
                if text.strip():
                    self.viewport.add(blocks.Assistant(text=text))
                    group = None
                for call in (p for p in msg.content if isinstance(p, ToolCall)):
                    block = _make_tool_status_block(call.name)
                    _populate_tool_block_args(block, call.name, call.arguments)
                    if call.name == "apply_patch":
                        # Standalone, so an old turn groups calls the way a
                        # live one does.
                        self.viewport.add(blocks.ToolGroup(calls=[block]))
                        group = None
                    else:
                        if group is None:
                            group = blocks.ToolGroup()
                            self.viewport.add(group)
                        group.calls.append(block)
                    by_call_id[call.id] = block
                continue
            if isinstance(msg, ToolResultMessage):
                block = by_call_id.get(msg.tool_call_id)
                if block is None:
                    continue
                block.status = "error" if msg.is_error else "ok"
                block.finished = len(by_call_id)
                if isinstance(block, blocks.BashToolStatus):
                    _apply_exit_status(block, msg)

    def _render_history_message(self, msg: Any) -> None:
        self._render_history_messages([msg])

    # ==================================================================
    # input
    # ==================================================================

    def _on_stdin(self) -> None:
        data = self.term.read_available()
        if not data:
            return
        try:
            events = self.decoder.feed(data)
        except Exception:
            return
        for ev in events:
            with suppress(Exception):
                self._handle_key(ev)

    def _on_resize(self) -> None:
        self.painter.reset()
        with suppress(Exception):
            self._draw(force=True)

    def _mouse_key(self, key: str) -> None:
        """The wheel scrolls; a left click opens an OSC 8 link if one is there."""
        parts = key.split(":")
        action = parts[1] if len(parts) > 1 else ""
        if action in ("wheelup", "wheeldown"):
            step = 3 if action == "wheelup" else -3
            self.viewport.scroll_by(step, *self._size())
            self._draw()
            return
        if action in ("left", "click") and len(parts) >= 4:
            with suppress(Exception):
                x, y = int(parts[2]), int(parts[3])
                if 1 <= y <= len(self._frame):
                    uri = ansi.link_at(self._frame[y - 1], x - 1)
                    if uri:
                        self._open_link(uri)

    @staticmethod
    def _open_link(uri: str) -> None:
        """Open a hyperlink off the event loop; a slow browser must not stall keys."""
        import threading
        import webbrowser

        def go() -> None:
            with suppress(Exception):
                webbrowser.open(uri)

        threading.Thread(target=go, daemon=True).start()

    def _handle_key(self, key: str) -> None:
        if self._exiting:
            return
        if self._menu is not None:
            self._menu_key(key)
            return
        if self._approval is not None:
            self._approval_key(key)
            return
        if self._ask is not None:
            self._ask_key(key)
            return
        if self._searching:
            self._search_key(key)
            return
        if key == "ctrl+f" and self._prompt is None:
            self._open_search()
            return
        if (
            self.viewport.search
            and self._prompt is None
            and not self._input
            and key in ("n", "N", "ctrl+n", "ctrl+p")
        ):
            step = 1 if key in ("n", "ctrl+n") else -1
            self.viewport.search_next(step, *self._size())
            self._draw()
            return
        if key.startswith("mouse:"):
            self._mouse_key(key)
            return
        if key == "tab" and not self._input:
            self._toggle_thinking_level(1)
            return
        if key == "shift+tab" and not self._input:
            self._toggle_permission_mode(1)
            return

        if key == "esc" and self._prompt is not None:
            self._prompt = None
            self._input = ""
            self._cursor = 0
            return
        if key == "esc":
            if self._is_running:
                self._interrupt()
            elif self._input:
                self._input = ""
                self._cursor = 0
            self._last_ctrl_c = 0.0
            return
        if key == "ctrl+c":
            now = time.monotonic()
            if self._is_running:
                self._interrupt()
                self._last_ctrl_c = 0.0
            elif self._input:
                self._input = ""
                self._cursor = 0
                self._last_ctrl_c = 0.0
            elif now - self._last_ctrl_c < 2.0:
                self._exiting = True
            else:
                self._last_ctrl_c = now
            return
        if key == "ctrl+d":
            if not self._input:
                self._exiting = True
            return
        if key == "ctrl+o":
            blocks.set_expanded(not blocks.expanded())
            self.viewport.touch()
            return
        # ``shift+pageup``/``ctrl+pageup`` mean the same thing in every
        # terminal that sends them; only the bare key is guaranteed to exist.
        base_key = key.rsplit("+", 1)[-1]
        if base_key in ("pageup", "pagedown") or key in ("ctrl+up", "ctrl+down"):
            step = -1 if base_key == "pageup" or key == "ctrl+up" else 1
            # A composer taller than its window takes the page keys first.
            if not self._page_input(step):
                self.viewport.page(-step, *self._size())
            return
        if key == "end":
            self.viewport.bottom()
            return
        if key == "enter":
            self._submit()
            return
        if key == "alt+enter":
            self._insert("\n")
            return
        if key == "backspace":
            self._backspace()
            return
        if key == "delete":
            self._delete()
            return
        if key == "left":
            self._cursor = max(0, self._cursor - 1)
            return
        if key == "right":
            self._cursor = min(len(self._input), self._cursor + 1)
            return
        if key == "home" or key == "ctrl+a":
            self._cursor = 0
            return
        if key == "ctrl+e":
            self._cursor = len(self._input)
            return
        if key == "ctrl+u":
            # Kill to the start of the line, like readline.
            self._input = self._input[self._cursor :]
            self._cursor = 0
            self._refresh_slash_menu()
            self._refresh_atpath_menu()
            return
        if key == "ctrl+k":
            self._input = self._input[: self._cursor]
            self._refresh_slash_menu()
            self._refresh_atpath_menu()
            return
        if key == "ctrl+w":
            start = self._word_start()
            self._kill(self._input[start : self._cursor])
            self._input = self._input[:start] + self._input[self._cursor :]
            self._cursor = start
            self._after_edit()
            return
        if key in ("alt+d", "ctrl+delete"):
            end = self._word_end()
            self._undo_push()
            self._kill(self._input[self._cursor : end])
            self._input = self._input[: self._cursor] + self._input[end:]
            self._after_edit()
            return
        if key in ("ctrl+left", "alt+b"):
            self._cursor = self._word_start()
            return
        if key in ("ctrl+right", "alt+f"):
            self._cursor = self._word_end()
            return
        if key == "ctrl+y":
            if self._kill_ring:
                self._undo_push()
                self._insert(self._kill_ring)
            return
        if key == "alt+y":
            self._cycle_attachments()
            return
        if key == "ctrl+z":
            self._undo_pop()
            return
        if key == "ctrl+v":
            self._paste_image()
            return
        if key == "ctrl+x":
            self._drop_attachment()
            return
        if key == "ctrl+r":
            self._open_history_search()
            return
        if key == "up":
            # Inside a multi-line prompt the arrows move between its lines; the
            # prompt history sits one row above the first line and one below the
            # last, so a half-written message is never replaced mid-way.
            if self._move_input_line(-1):
                return
            if self._input:
                self._history_move(-1)
            elif self._is_running and self._pending_queue:
                self._recall_queued()
            else:
                self.viewport.page(1, *self._size())
            return
        if key == "down":
            if self._move_input_line(1):
                return
            if self._history_idx is not None:
                self._history_move(1)
            else:
                self.viewport.page(-1, *self._size())
            return
        if key == "tab":
            self._complete()
            return
        if key.startswith("paste:"):
            self._insert(key[6:].replace("\r\n", "\n").replace("\r", "\n"))
            return
        if key.startswith("alt+") and len(key) > 4:
            self._insert(key[4:])
            return
        if len(key) == 1 and ord(key) >= 32:
            self._insert(key)
            self._refresh_slash_menu()
            self._refresh_atpath_menu()

    # -- transcript search ------------------------------------------------

    def _open_search(self) -> None:
        self._searching = True
        self._search_cursor = len(self._search)
        if self.viewport.search_matches:
            self.viewport.search_next(0, *self._size())

    def _set_search(self, text: str, cursor: int | None = None) -> None:
        self._search = text
        self._search_cursor = len(text) if cursor is None else max(0, min(len(text), cursor))
        cols, _ = self._size()
        self.viewport.set_search(text, cols)
        self._draw()

    def _search_key(self, key: str) -> None:
        if key in ("esc", "ctrl+f"):
            self._searching = False
            self._draw()
            return
        if key == "enter":
            if self.viewport.search_matches:
                self.viewport.search_next(0, *self._size())
            self._searching = False
            self._draw()
            return
        if key in ("ctrl+n", "ctrl+p"):
            step = 1 if key == "ctrl+n" else -1
            self.viewport.search_next(step, *self._size())
            self._draw()
            return
        text, cursor = self._search, self._search_cursor
        if key in ("left", "ctrl+b"):
            self._search_cursor = max(0, cursor - 1)
            return
        if key == "right":
            self._search_cursor = min(len(text), cursor + 1)
            return
        if key in ("home", "ctrl+a"):
            self._search_cursor = 0
            return
        if key in ("end", "ctrl+e"):
            self._search_cursor = len(text)
            return
        if key == "backspace" and cursor:
            self._set_search(text[: cursor - 1] + text[cursor:], cursor - 1)
            return
        if key == "delete" and cursor < len(text):
            self._set_search(text[:cursor] + text[cursor + 1 :], cursor)
            return
        if key == "ctrl+u":
            self._set_search(text[cursor:], 0)
            return
        if key == "ctrl+k":
            self._set_search(text[:cursor], cursor)
            return
        if key.startswith("paste:"):
            inserted = key[6:].replace("\r\n", "\n").replace("\r", "\n")
            self._set_search(text[:cursor] + inserted + text[cursor:], cursor + len(inserted))
            return
        if len(key) == 1 and ord(key) >= 32:
            self._set_search(text[:cursor] + key + text[cursor:], cursor + 1)

    def _size(self) -> tuple[int, int]:
        """The scrolling area: terminal columns and the transcript's rows.

        The footer's height changes with the composer, menus and the approval
        panel, so a scroll bound taken from the terminal height alone lets the
        window drift past the top of the transcript and hide its first rows.
        """
        rows, cols = self.term.size()
        return cols, self._body_rows or max(1, rows - 1)

    # -- composer editing ------------------------------------------------

    def _insert(self, text: str) -> None:
        self._undo_push()
        self._input = self._input[: self._cursor] + text + self._input[self._cursor :]
        self._cursor += len(text)
        self._history_idx = None

    def _backspace(self) -> None:
        if self._cursor > 0:
            self._undo_push()
            self._input = self._input[: self._cursor - 1] + self._input[self._cursor :]
            self._cursor -= 1
        self._refresh_slash_menu()

    def _delete(self) -> None:
        if self._cursor < len(self._input):
            self._undo_push()
            self._input = self._input[: self._cursor] + self._input[self._cursor + 1 :]
        self._refresh_slash_menu()

    def _input_window(self) -> tuple[int, int, int] | None:
        """``(first row, visible rows, total rows)`` when the composer overflows."""
        _, cols = self.term.size()
        total, _, follow = footer.composer_window(self._input, self._cursor, cols, MAX_INPUT_LINES)
        if total <= MAX_INPUT_LINES:
            return None
        pinned = self._input_scroll
        first = pinned[0] if pinned is not None and pinned[1] == self._cursor else follow
        first = max(0, min(first, total - MAX_INPUT_LINES))
        return first, MAX_INPUT_LINES, total

    def _move_input_line(self, delta: int) -> bool:
        """Move the caret a visual line inside the composer.

        Returns ``False`` at the first or last line, where the arrow keys go
        back to meaning history and transcript scrolling.
        """
        if "\n" not in self._input:
            return False
        _, cols = self.term.size()
        rows = footer.wrap_input(self._input, cols)
        row, col = footer.cursor_visual(self._input, self._cursor, cols)
        target = row + delta
        if target < 0 or target >= len(rows):
            return False
        start, end = rows[target]
        self._cursor = start + min(col, end - start)
        self._input_scroll = None
        return True

    def _page_input(self, direction: int) -> bool:
        """Page the composer when it holds more lines than it shows.

        Returns ``False`` when it fits, so the page keys fall through to the
        transcript and keep their usual meaning.
        """
        rows, cols = self.term.size()
        del rows
        total, _, follow = footer.composer_window(self._input, self._cursor, cols, MAX_INPUT_LINES)
        visible = max(1, min(MAX_INPUT_LINES, total))
        if total <= visible:
            return False
        pinned = self._input_scroll
        first = pinned[0] if pinned is not None and pinned[1] == self._cursor else follow
        step = max(1, visible - 1)
        last = total - visible
        self._input_scroll = (max(0, min(last, first + direction * step)), self._cursor)
        return True

    def _recall_queued(self) -> None:
        """up on an empty composer takes back the last queued prompt.

        A prompt typed mid-turn is only visible as a count, so without this the
        only way to fix a typo is to wait for the turn to end.
        """
        if not self._pending_queue:
            return
        prompt = self._pending_queue.pop()
        self._input = prompt
        self._cursor = len(prompt)
        self._history_idx = None
        remaining = len(self._pending_queue)
        self._notice(
            "took back the queued prompt"
            if not remaining
            else f"took back the queued prompt · {remaining} still queued"
        )

    def _history_move(self, delta: int) -> None:
        if not self._history:
            return
        if self._history_idx is None:
            if delta > 0:
                return
            self._history_draft = self._input
            self._history_idx = len(self._history) - 1
        else:
            new = self._history_idx + delta
            if new >= len(self._history):
                self._history_idx = None
                self._input = self._history_draft
                self._cursor = len(self._input)
                return
            self._history_idx = max(0, new)
        self._input = self._history[self._history_idx]
        self._cursor = len(self._input)

    # -- composer helpers ------------------------------------------------

    def _word_start(self) -> int:
        """Where the word before the caret begins, as ctrl+left and ctrl+w move."""
        index = self._cursor
        while index > 0 and self._input[index - 1].isspace():
            index -= 1
        while index > 0 and not self._input[index - 1].isspace():
            index -= 1
        return index

    def _word_end(self) -> int:
        """Where the word after the caret ends, as ctrl+right and alt+d move."""
        index = self._cursor
        while index < len(self._input) and self._input[index].isspace():
            index += 1
        while index < len(self._input) and not self._input[index].isspace():
            index += 1
        return index

    def _kill(self, text: str) -> None:
        """Keep killed text for ctrl+y, the way readline does."""
        self._kill_ring = text

    def _undo_push(self) -> None:
        """Remember the composer before an edit, for ctrl+z."""
        state = (self._input, self._cursor)
        if self._undo_stack and self._undo_stack[-1] == state:
            return
        self._undo_stack.append(state)
        if len(self._undo_stack) > 200:
            del self._undo_stack[:100]

    def _undo_pop(self) -> None:
        if not self._undo_stack:
            return
        self._input, self._cursor = self._undo_stack.pop()
        self._history_idx = None
        self._after_edit()

    def _after_edit(self) -> None:
        """Re-derive the pickers after the composer changed."""
        self._history_idx = None
        self._refresh_slash_menu()
        self._refresh_atpath_menu()

    def _paste_image(self) -> None:
        """ctrl+v: attach the clipboard's image, or paste its text."""
        image = attach.from_clipboard()
        if image is None:
            text = attach.clipboard_text()
            if text:
                self._insert(text.replace("\r\n", "\n").replace("\r", "\n"))
                self._after_edit()
                return
            self._notice("clipboard is empty", tone="warning")
            return
        self._attachments.append(image)
        self._activity = f"attached {image.label}"
        self.viewport.touch()

    def _drop_attachment(self) -> None:
        if not self._attachments:
            return
        gone = self._attachments.pop()
        self._activity = f"dropped {gone.label}"
        self.viewport.touch()

    def _cycle_attachments(self) -> None:
        """Say what is attached, since a chip can hide behind a long prompt."""
        if not self._attachments:
            return
        self._notice(attach.describe(self._attachments))

    def _open_history_search(self) -> None:
        """ctrl+r: recall an earlier prompt, filtered as you type."""
        if not self._history:
            self._notice("no prompt history yet")
            return
        items = [
            footer.MenuItem(label=text.split("\n")[0], value=text)
            for text in reversed(self._history)
        ]
        self._menu = {
            "kind": "history",
            "items": items,
            "selected": 0,
            "noun": "History",
            "type_hint": True,
            "filterable": True,
            "variants": footer.HISTORY_HINT_VARIANTS,
            "on_select": self._use_history_item,
        }

    def _use_history_item(self, item: footer.MenuItem) -> None:
        self._undo_push()
        self._input = item.value or item.label
        self._cursor = len(self._input)
        self._after_edit()

    def _refresh_slash_menu(self) -> None:
        # A text prompt owns the composer; typing / there is not a command.
        if self._prompt is not None:
            return
        text = self._input
        if not text.startswith("/") or " " in text:
            if self._menu is not None and self._menu["kind"] == "slash":
                self._menu = None
            return
        prefix = text[1:].lower()
        commands, skills = self._slash_matches(prefix)
        items = commands + skills
        if not items:
            self._menu = None
            return
        current = self._menu.get("selected", 0) if self._menu else 0
        self._menu = {
            "kind": "slash",
            "items": items,
            "selected": min(current, len(items) - 1),
            "noun": "Commands" if len(commands) == len(items) else "Results",
            "type_hint": text == "/",
            "variants": footer.MENU_HINT_VARIANTS,
        }

    def _refresh_atpath_menu(self) -> None:
        # Don't clobber an active slash menu.
        if self._menu is not None and self._menu.get("kind") == "slash":
            return
        parsed = parse_at(self._input, self._cursor)
        if parsed is None:
            if self._menu is not None and self._menu.get("kind") == "atpath":
                self._menu = None
            return
        _at_offset, _replace_end, query = parsed
        items = [
            footer.MenuItem(
                label=candidate,
                metadata="dir" if os.path.isdir(os.path.join(self.cwd, candidate)) else "file",
            )
            for candidate in list_candidates(self.cwd, query)
        ]
        if not items:
            if self._menu is not None and self._menu.get("kind") == "atpath":
                self._menu = None
            return
        current = (
            self._menu.get("selected", 0)
            if (self._menu and self._menu.get("kind") == "atpath")
            else 0
        )
        self._menu = {
            "kind": "atpath",
            "items": items,
            "selected": min(current, len(items) - 1),
            "noun": "Files",
            "type_hint": False,
            "variants": footer.MENU_HINT_VARIANTS,
            "at_offset": _at_offset,
            "replace_end": _replace_end,
            "query": query,
        }

    def _slash_matches(self, prefix: str) -> tuple[list[footer.MenuItem], list[footer.MenuItem]]:
        """Commands and skills that match ``prefix``, ranked like fx.

        fx puts name matches above prose matches: prefix, then substring, then
        subsequence, and only then anything found in a skill's description,
        source or path. Commands and skills share the one ranked list.
        """
        ranked: list[tuple[int, int, int, footer.MenuItem]] = []
        # The command table lives in vx.commands, which is imported lazily.
        from vx.commands import SLASH_COMMANDS

        for order, (name, description, category) in enumerate(SLASH_COMMANDS):
            stem = name[1:].lower()
            rank = _name_rank(prefix, stem)
            if rank is None:
                rank = 5 if prefix in description.lower() else None
            if rank is None:
                continue
            ranked.append((rank, 0, order, footer.MenuItem(name, description, category)))

        context = getattr(self.runtime, "context", None) if self.runtime else None
        skills = sorted(
            getattr(context, "skills", None) or [], key=lambda sk: getattr(sk, "name", "").lower()
        )
        for order, skill in enumerate(skills):
            name = getattr(skill, "name", "")
            if not name:
                continue
            description = getattr(skill, "description", "") or ""
            path = getattr(skill, "path", "") or ""
            source = _skill_source(path, self.cwd)
            rank = _name_rank(prefix, name.lower())
            if rank is None:
                prose = f"{description}\n{source}\n{path}".lower()
                rank = 4 if prefix in prose else None
            if rank is None:
                continue
            label = f"/{name}" if getattr(skill, "register_cmd", False) else name
            ranked.append((rank, 1, order, footer.MenuItem(label, description, source)))

        ranked.sort(key=lambda row: (row[0], row[1], row[2]))
        items = [row[3] for row in ranked]
        command_count = sum(1 for row in ranked if row[1] == 0)
        return items[:command_count], items[command_count:]

    def _complete(self) -> None:
        if self._menu is None:
            return
        item = self._menu["items"][self._menu["selected"]]
        if self._menu["kind"] == "slash":
            self._input = item.label + " "
            self._cursor = len(self._input)
            self._menu = None
        elif self._menu["kind"] == "atpath":
            m = self._menu
            at_offset = m["at_offset"]
            replace_end = m["replace_end"]
            chosen = item.label
            self._input = self._input[:at_offset] + "@" + chosen + " " + self._input[replace_end:]
            self._cursor = at_offset + 1 + len(chosen) + 1
            self._menu = None

    def _menu_view(self) -> list[footer.MenuItem]:
        """The rows the picker currently shows: filtered when it is filterable."""
        menu = self._menu
        if menu is None:
            return []
        items: list[footer.MenuItem] = menu["items"]
        query = str(menu.get("query") or "").strip()
        if not query or not menu.get("filterable"):
            return items
        # ctx's fuzzy matcher scores subsequence hits and word starts, so a
        # picker finds "/cmpct" or "ssion" the way a reader types them.
        with suppress(Exception):
            from ctx.term.fuzzy import fuzzy_filter

            return fuzzy_filter(items, query, lambda item: f"{item.label} {item.detail}")
        needle = query.lower()
        return [
            item
            for item in items
            if needle in f"{item.label} {item.detail} {item.metadata}".lower()
        ]

    def _menu_key(self, key: str) -> None:
        menu = self._menu
        if menu is None:
            return
        kind = menu.get("kind")
        if key in ("esc", "ctrl+c"):
            if kind == "slash":
                self._input = ""
                self._cursor = 0
            self._menu = None
            return
        if kind == "permissions":
            if key == "up":
                self._permissions_panel_scroll = max(0, self._permissions_panel_scroll - 1)
            elif key == "down":
                self._permissions_panel_scroll += 1
            elif key == "ctrl+o":
                blocks.set_expanded(not blocks.expanded())
                self.viewport.touch()
            return
        if key == "up":
            menu["selected"] = (menu["selected"] - 1) % max(1, len(self._menu_view()))
            return
        if key == "down":
            menu["selected"] = (menu["selected"] + 1) % max(1, len(self._menu_view()))
            return
        if kind == "slash" and key == "tab":
            self._complete()
            return
        if kind == "mode":
            # Mode picker has its own navigation
            if key in ("enter", "right"):
                self._menu_choose_mode()
                return
            if key == "up":
                view = menu.get("view")
                if view:
                    menu["selected"] = (menu["selected"] - 1) % len(view.modes)
                return
            if key == "down":
                view = menu.get("view")
                if view:
                    menu["selected"] = (menu["selected"] + 1) % len(view.modes)
                return
            if key == "ctrl+o":
                blocks.set_expanded(not blocks.expanded())
                self.viewport.touch()
            return
        if key in ("enter", "right"):
            self._menu_choose()
            return
        if kind == "atpath":
            # The @ token lives in the composer, so typing keeps editing it and
            # the file picker follows the text.
            if key.startswith("paste:"):
                self._insert(key[6:])
            elif key == "backspace":
                self._backspace()
            elif key == "left" or key == "right":
                key = "right"
            elif len(key) == 1 and ord(key) >= 32:
                self._insert(key)
            else:
                self._menu = None
                self._handle_key(key)
                return
            self._refresh_atpath_menu()
            return
        if key == "backspace":
            if kind == "slash":
                self._backspace()
                self._refresh_slash_menu()
                self._refresh_atpath_menu()
                return
            if menu.get("filterable"):
                menu["query"] = str(menu.get("query") or "")[:-1]
                menu["selected"] = 0
                return
            return
        if kind == "slash":
            if key.startswith("paste:"):
                self._insert(key[6:])
            elif len(key) == 1 and ord(key) >= 32:
                self._insert(key)
            elif key == "ctrl+o":
                blocks.set_expanded(not blocks.expanded())
                self.viewport.touch()
                return
            # The menu follows the text, including the space that ends it.
            self._refresh_slash_menu()
            self._refresh_atpath_menu()
            return
        if menu.get("filterable") and (len(key) == 1 and ord(key) >= 32):
            menu["query"] = str(menu.get("query") or "") + key
            menu["selected"] = 0
            return
        # Anything else closes a non-filterable picker and types as usual.
        self._menu = None
        self._handle_key(key)

    def _menu_choose(self) -> None:
        menu = self._menu
        if menu is None:
            return
        view = self._menu_view()
        if not view:
            self._menu = None
            return
        item = view[max(0, min(int(menu["selected"]), len(view) - 1))]
        on_select = menu.get("on_select")
        kind = menu.get("kind")
        self._menu = None
        if on_select is not None:
            on_select(item)
            return
        if kind in ("slash", "help"):
            if item.label.startswith("/"):
                self._input = item.label
                self._cursor = len(self._input)
                self._submit()
            else:
                # A skill completes as a plain name, as fx inserts it.
                self._input = ""
                self._cursor = 0
                self._insert(item.label + " ")
            return
        if kind == "atpath":
            at_offset = int(menu.get("at_offset") or 0)
            replace_end = int(menu.get("replace_end") or 0)
            chosen = item.label
            token = f'@"{chosen}"' if " " in chosen else f"@{chosen}"
            before = self._input[:at_offset]
            after = self._input[replace_end:].lstrip()
            self._input = f"{before}{token} {after}"
            self._cursor = len(before) + len(token) + 1

    def _toggle_thinking_level(self, step: int) -> None:
        """Cycle thinking levels: none → minimal → low → medium → high → none ..."""
        rt = self.runtime
        if rt is None:
            return
        try:
            levels = list(rt.effective_thinking_levels)
        except Exception:
            return
        if not levels:
            return
        current = rt.thinking_level or ""
        idx = levels.index(current) if current in levels else 0
        new_idx = (idx + step) % len(levels)
        new_level = levels[new_idx]
        rt.set_thinking_level(new_level)

    def _toggle_permission_mode(self, step: int) -> None:
        order = [spec.id for spec in BUILTIN_MODES]
        old_mode = self._permission_runtime.mode
        index = order.index(old_mode) if old_mode in order else 0
        new_mode = order[(index + step) % len(order)]
        self._permission_runtime.set_mode(new_mode)
        from vx.commands.settings import _sync_mode_to_config

        _sync_mode_to_config(new_mode)

    def open_permissions_panel(self) -> None:
        mode = next(
            (spec for spec in BUILTIN_MODES if spec.id == self._permission_runtime.mode), None
        )
        self._menu = {
            "kind": "permissions",
            "selected": 0,
            "filterable": False,
            "view": footer.PermissionsView(
                mode_name=mode.name if mode else self._permission_runtime.mode,
                description=mode.description if mode else "",
                grants=[
                    f"{grant.tool_name} {grant.target_path}"
                    for grant in self._permission_runtime.grants
                ],
                rules=[],
            ),
        }
        self._permissions_panel_scroll = 0
        self._draw()

    def _menu_choose_mode(self) -> None:
        """Handle selecting a mode from the mode picker."""
        menu = self._menu
        if menu is None:
            return
        view = menu.get("view")
        perm_runtime = menu.get("perm_runtime")
        if view is None or perm_runtime is None:
            self._menu = None
            return
        selected_idx = max(0, min(menu["selected"], len(view.modes) - 1))
        mode_item = view.modes[selected_idx]
        mode_id = mode_item.value or mode_item.label
        # Normalize label to mode id
        label_map = {"Ask": "ask", "Auto": "auto"}
        mode_id = label_map.get(mode_id, mode_id.lower())
        perm_runtime.set_mode(mode_id)
        self._menu = None
        from vx.commands.settings import _sync_mode_to_config

        _sync_mode_to_config(mode_id)

    # -- submit ----------------------------------------------------------

    def _submit(self) -> None:
        if self._prompt is not None:
            prompt = self._prompt
            self._prompt = None
            raw = self._input
            self._input = ""
            self._cursor = 0
            callback = prompt.get("callback")
            if callback is not None:
                callback(raw)
            return
        text = self._input.strip()
        # An image named with @ rides along even when it was never pasted.
        for path in attach.image_paths_in(text, self.cwd):
            if all(a.name != os.path.basename(path) for a in self._attachments):
                found = attach.from_path(path, self.cwd)
                if found is not None:
                    self._attachments.append(found)
        images = [a.content() for a in self._attachments]
        attached = attach.describe(self._attachments)
        self._input = ""
        self._cursor = 0
        self._menu = None
        self._attachments = []
        self._undo_stack.clear()
        if not text:
            return
        self._history = prompt_history.append(text, self._history)
        self._input_scroll = None
        self._history_idx = None
        if text.startswith("/"):
            # fx never echoes a command as a prompt; it acts on it.
            self._handle_slash(text)
            return
        if text.startswith("!"):
            self._handle_shell(text)
            return
        self._break_group()
        # Sending a prompt means "show me the answer", so jump to the tail.
        self.viewport.bottom()
        self.viewport.add(blocks.UserTurn(text=text, attachments=attached))
        self._enqueue_or_run(expand_paths(text, self.cwd), images=images)

    # ==================================================================
    # slash commands & shell
    # ==================================================================

    def _handle_slash(self, text: str) -> bool:
        """Slash commands live in ``vx.commands``; this is the entry point.

        Importing that package pulls ctx's config, provider catalog and aiohttp
        (~600 ms here), so it happens on the first ``/`` and not at boot. The
        mixin's methods are copied onto the class once, after which dispatch is
        plain attribute lookup.
        """
        from vx.commands import CommandsMixin

        _graft_commands(type(self), CommandsMixin)
        return CommandsMixin.handle_slash(self, text)

    async def _compact(self) -> None:
        rt = self.runtime
        if rt is None:
            return
        try:
            await rt.compact_now()
            self._notice("context compacted")
        except Exception as e:
            self._notice(f"compact failed: {e}", tone="error")

    def _handle_shell(self, text: str) -> None:
        send_to_llm = text.startswith("!!")
        command = (text[2:] if send_to_llm else text[1:]).strip()
        if not command:
            return
        block = blocks.ShellRun(command=command, send_to_llm=send_to_llm)
        self._break_group()
        self.viewport.add(block)
        self._is_running = True
        self._run_label = "running command"
        self._run_started = time.monotonic()
        asyncio.get_running_loop().create_task(self._run_shell(block, command, send_to_llm))

    async def _run_shell(self, block: blocks.ShellRun, command: str, send_to_llm: bool) -> None:
        from ctx.agent.tools import get_tool

        bash = get_tool("bash")
        if bash is None:
            block.output = "no bash tool registered"
            block.success = False
            block.done = True
            self._is_running = False
            return
        cancel = asyncio.Event()
        self._cancel_event = cancel
        try:
            params = bash.params(command=command)
            result = await bash.execute(params, cancel_event=cancel)
            block.output = getattr(result, "result", "") or ""
            block.success = bool(getattr(result, "success", True))
            session = self.runtime.session if self.runtime else None
            if session is not None:
                with suppress(Exception):
                    session.append_custom_message(
                        "shell_command",
                        f"{'!!' if send_to_llm else '!'}{command}",
                        details={
                            "command": command,
                            "output": block.output,
                            "success": block.success,
                        },
                    )
            block.done = True
            if send_to_llm and block.output and not cancel.is_set():
                self._enqueue_or_run(
                    "Shell command output:\n\n```\n"
                    + block.output
                    + "\n```\n\nWhat would you like me to do with this?"
                )
        except Exception as e:
            block.output = f"error: {e}"
            block.success = False
            block.done = True
        finally:
            self._is_running = False
            self._cancel_event = None

    # ==================================================================
    # agent runs
    # ==================================================================

    def _enqueue_or_run(self, prompt: str, images: list[Any] | None = None) -> None:
        if self._is_running:
            self._pending_queue.append(prompt)
            self._notice(f"queued · {len(self._pending_queue)} pending")
            return
        self._is_running = True
        self._run_label = "working"
        self._run_started = time.monotonic()
        self._cancel_event = asyncio.Event()
        self._live_assistant = None
        self._live_thinking = None
        self._thinking_started = None
        agent = self.runtime.prepare_for_run() if self.runtime else None
        if agent is None:
            self._notice("agent not initialized", tone="error")
            self._is_running = False
            return
        # Reuse one hook per run; prepare_for_run may return the same Agent.
        from vx.hook import VxPermissionHook

        agent._hooks[:] = [hook for hook in agent._hooks if not isinstance(hook, VxPermissionHook)]
        agent._hooks.append(VxPermissionHook(self._permission_runtime))
        # Sync vx's permission mode to ctx config so check_permission() respects it
        try:
            from ctx.core.config import config as ctx_config

            perms = getattr(ctx_config, "permissions", None)
            if perms is not None:
                mode_map = {"ask": "prompt", "auto": "auto"}
                perms.mode = mode_map.get(self._permission_runtime.mode, "prompt")
        except Exception:
            pass
        asyncio.get_running_loop().create_task(self._run_agent(agent, prompt, images))

    async def _run_agent(self, agent: Any, prompt: str, images: list[Any] | None = None) -> None:
        try:
            async for event in agent.run(
                prompt, images=images or None, cancel_event=self._cancel_event, steer_event=None
            ):
                self._events.put_nowait(event)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            from ctx.core import ErrorEvent

            self._events.put_nowait(ErrorEvent(error=str(e)))
        finally:
            self._events.put_nowait(_RunDone())

    def _interrupt(self) -> None:
        if self._cancel_event is not None:
            self._cancel_event.set()
            self._run_label = "interrupting"

    # ==================================================================
    # events → blocks
    # ==================================================================

    def _notice(
        self, body: str, *, topic: str = "", tone: str = "information", detail: str = ""
    ) -> None:
        self._break_group()
        self.viewport.add(blocks.Notice(topic=topic, body=body, tone=tone, detail=detail))

    def _break_group(self) -> None:
        """Ends the live tool group, so the next call starts a new one."""
        self._live_group = None

    def _drain_events(self) -> None:
        changed = False
        while True:
            try:
                ev = self._events.get_nowait()
            except asyncio.QueueEmpty:
                break
            self._handle_event(ev)
            changed = True
        if changed:
            self.viewport.touch()

    def _assistant_block(self) -> blocks.Assistant:
        if self._live_assistant is None:
            self._live_assistant = blocks.Assistant(text="")
            self.viewport.add(self._live_assistant)
        return self._live_assistant

    def _handle_event(self, ev: Any) -> None:
        # ctx.core carries the event types and, through it, ctx.protocol and
        # aiohttp: ~500 ms of imports. During boot nothing is dispatched, so
        # the import happens here, on the first event of the first turn.
        from ctx.core import (
            AgentEndEvent,
            AskUserEvent,
            BackgroundTaskCompletedEvent,
            CompactionEndEvent,
            CompactionStartEvent,
            ErrorEvent,
            HostNoticeEvent,
            InterruptedEvent,
            RetryEvent,
            TextDeltaEvent,
            TextEndEvent,
            TextStartEvent,
            ThinkingDeltaEvent,
            ThinkingEndEvent,
            ThinkingStartEvent,
            ToolApprovalEvent,
            ToolArgsDeltaEvent,
            ToolEndEvent,
            ToolOutputDeltaEvent,
            ToolResultEvent,
            ToolStartEvent,
            TurnEndEvent,
            TurnStartEvent,
            WarningEvent,
        )

        if isinstance(ev, _RunDone):
            self._finish_live()
            self._is_running = False
            self._cancel_event = None
            if self._pending_queue:
                self._enqueue_or_run(self._pending_queue.popleft())
            return

        if isinstance(ev, TurnStartEvent):
            return
        if isinstance(ev, ThinkingStartEvent):
            self._break_group()
            self._run_label = "thinking"
            if self._live_thinking is None:
                self._live_thinking = blocks.Thinking(text="")
                self._thinking_started = time.monotonic()
                self.viewport.add(self._live_thinking)
            return
        if isinstance(ev, ThinkingDeltaEvent):
            if self._live_thinking is not None:
                self._live_thinking.text += ev.delta
                self.viewport.touch()
            return
        if isinstance(ev, ThinkingEndEvent):
            # The block stays behind as the transcript's record of the reasoning
            # and folds to `ui.thinking_lines` rows now the stream is over, the
            # way ctx collapses a finished thinking block.
            if self._live_thinking is not None:
                self._live_thinking.streaming = False
                self._live_thinking.elapsed = self._thinking_elapsed()
                self.viewport.touch()
            self._live_thinking = None
            self._thinking_started = None
            return
        if isinstance(ev, TextStartEvent):
            self._break_group()
            self._assistant_block()
            return
        if isinstance(ev, TextDeltaEvent):
            self._break_group()
            block = self._assistant_block()
            block.text += ev.delta
            self._run_label = "responding"
            self.viewport.touch()
            return
        if isinstance(ev, TextEndEvent):
            block = self._assistant_block()
            if ev.text and not block.text:
                block.text = ev.text
            if not block.text.strip():
                self.viewport.blocks.remove(block)
            self._live_assistant = None
            return
        if isinstance(ev, ToolStartEvent):
            block = _make_tool_status_block(ev.tool_name)
            self._tool_blocks[ev.tool_call_id] = block
            self._tool_started[ev.tool_call_id] = time.monotonic()
            if ev.tool_name == "apply_patch":
                # A patch carries its own diff, so it stands alone: a group
                # header above one row would only repeat the row, and the diff
                # has to follow the call it belongs to.
                self._break_group()
                self.viewport.add(blocks.ToolGroup(calls=[block]))
            else:
                group = self._live_group
                if group is None:
                    group = blocks.ToolGroup()
                    self._live_group = group
                    self.viewport.add(group)
                group.calls.append(block)
                self._tool_groups[ev.tool_call_id] = group
            self.viewport.touch()
            self._run_label = f"running {ev.tool_name}"
            return
        if isinstance(ev, ToolArgsDeltaEvent):
            block = self._tool_blocks.get(ev.tool_call_id)
            if block is not None:
                snapshot = (
                    getattr(ev, "partial_arguments", None)
                    or getattr(ev, "snapshot", None)
                    or getattr(ev, "arguments", None)
                )
                if snapshot:
                    _populate_tool_block_args(
                        block, getattr(ev, "tool_name", "") or block.name, snapshot
                    )
                    self.viewport.touch()
            return
        if isinstance(ev, ToolEndEvent):
            block = self._tool_blocks.get(ev.tool_call_id)
            if block is not None:
                if ev.arguments:
                    _populate_tool_block_args(block, ev.tool_name or block.name, ev.arguments)
                display = getattr(ev, "display", "")
                if display:
                    _populate_tool_block_display(block, _short(_strip_markup(display), 70))
            return
        if isinstance(ev, ToolOutputDeltaEvent):
            # A call's row already says what it ran and how it ended; the stream
            # itself is for the model, not a wall of text in the transcript.
            return
        if isinstance(ev, ToolResultEvent):
            block = self._tool_blocks.get(ev.tool_call_id)
            result = ev.result
            ok = True
            if result is not None:
                ok = not result.is_error
            if block is not None:
                block.status = "ok" if ok else "error"
                started = self._tool_started.pop(ev.tool_call_id, None)
                if started is not None:
                    block.elapsed = max(0.0, time.monotonic() - started)
                # The mark speaks for the command that returned last, and
                # calls that ran together may not return in the order they
                # started.
                self._finished += 1
                block.finished = self._finished
                self._turn_calls.append(block)
                # For apply_patch, populate diff stats from file_changes
                if ev.file_changes and isinstance(block, blocks.ApplyPatchToolStatus):
                    block.added = sum(f.added for f in ev.file_changes)
                    block.removed = sum(f.removed for f in ev.file_changes)
                if isinstance(block, blocks.BashToolStatus):
                    _apply_exit_status(block, result)
                # A patch shows what it changed: the tool hands back its diff
                # lines, and the group carries them under the row.
                if block.name == "apply_patch" and ok and result and result.ui_data:
                    for path, lines in _diff_sections(result.ui_data):
                        self._place_detail(
                            ev.tool_call_id, blocks.DiffOutput(lines=lines, path=path)
                        )
            return
        if isinstance(ev, ToolApprovalEvent):
            self._request_approval(ev)
            return
        if isinstance(ev, AskUserEvent):
            self._break_group()
            self._request_ask_user(ev)
            return
        if isinstance(ev, CompactionStartEvent):
            self._notice(f"compacting context ({ev.tokens_before} tokens)")
            return
        if isinstance(ev, CompactionEndEvent):
            if ev.aborted:
                self._notice(f"compaction aborted: {ev.reason}", tone="warning")
            else:
                self._notice(f"context compacted → {ev.tokens_after} tokens")
            return
        if isinstance(ev, RetryEvent):
            self._notice(
                f"retrying {ev.attempt}/{ev.total_attempts} in {ev.delay:.0f}s — {ev.error}",
                tone="warning",
            )
            return
        if isinstance(ev, ErrorEvent):
            self._notice(str(ev.error), tone="error")
            return
        if isinstance(ev, WarningEvent):
            self._notice(str(ev.warning), tone="warning")
            return
        if isinstance(ev, InterruptedEvent):
            self._finish_live()
            self._notice("interrupted", tone="cancelled")
            return
        if isinstance(ev, HostNoticeEvent):
            self._notice(str(ev.text))
            return
        if isinstance(ev, BackgroundTaskCompletedEvent):
            self._notice(f"background task “{ev.description}” {ev.status}")
            return
        if isinstance(ev, TurnEndEvent):
            if ev.assistant_message and ev.assistant_message.usage:
                usage = ev.assistant_message.usage
                self._usage["in"] += usage.input_tokens
                self._usage["out"] += usage.output_tokens
                self._turn_usage["in"] += usage.input_tokens
                self._turn_usage["out"] += usage.output_tokens
            return
        if isinstance(ev, AgentEndEvent):
            self._finish_live()
            self._add_turn_summary()
            self._schedule_title()
            return

    def _schedule_title(self) -> None:
        """Name the session once, after a turn, without making anyone wait.

        The call goes out in the background and the title is applied when it
        arrives; an unanswered title simply leaves the first prompt in place,
        which is what the picker showed before.
        """
        rt = self.runtime
        session = rt.session if rt is not None else None
        provider = rt.provider if rt is not None else None
        if session is None or provider is None:
            return
        if not titles.needs_title(session, self._titled):
            return
        self._titled.add(session.id)
        self._title_task = asyncio.create_task(self._title_session(provider, session))

    async def _title_session(self, provider: Any, session: Any) -> None:
        title = await titles.title_session(provider, session)
        if not title:
            return
        # A title that lands mid-turn would append to a session the turn is
        # writing to, so it waits for the transcript to go quiet.
        while self._is_running:
            await asyncio.sleep(0.5)
        if self.runtime is None or self.runtime.session is not session:
            return
        with suppress(Exception):
            self.runtime.set_session_name(title)
            self.viewport.touch()

    def _place_detail(self, tool_call_id: str, detail: blocks.Block) -> None:
        """Put a call's detail under its group, or after the call when alone."""
        group = self._tool_groups.get(tool_call_id)
        if group is not None:
            group.details.append(detail)
        else:
            self.viewport.add(detail)
        self.viewport.touch()

    def _add_turn_summary(self) -> None:
        """fx's recap under a turn: what it did, how long it took, its tokens."""
        recap = _turn_recap(self._turn_calls)
        seconds = max(0.0, time.monotonic() - self._run_started) if self._run_started else 0.0
        usage = self._turn_usage
        self._turn_calls = []
        self._turn_usage = {"in": 0, "out": 0}
        if not recap:
            return
        self.viewport.add(
            blocks.TurnSummary(
                recap=recap, seconds=seconds, tokens_in=usage["in"], tokens_out=usage["out"]
            )
        )

    def _thinking_elapsed(self) -> float | None:
        """Seconds the thought ran, for the folded header. None when unknown."""
        if self._thinking_started is None:
            return None
        return max(0.0, time.monotonic() - self._thinking_started)

    def _finish_live(self) -> None:
        # A call still running when the turn ends was interrupted: fx marks it
        # cancelled rather than letting it read as done.
        for block in self._tool_blocks.values():
            if block.status == "running":
                block.status = "cancelled"
        self._live_assistant = None
        if self._live_thinking is not None:
            self._live_thinking.streaming = False
            self._live_thinking.elapsed = self._thinking_elapsed()
        self._live_thinking = None
        self._thinking_started = None
        self._live_group = None
        self._tool_blocks.clear()
        self._tool_started.clear()
        self._tool_groups.clear()
        self.viewport.touch()

    # ==================================================================
    # approvals
    # ==================================================================

    def _request_approval(self, ev: ToolApprovalEvent) -> None:
        if ev.future is None or ev.future.done():
            return
        display = _strip_markup(ev.display or "")
        if not display:
            block = self._tool_blocks.get(ev.tool_call_id)
            if block is not None:
                # Get display text from specialized block fields
                if hasattr(block, "command") and block.command:
                    display = f"$ {block.command}"
                elif hasattr(block, "path") and block.path:
                    display = str(block.path)
                elif hasattr(block, "pattern") and block.pattern:
                    display = block.pattern
                elif hasattr(block, "query") and block.query:
                    display = block.query
                elif hasattr(block, "url") and block.url:
                    display = block.url
                elif hasattr(block, "target") and block.target:
                    display = block.target
        if not display:
            display = ev.tool_name
        self._approval = {
            "label": ev.tool_name,
            "target": display,
            "reason": "This action needs your approval before vx continues.",
            "selected": 0,
            "future": ev.future,
        }
        self.viewport.touch()
        self._draw()

    def _approval_key(self, key: str) -> None:
        from ctx.core import ApprovalResponse

        ap = self._approval
        if ap is None:
            return
        if key in ("up", "tab"):
            ap["selected"] = (ap["selected"] - 1) % 3
            return
        if key == "down":
            ap["selected"] = (ap["selected"] + 1) % 3
            return
        if key in ("1", "2", "3"):
            ap["selected"] = int(key) - 1
            self._resolve_approval()
            return
        if key == "enter":
            self._resolve_approval()
            return
        if key == "ctrl+o":
            blocks.set_expanded(not blocks.expanded())
            self.viewport.touch()
            return
        if key in ("esc", "n"):
            ap["future"].set_result(ApprovalResponse.DENY)
            self._approval = None

    def _resolve_approval(self) -> None:
        from ctx.core import ApprovalResponse

        ap = self._approval
        if ap is None:
            return
        approved = ap["selected"] in (0, 1)
        ap["future"].set_result(ApprovalResponse.APPROVE if approved else ApprovalResponse.DENY)
        self._approval = None

    # ==================================================================
    # ask_user
    # ==================================================================

    def _request_ask_user(self, ev: AskUserEvent) -> None:
        from ctx.core import AskUserResponse

        if ev.future is None or ev.future.done() or not ev.questions:
            if ev.future is not None and not ev.future.done():
                ev.future.set_result(AskUserResponse())
            return
        self._ask = {
            "questions": ev.questions,
            "idx": 0,
            "selected": 0,
            "answers": [],
            "future": ev.future,
            "draft": "",
        }
        self._input = ""
        self._cursor = 0

    def _ask_view(self) -> footer.QuestionView:
        ask = self._ask
        assert ask is not None
        question = ask["questions"][min(ask["idx"], len(ask["questions"]) - 1)]
        options = [footer.QuestionOption(o.label, o.description or "") for o in question.options]
        return footer.QuestionView(
            question=question.question,
            options=options,
            selected=ask["selected"],
            index=ask["idx"],
            total=len(ask["questions"]),
            draft=ask["draft"],
        )

    def _ask_key(self, key: str) -> None:
        from ctx.core import AskUserResponse

        ask = self._ask
        if ask is None:
            return
        question = ask["questions"][ask["idx"]]
        count = len(question.options)
        if key == "esc":
            ask["future"].set_result(AskUserResponse())
            self._ask = None
            return
        if key == "up" and count:
            ask["selected"] = (ask["selected"] - 1) % (count + 1)
            return
        if key == "down" and count:
            ask["selected"] = (ask["selected"] + 1) % (count + 1)
            return
        if key in "123456789" and count:
            index = int(key) - 1
            if index < count:
                ask["selected"] = index
                self._answer_question(str(question.options[index].label))
            return
        if key == "enter":
            if ask["draft"].strip():
                self._answer_question(ask["draft"].strip(), custom=True)
            elif count and ask["selected"] < count:
                self._answer_question(question.options[ask["selected"]].label)
            return
        if key == "ctrl+o":
            blocks.set_expanded(not blocks.expanded())
            self.viewport.touch()
            return
        if key == "backspace":
            ask["draft"] = ask["draft"][:-1]
            return
        if key.startswith("paste:"):
            ask["draft"] += key[6:].replace("\r\n", "\n")
            return
        if len(key) == 1 and ord(key) >= 32:
            ask["draft"] += key

    def _answer_question(self, text: str, *, custom: bool = False) -> None:
        from ctx.core import AskUserAnswer, AskUserResponse

        ask = self._ask
        if ask is None:
            return
        question = ask["questions"][ask["idx"]]
        if custom:
            answer = AskUserAnswer(question=question.question, kind="custom", answer=text)
        else:
            answer = AskUserAnswer(question=question.question, kind="option", answer=text)
        ask["answers"].append(answer)
        ask["draft"] = ""
        ask["selected"] = 0
        ask["idx"] += 1
        if ask["idx"] >= len(ask["questions"]):
            ask["future"].set_result(AskUserResponse(answers=tuple(ask["answers"])))
            self._ask = None
            self._notice("; ".join(a.scalar() for a in ask["answers"]), topic="answered")

    # ==================================================================
    # frame
    # ==================================================================

    def _refresh_stats(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._stats_at < 1.0:
            return
        self._stats_at = now
        session = self.runtime.session if self.runtime is not None else None
        if session is None:
            return
        with suppress(Exception):
            totals = session.token_totals()
            self._context_used = totals.context_tokens
            self._usage["in"] = totals.input_tokens + totals.cache_read_tokens
            self._usage["out"] = totals.output_tokens

    async def _ticker(self) -> None:
        try:
            while not self._exiting:
                await asyncio.sleep(0.05)
                self._drain_events()
                self._refresh_stats()
                self._draw()
        except asyncio.CancelledError:
            pass

    async def _main_loop(self) -> None:
        while not self._exiting:
            await asyncio.sleep(0.1)

    def _footer(self, cols: int, height: int = 24) -> tuple[list[str], tuple[int, int] | None]:
        """Footer rows and the caret position inside them (if any)."""
        if self._approval is not None:
            ap = self._approval
            view = footer.ApprovalView(
                label=ap["label"],
                target=ap["target"],
                reason=ap["reason"],
                choices=["1. Yes", "2. Yes, and don't ask again", "3. No"],
                selected=ap["selected"],
            )
            return [footer.rule(cols), *view.panel(cols)[:7], view.hint(cols)], None

        if self._ask is not None:
            view = self._ask_view()
            panel = view.panel(cols)
            return [footer.rule(cols), "", *panel, footer.rule(cols), view.hint(cols)], None

        if self._searching:
            search_rows, caret_row, caret_col = footer.composer_rows(
                self._search, self._search_cursor, cols, 1, prompt=footer.SEARCH_PROMPT
            )
            caret = (1 + caret_row, caret_col) if 0 <= caret_row < len(search_rows) else None
            return [
                footer.rule(cols),
                *search_rows,
                footer.rule(cols),
                footer.search_hint(
                    cols=cols,
                    index=self.viewport.search_index,
                    count=len(self.viewport.search_matches),
                ),
            ], caret

        prompt = self._prompt
        input_rows = max(1, min(MAX_INPUT_LINES, len(footer.wrap_input(self._input, cols))))
        pinned = self._input_scroll
        scroll = pinned[0] if pinned is not None and pinned[1] == self._cursor else None
        composer, caret_row, caret_col = footer.composer_rows(
            self._input, self._cursor, cols, input_rows, secret=prompt is not None, scroll=scroll
        )
        rows: list[str] = []
        if prompt is not None:
            rows.append(footer.rule(cols))
            rows.append(footer.prompt_row(str(prompt.get("label") or ""), cols))
        else:
            rows.append(footer.rule(cols))
        composer_at = len(rows)
        if self._attachments:
            rows.append(footer.attachment_row(self._attachments, cols))
            composer_at += 1
        rows.extend(composer)
        # A paged window can leave the caret outside it; drawing it there would
        # put the cursor on the hint row or past the terminal.
        caret: tuple[int, int] | None = (
            None
            if caret_row < 0 or caret_row >= len(composer)
            else (composer_at + caret_row, caret_col)
        )

        if prompt is not None:
            rows.append(footer.rule(cols))
            rows.append(footer.menu_hint(cols=cols, variants=footer.PROMPT_HINT_VARIANTS))
            return rows, caret

        menu = self._menu
        if menu is not None:
            kind = menu.get("kind")
            # Mode picker has its own panel rendering
            if kind == "mode":
                mode_view = menu.get("view")
                if mode_view is not None:
                    mode_view.selected = menu["selected"]
                    panel = mode_view.panel(cols)
                    return [
                        footer.rule(cols),
                        *panel,
                        footer.rule(cols),
                        mode_view.hint(cols),
                    ], None
                return rows, caret
            if kind == "permissions":
                view = menu.get("view")
                if view is not None:
                    panel_height = max(1, height - 8 - input_rows)
                    panel = view.panel(cols, panel_height)
                    panel = panel[self._permissions_panel_scroll :]
                    return [
                        footer.rule(cols),
                        *panel[:panel_height],
                        footer.rule(cols),
                        view.hint(cols),
                    ], None
            view = self._menu_view()
            # fx's budget: 5 fixed footer rows plus the composer, then leave
            # five transcript rows on screen, capped at six picker rows.
            menu_window = max(1, min(footer.MENU_MAX_ROWS, height - 10 - input_rows))
            rows.append(footer.rule(cols))
            query = str(menu.get("query") or "")
            rows.extend(
                footer.menu_rows(
                    cols=cols,
                    items=view,
                    selected=menu["selected"],
                    noun=menu.get("noun", "Commands")
                    + (f" · {query}" if query and menu.get("filterable") else ""),
                    type_hint=menu.get("type_hint", False) and not query,
                    max_rows=menu_window,
                )
            )
            rows.append(footer.rule(cols))
            # fx replaces the statusline with the picker's own key hints.
            rows.append(
                footer.menu_hint(
                    cols=cols, variants=menu.get("variants", footer.MENU_HINT_VARIANTS)
                )
            )
            return rows, caret
        rows.append(footer.rule(cols))
        rows.append(self._hint(cols))
        return rows, caret

    def _hint(self, cols: int) -> str:
        rt = self.runtime
        right = ""
        if not self._ready.is_set():
            right = "starting" if self.runtime is None else "loading session"
        elif self._is_running:
            right = footer.activity_note(
                self._run_label, self._run_started, time.monotonic(), len(self._pending_queue)
            )
        elif self._activity:
            right = self._activity
        elif self._last_ctrl_c and time.monotonic() - self._last_ctrl_c < 2.0:
            right = "press ctrl+c again to exit"
        unseen = self.viewport.unseen
        if self.viewport.scroll and not self._is_running and not unseen:
            behind = self.viewport.scroll
            right = f"↑ {behind} line{'s' if behind != 1 else ''} · pgdn or wheel down"
        window = self._input_window()
        if window is not None:
            first, visible, total = window
            right = f"composer {first + 1}-{first + visible} of {total} \u00b7 pgup/pgdn"
            if self._is_running:
                right = f"{right} \u00b7 esc to interrupt"
        workspace = os.path.abspath(self.cwd)
        # Use vx's own permission mode for the hint row
        perm_mode = self._permission_runtime.mode
        return footer.hint_row(
            cols=cols,
            permission=perm_mode,
            model=getattr(rt, "model", "") or "",
            effort=getattr(rt, "thinking_level", "auto") or "auto",
            context_used=self._context_used,
            context_total=self._context_total,
            workspace=workspace,
            branch=_git_branch(self.cwd),
            right=right,
            unseen=unseen,
        )

    def _draw(self, *, force: bool = False) -> None:
        rows, cols = self.term.size()
        foot, caret = self._footer(cols, rows)
        foot = foot[-max(1, rows - 1) :]
        area = max(1, rows - len(foot))
        self._body_rows = area
        transcript = self.viewport.visible(cols, area)
        transcript = self.viewport.highlight(transcript)
        frame = transcript + foot
        frame = frame[:rows] if len(frame) > rows else frame + [""] * (rows - len(frame))
        self._frame = frame
        cursor = None
        if caret is not None:
            cursor = (area + caret[0], caret[1])
        if os.environ.get("VX_DEBUG_FRAME"):
            import json as _json

            with open(os.environ["VX_DEBUG_FRAME"], "a") as fh:
                fh.write(
                    _json.dumps(
                        {
                            "rows": rows,
                            "cols": cols,
                            "len": len(frame),
                            "area": area,
                            "scroll": self.viewport.scroll,
                            "total": self.viewport.total(cols),
                            "tail": [ansi.strip_ansi(x) for x in frame[-4:]],
                        }
                    )
                    + "\n"
                )
        payload = self.painter.paint(frame, cols, cursor)
        if payload or force:
            self.term.write(payload)


# -- helpers -----------------------------------------------------------------


def _make_tool_status_block(tool_name: str) -> blocks.ToolStatusBlock:
    """Create a specialized status block for the given tool name."""
    if tool_name == "read":
        return blocks.ReadToolStatus(status="running")
    if tool_name == "bash":
        return blocks.BashToolStatus(status="running")
    if tool_name == "apply_patch":
        return blocks.ApplyPatchToolStatus(status="running")
    if tool_name == "grep":
        return blocks.GrepToolStatus(status="running")
    if tool_name in ("web", "web_search", "web_fetch"):
        return blocks.WebToolStatus(name=tool_name, status="running")
    # Fallback to generic block for unknown tools
    return blocks.ToolStatus(name=tool_name, status="running")


def _apply_exit_status(block: blocks.BashToolStatus, result: Any) -> None:
    """Pull a command's exit code and failure reason out of its result.

    ctx reports the code in ``ui_summary`` ("Exit code 2") and the reason is
    what the program wrote, which fx puts after a middle dot on the row.
    """
    if result is None:
        return
    summary = _strip_markup(result.ui_summary or "")
    match = re.search(r"exit code (-?\d+)", summary, re.IGNORECASE)
    if match:
        block.exit_code = int(match.group(1))
    elif not result.is_error:
        # fx says how every command ended, so a command that passed says 0.
        block.exit_code = 0
    if not result.is_error:
        return
    for line in _result_lines(result):
        failure = line.strip()
        if failure and failure not in _OUTPUT_MARKERS:
            block.reason = _short(failure, 60)
            break


#: ctx labels a command's stderr stream; that label is not output.
_OUTPUT_MARKERS = {"[stderr]", "[stdout]"}


def _diff_sections(ui_data: dict[str, Any]) -> list[tuple[str, list[tuple[str, str, str]]]]:
    """A patch's diff, one section per file, from the tool's ``ui_data``."""
    groups = ui_data.get("diff_groups")
    if isinstance(groups, list) and groups:
        sections: list[tuple[str, list[tuple[str, str, str]]]] = []
        for group in groups:
            if not isinstance(group, dict):
                continue
            rows = group.get("lines") or []
            lines = [
                (row[0], row[1], row[2])
                for row in rows
                if isinstance(row, (list, tuple)) and len(row) == 3
            ]
            if lines:
                sections.append((str(group.get("path", "")), lines))
        if sections:
            return sections
    rows = ui_data.get("diff_lines") or []
    lines = [
        (row[0], row[1], row[2])
        for row in rows
        if isinstance(row, (list, tuple)) and len(row) == 3
    ]
    return [("", lines)] if lines else []


def _result_lines(result: Any) -> list[str]:
    """A finished call's output lines, as the model received them."""
    lines: list[str] = []
    for content in getattr(result, "content", None) or []:
        text = getattr(content, "text", None)
        if text:
            lines.extend(str(text).replace("\r", "").rstrip("\n").split("\n"))
    return [line for line in lines if line.strip()]


def _turn_recap(calls: list[blocks.ToolStatusBlock]) -> str:
    """fx's recap of a turn: files changed, then the commands it ran."""
    edits = [call for call in calls if call.category in ("edit", "write")]
    commands = [call for call in calls if call.category == "command"]
    parts: list[str] = []
    if edits:
        added = sum(getattr(call, "added", 0) for call in edits)
        removed = sum(getattr(call, "removed", 0) for call in edits)
        text = f"{len(edits)} file{'s' if len(edits) > 1 else ''} changed"
        if added or removed:
            text += f" +{added} -{removed}"
        parts.append(text)
    if commands:
        parts.append(f"ran {len(commands)} command{'s' if len(commands) > 1 else ''}")
    if not parts:
        reads = [call for call in calls if call.category == "read"]
        if reads:
            parts.append(f"explored {len(reads)} file{'s' if len(reads) > 1 else ''}")
    if not parts:
        return ""
    recap = " · ".join(parts)
    return recap[:1].upper() + recap[1:]


def _parse_args(args: Any) -> dict:
    """Parse tool args from string or dict."""
    if isinstance(args, dict):
        return args
    if isinstance(args, str):
        try:
            import json

            return json.loads(args) if args.strip().startswith("{") else {"value": args}
        except Exception:
            return {"value": args}
    return {}


def _populate_tool_block_args(block: blocks.Block, tool_name: str, args: Any) -> None:
    """Populate specialized block fields from tool arguments."""
    a = _parse_args(args)
    if isinstance(block, blocks.ReadToolStatus):
        path = a.get("path", a.get("file_path", a.get("filename", "")))
        if path:
            block.path = str(path)
        block.start_line = int(a.get("start_line", a.get("line", a.get("offset", 0))))
        block.end_line = int(a.get("end_line", a.get("limit", 0)))
        block.total_lines = int(a.get("total_lines", 0))
        block.is_dir = bool(a.get("is_dir", False))
    elif isinstance(block, blocks.BashToolStatus):
        cmd = a.get("command", a.get("cmd", a.get("value", "")))
        if cmd:
            block.command = str(cmd)
    elif isinstance(block, blocks.ApplyPatchToolStatus):
        ops = a.get("ops", a.get("edits", []))
        paths = [
            str(op.get("path", "")).strip()
            for op in ops
            if isinstance(op, dict) and str(op.get("path", "")).strip()
        ]
        if paths:
            block.path = paths[0]
            block.files = len(dict.fromkeys(paths))
        else:
            path = a.get("path", a.get("file_path", ""))
            if path:
                block.path = str(path)
    elif isinstance(block, blocks.GrepToolStatus):
        pattern = a.get("pattern", a.get("query", a.get("regex", a.get("search", ""))))
        if pattern:
            block.pattern = str(pattern)
        path = a.get("path", a.get("directory", a.get("root_dir", "")))
        if path:
            block.path = str(path)
    elif isinstance(block, blocks.WebToolStatus):
        mode = a.get("mode")
        if isinstance(mode, str) and mode:
            block.mode = mode.lower()
        query = a.get("query", a.get("search", ""))
        if query:
            block.query = str(query)
        url = a.get("url", a.get("link", ""))
        if url:
            block.url = str(url)
        if block.mode == "fetch" and not block.url and block.query:
            # One field carries both jobs: the payload says which mode reads it.
            block.url, block.query = block.query, ""
    elif isinstance(block, blocks.ToolStatus):
        # Generic fallback. The target is taken again on the finished call,
        # because a streamed argument arrives in pieces: the first parse of
        # {"name": "ctx-tmux-test"} sees "ctx" and would keep it otherwise.
        target = _tool_target(tool_name, args)
        if target:
            block.target = target


def _populate_tool_block_display(block: blocks.Block, display: str) -> None:
    """Fill in display text on specialized blocks if their main field is empty."""
    if isinstance(block, blocks.ReadToolStatus) and not block.path:
        block.path = display
    elif isinstance(block, blocks.BashToolStatus) and not block.command:
        block.command = display
    elif isinstance(block, blocks.ApplyPatchToolStatus) and not block.path:
        # A multi-op call's display is a joined path list, which is not a target.
        if ", " not in display:
            block.path = display
    elif isinstance(block, blocks.GrepToolStatus) and not block.pattern:
        block.pattern = display
    elif isinstance(block, blocks.WebToolStatus) and not block.query and not block.url:
        block.query = display
    elif isinstance(block, blocks.ToolStatus) and not block.target:
        block.target = display


def _short(text: str, limit: int = 70) -> str:
    flat = " ".join(str(text).split())
    return flat[: limit - 1] + "…" if len(flat) > limit else flat


def _strip_markup(text: str) -> str:
    return ansi.strip_ansi(ansi.markup_to_ansi(str(text)))


def _tool_target(name: str, args: Any) -> str:
    """A short fx-style target: ``src/app.py``, ``zig build``, a pattern…"""
    if isinstance(args, str):
        try:
            import json

            args = json.loads(args) if args.strip().startswith("{") else {"value": args}
        except Exception:
            args = {"value": args}
    if not isinstance(args, dict):
        return ""
    for key in (
        "path",
        "file_path",
        "filename",
        "command",
        "cmd",
        "pattern",
        "query",
        "url",
        "label",
        "name",
        "value",
    ):
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return _short(value.strip(), 70)
    return ""


def _name_rank(prefix: str, name: str) -> int | None:
    """0 exact, 1 prefix, 2 substring, 3 subsequence, ``None`` no name match."""
    if not prefix:
        return 0
    if name == prefix:
        return 0
    if name.startswith(prefix):
        return 1
    if prefix in name:
        return 2
    position = 0
    for char in name:
        if position < len(prefix) and char == prefix[position]:
            position += 1
    return 3 if position == len(prefix) else None


def _skill_source(path: str, cwd: str) -> str:
    """fx labels a skill by where it was found (``global .agents``...)."""
    if not path:
        return ""
    absolute = os.path.abspath(path)
    parts = absolute.split(os.sep)
    scope = "workspace" if absolute.startswith(os.path.abspath(cwd)) else "global"
    for marker in (".agents", ".fx", ".claude", ".codex", ".opencode", ".ctx"):
        if marker in parts:
            return f"{scope} {marker}"
    return f"{scope} skills/"


#: (cwd, second) -> branch label. Status runs on every frame otherwise, and a
#: large tree makes that visible as lag; a two-second window is plenty fresh.
_BRANCH_CACHE: dict[tuple[str, int], str | None] = {}


def _git_branch(cwd: str) -> str | None:
    """The branch name, with ``*`` when the working tree has uncommitted work."""
    bucket = (os.path.abspath(cwd), int(time.monotonic() // 2))
    if bucket in _BRANCH_CACHE:
        return _BRANCH_CACHE[bucket]
    label = _read_git_branch(cwd)
    if len(_BRANCH_CACHE) > 64:
        _BRANCH_CACHE.clear()
    _BRANCH_CACHE[bucket] = label
    return label


def _read_git_branch(cwd: str) -> str | None:
    try:
        import subprocess

        out = subprocess.run(
            ["git", "status", "--porcelain=v1", "--branch"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=0.35,
        )
        if out.returncode != 0:
            return None
        lines = out.stdout.splitlines()
        if not lines or not lines[0].startswith("## "):
            return None
        head = lines[0][3:].strip()
        # "main...origin/main [ahead 1]" -> "main"
        branch = head.split("...")[0].split(" ")[0]
        if not branch or branch == "HEAD" or branch.startswith("No commits"):
            return None
        return branch + ("*" if len(lines) > 1 else "")
    except Exception:
        return None
