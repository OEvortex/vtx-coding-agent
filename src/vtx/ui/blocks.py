import contextlib
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Literal

from rich.style import Style
from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.message import Message
from textual.widgets import Label, Static

from vtx import config
from vtx.core.types import ImageContent
from vtx.diff_display import DIFF_BG_PAD_MARKER
from vtx.permissions import ApprovalResponse, AskUserOption
from vtx.tools.base import BaseTool

from .formatting import (
    find_stable_block_boundary,
    format_bash_command,
    format_markdown,
    format_markdown_block,
    markdown_render_width,
    strip_markdown_for_collapsed_text,
)
from .input import AskUserInput

_UPDATE_COMMAND = "uv tool upgrade vtx-coding-agent"

# Sentinel label for the synthetic "Other" row in the ask_user picker.
# Stored as a plain string so it round-trips through AskUserOption's
# label type and never collides with a real option the LLM supplied.
ASK_USER_OTHER_LABEL = "\u0000__vtx_ask_user_other__\u0000"
ASK_USER_OTHER_DISPLAY = "Other (type your own answer)"


@dataclass(frozen=True)
class LaunchWarning:
    message: str
    severity: Literal["warning", "error"] = "warning"


def stylize_badge_markers(text: Text, markers: Iterable[str]) -> None:
    badge_style = f"{config.ui.colors.badge.label} bold"
    plain = text.plain
    for marker in markers:
        search_start = 0
        while True:
            start = plain.find(marker, search_start)
            if start == -1:
                break
            text.stylize(badge_style, start, start + len(marker))
            search_start = start + len(marker)


class _StreamingMarkdownMixin:
    """Block-cached markdown streaming.

    The current unfinished line is buffered until a newline arrives. Completed text is
    split at stable block boundaries (blank lines outside code fences). Closed blocks
    are rendered once and cached, so each refresh only re-renders the open tail block,
    coalesced into the next frame. `_flush_streaming` does one full render at the end,
    so the final display never carries streaming artifacts.
    """

    _pending: str
    _completed: str
    _completed_display: Text
    _committed_blocks: list[Text]
    _committed_len: int
    _committed_width: int
    _stream_update_pending: bool
    _stream_finalized: bool
    # Provided by Textual's Static widget at runtime
    call_after_refresh: Callable[[Callable[[], None]], object]

    def _init_streaming(self) -> None:
        self._pending = ""
        self._completed = ""
        self._completed_display = Text()
        self._committed_blocks = []
        self._committed_len = 0
        self._committed_width = 0
        self._stream_update_pending = False
        self._stream_finalized = False

    def _streaming_update_label(self, display: Text) -> None:
        raise NotImplementedError

    def _streaming_pending_style(self) -> str | None:
        return None

    def _refresh_completed_display(self) -> None:
        width = markdown_render_width()
        if width != self._committed_width:  # cached renders are stale after a resize
            self._committed_blocks = []
            self._committed_len = 0
            self._committed_width = width

        boundary = find_stable_block_boundary(self._completed)
        if boundary > self._committed_len:
            block = format_markdown_block(self._completed[self._committed_len : boundary], width)
            # Some source renders to nothing (HTML comments, link reference definitions).
            # An empty entry here would add a stray blank gap to every later join.
            if block.plain:
                self._committed_blocks.append(block)
            self._committed_len = boundary

        tail = self._completed[self._committed_len :]
        parts = [*self._committed_blocks]
        if tail.strip():
            tail_block = format_markdown_block(tail, width)
            if tail_block.plain:
                parts.append(tail_block)
        self._completed_display = Text("\n\n").join(parts) if parts else Text()

    def _render_streaming_display(self) -> Text:
        display = self._completed_display.copy()
        completed_needs_separator = self._completed.endswith("\n") or self._completed.endswith(
            "\r"
        )

        if (
            not self._stream_finalized
            and completed_needs_separator
            and not self._pending
            and display.plain
        ):
            display.append("\n")

        return display

    def _schedule_streaming_update(self) -> None:
        if self._stream_update_pending:
            return
        self._stream_update_pending = True
        self.call_after_refresh(self._flush_streaming_update)

    def _flush_streaming_update(self) -> None:
        self._stream_update_pending = False
        if self._stream_finalized:
            # An update scheduled by the last newline can fire after finalize() already
            # put the final render on the label. Don't overwrite it.
            return
        self._refresh_completed_display()
        self._streaming_update_label(self._render_streaming_display())

    def _append_streaming(self, text: str) -> None:
        self._pending += text

        last_nl = self._pending.rfind("\n")
        if last_nl != -1:
            self._completed += self._pending[: last_nl + 1]
            self._pending = self._pending[last_nl + 1 :]
            self._schedule_streaming_update()

    def _flush_streaming(self) -> Text:
        self._stream_finalized = True
        if self._pending:
            self._completed += self._pending
            self._pending = ""
        self._completed_display = format_markdown(self._completed) if self._completed else Text()
        return self._render_streaming_display()


class ThinkingBlock(_StreamingMarkdownMixin, Static):
    ALLOW_SELECT = True
    can_focus = False

    def __init__(self, content: str = "", finalized: bool = False, **kwargs) -> None:
        super().__init__(**kwargs)
        self._content = content
        self._finalized = finalized
        self._label: Label | None = None
        self._init_streaming()
        self.add_class("thinking-block")

    def compose(self) -> ComposeResult:
        if self._finalized and self._content and config.ui.collapse_thinking:
            yield Label(self._format_collapsed(), id="thinking-content", markup=False)
        else:
            yield Label(self._content, id="thinking-content", markup=False)

    @property
    def label(self) -> Label:
        if self._label is None:
            self._label = self.query_one("#thinking-content", Label)
        return self._label

    def _format_collapsed(self) -> Text:
        """Show collapsed thinking with configured line count."""
        lines = self._content.strip().split("\n")
        max_lines = self._get_max_lines()
        style = f"{config.ui.colors.dim} italic"

        if max_lines is None:
            # No truncation — show everything
            text = Text()
            for i, line in enumerate(lines):
                if i > 0:
                    text.append("\n")
                text.append(strip_markdown_for_collapsed_text(line.strip()), style=style)
            return text

        visible = lines[:max_lines]
        text = Text()
        for i, line in enumerate(visible):
            if i > 0:
                text.append("\n")
            text.append(strip_markdown_for_collapsed_text(line.strip()), style=style)

        remaining = len(lines) - max_lines
        if remaining > 0:
            text.append(f" ... ({remaining} more lines)", style=style)
        return text

    @staticmethod
    def _get_max_lines() -> int | None:
        setting = config.ui.thinking_lines
        if setting == "none":
            return None
        return int(setting)

    def _streaming_update_label(self, display: Text) -> None:
        self.label.update(display)
        return None

    def _streaming_pending_style(self) -> str | None:
        return f"{config.ui.colors.dim} italic"

    async def append(self, text: str) -> None:
        self._content += text
        self._append_streaming(text)

    def finalize(self) -> None:
        if self._content and not self._finalized:
            self._finalized = True
            self.label.update(self._flush_streaming())
            self.call_after_refresh(self._do_finalize)

    def _do_finalize(self) -> None:
        if self._content and config.ui.collapse_thinking:
            self.label.update(self._format_collapsed())

    def set_content(self, text: str) -> None:
        self._content = text
        self._finalized = True
        if config.ui.collapse_thinking:
            self.label.update(self._format_collapsed())
        else:
            self.label.update(text)


class ContentBlock(_StreamingMarkdownMixin, Static):
    # TODO: Consider switching to Textual's Markdown widget + MarkdownStream.write() for
    # incremental rendering during streaming. This would eliminate the visual reflow when
    # finalize() converts plain text to markdown. The tradeoff: our custom Rich-based
    # formatting (CustomMarkdown with LeftJustifiedHeading, PlainListItem, PlainCodeBlock)
    # is incompatible with Textual's Markdown pipeline, so we'd need to reimplement those
    # customizations using Textual's theming/CSS system. See toad and mistral-vibe for
    # reference implementations using MarkdownStream.

    ALLOW_SELECT = True
    can_focus = False

    def __init__(self, content: str = "", finalized: bool = False, **kwargs) -> None:
        super().__init__(**kwargs)
        self._content = content
        self._finalized = finalized
        self._label: Label | None = None
        self._init_streaming()
        self.add_class("content-block")

    def compose(self) -> ComposeResult:
        if self._finalized and self._content:
            yield Label(format_markdown(self._content), id="content-text", markup=False)
        else:
            yield Label(self._content, id="content-text", markup=False)

    @property
    def label(self) -> Label:
        if self._label is None:
            self._label = self.query_one("#content-text", Label)
        return self._label

    def _streaming_update_label(self, display: Text) -> None:
        self.label.update(display)
        return None

    async def append(self, text: str) -> None:
        self._content += text
        self._append_streaming(text)

    def finalize(self) -> None:
        if self._content and not self._finalized:
            self._finalized = True
            self.label.update(self._flush_streaming())
            self.call_after_refresh(self._do_finalize)

    def _do_finalize(self) -> None:
        if self._content:
            self.label.update(format_markdown(self._content))

    def set_content(self, text: str) -> None:
        self._content = text
        self._finalized = True
        self.label.update(format_markdown(self._content))


class ToolBlock(Static):
    """
    Format:
    TOOL_NAME call_msg
    truncated output
    """

    ALLOW_SELECT = True
    can_focus = False
    MAX_HEADER_LINES = 2

    def __init__(
        self,
        name: str = "",
        call_msg: str | None = None,
        icon: str = "→",
        expanded: bool = False,
        tool: BaseTool | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._name = name
        self._icon = icon
        self._call_msg = call_msg
        # ``tool`` is set by ``ChatLog.start_tool`` after construction. It
        # is the bound :class:`vtx.tools.base.BaseTool` instance the
        # block is rendering. Custom subclasses can use it to call
        # ``self.tool.format_call(params)`` or ``self.tool.format_preview``
        # instead of accepting pre-formatted strings from the runner.
        self.tool: BaseTool | None = tool
        self._ui_summary: str | None = None
        self._ui_details: str | None = None
        self._ui_details_full: str | None = None
        self._images: list[ImageContent] | None = None
        self._result_markup: bool = True
        self._expanded: bool = expanded
        self._success: bool | None = None
        self._awaiting_approval: bool = False
        self._approval_preview: str | None = None
        self._approval_selection: ApprovalResponse = ApprovalResponse.APPROVE
        # ask_user state. ``_ask_user_options`` is the user-supplied
        # options; the synthetic "Other" entry is implicit at index
        # ``len(_ask_user_options)``. When ``_ask_user_highlight`` points
        # at "Other" the inline input is shown and any non-empty text +
        # Enter submits custom text.
        self._ask_user_options: list[AskUserOption] = []
        self._ask_user_multi: bool = False
        self._ask_user_toggled: set[str] = set()
        self._ask_user_highlight: int = 0
        # Tracks whether the inline Other input is currently displayed.
        # Used to move focus to the input on show and back to the chat
        # input box on hide, since the picker keys would otherwise be
        # forwarded from the chat input and the user could never type
        # into the Other field.
        self._ask_user_input_visible: bool = False
        # Task-tool live tail. When set, the block renders a compact
        # transcript of in-flight sub-agent activity. Cleared by
        # ``set_result`` so the final transcript wins.
        self._task_live_lines: list[str] | None = None
        self._task_header: str | None = None
        self.add_class("tool-block")
        self._set_state(None)

    def compose(self) -> ComposeResult:
        yield Label(self._format_header(), id="tool-header")
        yield Label("", id="tool-output", classes="tool-output -hidden")
        yield AskUserInput(
            placeholder="Type a custom answer, then press Enter",
            id="ask-user-input",
            classes="-hidden",
        )

    def _format_header(self, truncate: bool = True) -> Text:
        colors = config.ui.colors
        result = Text()
        formatted_name = self._name or ""

        success_style = Style(color=colors.muted, bold=True)
        icon_style: str | Style = success_style
        name_style: str | Style = success_style
        if self._success is None:
            icon_style = colors.running
            name_style = colors.running
        elif self._success is False:
            icon_style = colors.failed
            name_style = colors.failed
        elif self._success is True and config.ui.colored_tool_badge:
            badge_style = Style(color=colors.badge.label, bold=True)
            icon_style = badge_style
            name_style = badge_style

        if self._awaiting_approval:
            result.append(
                " △ Permission required ",
                style=Style(bgcolor=colors.notice, color=colors.bg, bold=True),
            )
            result.append("\n\n")

        result.append(f"{self._icon} ", style=icon_style)
        result.append(formatted_name, style=name_style)

        if self._call_msg:
            result.append(" ")
            result.append_text(self._format_call_msg(truncate=truncate))

        if self._ui_summary:
            result.append(" ")
            summary = self._render_markup_safe(self._ui_summary)
            result.append_text(summary)

        if self._success is None and not self._awaiting_approval and not self._call_msg:
            result.append(" ...", style=colors.dim)

        return result

    def _format_call_msg(self, truncate: bool = True) -> Text:
        if not self._call_msg:
            return Text()

        if truncate:
            lines = self._call_msg.split("\n")
            if len(lines) > self.MAX_HEADER_LINES:
                content = "\n".join(lines[: self.MAX_HEADER_LINES])
                content += f"\n... ({len(lines) - self.MAX_HEADER_LINES} more lines)"
            else:
                content = self._call_msg
        else:
            content = self._call_msg

        if self._name == "bash":
            return format_bash_command(content)

        rendered = self._render_markup_safe(content)
        return Text(rendered.plain, style=config.ui.colors.dim)

    def _render_markup_safe(self, content: str) -> Text:
        try:
            text = Text.from_markup(content)
        except Exception:
            return Text(content)

        for span in text.spans:
            style = span.style
            if isinstance(style, str):
                try:
                    Style.parse(style)
                except Exception:
                    return Text(content)

        return text

    def _pad_diff_backgrounds(self, text: Text, width: int) -> Text:
        if DIFF_BG_PAD_MARKER not in text.plain or width <= 0:
            return text

        result = Text()
        lines = text.split("\n", allow_blank=True)
        for index, line in enumerate(lines):
            marker_pos = line.plain.find(DIFF_BG_PAD_MARKER)
            if marker_pos != -1:
                line = line.copy()
                marker_end = marker_pos + len(DIFF_BG_PAD_MARKER)
                marker_spans = [span for span in line.spans if span.start <= marker_pos < span.end]
                marker_style = marker_spans[0].style if marker_spans else None
                line.plain = line.plain[:marker_pos] + line.plain[marker_end:]
                line.spans = [
                    span
                    for span in line.spans
                    if not (span.start >= marker_pos and span.end <= marker_end)
                ]
                padding = max(0, width - len(line.plain))
                if padding:
                    line.append(" " * padding, style=marker_style)
            if index > 0:
                result.append("\n")
            result.append_text(line)
        return result

    def _set_state(self, success: bool | None) -> None:
        self.remove_class("-pending", "-success", "-error", "-approval")
        if success is None:
            if self._awaiting_approval:
                self.add_class("-approval")
            else:
                self.add_class("-pending")
        elif success:
            self.add_class("-success")
        else:
            self.add_class("-error")

    def show_approval(
        self, preview: str | None = None, selected: ApprovalResponse | None = None
    ) -> None:
        self._awaiting_approval = True
        self._approval_preview = preview
        if selected is not None:
            self._approval_selection = selected
        self._set_state(None)
        self.query_one("#tool-header", Label).update(self._format_header())
        self._render_approval_output()

    def update_approval_selection(self, selected: ApprovalResponse) -> None:
        if not self._awaiting_approval:
            return
        self._approval_selection = selected
        self._render_approval_output()

    def _render_approval_output(self) -> None:
        output = self.query_one("#tool-output", Label)
        self.remove_class("-with-details")
        output.remove_class("-hidden")
        output.remove_class("-details")

        content = Text()
        if self._approval_preview:
            content.append_text(self._render_markup_safe(self._approval_preview))
            content.append("\n\n")
        content.append_text(self._format_approval_controls(self._approval_selection))
        output.update(content)

    def hide_approval(self) -> None:
        self._awaiting_approval = False
        self._approval_preview = None
        self._approval_selection = ApprovalResponse.APPROVE
        self._set_state(None)
        self.query_one("#tool-header", Label).update(self._format_header())
        output = self.query_one("#tool-output", Label)
        self.remove_class("-with-details")
        output.remove_class("-details")
        output.add_class("-hidden")
        output.update(Text(""))

    def _format_approval_controls(
        self, selected: ApprovalResponse = ApprovalResponse.APPROVE
    ) -> Text:
        colors = config.ui.colors
        text = Text()
        # The non-selected button uses the dim panel_alt background; the
        # selected one gets the accent. Direct y/n keys submit immediately;
        # left/right move the highlight; enter submits the highlight.
        approve_selected = selected == ApprovalResponse.APPROVE
        approve_style = Style(
            bgcolor=colors.accent if approve_selected else colors.panel_alt,
            color=colors.bg if approve_selected else colors.dim,
            bold=True,
        )
        deny_style = Style(
            bgcolor=colors.accent if not approve_selected else colors.panel_alt,
            color=colors.bg if not approve_selected else colors.dim,
            bold=True,
        )
        text.append("[y] approve ", style=approve_style)
        text.append("  ")
        text.append("[n] deny ", style=deny_style)
        text.append("  ")
        text.append("(← → enter)", style=Style(color=colors.dim))
        return text

    # -- ask_user rendering ---------------------------------------------------

    @property
    def is_awaiting_ask_user(self) -> bool:
        return bool(self._ask_user_options) or self.has_class("-ask-user")

    def show_ask_user(self, options: list[AskUserOption], multi_select: bool) -> None:
        """Render the ask_user picker into the tool block body.

        ``options`` is the LLM-supplied list; the synthetic "Other" row
        is appended implicitly. The first row is highlighted by default.

        Safe to call before the widget is mounted — the DOM updates
        become no-ops, which keeps unit tests from needing a live app.
        """
        self._ask_user_options = list(options)
        self._ask_user_multi = multi_select
        self._ask_user_toggled = set()
        self._ask_user_highlight = 0
        self.add_class("-ask-user")
        self._set_state(None)
        self._safe_update(
            lambda: self.query_one("#tool-header", Label).update(self._format_header())
        )
        self._safe_update(self._render_ask_user_output)
        self._set_ask_user_input_visible(False)

    def update_ask_user_selection(self, highlight: int, toggled: set[str] | None = None) -> None:
        if not self.is_awaiting_ask_user:
            return
        max_idx = len(self._ask_user_options)  # +1 for "Other"
        self._ask_user_highlight = max(0, min(highlight, max_idx))
        if toggled is not None:
            self._ask_user_toggled = set(toggled)
        self._safe_update(self._render_ask_user_output)
        # Reveal the inline input only when the user picked "Other".
        self._set_ask_user_input_visible(self._highlight_is_other())

    def hide_ask_user(self) -> None:
        self._ask_user_options = []
        self._ask_user_toggled = set()
        self._ask_user_highlight = 0
        self._ask_user_multi = False
        self.remove_class("-ask-user")
        self._set_ask_user_input_visible(False)
        self._set_state(None)
        self._safe_update(
            lambda: self.query_one("#tool-header", Label).update(self._format_header())
        )
        self._safe_update(self._hide_ask_user_output)

    def _hide_ask_user_output(self) -> None:
        try:
            output = self.query_one("#tool-output", Label)
        except Exception:
            return
        self.remove_class("-with-details")
        output.remove_class("-details")
        output.add_class("-hidden")
        output.update(Text(""))

    def _safe_update(self, fn) -> None:
        """Run ``fn`` and swallow DOM errors (e.g. widget not yet mounted)."""
        with contextlib.suppress(Exception):
            fn()

    def _highlight_is_other(self) -> bool:
        return self._ask_user_highlight == len(self._ask_user_options)

    def _render_ask_user_output(self) -> None:
        try:
            output = self.query_one("#tool-output", Label)
        except Exception:
            return
        self.remove_class("-with-details")
        output.remove_class("-hidden")
        output.remove_class("-details")
        content = self._format_ask_user_options()
        content.append("\n")
        content.append_text(self._format_ask_user_hint())
        output.update(content)

    def _format_ask_user_options(self) -> Text:
        text = Text()
        for idx, opt in enumerate(self._ask_user_options):
            text.append_text(self._format_ask_user_row(idx, opt.label, opt.description))
            text.append("\n")
        # Synthetic "Other" row
        text.append_text(
            self._format_ask_user_row(len(self._ask_user_options), ASK_USER_OTHER_DISPLAY, "")
        )
        return text

    def _format_ask_user_row(self, idx: int, label: str, description: str) -> Text:
        colors = config.ui.colors
        is_highlight = idx == self._ask_user_highlight
        is_toggled = label in self._ask_user_toggled

        # Highlight style: match the approval pattern — selected row
        # uses accent bg + bg fg, unselected uses panel_alt bg + dim fg.
        if is_highlight:
            chip_style = Style(bgcolor=colors.accent, color=colors.bg, bold=True)
            body_style = Style(color=colors.bg, bold=True)
            desc_style = Style(color=colors.bg)
        else:
            chip_style = Style(bgcolor=colors.panel_alt, color=colors.fg)
            body_style = Style(color=colors.fg)
            desc_style = Style(color=colors.dim)

        text = Text()
        prefix = " "
        if self._ask_user_multi:
            prefix = "✓ " if is_toggled else "○ "
        if is_highlight:
            prefix = "▶ " + (prefix[2:] if len(prefix) > 2 else prefix.lstrip())
        # Number key
        text.append(f"  [{idx + 1}]", style=chip_style)
        text.append(" ")
        # Label + description
        text.append(prefix, style=body_style)
        text.append(label, style=body_style)
        if description:
            text.append("  ")
            text.append(description, style=desc_style)
        return text

    def _format_ask_user_hint(self) -> Text:
        colors = config.ui.colors
        text = Text()
        if self._ask_user_multi:
            text.append("  (1-", style=colors.dim)
            text.append(str(len(self._ask_user_options) + 1), style=colors.dim)
            text.append(" to toggle · space to confirm · esc cancel)", style=colors.dim)
        else:
            text.append("  (1-", style=colors.dim)
            text.append(str(len(self._ask_user_options) + 1), style=colors.dim)
            text.append(" to pick · ↑↓ to move · enter to submit · esc cancel)", style=colors.dim)
        return text

    def _set_ask_user_input_visible(self, visible: bool) -> None:
        with contextlib.suppress(Exception):
            self.query_one("#ask-user-input", AskUserInput).display = visible
        if visible == self._ask_user_input_visible:
            return
        self._ask_user_input_visible = visible
        # Move focus when the inline input is shown or hidden so the
        # user can actually type into it. Picker keys (digits, arrows,
        # j/k, space, enter) would otherwise be forwarded from the
        # chat input and the user could never reach the Other field.
        # Use call_after_refresh so the DOM has caught up with the
        # visibility change before we steal focus.
        self.call_after_refresh(self._sync_ask_user_focus)

    def _sync_ask_user_focus(self) -> None:
        with contextlib.suppress(Exception):
            if self._ask_user_input_visible:
                self.query_one("#ask-user-input", AskUserInput).focus()
            else:
                # Return focus to the chat input box so picker keys
                # (digits/arrows) keep working after the user navigates
                # away from the Other row.
                self.app.query_one("#input-box").focus()

    def update_call_msg(self, call_msg: str) -> None:
        self._call_msg = call_msg
        self.query_one("#tool-header", Label).update(self._format_header())

    def set_result(
        self,
        ui_summary: str | None,
        ui_details: str | None,
        success: bool,
        markup: bool = True,
        ui_details_full: str | None = None,
        images: list[ImageContent] | None = None,
    ) -> None:
        self._ui_summary = ui_summary
        self._ui_details = ui_details
        self._ui_details_full = ui_details_full
        self._images = images
        self._result_markup = markup
        self._success = success
        self._awaiting_approval = False
        # The Task tool's live tail is now done — drop the in-progress
        # events so the final result wins.
        self._task_live_lines = None
        self._set_state(success)
        self._render_result_output()
        self.query_one("#tool-header", Label).update(self._format_header())

    # -- Task tool live progress ------------------------------------------

    def set_task_progress(
        self, subagent_name: str, live_lines: list[str], header: str | None = None
    ) -> None:
        """Render the Task tool's live sub-agent progress tail.

        ``live_lines`` is a compact transcript built by the chat log
        (sub-agent name, recent tool calls, last text delta). Called
        repeatedly while the sub-agent runs. ``set_result`` clears
        the live tail and renders the final transcript instead.
        """
        self._task_live_lines = list(live_lines)
        self._task_header = header or f"sub-agent: {subagent_name}"
        # Force a re-render against the live data.
        if not self._ui_details:
            self._render_result_output()
            self.query_one("#tool-header", Label).update(self._format_header())

    def set_expanded(self, expanded: bool) -> None:
        if self._expanded == expanded:
            return
        self._expanded = expanded
        self._render_result_output()

    def on_resize(self, event: events.Resize) -> None:
        del event
        if self._ui_details or self._ui_details_full:
            self._render_result_output()

    def _render_result_output(self) -> None:
        output = self.query_one("#tool-output", Label)
        ui_details = (
            self._ui_details_full if self._expanded and self._ui_details_full else self._ui_details
        )

        # Live Task tool tail: shown when the sub-agent is still
        # running and the final details haven't been set yet. The
        # tail is cleared by ``set_result``.
        if ui_details is None and self._task_live_lines is not None:
            lines: list[str] = []
            if self._task_header:
                lines.append(self._task_header)
            lines.extend(self._task_live_lines[-12:])
            rendered = Text("\n".join(lines))
            self.remove_class("-compact")
            self.add_class("-with-details")
            output.remove_class("-hidden")
            output.remove_class("-details")
            output.update(rendered)
            return

        if ui_details:
            rendered = (
                self._render_markup_safe(ui_details) if self._result_markup else Text(ui_details)
            )
            is_diff_output = DIFF_BG_PAD_MARKER in rendered.plain
            rendered = self._pad_diff_backgrounds(rendered, output.size.width or self.size.width)
            # Detail blocks need a 1-line gap; drop compact spacing that was
            # applied before we knew this tool would have output.
            self.remove_class("-compact")
            self.add_class("-with-details")
            output.remove_class("-hidden")
            output.remove_class("-details")
            if is_diff_output:
                output.add_class("-diff-output")
            else:
                output.remove_class("-diff-output")
            output.update(rendered)
        elif self._images:
            image_count = len(self._images)
            image_label = "image" if image_count == 1 else "images"
            rendered = Text(f"Attached {image_count} {image_label}", style=config.ui.colors.dim)
            self.remove_class("-compact")
            self.add_class("-with-details")
            output.remove_class("-hidden")
            output.remove_class("-details")
            output.remove_class("-diff-output")
            output.update(rendered)
        else:
            output.update(Text(""))
            self.remove_class("-with-details")
            output.remove_class("-details")
            output.remove_class("-diff-output")
            output.add_class("-hidden")


class UserBlock(Static):
    ALLOW_SELECT = True
    can_focus = False

    def __init__(self, content: str = "", highlighted_skill: str | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self._content = content
        self._highlighted_skill = highlighted_skill
        self.add_class("user-block")
        if highlighted_skill:
            self.add_class("skill-trigger-message")

    def compose(self) -> ComposeResult:
        text = Text()
        if self._highlighted_skill:
            text.append(self._content)
            stylize_badge_markers(text, [f"[{self._highlighted_skill}]", "[query]"])
        else:
            text.append(self._content)

        yield Label(text)


class HandoffLinkBlock(Static):
    ALLOW_SELECT = True
    can_focus = False

    def __init__(
        self,
        label: str,
        target_session_id: str,
        query: str,
        direction: Literal["back", "forward"],
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._label = label
        self._target_session_id = target_session_id
        self._query = query
        self._direction: Literal["back", "forward"] = direction
        self.add_class("handoff-link-block")

    def compose(self) -> ComposeResult:
        link_text = f"{self._target_session_id[:8]} (click to open)"
        handoff_line = f"{self._label} → {link_text}"
        text = Text(f"[handoff]\n{handoff_line}\n\n[query]\n{self._query}")
        stylize_badge_markers(text, ("[handoff]", "[query]"))

        link_start = text.plain.find(link_text)
        if link_start != -1:
            text.stylize(
                f"{config.ui.colors.notice} underline", link_start, link_start + len(link_text)
            )

        yield Label(text)

    def on_click(self, event: events.Click) -> None:
        event.stop()
        if not self._target_session_id:
            return
        self.post_message(
            self.LinkSelected(self, self._target_session_id, self._query, self._direction)
        )

    class LinkSelected(Message):
        def __init__(
            self,
            block: "HandoffLinkBlock",
            target_session_id: str,
            query: str,
            direction: Literal["back", "forward"],
        ) -> None:
            super().__init__()
            self.block = block
            self.target_session_id = target_session_id
            self.query = query
            self.direction = direction


class UpdateAvailableBlock(Static):
    ALLOW_SELECT = True
    can_focus = False

    def __init__(self, latest_version: str, changelog_url: str | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self._latest_version = latest_version
        self._changelog_url = changelog_url
        self.add_class("update-available-block")

    def compose(self) -> ComposeResult:
        notice_color = config.ui.colors.notice
        dim_color = config.ui.colors.dim
        accent_color = config.ui.colors.accent

        text = Text()
        text.append("Update Available", style=f"{notice_color} bold")
        text.append("\n", style=dim_color)
        text.append(f"New version {self._latest_version} is available. ", style=dim_color)
        text.append("Run: ", style=dim_color)
        text.append(_UPDATE_COMMAND, style=accent_color)

        if self._changelog_url:
            text.append("\n", style=dim_color)
            text.append("Changelog: ", style=dim_color)
            text.append(self._changelog_url, style=accent_color)

        yield Label(text)


class LaunchWarningsBlock(Static):
    ALLOW_SELECT = True
    can_focus = False

    def __init__(self, warnings: list[LaunchWarning], **kwargs) -> None:
        super().__init__(**kwargs)
        self._warnings = warnings
        self.add_class("launch-warnings-block")

    def compose(self) -> ComposeResult:
        notice_color = config.ui.colors.notice
        error_color = config.ui.colors.error
        dim_color = config.ui.colors.dim

        text = Text()
        text.append("Launch Warnings", style=f"{notice_color} bold")

        for warning in self._warnings:
            bullet = "\n✗ " if warning.severity == "error" else "\n! "
            style = error_color if warning.severity == "error" else dim_color
            text.append(bullet, style=style)
            text.append(warning.message, style=style)

        yield Label(text)


class TaskToolBlock(ToolBlock):
    """Custom block rendering Task tool with compact live tail and collapsed results."""

    def _render_result_output(self) -> None:
        output = self.query_one("#tool-output", Label)
        ui_details = (
            self._ui_details_full if self._expanded and self._ui_details_full else self._ui_details
        )

        # 1. Live Task tool tail: shown when the sub-agent is still running
        if ui_details is None and self._task_live_lines is not None:
            status = "working"
            current_tool = None
            text_snippet = None

            for line in self._task_live_lines:
                line_str = line.strip()
                if not line_str:
                    continue
                if line_str.startswith("→"):
                    current_tool = line_str.replace("→", "").strip()
                elif line_str.startswith("(") and line_str.endswith(")"):
                    status = line_str[1:-1]
                else:
                    text_snippet = line_str

            parts = []
            if self._task_header:
                sa_name = self._task_header.replace("sub-agent:", "").strip()
                parts.append(f"[{sa_name}]")

            if current_tool:
                parts.append(f"Running {current_tool}...")
            elif text_snippet:
                parts.append(f"Streaming: {text_snippet[:50]}")
            else:
                parts.append(f"{status}...")

            rendered = Text("  " + " ".join(parts), style=config.ui.colors.running)
            self.add_class("-compact")
            self.remove_class("-with-details")
            output.remove_class("-hidden")
            output.remove_class("-details")
            output.update(rendered)
            return

        # 2. Finished state with details (expanded)
        if ui_details:
            rendered = (
                self._render_markup_safe(ui_details) if self._result_markup else Text(ui_details)
            )
            is_diff_output = DIFF_BG_PAD_MARKER in rendered.plain
            rendered = self._pad_diff_backgrounds(rendered, output.size.width or self.size.width)
            self.remove_class("-compact")
            self.add_class("-with-details")
            output.remove_class("-hidden")
            output.remove_class("-details")
            if is_diff_output:
                output.add_class("-diff-output")
            else:
                output.remove_class("-diff-output")
            output.update(rendered)
        # 3. Finished state without details (collapsed by default)
        else:
            output.update(Text(""))
            self.remove_class("-with-details")
            output.remove_class("-details")
            output.remove_class("-diff-output")
            output.add_class("-hidden")
