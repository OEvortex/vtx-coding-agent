import contextlib
import textwrap
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Literal

from rich.style import Style
from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.message import Message
from textual.timer import Timer
from textual.widgets import Label, Static

from vtx.ai.agent.tools.base import BaseTool
from vtx.coding_agent.config import config
from vtx.coding_agent.diff_display import DIFF_BG_PAD_MARKER
from vtx.core import ApprovalResponse
from vtx.core.types import ImageContent
from vtx.tui import task_ui
from vtx.tui.ask_user import (
    INCOMPLETE_WARNING_PREFIX,
    NEXT_LABEL,
    NO_INPUT_PLACEHOLDER,
    OTHER_DISPLAY,
    REVIEW_HEADING,
    SUBMIT_PICK_CANCEL,
    SUBMIT_PICK_LABEL,
    AskUserDialog,
)
from vtx.tui.formatting import (
    find_stable_block_boundary,
    format_bash_command,
    format_markdown,
    format_markdown_block,
    markdown_render_width,
    strip_markdown_for_collapsed_text,
)
from vtx.tui.input import AskUserInput

_UPDATE_COMMAND = "uv tool upgrade vtx-coding-agent"

ACTIVE_POINTER = "❯ "  # noqa: RUF001 - intentional pointer glyph
INACTIVE_POINTER = "  "
CHECKED_BOX = "[✔]"
UNCHECKED_BOX = "[ ]"
CONFIRMED_MARK = " ✔"
NUMBER_SEPARATOR = ". "
CONTINUATION_INDENT = "  "

# Footer hint fragments; joined with " · " into the footer line.
HINT_ENTER = "enter to select"
HINT_NAV = "↑/↓ to navigate"
HINT_TOGGLE = "space to toggle"
HINT_TAB = "tab to switch questions"
HINT_CANCEL = "esc to cancel"
HINT_SAVE_DRAFT = "enter to save"
HINT_CLEAR = "ctrl+u to clear"
HINT_COLLAPSE = "ctrl+] to collapse"
HINT_EXPAND = "ctrl+] to expand"

# Snug box width bounds for the bordered questionnaire.
MIN_DIALOG_WIDTH = 48
MAX_DIALOG_WIDTH = 100


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
        # ask_user questionnaire. The block renders whatever the
        # :class:`AskUserDialog` state machine holds; the app mutates
        # the dialog from keypresses and asks for a re-render.
        self._ask_dialog: AskUserDialog | None = None
        # Tracks whether an inline input (custom answer) is
        # currently displayed. Used to move focus to the input on show
        # and back to the chat input box on hide, since picker keys
        # would otherwise be forwarded from the chat input and the user
        # could never type into these fields.
        self._ask_user_input_visible: bool = False
        self.add_class("tool-block")
        self._set_state(None)

    def compose(self) -> ComposeResult:
        yield Label(self._format_header(), id="tool-header")
        yield Label("", id="tool-output", classes="tool-output -hidden")
        yield AskUserInput(placeholder=OTHER_DISPLAY, id="ask-user-input", classes="-hidden")

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
        return self._ask_dialog is not None or self.has_class("-ask-user")

    def show_ask_user(self, dialog: AskUserDialog) -> None:
        """Attach the questionnaire dialog and render it into the block.

        Safe to call before the widget is mounted — the DOM updates
        become no-ops, which keeps unit tests from needing a live app.
        """
        self._ask_dialog = dialog
        self.add_class("-ask-user")
        self._set_state(None)
        self._safe_update(
            lambda: self.query_one("#tool-header", Label).update(self._format_header())
        )
        self._safe_update(self._render_ask_user_output)
        self._sync_ask_user_widgets()

    def rerender_ask_user(self) -> None:
        """Re-render the dialog from current state machine contents."""
        if self._ask_dialog is None:
            return
        self._safe_update(self._render_ask_user_output)
        self._sync_ask_user_widgets()

    def hide_ask_user(self) -> None:
        self._ask_dialog = None
        self.remove_class("-ask-user")
        self._set_state(None)
        self._safe_update(
            lambda: self.query_one("#tool-header", Label).update(self._format_header())
        )
        self._sync_ask_user_widgets()
        self._safe_update(self._hide_ask_user_output)

    # Inline input accessors used by the app layer -----------------------------

    def ask_user_custom_value(self) -> str:
        with contextlib.suppress(Exception):
            return self.query_one("#ask-user-input", AskUserInput).value
        return ""

    def set_ask_user_custom_value(self, value: str) -> None:
        with contextlib.suppress(Exception):
            self.query_one("#ask-user-input", AskUserInput).value = value

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

    def _render_ask_user_output(self) -> None:
        try:
            output = self.query_one("#tool-output", Label)
        except Exception:
            return
        if self._ask_dialog is None:
            return
        self.remove_class("-with-details")
        output.remove_class("-hidden")
        output.remove_class("-details")
        output.update(self.format_ask_user_dialog())

    # Dialog drawing ------------------------------------------------------------

    def _dialog_width(self) -> int:
        try:
            term_width = self.app.size.width
        except Exception:
            term_width = 0
        if term_width <= 0:
            return 96
        return max(MIN_DIALOG_WIDTH, min(MAX_DIALOG_WIDTH, term_width - 4))

    def format_ask_user_dialog(self) -> Text:
        """The bordered questionnaire (or its collapsed hint row)."""
        assert self._ask_dialog is not None
        colors = config.ui.colors
        if self._ask_dialog.collapsed:
            return Text(f"  {HINT_EXPAND} · {HINT_CANCEL}", style=Style(color=colors.dim))

        width = self._dialog_width()
        body = self._build_dialog_body(self._ask_dialog, width)
        inner = max(line.cell_len for line in body)

        result = Text()
        result.append(f"╭{'─' * (inner + 2)}╮\n", style=Style(color=colors.accent))
        for line in body:
            result.append("│ ")
            result.append_text(line)
            padding = inner - line.cell_len
            if padding > 0:
                result.append(" " * padding)
            result.append(" │\n", style=Style(color=colors.accent))
        result.append(f"╰{'─' * (inner + 2)}╯", style=Style(color=colors.accent))

        hints = self._format_hint_line(self._ask_dialog)
        if hints.plain.strip():
            result.append("\n  ")
            result.append_text(hints)
        return result

    def _build_dialog_body(self, dialog: AskUserDialog, width: int) -> list[Text]:
        lines: list[Text] = []
        if dialog.is_multi_question:
            lines.append(self._format_tab_bar(dialog))
            lines.append(Text())
        if dialog.is_on_submit_tab():
            lines.extend(self._format_submit_body(dialog))
        else:
            lines.extend(self._format_question_body(dialog, width))
        lines.append(Text())
        return lines

    def _format_tab_bar(self, dialog: AskUserDialog) -> Text:
        colors = config.ui.colors
        answered = set(dialog.answered_indices())
        row = Text()
        row.append(" ← ", style=Style(color=colors.dim))
        for i, question in enumerate(dialog.questions):
            label = question.header or f"Q{i + 1}"
            box = "■" if i in answered else "□"
            if i == dialog.tab:
                style: Style | str = Style(bgcolor=colors.accent, color=colors.bg, bold=True)
            elif i in answered:
                style = Style(color=colors.success)
            else:
                style = Style(color=colors.muted)
            row.append(f" {box} {label} ", style=style)
            row.append(" ", style=Style(color=colors.dim))
        all_answered = len(answered) == len(dialog.questions)
        on_submit = dialog.is_on_submit_tab()
        submit_style: Style | str = (
            Style(bgcolor=colors.accent, color=colors.bg, bold=True)
            if on_submit
            else Style(color=colors.success if all_answered else colors.dim)
        )
        row.append(" ✓ Submit ", style=submit_style)
        row.append(" → ", style=Style(color=colors.dim))
        return row

    def _format_question_body(self, dialog: AskUserDialog, width: int) -> list[Text]:
        colors = config.ui.colors
        question = dialog.questions[dialog.tab]
        state = dialog.current_state()
        content_width = max(20, width - 6)

        lines: list[Text] = []
        for segment in textwrap.wrap(question.question, content_width) or [""]:
            lines.append(Text(segment, style=Style(color=colors.fg, bold=True)))
        lines.append(Text())

        rows = dialog.rows()
        number_width = len(str(len(rows)))
        active_preview: str | None = None

        for row_index, (kind, option_index) in enumerate(rows):
            is_active = row_index == state.row
            pointer = ACTIVE_POINTER if is_active else INACTIVE_POINTER
            number = f"{row_index + 1}{NUMBER_SEPARATOR}".rjust(number_width + 2)

            if kind == "option":
                option = question.options[option_index]
                checked = option_index in state.toggled
                confirmed = not question.multi_select and state.selected == option_index
                label_style = Style(color=colors.accent, bold=True) if is_active else Style()
                line = Text()
                line.append(pointer, style=label_style if is_active else Style(color=colors.dim))
                line.append(number, style=label_style if is_active else Style(color=colors.fg))
                if question.multi_select:
                    box = CHECKED_BOX if checked else UNCHECKED_BOX
                    line.append(
                        box + " ", style=Style(color=colors.accent if checked else colors.muted)
                    )
                line.append(
                    option.label + (CONFIRMED_MARK if confirmed else ""), style=label_style
                )
                lines.append(line)
                if option.description:
                    for segment in textwrap.wrap(option.description, content_width - 2) or [""]:
                        lines.append(
                            Text(CONTINUATION_INDENT + segment, style=Style(color=colors.dim))
                        )
                if is_active and option.preview:
                    active_preview = option.preview
            elif kind == "other":
                draft = state.draft.strip()
                if draft:
                    # A fresh draft shows unconfirmed; a draft equal to
                    # the committed answer keeps its ✔ mark below.
                    label = draft
                    confirmed_custom = bool(state.custom.strip()) and draft == state.custom.strip()
                else:
                    label = OTHER_DISPLAY
                    confirmed_custom = False
                label_style = (
                    Style(color=colors.accent, bold=True) if is_active else Style(color=colors.fg)
                )
                line = Text()
                line.append(pointer, style=label_style if is_active else Style(color=colors.dim))
                line.append(number, style=label_style if is_active else Style(color=colors.fg))
                if question.multi_select:
                    line.append(UNCHECKED_BOX + " ", style=Style(color=colors.muted))
                line.append(
                    label + (CONFIRMED_MARK if confirmed_custom else ""), style=label_style
                )
                lines.append(line)
            else:  # next — commit row for multi-select questions
                label_style = (
                    Style(color=colors.accent, bold=True) if is_active else Style(color=colors.fg)
                )
                line = Text()
                line.append(pointer, style=label_style if is_active else Style(color=colors.dim))
                line.append(NEXT_LABEL, style=label_style)
                lines.append(line)

        if active_preview:
            lines.extend(self._format_preview_box(active_preview, width))
        return lines

    def _format_preview_box(self, preview: str, width: int) -> list[Text]:
        colors = config.ui.colors
        border_style = Style(color=colors.dim)
        inner_width = max(16, min(72, width - 10))
        wrapped: list[str] = []
        for raw_line in preview.splitlines() or [""]:
            wrapped.extend(textwrap.wrap(raw_line, inner_width) or [""])
            if len(wrapped) >= 12:
                break
        if len(wrapped) >= 12:
            wrapped = wrapped[:12]
            if wrapped:
                wrapped[-1] = wrapped[-1][: max(0, inner_width - 1)] + "…"
        lines = [Text("  ┌" + "─" * (inner_width + 2) + "┐", style=border_style)]
        for segment in wrapped:
            pad = " " * (inner_width - len(segment))
            lines.append(Text("  │ " + segment + pad + " │", style=Style(color=colors.muted)))
        lines.append(Text("  └" + "─" * (inner_width + 2) + "┘", style=border_style))
        return lines

    def _question_answer_scalar(self, dialog: AskUserDialog, index: int) -> str | None:
        question = dialog.questions[index]
        state = dialog._states[index]
        if question.multi_select:
            if state.toggled:
                ordered = sorted(state.toggled)
                return ", ".join(question.options[j].label for j in ordered)
            if state.custom.strip():
                return state.custom.strip()
            return None
        if state.selected is not None:
            return question.options[state.selected].label
        if state.custom.strip():
            return state.custom.strip()
        return None

    def _format_submit_body(self, dialog: AskUserDialog) -> list[Text]:
        colors = config.ui.colors
        lines: list[Text] = [Text(REVIEW_HEADING, style=Style(color=colors.fg, bold=True)), Text()]
        for i, question in enumerate(dialog.questions):
            header = question.header or question.question
            if len(header) > 32:
                header = header[:29] + "..."
            answer = self._question_answer_scalar(dialog, i)
            entry = Text(f"{header}: ")
            if answer is None:
                entry.append(NO_INPUT_PLACEHOLDER, style=Style(color=colors.dim))
            else:
                entry.append(answer)
            lines.append(entry)
        unanswered = []
        for i, question in enumerate(dialog.questions):
            if self._question_answer_scalar(dialog, i) is None:
                unanswered.append(question.header or question.question)
        if unanswered:
            warning = Text(INCOMPLETE_WARNING_PREFIX, style=Style(color=colors.notice))
            warning.append(" " + ", ".join(unanswered), style=Style(color=colors.notice))
            lines.append(warning)
        lines.append(Text())
        for i, label in enumerate((SUBMIT_PICK_LABEL, SUBMIT_PICK_CANCEL)):
            active = dialog.submit_row == i
            pointer = ACTIVE_POINTER if active else INACTIVE_POINTER
            style = Style(color=colors.accent, bold=True) if active else Style(color=colors.fg)
            line = Text()
            line.append(pointer, style=style)
            line.append(f"{i + 1}{NUMBER_SEPARATOR}", style=style)
            line.append(label, style=style)
            lines.append(line)
        return lines

    def _format_hint_line(self, dialog: AskUserDialog) -> Text:
        colors = config.ui.colors
        if dialog.collapsed:
            parts = [HINT_EXPAND, HINT_CANCEL]
        elif dialog.input_mode:
            parts = [HINT_SAVE_DRAFT, HINT_NAV, HINT_CLEAR, HINT_CANCEL]
        elif dialog.is_on_submit_tab():
            parts = ["enter to confirm", HINT_NAV, HINT_CANCEL]
        else:
            parts = [HINT_ENTER, HINT_NAV]
            if dialog.questions[min(dialog.tab, len(dialog.questions) - 1)].multi_select:
                parts.append(HINT_TOGGLE)
            if dialog.is_multi_question:
                parts.append(HINT_TAB)
            parts.append(HINT_CANCEL)
            parts.append(HINT_COLLAPSE)
        return Text("  " + " · ".join(parts), style=Style(color=colors.dim))

    # Inline widget sync --------------------------------------------------------

    def _sync_ask_user_widgets(self) -> None:
        """Show/hide + focus the inline input to match dialog state."""
        dialog = self._ask_dialog
        custom_visible = bool(dialog and not dialog.collapsed and dialog.input_mode)
        with contextlib.suppress(Exception):
            custom_input = self.query_one("#ask-user-input", AskUserInput)
            custom_input.display = custom_visible
            if custom_visible and dialog is not None:
                draft = dialog.current_state().draft
                if custom_input.value != draft:
                    custom_input.value = draft
        focus_changed = custom_visible != self._ask_user_input_visible
        self._ask_user_input_visible = custom_visible
        if focus_changed:
            # Move focus after the DOM has caught up with the visibility
            # change so the user can actually type into the field.
            self.call_after_refresh(self._sync_ask_user_focus)

    def _sync_ask_user_focus(self) -> None:
        with contextlib.suppress(Exception):
            if self._ask_user_input_visible:
                self.query_one("#ask-user-input", AskUserInput).focus()
            else:
                # Return focus to the chat input box so picker keys
                # (digits/arrows) keep working after the user navigates
                # away from an inline field.
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
        self._set_state(success)
        self._render_result_output()
        self.query_one("#tool-header", Label).update(self._format_header())

    # -- Task tool live progress ------------------------------------------

    def set_task_progress(self, stats: dict) -> None:
        """Forward a Task-tool sub-agent progress snapshot to the block.

        ``stats`` is a plain data dict built by the chat log (subagent
        name, model, turns, tool_uses, tokens, active_tool, last_text,
        ended/stop_label/error). Blocks that don't override this simply
        ignore it.
        """

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
        prompt: str | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._label = label
        self._target_session_id = target_session_id
        self._query = query
        self._direction: Literal["back", "forward"] = direction
        self._prompt = prompt
        self.add_class("handoff-link-block")

    def compose(self) -> ComposeResult:
        colors = config.ui.colors
        short_id = self._target_session_id[:8]

        if self._direction == "forward":
            badge_text = "HANDOFF"
            badge_style = f"{colors.accent} bold"
            icon = "→"
            icon_style = colors.accent
            link_label = "Handoff session"
        else:
            badge_text = "ORIGIN"
            badge_style = f"{colors.notice} bold"
            icon = "←"
            icon_style = colors.notice
            link_label = "Origin session"

        text = Text()
        text.append(f"[{badge_text}] ", style=badge_style)
        text.append(f"{self._label} ", style=colors.dim)
        text.append(short_id, style=colors.muted)

        text.append("\n\n", style="")
        text.append("Query: ", style=f"{colors.dim} bold")
        text.append(self._query, style="")

        if self._prompt:
            text.append("\n\n", style="")
            text.append("Prompt: ", style=f"{colors.dim} bold")
            prompt_preview = self._prompt.strip()
            if len(prompt_preview) > 120:
                prompt_preview = prompt_preview[:117] + "..."
            text.append(prompt_preview, style=colors.dim)

        text.append("\n\n", style="")
        text.append(f"{icon} {link_label} ", style=icon_style)
        text.append("(click to open)", style=colors.dim)

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
    """Task tool block with pi-style live and finished sub-agent rendering.

    Running (animated ~8fps by a Textual timer, mirroring pi's 80ms widget
    loop)::

        ⠙ haiku · ↻2 · 3 tool uses · 12.3s
          ⎿  reading, running command…

    Finished::

        ✓ general-purpose · ↻5 · 7 tool uses · 33.8k token · 45.6s
          ⎿  Done

    Expanding the block (ctrl+]) still shows the full transcript from
    ``ui_details_full``.
    """

    LIVE_TICK_SECONDS = 0.12

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._task_stats: dict | None = None
        self._task_finished: dict | None = None
        self._task_started: float | None = None
        self._task_elapsed_ms: float | None = None
        self._live_timer: Timer | None = None
        self._spinner_frame: int = 0

    def set_task_progress(self, stats: dict) -> None:
        """Render a progress snapshot from :meth:`ChatLog.apply_task_progress`."""
        if self._task_started is None:
            self._task_started = time.monotonic()
        if stats.get("ended"):
            elapsed = stats.get("elapsed_ms")
            self._task_elapsed_ms = (
                elapsed if elapsed is not None else (time.monotonic() - self._task_started) * 1000
            )
            self._task_finished = dict(stats)
            self._stop_live_timer()
        else:
            self._task_stats = dict(stats)
            self._ensure_live_timer()
        self._render_result_output()

    def _ensure_live_timer(self) -> None:
        if self._live_timer is None:
            self._live_timer = self.set_interval(self.LIVE_TICK_SECONDS, self._on_live_tick)

    def _stop_live_timer(self) -> None:
        if self._live_timer is not None:
            self._live_timer.stop()
            self._live_timer = None

    def _on_live_tick(self) -> None:
        # Animate only while a sub-agent is actually in flight.
        if self._task_finished is not None or self._task_stats is None:
            self._stop_live_timer()
            return
        self._spinner_frame += 1
        self._render_result_output()

    def _current_elapsed_ms(self) -> float | None:
        if self._task_elapsed_ms is not None:
            return self._task_elapsed_ms
        if self._task_started is not None:
            return (time.monotonic() - self._task_started) * 1000
        return None

    def _show_body(self, rendered: Text, *, finished: bool) -> None:
        output = self.query_one("#tool-output", Label)
        if finished:
            self.remove_class("-compact")
            self.add_class("-with-details")
        else:
            self.add_class("-compact")
            self.remove_class("-with-details")
        output.remove_class("-hidden")
        output.remove_class("-details")
        output.update(rendered)

    def _render_result_output(self) -> None:
        # Expanded view keeps VTX's full-transcript expansion semantics.
        if self._expanded and self._ui_details_full:
            super()._render_result_output()
            return

        try:
            self.query_one("#tool-output", Label)
        except Exception:
            return

        if self._task_finished is not None and not self._awaiting_approval:
            rendered = task_ui.render_finished(
                self._task_finished, self._success, self._current_elapsed_ms()
            )
            self._show_body(rendered, finished=True)
            return

        # Live in-flight view; also resumes over an already-finalized
        # background block so late progress stays visible.
        if self._task_stats is not None and not self._awaiting_approval:
            rendered = task_ui.render_live(
                self._task_stats, self._spinner_frame, self._current_elapsed_ms()
            )
            self._show_body(rendered, finished=False)
            return

        super()._render_result_output()
