from __future__ import annotations

import contextlib
import json
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, Literal

from ctx.agent.tools.base import BaseTool
from ctx.core import ApprovalResponse
from ctx.core.compaction import SUMMARY_SECTIONS
from ctx.core.config import config
from ctx.protocol.types import ImageContent
from ctx.term import boxed
from ctx.term.diff_display import DIFF_BG_PAD_MARKER
from ctx.term.formatting import (
    find_stable_block_boundary,
    format_markdown,
    format_markdown_block,
    format_tokens,
    markdown_render_width,
    split_open_fence,
    strip_markdown_for_collapsed_text,
)
from ctx.term.input import AskUserInput
from rich.spinner import Spinner
from rich.style import Style
from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.content import Content
from textual.message import Message
from textual.timer import Timer
from textual.widgets import Label, ProgressBar, Static

_UPDATE_COMMAND = "uv tool upgrade ctx-code"

# The tool-output toggle hint is shared by every tool block. Questionnaires
# and other interactive tool UIs live in the product package and own their
# own hints.
HINT_TOOL_EXPAND = "ctrl+o to expand"


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

    Completed text is split at stable block boundaries (blank lines outside code
    fences). Closed blocks are rendered once and cached, so each refresh only
    re-renders the still-open block: the uncommitted tail plus the line currently
    being written. Every delta schedules an update, coalesced into the next refresh
    frame, so text shows up as it arrives instead of waiting on a newline.
    `_flush_streaming` does one full render at the end, so the final display never
    carries streaming artifacts.
    """

    _pending: str
    _completed: str
    _completed_display: Text
    _committed_blocks: list[Text]
    _committed_len: int
    _committed_width: int
    _fence_blocks: list[Text]
    _fence_len: int
    _fence_marker: str
    _stream_update_pending: bool
    _stream_finalized: bool
    # Provided by Textual's Static widget at runtime; typed loosely because the
    # real signature takes *args/**kwargs and returns bool.
    call_after_refresh: Callable[..., bool]

    def _init_streaming(self) -> None:
        self._pending = ""
        self._completed = ""
        self._completed_display = Text()
        self._committed_blocks = []
        self._committed_len = 0
        self._committed_width = 0
        self._reset_fence_cache()
        self._stream_update_pending = False
        self._stream_finalized = False

    def _reset_fence_cache(self) -> None:
        self._fence_blocks = []
        self._fence_len = 0
        self._fence_marker = ""

    def _streaming_update_label(self, display: Text) -> None:
        raise NotImplementedError

    def _refresh_completed_display(self) -> None:
        width = markdown_render_width()
        if width != self._committed_width:  # cached renders are stale after a resize
            self._committed_blocks = []
            self._committed_len = 0
            self._reset_fence_cache()
            self._committed_width = width

        boundary = find_stable_block_boundary(self._completed)
        if boundary > self._committed_len:
            block = format_markdown_block(self._completed[self._committed_len : boundary], width)
            # Some source renders to nothing (HTML comments, link reference definitions).
            # An empty entry here would add a stray blank gap to every later join.
            if block.plain:
                self._committed_blocks.append(block)
            self._committed_len = boundary
            # A blank line can never fall inside a fence, so this only runs while
            # the open block sits outside one and the fence cache starts over.
            self._reset_fence_cache()

        open_text = self._completed[self._committed_len :] + self._pending
        tail, separator = self._refresh_fence_cache(open_text, width)

        parts = [*self._committed_blocks]
        if self._fence_blocks:
            parts.append(Text("\n").join(self._fence_blocks))
        if tail.strip():
            open_block = format_markdown_block(tail, width)
            if open_block.plain:
                parts.append(open_block)
        self._completed_display = Text(separator).join(parts) if parts else Text()

    def _refresh_fence_cache(self, open_text: str, width: int) -> tuple[str, str]:
        """Fold completed code-fence lines into the cache.

        Returns the live tail — already re-wrapped with the fence marker while it
        is still inside one — plus the separator the display parts join with.
        Blank lines inside a fence are illegal, so the block-boundary rule never
        fires there and without this the whole file is re-rendered on every
        single refresh.
        """
        body_len, stable, marker, closed = split_open_fence(open_text)

        if not marker or closed:
            if closed and (self._fence_marker != marker or self._fence_len != stable):
                # The closer just arrived. Re-render the fence once as a single
                # block so the tail after it is ordinary markdown again; one
                # whole-fence render per close is still linear.
                block = format_markdown_block(open_text[:stable], width)
                self._reset_fence_cache()
                if block.plain:
                    self._fence_blocks.append(block)
                self._fence_len = stable
                self._fence_marker = marker
            elif not marker:
                self._reset_fence_cache()
            return open_text[stable:], "\n\n"

        if marker != self._fence_marker:
            self._reset_fence_cache()
            self._fence_marker = marker
            # Start just past the opener: the cached chunks are re-wrapped with
            # the marker, so the opener itself is rendered exactly once, by the
            # chunk that follows it.
            self._fence_len = body_len
        if stable > self._fence_len:
            # Re-wrap with the opener so the fragment still renders as code.
            chunk = format_markdown_block(
                f"{marker}\n{open_text[self._fence_len : stable]}", width
            )
            if chunk.plain:
                self._fence_blocks.append(chunk)
            self._fence_len = stable
        return f"{marker}\n{open_text[stable:]}", "\n"

    def _render_streaming_display(self) -> Text:
        display = self._completed_display.copy()
        # Only reserve the next line when the current one is closed. With a partial
        # line pending the open block already ends with that text, so a trailing
        # newline would just add a blank line under live output.
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

        # Unconditional: the newline only decides what is eligible for block caching,
        # not whether anything is renderable. Gating this on a newline left the text
        # invisible until the line closed, which is what made streaming look buffered.
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


class RunSummaryBlock(Static):
    ALLOW_SELECT = True
    can_focus = False

    class Toggled(Message):
        def __init__(self, block: RunSummaryBlock) -> None:
            super().__init__()
            self.block = block

    def __init__(
        self,
        stats: Any = None,
        *,
        thoughts: Iterable[ThinkingBlock] | None = None,
        tools: Iterable[ToolBlock] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        from ctx.term.run_summary import RunSummaryStats, format_merged_summary_text

        self._stats = stats or RunSummaryStats()
        self._format_func = format_merged_summary_text
        #: The blocks this row summarizes. Toggling the row expands or
        #: collapses only this segment, not the whole run.
        self._thoughts: list[ThinkingBlock] = list(thoughts or ())
        self._tools: list[ToolBlock] = list(tools or ())
        self.add_class("run-summary-block")

    def compose(self) -> ComposeResult:
        yield Label(self._format_func(self._stats), id="run-summary-label", markup=False)

    def update_stats(self, stats: Any) -> None:
        self._stats = stats
        with contextlib.suppress(Exception):
            self.query_one("#run-summary-label", Label).update(self._format_func(self._stats))

    def is_open(self) -> bool:
        return any(not t.has_class("-hidden") for t in self._thoughts) or any(
            not b.has_class("-run-collapsed") for b in self._tools
        )

    def set_open(self, open_: bool) -> None:
        for thought in self._thoughts:
            if open_:
                thought.remove_class("-hidden")
            else:
                thought.add_class("-hidden")
        for tool in self._tools:
            tool.set_run_collapsed(not open_)

    def on_click(self, event: events.Click) -> None:
        event.stop()
        self.post_message(self.Toggled(self))


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
    #: Product blocks opt in to being reused across calls instead of stacked.
    pinned: bool = False
    #: Product blocks hosting an interactive questionnaire set this so the
    #: chat log can find them without knowing a tool name.
    is_ask_user: bool = False

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
        # is the bound :class:`ctx.tools.base.BaseTool` instance the
        # block is rendering. Custom subclasses can use it to call
        # ``self.tool.format_call(params)`` or ``self.tool.format_preview``
        # instead of accepting pre-formatted strings from the runner.
        self.tool: BaseTool | None = tool
        self._ui_summary: str | None = None
        self._ui_details: str | None = None
        self._ui_details_full: str | None = None
        #: Structured payload for a block that lays out its own result. An
        #: attribute rather than another ``set_result`` keyword because an extension's
        #: block may override ``set_result`` with the documented signature, and
        #: a kwarg it has never heard of would fail on the first tool that sent
        #: one. ``None`` everywhere else.
        self.ui_data: dict | None = None
        self._images: list[ImageContent] | None = None
        self._result_markup: bool = True
        self._expanded: bool = expanded
        self._success: bool | None = None
        self._awaiting_approval: bool = False
        self._approval_preview: str | None = None
        self._approval_selection: ApprovalResponse = ApprovalResponse.APPROVE
        self._live_output: str = ""
        self._live_render_pending: bool = False
        self._partial_args: dict | None = None
        self._args_render_pending: bool = False
        self._started_at = time.monotonic()
        self._elapsed: float = 0.0
        self.add_class("tool-block")
        self._set_state(None)

    @property
    def name(self) -> str | None:
        return self._name

    def compose(self) -> ComposeResult:
        yield Label(self._format_header(), id="tool-header")
        yield Label("", id="tool-output", classes="tool-output -hidden")
        yield Label("", id="tool-footer", classes="tool-footer")
        yield AskUserInput(placeholder="Type something.", id="ask-user-input", classes="-hidden")

    def _box_w(self) -> int:
        """Shared box width for header/body/footer so corners always align."""
        return boxed.box_width(self.size.width or 80)

    def _source_path(self) -> str | None:
        """File the call touches, used for the language chip and highlighting.

        Generic blocks have no way to derive a path from an opaque call message;
        tool blocks that edit or read files override this.
        """
        return None

    def _format_call_detail(self, first_line: str) -> str | Text:
        """Style the first call line shown in the header.

        Overridden by tools with a dedicated call syntax (shell commands).
        """
        return first_line

    def _refresh_box(self) -> None:
        """Re-render every box surface at the current width (post-layout/resize).

        This is also the retry that makes an interactive body appear. The
        stream delivers a tool call and its follow-up event (the ``ask_user``
        questionnaire, a permission prompt) back-to-back, so the body can be
        attached to a block Textual has not mounted yet: ``query_one`` misses
        on every surface, the best-effort writes are dropped, and the block
        sits there looking like a running tool while the turn waits for an
        answer the user cannot give. ``on_mount`` lands here with the children
        in place, so repaint whatever interactive state was set meanwhile.
        """
        self._safe_update(
            lambda: self.query_one("#tool-header", Label).update(self._format_header())
        )
        self._render_result_output()
        self._render_interactive_output()
        if self._awaiting_approval:
            self._safe_update(self._render_approval_output)
        self._render_footer()

    def _safe_update(self, fn) -> None:
        """Run ``fn`` and swallow DOM errors (e.g. widget not yet mounted)."""
        with contextlib.suppress(Exception):
            fn()

    def _render_interactive_output(self) -> None:
        """Extension hook for tool UIs with an interactive body (e.g. dialogs).

        The base block has no interactive body, so this is a no-op. Product
        tool blocks override it to repaint state attached before mount.
        """

    def on_mount(self) -> None:
        self.call_after_refresh(self._refresh_box)

    def _format_header(self, truncate: bool = True) -> Text:
        """Boxed top border: ``╭─ {icon} {Name} {detail} ───╮``."""
        del truncate
        width = self._box_w()
        name = self._name or ""
        display = name[:1].upper() + name[1:] if name else ""
        call_plain = boxed.strip_markup(self._call_msg or "")
        detail: str | Text = ""
        if call_plain:
            first_line = call_plain.split("\n", 1)[0]
            detail = self._format_call_detail(first_line)
        running = self._success is None and not self._awaiting_approval
        status = ""
        if self._success is True:
            status = "✓"
        elif self._success is False:
            status = "✗"
        elif running:
            status = "◌"
        header = boxed.top_border(
            self._icon,
            display,
            detail,
            width,
            running=running,
            failed=self._success is False,
            status=status,
            badge=boxed.language_badge(self._source_path()),
        )
        if self._awaiting_approval:
            notice = Text(
                " △ Permission required ",
                style=Style(bgcolor=config.ui.colors.notice, color=config.ui.colors.bg, bold=True),
            )
            return Text.assemble(notice, "\n", header)
        return header

    def _render_footer(self) -> None:
        """Boxed bottom border: ``╰─ metrics ──── hint ─╯``."""
        try:
            footer = self.query_one("#tool-footer", Label)
        except Exception:
            return
        width = self._box_w()
        hint = HINT_TOOL_EXPAND if self._ui_details_full else ""
        # CodemodeToolBlock reuses _started_at as Optional[float] for its own
        # timing — fall back to now rather than crash the footer render.
        started = self._started_at
        if not isinstance(started, (int, float)):
            started = time.monotonic()
        if self._success is None:
            elapsed = time.monotonic() - started
            text = boxed.bottom_border(boxed.running_footer(elapsed), width, hint=hint)
        else:
            summary = self._ui_summary or ""
            elapsed_txt = boxed.elapsed_footer(self._elapsed, summary)
            text = boxed.bottom_border(
                elapsed_txt, width, hint=hint, failed=self._success is False
            )
        footer.update(text)

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

        rendered = self._render_markup_safe(content)
        if rendered.spans:
            return rendered
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
        # Best effort: the block may not be mounted yet, in which case
        # ``_refresh_box`` paints the prompt once its children exist.
        self._safe_update(
            lambda: self.query_one("#tool-header", Label).update(self._format_header())
        )
        self._safe_update(self._render_approval_output)

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
        self._safe_update(
            lambda: self.query_one("#tool-header", Label).update(self._format_header())
        )
        self._safe_update(self._hide_approval_output)

    def _hide_approval_output(self) -> None:
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

    # -- interactive tool UI extension hooks ----------------------------------

    @property
    def is_awaiting_ask_user(self) -> bool:
        """Whether an interactive questionnaire is open. Product overrides answer."""
        return False

    def show_ask_user(self, dialog: Any) -> None:
        """Open a questionnaire. No-op on the generic block."""

    def rerender_ask_user(self) -> None:
        """Repaint an open questionnaire. No-op on the generic block."""

    def hide_ask_user(self) -> None:
        """Close an open questionnaire. No-op on the generic block."""

    def ask_user_custom_value(self) -> str:
        """Current free-text value of an open questionnaire."""
        return ""

    def set_ask_user_custom_value(self, value: str) -> None:
        """Overwrite the free-text value of an open questionnaire."""

    def update_call_msg(self, call_msg: str) -> None:
        self._call_msg = call_msg
        self.query_one("#tool-header", Label).update(self._format_header())

    def update_call_args(self, partial_args: dict | None) -> None:
        """Show live partial tool-call arguments on the header.

        The provider's tolerant parse arrives on every argument delta. The
        partial dict is rendered through the tool's own ``format_call`` when
        it already validates, else as a compact JSON preview. Coalesced into
        one render per frame like the live output path, since argument deltas
        arrive far faster than the screen refreshes.
        """
        if partial_args is None:
            return
        self._partial_args = partial_args
        if self._args_render_pending:
            return
        self._args_render_pending = True
        self.call_after_refresh(self._flush_call_args_render)

    def _flush_call_args_render(self) -> None:
        self._args_render_pending = False
        args = self._partial_args
        if not args:
            return
        call_msg = self._streaming_file_preview(args)
        if not call_msg and self.tool is not None:
            try:
                call_msg = self.tool.format_call(self.tool.params(**args))
            except Exception:
                call_msg = ""
        if not call_msg:
            preview = json.dumps(args, ensure_ascii=False, default=str)
            call_msg = preview[:157] + "..." if len(preview) > 160 else preview
        self._call_msg = call_msg
        self._safe_update(
            lambda: self.query_one("#tool-header", Label).update(self._format_header())
        )
        if self._rerender_body_on_args():
            self._render_result_output()
        self._render_footer()

    def _streaming_file_preview(self, args: dict) -> str:
        """Live call-header preview while arguments stream.

        Generic blocks have no specialised call syntax; product tool blocks
        override this to show a diff, tree, or other live preview.
        """
        return ""

    def _rerender_body_on_args(self) -> bool:
        """Whether a streaming argument delta should repaint the body."""
        return False

    def append_live_output(self, delta: str) -> None:
        """Stream incremental stdout/stderr chunk into the tool block in real-time."""
        if not delta:
            return
        self._live_output += delta
        if len(self._live_output) > 20000:
            lines = self._live_output.split("\n")
            if len(lines) > 100:
                self._live_output = "\n".join(lines[-100:])
        self._request_live_render()

    def _request_live_render(self) -> None:
        """Coalesce the live re-render into the next frame.

        A chatty tool emits output deltas far faster than the screen refreshes
        (the turn drains the tool's output queue with no throttle), and each
        render is a full split of the accumulated buffer plus a Rich build and
        a widget update. Doing that per delta saturates the event loop, and
        because key events, the spinner and Esc are all served by that same
        loop, the whole app stops responding. One render per frame is
        indistinguishable to the eye.
        """
        if self._live_render_pending:
            return
        self._live_render_pending = True
        self.call_after_refresh(self._flush_live_render)

    def _flush_live_render(self) -> None:
        self._live_render_pending = False
        self._render_live_output()

    def _render_live_output(self) -> None:
        try:
            output = self.query_one("#tool-output", Label)
        except Exception:
            return
        if not self._live_output:
            return

        lines = self._live_output.rstrip().split("\n")
        max_live_lines = 16
        if len(lines) > max_live_lines:
            visible_lines = lines[-max_live_lines:]
            prefix = f"  ... ({len(lines) - max_live_lines} earlier lines)\n"
        else:
            visible_lines = lines
            prefix = ""

        body = Text("\n".join(visible_lines), style=config.ui.colors.dim)
        if prefix:
            body = Text.assemble(Text(prefix, style=config.ui.colors.dim), body)

        rendered = boxed.wrap_boxed(body, self._box_w())

        self.remove_class("-compact")
        self.add_class("-with-details")
        output.remove_class("-hidden")
        output.remove_class("-details")
        output.remove_class("-diff-output")
        output.update(rendered)
        self._render_footer()

    def _streaming_body_text(self) -> Text | None:
        """Live body while arguments stream. Product tool blocks override."""
        return None

    def set_result(
        self,
        ui_summary: str | None,
        ui_details: str | None,
        success: bool,
        markup: bool = True,
        ui_details_full: str | None = None,
        images: list[ImageContent] | None = None,
    ) -> None:
        self._live_output = ""
        self._ui_summary = ui_summary
        self._ui_details = ui_details
        self._ui_details_full = ui_details_full
        self._images = images
        self._result_markup = markup
        self._success = success
        self._awaiting_approval = False
        # Freeze the elapsed time at completion so later re-renders (scroll,
        # resize, refocus) don't keep growing it from the mount time.
        # ``CodemodeToolBlock`` keeps ``_started_at`` as ``None`` until the
        # script actually starts, so guard the subtraction.
        started = self._started_at
        self._elapsed = time.monotonic() - started if isinstance(started, (int, float)) else 0.0
        self._set_state(success)
        self._render_result_output()
        self._safe_update(
            lambda: self.query_one("#tool-header", Label).update(self._format_header())
        )

    # -- Task tool live progress ------------------------------------------

    def set_task_progress(self, stats: dict) -> None:
        """Consume a progress snapshot. Product tool blocks override."""

    def apply_task_progress(self, event: dict) -> None:
        """Consume a raw progress event. Product tool blocks override."""

    def set_expanded(self, expanded: bool) -> None:
        if self._expanded == expanded:
            return
        self._expanded = expanded
        self._render_result_output()

    def set_run_collapsed(self, collapsed: bool) -> None:
        """Collapse or reveal the entire tool block in merged run summary mode."""
        if collapsed:
            self.add_class("-run-collapsed")
        else:
            self.remove_class("-run-collapsed")

    def on_resize(self, event: events.Resize) -> None:
        del event
        self._refresh_box()

    def _render_custom_output(self) -> Text | None:
        """Render a tool-specific ``ui_data`` shape. Generic blocks return None."""
        return None

    def _render_result_output(self) -> None:
        try:
            output = self.query_one("#tool-output", Label)
        except Exception:
            return
        ui_details = (
            self._ui_details_full if self._expanded and self._ui_details_full else self._ui_details
        )

        streaming = self._streaming_body_text()
        if streaming is not None:
            rendered = streaming
            is_diff_output = False
        elif ui_details:
            if self._result_markup and "\x1b[" in ui_details:
                # Command output carrying ANSI SGR codes (bash): paint it like a
                # real terminal instead of treating the codes as markup.
                try:
                    rendered = Text.from_ansi(ui_details)
                except Exception:
                    rendered = self._render_markup_safe(ui_details)
            else:
                rendered = (
                    self._render_markup_safe(ui_details)
                    if self._result_markup
                    else Text(ui_details)
                )
            is_diff_output = DIFF_BG_PAD_MARKER in rendered.plain
            if is_diff_output:
                rendered = boxed.highlight_diff(rendered, self._source_path())
            rendered = self._pad_diff_backgrounds(rendered, max(20, self._box_w() - 4))
        elif (custom := self._render_custom_output()) is not None:
            rendered = custom
            is_diff_output = False
        elif self._images:
            image_count = len(self._images)
            image_label = "image" if image_count == 1 else "images"
            rendered = Text(f"Attached {image_count} {image_label}", style=config.ui.colors.dim)
            is_diff_output = False
        else:
            output.update(Text(""))
            self.remove_class("-with-details")
            output.remove_class("-details")
            output.remove_class("-diff-output")
            output.add_class("-hidden")
            self._render_footer()
            return

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
        output.update(boxed.wrap_boxed(rendered, self._box_w()))
        self._render_footer()


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
            block: HandoffLinkBlock,
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


# ---------------------------------------------------------------------------
# Compaction
# ---------------------------------------------------------------------------

COMPACTION_TICK_MS = 100

_TRIGGER_LABELS = {"overflow": "auto-compaction", "manual": "requested"}


def _short_section_title(title: str) -> str:
    """Condense a mandated summary heading to a checklist-sized label."""
    head = re.split(r"\s*[&,(-]", title, maxsplit=1)[0].strip()
    if head.lower().startswith("all "):
        head = head[4:]
    return head[:14]


def _format_elapsed(seconds: float) -> str:
    if seconds < 10:
        return f"{seconds:.1f}s"
    if seconds < 60:
        return f"{int(seconds)}s"
    return f"{int(seconds // 60)}m{int(seconds % 60):02d}s"


class CompactionBlock(Static):
    """Context-compaction UI.

    While the handoff summary is generated this shows an indeterminate bar and
    a live checklist of the sections the summarization prompt mandates, so a
    60-second wait shows work instead of a spinner. Afterwards it shows the
    token delta and, on expand, the summary itself — the one artifact
    compaction produces and the user otherwise never sees.
    """

    ALLOW_SELECT = True
    can_focus = False

    def __init__(
        self,
        *,
        tokens_before: int = 0,
        context_window: int = 0,
        trigger: str = "overflow",
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._tokens_before = tokens_before
        self._context_window = context_window
        self._trigger = trigger
        self._tokens_after: int | None = None
        self._summary: str = ""
        self._error: str = ""
        self._finished = False
        self._expanded = False
        self._chars = 0
        self._sections: list[tuple[int, str]] = []
        self._started_at = time.monotonic()
        self._elapsed = 0.0
        self._pulse_frame = 0
        self._pulse_timer: Timer | None = None
        self._spinner = Spinner("dots")
        self.add_class("compaction-block", "-running")
        self._refresh()

    # -- Compose ---------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Label(self._format_header(), id="compaction-header")
        yield ProgressBar(total=None, show_percentage=False, show_eta=False, id="compaction-bar")
        yield Label("", id="compaction-sections")
        yield Label("", id="compaction-summary", classes="-hidden")

    # -- Pulse -----------------------------------------------------------

    def on_mount(self, event: events.Mount) -> None:
        del event
        # Children exist now, so paint the initial state the timer will keep up to.
        self._refresh()
        self._pulse_timer = self.set_interval(COMPACTION_TICK_MS / 1000.0, self._tick_pulse)

    def on_unmount(self) -> None:
        self._stop_pulse()

    def _stop_pulse(self) -> None:
        timer, self._pulse_timer = self._pulse_timer, None
        if timer is not None:
            with contextlib.suppress(Exception):
                timer.stop()

    def _tick_pulse(self) -> None:
        if self._finished:
            self._stop_pulse()
            return
        self._pulse_frame += 1
        self._refresh()

    def _current_elapsed(self) -> float:
        if self._finished:
            return self._elapsed
        return time.monotonic() - self._started_at

    # -- State transitions -----------------------------------------------

    def update_progress(self, chars: int, sections: list[tuple[int, str]]) -> None:
        """Record streaming progress from the summary generation."""
        if self._finished:
            return
        self._chars = chars
        # The contract is cumulative, but never let a partial update clear a
        # checklist the user has already watched fill in.
        self._sections = sections or self._sections
        self._refresh()

    def finish(
        self,
        *,
        tokens_before: int | None = None,
        tokens_after: int,
        summary: str = "",
        error: str = "",
    ) -> None:
        """Settle the block: success shows the token delta, failure the reason."""
        if self._finished:
            return
        self._finished = True
        self._elapsed = time.monotonic() - self._started_at
        self._error = error
        if tokens_before is not None:
            self._tokens_before = tokens_before
        if error:
            self.remove_class("-running")
            self.add_class("-error")
        else:
            self._tokens_after = tokens_after
            self._summary = summary
            self.remove_class("-running")
            self.add_class("-done")
        self._stop_pulse()
        self._refresh()

    def toggle_expanded(self) -> bool:
        """Toggle summary visibility. No-op until compaction has finished."""
        if not self._finished or self._error or not self._summary:
            return False
        self._expanded = not self._expanded
        self._render_summary()
        self._refresh()
        return self._expanded

    def on_click(self, event: events.Click) -> None:
        event.stop()
        self.toggle_expanded()

    # -- Rendering -------------------------------------------------------

    def _refresh(self) -> None:
        with contextlib.suppress(Exception):
            self.query_one("#compaction-header", Label).update(self._format_header(), layout=False)
            self._render_sections()
        if self._finished:
            with contextlib.suppress(Exception):
                self._render_bar()

    def _render_bar(self) -> None:
        bar = self.query_one("#compaction-bar", ProgressBar)
        if self._finished:
            before = self._tokens_before or 1
            bar.update(total=before, progress=self._tokens_after or 0)
        else:
            bar.update(total=None, progress=0)

    def _render_sections(self) -> None:
        label = self.query_one("#compaction-sections", Label)
        label.set_class(self._finished or bool(self._error), "-hidden")
        if self._finished:
            label.update("")
            return
        label.update(self._format_sections())

    def _render_summary(self) -> None:
        with contextlib.suppress(Exception):
            self._render_summary_unsafe()

    def _render_summary_unsafe(self) -> None:
        label = self.query_one("#compaction-summary", Label)
        if not self._expanded or not self._summary:
            label.add_class("-hidden")
            return
        label.remove_class("-hidden")
        label.update(format_markdown_block(self._summary, markdown_render_width()))

    def _format_header(self) -> Content:
        colors = config.ui.colors
        parts: list[Content | tuple[str, str]] = [Content.assemble(("⇊ ", colors.spinner))]

        if self._error:
            parts.append(Content.assemble(("Compaction failed", f"{colors.failed} bold")))
            parts.append(Content.assemble((f": {self._error}", colors.dim)))
            return Content.assemble(*parts)

        if not self._finished:
            parts.append(Content.assemble(("Compacting", f"{colors.running} bold")))
            parts.append(
                Content.assemble((f"  {_format_elapsed(self._current_elapsed())}", colors.dim))
            )
            if self._context_window:
                pct = int(self._tokens_before * 100 / self._context_window)
                parts.append(
                    Content.assemble(
                        (
                            f"  {format_tokens(self._tokens_before)}"
                            f"/{format_tokens(self._context_window)} ({pct}%)",
                            colors.notice if pct >= 80 else colors.muted,
                        )
                    )
                )
            parts.append(
                Content.assemble(
                    (f"  {_TRIGGER_LABELS.get(self._trigger, self._trigger)}", colors.dim)
                )
            )
            return Content.assemble(*parts)

        before = self._tokens_before
        after = self._tokens_after or 0
        parts.append(Content.assemble(("Compacted", f"{colors.accent} bold")))
        parts.append(
            Content.assemble((f"  {format_tokens(before)} → {format_tokens(after)}", colors.fg))
        )
        if before > 0:
            saved = 100 - int(after * 100 / before)
            parts.append(Content.assemble((f"  (−{saved}%)", colors.success)))  # noqa: RUF001
        parts.append(Content.assemble((f"  {_format_elapsed(self._elapsed)}", colors.dim)))
        if self._summary:
            parts.append(
                Content.assemble(
                    (
                        "  ctrl+o to collapse" if self._expanded else "  ⏎ view summary",
                        colors.muted,
                    )
                )
            )
        return Content.assemble(*parts)

    def _format_sections(self) -> Content:
        colors = config.ui.colors
        if not self._sections:
            return Content.assemble(("drafting handoff…", colors.dim))
        done = {num for num, _ in self._sections}
        active_num = max(done) + 1
        active = self._spinner.render(time.time()) if self._spinner else ""
        width = max(24, self.size.width or 80)
        plain_width = 0
        parts: list[Content | tuple[str, str]] = []
        for num, title in SUMMARY_SECTIONS:
            label = _short_section_title(title)
            if num in done:
                text, style = f"✓ {label}", colors.success
            elif num == active_num:
                text, style = f"{active} {label}", colors.running
            else:
                text, style = label, colors.dim
            cost = len(text) + (3 if parts else 0)
            if plain_width + cost > width - 1:
                parts.append(("…", colors.dim))
                break
            if parts:
                parts.append((" · ", colors.dim))
            parts.append(Content.assemble((text, style)))
            plain_width += cost
        return Content.assemble(*parts)


# GitHub-style contribution cell glyphs, lightest first. Shaded blocks read as
# a heat ramp in any terminal that has truecolor; the theme blend below
# provides the actual color.
_HEATMAP_GLYPHS = ("░", "▒", "▓", "█")

#: Blends of background toward accent, one per heat level (plus level 0 = dim).
_HEATMAP_WEIGHTS = (0.0, 0.25, 0.45, 0.7, 1.0)

#: Weekday initials, index 0 = Sunday to match the column alignment below.
_DAY_INITIALS = ("s", "m", "t", "w", "t", "f", "s")

#: Rows carrying a weekday label -- Mon/Wed/Fri, as GitHub does.
_HEATMAP_LABELLED_ROWS = (1, 3, 5)


def _heat_color(level: int) -> str:
    colors = config.ui.colors
    if level <= 0:
        return colors.border
    from ctx.term.styles import _blend_hex

    return _blend_hex(colors.bg, colors.accent, overlay_weight=_HEATMAP_WEIGHTS[level])


def _heat_level(value: int, thresholds: list[int]) -> int:
    """Bucket a value against quartile-ish thresholds derived from the data.

    Thresholds come from the window's own distribution rather than fixed
    round numbers, so a history of 200M-token days and one of 2M-token days
    both produce a readable gradient instead of one flat colour or one
    saturated block.
    """
    level = 0
    for threshold in thresholds:
        if value >= threshold:
            level += 1
    return min(level, len(_HEATMAP_GLYPHS) - 1)


def _thresholds(values: list[int]) -> list[int]:
    """Three ascending cut points at the 50th/75th/90th percentile of non-zero days."""
    positives = sorted(v for v in values if v > 0)
    if not positives:
        return []

    def pick(fraction: float) -> int:
        return positives[min(len(positives) - 1, int(len(positives) * fraction))]

    # Equal cut points would collapse levels; nudge duplicates so the ramp
    # stays monotonic.
    cuts = [pick(0.5), pick(0.75), pick(0.9)]
    for i in range(1, len(cuts)):
        if cuts[i] <= cuts[i - 1]:
            cuts[i] = cuts[i - 1] + 1
    return cuts


def _total_line(usage) -> str:
    """Compact total plus the exact count.

    The compact form alone is a trap at scale: at 2.7B one decimal place
    swallows output entirely, so ``2.7B`` appears as both the input figure and
    the total and the breakdown looks like it does not add up. The exact
    number is what makes the arithmetic checkable; the short one is what fits.
    """
    from ctx.term.formatting import format_tokens

    return f" {format_tokens(usage.total_tokens)} · {usage.total_tokens:,}"


class UsageBlock(Static):
    """``/usage``: session, rolling-month and lifetime totals with a yearly grid.

    The grid is a contribution heatmap -- one cell per day, columns are weeks,
    rows are weekdays -- so activity patterns are visible at a glance.
    """

    ALLOW_SELECT = True
    can_focus = False

    def __init__(self, report, session_totals, days: int, **kwargs) -> None:
        super().__init__(**kwargs)
        self._report = report
        self._session = session_totals
        self._days = days
        self.add_class("usage-block")

    def compose(self) -> ComposeResult:
        yield Label(self._build())

    def on_resize(self) -> None:
        with contextlib.suppress(Exception):
            self.query_one(Label).update(self._build())

    def _available_width(self) -> int:
        sources = [
            getattr(self.size, "width", 0),
            getattr(self.content_size, "width", 0),
            getattr(self.container_size, "width", 0),
        ]
        with contextlib.suppress(Exception):
            app = self.app
            if app and hasattr(app, "size") and app.size.width:
                sources.append(app.size.width)

        for source in sources:
            if source and source > 0:
                return source
        return 120

    # -- pieces ----------------------------------------------------------------

    def _summary_cards(self, available_width: int = 120) -> Text:
        from ctx.term.formatting import format_tokens

        colors = config.ui.colors
        summaries = (
            ("Session", self._session),
            ("Last 30 days", self._report.last_30_days),
            ("Lifetime", self._report.lifetime),
        )

        card_gap = 2
        min_card_w = 28
        max_card_w = 34

        horizontal = available_width >= (min_card_w * 3 + card_gap * 2)
        text = Text()

        if horizontal:
            card_w = min(max_card_w, max(min_card_w, (available_width - card_gap * 2) // 3))
            inner_w = card_w - 2

            # Row 0: Top border with card title embedded
            for idx, (title, _) in enumerate(summaries):
                if idx:
                    text.append(" " * card_gap)
                title_part = f"─ {title} "
                fill = "─" * max(0, inner_w - len(title_part))
                text.append("╭", style=colors.border)
                text.append(title_part, style=f"{colors.fg} bold")
                text.append(fill, style=colors.border)
                text.append("╮", style=colors.border)
            text.append("\n")

            # Row 1: Total tokens
            for idx, (_, usage) in enumerate(summaries):
                if idx:
                    text.append(" " * card_gap)
                val = _total_line(usage)
                fill = " " * max(0, inner_w - len(val))
                text.append("│", style=colors.border)
                text.append(val, style=f"{colors.fg} bold")
                text.append(fill)
                text.append("│", style=colors.border)
            text.append("\n")

            # Row 2: Turns
            for idx, (_, usage) in enumerate(summaries):
                if idx:
                    text.append(" " * card_gap)
                turns_suffix = "turn" if usage.turns == 1 else "turns"
                t_str = f" {usage.turns:,} {turns_suffix}"
                fill = " " * max(0, inner_w - len(t_str))
                text.append("│", style=colors.border)
                text.append(t_str, style=colors.dim)
                text.append(fill)
                text.append("│", style=colors.border)
            text.append("\n")

            # Row 3: Breakdown
            for idx, (_, usage) in enumerate(summaries):
                if idx:
                    text.append(" " * card_gap)
                detail = (
                    f" ↑ {format_tokens(usage.input_tokens)}"
                    f"  ↓ {format_tokens(usage.output_tokens)}"
                    f"  ◈ {format_tokens(usage.cache_read_tokens)}"
                )
                fill = " " * max(0, inner_w - len(detail))
                text.append("│", style=colors.border)
                text.append(detail, style=colors.dim)
                text.append(fill)
                text.append("│", style=colors.border)
            text.append("\n")

            # Row 4: Bottom border
            for idx, _ in enumerate(summaries):
                if idx:
                    text.append(" " * card_gap)
                b_fill = "─" * inner_w
                text.append(f"╰{b_fill}╯", style=colors.border)
        else:
            card_w = min(max_card_w, max(min_card_w, available_width - 2))
            inner_w = card_w - 2
            for idx, (title, usage) in enumerate(summaries):
                if idx:
                    text.append("\n")
                title_part = f"─ {title} "
                fill = "─" * max(0, inner_w - len(title_part))
                text.append("╭", style=colors.border)
                text.append(title_part, style=f"{colors.fg} bold")
                text.append(fill, style=colors.border)
                text.append("╮\n", style=colors.border)

                val = _total_line(usage)
                fill = " " * max(0, inner_w - len(val))
                text.append("│", style=colors.border)
                text.append(val, style=f"{colors.fg} bold")
                text.append(fill)
                text.append("│\n", style=colors.border)

                turns_suffix = "turn" if usage.turns == 1 else "turns"
                t_str = f" {usage.turns:,} {turns_suffix}"
                fill = " " * max(0, inner_w - len(t_str))
                text.append("│", style=colors.border)
                text.append(t_str, style=colors.dim)
                text.append(fill)
                text.append("│\n", style=colors.border)

                detail = (
                    f" ↑ {format_tokens(usage.input_tokens)}"
                    f"  ↓ {format_tokens(usage.output_tokens)}"
                    f"  ◈ {format_tokens(usage.cache_read_tokens)}"
                )
                fill = " " * max(0, inner_w - len(detail))
                text.append("│", style=colors.border)
                text.append(detail, style=colors.dim)
                text.append(fill)
                text.append("│\n", style=colors.border)

                b_fill = "─" * inner_w
                text.append(f"╰{b_fill}╯", style=colors.border)

        return text

    def _columns(self, available_width: int) -> tuple[list, object]:
        from datetime import date, timedelta

        end = self._report.as_of or date.today()
        start = end - timedelta(days=self._days - 1)
        start -= timedelta(days=(start.weekday() + 1) % 7)
        window_start = end - timedelta(days=self._days - 1)

        columns = [start + timedelta(days=7 * w) for w in range((end - start).days // 7 + 1)]

        gutter_width = 4
        col_width = 2
        max_fit_cols = max(6, (available_width - gutter_width - 2) // col_width)
        if len(columns) > max_fit_cols:
            columns = columns[-max_fit_cols:]
            window_start = max(window_start, columns[0])

        return columns, window_start

    def _heatmap(self, available_width: int = 120) -> Text:
        """Weeks as columns, weekdays as rows -- GitHub's contribution shape.

        Loops weekdays outermost so each weekday label lands once in the left
        gutter. Iterating the other way (the obvious way) drops a label in the
        middle of every column.
        """
        from datetime import date, timedelta

        colors = config.ui.colors
        report = self._report
        end = report.as_of or date.today()

        columns, window_start = self._columns(available_width)

        cuts = _thresholds([d.total_tokens for d in report.days.values()])
        cells = {day: _heat_level(d.total_tokens, cuts) for day, d in report.days.items()}

        gutter_width = 4
        col_width = 2
        total_cols = len(columns)
        base_width = gutter_width + total_cols * col_width

        # Build month header line with labels placed directly over start weeks
        header_chars = [" "] * base_width
        last_month = ""
        next_allowed_pos = gutter_width
        for w, week_start in enumerate(columns):
            visible = [
                week_start + timedelta(days=i)
                for i in range(7)
                if window_start <= week_start + timedelta(days=i) <= end
            ]
            month = visible[0].strftime("%b") if visible else ""
            if month and month != last_month:
                pos = gutter_width + w * col_width
                if pos >= next_allowed_pos:
                    while len(header_chars) < pos + len(month):
                        header_chars.append(" ")
                    for ci, ch in enumerate(month):
                        header_chars[pos + ci] = ch
                    next_allowed_pos = pos + len(month) + 1
                    last_month = month

        header_str = "".join(header_chars)
        total_width = max(len(header_str), base_width)
        if len(header_str) < total_width:
            header_str += " " * (total_width - len(header_str))

        text = Text()
        text.append(header_str + "\n", style=colors.dim)

        for weekday in range(7):
            row_text = Text()
            if weekday in _HEATMAP_LABELLED_ROWS:
                row_text.append(f"  {_DAY_INITIALS[weekday]} ", style=colors.dim)
            else:
                row_text.append("    ", style=colors.dim)

            for week_start in columns:
                cell_day = week_start + timedelta(days=weekday)
                if cell_day < window_start or cell_day > end:
                    row_text.append("  ")
                elif cell_day in cells:
                    level = cells[cell_day]
                    shade = _heat_color(level)
                    row_text.append(f"{_HEATMAP_GLYPHS[level]} ", style=shade)
                else:
                    shade = _heat_color(0)
                    row_text.append(f"{_HEATMAP_GLYPHS[0]} ", style=shade)

            pad = total_width - len(row_text.plain)
            if pad > 0:
                row_text.append(" " * pad)
            text.append_text(row_text)
            text.append("\n")

        text.append("    less ", style=colors.dim)
        for level in range(len(_HEATMAP_GLYPHS)):
            shade = _heat_color(level)
            text.append(f"{_HEATMAP_GLYPHS[level]} ", style=shade)
        text.append(" more", style=colors.dim)
        return text

    def _build(self) -> Text:
        from datetime import date

        colors = config.ui.colors
        report = self._report
        available_width = self._available_width()
        columns, _ = self._columns(available_width)

        if self._days <= 31:
            subtitle = "Token activity · last 30 days"
        elif len(columns) >= 50:
            subtitle = "Token activity · last 12 months"
        else:
            end = report.as_of or date.today()
            months = max(1, round((end - columns[0]).days / 30.4))
            subtitle = f"Token activity · last {months} months"

        text = Text()
        text.append("Token usage\n\n", style=f"{colors.fg} bold")
        text.append_text(self._summary_cards(available_width))
        text.append("\n\n")
        text.append(f"{subtitle}\n", style=f"{colors.fg} bold")
        text.append_text(self._heatmap(available_width))
        return text
