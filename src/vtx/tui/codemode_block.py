"""The ``codemode`` tool block: the script, the calls it made, its output.

Codemode is the one tool whose *input* is the work. A one-line header summary is
right for ``bash`` -- ``$ git status`` says what ran -- but the interesting part
of a script is the script, so this block shows the run rather than a receipt for
it::

    ⟩ codemode  3 tool calls · 1.2s
      ⎿ script
          rows = await tools.search(query="find dead code")
          return [r["name"] for r in rows]
      ⎿ calls
          ✓  search    0.4s
          ✓  read      0.1s
          ✗  bash      1.2s   exit 1 · tool_failure
      ⎿ output
          scanned 42 files
      ⎿ value
          { "tools": ["edit", "grep", "read"], "hits": 12 }

Four things hold it together, and each is a deliberate departure from the plain
receipt other tools get:

**Sections are labelled, not implied.** ``script`` / ``calls`` / ``output`` /
``value`` / ``error`` are named because a script's output mixes four kinds of
content that look alike and mean different things, and a reader who cannot tell
a printed line from a returned value is reading noise. The ``⎿`` marker and its
two-column indent are the same vocabulary every other tool detail uses, so the
block sits in the scrollback without looking like it came from somewhere else.

**Names and durations line up.** Tool names are padded and durations
right-aligned into a column, because a call list is a table and a table whose
numbers jitter is not a table.

**Nothing is clipped.** A script that printed four hundred lines printed them
because the model asked for them; hiding the tail to protect the scrollback hides
exactly the line that decided the outcome, and the user has no second chance to
ask for it. ``ctrl+o`` therefore has nothing left to expand. What bounds a
codemode cell is the sandbox's own ``max_output_tokens``, not a cap drawn here.

**A failure keeps its cause.** The diagnostic and the script's own traceback are
shown in full under ``error``, because the user is the one who decides whether
to trust the next script.

Anything this block does not own falls through to :class:`ToolBlock`: the
permission preview, the ask_user dialog, images, and the expansion state machine
are shared machinery and stay shared.
"""

from __future__ import annotations

import contextlib

from rich.console import Console
from rich.style import Style
from rich.text import Text
from textual import events
from textual.widgets import Label

from vtx.core.config import config
from vtx.tui.blocks import ToolBlock
from vtx.tui.formatting import format_python_source
from vtx.tui.task_ui import GLYPHS, format_ms

#: Column the ``⎿`` section markers sit in, so the block aligns with how tool
#: details render everywhere else in the scrollback.
BODY_INDENT = "  "

#: Column a section's content sits in, under its label rather than under the
#: marker -- the same two-step the approval and ask-user bodies use.
SECTION_INDENT = "  " + "  "

#: Smallest body the block will lay out for. A 20-column terminal is not a real
#: target, but the arithmetic must not go negative on the way there.
MIN_BODY_WIDTH = 24

#: Asked where a line breaks, never drawn through. Shared across renders
#: because it holds no state and building one per render would show up in the
#: time a long script takes to paint.
_WRAP_CONSOLE = Console(force_terminal=True, no_color=True, color_system=None)


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
            return Text("  running…", style=Style(color=colors.dim))
        if self._success is False:
            summary = self._ui_summary or (self.ui_data or {}).get("error", {}).get("kind")
            return Text(f"  {summary or 'failed'}", style=Style(color=colors.failed, bold=True))

        data = self.ui_data or {}
        calls = self._calls(data)
        bits: list[str] = []
        if calls:
            bits.append(f"{len(calls)} tool call" + ("" if len(calls) == 1 else "s"))
        duration = data.get("duration_ms")
        if isinstance(duration, int | float) and duration:
            bits.append(format_ms(duration))
        return Text("  " + " · ".join(bits or ["ok"]), style=Style(color=colors.dim))

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
        """The run, as labelled sections.

        Each section is rendered on its own and joined, rather than appended
        into one growing buffer: a section that decides it has nothing to say
        must not leave a stray blank line behind, and the join is where the
        spacing between sections is decided, once.
        """
        data = self.ui_data or {}
        code = str(data.get("code") or self._call_msg or "")
        printed = self._printed(data)
        value = self._value(data)
        error = data.get("error")
        calls = self._calls(data)
        images = self._image_count(data)

        sections: list[Text] = []
        if code.strip():
            sections.append(self._render_script(code))
        if calls:
            sections.append(self._render_calls(calls))
        if printed:
            sections.append(self._render_plain("output", printed))
        if value:
            sections.append(self._render_plain("value", _squash_value(value, self._body_width())))
        if isinstance(error, dict) and error:
            sections.append(self._render_error(error))
        if images:
            sections.append(
                self._render_note(
                    f"{images} image{'' if images == 1 else 's'} attached to the result"
                )
            )

        if not sections:
            return None
        return Text("\n").join(sections)

    # -- sections ----------------------------------------------------------

    def _section_label(self, label: str) -> Text:
        """The ``⎿ name`` line every section opens with."""
        colors = config.ui.colors
        line = Text()
        line.append(f"{BODY_INDENT}{GLYPHS['sub_line']} ", style=Style(color=colors.dim))
        line.append(label, style=Style(color=colors.muted))
        return line

    def _render_script(self, code: str) -> Text:
        """The script itself: highlighted, in full, under ``script``.

        Rendered as one block rather than line by line so a triple-quoted string
        is highlighted as the string it is, and wrapped to the content column
        rather than the block's, so a long line folds in the same place on
        every repaint instead of drifting with the terminal.
        """
        text = self._section_label("script")
        text.append("\n")
        source = format_python_source(code.lstrip("\n"), self._body_width())
        text.append_text(self._section_body(source))
        return text

    def _render_calls(self, calls: list[dict]) -> Text:
        """The nested tool calls as a checklist: what ran, how long, why not.

        A failure carries its own failure message and kind, which is what tells
        a model which call to avoid next time. Names are padded and durations
        right-aligned into a column so a twenty-call run reads as a table
        rather than a list of lines that happen to end in a number.

        The columns come from this run's own widest name and widest duration,
        so the table is as narrow as its content rather than as the widest name
        the tool has ever been called under.

        A failure's reason goes inline when the table leaves it room, and drops
        to its own row underneath when it does not. Hanging it off a reason
        column wider than the terminal would push the wrap past the right edge,
        and a row that runs off the edge is the one row the reader most needs
        to be able to read.
        """
        colors = config.ui.colors
        text = self._section_label("calls")

        names = [str(call.get("name") or "?") for call in calls]
        name_width = max(len(name) for name in names)
        durations = [
            format_ms(call["duration_ms"])
            for call in calls
            if isinstance(call.get("duration_ms"), int | float)
        ]
        duration_width = max((len(item) for item in durations), default=0)
        # Column a failure's reason starts at, or wraps back to: the indent, the
        # glyph, the gap after it, the name column, and the gap past the
        # duration column.
        reason_column = len(SECTION_INDENT) + 3 + name_width + 2 + duration_width + 3
        # Where a dropped-down reason hangs instead: under the name it belongs
        # to, so it still reads as that call's row.
        reason_gap = len(SECTION_INDENT) + 3
        # The narrowest a reason may be and still be readable as a sentence.
        min_reason_width = 24
        inline_reason = self._body_width() - reason_column >= min_reason_width

        for index, call in enumerate(calls):
            text.append("\n")
            text.append(SECTION_INDENT)
            ok = bool(call.get("ok", True))
            text.append(
                GLYPHS["success"] if ok else GLYPHS["failure"],
                style=Style(color=colors.success if ok else colors.failed),
            )
            text.append("  ")
            text.append(names[index].ljust(name_width))
            duration = call.get("duration_ms")
            if isinstance(duration, int | float):
                text.append(f"  {format_ms(duration).rjust(duration_width)}", style=colors.dim)
            if not ok:
                message = str(call.get("message") or "").strip()
                kind = str(call.get("kind") or "").strip()
                detail = " · ".join(part for part in (message, kind) if part)
                if not detail:
                    continue
                reason = _wrap(
                    Text(detail, style=Style(color=colors.failed)),
                    self._body_width() - (reason_column if inline_reason else reason_gap),
                )
                if inline_reason:
                    text.append("   ")
                    text.append_text(_hang(reason, reason_column))
                else:
                    text.append("\n")
                    text.append_text(_indent(reason, " " * reason_gap))
        return text

    def _render_plain(self, label: str, body: str) -> Text:
        """A text section under its own name, dimmed and indented."""
        text = self._section_label(label)
        text.append("\n")
        text.append_text(self._section_body(Text(body, style=Style(color=config.ui.colors.dim))))
        return text

    def _render_note(self, body: str) -> Text:
        """A one-line remark that belongs to the run but is not a section."""
        colors = config.ui.colors
        text = Text()
        text.append(f"{BODY_INDENT}{GLYPHS['sub_line']} ", style=Style(color=colors.dim))
        text.append(body, style=Style(color=colors.dim))
        return text

    def _render_error(self, error: dict) -> Text:
        """The failure half of the run: the diagnostic, then the traceback.

        The traceback is the part the model already reads, and the user is the
        one who decides whether to trust the next script, so it is shown too --
        whole, because a traceback cut off mid-frame is not a traceback.
        """
        colors = config.ui.colors
        kind = str(error.get("kind") or "error")
        message = str(error.get("message") or "")
        head = f"{kind} · {message}" if message else kind

        text = self._section_label("error")
        text.append("\n")
        text.append_text(
            self._section_body(Text(head, style=Style(color=colors.failed, bold=True)))
        )

        stack = str(error.get("stack") or "")
        if stack.strip():
            text.append("\n")
            text.append_text(self._section_body(Text(stack, style=Style(color=colors.dim))))
        return text

    def _section_body(self, text: Text) -> Text:
        """Wrap ``text`` to the content column and indent every resulting line.

        The wrap is done here rather than left to the ``Label`` because the
        label only knows the block's width, not that this text is indented
        under a section label: a line left to the label wraps back to the
        block's own margin and the section loses its shape exactly where it is
        hardest to read.
        """
        return _indent(_wrap(text, self._body_width()), SECTION_INDENT)

    # -- payload -----------------------------------------------------------

    def _printed(self, data: dict) -> str:
        """What the script wrote with ``text()``, one entry per line."""
        lines = [str(item).rstrip("\n") for item in (data.get("printed") or [])]
        return "\n".join(line for line in lines if line.strip())

    def _value(self, data: dict) -> str:
        """The returned value as the tool rendered it for the model.

        Read from ``ui_data`` rather than re-derived, so the block and the model
        are looking at the same bytes: a value the tool already stringified
        must not be stringified differently here.
        """
        value = data.get("value")
        return str(value) if value else ""

    def _image_count(self, data: dict) -> int:
        """How many images the script emitted.

        Named for the count rather than the images themselves because
        ``ToolBlock._images`` already holds the real image payload the base
        class renders, and shadowing it would break that.
        """
        images = data.get("images")
        return images if isinstance(images, int) and images > 0 else 0

    def _calls(self, data: dict) -> list[dict]:
        calls = data.get("calls")
        if not isinstance(calls, list):
            return []
        return [call for call in calls if isinstance(call, dict)]

    # -- geometry ----------------------------------------------------------

    def _body_width(self) -> int:
        """Columns a section's content may draw in.

        Re-measured every render rather than cached: the block is mounted once
        and the terminal resizes under it, and a body rendered at the old width
        keeps its stale wrap until something else forces a repaint.

        The label is the authority, because it is the thing that draws and its
        own left padding is the ``#tool-output`` indent from the stylesheet --
        already taken out of its reported width, and not something to subtract
        again. The block's width is the fallback for an unmounted block, and it
        is the wider of the two, so the block's own padding comes off there.
        """
        label_width = 0
        # Raises in a bare unit test, where the block is neither mounted nor in
        # an app; silence beats a crashed render.
        with contextlib.suppress(Exception):
            label_width = self.query_one("#tool-output", Label).size.width

        widths = [
            label_width,
            getattr(self.size, "width", 0) - 2,
            getattr(self.content_size, "width", 0),
            getattr(self.container_size, "width", 0),
        ]
        with contextlib.suppress(Exception):
            widths.append(self.app.size.width - 2)

        for width in widths:
            if width and width > 0:
                return max(MIN_BODY_WIDTH, width - len(SECTION_INDENT))
        return 96


def _wrap(text: Text, width: int) -> Text:
    """Word-wrap ``text`` at ``width``, preserving every line break it already has.

    A ``Label`` wraps to its own width, but it cannot know that this text has
    been indented under a section label and so has less room than the block
    does: left alone, a long line wraps back to the block's margin and the
    section loses its shape precisely where the text is hardest to read.
    Wrapping here is what lets the indent be the same on every physical line.

    Word boundaries are Rich's, because Rich is what will draw it and a
    different rule would move the break somewhere else on the next repaint.
    """
    out = Text()
    for index, line in enumerate(text.split("\n", allow_blank=True)):
        if index:
            out.append("\n")
        if not line.plain:
            out.append("")
            continue
        wrapped = _WRAP_CONSOLE.render_lines(line, _WRAP_CONSOLE.options.update_width(width))
        for position, segment in enumerate(wrapped):
            if position:
                out.append("\n")
            # ``render_lines`` pads to the full width; that padding is not
            # content, and leaving it in would be re-wrapped on the next pass.
            out.append_text(Text("".join(part.text for part in segment).rstrip()))
    return out


def _indent(text: Text, indent: str) -> Text:
    """Prefix every line of ``text`` with ``indent``.

    Applied after the wrap, so a continuation line lands under its own content
    rather than under the section marker its first line hangs from.
    """
    out = Text()
    for index, line in enumerate(text.split("\n", allow_blank=True)):
        if index:
            out.append("\n")
        out.append(indent)
        out.append_text(line)
    return out


def _hang(text: Text, column: int) -> Text:
    """Indent every line of ``text`` to ``column``, except the first.

    For a field that follows a row already on screen -- a failure's reason
    after its name and duration -- where the wrapped remainder belongs under
    the field it continues, not under the row it started on.
    """
    out = Text()
    for index, line in enumerate(text.split("\n", allow_blank=True)):
        if index:
            out.append("\n")
            out.append(" " * column)
        out.append_text(line)
    return out


def _squash_value(value: str, width: int) -> str:
    """Fold a short pretty-printed value back onto one line.

    ``json.dumps(indent=2)`` is what the model reads and is right for anything
    big, but a three-element list became three lines where one was meant. The
    pretty form is kept whenever the folded one would not fit, so nothing that
    needs its lines loses them.
    """
    folded = " ".join(value.split())
    if len(folded) <= width:
        return folded
    return value
