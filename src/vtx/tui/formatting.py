import re
import shutil
from typing import ClassVar

from rich import box
from rich._loop import loop_first
from rich.console import Console, ConsoleOptions, RenderResult
from rich.markdown import CodeBlock, Heading, ListElement, ListItem, Markdown, TableElement
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
                "markdown.hr": Style(color=colors.border),
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

        if self.body is not None:
            for row in self.body.rows:
                table.add_row(*[element.content for element in row.cells])

        yield table


class CustomMarkdown(Markdown):
    elements: ClassVar[dict] = {
        **Markdown.elements,
        "heading_open": LeftJustifiedHeading,
        "bullet_list_open": PlainListElement,
        "ordered_list_open": PlainListElement,
        "list_item_open": PlainListItem,
        "fence": PlainCodeBlock,
        "code_block": PlainCodeBlock,
        "table_open": VtxTableElement,
    }


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
    sanitized = _strip_inline_code_ticks_in_headings(text)
    md = CustomMarkdown(sanitized)
    if width is None:
        width = markdown_render_width()
    console = Console(force_terminal=True, no_color=False, theme=MARKDOWN_THEME, width=width)
    with console.capture() as capture:
        console.print(md)
    rendered = capture.get()
    return Text.from_ansi(rendered.rstrip("\n"))


def find_stable_block_boundary(text: str) -> int:
    """Offset just after the last blank line outside a code fence, 0 if none.

    Everything before the boundary is a closed run of top-level markdown blocks.
    Content streamed after it can no longer change how that text renders.
    """
    boundary = 0
    offset = 0
    in_fence = False
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith(("```", "~~~")):
            in_fence = not in_fence
        elif not stripped and not in_fence:
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
    if n >= 1_000_000:
        return f"{int(n / 1_000_000)}m"
    elif n >= 1_000:
        return f"{int(n / 1_000)}k"
    return str(n)
