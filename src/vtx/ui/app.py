"""The Vtx app: widget composition, runtime wiring, key bindings and input routing.

Behaviour is split across focused mixins:

- commands/        - slash-command handling (CommandsMixin)
- session_ui.py    - rendering persisted sessions into the chat log (SessionUIMixin)
- queue_ui.py      - pending/steer message queues (QueueUIMixin)
- completion_ui.py - completion list and selection-mode pickers (CompletionUIMixin)
- agent_runner.py  - driving agent runs and shell commands (AgentRunnerMixin)
- startup.py       - background startup chores (StartupMixin)
- launch.py        - run_tui() entrypoint and exit summary
"""

import asyncio
import os
import time
from collections import deque
from typing import ClassVar

from textual import events, on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Input

from vtx import config, consume_config_warnings
from vtx.config import get_last_selected
from vtx.tools_manager import get_tool_path
from vtx.version import VERSION

from ..context.skills import (
    load_builtin_cmd_skills,
    load_skills,
    merge_registered_skills,
    render_skill_prompt,
)
from ..extensions import load_for_runtime
from ..llm import BaseProvider
from ..llm.base import AuthMode
from ..permissions import ApprovalResponse, AskUserOption, AskUserResponse
from ..runtime import ConversationRuntime
from ..session import Session
from ..tools import DEFAULT_TOOLS, get_tools_with_extensions
from .agent_runner import AgentRunnerMixin
from .autocomplete import DEFAULT_COMMANDS, SlashCommand
from .blocks import HandoffLinkBlock, LaunchWarning
from .chat import ChatLog
from .commands import CommandsMixin
from .completion_ui import CompletionUIMixin
from .floating_list import FloatingList
from .input import InputBox
from .queue_ui import QueueUIMixin
from .selection_mode import SelectionMode
from .session_ui import SessionUIMixin
from .startup import StartupMixin
from .styles import get_styles
from .tree import TreeSelector
from .widgets import InfoBar, QueueDisplay, StatusLine, format_path

_GIT_BRANCH_REFRESH_INTERVAL_SECONDS = 1.0


class Vtx(
    CommandsMixin,
    SessionUIMixin,
    QueueUIMixin,
    CompletionUIMixin,
    AgentRunnerMixin,
    StartupMixin,
    App[None],
):
    CSS = get_styles()
    TITLE = "vtx"
    VERSION = VERSION
    PAUSE_GC_ON_SCROLL = True

    BINDINGS: ClassVar[list] = [
        ("ctrl+c", "handle_ctrl_c", "Clear"),
        Binding("ctrl+d", "handle_ctrl_d", "Delete session", priority=True),
        ("escape", "interrupt_agent", "Interrupt"),
        Binding("left", "tree_page_up", "Tree page up", priority=True),
        Binding("right", "tree_page_down", "Tree page down", priority=True),
        ("ctrl+t", "cycle_thinking_level", "Cycle thinking level"),
        Binding("ctrl+o", "toggle_tool_output", "Toggle tool output", priority=True),
        Binding("ctrl+shift+t", "toggle_thinking", "Toggle thinking", priority=True),
        # Shift+Tab cycles handoff agents (was: cycle_permission_mode).
        # Permission-mode cycling moved to alt+ctrl+p — single chord, leaves
        # ctrl+shift+p free for a future command palette.
        Binding("alt+ctrl+p", "cycle_permission_mode", "Cycle permission mode", priority=True),
        Binding("shift+tab", "cycle_agent", "Cycle handoff agent", priority=True),
    ]

    # Textual registers @on handlers through a metaclass that only scans this
    # class's own namespace, so handlers defined on plain mixins must be
    # re-bound here or they would silently never be dispatched.
    on_completion_update = CompletionUIMixin.on_completion_update
    on_completion_hide = CompletionUIMixin.on_completion_hide
    on_completion_select = CompletionUIMixin.on_completion_select
    on_search_update = CompletionUIMixin.on_search_update
    on_completion_move = CompletionUIMixin.on_completion_move
    on_tree_selected = CompletionUIMixin.on_tree_selected
    on_tree_cancelled = CompletionUIMixin.on_tree_cancelled

    _ANSI_THEME_PREFERENCE = ("textual-ansi", "ansi-dark")

    def _resolve_ansi_theme(self) -> str:
        for name in self._ANSI_THEME_PREFERENCE:
            if name in self.available_themes:
                return name
        return "textual-dark"

    def __init__(
        self,
        cwd: str | None = None,
        model: str | None = None,
        provider: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        resume_session: str | None = None,
        continue_recent: bool = False,
        thinking_level: str | None = None,
        openai_compat_auth_mode: AuthMode | None = None,
        anthropic_compat_auth_mode: AuthMode | None = None,
        extra_extension_paths: list[str] | None = None,
        auto_discover_extensions: bool = True,
        active_agent: str | None = None,
        extra_agent_paths: list[str] | None = None,
        auto_discover_agents: bool = True,
        initial_goal: str | None = None,
    ):
        super().__init__()
        self.theme = self._resolve_ansi_theme()
        self._cwd = cwd or os.getcwd()
        last_selected = get_last_selected()
        initial_model = model or last_selected.model_id or config.llm.default_model
        initial_model_provider = (
            provider
            if provider is not None
            else (
                last_selected.provider
                if last_selected.model_id
                else (config.llm.default_provider if model is None else None)
            )
        )
        self._api_key = api_key
        self._base_url = base_url or config.llm.default_base_url or None
        self._resume_session = resume_session
        self._continue_recent = continue_recent
        initial_thinking_level = (
            thinking_level or last_selected.thinking_level or config.llm.default_thinking_level
        )
        self._openai_compat_auth_mode: AuthMode = (
            openai_compat_auth_mode or config.llm.auth.openai_compat
        )
        self._anthropic_compat_auth_mode: AuthMode = (
            anthropic_compat_auth_mode or config.llm.auth.anthropic_compat
        )
        self._is_running = False
        self._last_ctrl_c_time = 0.0
        self._last_ctrl_d_time = 0.0
        self._ctrl_c_threshold = 2.0
        self._ctrl_d_threshold = 2.0
        self._ctrl_c_timer = None
        self._ctrl_d_timer = None
        self._cancel_event: asyncio.Event | None = None
        self._interrupt_requested = False
        self._pending_session_switch_id: str | None = None
        self._abort_shown = False
        self._current_block_type: str | None = None
        self._approval_future: asyncio.Future[ApprovalResponse] | None = None
        self._approval_tool_id: str | None = None
        self._approval_selection: ApprovalResponse = ApprovalResponse.APPROVE
        # ask_user state — mirrors the approval fields so the app's
        # on_key can route keypresses to the right future.
        self._ask_user_future: asyncio.Future[AskUserResponse] | None = None
        self._ask_user_tool_id: str | None = None
        self._ask_user_options: list[AskUserOption] = []
        self._ask_user_multi: bool = False
        self._ask_user_highlight: int = 0
        self._ask_user_toggled: set[str] = set()
        self._hide_thinking = False
        self._fd_path: str | None = None
        self._selection_mode: SelectionMode | None = None
        self._settings_active: bool = False
        self._pending_api_key_provider: str | None = None
        self._settings_selected_value: str | None = None
        self._shell_tool_counter = 0

        self._pending_queue: deque[tuple[str, str]] = deque(maxlen=QueueDisplay.MAX_QUEUE)
        self._steer_queue: deque[tuple[str, str]] = deque(maxlen=QueueDisplay.MAX_QUEUE)
        self._queue_selection: tuple[bool, int] | None = None
        self._queue_editing: tuple[bool, int, tuple[str, str]] | None = None
        self._steer_event: asyncio.Event | None = None
        self._exit_hints: list[str] = []
        self._session_start_time: float | None = None

        self._pending_update_notice_version: str | None = None
        self._update_notice_shown = False
        self._startup_complete = False
        self._git_branch_refresh_inflight = False
        self._launch_warnings: list[LaunchWarning] = []

        self._tools = get_tools_with_extensions(DEFAULT_TOOLS)

        # Load extensions (project-local, global, config, plus anything the
        # caller passed via ``extra_extension_paths``). Extension tools get
        # merged with the built-ins and the agent loop has an EventBus to
        # fire tool_call / tool_result / agent_* hooks through.
        self._loaded_extensions = load_for_runtime(
            cwd=self._cwd,
            extra_paths=extra_extension_paths,
            auto_discover=auto_discover_extensions,
        )
        for err in self._loaded_extensions.errors:
            self._launch_warnings.append(LaunchWarning(f"extension: {err}"))

        # Load handoff agents (project-local, global, plus the caller's
        # extra paths). Agent-scoped local tools + commands are merged
        # into the active tool set when an agent is active.
        from ..agents import AgentRegistry, load_all_agents

        self._agent_registry = AgentRegistry()
        if auto_discover_agents or extra_agent_paths:
            configured_agents: list[str] = list(extra_agent_paths or [])
            configured_agents.extend(config.agents.files)
            agent_loaded, agent_errors = load_all_agents(
                cwd=self._cwd,
                configured=configured_agents,
                on_event=lambda event, handler: (
                    self._loaded_extensions.bus.on(event, handler)
                    if self._loaded_extensions.bus is not None
                    else None
                ),
            )
            self._agent_registry.agents = agent_loaded
            self._agent_registry.errors = agent_errors
            for err in agent_errors:
                self._launch_warnings.append(LaunchWarning(f"agent: {err}"))

        # Resolve the initial active agent: CLI > env > last-selected > config.
        import os as _os

        ls = get_last_selected()
        env_agent = _os.environ.get("VTX_AGENT")
        desired = (
            active_agent or env_agent or (ls.agent or None) or (config.agents.default or None)
        )
        if desired:
            resolved = self._agent_registry.set_active(desired)
            if resolved is None:
                self._launch_warnings.append(
                    LaunchWarning(f"agent {desired!r} not found; running without an active agent")
                )

        # Build the active tool set: built-ins + session extension tools +
        # active agent's local tools + active agent's local_tools from
        # extensions + per-agent extensions.
        ext_tools = list(self._loaded_extensions.list_extension_tools())
        active = self._agent_registry.active
        if active is not None:
            ext_tools.extend(active.local_tools.values())
            ext_tools.extend(self._loaded_extensions.local_tools_for(active.definition.name))

        if ext_tools:
            self._tools = get_tools_with_extensions(DEFAULT_TOOLS, ext_tools)
        else:
            self._tools = get_tools_with_extensions(DEFAULT_TOOLS)

        self._runtime = ConversationRuntime(
            cwd=self._cwd,
            model=initial_model,
            model_provider=initial_model_provider,
            api_key=self._api_key,
            base_url=self._base_url,
            thinking_level=initial_thinking_level,
            tools=self._tools,
            openai_compat_auth_mode=self._openai_compat_auth_mode,
            anthropic_compat_auth_mode=self._anthropic_compat_auth_mode,
            extensions=self._loaded_extensions.bus,
            agent_registry=self._agent_registry,
            active_agent=self._agent_registry.active,
            agent_extensions=list(self._loaded_extensions.extensions),
        )
        self._runtime.set_loaded_extensions(self._loaded_extensions)
        self._initial_goal = initial_goal

        # Install a Task tool progress forwarder that streams sub-agent
        # events into the chat log. The TUI mounts the chat log later
        # (in ``compose``), so we capture a reference via a callback
        # closure that re-resolves the ChatLog at call time.
        self._install_task_progress_callback()

    def compose(self) -> ComposeResult:
        yield ChatLog(id="chat-log")
        yield QueueDisplay(id="queue-display")
        yield StatusLine(id="status-line")
        yield InputBox(cwd=self._cwd, id="input-box")
        yield FloatingList(window_size=10, label_width=6, id="completion-list")
        yield TreeSelector(id="tree-selector")
        active_name = (
            self._runtime.active_agent.definition.name if self._runtime.active_agent else ""
        )
        info_bar = InfoBar(
            cwd=self._cwd,
            model=self._runtime.model,
            thinking_level=self._runtime.thinking_level,
            hide_thinking=self._hide_thinking,
            id="info-bar",
        )
        info_bar._active_agent = active_name
        yield info_bar

    @staticmethod
    def _thinking_level_class(level: str) -> str:
        return f"-thinking-{level}"

    def _apply_thinking_level_style(self, level: str) -> None:
        input_box = self.query_one("#input-box", InputBox)
        for name in ("none", "minimal", "low", "medium", "high", "xhigh"):
            input_box.remove_class(self._thinking_level_class(name))
        input_box.add_class(self._thinking_level_class(level))

    def _apply_theme(self, theme_id: str) -> None:
        type(self).CSS = get_styles()
        self.refresh_css(animate=False)
        self.query_one("#input-box", InputBox).refresh_theme()
        self._apply_thinking_level_style(self._runtime.thinking_level)

    @property
    def _model(self) -> str:
        return self._runtime.model

    @_model.setter
    def _model(self, value: str) -> None:
        self._runtime.model = value

    @property
    def _model_provider(self) -> str | None:
        return self._runtime.model_provider

    @_model_provider.setter
    def _model_provider(self, value: str | None) -> None:
        self._runtime.model_provider = value

    @property
    def _thinking_level(self) -> str:
        return self._runtime.thinking_level

    @_thinking_level.setter
    def _thinking_level(self, value: str) -> None:
        self._runtime.thinking_level = value

    @property
    def _provider(self) -> BaseProvider | None:
        return self._runtime.provider

    @_provider.setter
    def _provider(self, value: BaseProvider | None) -> None:
        self._runtime.provider = value

    @property
    def _session(self) -> Session | None:
        return self._runtime.session

    @_session.setter
    def _session(self, value: Session | None) -> None:
        self._runtime.session = value

    @property
    def _agent(self):
        return self._runtime.agent

    @_agent.setter
    def _agent(self, value) -> None:
        self._runtime.agent = value

    def _registered_slash_skills(self):
        agent = self._runtime.agent
        skills = agent.context.skills if agent else load_skills(self._cwd).skills
        builtin_skills = load_builtin_cmd_skills().skills
        return merge_registered_skills(skills, builtin_skills)

    def _sync_slash_commands(self) -> None:
        input_box = self.query_one("#input-box", InputBox)
        commands = DEFAULT_COMMANDS.copy()

        for skill in self._registered_slash_skills():
            if not skill.register_cmd:
                continue
            cmd_description = skill.cmd_info
            if not cmd_description:
                cmd_description = skill.description[:32]
                if len(skill.description) > 32:
                    cmd_description = f"{cmd_description}..."
            commands.append(
                SlashCommand(name=skill.name, description=cmd_description, is_skill=True)
            )

        # Extension commands get added last so they appear in the help
        # / autocompletion list. The router in commands/__init__.py looks
        # these up before the built-ins, so they can shadow built-ins if the
        # user wants to.
        for cmd in self._loaded_extensions.all_commands.values():
            commands.append(SlashCommand(name=cmd.name, description=f"[ext] {cmd.description}"))

        input_box.set_commands(commands)

    @staticmethod
    def _build_skill_trigger_message(skill_name: str, description: str, query: str) -> str:
        truncated_description = description[:300]
        if len(description) > 300:
            truncated_description = f"{truncated_description}..."

        parts = [f"[{skill_name}]", truncated_description]
        if query.strip():
            parts.extend(["", "[query]", query.strip()])
        return "\n".join(parts)

    def _install_task_progress_callback(self) -> None:
        """Wire the Task tool's parent context to the chat log.

        The TUI captures sub-agent events (text deltas, tool starts,
        sub-agent end) and renders them into the parent tool block
        after it mounts.
        """
        from ..dispatcher import get_context, set_context
        from .chat import ChatLog

        def _callback(tool_call_id: str, event: dict) -> None:
            try:
                chat = self.query_one("#chat-log", ChatLog)
            except Exception:
                # Chat log not mounted yet (early turn) — drop the event.
                return
            chat.apply_task_progress(tool_call_id, event)

        existing = get_context()
        if existing is None:
            return
        set_context(
            existing.__class__(
                provider=existing.provider,
                model=existing.model,
                model_provider=existing.model_provider,
                base_url=existing.base_url,
                thinking_level=existing.thinking_level,
                agent_registry=existing.agent_registry,
                cwd=existing.cwd,
                system_prompt=existing.system_prompt,
                progress_callback=_callback,
            )
        )

    def _sync_runtime_state(self) -> None:
        # Compatibility hook for mixin/unit-test fakes. Runtime is the source of truth.
        return None

    @on(events.TextSelected)
    def _on_text_selected(self) -> None:
        selection = self.screen.get_selected_text()
        if selection:
            self.copy_to_clipboard(selection)

    def on_mount(self) -> None:
        self._fd_path = get_tool_path("fd")

        input_box = self.query_one("#input-box", InputBox)
        input_box.set_fd_path(self._fd_path)
        input_box.set_commands(DEFAULT_COMMANDS.copy())

        if not self._fd_path:
            self.run_worker(self._collect_file_paths(), exclusive=False)

        self.run_worker(self._ensure_binaries(), exclusive=False)
        self.run_worker(self._check_for_updates(), exclusive=False)
        self.run_worker(self._ensure_models_dev(), exclusive=False)

        # Fire session_start on the extension bus. Sync-only emit because
        # on_mount is itself a sync Textual method; async handlers are
        # skipped with a warning logged by the bus.
        if self._loaded_extensions.bus.handler_count("session_start"):
            self._loaded_extensions.bus.emit_sync("session_start", cwd=self._cwd, session_id="")

        try:
            init_result = self._runtime.initialize(
                resume_session=self._resume_session, continue_recent=self._continue_recent
            )
        except Exception as e:
            self._add_launch_warning(str(e), severity="error")
            chat = self.query_one("#chat-log", ChatLog)
            self._flush_launch_warnings(chat)
            return

        self._session_start_time = time.time()

        self._sync_slash_commands()

        chat = self.query_one("#chat-log", ChatLog)
        chat.add_session_info(VERSION)

        if self._runtime.context:
            chat.add_loaded_resources(
                context_paths=[format_path(f.path) for f in self._runtime.context.agents_files],
                skills=self._runtime.context.skills,
                tools=self._runtime.tools,
            )
            for path, message in self._runtime.context.skill_warnings:
                self._add_launch_warning(f"Skill warning in {format_path(path)}: {message}")

        if init_result.provider_error:
            self._add_launch_warning(init_result.provider_error, severity="error")

        for warning in consume_config_warnings():
            self._add_launch_warning(warning)

        # Apply the --goal CLI flag once the runtime + session are ready.
        # We do this after init_result so any restored goal from
        # --resume wins (matching the "resume takes priority over --goal"
        # convention used by the rest of the CLI flags).
        info_bar = self.query_one("#info-bar", InfoBar)
        restored_goal = self._runtime.goal_manager.goal
        if restored_goal is not None and restored_goal.status == "pursuing":
            info_bar.set_goal("active", started_at=time.time())
        elif self._initial_goal and config.goal.enabled and self._runtime.session is not None:
            try:
                self._runtime.set_goal(self._initial_goal)
                info_bar.set_goal("active", started_at=time.time())
                chat.add_info_message(f"Goal set: {self._initial_goal[:200]}")
            except (RuntimeError, ValueError) as exc:
                self._add_launch_warning(f"goal: {exc}", severity="warning")

        self._flush_launch_warnings(chat)

        info_bar.set_model(self._runtime.model, self._runtime.model_provider)
        info_bar.set_thinking_level(self._runtime.thinking_level)
        self._apply_thinking_level_style(self._runtime.thinking_level)

        if (
            (self._continue_recent or self._resume_session)
            and self._runtime.session
            and self._runtime.session.entries
        ):
            self._render_session_entries(self._runtime.session)
            token_totals = self._runtime.session.token_totals()
            info_bar.set_tokens(
                token_totals.input_tokens,
                token_totals.output_tokens,
                token_totals.context_tokens,
                token_totals.cache_read_tokens,
                token_totals.cache_write_tokens,
            )
            info_bar.set_file_changes(self._runtime.session.file_changes_summary())
            chat.add_info_message("Resumed session")

        self.set_interval(_GIT_BRANCH_REFRESH_INTERVAL_SECONDS, self._refresh_git_branch)

        self._startup_complete = True
        self._show_pending_update_notice_if_idle()
        input_box.focus()

        import gc

        gc.freeze()

    async def on_unmount(self) -> None:
        """Tear down background sub-agents when the TUI exits.

        Called by Textual as the app is being torn down. Cancels any
        still-running background sub-agents so they do not outlive
        the parent session.
        """
        import contextlib

        with contextlib.suppress(Exception):
            await self._runtime.close()

    # -------------------------------------------------------------------------
    # Key bindings
    # -------------------------------------------------------------------------

    def action_handle_ctrl_c(self) -> None:
        input_box = self.query_one("#input-box", InputBox)
        status = self.query_one("#status-line", StatusLine)

        if input_box.text.strip():
            input_box.clear()
            status.hide_exit_hint()
            self._last_ctrl_c_time = 0.0
            return

        now = time.time()
        if now - self._last_ctrl_c_time < self._ctrl_c_threshold:
            self.exit()
        else:
            self._last_ctrl_c_time = now
            status.show_exit_hint()

            if self._ctrl_c_timer:
                self._ctrl_c_timer.stop()
            self._ctrl_c_timer = self.set_timer(
                self._ctrl_c_threshold, lambda: status.hide_exit_hint()
            )

    def action_handle_ctrl_d(self) -> None:
        if self.delete_selected_queue_item():
            return

        if self._selection_mode != SelectionMode.SESSION:
            return

        completion_list = self.query_one("#completion-list", FloatingList)
        if not completion_list.is_visible or completion_list.selected_item is None:
            return

        status = self.query_one("#status-line", StatusLine)
        now = time.time()
        if now - self._last_ctrl_d_time < self._ctrl_d_threshold:
            self._last_ctrl_d_time = 0.0
            if self._ctrl_d_timer:
                self._ctrl_d_timer.stop()
                self._ctrl_d_timer = None
            status.hide_exit_hint()
            self._delete_selected_resume_session()
            return

        self._last_ctrl_d_time = now
        status.show_delete_session_hint()
        if self._ctrl_d_timer:
            self._ctrl_d_timer.stop()
        self._ctrl_d_timer = self.set_timer(
            self._ctrl_d_threshold, lambda: status.hide_exit_hint()
        )

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action in {"tree_page_up", "tree_page_down"}:
            return self._selection_mode == SelectionMode.TREE
        return True

    def action_tree_page_up(self) -> None:
        if self._selection_mode == SelectionMode.TREE:
            self.query_one("#tree-selector", TreeSelector).action_page_up()

    def action_tree_page_down(self) -> None:
        if self._selection_mode == SelectionMode.TREE:
            self.query_one("#tree-selector", TreeSelector).action_page_down()

    def action_interrupt_agent(self) -> None:
        if self._selection_mode == SelectionMode.TREE:
            self.query_one("#tree-selector", TreeSelector).action_cancel()
            return
        if self._is_running:
            self._request_interrupt()

    def _request_interrupt(self, status_message: str | None = "Interrupting...") -> None:
        if not self._is_running:
            return

        self._interrupt_requested = True

        if status_message:
            chat = self.query_one("#chat-log", ChatLog)
            chat.show_status(status_message)

        if self._cancel_event:
            self._cancel_event.set()

    def _reset_ctrl_d_delete_state(self) -> None:
        self._last_ctrl_d_time = 0.0
        if self._ctrl_d_timer:
            self._ctrl_d_timer.stop()
            self._ctrl_d_timer = None

        status = self.query_one("#status-line", StatusLine)
        status.hide_exit_hint()

    def action_toggle_tool_output(self) -> None:
        chat = self.query_one("#chat-log", ChatLog)
        expanded = chat.toggle_tool_output_expanded()
        status = "expanded" if expanded else "collapsed"
        chat.show_status(f"Tool output {status}")

    def action_toggle_thinking(self) -> None:
        self._hide_thinking = not self._hide_thinking
        chat = self.query_one("#chat-log", ChatLog)
        info_bar = self.query_one("#info-bar", InfoBar)

        info_bar.set_thinking_visibility(self._hide_thinking)

        for block in chat.query(".thinking-block"):
            if self._hide_thinking:
                block.add_class("-hidden")
            else:
                block.remove_class("-hidden")

        status = "hidden" if self._hide_thinking else "visible"
        chat.show_status(f"Thinking blocks {status}")

    def action_cycle_permission_mode(self) -> None:
        current_mode = config.permissions.mode
        new_mode = "prompt" if current_mode == "auto" else "auto"
        self._select_permission_mode(new_mode)

    def action_cycle_thinking_level(self) -> None:
        if self._runtime.provider is None:
            return

        levels = self._runtime.effective_thinking_levels
        if not levels:
            return
        current = self._runtime.thinking_level or "none"
        current_idx = levels.index(current) if current in levels else 0
        new_level = levels[(current_idx + 1) % len(levels)]
        self._select_thinking_level(new_level)

    @on(HandoffLinkBlock.LinkSelected)
    def on_handoff_link_selected(self, event: HandoffLinkBlock.LinkSelected) -> None:
        if not event.target_session_id:
            return
        event.stop()
        if self._is_running:
            self._pending_session_switch_id = event.target_session_id
            self._request_interrupt(status_message="Interrupting before handoff...")
            return
        self.run_worker(self._load_session_by_id(event.target_session_id), exclusive=True)

    # -------------------------------------------------------------------------
    # Tool approval
    # -------------------------------------------------------------------------

    def _clear_approval_state(self) -> None:
        self._approval_future = None
        if self._approval_tool_id is not None:
            chat = self.query_one("#chat-log", ChatLog)
            chat.hide_tool_approval(self._approval_tool_id)
            self._approval_tool_id = None

    def deny_pending_approval(self) -> bool:
        if self._approval_future and not self._approval_future.done():
            self._approval_future.set_result(ApprovalResponse.DENY)
            self._clear_approval_state()
            return True
        return False

    def _handle_ask_user_key(self, event: events.Key) -> bool:
        """Handle a keypress while an ask_user picker is active.

        Returns True if the key was consumed (caller should stop
        propagation). Mirrors the approval flow's on_key contract.
        """
        if self._ask_user_future is None or self._ask_user_future.done():
            return False

        key = event.key
        max_index = len(self._ask_user_options)  # +1 for "Other"
        total = max_index + 1

        # Number keys 1..N: pick that option directly. 1-indexed for
        # human readability; the last index is "Other".
        if key.isdigit() and len(key) == 1:
            n = int(key)
            if 1 <= n <= total:
                self._ask_user_pick(n - 1)
                event.prevent_default()
                event.stop()
                return True
            return False

        if key in ("up", "k"):
            self._ask_user_move(-1)
            event.prevent_default()
            event.stop()
            return True
        if key in ("down", "j"):
            self._ask_user_move(1)
            event.prevent_default()
            event.stop()
            return True
        if key == " " and self._ask_user_multi:
            self._ask_user_toggle_highlight()
            event.prevent_default()
            event.stop()
            return True
        if key == "enter":
            self._ask_user_submit_highlighted()
            event.prevent_default()
            event.stop()
            return True
        if key == "escape":
            self._ask_user_cancel()
            event.prevent_default()
            event.stop()
            return True
        return False

    def _ask_user_move(self, delta: int) -> None:
        total = len(self._ask_user_options) + 1
        self._ask_user_highlight = (self._ask_user_highlight + delta) % total
        self._refresh_ask_user_visual()

    def _ask_user_toggle_highlight(self) -> None:
        idx = self._ask_user_highlight
        if idx >= len(self._ask_user_options):
            # Toggling the "Other" row toggles the synthetic label; not
            # useful on its own, so ignore.
            return
        label = self._ask_user_options[idx].label
        if label in self._ask_user_toggled:
            self._ask_user_toggled.discard(label)
        else:
            self._ask_user_toggled.add(label)
        self._refresh_ask_user_visual()

    def _ask_user_pick(self, index: int) -> None:
        """Pick a specific row by 0-based index (user-options + 1 for Other)."""
        if self._ask_user_multi:
            # Multi-select: number toggles a single option and Enter
            # submits. Single-click picks don't make sense here.
            self._ask_user_highlight = index
            label = (
                self._ask_user_options[index].label
                if index < len(self._ask_user_options)
                else None
            )
            if label is not None:
                if label in self._ask_user_toggled:
                    self._ask_user_toggled.discard(label)
                else:
                    self._ask_user_toggled.add(label)
            self._refresh_ask_user_visual()
            return

        # Single-select: 1..N submits immediately
        self._ask_user_highlight = index
        self._ask_user_submit_highlighted()

    def _ask_user_submit_highlighted(self) -> None:
        if self._ask_user_future is None or self._ask_user_future.done():
            return

        idx = self._ask_user_highlight
        if idx >= len(self._ask_user_options):
            # "Other" — submit the inline input value
            chat = self.query_one("#chat-log", ChatLog)
            text = chat.ask_user_input_value(self._ask_user_tool_id or "").strip()
            if not text:
                return  # empty custom answer: ignore
            self._ask_user_future.set_result(AskUserResponse(custom_text=text))
        elif self._ask_user_multi:
            # If the user hasn't toggled anything, fall back to the
            # highlighted option.
            if not self._ask_user_toggled:
                label = self._ask_user_options[idx].label
                self._ask_user_future.set_result(AskUserResponse(selections=(label,)))
            else:
                self._ask_user_future.set_result(
                    AskUserResponse(selections=tuple(sorted(self._ask_user_toggled)))
                )
        else:
            label = self._ask_user_options[idx].label
            self._ask_user_future.set_result(AskUserResponse(selections=(label,)))

        self._clear_ask_user_state()

    def _ask_user_cancel(self) -> None:
        if self._ask_user_future and not self._ask_user_future.done():
            self._ask_user_future.set_result(AskUserResponse())
        self._clear_ask_user_state()

    def _clear_ask_user_state(self) -> None:
        if self._ask_user_tool_id is not None:
            chat = self.query_one("#chat-log", ChatLog)
            chat.hide_ask_user(self._ask_user_tool_id)
        self._ask_user_future = None
        self._ask_user_tool_id = None
        self._ask_user_options = []
        self._ask_user_toggled = set()
        self._ask_user_highlight = 0

    def _refresh_ask_user_visual(self) -> None:
        if self._ask_user_tool_id is None:
            return
        chat = self.query_one("#chat-log", ChatLog)
        chat.update_ask_user_selection(
            self._ask_user_tool_id, self._ask_user_highlight, set(self._ask_user_toggled)
        )

    @on(Input.Submitted)
    def on_ask_user_input_submitted(self, event: Input.Submitted) -> None:
        """Submit the inline 'Other' input as a custom-text answer."""
        if event.input.id != "ask-user-input":
            return
        if self._ask_user_future is None or self._ask_user_future.done():
            return
        text = event.value.strip()
        if not text:
            return
        self._ask_user_future.set_result(AskUserResponse(custom_text=text))
        self._clear_ask_user_state()
        event.stop()

    def on_key(self, event: events.Key) -> None:
        # ask_user takes priority over approval since its picker is
        # more elaborate (number keys, arrows, space, etc.).
        if (
            self._ask_user_future is not None
            and not self._ask_user_future.done()
            and self._handle_ask_user_key(event)
        ):
            return
        if self._approval_future is None or self._approval_future.done():
            return
        # Direct y/n keys still work and submit immediately, matching prior
        # behaviour. Left/right move the highlight between the two buttons
        # without submitting; enter submits the highlighted button.
        if event.key in ("y", "Y"):
            self._approval_future.set_result(ApprovalResponse.APPROVE)
        elif event.key in ("n", "N"):
            self._approval_future.set_result(ApprovalResponse.DENY)
        elif event.key in ("left", "right"):
            self._approval_selection = (
                ApprovalResponse.DENY
                if self._approval_selection == ApprovalResponse.APPROVE
                else ApprovalResponse.APPROVE
            )
            if self._approval_tool_id is not None:
                chat = self.query_one("#chat-log", ChatLog)
                chat.update_tool_approval_selection(
                    self._approval_tool_id, self._approval_selection
                )
            event.prevent_default()
            event.stop()
            return
        elif event.key == "enter":
            self._approval_future.set_result(self._approval_selection)
        else:
            return
        event.prevent_default()
        event.stop()
        self._clear_approval_state()

    # -------------------------------------------------------------------------
    # Input submission
    # -------------------------------------------------------------------------

    @on(InputBox.Submitted)
    def on_input_submitted(self, event: InputBox.Submitted) -> None:
        display_text = event.text.strip()
        if not display_text:
            return

        # Intercept API-key entry: the user is in the middle of /login <provider>
        # and is typing the key. Route it to the auth command instead of the agent.
        if self._selection_mode == SelectionMode.API_KEY:
            self._submit_api_key(event.text)
            return

        if display_text.startswith("/") and self._handle_command(display_text):
            return

        # Handle shell commands (! and !!)
        if display_text.startswith("!") or display_text.startswith("!!"):
            self._handle_shell_command(display_text, event.text)
            return

        query_text = event.query_text.strip()

        selected_skill_name = event.selected_skill_name
        highlighted_skill: str | None = None
        if selected_skill_name:
            selected_skill = next(
                (
                    skill
                    for skill in self._registered_slash_skills()
                    if skill.register_cmd and skill.name == selected_skill_name
                ),
                None,
            )
            if selected_skill:
                skill_query = event.selected_skill_query or ""
                display_text = self._build_skill_trigger_message(
                    selected_skill.name, selected_skill.description, skill_query
                )
                query_text = render_skill_prompt(selected_skill, skill_query)
                highlighted_skill = selected_skill.name

        if self._is_running:
            if event.steer:
                if len(self._steer_queue) >= QueueDisplay.MAX_QUEUE:
                    self.notify("Steer queue full (max 5)", severity="warning", timeout=2)
                    return
                self._steer_queue.append((display_text, query_text))
                if self._steer_event:
                    self._steer_event.set()
            else:
                if len(self._pending_queue) >= QueueDisplay.MAX_QUEUE:
                    self.notify("Queue full (max 5)", severity="warning", timeout=2)
                    return
                self._pending_queue.append((display_text, query_text))
            self._update_queue_display()
            return

        chat = self.query_one("#chat-log", ChatLog)
        chat.add_user_message(display_text, highlighted_skill=highlighted_skill)

        self._is_running = True
        self.run_worker(self._run_agent(query_text), exclusive=True)
