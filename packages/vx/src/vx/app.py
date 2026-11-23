"""The vx TUI: an fx-style terminal front end for the ctx agent runtime.

The frame is built bottom-up the way fx builds it: transcript, ``─`` rule,
composer, ``─`` rule, hint row. Overlays (approval, questions, pickers) take
the composer's place instead of printing into the transcript.
"""

from __future__ import annotations

import asyncio
import os
import signal
import time
from collections import deque
from contextlib import suppress
from typing import Any

from ctx.agent.agents import AgentRegistry, load_all_agents
from ctx.agent.runtime import ConversationRuntime
from ctx.agent.tools import get_tool
from ctx.core import (
    AgentEndEvent,
    ApprovalResponse,
    AskUserAnswer,
    AskUserEvent,
    AskUserResponse,
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
from ctx.core.config import config
from ctx.plugins.extensions import load_for_runtime
from ctx.protocol.types import (
    AssistantMessage,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)

import vx.tools
from vx.atpath import expand_paths, list_candidates, parse_at
from vx.brand import VERSION
from vx.commands import SLASH_COMMANDS, CommandsMixin
from vx.tui import ansi, blocks, footer, theme
from vx.tui.canvas import Painter
from vx.tui.keys import KeyDecoder
from vx.tui.terminal import Terminal
from vx.tui.viewport import Viewport

HISTORY_LIMIT = 200
MAX_INPUT_LINES = 8


class _RunDone:
    pass


class VxApp(CommandsMixin):
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
    ) -> None:
        self.cwd = cwd
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
        self.painter = painter or Painter()
        self.decoder = KeyDecoder()

        # Composer
        self._input = ""
        self._cursor = 0
        self._history: list[str] = []
        self._history_idx: int | None = None
        self._history_draft = ""

        # Agent
        self.runtime: ConversationRuntime | None = None
        self._loaded: Any = None
        self._is_running = False
        self._run_label = "working"
        self._run_started = 0.0
        self._cancel_event: asyncio.Event | None = None
        self._pending_queue: deque[str] = deque()
        self._events: asyncio.Queue[Any] = asyncio.Queue()
        self._live_assistant: blocks.Assistant | None = None
        self._live_thinking: blocks.Thinking | None = None
        self._tool_blocks: dict[str, blocks.ToolStatus] = {}
        self._tool_outputs: dict[str, blocks.ToolOutput] = {}
        self._last_ctrl_c = 0.0
        self._usage = {"in": 0, "out": 0}
        self._context_total: int | None = None
        self._context_used = 0
        self._stats_at = 0.0

        # Overlays
        self._approval: dict[str, Any] | None = None
        self._ask: dict[str, Any] | None = None
        self._menu: dict[str, Any] | None = None
        self._prompt: dict[str, Any] | None = None
        self._activity: str = ""

        self._exiting = False
        self._ticker_task: asyncio.Task | None = None
        self._boot_task: asyncio.Task | None = None
        self._ready = asyncio.Event()

    # ==================================================================
    # lifecycle
    # ==================================================================

    async def run(self) -> int:
        loop = asyncio.get_running_loop()
        self._ticker_task = loop.create_task(self._ticker())
        try:
            await self._startup()
            await self._main_loop()
        finally:
            if self._ticker_task:
                self._ticker_task.cancel()
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
        self._welcome()
        # Paint and start reading keys before the runtime loads: ctx's context
        # load (skills, MCP, model limits) costs over a second.
        self._draw(force=True)
        self._boot_task = loop.create_task(self._boot())

    async def _boot(self) -> None:
        try:
            await self._init_runtime()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._notice(f"startup failed: {exc}", tone="error")
        finally:
            self._ready.set()
            self.viewport.touch()
            if self._pending_queue and not self._is_running:
                self._enqueue_or_run(self._pending_queue.popleft())

    def _welcome(self) -> None:
        self.viewport.add(blocks.Welcome(version=VERSION))

    async def _init_runtime(self) -> None:
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

        result = self.runtime.initialize(
            resume_session=k["resume_session"], continue_recent=k["continue_recent"]
        )
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
        msgs = list(session.messages)
        if len(msgs) > HISTORY_LIMIT:
            msgs = msgs[-HISTORY_LIMIT:]
            self._notice(f"showing the last {HISTORY_LIMIT} messages")
        for msg in msgs:
            self._render_history_message(msg)

    def _render_history_message(self, msg: Any) -> None:
        if isinstance(msg, UserMessage):
            content = msg.content
            if isinstance(content, str):
                text = content
            else:
                text = " ".join(p.text for p in content if isinstance(p, TextContent))
            if text.strip():
                self.viewport.add(blocks.UserTurn(text=text))
        elif isinstance(msg, AssistantMessage):
            thinking = "".join(p.thinking for p in msg.content if isinstance(p, ThinkingContent))
            text = "".join(p.text for p in msg.content if isinstance(p, TextContent))
            calls = [p for p in msg.content if isinstance(p, ToolCall)]
            if text.strip():
                self.viewport.add(blocks.Assistant(text=text))
            elif thinking.strip():
                self.viewport.add(blocks.Thinking(text=thinking))
            for call in calls:
                target = _short(str(call.arguments), 70)
                self.viewport.add(blocks.ToolStatus(name=call.name, target=target, status="ok"))
        elif isinstance(msg, ToolResultMessage):
            detail = msg.ui_details_full or msg.ui_details
            if detail:
                lines = [x for x in str(detail).split("\n") if x.strip()]
                if lines:
                    self.viewport.add(
                        blocks.ToolOutput(lines=[_markup_line(x) for x in lines[:5]])
                    )

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
        if key == "pageup":
            self.viewport.page(1, *self._size())
            return
        if key == "pagedown":
            self.viewport.page(-1, *self._size())
            return
        if key == "end":
            self.viewport.scroll = 0
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
            start = self._cursor
            while start > 0 and self._input[start - 1].isspace():
                start -= 1
            while start > 0 and not self._input[start - 1].isspace():
                start -= 1
            self._input = self._input[:start] + self._input[self._cursor :]
            self._cursor = start
            self._refresh_slash_menu()
            self._refresh_atpath_menu()
            return
        if key == "up":
            if self._input:
                self._history_move(-1)
            else:
                self.viewport.page(1, *self._size())
            return
        if key == "down":
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

    def _size(self) -> tuple[int, int]:
        rows, cols = self.term.size()
        return cols, rows

    # -- composer editing ------------------------------------------------

    def _insert(self, text: str) -> None:
        self._input = self._input[: self._cursor] + text + self._input[self._cursor :]
        self._cursor += len(text)
        self._history_idx = None

    def _backspace(self) -> None:
        if self._cursor > 0:
            self._input = self._input[: self._cursor - 1] + self._input[self._cursor :]
            self._cursor -= 1
        self._refresh_slash_menu()

    def _delete(self) -> None:
        if self._cursor < len(self._input):
            self._input = self._input[: self._cursor] + self._input[self._cursor + 1 :]
        self._refresh_slash_menu()

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
        query = str(menu.get("query") or "").strip().lower()
        if not query or not menu.get("filterable"):
            return items
        return [
            item
            for item in items
            if query in f"{item.label} {item.detail} {item.metadata}".lower()
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
        if key == "up":
            menu["selected"] = (menu["selected"] - 1) % max(1, len(self._menu_view()))
            return
        if key == "down":
            menu["selected"] = (menu["selected"] + 1) % max(1, len(self._menu_view()))
            return
        if kind == "slash" and key == "tab":
            self._complete()
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
        self._input = ""
        self._cursor = 0
        self._menu = None
        if not text:
            return
        self._history.append(text)
        if len(self._history) > 500:
            self._history = self._history[-500:]
        self._history_idx = None
        if text.startswith("/"):
            # fx never echoes a command as a prompt; it acts on it.
            self._handle_slash(text)
            return
        if text.startswith("!"):
            self._handle_shell(text)
            return
        self.viewport.add(blocks.UserTurn(text=text))
        self._enqueue_or_run(expand_paths(text, self.cwd))

    # ==================================================================
    # slash commands & shell
    # ==================================================================

    def _handle_slash(self, text: str) -> bool:
        """Slash commands live in ``vx.commands``; this is the entry point."""
        return self.handle_slash(text)

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
        self.viewport.add(block)
        self._is_running = True
        self._run_label = "running command"
        self._run_started = time.monotonic()
        asyncio.get_running_loop().create_task(self._run_shell(block, command, send_to_llm))

    async def _run_shell(self, block: blocks.ShellRun, command: str, send_to_llm: bool) -> None:
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

    def _enqueue_or_run(self, prompt: str) -> None:
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
        agent = self.runtime.prepare_for_run() if self.runtime else None
        if agent is None:
            self._notice("agent not initialized", tone="error")
            self._is_running = False
            return
        asyncio.get_running_loop().create_task(self._run_agent(agent, prompt))

    async def _run_agent(self, agent: Any, prompt: str) -> None:
        try:
            async for event in agent.run(
                prompt, images=None, cancel_event=self._cancel_event, steer_event=None
            ):
                self._events.put_nowait(event)
        except asyncio.CancelledError:
            raise
        except Exception as e:
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
        self.viewport.add(blocks.Notice(topic=topic, body=body, tone=tone, detail=detail))

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
            if self._live_thinking is None:
                self._live_thinking = blocks.Thinking(text="")
                self.viewport.add(self._live_thinking)
            return
        if isinstance(ev, ThinkingDeltaEvent):
            if self._live_thinking is not None:
                self._live_thinking.text += ev.delta
            return
        if isinstance(ev, ThinkingEndEvent):
            if self._live_thinking is not None and not self._live_thinking.text.strip():
                self.viewport.blocks.remove(self._live_thinking)
            self._live_thinking = None
            return
        if isinstance(ev, TextStartEvent):
            self._assistant_block()
            return
        if isinstance(ev, TextDeltaEvent):
            block = self._assistant_block()
            block.text += ev.delta
            self._run_label = "responding"
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
            self.viewport.add(block)
            self._run_label = f"running {ev.tool_name}"
            return
        if isinstance(ev, ToolArgsDeltaEvent):
            block = self._tool_blocks.get(ev.tool_call_id)
            if block is not None:
                snapshot = getattr(ev, "snapshot", None) or getattr(ev, "arguments", None)
                if snapshot:
                    _populate_tool_block_args(block, ev.tool_name, snapshot)
            return
        if isinstance(ev, ToolEndEvent):
            block = self._tool_blocks.get(ev.tool_call_id)
            if block is not None:
                display = getattr(ev, "display", "")
                if display:
                    _populate_tool_block_display(block, _short(_strip_markup(display), 70))
            return
        if isinstance(ev, ToolOutputDeltaEvent):
            block = self._tool_blocks.get(ev.tool_call_id)
            if block is not None:
                output = self._tool_outputs.get(ev.tool_call_id)
                if output is None:
                    output = blocks.ToolOutput(lines=[])
                    self._tool_outputs[ev.tool_call_id] = output
                    self.viewport.add(output)
                buffer = getattr(output, "buffer", "") + ev.delta
                output.buffer = buffer[-8000:]
                lines = buffer.replace("\r", "").split("\n")
                while len(lines) > 1 and not lines[-1].strip():
                    lines.pop()
                output.lines = [_markup_line(x) for x in lines[-blocks.OUTPUT_PREVIEW_ROWS :]]
                output.hidden = max(0, len(lines) - blocks.OUTPUT_PREVIEW_ROWS)
            return
        if isinstance(ev, ToolResultEvent):
            block = self._tool_blocks.get(ev.tool_call_id)
            result = ev.result
            ok = True
            if result is not None:
                ok = not result.is_error
            if block is not None:
                block.status = "ok" if ok else "error"
                # For apply_patch, populate diff stats from file_changes
                if ev.file_changes and hasattr(block, "added"):
                    block.added = sum(f.added for f in ev.file_changes)
                    block.removed = sum(f.removed for f in ev.file_changes)
            output = self._tool_outputs.get(ev.tool_call_id)
            if output is not None and not output.lines:
                with suppress(Exception):
                    self.viewport.blocks.remove(output)
            if ev.file_changes:
                added = sum(f.added for f in ev.file_changes)
                removed = sum(f.removed for f in ev.file_changes)
                self.viewport.add(
                    blocks.DiffSummary(files=len(ev.file_changes), added=added, removed=removed)
                )
            return
        if isinstance(ev, ToolApprovalEvent):
            self._request_approval(ev)
            return
        if isinstance(ev, AskUserEvent):
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
            return
        if isinstance(ev, AgentEndEvent):
            self._finish_live()
            return

    def _finish_live(self) -> None:
        for block in self.viewport.blocks:
            if isinstance(block, blocks.ToolStatus) and block.status == "running":
                block.status = "ok"
        self._live_assistant = None
        self._live_thinking = None
        self._tool_blocks.clear()
        self._tool_outputs.clear()
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
            if block is not None and block.target:
                display = f"$ {block.target}"
        self._approval = {
            "label": ev.tool_name,
            "target": display,
            "reason": "This action needs your approval before vx continues.",
            "selected": 0,
            "future": ev.future,
        }

    def _approval_key(self, key: str) -> None:
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
        if key in ("esc", "n"):
            ap["future"].set_result(ApprovalResponse.DENY)
            self._approval = None

    def _resolve_approval(self) -> None:
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
        if key == "backspace":
            ask["draft"] = ask["draft"][:-1]
            return
        if key.startswith("paste:"):
            ask["draft"] += key[6:].replace("\r\n", "\n")
            return
        if len(key) == 1 and ord(key) >= 32:
            ask["draft"] += key

    def _answer_question(self, text: str, *, custom: bool = False) -> None:
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

        prompt = self._prompt
        input_rows = max(1, min(MAX_INPUT_LINES, len(footer.wrap_input(self._input, cols))))
        composer, caret_row, caret_col = footer.composer_rows(
            self._input, self._cursor, cols, input_rows, secret=prompt is not None
        )
        rows: list[str] = []
        if prompt is not None:
            rows.append(footer.rule(cols))
            rows.append(footer.prompt_row(str(prompt.get("label") or ""), cols))
        else:
            rows.append(footer.rule(cols))
        composer_at = len(rows)
        rows.extend(composer)
        caret: tuple[int, int] | None = (composer_at + caret_row, caret_col)

        if prompt is not None:
            rows.append(footer.rule(cols))
            rows.append(footer.menu_hint(cols=cols, variants=footer.PROMPT_HINT_VARIANTS))
            return rows, caret

        menu = self._menu
        if menu is not None:
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
        workspace = os.path.abspath(self.cwd)
        return footer.hint_row(
            cols=cols,
            permission=config.permissions.mode,
            model=getattr(rt, "model", "") or "",
            effort=getattr(rt, "thinking_level", "auto") or "auto",
            context_used=self._context_used,
            context_total=self._context_total,
            workspace=workspace,
            branch=_git_branch(self.cwd),
            right=right,
        )

    def _draw(self, *, force: bool = False) -> None:
        rows, cols = self.term.size()
        foot, caret = self._footer(cols, rows)
        foot = foot[-max(1, rows - 1) :]
        area = max(1, rows - len(foot))
        transcript = self.viewport.visible(cols, area)
        frame = transcript + foot
        frame = frame[:rows] if len(frame) > rows else frame + [""] * (rows - len(frame))
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
                            "tail": [ansi.strip_ansi(x) for x in frame[-4:]],
                        }
                    )
                    + "\n"
                )
        payload = self.painter.paint(frame, cols, cursor)
        if payload or force:
            self.term.write(payload)


# -- helpers -----------------------------------------------------------------


def _make_tool_status_block(tool_name: str) -> blocks.Block:
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
        return blocks.WebToolStatus(tool_name=tool_name, status="running")
    # Fallback to generic block for unknown tools
    return blocks.ToolStatus(name=tool_name, status="running")


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
        path = a.get("path", a.get("file_path", ""))
        if path:
            block.path = str(path)
        ops = a.get("ops", a.get("edits", []))
        if isinstance(ops, list):
            block.ops_count = len(ops)
    elif isinstance(block, blocks.GrepToolStatus):
        pattern = a.get("pattern", a.get("query", a.get("regex", a.get("search", ""))))
        if pattern:
            block.pattern = str(pattern)
        path = a.get("path", a.get("directory", a.get("root_dir", "")))
        if path:
            block.path = str(path)
    elif isinstance(block, blocks.WebToolStatus):
        query = a.get("query", a.get("search", ""))
        if query:
            block.query = str(query)
        url = a.get("url", a.get("link", ""))
        if url:
            block.url = str(url)
    elif isinstance(block, blocks.ToolStatus):
        # Generic fallback
        target = _tool_target(tool_name, args)
        if target and not block.target:
            block.target = target


def _populate_tool_block_display(block: blocks.Block, display: str) -> None:
    """Fill in display text on specialized blocks if their main field is empty."""
    if isinstance(block, blocks.ReadToolStatus) and not block.path:
        block.path = display
    elif isinstance(block, blocks.BashToolStatus) and not block.command:
        block.command = display
    elif isinstance(block, blocks.ApplyPatchToolStatus) and not block.path:
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


def _markup_line(text: str) -> str:
    """Tool output can arrive already styled; only convert plain markup."""
    return text if "\x1b" in text else ansi.markup_to_ansi(text)


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
    """0 prefix, 1 substring, 2 subsequence, ``None`` no name match."""
    if not prefix:
        return 0
    if name.startswith(prefix):
        return 0
    if prefix in name:
        return 1
    position = 0
    for char in name:
        if position < len(prefix) and char == prefix[position]:
            position += 1
    return 2 if position == len(prefix) else None


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


def _git_branch(cwd: str) -> str | None:
    try:
        import subprocess

        out = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=0.25,
        )
        if out.returncode == 0:
            branch = out.stdout.strip()
            if branch and branch != "HEAD":
                return branch
    except Exception:
        return None
    return None
