"""The ``codemode`` tool block: one script rendered as a notebook cell.

Codemode is the one tool whose *input* is the work. A one-line header summary is
right for ``bash`` -- ``$ git status`` says what ran -- but the interesting part
of a script is the script, and until now that was truncated to its first line
and its result flattened into "Script ok (3 tool calls)", so the user watched a
turn produce output they could never read. This block shows the cell properly:

    ⟩ In [7]: rows = await tools.search(query="find x")
              return [r["name"] for r in rows]
      Out[7]: ["read", "grep", "edit"]
      ⎿ tools.search 0.4s · read 1.2s

``In``/``Out`` numbering is IPython's language for "one execution", which is
exactly what a codemode call is; running it matters for the same reason, and
``ctrl+o`` opens the whole cell plus the per-call trace.

Anything this block does not own falls through to :class:`ToolBlock`: the
permission preview, the ask_user dialog, images, and the expansion state
machine are shared machinery and stay shared.
"""

from __future__ import annotations

from typing import Any

from rich.style import Style
from rich.text import Text
from textual.widgets import Label

from vtx.core.config import config
from vtx.tui.blocks import ToolBlock
from vtx.tui.formatting import format_python_source
from vtx.tui.task_ui import GLYPHS, format_ms
from vtx.tui.tool_output import format_expand_hint

#: Source lines visible in a collapsed cell. Generous on purpose: the script is
#: the payload, so the cap exists only so a 200-line script does not bury the
#: turn that ran it.
CODE_LINES = 16
#: Lines of returned value, same reasoning.
VALUE_LINES = 12

#: Expanded multiples. ``ctrl+o`` is an explicit "show me everything".
WIDE_CODE_LINES = 80
WIDE_VALUE_LINES = 60

#: Traceback lines under a failed cell, collapsed and expanded.
STACK_LINES = 5
WIDE_STACK_LINES = 40


class CodemodeToolBlock(ToolBlock):
    """One ``codemode`` call as an IPython-style cell."""

    #: Cells constructed in this process. A module-visible counter rather than a
    #: per-session one because a resumed session replays its cells into a live
    #: process, and a restarted kernel is what resets the numbering in the first
    #: place.
    execution_count = 0

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        CodemodeToolBlock.execution_count += 1
        self._execution = CodemodeToolBlock.execution_count

    # -- header ------------------------------------------------------------

    @property
    def _prompt(self) -> str:
        return f"In [{self._execution}]:"

    def _format_header(self, truncate: bool = True) -> Text:
        # The permission box and the questionnaire are the base class's; both
        # need the tool name and the call text they were built around.
        if self._awaiting_approval or self._ask_dialog is not None:
            return super()._format_header(truncate=truncate)

        colors = config.ui.colors
        if self._success is None:
            prompt_style = colors.running
        elif self._success is False:
            prompt_style = colors.failed
        else:
            prompt_style = colors.accent if config.ui.colored_tool_badge else colors.muted

        result = Text()
        result.append(f"{self._icon} ", style=Style(color=prompt_style, bold=True))
        result.append(self._prompt, style=Style(color=prompt_style, bold=True))
        result.append_text(self._header_status())
        return result

    def _header_status(self) -> Text:
        """What the cell ended up doing, on the prompt line.

        Call count and duration are the two facts that decide how the user reads
        the cell below: three tool calls and two seconds is a different story
        from three tool calls and ninety seconds.
        """
        colors = config.ui.colors
        data = self._ui_data or {}

        if self._success is None:
            return Text(" running...", style=Style(color=colors.dim))
        if self._success is False:
            error = data.get("error") or {}
            kind = str(error.get("kind") or "failed")
            return Text(f" {kind}", style=Style(color=colors.failed, bold=True))

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
        # ToolEndEvent lands before execution, so this is the moment the script
        # becomes known and the cell can draw its input while it is still
        # running. Rendering here rather than only in set_result is the whole
        # difference between a cell that appears and one that pops in at the end.
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
        data = self._ui_data or {}
        code = str(data.get("code") or self._call_msg or "")
        value = data.get("value")
        printed = [str(item) for item in (data.get("printed") or []) if str(item).strip()]
        error = data.get("error")
        if not code.strip() and not value and not printed and not error:
            return None

        colors = config.ui.colors
        prompt = f"{self._prompt} "
        indent = " " * len(prompt)
        body_width = max(24, self._cell_width() - len(prompt))

        text = Text()
        text.append(prompt, style=Style(color=colors.accent, bold=True))

        limit = WIDE_CODE_LINES if self._expanded else CODE_LINES
        shown, hidden = _clip_lines(code, limit)
        source = format_python_source(shown, body_width)
        if source.plain:
            text.append_text(_prefixed(source, "", indent))
        if hidden:
            text.append("\n")
            text.append(indent)
            text.append_text(format_expand_hint(hidden))

        for chunk in printed:
            text.append("\n")
            text.append(chunk, style=Style(color=colors.muted))

        if value:
            out_prompt = f"Out [{self._execution}]: "
            shown, hidden = _clip_lines(
                str(value), WIDE_VALUE_LINES if self._expanded else VALUE_LINES
            )
            text.append("\n\n")
            text.append(out_prompt, style=Style(color=colors.accent, bold=True))
            text.append_text(_prefixed(Text(shown), "", " " * (len(out_prompt) - 1)))
            if hidden:
                text.append("\n")
                text.append(" " * (len(out_prompt) - 1))
                text.append_text(format_expand_hint(hidden))

        if isinstance(error, dict) and error:
            text.append_text(self._render_error(error, indent, body_width))

        if data.get("output_truncated"):
            text.append("\n\n")
            text.append(
                f"{indent}{GLYPHS['sub_line']} [Output truncated to fit the model's context]",
                style=Style(color=colors.notice),
            )

        images = data.get("images")
        if isinstance(images, int) and images > 0:
            label = "image" if images == 1 else "images"
            text.append("\n\n")
            text.append(
                f"{indent}{GLYPHS['sub_line']} {images} {label} attached to the result",
                style=Style(color=colors.dim),
            )

        calls = self._calls(data)
        if calls:
            text.append_text(self._render_trace(calls, indent, body_width))

        return text

    def _render_error(self, error: dict, indent: str, body_width: int) -> Text:
        """The failure half of the cell: a rule, the diagnostic, the traceback.

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
        text.append(f"{indent}{'─' * max(8, min(body_width, 48))}\n", style=Style(color=colors.failed))
        text.append(f"{indent}{head}", style=Style(color=colors.failed, bold=True))

        stack = str(error.get("stack") or "")
        if stack.strip():
            shown, hidden = _clip_lines(
                stack, WIDE_STACK_LINES if self._expanded else STACK_LINES
            )
            text.append("\n\n")
            text.append_text(
                _prefixed(Text(shown, style=Style(color=colors.dim)), indent, indent)
            )
            if hidden:
                text.append("\n")
                text.append(indent)
                text.append_text(format_expand_hint(hidden))
        return text

    def _render_trace(self, calls: list[dict], indent: str, body_width: int) -> Text:
        """The nested tool calls: one summary line, one line each when expanded.

        The collapsed line is the summary that used to be the only thing shown,
        which is why it survives there. Per-call detail goes behind expansion
        because that is where the durations and the failure reasons are worth
        reading, and forty of them in a scrollback are not.
        """
        colors = config.ui.colors
        text = Text()
        text.append("\n\n")
        text.append(f"{indent}{GLYPHS['sub_line']} ", style=Style(color=colors.dim))

        if not self._expanded:
            parts = [_call_label(call) for call in calls[:6]]
            if len(calls) > 6:
                parts.append(f"+{len(calls) - 6} more")
            text.append(" · ".join(parts), style=Style(color=colors.dim))
            return text

        for index, call in enumerate(calls):
            if index:
                text.append("\n")
                text.append(f"{indent}  ")
            ok = bool(call.get("ok", True))
            glyph = GLYPHS["success"] if ok else GLYPHS["failure"]
            style = colors.success if ok else colors.failed
            text.append(f"{glyph} ", style=Style(color=style))
            text.append(str(call.get("name") or "?"))
            message = str(call.get("message") or "")
            if not ok and message:
                text.append(f" - {message}", style=Style(color=colors.failed))
            duration = call.get("duration_ms")
            if isinstance(duration, int | float):
                text.append(f"  {format_ms(duration)}", style=Style(color=colors.dim))
        return text

    # -- internals ---------------------------------------------------------

    def _calls(self, data: dict) -> list[dict]:
        calls = data.get("calls")
        if not isinstance(calls, list):
            return []
        return [call for call in calls if isinstance(call, dict)]

    def _cell_width(self) -> int:
        """Columns this cell may draw in.

        Re-measured every render rather than cached: the block is mounted once
        and the terminal resizes under it, and a cell rendered at the old width
        keeps its stale wrap until something else forces a repaint.
        """
        widths = [
            getattr(self.size, "width", 0),
            getattr(self.content_size, "width", 0),
            getattr(self.container_size, "width", 0),
        ]
        try:
            widths.append(self.query_one("#tool-output", Label).size.width)
        except Exception:
            pass
        try:
            widths.append(self.app.size.width)
        except Exception:
            pass

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


def _prefixed(text: Text, first: str, rest: str) -> Text:
    """Prefix ``text``'s first line with ``first`` and the rest with ``rest``.

    Rich wraps a ``Syntax`` render to the width it was given, so the prompt has
    to be paid for in columns *before* rendering -- this puts it back, line by
    line, with the continuation lines aligned under the code rather than under
    the prompt.
    """
    out = Text()
    for index, line in enumerate(text.split("\n", allow_blank=True)):
        if index:
            out.append("\n")
        out.append(first if index == 0 else rest)
        out.append_text(line)
    return out


def _call_label(call: dict) -> str:
    name = str(call.get("name") or "?")
    duration = call.get("duration_ms")
    label = f"{name} {format_ms(duration)}" if isinstance(duration, int | float) else name
    if not call.get("ok", True):
        label += f" ({call.get('kind') or 'error'})"
    return label
