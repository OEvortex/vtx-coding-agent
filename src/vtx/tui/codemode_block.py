"""The ``codemode`` tool block: the script, the calls it made, its output.

Codemode is the one tool whose *input* is the work. A one-line header summary is
right for ``bash`` -- ``$ git status`` says what ran -- but the interesting part
of a script is the script, and until now that was truncated to its first line
and its result flattened into "Script ok (3 tool calls)", so the user watched a
turn produce output they could never read. This block shows the run properly::

    ⟩ codemode  3 tool calls · 1.2s
      rows = await tools.search(query="find x")
      return [r["name"] for r in rows]

      ✓ search 0.4s
      ✓ read 1.2s

      ⎿ ["read", "grep", "edit"]

The script is the call, so it leads. The nested tool calls follow as a
checklist -- what ran, whether it worked, what it cost -- and the script's own
output closes the block. Every section is clipped until ``ctrl+o`` expands it;
the caps exist so a forty-call script does not bury the turn that ran it. The
output is compact by default: the model reads the full text result in context,
the user reads a preview and expands when it matters.

Anything this block does not own falls through to :class:`ToolBlock`: the
permission preview, the ask_user dialog, images, and the expansion state
machine are shared machinery and stay shared.
"""

from __future__ import annotations

import contextlib

from rich.style import Style
from rich.text import Text
from textual import events
from textual.widgets import Label

from vtx.core.config import config
from vtx.tui.blocks import ToolBlock
from vtx.tui.formatting import format_python_source
from vtx.tui.task_ui import GLYPHS, format_ms
from vtx.tui.tool_output import format_expand_hint

#: Indent for every line of the body, so the block aligns with how tool details
#: render elsewhere in the scrollback.
BODY_INDENT = "  "

#: Source lines visible in a collapsed run. Generous on purpose: the script is
#: the payload, so the cap exists only so a 200-line script does not bury the
#: turn that ran it.
CODE_LINES = 12
#: Lines of script output, same reasoning. Compact by default; ``ctrl+o`` is the
#: explicit "show me everything".
OUTPUT_LINES = 5
#: Nested calls listed when collapsed. The most recent ones are the interesting
#: ones -- a script that fails usually fails near its tail.
CALL_PREVIEW_COUNT = 8

#: Expanded multiples. ``ctrl+o`` is an explicit "show me everything".
WIDE_CODE_LINES = 80
WIDE_OUTPUT_LINES = 40

#: Traceback lines under a failed run, collapsed and expanded.
STACK_LINES = 5
WIDE_STACK_LINES = 40

#: Width of the rule drawn above a traceback, bounded so a very wide terminal
#: does not get a 200-column divider.
ERROR_RULE_WIDTH = 48


class CodemodeToolBlock(ToolBlock):
    """One ``codemode`` call: the script, its nested calls, and its output."""

    # -- header ------------------------------------------------------------

    def _format_header(self, truncate: bool = True) -> Text:
        # The permission box and the questionnaire are the base class's; both
        # need the tool name and the call text they were built around.
        if self._awaiting_approval or self._ask_dialog is not None:
            return super()._format_header(truncate=truncate)

        colors = config.ui.colors
        if self._success is None:
            icon_style = colors.running
            name_style: str | Style = Style(color=colors.running, bold=True)
        elif self._success is False:
            icon_style = colors.failed
            name_style = Style(color=colors.failed, bold=True)
        elif config.ui.colored_tool_badge:
            icon_style = colors.accent
            name_style = Style(color=colors.accent, bold=True)
        else:
            icon_style = colors.muted
            name_style = Style(color=colors.muted, bold=True)

        result = Text()
        result.append(f"{self._icon} ", style=Style(color=icon_style, bold=True))
        result.append(self._name or "codemode", style=name_style)
        result.append_text(self._header_status())
        return result

    def _header_status(self) -> Text:
        """What the run ended up doing, on the header line.

        Call count and duration are the two facts that decide how the user
        reads the body below: three tool calls and two seconds is a different
        story from three tool calls and ninety seconds. A failure reuses the
        tool's own one-line summary, which is the one that says what already
        ran and was not undone.
        """
        colors = config.ui.colors

        if self._success is None:
            return Text(" running...", style=Style(color=colors.dim))
        if self._success is False:
            summary = self._ui_summary or (self.ui_data or {}).get("error", {}).get("kind")
            return Text(f" {summary or 'failed'}", style=Style(color=colors.failed, bold=True))

        data = self.ui_data or {}
        calls = self._calls(data)
        bits: list[str] = []
        if calls:
            bits.append(f"{len(calls)} tool call" + ("" if len(calls) == 1 else "s"))
        duration = data.get("duration_ms")
        if isinstance(duration, int | float) and duration:
            bits.append(format_ms(duration))
        return Text(" " + " · ".join(bits or ["ok"]), style=Style(color=colors.dim))

    # -- result ------------------------------------------------------------

    def update_call_msg(self, call_msg: str) -> None:
        super().update_call_msg(call_msg)
        # ToolEndEvent lands before execution, which is the moment the script
        # becomes known and the block can draw it while it is still running.
        # Rendering here rather than only in set_result is the whole
        # difference between a script that appears and one that pops in at the
        # end.
        self._render_result_output()

    def on_resize(self, event: events.Resize) -> None:
        del event
        # The base class only repaints a result it holds as detail strings. A
        # codemode block draws its own body, so without this it keeps the line
        # wrap from whatever width it was last rendered at.
        if self.ui_data or self._call_msg:
            self._render_result_output()

    def _render_result_output(self) -> None:
        try:
            output = self.query_one("#tool-output", Label)
        except Exception:
            return

        cell = self._render_cell()
        if cell is None:
            output.update(Text(""))
            self.remove_class("-with-details")
            output.remove_class("-details")
            output.remove_class("-diff-output")
            output.add_class("-hidden")
            return

        self.remove_class("-compact")
        self.add_class("-with-details")
        output.remove_class("-hidden")
        output.remove_class("-details")
        output.remove_class("-diff-output")
        output.update(cell)

    def _render_cell(self) -> Text | None:
        data = self.ui_data or {}
        code = str(data.get("code") or self._call_msg or "")
        output_text = self._output_text(data)
        error = data.get("error")
        if not code.strip() and not output_text and not error:
            return None

        colors = config.ui.colors
        body_width = max(24, self._cell_width() - len(BODY_INDENT))

        text = Text()

        # The script itself: highlighted, indented, clipped.
        limit = WIDE_CODE_LINES if self._expanded else CODE_LINES
        shown, hidden = _clip_lines(code.lstrip("\n"), limit)
        source = format_python_source(shown, body_width)
        if source.plain:
            text.append_text(_prefixed(source, BODY_INDENT, BODY_INDENT))
            if hidden:
                text.append("\n")
                text.append(BODY_INDENT)
                text.append_text(format_expand_hint(hidden))

        # The nested tool calls, one per line, as a checklist.
        calls = self._calls(data)
        if calls:
            text.append_text(self._render_trace(calls))

        # The script's own output: printed lines plus the returned value.
        if output_text:
            text.append_text(self._render_output(output_text, body_width))

        if isinstance(error, dict) and error:
            text.append_text(self._render_error(error, body_width))

        if data.get("output_truncated"):
            text.append("\n\n")
            text.append(
                f"{BODY_INDENT}{GLYPHS['sub_line']} [Output truncated to fit the model's context]",
                style=Style(color=colors.notice),
            )

        images = data.get("images")
        if isinstance(images, int) and images > 0:
            label = "image" if images == 1 else "images"
            text.append("\n\n")
            text.append(
                f"{BODY_INDENT}{GLYPHS['sub_line']} {images} {label} attached to the result",
                style=Style(color=colors.dim),
            )

        return text

    def _output_text(self, data: dict) -> str:
        """Printed lines plus the returned value, as one compact section.

        They are one section rather than two because the model reads both in
        its result text as a single body, and the user asking "what did this
        script produce?" asks about the whole of it.
        """
        parts = [str(item) for item in (data.get("printed") or []) if str(item).strip()]
        value = data.get("value")
        if value:
            parts.append(str(value))
        return "\n".join(part.rstrip("\n") for part in parts if part.strip())

    def _render_trace(self, calls: list[dict]) -> Text:
        """The nested tool calls: one line each, most recent last.

        A failure reuses the call's own failure message and every line carries
        its duration, which is what tells a model which call to avoid next
        time. Collapsed shows the tail of a long run -- the calls that decided
        the outcome -- with the earlier ones counted, because forty of them in
        a scrollback are not.
        """
        colors = config.ui.colors
        text = Text()
        text.append("\n\n")

        shown = calls if self._expanded else calls[-CALL_PREVIEW_COUNT:]
        if len(shown) < len(calls):
            earlier = len(calls) - len(shown)
            text.append(f"{BODY_INDENT}... ({earlier} earlier calls, ")
            text.append("ctrl+o", style=colors.muted)
            text.append(" to expand)", style=colors.dim)

        for index, call in enumerate(shown):
            # Every row sits on its own line; the blank-line separation from the
            # code above was paid for up front.
            if index:
                text.append("\n")
            text.append(BODY_INDENT)
            ok = bool(call.get("ok", True))
            text.append(
                GLYPHS["success"] if ok else GLYPHS["failure"],
                style=Style(color=colors.success if ok else colors.failed),
            )
            text.append(" ")
            text.append(str(call.get("name") or "?"))
            if not ok and call.get("message"):
                text.append(f" - {call['message']}", style=Style(color=colors.failed))
            if not ok and call.get("kind"):
                text.append(f" ({call['kind']})", style=Style(color=colors.dim))
            duration = call.get("duration_ms")
            if isinstance(duration, int | float):
                text.append(f"  {format_ms(duration)}", style=Style(color=colors.dim))
        return text

    def _render_output(self, output_text: str, body_width: int) -> Text:
        """The script's output under the sub-line marker, dimmed.

        Wrapped long lines stay on one logical line of source: a script that
        prints one long JSON line is the common case, and clipping on logical
        lines alone would show a fragment that reads as corruption. Short
        pretty-printed values are folded onto one line first, because that is
        what the script meant to say.
        """
        colors = config.ui.colors
        text = Text()
        text.append("\n\n")
        text.append(f"{BODY_INDENT}{GLYPHS['sub_line']} ", style=Style(color=colors.dim))

        marker = f"{BODY_INDENT}{GLYPHS['sub_line']} "
        indent = " " * len(marker)
        shown, hidden = _clip_lines(
            _squash_value(output_text, body_width - len(indent)),
            WIDE_OUTPUT_LINES if self._expanded else OUTPUT_LINES,
        )
        text.append_text(_prefixed(Text(shown, style=Style(color=colors.muted)), "", indent))
        if hidden:
            text.append("\n")
            text.append(indent)
            text.append_text(format_expand_hint(hidden))
        return text

    def _render_error(self, error: dict, body_width: int) -> Text:
        """The failure half of the run: a rule, the diagnostic, the traceback.

        The traceback is the part the model already reads, and the user is the
        one who decides whether to trust the next script, so it is shown too --
        clipped until expanded, never dropped.
        """
        colors = config.ui.colors
        kind = str(error.get("kind") or "error")
        message = str(error.get("message") or "")
        head = f"{kind}: {message}" if message else kind

        text = Text()
        text.append("\n\n")
        text.append(
            f"{BODY_INDENT}{'─' * max(8, min(body_width, ERROR_RULE_WIDTH))}\n",
            style=Style(color=colors.failed),
        )
        text.append(f"{BODY_INDENT}{head}", style=Style(color=colors.failed, bold=True))

        stack = str(error.get("stack") or "")
        if stack.strip():
            shown, hidden = _clip_lines(stack, WIDE_STACK_LINES if self._expanded else STACK_LINES)
            text.append("\n\n")
            text.append_text(
                _prefixed(Text(shown, style=Style(color=colors.dim)), BODY_INDENT, BODY_INDENT)
            )
            if hidden:
                text.append("\n")
                text.append(BODY_INDENT)
                text.append_text(format_expand_hint(hidden))
        return text

    # -- internals ---------------------------------------------------------

    def _calls(self, data: dict) -> list[dict]:
        calls = data.get("calls")
        if not isinstance(calls, list):
            return []
        return [call for call in calls if isinstance(call, dict)]

    def _cell_width(self) -> int:
        """Columns this block may draw in.

        Re-measured every render rather than cached: the block is mounted once
        and the terminal resizes under it, and a body rendered at the old width
        keeps its stale wrap until something else forces a repaint.
        """
        widths = [
            getattr(self.size, "width", 0),
            getattr(self.content_size, "width", 0),
            getattr(self.container_size, "width", 0),
        ]
        # Both of these raise in a bare unit test, where the block is neither
        # mounted nor in an app; silence beats a crashed render.
        with contextlib.suppress(Exception):
            widths.append(self.query_one("#tool-output", Label).size.width)
        with contextlib.suppress(Exception):
            widths.append(self.app.size.width)

        for width in widths:
            if width and width > 0:
                # The block has a one-column padding on each side.
                return max(24, width - 2)
        return 96


def _clip_lines(text: str, limit: int) -> tuple[str, int]:
    """The first ``limit`` lines of ``text`` plus how many were held back."""
    lines = text.split("\n")
    if len(lines) <= limit:
        return text, 0
    return "\n".join(lines[:limit]), len(lines) - limit


def _squash_value(value: str, width: int) -> str:
    """Fold a short pretty-printed value back onto one line.

    ``json.dumps(indent=2)`` is what the model reads and is right for anything
    big, but a five-element list became five lines where one was meant. The
    pretty form is kept whenever the folded one would not fit, so nothing that
    needs its lines loses them.
    """
    folded = " ".join(value.split())
    if len(folded) <= width:
        return folded
    return value


def _prefixed(text: Text, first: str, rest: str) -> Text:
    """Prefix ``text``'s first line with ``first`` and the rest with ``rest``.

    Rich wraps a ``Syntax`` render to the width it was given, so the indent has
    to be paid for in columns *before* rendering -- this puts it back, line by
    line, with the continuation lines aligned under the body rather than under
    the marker.
    """
    out = Text()
    for index, line in enumerate(text.split("\n", allow_blank=True)):
        if index:
            out.append("\n")
        out.append(first if index == 0 else rest)
        out.append_text(line)
    return out
