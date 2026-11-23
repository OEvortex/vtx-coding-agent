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
from ctx.agent.tools import get_tool, get_tools_with_extensions
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

from vx.brand import VERSION
from vx.tui import ansi, blocks, footer, theme
from vx.tui.canvas import Painter
from vx.tui.keys import KeyDecoder
from vx.tui.terminal import Terminal
from vx.tui.viewport import Viewport

HISTORY_LIMIT = 200
MAX_INPUT_LINES = 8
MENU_WINDOW = 10

SLASH_COMMANDS: list[tuple[str, str]] = [
    ("/help", "show commands and key bindings"),
    ("/model", "switch model, or list the catalog"),
    ("/think", "set the reasoning effort level"),
    ("/permissions", "ask, auto or full access"),
    ("/new", "start a fresh session"),
    ("/clear", "clear the transcript"),
    ("/compact", "compact the context window"),
    ("/usage", "session token usage"),
    ("/tools", "list the registered tools"),
    ("/session", "show the session id"),
    ("/agents", "list handoff agents"),
    ("/quit", "exit vx"),
]


class _RunDone:
    pass


class VxApp:
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
        self.term = Terminal()
        self.viewport = Viewport()
        self.painter = Painter()
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

        self._exiting = False
        self._ticker_task: asyncio.Task | None = None

    # ==================================================================
    # lifecycle
    # ==================================================================

    async def run(self) -> int:
        self._ticker_task = asyncio.get_running_loop().create_task(self._ticker())
        try:
            await self._startup()
            await self._main_loop()
        finally:
            if self._ticker_task:
                self._ticker_task.cancel()
            if self.runtime is not None:
                with suppress(Exception):
                    await self.runtime.close()
            self.term.exit()
        return 0

    async def _startup(self) -> None:
        self.term.enter()
        self.painter.reset()
        loop = asyncio.get_running_loop()
        loop.add_reader(self.term._fd, self._on_stdin)
        signal.signal(signal.SIGWINCH, lambda *_: self._on_resize())
        self._welcome()
        await self._init_runtime()
        self._draw(force=True)

    def _welcome(self) -> None:
        self.viewport.add(blocks.Welcome(version=VERSION))

    async def _init_runtime(self) -> None:
        k = self._init_kwargs
        tools = get_tools_with_extensions()
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
        if key == "home":
            self._cursor = 0
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
        text = self._input
        if not text.startswith("/") or " " in text:
            if self._menu is not None and self._menu["kind"] == "slash":
                self._menu = None
            return
        query = text[1:].lower()
        items = [
            footer.MenuItem(label=name, detail=desc)
            for name, desc in SLASH_COMMANDS
            if name[1:].startswith(query)
        ]
        if not items:
            self._menu = None
            return
        current = self._menu.get("selected", 0) if self._menu else 0
        self._menu = {
            "kind": "slash",
            "items": items,
            "selected": min(current, len(items) - 1),
            "header": f"Commands {len(items)} · type to filter",
        }

    def _complete(self) -> None:
        if self._menu is None:
            return
        item = self._menu["items"][self._menu["selected"]]
        if self._menu["kind"] == "slash":
            self._input = item.label + " "
            self._cursor = len(self._input)
            self._menu = None

    def _menu_key(self, key: str) -> None:
        menu = self._menu
        if menu is None:
            return
        if key in ("esc", "ctrl+c"):
            if menu["kind"] == "slash":
                self._input = ""
                self._cursor = 0
            self._menu = None
            return
        if key == "up":
            menu["selected"] = (menu["selected"] - 1) % len(menu["items"])
            return
        if key == "down" or key == "tab":
            if key == "tab" and menu["kind"] == "slash":
                self._complete()
                return
            menu["selected"] = (menu["selected"] + 1) % len(menu["items"])
            return
        if key in ("enter", "right"):
            self._menu_choose()
            return
        if key == "backspace":
            self._backspace()
            if menu["kind"] == "slash":
                self._refresh_slash_menu()
            return
        if menu["kind"] == "slash":
            if key.startswith("paste:"):
                self._insert(key[6:])
            elif len(key) == 1 and ord(key) >= 32:
                self._insert(key)
            self._refresh_slash_menu()
            return
        # Non-slash menus close once the user types.
        self._menu = None
        self._handle_key(key)

    def _menu_choose(self) -> None:
        menu = self._menu
        if menu is None:
            return
        item = menu["items"][menu["selected"]]
        kind = menu["kind"]
        self._menu = None
        if kind in ("slash", "help"):
            self._input = item.label
            self._cursor = len(self._input)
            self._submit()
        elif kind == "model":
            self._switch_model(item.label)
        elif kind == "agents":
            self._notice(f"agent: {item.label}")

    # -- submit ----------------------------------------------------------

    def _submit(self) -> None:
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
        self._enqueue_or_run(text)

    # ==================================================================
    # slash commands & shell
    # ==================================================================

    def _handle_slash(self, text: str) -> bool:
        parts = text[1:].split(None, 1)
        cmd = parts[0].lower() if parts else ""
        arg = parts[1].strip() if len(parts) > 1 else ""
        rt = self.runtime

        if cmd in ("quit", "exit", "q"):
            self._exiting = True
            return True
        if cmd == "help":
            self._menu = {
                "kind": "help",
                "items": [
                    footer.MenuItem(label=name, detail=desc) for name, desc in SLASH_COMMANDS
                ],
                "selected": 0,
                "header": "Commands · enter to run, esc to close",
            }
            return True
        if cmd == "new":
            if rt is not None:
                rt.new_session()
                self.viewport.clear()
                self._welcome()
                self._notice("new session started")
            return True
        if cmd == "clear":
            self.viewport.clear()
            self._welcome()
            return True
        if cmd == "model":
            if not arg:
                self._open_model_menu()
                return True
            self._switch_model(arg)
            return True
        if cmd in ("think", "thinking"):
            if rt is not None and arg:
                rt.set_thinking_level(arg)
                self._notice(f"thinking level: {rt.thinking_level}")
            elif rt is not None:
                self._notice(f"thinking level: {rt.thinking_level}")
            return True
        if cmd in ("permissions", "perm"):
            if arg:
                try:
                    config.permissions.mode = arg
                    self._notice(f"permission mode: {arg}")
                except Exception as e:
                    self._notice(f"bad mode: {e}", tone="error")
            else:
                self._notice(f"permission mode: {config.permissions.mode}")
            return True
        if cmd == "compact":
            if rt is not None:
                self._notice("compacting context…")
                asyncio.get_running_loop().create_task(self._compact())
            return True
        if cmd == "usage":
            self._refresh_stats(force=True)
            total = self._context_total or 0
            pct = f" · {self._context_used / total * 100:.0f}% used" if total else ""
            self._notice(
                f"{self._usage['in']} in · {self._usage['out']} out · "
                f"{self._context_used} context{pct}",
                topic="usage",
            )
            return True
        if cmd == "tools":
            names = sorted(t.name for t in (rt.tools if rt else []))
            self._notice(", ".join(names) or "none", topic="tools")
            return True
        if cmd == "session":
            self._notice(str(rt.session.id if rt and rt.session else "?"), topic="session")
            return True
        if cmd == "agents":
            names = [a.definition.name for a in (rt.agent_registry.agents if rt else [])]
            active = (
                rt.agent_registry.active.definition.name
                if rt and rt.agent_registry.active
                else "-"
            )
            self._notice(f"{', '.join(names) or 'none'} (active: {active})", topic="agents")
            return True
        self._notice(f"unknown command: /{cmd}", tone="warning")
        return True

    def _open_model_menu(self) -> None:
        try:
            from ctx.ai.models import get_all_models
        except Exception as e:
            self._notice(f"model catalog unavailable: {e}", tone="error")
            return
        try:
            models = [m for m in get_all_models() if not m.hidden]
        except Exception as e:
            self._notice(f"model catalog unavailable: {e}", tone="error")
            return
        current = getattr(self.runtime, "model", "") or ""
        items = [
            footer.MenuItem(
                label=m.id,
                detail=f"{m.provider}"
                + (f" · {m.context_window // 1000}k" if m.context_window else ""),
            )
            for m in models[:400]
        ]
        if not items:
            self._notice("no models in the catalog", tone="warning")
            return
        selected = next((i for i, m in enumerate(items) if m.label == current), 0)
        self._menu = {
            "kind": "model",
            "items": items,
            "selected": selected,
            "header": f"Models {len(items)} · enter to switch",
        }
        self.viewport.scroll = 0

    def _switch_model(self, query: str) -> None:
        try:
            from ctx.ai.dynamic_models import find_dynamic_model
            from ctx.ai.models import get_model
        except Exception as e:
            self._notice(f"model catalog unavailable: {e}", tone="error")
            return
        prov = self.runtime.model_provider if self.runtime else None
        info = get_model(query, prov) or find_dynamic_model(query, prov)
        if info is None:
            self._notice(f"model not found: {query}", tone="error")
            return
        try:
            assert self.runtime is not None
            self.runtime.switch_model(info)
            self._context_total = info.context_window
            self._notice(f"model: {info.id}")
        except Exception as e:
            self._notice(f"model switch failed: {e}", tone="error")

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
            target = ""
            with suppress(Exception):
                target = _tool_target(ev.tool_name, "")
            block = blocks.ToolStatus(name=ev.tool_name, target=target, status="running")
            self._tool_blocks[ev.tool_call_id] = block
            self.viewport.add(block)
            self._run_label = f"running {ev.tool_name}"
            return
        if isinstance(ev, ToolArgsDeltaEvent):
            block = self._tool_blocks.get(ev.tool_call_id)
            if block is not None and not block.target:
                snapshot = getattr(ev, "snapshot", None) or getattr(ev, "arguments", None)
                if snapshot:
                    block.target = _tool_target(ev.tool_name, snapshot)
            return
        if isinstance(ev, ToolEndEvent):
            block = self._tool_blocks.get(ev.tool_call_id)
            if block is not None and not block.target and getattr(ev, "display", ""):
                block.target = _short(_strip_markup(ev.display), 70)
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
                summary = result.ui_summary
                details = result.ui_details_full or result.ui_details
                # fx keeps the call's own target in the status line; the
                # result summary only fills in when the call had none.
                if block is not None and not block.target:
                    if summary:
                        block.target = _short(_strip_markup(summary), 70)
                    elif details:
                        block.target = _short(_strip_markup(details), 70)
            if block is not None:
                block.status = "ok" if ok else "error"
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

    def _footer(self, cols: int) -> tuple[list[str], tuple[int, int] | None]:
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
            rows = [footer.rule(cols), *view.panel(cols)[:7], view.hint(cols)]
            return rows, None

        if self._ask is not None:
            view = self._ask_view()
            panel = view.panel(cols)
            rows = [footer.rule(cols), "", *panel, footer.rule(cols), view.hint(cols)]
            return rows, None

        input_rows = max(1, min(MAX_INPUT_LINES, len(footer.wrap_input(self._input, cols))))
        composer, caret_row, caret_col = footer.composer_rows(
            self._input, self._cursor, cols, input_rows
        )
        rows = [footer.rule(cols), *composer]
        menu = self._menu
        if menu is not None:
            rows.append(footer.rule(cols))
            rows.extend(
                footer.menu_rows(
                    cols=cols,
                    items=menu["items"],
                    selected=menu["selected"],
                    header=menu.get("header", ""),
                    window=MENU_WINDOW,
                )
            )
        rows.append(footer.rule(cols))
        rows.append(self._hint(cols))
        caret = (len(rows) - 2 - len(composer) + caret_row, caret_col)
        return rows, caret

    def _hint(self, cols: int) -> str:
        rt = self.runtime
        right = ""
        if self._is_running:
            right = footer.activity_note(
                self._run_label, self._run_started, time.monotonic(), len(self._pending_queue)
            )
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
        foot, caret = self._footer(cols)
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
