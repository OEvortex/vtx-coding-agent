import re
import shutil
import string
import unicodedata
from typing import Any, ClassVar

from markdown_it.token import Token
from rich import box
from rich._loop import loop_first
from rich.console import Console, ConsoleOptions, RenderResult
from rich.markdown import (
    CodeBlock,
    Heading,
    ListElement,
    ListItem,
    Markdown,
    MarkdownElement,
    TableElement,
)
from rich.segment import Segment
from rich.style import Style
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

from vtx.core.config import config
from vtx.tui.latex import preprocess_latex

_MARKDOWN_THEME: Theme | None = None


def get_markdown_theme() -> Theme:
    global _MARKDOWN_THEME
    if _MARKDOWN_THEME is None:
        colors = config.ui.colors
        code_color = colors.markdown_code
        # A real hierarchy: every level was plain bold before, so # and #######
        # were indistinguishable. Ramp from the brightest title colour down to
        # dim, which still reads on a monochrome terminal because weight is
        # preserved at every level.
        heading_styles = {
            "markdown.h1": Style(bold=True, underline=True, color=colors.title),
            "markdown.h2": Style(bold=True, color=colors.accent),
            "markdown.h3": Style(bold=True, color=colors.markdown_heading),
            "markdown.h4": Style(bold=True, color=colors.muted),
            "markdown.h5": Style(bold=True, color=colors.dim),
            "markdown.h6": Style(bold=True, italic=True, color=colors.dim),
        }
        _MARKDOWN_THEME = Theme(
            {
                **heading_styles,
                "markdown.code": Style(color=code_color),
                "markdown.code_block": Style(color=code_color),
                "markdown.block_quote": Style(color=colors.muted),
                "markdown.item.bullet": Style(color=colors.accent),
                "markdown.item.number": Style(color=colors.accent),
                "markdown.hr": Style(color=colors.muted),
                "markdown.table.header": Style(bold=True, color=colors.markdown_heading),
                "markdown.table.border": Style(color=colors.dim),
            }
        )
    return _MARKDOWN_THEME


MARKDOWN_THEME = get_markdown_theme()


class LeftJustifiedHeading(Heading):
    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        yield from console.render(self.text, options=options.update(justify="left"))


class PlainListItem(ListItem):
    def render_bullet(self, console: Console, options: ConsoleOptions) -> RenderResult:
        render_options = options.update(width=options.max_width - 2)
        lines = console.render_lines(self.elements, render_options, style=self.style)
        bullet = Segment("- ")
        padding = Segment("  ")
        new_line = Segment("\n")
        for first, line in loop_first(lines):
            yield bullet if first else padding
            yield from line
            yield new_line

    def render_number(
        self, console: Console, options: ConsoleOptions, number: int, last_number: int
    ) -> RenderResult:
        number_width = len(str(last_number)) + 2
        render_options = options.update(width=options.max_width - number_width)
        lines = console.render_lines(self.elements, render_options, style=self.style)
        new_line = Segment("\n")
        padding = Segment(" " * number_width)
        numeral = Segment(f"{number}".rjust(number_width - 1) + " ")
        for first, line in loop_first(lines):
            yield numeral if first else padding
            yield from line
            yield new_line


class PlainListElement(ListElement):
    def on_child_close(self, context, child) -> bool:
        assert isinstance(child, ListItem)
        self.items.append(child)
        return False

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        if self.list_type == "bullet_list_open":
            for item in self.items:
                if isinstance(item, PlainListItem):
                    yield from item.render_bullet(console, options)
        else:
            number = 1 if self.list_start is None else self.list_start
            last_number = number + len(self.items)
            for index, item in enumerate(self.items):
                if isinstance(item, PlainListItem):
                    yield from item.render_number(console, options, number + index, last_number)


class PlainCodeBlock(CodeBlock):
    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        code = str(self.text).rstrip()
        syntax = Syntax(code, self.lexer_name, theme="ansi_dark", word_wrap=True, padding=0)
        yield syntax


class VtxTableElement(TableElement):
    """Table renderer tuned for a narrow chat pane.

    Rich's default uses ``show_edge=True``, which wraps every table in blank
    lines, and an unstyled rule. Dropping the edges and styling the rule keeps
    consecutive tables readable when the model emits several in one reply.
    """

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        table = Table(
            box=box.SIMPLE_HEAD,
            pad_edge=False,
            style="markdown.table.border",
            show_edge=False,
            collapse_padding=True,
            padding=(0, 1),
        )

        if self.header is not None and self.header.row is not None:
            for column in self.header.row.cells:
                heading = column.content.copy()
                heading.stylize("markdown.table.header")
                table.add_column(heading)

        ncols = len(self.header.row.cells) if self.header and self.header.row else 0
        if self.body is not None:
            for row in self.body.rows:
                cells = [element.content for element in row.cells]
                if ncols:
                    # A ragged row silently dropped its overflow and padded short
                    # rows into noise. Fold extras into the last column and pad
                    # the rest, so no value the model wrote disappears.
                    if len(cells) > ncols:
                        overflow = cells[ncols - 1 :]
                        merged = Text(" ".join(c.plain.strip() for c in overflow))
                        cells = [*cells[: ncols - 1], merged]
                    elif len(cells) < ncols:
                        cells = cells + [Text("")] * (ncols - len(cells))
                table.add_row(*cells)

        yield table


_DELIM_CELL_RE = re.compile(r"^\s*:?-{1,}:?\s*$")


def _split_row(line: str) -> list[str]:
    """Cells of a table row, splitting only on real cell separators.

    A plain ``split("|")`` broke rows the parser handles correctly: an escaped
    ``\\|`` and a pipe inside a code span are both content, and cutting on them
    invented an extra cell that the repair step then merged back with a space --
    so a well-formed row came out rewritten, with the pipe gone.
    """
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|") and not body.endswith("\\|"):
        body = body[:-1]
    cells: list[str] = []
    current: list[str] = []
    i = 0
    while i < len(body):
        char = body[i]
        if char == "\\" and i + 1 < len(body):
            current.append(body[i : i + 2])
            i += 2
            continue
        if char == "`":
            run = 0
            while i + run < len(body) and body[i + run] == "`":
                run += 1
            end = body.find("`" * run, i + run)
            if end != -1:
                current.append(body[i : end + run])
                i = end + run
                continue
        if char == "|":
            cells.append("".join(current))
            current = []
            i += 1
            continue
        current.append(char)
        i += 1
    cells.append("".join(current))
    return cells


def _join_row(cells: list[str]) -> str:
    return "| " + " | ".join(c.strip() for c in cells) + " |"


def _is_delimiter_row(cells: list[str]) -> bool:
    return bool(cells) and all(_DELIM_CELL_RE.match(c) for c in cells)


def _normalize_tables(text: str) -> str:
    """Repair ragged markdown tables before the parser rejects them.

    markdown-it requires the delimiter row to have exactly as many cells as the
    header, and silently truncates body rows to that width. So one stray pipe
    degrades a whole table into raw ``|---|`` prose, and an extra body cell
    vanishes with no sign anything was lost. Both are the model being slightly
    off, which is the normal case, so the widths are reconciled here instead.

    Fenced code is left exactly as written: a table shown in a code sample is
    content, not a table.
    """
    lines = text.splitlines(keepends=True)
    fence = [in_f for _, in_fence in _iter_outside_fences(text) for in_f in (in_fence,)]
    i = 0
    while i < len(lines) - 1:
        if fence[i] or fence[i + 1] or "|" not in lines[i] or "|" not in lines[i + 1]:
            i += 1
            continue
        hcells = _split_row(lines[i])
        dcells = _split_row(lines[i + 1])
        if not _is_delimiter_row(dcells):
            i += 1
            continue
        ncols = len(hcells)
        if len(dcells) != ncols:
            if len(dcells) > ncols:
                fixed = dcells[:ncols]
            else:
                fixed = dcells + [dcells[-1]] * (ncols - len(dcells))
            lines[i + 1] = _join_row(fixed) + _eol(lines[i + 1])
        j = i + 2
        while j < len(lines) and not fence[j] and lines[j].strip() and "|" in lines[j]:
            cells = _split_row(lines[j])
            if len(cells) > ncols:
                merged = cells[ncols - 1 :]
                cells = [*cells[: ncols - 1], " ".join(c.strip() for c in merged)]
            elif len(cells) < ncols:
                cells = cells + [""] * (ncols - len(cells))
            lines[j] = _join_row(cells) + _eol(lines[j])
            j += 1
        i = j
    return "".join(lines)


def _eol(line: str) -> str:
    """The line terminator of ``line`` ("" if it had none)."""
    body = line.rstrip("\r\n")
    return line[len(body) :]


_HTML_TAG_RE = re.compile(r"<[^>]*>")


class HtmlBlock(MarkdownElement):
    """Render an HTML block as its inner text rather than nothing.

    Rich has no handler for ``html_block``, so it fell through to an unknown
    element and rendered empty - a ``<div>`` wrapper made the whole block,
    including the readable text inside it, vanish from the reply with nothing on
    screen to say so. Unwrapping keeps the content.
    """

    def __init__(self, content: str = "") -> None:
        self.content = content

    @classmethod
    def create(cls, markdown: Markdown, token: Token) -> "HtmlBlock":
        return cls(str(token.content))

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        text = _HTML_TAG_RE.sub("", self.content).strip()
        if text:
            yield Text(text)


class CustomMarkdown(Markdown):
    elements: ClassVar[dict] = {
        **Markdown.elements,
        "heading_open": LeftJustifiedHeading,
        "bullet_list_open": PlainListElement,
        "ordered_list_open": PlainListElement,
        "list_item_open": PlainListItem,
        "fence": PlainCodeBlock,
        "code_block": PlainCodeBlock,
        "html_block": HtmlBlock,
        "table_open": VtxTableElement,
    }


# Only names that are actually HTML. A blanket ``</?[a-zA-Z][^>]*>`` also ate
# autolinks (``<https://...>``, ``<user@host>``) and plain angle-bracket text
# (``List<T>``, ``a < b > c``), trading one silent deletion for another. The
# attribute clause requires whitespace, so ``<a@b.com>`` is left alone too.
_INLINE_TAG_RE = re.compile(
    r"</?(?:b|i|u|s|em|strong|span|small|sub|sup|kbd|mark|code|a|font|del|ins|abbr)"
    r"(?:\s[^<>]*)?/?>|<!--.*?-->",
    re.S | re.I,
)
# Inline code spans: a `<br>` shown in backticks is content the writer is
# *describing*, and stripping it leaves an empty code span. Split on these
# and only transform the segments outside them.
_CODE_SPAN_RE = re.compile(r"(`+)(?!`)(.*?)(?<!`)\1(?!`)", re.S)
_BR_INLINE_RE = re.compile(r"<\s*br\s*/?\s*>", re.I)
_ASCII_PUNCT = frozenset(string.punctuation)


def _apply_outside_code(text: str, fn) -> str:
    """Apply ``fn`` to the parts of ``text`` that are not inline code spans."""
    parts: list[str] = []
    pos = 0
    for match in _CODE_SPAN_RE.finditer(text):
        parts.append(fn(text[pos : match.start()]))
        parts.append(match.group(0))
        pos = match.end()
    parts.append(fn(text[pos:]))
    return "".join(parts)


def _clean_inline_html(segment: str) -> str:
    return _INLINE_TAG_RE.sub("", _BR_INLINE_RE.sub("  \n", segment))


def _normalize_inline_html(text: str) -> str:
    """Turn inline HTML into markdown so nothing is silently dropped.

    Rich dispatches inline children through a different branch than the block
    ``elements`` map, so an inline tag cannot be hooked there. Without this,
    every ``<br>`` was deleted -- the most common tag in model output -- welding
    ``line1<br>line2`` into ``line1line2``, and any other inline tag left two
    blank lines where it had been.

    Fenced code is left exactly as written: a ``<div>`` shown in a code sample
    is content, not markup.
    """
    out = []
    for line, in_fence in _iter_outside_fences(text):
        if in_fence:
            out.append(line)
            continue
        # Work on the line without its terminator, then put the terminator back;
        # dropping it would run every line of the reply together.
        body = line.rstrip("\r\n")
        ending = line[len(body) :]
        # Rewrite only the gaps between inline code spans, so a tag the writer
        # is describing in backticks is left alone.
        out.append(_apply_outside_code(body, _clean_inline_html) + ending)
    return "".join(out)


def _strip_inline_code_ticks_in_headings(text: str) -> str:
    lines = text.splitlines(keepends=True)
    in_fence = False
    processed: list[str] = []

    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            processed.append(line)
            continue

        if in_fence:
            processed.append(line)
            continue

        if re.match(r"^\s{0,3}#{1,6}\s+", line):
            line = re.sub(r"`([^`]+)`", r"\1", line)

        processed.append(line)

    return "".join(processed)


class _DelimRun:
    """One run of ``*`` or ``_`` plus the flanking rules that decide its role.

    ``lo``/``hi`` are the slice of the run still standing after it has been
    matched against an opener or a closer; the rest of the run is markup and
    gets dropped when the text is rebuilt.
    """

    __slots__ = ("can_close", "can_open", "char", "end", "hi", "lo", "orig_length", "start")

    def __init__(self, char: str, start: int, end: int, can_open: bool, can_close: bool) -> None:
        self.char = char
        self.start = start
        self.end = end
        self.lo = start
        self.hi = end
        self.orig_length = end - start
        self.can_open = can_open
        self.can_close = can_close


def _is_punct(ch: str) -> bool:
    """CommonMark "punctuation": ASCII punctuation or any Unicode P*/S* character."""
    return ch in _ASCII_PUNCT or unicodedata.category(ch)[0] in ("P", "S")


def _scan_delim_runs(text: str) -> list[_DelimRun]:
    runs: list[_DelimRun] = []
    i, n = 0, len(text)
    while i < n:
        char = text[i]
        if char not in "*_":
            i += 1
            continue
        j = i
        while j < n and text[j] == char:
            j += 1
        before = text[i - 1] if i else ""
        after = text[j] if j < n else ""
        before_space = not before or before.isspace()
        after_space = not after or after.isspace()
        before_punct = bool(before) and _is_punct(before)
        after_punct = bool(after) and _is_punct(after)
        left = not after_space and (not after_punct or before_space or before_punct)
        right = not before_space and (not before_punct or after_space or after_punct)
        if char == "_":
            # Intraword `_` is never emphasis. Without this guard the old
            # `(?<!_)_([^_]+)_(?!_)` regex ate the underscores out of every
            # snake_case identifier: `L_O_L` rendered as `LOL`, and
            # `snake_case_name` welded into `snakecasename`.
            can_open = left and (not right or before_punct)
            can_close = right and (not left or after_punct)
        else:
            can_open, can_close = left, right
        runs.append(_DelimRun(char, i, j, can_open, can_close))
        i = j
    return runs


def _blocks_by_rule_of_three(opener: _DelimRun, closer: _DelimRun) -> bool:
    if not (closer.can_open or opener.can_close):
        return False
    if closer.orig_length % 3 == 0:
        return False
    return (opener.orig_length + closer.orig_length) % 3 == 0


def _strip_emphasis(text: str) -> str:
    """Drop emphasis delimiters, keeping the words between them.

    Delimiter runs are matched with the CommonMark flanking rules rather than
    by regex, so ``**bold**``, ``_italic_`` and ``__bold__`` still collapse while
    identifiers that merely contain the character survive intact.
    """
    runs = _scan_delim_runs(text)
    if not runs:
        return text

    openers: list[int] = []
    closer_idx = 0
    while closer_idx < len(runs):
        closer = runs[closer_idx]
        if not closer.can_close:
            if closer.can_open:
                openers.append(closer_idx)
            closer_idx += 1
            continue

        found = None
        for pos in range(len(openers) - 1, -1, -1):
            candidate = runs[openers[pos]]
            if candidate.char != closer.char or not candidate.can_open:
                continue
            if _blocks_by_rule_of_three(candidate, closer):
                continue
            found = pos
            break

        if found is None:
            if closer.can_open:
                openers.append(closer_idx)
            closer_idx += 1
            continue

        opener_idx = openers.pop(found)
        opener = runs[opener_idx]
        used = 2 if opener.hi - opener.lo >= 2 and closer.hi - closer.lo >= 2 else 1
        opener.hi -= used  # an opener spends its delimiters from the right
        closer.lo += used  # a closer spends its delimiters from the left
        # Whatever sat between the matched pair is now inside the emphasis and
        # can never pair outwards again: in `_a*b1_*` the inner `*` is literal,
        # so it must not later close the trailing one.
        openers = [i for i in openers if not opener.start < runs[i].start < closer.start]
        if opener.hi > opener.lo:
            # `***x***` spends two, then one: a part-used opener keeps its
            # place in the queue so the leftovers can still pair up.
            openers.insert(found, opener_idx)
        if closer.lo >= closer.hi:
            closer_idx += 1

    out: list[str] = []
    pos = 0
    for run in runs:
        out.append(text[pos : run.start])
        if run.lo < run.hi:
            out.append(text[run.lo : run.hi])
        pos = run.end
    out.append(text[pos:])
    return "".join(out)


def strip_markdown_for_collapsed_text(text: str) -> str:
    """Flatten one line of markdown down to the words it shows.

    A code span is unwrapped rather than rewritten: `_a_` inside backticks is
    something the writer is *describing*, so only the ticks come off. Matching
    the ticks first also keeps a lone fence line (``` on its own) intact,
    because it is not a code span and there are no ticks to remove.
    """
    if "*" not in text and "_" not in text and "`" not in text:
        return text
    parts: list[str] = []
    pos = 0
    for match in _CODE_SPAN_RE.finditer(text):
        parts.append(_strip_emphasis(text[pos : match.start()]))
        parts.append(match.group(2))
        pos = match.end()
    parts.append(_strip_emphasis(text[pos:]))
    return "".join(parts)


def markdown_render_width() -> int:
    term_width = shutil.get_terminal_size().columns
    return max(40, term_width - 4)


def format_markdown(text: str, width: int | None = None) -> Text:
    text = preprocess_latex(text)
    text = _normalize_inline_html(text)
    text = _normalize_tables(text)
    sanitized = _strip_inline_code_ticks_in_headings(text)
    md = CustomMarkdown(sanitized)
    if width is None:
        width = markdown_render_width()
    console = Console(force_terminal=True, no_color=False, theme=MARKDOWN_THEME, width=width)
    with console.capture() as capture:
        console.print(md)
    rendered = capture.get()
    return Text.from_ansi(rendered.rstrip("\n"))


_FENCE_OPEN = re.compile(r"^\s{0,3}(`{3,}|~{3,})(.*)$")


def _iter_outside_fences(text: str):
    """Yield ``(line, in_fence)`` for each line, tracking fences by length.

    Two bugs lived in the old "does this line start with three backticks" check.
    A line that merely begins with a fence while talking about fences toggled
    the state, so the rest of the reply was never stable and the whole block
    cache was defeated. And inside a four-backtick fence the inner
    three-backtick fence closed it early, so a blank line inside the outer
    fence read as a block boundary and the stream split mid-block, collapsing
    soft breaks until finalisation.

    A fence closes only on a run at least as long as the one that opened it.
    """
    fence_len = 0
    fence_char = ""
    for line in text.splitlines(keepends=True):
        stripped = line.rstrip("\n")
        m = _FENCE_OPEN.match(stripped)
        if fence_len:
            closes = (
                m
                and stripped.lstrip().startswith(fence_char * fence_len)
                and fence_char not in m.group(2)
            )
            if closes:
                fence_len = 0
                fence_char = ""
            yield line, True
            continue
        if m:
            marker = m.group(1)
            fence_char = marker[0]
            fence_len = len(marker)
            if fence_char in m.group(2):
                fence_len = 0
                fence_char = ""
                yield line, False
                continue
            yield line, True
            continue
        yield line, False


def find_stable_block_boundary(text: str) -> int:
    """Offset just after the last blank line outside a code fence, 0 if none.

    Everything before the boundary is a closed run of top-level markdown blocks.
    Content streamed after it can no longer change how that text renders.
    """
    boundary = 0
    offset = 0
    for line, in_fence in _iter_outside_fences(text):
        if not in_fence and not line.strip():
            boundary = offset + len(line)
        offset += len(line)
    return boundary


def split_open_fence(text: str) -> tuple[int, int, str, bool]:
    """Locate the cacheable part of a fenced code block at the head of *text*.

    Returns ``(body_len, stable, marker, closed)``:

    - ``body_len`` offset just after the fence's opening line, ``0`` when
      *text* does not open into a fence.
    - ``stable``  offset just after the last line that can be rendered once and
      kept: the opener plus every newline-terminated line after it, extended
      past the closing line when one has already arrived.
    - ``marker``  the opening line without its newline, so a fragment can be
      re-wrapped with it and still render as a code block.
    - ``closed``  True when ``stable`` includes the fence's closing line.

    A code fence is the one streaming construct that grows without ever
    producing a blank line, so :func:`find_stable_block_boundary` leaves it in
    the open block forever and every refresh re-renders the entire file. Lines
    inside a fence are inert -- nothing arriving later can change how an
    already-terminated one renders -- which makes them safe to commit one at a
    time and turns a quadratic re-render into a linear pass.

    Only newline-terminated lines are stable. A partially written line is the
    live tail by definition, and a half-written opener would otherwise be
    cached as if it were complete.
    """
    fence_len = 0
    fence_char = ""
    marker = ""
    body_len = 0
    stable = 0
    offset = 0
    for raw in text.splitlines(keepends=True):
        if not raw.endswith("\n"):
            break  # still streaming into this line
        line = raw[:-1]
        m = _FENCE_OPEN.match(line)
        if fence_len:
            if (
                m
                and line.lstrip().startswith(fence_char * fence_len)
                and fence_char not in m.group(2)
            ):
                fence_len = 0
                fence_char = ""
                end = offset + len(raw)
                return body_len, end, marker, True
            offset += len(raw)
            stable = offset
            continue
        if m:
            mark = m.group(1)
            char = mark[0]
            if char in m.group(2):
                # A fence character in the info string makes this a plain line.
                offset += len(raw)
                continue
            fence_char = char
            fence_len = len(mark)
            marker = line
            offset += len(raw)
            body_len = offset
            stable = offset
            continue
        break  # ordinary prose: the fence never opened
    return body_len, stable, marker, False


def format_markdown_block(text: str, width: int) -> Text:
    """Render a markdown fragment with blank edge lines stripped.

    A fragment can render with stray blank edge lines (a lone list starts with one).
    Stripping them lets cached blocks be joined with a single blank line, the same
    spacing Rich puts between top-level elements in a full render.
    """
    lines = list(format_markdown(text, width).split("\n"))
    while lines and not lines[0].plain.strip():
        lines.pop(0)
    while lines and not lines[-1].plain.strip():
        lines.pop()
    return Text("\n").join(lines)


_BASH_TOKEN_RE = re.compile(
    r"(?P<space>\s+)"
    r"|(?P<op>\|\||&&|;;|[|;&()<>])"
    r"|(?P<sq>'[^']*')"
    r'|(?P<dq>"(?:\\.|[^"\\])*")'
    r"|(?P<word>[^\s|;&()<>]+)"
)


def _format_bash_command_tokens(command: str) -> Text:
    """Small shell highlighter for common command headers.

    Pygments mostly highlights shell syntax, not ordinary argv words, so a
    command like `git status --short && git log` can otherwise appear plain.
    Use a compact Catppuccin-ish palette similar to Codex's default command
    highlighting instead of dimming argv text.
    """
    syntax = config.ui.colors.syntax_colors
    command_style = syntax.command
    arg_style = syntax.arg
    option_style = syntax.option
    operator_style = syntax.operator
    string_style = syntax.string
    variable_style = syntax.variable

    text = Text()
    expect_command = True

    for match in _BASH_TOKEN_RE.finditer(command):
        token = match.group(0)
        kind = match.lastgroup

        if kind == "space":
            text.append(token)
            continue

        if kind == "op":
            text.append(token, style=operator_style)
            expect_command = token in {"|", "||", "&&", ";", ";;", "("}
            continue

        if kind in {"sq", "dq"}:
            text.append(token, style=string_style)
            expect_command = False
            continue

        if token.startswith("$"):
            text.append(token, style=variable_style)
            expect_command = False
        elif expect_command:
            text.append(token, style=command_style)
            expect_command = False
        elif token.startswith("-"):
            text.append(token, style=option_style)
        else:
            text.append(token, style=arg_style)

    return text


_PYTHEME_KEY: str | None = None
_PYTHEME: Any = None


def get_python_theme() -> Any:
    """A Pygments style for Python source, built from the active vtx theme.

    The one Rich theme that ships with no dependencies (``ansi_dark``) paints the
    same colours regardless of which theme the user picked, so a script rendered
    in it was the one block in the log that ignored ``/themes``. Mapping the
    theme's own syntax palette onto the Python tokens keeps a notebook cell on
    palette; cached per theme id so a switch repaints instead of keeping the
    previous theme's class.
    """
    global _PYTHEME_KEY, _PYTHEME

    from pygments.style import Style as PygmentsStyle
    from pygments.token import Token

    key = config.ui.theme
    if _PYTHEME is not None and key == _PYTHEME_KEY:
        return _PYTHEME

    colors = config.ui.colors
    syntax = colors.syntax_colors

    styles: dict[Any, str] = {
        # Pygments' metaclass resolves unlisted tokens against the root, so this
        # is what keeps whitespace and punctuation the theme's own foreground
        # rather than black.
        Token: colors.fg,
        Token.Comment: f"{colors.dim} italic",
        Token.Comment.Preproc: f"{syntax.option} italic",
        Token.Keyword: f"bold {syntax.option}",
        Token.Keyword.Constant: syntax.variable,
        Token.Keyword.Namespace: f"bold {syntax.option}",
        Token.Operator: syntax.operator,
        Token.Operator.Word: f"bold {syntax.option}",
        Token.Punctuation: colors.muted,
        Token.Name: colors.fg,
        Token.Name.Builtin: f"bold {syntax.command}",
        Token.Name.Builtin.Pseudo: colors.dim,
        Token.Name.Class: f"bold {syntax.command}",
        Token.Name.Function: f"bold {syntax.command}",
        Token.Name.Decorator: f"bold {syntax.variable}",
        Token.Name.Exception: f"bold {colors.error}",
        Token.Name.Attribute: syntax.variable,
        Token.Name.Tag: f"bold {syntax.option}",
        Token.Literal.String: syntax.string,
        Token.Literal.String.Doc: f"{syntax.string} italic",
        Token.Literal.String.Escape: f"bold {syntax.variable}",
        Token.Literal.Number: syntax.variable,
        Token.Generic.Heading: f"bold {colors.title}",
        Token.Generic.Subheading: f"bold {syntax.option}",
        Token.Error: colors.error,
    }

    # Built with ``type`` rather than a subclass body because pygments resolves
    # ``styles`` in its metaclass at class-creation time. Three-argument ``type``
    # still picks up the base's metaclass, so the token table gets built.
    _PYTHEME = type(
        "VtxPythonStyle", (PygmentsStyle,), {"background_color": None, "styles": styles}
    )
    _PYTHEME_KEY = key
    return _PYTHEME


def format_python_source(code: str, width: int | None = None) -> Text:
    """Syntax-highlight a Python fragment at ``width`` columns.

    Returns plain ``Text`` (not a renderable) so the caller can indent it under
    a prompt, which is what a notebook cell needs and what a ``Syntax`` object
    alone cannot do.
    """
    if width is None:
        width = markdown_render_width()
    width = max(24, width)

    source = code.rstrip("\n")
    if not source.strip():
        return Text()

    syntax = Syntax(source, "python", theme=get_python_theme(), word_wrap=True, padding=0)
    console = Console(force_terminal=True, no_color=False, color_system="truecolor", width=width)
    with console.capture() as capture:
        console.print(syntax)
    return Text.from_ansi(capture.get().rstrip("\n"))


def format_bash_command(text: str, width: int | None = None) -> Text:
    """Syntax-highlight a bash command for compact tool headers."""
    if width is None:
        term_width = shutil.get_terminal_size().columns
        width = max(40, term_width - 4)

    prompt = ""
    command = text
    if text.startswith("$ "):
        prompt = "$ "
        command = text[2:]

    highlighted = _format_bash_command_tokens(command)

    if not prompt:
        return highlighted

    result = Text(prompt, style=config.ui.colors.dim)
    result.append_text(highlighted)
    return result


def format_tokens(n: int) -> str:
    """Compact token count on 1024-based units: ``940``, ``33.8k``, ``1.2M``.

    1024 rather than 1000 because that is how tokenizers actually bill, so a
    "1M context" model is really 1048576 and reading `976k` against it is the
    honest number. Trailing ``.0`` is dropped so round magnitudes stay two or
    three characters wide -- the info bar has a fixed width budget.
    """
    for cutoff, unit in ((1 << 40, "T"), (1 << 30, "B"), (1 << 20, "M"), (1 << 10, "k")):
        if n >= cutoff:
            return f"{n / cutoff:.1f}".replace(".0", "") + unit
    return str(n)
