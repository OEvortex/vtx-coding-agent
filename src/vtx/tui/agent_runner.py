"""Drives agent runs: forwards agent events to the chat UI and handles ! / !! shell
commands typed at the prompt."""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections import deque
from typing import TYPE_CHECKING, Any

from vtx.coding_agent.config import config
from vtx.coding_agent.runtime import ConversationRuntime
from vtx.coding_agent.tools import get_tool
from vtx.coding_agent.tools.bash import BashParams, BashTool
from vtx.core import (
    AgentEndEvent,
    AgentStartEvent,
    ApprovalResponse,
    AskUserEvent,
    AskUserQuestion,
    AskUserResponse,
    BackgroundTaskCompletedEvent,
    CompactionEndEvent,
    CompactionStartEvent,
    ErrorEvent,
    InterruptedEvent,
    RetryEvent,
    TextDeltaEvent,
    TextEndEvent,
    TextStartEvent,
    ThinkingDeltaEvent,
    ThinkingEndEvent,
    ThinkingStartEvent,
    ToolApprovalEvent,
    ToolArgsTokenUpdateEvent,
    ToolEndEvent,
    ToolResultEvent,
    ToolStartEvent,
    TurnEndEvent,
    TurnStartEvent,
    WarningEvent,
)
from vtx.core.notify import NotificationEvent, notify
from vtx.core.types import ImageContent, StopReason, ToolResultMessage
from vtx.tui.ask_user import AskUserDialog
from vtx.tui.chat import ChatLog
from vtx.tui.widgets import InfoBar, StatusLine

_NOTIFY_EVENTS = (AgentEndEvent, ToolApprovalEvent, BackgroundTaskCompletedEvent)


class AgentRunnerMixin:
    _is_running: bool
    _cancel_event: asyncio.Event | None
    _steer_event: asyncio.Event | None
    _interrupt_requested: bool
    _abort_shown: bool
    _current_block_type: str | None
    _hide_thinking: bool
    _approval_future: asyncio.Future[ApprovalResponse] | None
    _approval_tool_id: str | None
    _approval_selection: ApprovalResponse
    _pending_session_switch_id: str | None
    _shell_tool_counter: int
    _pending_queue: deque[tuple[str, str, list[ImageContent] | None]]
    _steer_queue: deque[tuple[str, str, list[ImageContent] | None]]
    _runtime: ConversationRuntime
    # ask_user state (mirrors _approval_*). Lives here so agent_runner
    # owns the data and the app's on_key can read/write it.
    _ask_user_future: asyncio.Future[AskUserResponse] | None
    _ask_user_tool_id: str | None
    _ask_dialog: AskUserDialog | None

    if TYPE_CHECKING:
        app: Any
        query_one: Any
        run_worker: Any

        def _dismiss_recap(self) -> None: ...
        def _arm_recap_timer(self) -> None: ...
        def _update_queue_display(self) -> None: ...
        def _clear_approval_state(self) -> None: ...
        def _show_pending_update_notice_if_idle(self) -> None: ...
        def _goal_context_block(self) -> str: ...
        def _goal_auto_continue_prompt(self) -> tuple[str, str] | None: ...
        def _charge_goal_usage(
            self, *, input_tokens: int, output_tokens: int, elapsed_ms: float
        ) -> None: ...
        def _format_tool_result_text(
            self, message: ToolResultMessage
        ) -> tuple[str, str | None]: ...
        async def _load_session_by_id(self, session_id: str) -> None: ...

    def _should_notify_for_event(self, event: object) -> bool:
        return self._notification_event_type(event) is not None

    def _notification_event_type(self, event: object) -> NotificationEvent | None:
        if not config.notifications.enabled:
            return None
        if not isinstance(event, _NOTIFY_EVENTS):
            return None
        if isinstance(event, AgentEndEvent):
            if event.stop_reason == StopReason.INTERRUPTED:
                return None
            if event.stop_reason == StopReason.ERROR:
                return "error"
            return "completion"
        if isinstance(event, ToolApprovalEvent):
            return "permission"
        if isinstance(event, BackgroundTaskCompletedEvent):
            if event.status == "completed":
                return "completion"
            if event.status == "error":
                return "error"
            return None
        return None

    async def _run_agent(self, prompt: str, images: list[ImageContent] | None = None) -> None:
        chat = self.query_one("#chat-log", ChatLog)
        status = self.query_one("#status-line", StatusLine)
        info_bar = self.query_one("#info-bar", InfoBar)

        agent = self._runtime.prepare_for_run()
        if agent is None:
            chat.add_info_message("Agent not initialized")
            self._is_running = False
            return
        self._dismiss_recap()
        current_prompt = prompt
        current_images = images

        while True:
            was_interrupted = False

            self._cancel_event = asyncio.Event()
            self._steer_event = asyncio.Event()
            self._abort_shown = False
            self._current_block_type = None
            if self._interrupt_requested:
                self._cancel_event.set()

            status.set_status("working")
            turn_started = time.monotonic()

            # Goal state rides along with every user prompt (pi-goal-x
            # injects once per turn; here the block prepends to the query).
            goal_block = ""
            try:
                goal_block = self._goal_context_block()
            except Exception:
                goal_block = ""
            outgoing_prompt = f"{goal_block}\n\n{current_prompt}" if goal_block else current_prompt

            try:
                async for event in agent.run(
                    outgoing_prompt,
                    images=current_images,
                    cancel_event=self._cancel_event,
                    steer_event=self._steer_event,
                ):
                    notification_event = self._notification_event_type(event)
                    if notification_event:
                        notify(notification_event)

                    if await self._render_agent_event(event, chat, status, info_bar):
                        was_interrupted = True

            except Exception as e:
                chat.add_info_message(str(e), error=True)

            self._charge_goal_turn_usage((time.monotonic() - turn_started) * 1000.0)

            if was_interrupted and not self._abort_shown:
                chat.add_aborted_message("Interrupted by user")
                self._abort_shown = True

            self._interrupt_requested = False
            self._cancel_event = None
            self._steer_event = None
            self._clear_approval_state()
            status.set_status("idle")

            if was_interrupted:
                self._pending_queue.clear()
                self._steer_queue.clear()
                self._update_queue_display()
                break

            queued = self._dequeue_next_prompt()
            if queued is None:
                # Auto-continue: a focused active goal schedules the next
                # checkpoint turn instead of returning control to the user.
                checkpoint = None
                try:
                    checkpoint = self._goal_auto_continue_prompt()
                except Exception:
                    checkpoint = None
                if checkpoint is not None and not self._pending_session_switch_id:
                    display, checkpoint_query = checkpoint
                    chat.show_status(f"◈ {display}")
                    current_prompt = checkpoint_query
                    current_images = None
                    continue
                break
            next_display, next_query, next_images = queued
            chat.add_user_message(next_display)
            current_prompt = next_query
            current_images = next_images

        self._is_running = False
        self._arm_recap_timer()

        if self._pending_session_switch_id:
            session_id = self._pending_session_switch_id
            self._pending_session_switch_id = None
            self.run_worker(self._load_session_by_id(session_id), exclusive=True)

        self._show_pending_update_notice_if_idle()

    def _dequeue_next_prompt(self) -> tuple[str, str, list[ImageContent] | None] | None:
        # Steer messages take priority — drain steer queue first
        if self._steer_queue:
            queued = self._steer_queue.popleft()
        elif self._pending_queue:
            queued = self._pending_queue.popleft()
        else:
            return None
        self._update_queue_display()
        return queued

    def _charge_goal_turn_usage(self, elapsed_ms: float) -> None:
        """Charge this turn's token delta to the focused goal (best-effort).

        Session totals are cumulative; only the difference since the last
        charge is billed so a goal never double-charges an interval.
        """
        charge = getattr(self, "_charge_goal_usage", None)
        if not callable(charge):
            return
        input_tokens = output_tokens = 0
        session = getattr(self._runtime, "session", None)
        if session is not None:
            totals = session.token_totals()
            input_tokens = totals.input_tokens
            output_tokens = totals.output_tokens
        previous = getattr(self, "_goal_charged_totals", None)
        if previous is None:
            self._goal_charged_totals = (input_tokens, output_tokens)
            return
        delta_in = max(0, input_tokens - previous[0])
        delta_out = max(0, output_tokens - previous[1])
        self._goal_charged_totals = (input_tokens, output_tokens)
        if delta_in <= 0 and delta_out <= 0:
            return
        with contextlib.suppress(Exception):
            charge(input_tokens=delta_in, output_tokens=delta_out, elapsed_ms=max(0.0, elapsed_ms))

    async def _render_agent_event(
        self, event: object, chat: ChatLog, status: StatusLine, info_bar: InfoBar
    ) -> bool:
        """Render one agent event into the UI. Returns True if it signals interruption."""
        was_interrupted = False

        match event:
            case AgentStartEvent():
                status.set_agent_state("general")

            case TurnStartEvent():
                status.set_agent_state("thinking")

            case ThinkingStartEvent():
                status.set_agent_state("thinking")
                if self._current_block_type != "thinking":
                    if self._current_block_type:
                        chat.end_block()
                    block = chat.start_thinking()
                    if self._hide_thinking:
                        block.add_class("-hidden")
                    self._current_block_type = "thinking"

            case ThinkingDeltaEvent(delta=d):
                await chat.append_to_current(d)

            case ThinkingEndEvent():
                pass

            case TextStartEvent():
                status.set_agent_state("general")
                if self._current_block_type != "content":
                    if self._current_block_type:
                        chat.end_block()
                    chat.start_content()
                    self._current_block_type = "content"

            case TextDeltaEvent(delta=d):
                await chat.append_to_current(d)

            case TextEndEvent():
                pass

            case ToolStartEvent(tool_call_id=id, tool_name=name):
                if self._current_block_type:
                    chat.end_block()
                tool = get_tool(name)
                icon = tool.tool_icon if tool else "→"
                chat.start_tool(name, id, "", icon=icon, tool=tool)
                self._current_block_type = "tool_call"
                status.set_active_tool(name)
                chat.set_spinner_tool(name)
                status.increment_tool_calls()
                status.set_streaming_tokens(0)  # Reset token count for new tool

            case ToolArgsTokenUpdateEvent(token_count=tc):
                status.set_streaming_tokens(tc)

            case ToolEndEvent(tool_call_id=id, display=display):
                chat.update_tool_call_msg(id, display)
                chat.set_spinner_tool(None)

            case ToolApprovalEvent(tool_call_id=id, tool_name=name, display=disp, future=f):
                self.app.bell()
                status.set_agent_state("approval")
                self._approval_selection = ApprovalResponse.APPROVE
                chat.show_tool_approval(
                    id, preview=disp or None, selected=self._approval_selection
                )
                self._approval_future = f
                self._approval_tool_id = id

            case AskUserEvent(tool_call_id=id, questions=questions, future=f):
                self._handle_ask_user(chat, id, questions, f)

            case ToolResultEvent(tool_call_id=id, result=r, file_changes=fc):
                self._approval_future = None
                self._approval_tool_id = None
                tool_block = chat._tool_blocks.get(id)
                tool_name = (tool_block.name if tool_block else None) or "default"
                if r:
                    markup = True
                    ui_summary = r.ui_summary
                    ui_details = r.ui_details
                    ui_details_full = r.ui_details_full
                    if ui_summary is None and ui_details is None and r.content:
                        ui_details, ui_details_full = self._format_tool_result_text(r)
                    success = not r.is_error
                    if not success:
                        status.show_tool_error(tool_name)
                    chat.set_tool_result(
                        id,
                        ui_summary,
                        ui_details,
                        success,
                        markup=markup,
                        ui_details_full=ui_details_full,
                    )
                status.set_active_tool(None)
                if fc:
                    info_bar.update_file_changes(fc.path, fc.added, fc.removed)

            case TurnEndEvent():
                if event.assistant_message and event.assistant_message.usage:
                    usage = event.assistant_message.usage
                    info_bar.update_tokens(
                        usage.input_tokens,
                        usage.output_tokens,
                        usage.cache_read_tokens,
                        usage.cache_write_tokens,
                    )

            case InterruptedEvent():
                was_interrupted = True
                if self._current_block_type:
                    chat.end_block()
                    self._current_block_type = None

            case CompactionStartEvent():
                if self._current_block_type:
                    chat.end_block()
                    self._current_block_type = None
                status.set_agent_state("compacting")
                chat.show_spinner_status(state="compacting")

            case CompactionEndEvent(tokens_before=tb, tokens_after=ta, aborted=ab, reason=why):
                if ab:
                    msg = "Compaction failed"
                    if why:
                        msg += f": {why}"
                    chat.show_status(msg)
                else:
                    chat.add_compaction_message(tb, ta)

            case RetryEvent(attempt=a, total_attempts=t, delay=d, error=e):
                msg = f"Request failed (attempt {a}/{t}), retrying in {d}s; Error: {e}"
                chat.add_info_message(msg, error=True)

            case ErrorEvent(error=e):
                chat.add_info_message(str(e), error=True)

            case WarningEvent(warning=w):
                chat.add_info_message(str(w), warning=True)

            case BackgroundTaskCompletedEvent(task_id=tid, description=desc, status=st):
                chat.add_info_message(f"Background task '{desc}' ({st}) — task_id={tid}")

            case AgentEndEvent(stop_reason=reason):
                if reason == StopReason.INTERRUPTED:
                    was_interrupted = True
                if self._current_block_type:
                    chat.end_block()
                self._current_block_type = None
                status.set_active_tool(None)
                status.set_agent_state(None)

        return was_interrupted

    def _handle_ask_user(
        self,
        chat: ChatLog,
        tool_call_id: str,
        questions: list[AskUserQuestion],
        future: asyncio.Future[AskUserResponse] | None,
    ) -> None:
        """Show the inline ask_user questionnaire and resolve ``future``.

        The dialog renders inside the existing ``ask_user`` tool block
        (matching the approval style): a bordered questionnaire with a
        tab strip for multi-question calls and a Submit review tab.
        Keys are handled by the app's ``on_key`` and routed into the
        :class:`AskUserDialog` state machine.
        """
        if future is None:
            return

        dialog = AskUserDialog(questions)
        chat.show_ask_user(tool_call_id, dialog=dialog)

        self._ask_user_future = future
        self._ask_user_tool_id = tool_call_id
        self._ask_dialog = dialog
        self.app.bell()

    def _handle_shell_command(self, display_text: str) -> None:
        """Handle shell commands prefixed with ! or !!"""
        if self._is_running:
            return

        chat = self.query_one("#chat-log", ChatLog)

        # Determine if we should send output to LLM
        send_to_llm = display_text.startswith("!!")

        command_text = display_text[2:] if send_to_llm else display_text[1:]
        command_text = command_text.strip()

        if not command_text:
            return

        # Add user message showing the command
        chat.add_user_message(display_text)

        # Execute the command
        self._is_running = True
        self.run_worker(self._execute_shell_command(command_text, send_to_llm), exclusive=True)

    async def _execute_shell_command(self, command: str, send_to_llm: bool) -> None:
        """Execute a shell command and display the result"""
        chat = self.query_one("#chat-log", ChatLog)
        status = self.query_one("#status-line", StatusLine)

        try:
            # Create bash tool instance
            bash_tool = BashTool()

            # Create cancellation event for this command
            cancel_event = asyncio.Event()
            self._cancel_event = cancel_event

            # Execute the command
            status.set_status("running")
            # Manual shell output should render like regular bash tool output:
            # collapsed preview with ctrl+o expansion when details are available.
            result = await bash_tool.execute(
                BashParams(command=command), cancel_event=cancel_event, inline_output=False
            )

            # Persist the command and its output so session resume and /export
            # include manual shell commands, not just agent tool calls.
            session = self._runtime.session
            if session is not None:
                prefix = "!!" if send_to_llm else "!"
                session.append_custom_message(
                    "shell_command",
                    f"{prefix}{command}",
                    details={
                        "command": command,
                        "output": result.result or "",
                        "success": result.success,
                    },
                )

            # Start tool block and route the result through ChatLog so manual
            # shell commands use the same rendering/expansion path as agent tools.
            self._shell_tool_counter += 1
            tool_id = f"shell-{self._shell_tool_counter}"
            chat.start_tool("bash", tool_id, f"$ {command}", icon="$")

            # Display the result
            if result.success:
                ui_summary = result.ui_summary
                ui_details = result.ui_details
                markup = True
                if ui_summary is None and ui_details is None:
                    ui_summary = result.result or "(no output)"
                    markup = False
            else:
                ui_summary = result.ui_summary or "Command failed"
                ui_details = result.ui_details or result.result
                markup = True

            chat.set_tool_result(
                tool_id,
                ui_summary,
                ui_details,
                result.success,
                markup=markup,
                ui_details_full=result.ui_details_full,
            )

            # If using !!, send output to LLM for follow-up unless the command was interrupted.
            if send_to_llm and result.result and not cancel_event.is_set():
                prompt = (
                    "Shell command output:\n\n```\n"
                    f"{result.result}\n```\n\nWhat would you like me to do with this?"
                )
                self._is_running = True
                await self._run_agent(prompt)
                return

        except Exception as e:
            chat.add_info_message(f"Error executing command: {e}", error=True)
        finally:
            self._is_running = False
            self._interrupt_requested = False
            self._cancel_event = None
            status.set_status("idle")
            self._arm_recap_timer()
