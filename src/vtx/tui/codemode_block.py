"""The ``codemode`` tool block: the script, the calls it made, its output.

Codemode is the one tool whose *input* is the work. A one-line header summary
is right for ``bash`` -- ``$ git status`` says what ran -- but the interesting
part of a script is the script, so this block shows the run rather than a
receipt for it::

    ⟩ codemode  3 tool calls · 1.2s
      ⎿ script
           1  rows = await tools.search(query="find dead code")
           2  return [r["name"] for r in rows]
      ⎿ calls
           ✓  search  {"query":"find dead code"}   0.4s
           ✓  read    {"path":"pyproject.toml"}    0.1s
           ✗  bash    {"command":"false"}          1.2s  exit 1 · tool_failure
      ⎿ output
           scanned 42 files
      ⎿ value
           { "tools": ["edit", "grep", "read"], "hits": 12 }

Five things hold it together, and each answers a question the bare payload
cannot:

**Sections are labelled, not implied.** ``script`` / ``calls`` / ``output`` /
``value`` / ``error`` are named because a script's output mixes four kinds of
content that look alike and mean different things, and a reader who cannot tell
a printed line from a returned value is reading noise. The ``⎿`` marker and its
two-column indent are the same vocabulary every other tool detail uses, so the
block sits in the scrollback without looking like it came from somewhere else.

**A call shows what it was called with.** ``✓ grep`` twice in a row says
nothing; ``grep {"pattern":"name"}`` then ``grep {"pattern":"path"}`` says the
script is walking a list, and the arguments are the only part of a call that
distinguishes one from another. They are recorded as compact JSON, cut to
:data:`vtx.codemode.types.ARGS_PREVIEW_CHARS`, because a run's whole point is
that the user can see what it did without expanding anything.

**Names, durations, and arguments line up.** A call list is a table, and a
table whose numbers jitter is not a table. Arguments get whatever room is left
after the fixed columns and are dropped before a duration is, because a
duration that moved is worse than an argument that was not shown.

**Runs are drawn while they happen.** A script may sit on eight slow tools for
ninety seconds; a block that says "running" for ninety seconds is
indistinguishable from one that has hung. Each call appears as the host admits
it -- marked ``…`` while in flight -- and the header carries a clock, so
progress is visible rather than inferred.

**The default view is a preview; nothing is lost.** Script, calls, and output
collapse to :data:`SCRIPT_PREVIEW_LINES`, :data:`CALL_PREVIEW_COUNT`, and
:data:`OUTPUT_PREVIEW_LINES`, and ``ctrl+o`` reveals the rest. Each preview
keeps the end that carries the answer -- the tail of the call list, where a
failure lands, and the head of the output, where a script's own reasoning does
-- and says how much it left out. A four-hundred-line script was printed because
the model asked for it; what should not happen is that it buries the six lines
that decided the outcome. The traceback under ``error`` is never previewed,
because a traceback cut off mid-frame is not a traceback.

Anything this block does not own falls through to :class:`ToolBlock`: the
permission preview, the ask_user dialog, images, and the expansion state
machine are shared machinery and stay shared.
"""

from __future__ import annotations

import contextlib
import json
import time

from rich.console import Console
from rich.style import Style
from rich.text import Text
from textual import events
from textual.timer import Timer
from textual.widgets import Label

from vtx.coding_agent.tools.codemode import PROGRESS_SENTINEL
from vtx.core.config import config
from vtx.tui.blocks import ToolBlock
from vtx.tui.formatting import format_python_source
from vtx.tui.task_ui import GLYPHS, format_ms
from vtx.tui.tool_output import format_expand_hint

#: Column the ``⎿`` section markers sit in, so the block aligns with how tool
#: details render everywhere else in the scrollback.
BODY_INDENT = "  "

#: Column a section's content sits in, under its label rather than under the
#: marker -- the same two-step the approval and ask-user bodies use.
SECTION_INDENT = "  " + "  "

#: Smallest body the block will lay out for. A 20-column terminal is not a real
#: target, but the arithmetic must not go negative on the way there.
MIN_BODY_WIDTH = 24

#: Script lines shown before ``ctrl+o``. Generous on purpose: a script the model
#: writes is usually ten or twenty lines, and a preview that hides the last two
#: of a twelve-line script costs a ``ctrl+o`` and gives nothing back.
SCRIPT_PREVIEW_LINES = 16

#: Nested calls shown before ``ctrl+o``. The *tail* is kept: a script that fails
#: fails at the end, and the calls that led there are the ones being looked at.
CALL_PREVIEW_COUNT = 8

#: Printed-output lines shown before ``ctrl+o``. The *head* is kept, because
#: what a script printed starts with what it decided and usually ends with a
#: summary of the same thing.
OUTPUT_PREVIEW_LINES = 5

#: Characters of a call's arguments shown beside its name. A preview long
#: enough to wrap is a preview the reader has to reassemble, and the remainder
#: is one ``ctrl+o`` away.
CALL_ARGS_CHARS = 60

#: Narrowest an argument column may be and still be worth drawing. When less
#: than this is left after the name and duration columns, the arguments are
#: dropped entirely rather than truncated to a stub, because ``{"pat…`` tells
#: the reader nothing the call name did not.
MIN_ARGS_WIDTH = 16

#: Narrowest a line of code may wrap to behind its line-number gutter. A line
#: squeezed below this is unreadable whatever it says, and at this width the
#: reader is better served by the preview hint than by fragments.
MIN_CODE_WIDTH = 24

#: How often the running header redraws its clock. Fast enough to read as
#: counting, slow enough not to repaint a long script twice a second.
TICK_SECONDS = 0.5

#: Asked where a line breaks, never drawn through. Shared across renders
#: because it holds no state and building one per render would show up in the
#: time a long script takes to paint.
_WRAP_CONSOLE = Console(force_terminal=True, no_color=True, color_system=None)

#: Width the highlighter is asked for when it must not wrap. The block wraps
#: each logical line itself, against the width left over after the line-number
#: gutter, so the highlight's own wrapping would break in the wrong place.
_UNWRAPPED = 100_000


class CodemodeToolBlock(ToolBlock):
    """One ``codemode`` call: the script, its nested calls, and its output."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        #: Calls reported live, in admission order. Kept separately from
        #: ``ui_data`` because these arrive while the run is still going and the
        #: payload that replaces them does not exist yet.
        self._live_calls: list[dict] = []
        #: When the script started running, for the header clock. ``None`` once
        #: the result has landed, at which point the recorded duration is the
        #: truth and a wall clock would only drift from it.
        self._started_at: float | None = None
        self._tick: Timer | None = None

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
        story from three tool calls and ninety seconds. While running, the count
        of calls still in flight is the fact that separates working from hung.
        A failure reuses the tool's own one-line summary, which is the one that
        says what already ran and was not undone.
        """
        colors = config.ui.colors

        if self._success is None:
            bits: list[str] = ["running"]
            done = [call for call in self._calls() if not self._is_running(call)]
            if done:
                bits.append(f"{len(done)} call" + ("" if len(done) == 1 else "s") + " done")
            inflight = len(self._live_calls) - len(done)
            if inflight > 0:
                bits.append(f"{inflight} in flight")
            elapsed = self._elapsed_ms()
            if elapsed:
                bits.append(format_ms(elapsed))
            return Text("  " + " · ".join(bits), style=Style(color=colors.running))

        if self._success is False:
            summary = self._ui_summary or (self.ui_data or {}).get("error", {}).get("kind")
            return Text(f"  {summary or 'failed'}", style=Style(color=colors.failed, bold=True))

        data = self.ui_data or {}
        calls = self._calls()
        bits = []
        if calls:
            bits.append(f"{len(calls)} tool call" + ("" if len(calls) == 1 else "s"))
            failed = sum(1 for call in calls if not self._is_ok(call))
            if failed:
                bits.append(f"{failed} failed")
        duration = data.get("duration_ms")
        if isinstance(duration, int | float) and duration:
            bits.append(format_ms(duration))
        return Text("  " + " · ".join(bits or ["ok"]), style=Style(color=colors.dim))

    # -- live progress -----------------------------------------------------

    def update_call_msg(self, call_msg: str) -> None:
        super().update_call_msg(call_msg)
        # ToolEndEvent lands before execution, which is the moment the script
        # becomes known and the block can draw it while it is still running.
        # Rendering here rather than only in set_result is the whole
        # difference between a script that appears and one that pops in at the
        # end.
        if self._started_at is None:
            self._started_at = time.monotonic()
            self._start_tick()
        self._render_result_output()

    def append_live_output(self, delta: str) -> None:
        """Take a call frame off the tool's output channel.

        The channel carries text because that is what the rest of the TUI is
        built to stream, so a nested call is framed as text and recognised by
        its sentinel. Everything outside a sentinel is dropped rather than
        rendered: the tool streams frames and nothing else, and a buffer of text
        that no path draws would be a claim of completeness this block does not
        make. The script's own ``text()`` output arrives whole, with the result.
        """
        if not delta:
            return
        remainder = delta
        while remainder:
            _, marker, remainder = remainder.partition(PROGRESS_SENTINEL)
            if not marker:
                break
            frame, _, remainder = remainder.partition("\n")
            call = self._decode_frame(frame)
            if call is not None:
                self._record_live_call(call)

        self._request_live_render()

    def _decode_frame(self, frame: str) -> dict | None:
        try:
            call = json.loads(frame)
        except ValueError:
            return None
        return call if isinstance(call, dict) else None

    def _record_live_call(self, call: dict) -> None:
        """Merge one call into the live ledger.

        A ``running`` frame is always a new call -- the host announces one
        before it runs it, and a script may well call the same tool with the
        same arguments twice, in which case the two frames that arrive later
        belong to whichever is still in flight. Only a finished frame is looked
        for an existing row, and it takes the *oldest* still-running match,
        because with ``asyncio.gather`` several identical calls are in flight
        at once and the first to finish is the first that was issued.

        Matching finished frames to their row is what stops every call being
        drawn twice, once announced and once answered.
        """
        if not self._is_running(call):
            for index, existing in enumerate(self._live_calls):
                if (
                    self._is_running(existing)
                    and existing.get("name") == call.get("name")
                    and existing.get("args") == call.get("args")
                ):
                    self._live_calls[index] = call
                    return
        self._live_calls.append(call)

    def _start_tick(self) -> None:
        if self._tick is not None:
            return
        with contextlib.suppress(Exception):
            self._tick = self.set_interval(TICK_SECONDS, self._tick_clock)

    def _stop_tick(self) -> None:
        timer, self._tick = self._tick, None
        if timer is not None:
            with contextlib.suppress(Exception):
                timer.stop()

    def _tick_clock(self) -> None:
        # Header only: the body does not change between ticks, and a run holding
        # a two-hundred-line script should not re-highlight it twice a second.
        if self._success is not None:
            self._stop_tick()
            return
        with contextlib.suppress(Exception):
            self.query_one("#tool-header", Label).update(self._format_header())

    def _elapsed_ms(self) -> int | None:
        if self._started_at is None:
            return None
        return int((time.monotonic() - self._started_at) * 1000)

    def on_unmount(self) -> None:
        self._stop_tick()

    def set_result(self, *args, **kwargs) -> None:
        self._stop_tick()
        super().set_result(*args, **kwargs)

    def on_resize(self, event: events.Resize) -> None:
        del event
        # The base class only repaints a result it holds as detail strings. A
        # codemode block draws its own body, so without this it keeps the line
        # wrap from whatever width it was last rendered at.
        if self.ui_data or self._call_msg or self._live_calls:
            self._render_result_output()

    def _render_live_output(self) -> None:
        """Repaint from the live ledger rather than the text buffer.

        The base class renders raw stdout; a codemode run's interesting part is
        the fan-out, which arrives as frames and is kept as a list. The header
        goes with it, because "1 call done · 2 in flight" is the line that tells
        the user the run is moving.
        """
        try:
            output = self.query_one("#tool-output", Label)
        except Exception:
            return
        cell = self._render_cell()
        if cell is None:
            return
        self.remove_class("-compact")
        self.add_class("-with-details")
        output.remove_class("-hidden")
        output.remove_class("-details")
        output.remove_class("-diff-output")
        output.update(cell)
        with contextlib.suppress(Exception):
            self.query_one("#tool-header", Label).update(self._format_header())

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

    # -- result ------------------------------------------------------------

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
        calls = self._calls()
        images = self._image_count(data)

        sections: list[Text] = []
        if code.strip():
            sections.append(self._render_script(code))
        if calls:
            sections.append(self._render_calls(calls))
        if printed:
            sections.append(
                self._render_plain(
                    "output", printed, OUTPUT_PREVIEW_LINES, keep="start", previewed=True
                )
            )
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
        """The script itself: numbered, highlighted, in full, under ``script``.

        Numbered because the traceback under ``error`` names a line, and a
        ``File "<codemode>", line 12`` a reader cannot find is a traceback they
        have to take on trust. The number is drawn once per *logical* line and
        the code wrapped to what is left beside it, so a long line folds under
        its own code rather than under its own number.

        Previewed to the head: the first ten lines are what a script is, and a
        preview that hid them would be hiding the part that explains the rest.
        """
        text = self._section_label("script")
        text.append("\n")

        body = code.lstrip("\n").rstrip()
        if not body:
            return text
        lines = body.split("\n")
        digits = len(str(len(lines)))
        # Number, two spaces, then the code. Continuations get the whole gutter
        # so a wrapped line starts under its code and not under its number.
        gutter = digits + 2
        code_width = max(MIN_CODE_WIDTH, self._body_width() - gutter)

        highlighted = format_python_source(body, _UNWRAPPED)
        numbered = Text()
        for index, line in enumerate(highlighted.split("\n", allow_blank=True)):
            wrapped = _wrap(line, code_width).split("\n", allow_blank=True)
            for position, piece in enumerate(wrapped):
                if index or position:
                    numbered.append("\n")
                if not piece.plain:
                    # A blank source line keeps its number but not the padding
                    # after it: trailing spaces are invisible and make the line
                    # look like it holds something.
                    numbered.append(f"{index + 1:>{digits}}" if position == 0 else "")
                else:
                    numbered.append(f"{index + 1:>{digits}}  " if position == 0 else " " * gutter)
                    numbered.append_text(piece)

        text.append_text(
            _indent(
                _preview(numbered, SCRIPT_PREVIEW_LINES, "start", self._expanded), SECTION_INDENT
            )
        )
        return text

    def _render_calls(self, calls: list[dict]) -> Text:
        """The nested tool calls as a table: what, with what, how long, why not.

        A failure carries its own failure message and kind, which is what tells
        a model which call to avoid next time. Names are padded and durations
        right-aligned into a column so a twenty-call run reads as a table rather
        than a list of lines that happen to end in a number.

        The columns come from this run's own widest name and widest duration, so
        the table is as narrow as its content rather than as the widest name the
        tool has ever been called under. Arguments take the room that is left
        and are dropped when what is left cannot hold one, because a duration
        that jitters is worse than an argument that is not shown.

        Previewed to the tail: a script that fails fails at the end, and the
        calls that led there are the ones being read.
        """
        colors = config.ui.colors
        text = self._section_label("calls")

        hidden = 0 if self._expanded else max(0, len(calls) - CALL_PREVIEW_COUNT)
        shown = calls[len(calls) - CALL_PREVIEW_COUNT :] if hidden else calls

        names = [str(call.get("name") or "?") for call in shown]
        name_width = max(len(name) for name in names)
        durations = [
            format_ms(call["duration_ms"])
            for call in shown
            if isinstance(call.get("duration_ms"), int | float)
        ]
        duration_width = max((len(item) for item in durations), default=0)
        # Indent, glyph, gap after it, the name column, and the gap past it.
        # Arguments follow, and the duration column closes the row.
        args_column = len(SECTION_INDENT) + 3 + name_width + 2
        widest_args = max((len(str(call.get("args") or "")) for call in shown), default=0)
        available = self._body_width() - args_column - duration_width - 2
        # Whether there is *room* for a column, not whether the widest argument
        # happens to be long: `{"path":"a.py"}` is thirteen characters of the
        # most useful thing on the row, and refusing to draw it because it is
        # short is backwards.
        show_args = available >= MIN_ARGS_WIDTH
        # Sized to the rows actually drawn and then capped, rather than either
        # stretched to the right edge or held at the cap: a run whose longest
        # argument is twenty characters should not reserve sixty for it, and a
        # run carrying a serialized blob should not push the durations past the
        # edge. Either way the column is common to every row, which is the part
        # that makes the timings scannable.
        args_width = min(widest_args, CALL_ARGS_CHARS, available) if show_args else 0

        if hidden:
            text.append("\n")
            text.append_text(_indent(_hidden_calls_hint(hidden), SECTION_INDENT))

        for call in shown:
            text.append("\n")
            text.append(SECTION_INDENT)
            running = self._is_running(call)
            ok = self._is_ok(call)
            text.append(
                GLYPHS["running"] if running else (GLYPHS["success"] if ok else GLYPHS["failure"]),
                style=Style(
                    color=colors.running if running else (colors.success if ok else colors.failed)
                ),
            )
            text.append("  ")
            name = str(call.get("name") or "?")
            text.append(name.ljust(name_width), style=colors.title if not running else None)
            text.append("  ")

            args = str(call.get("args") or "")
            if show_args and args:
                text.append(
                    _elide(args, args_width).ljust(args_width), style=Style(color=colors.dim)
                )
            elif args:
                # The column is padded even when this row has nothing to put in
                # it, or the durations below would not line up.
                text.append(" " * args_width)
            text.append("  ")

            duration = call.get("duration_ms")
            if isinstance(duration, int | float):
                text.append(format_ms(duration).rjust(duration_width), style=colors.dim)
            else:
                # Keeps the duration column's right edge where it is for every
                # row, so an in-flight call does not shift the ones below it.
                text.append(" " * duration_width)

            if not ok and not running:
                detail = " · ".join(
                    part
                    for part in (
                        str(call.get("message") or "").strip(),
                        str(call.get("kind") or "").strip(),
                    )
                    if part
                )
                if detail:
                    tail = args_column + max(0, args_width) + 2 + duration_width
                    text.append_text(_reason(detail, self._body_width(), tail))
        return text

    def _render_plain(
        self,
        label: str,
        body: str,
        limit: int | None = None,
        keep: str = "start",
        previewed: bool = False,
    ) -> Text:
        """A text section under its own name, dimmed and indented.

        ``limit`` previews to that many physical lines, and only when
        ``previewed`` is set. ``value`` passes neither, because it is the
        script's answer and an answer half-shown is a wrong answer.
        """
        text = self._section_label(label)
        text.append("\n")
        wrapped = _wrap(Text(body, style=Style(color=config.ui.colors.dim)), self._body_width())
        if previewed:
            wrapped = _preview(wrapped, limit, keep, self._expanded)
        text.append_text(_indent(wrapped, SECTION_INDENT))
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
        whole, because a traceback cut off mid-frame is not a traceback, and it
        is never previewed for the same reason.
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

    def _calls(self) -> list[dict]:
        """The nested calls: the finished run's, or the live ledger's.

        Prefers the payload once it exists, because that is the authoritative
        record; falls back to the ledger while running, which is the only
        source there is.
        """
        calls = (self.ui_data or {}).get("calls")
        if isinstance(calls, list):
            return [call for call in calls if isinstance(call, dict)]
        return list(self._live_calls)

    @staticmethod
    def _is_running(call: dict) -> bool:
        return call.get("status") == "running"

    @classmethod
    def _is_ok(cls, call: dict) -> bool:
        status = call.get("status")
        if status is not None:
            return status == "ok"
        # A session replayed from disk may carry a payload written before
        # ``status`` existed, where ``ok`` is the only outcome there was.
        return bool(call.get("ok", True))

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


def _hidden_calls_hint(hidden: int) -> Text:
    """The line above a collapsed call list saying what it dropped.

    Counted in calls rather than lines, because this list is counted in calls
    everywhere else -- the header says "14 tool calls" and a hint saying "84
    lines hidden" under it reads as a different quantity of something. Placed
    above the rows because the rows that were kept are the ones at the end, and
    a notice below them would look like it belonged to the last one.
    """
    colors = config.ui.colors
    unit = "call" if hidden == 1 else "calls"
    text = Text()
    text.append(f"... ({hidden} earlier {unit}, ", style=Style(color=colors.dim))
    text.append("ctrl+o", style=Style(color=colors.muted))
    text.append(" to expand)", style=Style(color=colors.dim))
    return text


def _reason(detail: str, width: int, column: int) -> Text:
    """A failure's reason after its row, inline when it fits and below when not.

    Hanging it off a reason column wider than the terminal would push the wrap
    past the right edge, and a row that runs off the edge is the one row the
    reader most needs to be able to read.
    """
    colors = config.ui.colors
    gap = 3
    inline = width - (column + gap)
    reason = Text(detail, style=Style(color=colors.failed))
    if inline >= 24:
        return Text(" " * gap) + _hang(_wrap(reason, inline), column + gap)
    hang = len(SECTION_INDENT) + 3
    return Text("\n") + _indent(_wrap(reason, width - hang), " " * hang)


def _elide(text: str, width: int) -> str:
    """Cut ``text`` to ``width`` on a character boundary, marking the cut."""
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    if width <= 1:
        return "…"
    return text[: width - 1] + "…"


def _preview(text: Text, limit: int | None, keep: str, expanded: bool = False) -> Text:
    """Show ``limit`` physical lines of ``text`` and say how many were dropped.

    Physical lines, not logical ones: a single line of minified JSON wraps
    across the whole terminal, and a budget counted in source lines would let
    it through untouched. That is the whole reason this is not a slice.

    ``keep="start"`` for content whose head is the answer and ``"end"`` for
    activity whose tail is, mirroring which end a reader is looking for.

    ``limit=None``, or an expanded block, means show everything: the preview is
    a default view, never a ceiling, and nothing here is allowed to be the one
    place a run's own output stops existing.
    """
    if expanded or limit is None:
        return text
    lines = text.split("\n", allow_blank=True)
    if len(lines) <= limit:
        return text
    kept = lines[:limit] if keep == "start" else lines[-limit:]
    hidden = len(lines) - limit
    hint = _indent(format_expand_hint(hidden), BODY_INDENT)
    return Text("\n").join([Text("\n").join(kept), hint])


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
