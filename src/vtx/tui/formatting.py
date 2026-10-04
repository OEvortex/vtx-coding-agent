import re
import shutil
from typing import ClassVar

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
_BR_TAGS = frozenset({"<br>", "<br/>", "<br />"})


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


def strip_markdown_for_collapsed_text(text: str) -> str:
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"__([^_]+)__", r"\1", text)
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"\1", text)
    text = re.sub(r"(?<!_)_([^_]+)_(?!_)", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    return text


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
