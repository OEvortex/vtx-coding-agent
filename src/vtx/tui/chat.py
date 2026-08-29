import time
from pathlib import Path
from typing import Literal

from rich.spinner import Spinner
from rich.text import Text
from textual.containers import VerticalScroll
from textual.timer import Timer
from textual.widgets import Label

from vtx.ai.agent.context.skills import Skill
from vtx.ai.agent.tools import BaseTool
from vtx.ai.config import config
from vtx.core import ApprovalResponse
from vtx.core.paths import get_agents_dir
from vtx.core.types import ImageContent
from vtx.tui.blocks import (
    ContentBlock,
    HandoffLinkBlock,
    LaunchWarning,
    LaunchWarningsBlock,
    ThinkingBlock,
    ToolBlock,
    UpdateAvailableBlock,
    UserBlock,
    stylize_badge_markers,
)
from vtx.tui.input import AskUserInput
from vtx.tui.status_lines import WITTY_STATUS_LINES, pick_witty_line

__all__ = ["MAX_CHILDREN", "PRUNE_TO", "WITTY_ROTATE_EVERY_TICKS", "WITTY_STATUS_LINES", "ChatLog"]

MAX_CHILDREN = 300
PRUNE_TO = 200

# How many spinner ticks (0.15s each) before rotating to a new witty line.
WITTY_ROTATE_EVERY_TICKS = 20

_pick_witty_line = pick_witty_line


def _format_skill_label(skill: Skill) -> str:
    global_skills_dir = (get_agents_dir() / "skills").resolve(strict=False)
    skill_path = Path(skill.path).resolve(strict=False)
    if skill_path.is_relative_to(global_skills_dir):
        return f"{skill.name} (global)"
    return skill.name


def _append_aligned_section(
    text: Text,
    title: str,
    rows: list[tuple[str, str]],
    *,
    notice_color: str,
    dim_color: str,
    muted_color: str,
) -> None:
    if text.plain.strip():
        text.append("\n")
    text.append(f"[{title}]\n", style=notice_color)
    if not rows:
        return
    max_key_len = max(len(k) for k, _ in rows)
    for key, value in rows:
        padded_key = key.ljust(max_key_len)
        text.append(f"  {padded_key}  ", style=dim_color)
        text.append(f"{value}\n", style=muted_color)


class ChatLog(VerticalScroll):
    can_focus = False

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._current_block: ThinkingBlock | ContentBlock | None = None
        self._tool_blocks: dict[str, ToolBlock] = {}
        self._tool_output_expanded = False
        self._anchor_released: bool = False
        self._last_status_label: Label | None = None
        self._spinner_label: Label | None = None
        self._spinner: Spinner | None = None
        self._spinner_timer: Timer | None = None
        self._spinner_ticks: int = 0
        self._spinner_line: str = ""
        self._scroll_pending: bool = False
        # Session recap block (header + body labels), tracked for removal.
        self._recap_labels: list[Label] = []
        # Task tool live state: per-tool-call transcript + last text
        # delta, updated by ``apply_task_progress``. Keyed by tool call
        # id; the block for that id must already exist (mounted when
        # the parent turn streamed the tool call).
        self._task_live: dict[str, dict] = {}

    def on_mount(self) -> None:
        self.anchor()

    def _scroll_if_anchored(self, animate: bool = False) -> None:
        if not self._anchor_released:
            self.scroll_end(animate=animate)
            return

        max_y = self.max_scroll_y
        current_y = self.scroll_y

        if abs(max_y - current_y) < 3:
            self._anchor_released = False
            self.scroll_end(animate=animate)

    def _request_scroll(self) -> None:
        """Batch scroll-to-bottom into the next refresh frame.

        Multiple calls between frames coalesce into a single scroll_end(),
        avoiding repeated layout recalculations during fast streaming.
        """
        if not self._scroll_pending:
            self._scroll_pending = True
            self.call_after_refresh(self._flush_scroll)

    def _flush_scroll(self) -> None:
        self._scroll_pending = False
        self._scroll_if_anchored(animate=False)

    def _prune_if_needed(self) -> None:
        children = list(self.children)
        if len(children) <= MAX_CHILDREN:
            return
        to_remove = children[: len(children) - PRUNE_TO]
        active_tool_ids = {tid for tid, block in self._tool_blocks.items() if block in to_remove}
        for tid in active_tool_ids:
            del self._tool_blocks[tid]
        if self._last_status_label in to_remove:
            self._last_status_label = None
        self.call_after_refresh(lambda: self.remove_children(to_remove))

    async def remove_all_children(self) -> None:
        self._stop_spinner()
        children = list(self.children)
        if children:
            await self.remove_children(children)
        self._tool_blocks.clear()
        self._tool_output_expanded = False
        self._current_block = None
        self._last_status_label = None
        self._recap_labels = []

    def on_click(self, event) -> None:
        event.stop()
        from vtx.tui.input import InputBox

        app = self.app
        input_box = app.query_one("#input-box", InputBox)
        input_box.focus()

    def _is_last_child_status(self) -> bool:
        if self._last_status_label is None:
            return False
        children = list(self.children)
        if not children:
            return False
        return children[-1] is self._last_status_label

    def show_status(self, message: str) -> None:
        self._stop_spinner()
        info_color = config.ui.colors.info
        text = Text(f"✓ {message}", style=info_color)

        # If our tracked status label is still the last child, update it
        if self._is_last_child_status() and self._last_status_label is not None:
            self._last_status_label.update(text)
            self._scroll_if_anchored(animate=False)
            return

        # Otherwise create a new status label
        label = Label(text)
        label.add_class("info-message")
        self.mount(label)
        self._last_status_label = label
        self._scroll_if_anchored(animate=False)

    def show_spinner_status(
        self, message: str | None = None, *, tool_name: str | None = None, state: str | None = None
    ) -> None:
        self._stop_spinner()
        self._spinner = Spinner("dots")
        self._spinner_ticks = 0
        self._spinner_tool = tool_name
        self._spinner_state = state
        if message:
            self._spinner_line = message
        else:
            self._spinner_line = _pick_witty_line(tool_name=tool_name, state=state)
        self._spinner_label = Label(self._render_spinner_text(self._spinner_line))
        self._spinner_label.add_class("info-message")
        self.mount(self._spinner_label)
        self._last_status_label = self._spinner_label
        self._spinner_timer = self.set_interval(0.15, self._tick_spinner)
        self._scroll_if_anchored(animate=False)

    def _render_spinner_text(self, line: str) -> Text:
        info_color = config.ui.colors.info
        spinner_text = self._spinner.render(time.time()) if self._spinner else ""
        result = Text()
        result.append(str(spinner_text), style=info_color)
        result.append(f" {line}", style=info_color)
        return result

    def set_spinner_tool(self, tool_name: str | None, state: str | None = None) -> None:
        """Immediately refresh the active spinner line for a new tool/state.

        Bypasses the normal ``_tick_spinner`` cooldown so tool changes show
        context-aware witty lines instantly.
        """
        if self._spinner_label is None or self._spinner is None:
            return
        self._spinner_tool = tool_name
        self._spinner_state = state
        self._spinner_ticks = 0
        self._spinner_line = _pick_witty_line(
            exclude=self._spinner_line, tool_name=tool_name, state=state
        )
        self._spinner_label.update(self._render_spinner_text(self._spinner_line))

    def _tick_spinner(self) -> None:
        if self._spinner_label is None or self._spinner is None:
            return
        self._spinner_ticks += 1
        if self._spinner_ticks >= WITTY_ROTATE_EVERY_TICKS:
            self._spinner_ticks = 0
            self._spinner_line = _pick_witty_line(
                exclude=self._spinner_line, tool_name=self._spinner_tool, state=self._spinner_state
            )
        self._spinner_label.update(self._render_spinner_text(self._spinner_line))

    def _stop_spinner(self) -> None:
        if self._spinner_timer is not None:
            self._spinner_timer.stop()
            self._spinner_timer = None
        self._spinner = None
        self._spinner_label = None
        self._spinner_tool = None
        self._spinner_state = None

    def add_session_info(self, version: str) -> None:
        info_text = Text()
        accent = config.ui.colors.accent
        dim = config.ui.colors.dim
        muted = config.ui.colors.muted

        # Logo
        logo_lines = ("░█░█░███░█░█", "░█░█░░█░░░█░", "░░█░░░█░░█░█")
        for i, line in enumerate(logo_lines):
            info_text.append(line, style=accent)
            if i == len(logo_lines) - 1:
                info_text.append(f" {version}", style=dim)
            info_text.append("\n")

        if config.ui.show_welcome_shortcuts:
            info_text.append("\n")

            shortcut_rows = (
                (
                    ("/", "slash commands"),
                    ("@", "files/dirs"),
                    ("tab", "complete paths"),
                    ("↑/↓", "history"),
                ),
                (
                    ("shift+tab", "switch agent"),
                    ("esc", "to interrupt"),
                    ("shift+enter", "add newline"),
                ),
                (
                    ("ctrl+c", "clear input"),
                    ("ctrl+c x2", "exit"),
                    ("enter", "queue"),
                    ("alt+enter", "steer"),
                ),
                (
                    ("↑/↓", "select queue"),
                    ("ctrl+t", "cycle thinking"),
                    ("ctrl+shift+t", "toggle thinking"),
                ),
            )

            for row_idx, row in enumerate(shortcut_rows):
                for item_idx, (key, desc) in enumerate(row):
                    if item_idx > 0:
                        info_text.append(" • ", style=dim)
                    info_text.append(key, style=muted)
                    info_text.append(f" {desc}", style=dim)
                if row_idx < len(shortcut_rows) - 1:
                    info_text.append("\n")

        info_text.rstrip()

        info_label = Label(info_text)
        info_label.add_class("session-info")
        self.mount(info_label, before=0)

    def add_loaded_resources(
        self, context_paths: list[str], skills: list[Skill], tools: list[BaseTool]
    ) -> None:
        if not context_paths and not skills and not tools:
            return

        dim_color = config.ui.colors.dim
        notice_color = config.ui.colors.notice
        text = Text()

        if tools:
            text.append("[Tools]\n", style=notice_color)
            text.append("  ", style=dim_color)
            text.append(", ".join(tool.name for tool in tools), style=dim_color)
            text.append("\n", style=dim_color)

        if context_paths:
            if tools:
                text.append("\n")
            text.append("[Context]\n", style=notice_color)
            for path in context_paths:
                text.append(f"  {path}\n", style=dim_color)

        if skills:
            if context_paths or tools:
                text.append("\n")
            text.append("[Skills]\n", style=notice_color)
            text.append("  ", style=dim_color)
            text.append(", ".join(_format_skill_label(skill) for skill in skills), style=dim_color)
            text.append("\n", style=dim_color)

        # Remove trailing newline
        text.rstrip()

        label = Label(text)
        label.add_class("info-message")
        label.add_class("loaded-resources")
        self.mount(label)

    def add_agent_details(self, *, rows: list[dict], active: str | None) -> None:
        """Render the ``/agent list`` table."""
        from rich.text import Text

        notice_color = config.ui.colors.notice
        dim_color = config.ui.colors.dim
        muted_color = config.ui.colors.muted
        accent = config.ui.colors.accent
        text = Text()
        text.append("[Handoff agents]\n", style=notice_color)
        if not rows:
            text.append("  (none loaded)\n", style=dim_color)
        else:
            for r in rows:
                marker = "● " if r["name"] == active else "  "
                text.append(marker, style=accent if r["name"] == active else "")
                text.append(r["name"], style=accent if r["name"] == active else "")
                if r.get("icon"):
                    text.append(f"  {r['icon']}", style=dim_color)
                text.append("\n")
                text.append(f"    {r.get('description') or ''}\n", style=muted_color)
                meta_bits = []
                if r.get("tools"):
                    meta_bits.append(f"tools: {', '.join(r['tools'])}")
                if r.get("commands"):
                    meta_bits.append(f"commands: {', '.join(r['commands'])}")
                if r.get("extensions"):
                    meta_bits.append(f"extensions: {', '.join(r['extensions'])}")
                if meta_bits:
                    text.append(f"    {' • '.join(meta_bits)}\n", style=dim_color)
                text.append(f"    {r.get('path', '')}\n", style=dim_color)
        label = Label(text)
        label.add_class("agent-details")
        self.mount(label)

    def add_session_details(
        self,
        *,
        session_dir: str | None,
        session_file: str,
        user_messages: int,
        assistant_messages: int,
        tool_calls: int,
        tool_results: int,
        total_messages: int,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int,
        cache_write_tokens: int,
        total_tokens: int,
    ) -> None:
        notice_color = config.ui.colors.notice
        dim_color = config.ui.colors.dim
        muted_color = config.ui.colors.muted
        colors = dict(notice_color=notice_color, dim_color=dim_color, muted_color=muted_color)
        text = Text("\n")

        file_rows: list[tuple[str, str]] = []
        if session_dir is not None:
            file_rows.append(("Dir", session_dir))
        file_rows.append(("File", session_file))
        _append_aligned_section(text, "File", file_rows, **colors)

        msg_rows = [
            ("User", str(user_messages)),
            ("Assistant", str(assistant_messages)),
            ("Tool Calls", str(tool_calls)),
            ("Tool Results", str(tool_results)),
            ("Total", str(total_messages)),
        ]
        _append_aligned_section(text, "Messages", msg_rows, **colors)

        token_rows = [
            ("Input", f"{input_tokens:,}"),
            ("Output", f"{output_tokens:,}"),
            ("Cache read", f"{cache_read_tokens:,}"),
            ("Cache write", f"{cache_write_tokens:,}"),
            ("Total", f"{total_tokens:,}"),
        ]
        _append_aligned_section(text, "Tokens", token_rows, **colors)

        text.rstrip()
        label = Label(text)
        label.add_class("info-message")
        label.add_class("loaded-resources")
        self.mount(label)

    def add_help_details(self) -> None:
        notice_color = config.ui.colors.notice
        dim_color = config.ui.colors.dim
        muted_color = config.ui.colors.muted
        colors = dict(notice_color=notice_color, dim_color=dim_color, muted_color=muted_color)
        text = Text("\n")

        commands = [
            ("/help", "Show this help"),
            ("/quit", "Quit (or ctrl+c twice)"),
            ("/clear", "Clear conversation history"),
            ("/compact", "Compact current conversation now"),
            ("/model", "Change model (/model gpt-4o)"),
            ("/provider", "Filter /model by provider"),
            ("/themes", "Change UI theme (/themes gruvbox-dark)"),
            ("/permissions", "Change permission mode (/permissions auto)"),
            ("/thinking", "Change thinking level (/thinking high)"),
            ("/notifications", "Toggle notifications (/notifications on)"),
            ("/new", "Start new conversation"),
            ("/handoff", "Start focused handoff in new session"),
            ("/resume", "Resume a session"),
            ("/session", "Show session info and stats"),
            ("/login", "Login to a provider"),
            ("/logout", "Logout from a provider"),
            ("/export", "Export session to HTML file"),
            ("/copy", "Copy last agent response text to clipboard"),
            ("/update", "Update vtx to latest version"),
        ]
        _append_aligned_section(text, "Commands", commands, **colors)

        keybindings = [
            ("@", "File path search (inline)"),
            ("/", "Slash commands (at start of input)"),
            ("escape", "Cancel completion / interrupt agent"),
            ("ctrl+c", "Clear input (press twice to quit)"),
            ("ctrl+t", "Cycle thinking levels"),
            ("ctrl+o", "Toggle tool output expansion"),
            ("↑/↓ on queue", "Select queued messages"),
            ("enter on queue", "Edit selected queued message"),
            ("ctrl+d on queue", "Delete selected queued message"),
            ("ctrl+shift+t", "Toggle thinking visibility"),
            ("shift+tab", "Cycle handoff agent / active profile"),
            ("alt+ctrl+g", "Cycle tool group (within profile)"),
            ("alt+ctrl+p", "Cycle permission mode"),
        ]
        _append_aligned_section(text, "Keybindings", keybindings, **colors)

        label = Label(text)
        label.add_class("info-message")
        label.add_class("loaded-resources")
        self.mount(label)
        self._scroll_if_anchored(animate=False)

    def add_launch_warnings(self, warnings: list[LaunchWarning]) -> None:
        if not warnings:
            return
        self.mount(LaunchWarningsBlock(warnings))
        self._scroll_if_anchored(animate=False)

    def add_user_message(self, content: str, highlighted_skill: str | None = None) -> UserBlock:
        block = UserBlock(content, highlighted_skill=highlighted_skill)
        self.mount(block)
        self._anchor_released = False
        self.scroll_end(animate=False)
        self._prune_if_needed()
        return block

    def add_handoff_link_message(
        self,
        label: str,
        target_session_id: str,
        query: str,
        direction: Literal["back", "forward"],
        prompt: str | None = None,
    ) -> HandoffLinkBlock:
        block = HandoffLinkBlock(
            label=label,
            target_session_id=target_session_id,
            query=query,
            direction=direction,
            prompt=prompt,
        )
        self.mount(block)
        self._scroll_if_anchored(animate=False)
        self._prune_if_needed()
        return block

    def add_update_available_message(
        self, latest_version: str, changelog_url: str | None = None
    ) -> UpdateAvailableBlock:
        block = UpdateAvailableBlock(latest_version, changelog_url=changelog_url)
        self.mount(block)
        self._scroll_if_anchored(animate=False)
        self._prune_if_needed()
        return block

    def start_thinking(self) -> ThinkingBlock:
        block = ThinkingBlock()
        self.mount(block)
        self._scroll_if_anchored(animate=False)
        self._current_block = block
        return block

    def add_thinking(self, content: str) -> ThinkingBlock:
        block = ThinkingBlock(content, finalized=True)
        self.mount(block)
        self._scroll_if_anchored(animate=False)
        return block

    def start_content(self) -> ContentBlock:
        block = ContentBlock()
        self.mount(block)
        self._scroll_if_anchored(animate=False)
        self._current_block = block
        return block

    def add_content(self, content: str) -> ContentBlock:
        block = ContentBlock(content, finalized=True)
        self.mount(block)
        self._scroll_if_anchored(animate=False)
        return block

    def start_tool(
        self,
        name: str,
        tool_id: str,
        call_msg: str | None = None,
        icon: str = "→",
        tool: BaseTool | None = None,
    ) -> "ToolBlock":
        """Mount a new tool block in the chat log.

        If ``tool`` is given and ``tool.ui_block`` is set, the chat log
        instantiates that class instead of the default
        :class:`ToolBlock`. Custom blocks can subclass ``ToolBlock`` to
        inherit the default rendering, then override ``compose``,
        ``set_result``, ``show_approval``, etc. as needed.

        ``block.tool`` is always set to ``tool`` after construction so
        custom blocks can introspect the bound :class:`BaseTool` (e.g.
        to call ``self.tool.format_call(params)``).
        """
        block_cls = tool.ui_block if (tool is not None and tool.ui_block) else ToolBlock
        block = block_cls(
            name=name, call_msg=call_msg, icon=icon, expanded=self._tool_output_expanded, tool=tool
        )

        # Consecutive tool calls without detail output render compactly (no
        # margin). Tools with detail output (diffs, bash output, etc.) always
        # keep a 1-line gap so they don't visually bleed into neighbours.
        previous = self.children[-1] if self.children else None
        if isinstance(previous, ToolBlock) and not previous.has_class("-with-details"):
            block.add_class("-compact")

        self.mount(block)
        self._scroll_if_anchored(animate=False)
        self._tool_blocks[tool_id] = block
        return block

    async def append_to_current(self, text: str) -> None:
        if self._current_block:
            await self._current_block.append(text)
            self._request_scroll()

    def set_block_content(self, text: str) -> None:
        if self._current_block:
            self._current_block.set_content(text)
            self._request_scroll()

    def append_tool_output(self, tool_id: str, delta: str) -> None:
        """Stream incremental output to a running tool block in real-time."""
        block = self._tool_blocks.get(tool_id)
        if block and hasattr(block, "append_live_output"):
            block.append_live_output(delta)
            self._scroll_if_anchored(animate=False)

    def set_tool_result(
        self,
        tool_id: str,
        ui_summary: str | None,
        ui_details: str | None,
        success: bool,
        markup: bool = True,
        ui_details_full: str | None = None,
        images: list[ImageContent] | None = None,
    ) -> None:
        block = self._tool_blocks.get(tool_id)
        if block:
            block.set_result(
                ui_summary,
                ui_details,
                success,
                markup=markup,
                ui_details_full=ui_details_full,
                images=images,
            )
            if ui_details:
                # All ToolStartEvents arrive during streaming before any
                # results, so later siblings were mounted compact.  Now that
                # this block has detail output, the next tool needs its
                # margin back so the detail block doesn't run into it.
                next_sibling = self._next_child(block)
                if isinstance(next_sibling, ToolBlock):
                    next_sibling.remove_class("-compact")
            self._scroll_if_anchored(animate=False)

    def _next_child(self, child):
        children = list(self.children)
        try:
            index = children.index(child)
        except ValueError:
            return None
        next_index = index + 1
        if next_index >= len(children):
            return None
        return children[next_index]

    def set_tool_output_expanded(self, expanded: bool) -> None:
        self._tool_output_expanded = expanded
        for block in self._tool_blocks.values():
            block.set_expanded(expanded)
        self._scroll_if_anchored(animate=False)

    def toggle_tool_output_expanded(self) -> bool:
        expanded = not self._tool_output_expanded
        self.set_tool_output_expanded(expanded)
        return expanded

    def update_tool_call_msg(self, tool_id: str, call_msg: str) -> None:
        block = self._tool_blocks.get(tool_id)
        if block:
            block.update_call_msg(call_msg)
            self._scroll_if_anchored(animate=False)

    def show_tool_approval(
        self, tool_id: str, preview: str | None = None, selected: ApprovalResponse | None = None
    ) -> None:
        block = self._tool_blocks.get(tool_id)
        if block:
            block.show_approval(preview=preview, selected=selected)
            self._scroll_if_anchored(animate=False)

    def update_tool_approval_selection(self, tool_id: str, selected: ApprovalResponse) -> None:
        block = self._tool_blocks.get(tool_id)
        if block:
            block.update_approval_selection(selected)

    def hide_tool_approval(self, tool_id: str) -> None:
        block = self._tool_blocks.get(tool_id)
        if block:
            block.hide_approval()
            self._scroll_if_anchored(animate=False)

    def show_ask_user(self, tool_id: str, dialog) -> None:
        block = self._tool_blocks.get(tool_id)
        if block:
            block.show_ask_user(dialog=dialog)
            self._scroll_if_anchored(animate=False)

    def rerender_ask_user(self, tool_id: str) -> None:
        block = self._tool_blocks.get(tool_id)
        if block:
            block.rerender_ask_user()
            self._scroll_if_anchored(animate=False)

    def hide_ask_user(self, tool_id: str) -> None:
        block = self._tool_blocks.get(tool_id)
        if block:
            block.hide_ask_user()
            self._scroll_if_anchored(animate=False)

    def ask_user_custom_value(self, tool_id: str) -> str:
        """Return the current value of the inline custom-answer input."""
        block = self._tool_blocks.get(tool_id)
        if not block:
            return ""
        return block.ask_user_custom_value()

    def set_ask_user_custom_value(self, tool_id: str, value: str) -> None:
        """Overwrite the inline custom-answer input (e.g. Ctrl+U clearing)."""
        block = self._tool_blocks.get(tool_id)
        if block:
            block.set_ask_user_custom_value(value)

    def focus_ask_user_input(self, tool_id: str) -> bool:
        """Focus an inline ask_user input; returns True if it was focused."""
        block = self._tool_blocks.get(tool_id)
        if not block:
            return False
        try:
            block.query_one("#ask-user-input", AskUserInput).focus()
            return True
        except Exception:
            return False

    def apply_task_progress(self, tool_call_id: str, event: dict) -> None:
        """Render a Task-tool sub-agent event into the parent tool block.

        Called from the Task tool's progress callback. ``event`` is the
        small dict shape produced by ``TaskTool``:

        * ``kind == "subagent_start"``: opens the live view (model, max_turns)
        * ``kind == "text_delta"``: updates the activity text snippet
        * ``kind == "tool_start"``: counts a tool use and names the activity
        * ``kind == "tool_result"``: clears the active-tool description
        * ``kind == "turn_end"``: updates turn/token counters
        * ``kind == "subagent_end" / "error" / "interrupted" / "cancelled"``:
          freezes the view with an outcome icon; ``set_result`` then
          finalizes the header
        """
        block = self._tool_blocks.get(tool_call_id)
        if block is None:
            return

        kind = event.get("kind")
        state = self._task_live.setdefault(
            tool_call_id,
            {
                "subagent": event.get("subagent", "subagent"),
                "model": None,
                "max_turns": None,
                "turns": 0,
                "tool_uses": 0,
                "tool_counts": {},
                "tokens": 0,
                "active_tool": None,
                "last_text": "",
                "final_text": "",
                "transcript": [],
                "started": time.monotonic(),
                "ended": False,
                "stop_label": None,
                "error": None,
            },
        )
        if event.get("tool_counts"):
            state["tool_counts"] = dict(event["tool_counts"])
        if event.get("final_text"):
            state["final_text"] = event["final_text"]
        if event.get("transcript"):
            state["transcript"] = list(event["transcript"])

        if kind == "subagent_start":
            state["model"] = event.get("model")
            state["max_turns"] = event.get("max_turns")
        elif kind == "text_delta":
            state["last_text"] = (state["last_text"] + event.get("delta", ""))[-400:]
        elif kind == "tool_start":
            state["tool_uses"] += 1
            state["active_tool"] = event.get("tool_name")
        elif kind == "tool_result":
            state["active_tool"] = None
        elif kind == "turn_end":
            state["turns"] = event.get("turns", state["turns"])
            state["tokens"] = event.get("tokens", state["tokens"])
        elif kind == "error":
            state["error"] = event.get("error")
        elif kind in ("interrupted", "cancelled"):
            state["error"] = state["error"] or kind
            self._end_task_state(state, stop_label=kind)
        elif kind == "subagent_end":
            self._end_task_state(state, stop_label=event.get("stop_reason"))

        # Snapshot for the block; internal timing key stripped.
        snapshot = {k: v for k, v in state.items() if k != "started"}
        block.set_task_progress(snapshot)
        self._scroll_if_anchored(animate=False)

    @staticmethod
    def _end_task_state(state: dict, stop_label: str | None) -> None:
        state["ended"] = True
        state["stop_label"] = stop_label or state.get("stop_label")
        state["elapsed_ms"] = (time.monotonic() - state["started"]) * 1000

    def end_block(self) -> None:
        # Finalize content/thinking blocks to render markdown once
        if isinstance(self._current_block, ContentBlock | ThinkingBlock):
            self._current_block.finalize()
        self._current_block = None

    def add_compaction_message(self, tokens_before: int, tokens_after: int = 0) -> None:
        self._stop_spinner()
        # Remove the "Auto-compacting..." status if it's still showing
        if self._is_last_child_status() and self._last_status_label is not None:
            self._last_status_label.remove()
            self._last_status_label = None

        dim_color = config.ui.colors.dim
        token_str = f"{tokens_before:,}"

        text = Text(f"[compaction] Compacted from {token_str}", style=dim_color)
        stylize_badge_markers(text, ("[compaction]",))

        label = Label(text)
        label.add_class("compaction-message")
        self.mount(label)
        self._scroll_if_anchored(animate=False)
        self._scroll_if_anchored(animate=False)

    def add_aborted_message(self, message: str = "Interrupted by user") -> None:
        error_color = config.ui.colors.error
        text = Text(message, style=error_color)
        label = Label(text)
        label.add_class("aborted-message")
        self.mount(label)
        self._scroll_if_anchored(animate=False)

    def clear_trailing_status(self) -> None:
        """Remove the trailing spinner/status label if it is still last."""
        self._stop_spinner()
        if self._is_last_child_status() and self._last_status_label is not None:
            self._last_status_label.remove()
            self._last_status_label = None

    def show_recap(self, text: str) -> None:
        """Render a session recap block (header + styled body) into the log."""
        self.clear_trailing_status()
        colors = config.ui.colors
        header = Label(Text("✦ Recap", style=f"{colors.accent} bold"))
        body = Label(Text(text, style=colors.dim))
        header.add_class("recap-message", "-header")
        body.add_class("recap-message", "-body")
        self._recap_labels = [header, body]
        self.mount(header)
        self.mount(body)
        self._scroll_if_anchored(animate=False)

    def remove_recap(self) -> None:
        labels, self._recap_labels = self._recap_labels, []
        for label in labels:
            # Labels may already be pruned with old children.
            if label in self.children:
                label.remove()

    def add_rich_message(self, renderable, *, error: bool = False) -> None:
        """Mount a pre-rendered rich renderable as an info-level log line."""
        label = Label(renderable)
        label.add_class("info-message")
        if error:
            label.add_class("-error")
        self.mount(label)
        self._scroll_if_anchored(animate=False)

    def add_info_message(self, message: str, error: bool = False, warning: bool = False) -> None:
        info_color = config.ui.colors.info
        error_color = config.ui.colors.error
        notice_color = config.ui.colors.notice

        cleaned_message = message.strip()
        if not cleaned_message:
            cleaned_message = (
                "Unknown error (no details provided)." if error else "No details provided."
            )

        style = info_color
        prefix = "✓ "
        if warning:
            style = notice_color
            prefix = "⚠ "
        if error:
            style = error_color
            prefix = "✗ "

        text = Text(f"{prefix}{cleaned_message}", style=style)
        label = Label(text)
        label.add_class("info-message")
        self.mount(label)
        self._scroll_if_anchored(animate=False)

    def clear_tool_blocks(self) -> None:
        self._tool_blocks.clear()
