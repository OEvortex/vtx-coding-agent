"""The ``codemode`` tool block: the script, the calls it made, its output.

Codemode is the one tool whose *input* is the work. A one-line header summary
is right for ``bash`` -- ``$ git status`` says what ran -- but the interesting
part of a script is the script, so this block shows the run rather than a
receipt for it::

    codemode
    rows = await tools.search(query="find dead code")
    return [r["name"] for r in rows]

    ✓ search {"query":"find dead code"} 412ms
    ✓ read {"path":"pyproject.toml"} 90ms
    ✗ bash {"command":"ruff check"} 1.2s  exit 1 · tool_failure

    scanned 42 files
    ==> text 2/2 <==
    { "tools": ["edit", "grep"], "hits": 12 }

    Took 1.2s

Four things hold it together, and each answers a question the bare payload
cannot.

**There are no sections.** The run reads top to bottom as one thing: what the
model wrote, what it called, what came back, how long it took -- in that order,
separated by blank lines and nothing else. Labelling the parts (``script`` /
``calls`` / ``output`` / ``value``) was tried and removed: the labels restate
what the shape already says, and there are four of them, so a block of eleven
real lines carried four of furniture. What survives is a rule that does work:
**a printed line and a returned value are not the same claim**, so a run with
both labels its items ``==> text N/M <==`` and the returned value is simply the
last item.

**A call shows what it was called with.** ``✓ grep`` twice in a row says
nothing; ``grep {"pattern":"name"}`` then ``grep {"pattern":"path"}`` says the
script is walking a list, and the arguments are the only part of a call that
tells one from another. Compact JSON, recorded at
:data:`vtx.codemode.types.ARGS_PREVIEW_CHARS` and cut again for the collapsed
row, because a run's whole point is that the user can see what it did without
expanding anything.

**Names, durations, and arguments line up.** A call list is a table, and a
table whose numbers jitter is not a table. The columns are sized from this run's
own widest name and widest duration, and arguments take the room that is left.

**Runs are drawn while they happen.** A script may sit on eight slow tools for
ninety seconds; a block that says "running" for ninety seconds is
indistinguishable from one that has hung. Each call appears as the host admits
it -- ``…`` while in flight -- the clock runs, and the output is withheld until
the run ends, because half-written JSON flickering into place is worse than a
moment's wait.

The default view is a preview; nothing is lost. Code, calls, and output collapse
to :data:`SCRIPT_PREVIEW_LINES`, :data:`CALL_PREVIEW_COUNT`, and
:data:`OUTPUT_PREVIEW_LINES` *wrapped* lines, and ``ctrl+o`` reveals the rest.
Each keeps the end that carries the answer, and says how much it left out. A
four-hundred-line script was printed because the model asked for it; what should
not happen is that it buries the six lines that decided the outcome.

Two places deliberately part company with pi, both because a field this block
carries does not exist there:

- **The call list keeps the tail and so does the output.** pi keeps the *head*
  of its output, which was fine when the returned value rode at the end of that
  same block: previewing the head threw the answer away. Nothing was ever
  written about the asymmetry against pi's own call list, which keeps the tail
  for exactly the reason the failure usually lands last.
- **A failed call's reason stays on the row when collapsed.** pi drops it and
  leaves a bare ``✗``, so a collapsed failure cannot be told from a denial, a
  missing file, or a bad argument without expanding the whole block.

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
from vtx.tui.task_ui import GLYPHS, format_call_ms, format_run_ms

#: Column the block's content sits in, matching every other tool detail in the
#: scrollback.
BODY_INDENT = "  "

#: Smallest body the block will lay out for. A 20-column terminal is not a real
#: target, but the arithmetic must not go negative on the way there.
MIN_BODY_WIDTH = 24

#: Script lines shown before ``ctrl+o``. A script's head is its imports, its
#: loop and its return: what it came to do. The tail of a long script is
#: elaboration.
SCRIPT_PREVIEW_LINES = 10

#: Nested calls shown before ``ctrl+o``. The *tail*: a script that fails fails
#: at the end, and the calls that led there are the ones being read.
CALL_PREVIEW_COUNT = 8

#: Printed-output lines shown before ``ctrl+o``. The *tail*, because the
#: returned value is the last item and is the answer.
OUTPUT_PREVIEW_LINES = 5

#: Characters of a call's arguments on a collapsed row. Wider than a terminal
#: can usefully show anyway, and long enough that a real call is not cut.
COLLAPSED_ARGS_CHARS = 80

#: Narrowest an argument column may be and still be worth drawing. When less
#: than this is left after the name and duration columns, the arguments are
#: dropped entirely rather than truncated to a stub, because ``{"pat…`` tells
#: the reader nothing the call name did not.
MIN_ARGS_WIDTH = 16

#: Narrowest a line of output may wrap to. Below this a JSON blob is fragments.
MIN_TEXT_WIDTH = 24

#: How often the running clock redraws. Fast enough to read as counting, slow
#: enough not to repaint a long script twice a second.
TICK_SECONDS = 0.5

#: Asked where a line breaks, never drawn through. Shared across renders
#: because it holds no state and building one per render would show up in the
#: time a long script takes to paint.
_WRAP_CONSOLE = Console(force_terminal=True, no_color=True, color_system=None)

#: Width the highlighter is asked for when it must not wrap. The block wraps
#: itself, against the width left over by the indent, so the highlight's own
#: wrapping would break in the wrong place.
_UNWRAPPED = 100_000


class CodemodeToolBlock(ToolBlock):
    """One ``codemode`` call: the script, its nested calls, and its output."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        #: Calls reported live, in admission order. Kept separately from
        #: ``ui_data`` because these arrive while the run is still going and the
        #: payload that replaces them does not exist yet.
        self._live_calls: list[dict] = []
        #: When the script started running, for the clock. ``None`` once the
        #: result has landed, at which point the recorded duration is the truth
        #: and a wall clock would only drift from it.
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
        status = self._header_status()
        if status.plain:
            result.append(" · ", style=Style(color=colors.dim))
        result.append_text(status)
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
            inflight = len(self._calls()) - len(done)
            if inflight > 0:
                bits.append(f"{inflight} in flight")
            return Text(" · ".join(bits), style=Style(color=colors.running))

        if self._success is False:
            summary = self._ui_summary or (self.ui_data or {}).get("error", {}).get("kind")
            return Text(summary or "failed", style=Style(color=colors.failed, bold=True))

        calls = self._calls()
        bits = []
        if calls:
            bits.append(f"{len(calls)} tool call" + ("" if len(calls) == 1 else "s"))
            failed = sum(1 for call in calls if not self._is_ok(call))
            if failed:
                bits.append(f"{failed} failed")
        return Text(" · ".join(bits), style=Style(color=colors.dim))

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
        if self._success is not None:
            self._stop_tick()
            return
        self._refresh_live()

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

    def _refresh_live(self) -> None:
        """Repaint body and header together.

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

    def _render_live_output(self) -> None:
        self._refresh_live()

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

    # -- the run -----------------------------------------------------------

    def _render_cell(self) -> Text | None:
        """The run: the script, the calls, the output, the clock.

        Each part is rendered on its own and joined with a blank line, because
        the blank line is the only separator the block has and deciding it once
        here is what keeps it from being decided twice.
        """
        data = self.ui_data or {}
        code = str(data.get("code") or self._call_msg or "")
        calls = self._calls()
        error = data.get("error")

        parts: list[Text] = []
        if code.strip():
            parts.append(self._render_script(code))
        if calls:
            parts.append(self._render_calls(calls))
        output_blocks = self._output(data)
        if output_blocks:
            parts.append(self._render_output(output_blocks))
        if isinstance(error, dict) and error:
            parts.append(self._render_error(error))
        images = self._image_count(data)
        if images:
            parts.append(
                _indent(
                    Text(
                        f"{images} image{'' if images == 1 else 's'} attached to the result",
                        style=Style(color=config.ui.colors.dim),
                    ),
                    BODY_INDENT,
                )
            )

        timer = self._timer_line(data)
        if timer:
            # Indented with everything else. The clock is the last line of the
            # block, not a caption hung off it, and a caption flush to the
            # margin is the one line that reads as belonging to the next block.
            parts.append(_indent(timer, BODY_INDENT))

        if not parts:
            return None
        return Text("\n\n").join(parts)

    # -- the script --------------------------------------------------------

    def _render_script(self, code: str) -> Text:
        """The script, syntax-highlighted, under the header.

        Highlighted as one block rather than line by line so a triple-quoted
        string is highlighted as the string it is, and wrapped to the content
        column so a long line folds in the same place on every repaint instead
        of drifting with the terminal.
        """
        body = code.strip("\n").rstrip()
        if not body.strip():
            return Text()
        width = self._body_width()
        source = _wrap(format_python_source(body, _UNWRAPPED), width)
        return _indent(
            _preview(source, SCRIPT_PREVIEW_LINES, "start", self._expanded), BODY_INDENT
        )

    # -- the calls ---------------------------------------------------------

    def _render_calls(self, calls: list[dict]) -> Text:
        """The nested tool calls as a list of ``<glyph> <name> <args> <duration>``.

        Each field is dropped when it has nothing to say, so a call with no
        arguments is not padded out with an empty column and an in-flight call
        is just a glyph and a name. Ragged, deliberately: a duration column wide
        enough to hold ``1.4s`` next to a run of ``76ms`` rows is mostly empty
        space, and the eye finds a duration by scanning for the number far more
        easily than by scanning for a column it cannot see.

        Previewed to the tail: a script that fails fails at the end, and the
        calls that led there are the ones being read.
        """
        hidden = 0 if self._expanded else max(0, len(calls) - CALL_PREVIEW_COUNT)
        shown = calls[len(calls) - CALL_PREVIEW_COUNT :] if hidden else calls

        rows: list[Text] = []
        if hidden:
            rows.append(_earlier_calls_hint(hidden))
        rows.extend(self._render_call(call) for call in shown)
        return _indent(Text("\n").join(rows), BODY_INDENT)

    def _render_call(self, call: dict) -> Text:
        colors = config.ui.colors
        status = str(call.get("status") or "")
        running = self._is_running(call)
        cancelled = status == "cancelled"
        ok = self._is_ok(call)

        if running:
            glyph, glyph_style = GLYPHS["running"], Style(color=colors.running)
        elif cancelled:
            glyph, glyph_style = GLYPHS["cancelled"], Style(color=colors.muted)
        elif ok:
            glyph, glyph_style = GLYPHS["success"], Style(color=colors.success)
        else:
            glyph, glyph_style = GLYPHS["failure"], Style(color=colors.failed)

        name = str(call.get("name") or "?")
        args = str(call.get("args") or "")
        duration = call.get("duration_ms")
        timing = format_call_ms(duration) if isinstance(duration, int | float) else ""

        # Everything that is not the arguments: the indent, the glyph, the name,
        # and the duration. Arguments are cut to what is genuinely left, because
        # a row running past the right edge is the one row whose arguments cannot
        # be read without scrolling sideways.
        fixed = len(BODY_INDENT) + 2 + len(name) + (len(timing) + 1 if timing else 0)
        available = self._body_width() - fixed

        row = Text()
        row.append(glyph, style=glyph_style)
        row.append(" ")
        row.append(name, style=colors.title)

        if args and available >= MIN_ARGS_WIDTH:
            row.append(" ")
            row.append(
                args if self._expanded else _elide(args, min(COLLAPSED_ARGS_CHARS, available)),
                style=Style(color=colors.dim),
            )

        if timing:
            row.append(" ")
            row.append(timing, style=colors.dim)

        # The reason stays on the row even when collapsed. A bare `✗` cannot be
        # told from a denial, a missing file and a bad argument, and expanding
        # the whole block to find out is a poor trade for one field.
        if not ok and not running and not cancelled:
            detail = _failure_reason(call)
            if detail:
                row.append("  ")
                row.append_text(
                    _hang(
                        _wrap(
                            Text(detail, style=Style(color=colors.failed)),
                            max(MIN_TEXT_WIDTH, self._body_width()),
                        ),
                        len(BODY_INDENT) + 2 + len(name),
                    )
                )
        return row

    # -- the output --------------------------------------------------------

    def _output(self, data: dict) -> list[Text]:
        """What the script produced, as a list of wrapped blocks.

        The printed items and the returned value arrive as separate things and
        are put back into one list, value last, because that is what a script
        means by them: findings along the way, then the answer. Labelled
        ``==> text N/M <==`` only when there is more than one, because with a
        single item the label restates what the shape already says.

        Console output (``print``) is grouped at the end, in a
        ``<console_output>`` block, because that is where it belongs: the model
        sees it after the returned value, and a human scanning the block reads
        findings, then the answer, then the chatter.

        Kept as blocks rather than pre-joined lines because the preview has to
        cut *between* items: a budget counted in lines lands the cut between a
        label and the text it names, leaving ``scanned 42 files`` headed by
        ``==> text 2/3 <==`` and the first item's label gone entirely.
        """
        colors = config.ui.colors
        items: list[str] = []
        for chunk in data.get("printed") or []:
            text = str(chunk).rstrip("\n")
            if text.strip():
                items.append(text)
        value = str(data.get("value") or "")
        if value:
            items.append(value)
        console = [str(c).rstrip("\n") for c in (data.get("console") or []) if str(c).strip()]

        blocks: list[Text] = []
        total = len(items)
        for index, body in enumerate(items, 1):
            label = f"==> text {index}/{total} <==" if total > 1 else ""
            content = f"{label}\n{body}" if label else body
            blocks.append(Text(content, style=Style(color=colors.dim)))
        if console:
            blocks.append(
                Text(
                    "<console_output>\n" + "\n".join(console) + "\n</console_output>",
                    style=Style(color=colors.dim),
                )
            )
        return blocks

    def _render_output(self, blocks: list[Text]) -> Text:
        """The run's output, dimmed, as one wrapped block.

        Withheld entirely while the script runs: half-written JSON flickering
        into place is worse than a moment's wait, and the call list above is
        already saying what is happening.
        """
        if self._success is None:
            return Text()
        width = self._body_width()
        wrapped = [_wrap(block, width) for block in blocks]
        return _indent(
            _preview_items(wrapped, OUTPUT_PREVIEW_LINES, self._expanded, keep="start"),
            BODY_INDENT,
        )

    # -- the failure -------------------------------------------------------

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

        parts = [
            _wrap(Text(head, style=Style(color=colors.failed, bold=True)), self._body_width())
        ]
        stack = str(error.get("stack") or "")
        if stack.strip():
            parts.append(_wrap(Text(stack, style=Style(color=colors.dim)), self._body_width()))
        return _indent(Text("\n").join(parts), BODY_INDENT)

    # -- the clock ---------------------------------------------------------

    def _timer_line(self, data: dict) -> Text | None:
        """``Elapsed 4.2s`` while running, ``Took 1.2s`` once finished.

        Last, because it is a fact about the whole block rather than about any
        one line of it. The recorded duration wins over the wall clock once
        there is one: it is monotonic and it survives a reload, where a clock
        started at render time has long since restarted from zero.
        """
        colors = config.ui.colors
        duration = data.get("duration_ms")
        if self._success is None:
            elapsed = self._elapsed_ms()
            if not elapsed:
                return None
            text = f"Elapsed {format_run_ms(elapsed)}"
        elif isinstance(duration, int | float) and duration:
            text = f"Took {format_run_ms(duration)}"
        else:
            return None
        return Text(text, style=Style(color=colors.dim))

    # -- payload -----------------------------------------------------------

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
        """Columns the content may draw in.

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
                return max(MIN_BODY_WIDTH, width - len(BODY_INDENT))
        return 96


def _failure_reason(call: dict) -> str:
    """Why a call failed: its message and its kind, or nothing."""
    return " · ".join(
        part
        for part in (str(call.get("message") or "").strip(), str(call.get("kind") or "").strip())
        if part
    )


def _elide(text: str, width: int) -> str:
    """Cut ``text`` to ``width`` on a character boundary, marking the cut."""
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    if width <= 3:
        return "." * width
    return text[: width - 3] + "..."


def _earlier_calls_hint(hidden: int) -> Text:
    """The line above a collapsed call list saying what it dropped.

    Counted in calls, and said as ``earlier`` rather than ``more``, because the
    tail was kept. Placed above the rows because the rows that survived are the
    ones at the end, and a notice below them would read as belonging to the last
    one.
    """
    colors = config.ui.colors
    unit = "call" if hidden == 1 else "calls"
    text = Text()
    text.append(f"... ({hidden} earlier {unit}, ", style=Style(color=colors.dim))
    text.append("ctrl+o", style=Style(color=colors.muted))
    text.append(" to expand)", style=Style(color=colors.dim))
    return text


def _preview_items(blocks: list[Text], limit: int, expanded: bool, keep: str = "start") -> Text:
    """Keep ``limit`` wrapped lines of ``blocks``, cutting whole items.

    Whole items, because an item is a labelled claim about the run and half an
    item is a label pointing at nothing. The end kept depends on which end
    carries the answer: the returned value is the last *text* item, so keeping
    the head is what keeps it in view as long as the early items are short
    enough to leave room. The console chatter is last and is the first thing
    dropped, which is the right order to drop it in.

    One guarantee on top of the count: the last text item (the answer) is
    always kept, even when the early items alone fill the budget. An answer
    that disappears behind ``... (N more lines, ctrl+o to expand)`` is an
    answer that has to be expanded to be read, which defeats the point of a
    preview. If the answer itself is bigger than the budget it is shown in
    full and the hint is dropped, because a preview that hides the answer is
    worse than no preview at all.

    The count in the hint is lines, not items, because a line is what the
    reader is counting on screen.
    """
    if expanded or not blocks:
        return Text("\n").join(blocks)

    heights = [len(block.split("\n", allow_blank=True)) for block in blocks]
    total = sum(heights)

    # The answer is the last block that is not the console chatter. Keep it
    # pinned so it survives the cut; everything else competes for the rest.
    if blocks and _is_console_block(blocks[-1]) and len(blocks) >= 2:
        answer_index = len(blocks) - 2
    else:
        answer_index = len(blocks) - 1
    answer_index = max(0, min(answer_index, len(blocks) - 1))

    kept: list[Text] = []
    used = 0
    for index, block in enumerate(blocks):
        if index == answer_index:
            continue
        height = heights[index]
        if used + height > limit:
            break
        kept.append(block)
        used += height
        if used >= limit:
            break

    answer_height = heights[answer_index]
    answer_block = blocks[answer_index]
    if answer_block not in kept:
        # Make room for the answer by dropping earlier items from the end of
        # the kept set, one at a time, until it fits or there is nothing left
        # to drop. An answer that disappears behind the hint is an answer that
        # has to be expanded to be read.
        while kept and used + answer_height > limit:
            dropped = kept.pop()
            used -= heights[blocks.index(dropped)]
        if used + answer_height <= limit or not kept:
            kept.append(answer_block)
            used += answer_height
        # Otherwise the answer itself is bigger than the whole budget; show it
        # alone, because a preview that hides the answer is worse than no
        # preview at all.
        elif answer_height > limit:
            kept = [answer_block]
            used = answer_height

    # Restore original order.
    kept_set = {id(b) for b in kept}
    kept = [b for b in blocks if id(b) in kept_set]

    hidden = total - sum(heights[blocks.index(b)] for b in kept)
    if hidden <= 0 or not kept:
        return Text("\n").join(kept)
    hint = _expand_hint(hidden)
    if keep == "start":
        return Text("\n").join([*kept, hint])
    return Text("\n").join([hint, *kept])


def _is_console_block(block: Text) -> bool:
    return block.plain.lstrip().startswith("<console_output>")


def _preview(text: Text, limit: int, keep: str, expanded: bool = False) -> Text:
    """Show ``limit`` physical lines of ``text`` and say how many were dropped.

    Physical lines, not logical ones: a single line of minified JSON wraps
    across the whole terminal, and a budget counted in source lines would let
    it through untouched. That is the whole reason this is not a slice.

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
    return Text("\n").join([Text("\n").join(kept), _expand_hint(hidden)])


def _expand_hint(hidden: int) -> Text:
    colors = config.ui.colors
    unit = "line" if hidden == 1 else "lines"
    text = Text()
    text.append(f"... ({hidden} more {unit}, ", style=Style(color=colors.dim))
    text.append("ctrl+o", style=Style(color=colors.muted))
    text.append(" to expand)", style=Style(color=colors.dim))
    return text


def _wrap(text: Text, width: int) -> Text:
    """Word-wrap ``text`` at ``width``, preserving every line break it already has.

    A ``Label`` wraps to its own width, but it cannot know that this text has
    been indented under the block's own margin and so has less room than the
    block does: left alone, a long line wraps back to that margin and the shape
    is lost precisely where the text is hardest to read. Wrapping here is what
    lets the indent be the same on every physical line.

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
