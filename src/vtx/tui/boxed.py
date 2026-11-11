"""Boxed tool rendering with an editor-grade body — Python/Textual port.

Port of the ``shared/box.ts`` chrome: rounded borders with embedded
labels, dim frame, boxed body lines, and metrics footers. On top of that the
body renders like an editor tab: a language chip and state glyph in the
header, a line-number gutter, and syntax-highlighted code/diff rows. Pure
functions over Rich ``Text`` so callers can compose them into any widget.
"""

from __future__ import annotations

import re
import time
from typing import Any

from rich.style import Style
from rich.text import Text

from vtx.core.config import config
from vtx.tui.diff_display import DIFF_BG_PAD_MARKER

BOX_H = "─"
BOX_V = "│"
CORNER_TL = "╭"
CORNER_TR = "╮"
CORNER_BL = "╰"
CORNER_BR = "╯"

MIN_BOX_WIDTH = 30
#: Skip the Pygments pass on diffs too large to colour cheaply (expanded view).
MAX_HIGHLIGHT_CHARS = 200_000


def box_width(width: int | None) -> int:
    """Box width for a widget ``width``: the widget's full width, floored.

    The box spans the chat column edge to edge (the widget's 1-cell margins
    keep it off the terminal bezel), so a wide terminal gets a wide box and
    long lines soft-wrap instead of being clipped at an arbitrary cap.
    """
    w = width or 80
    return max(MIN_BOX_WIDTH, w)


def _colors():
    return config.ui.colors


def _fit(text: str, budget: int) -> str:
    """Clip ``text`` to ``budget`` display cells with an ellipsis."""
    if budget <= 0:
        return ""
    clipped = Text(text)
    if clipped.cell_len <= budget:
        return text
    clipped.truncate(max(1, budget), overflow="ellipsis", pad=False)
    return clipped.plain


def _fit_text(text: Text, budget: int) -> Text:
    """Clip a styled ``Text`` to ``budget`` display cells, keeping its spans.

    Unlike :func:`_fit`, this works on an already-highlighted ``Text`` (e.g. a
    syntax-coloured bash command) so a truncated header keeps its colours
    instead of collapsing to a single dim run.
    """
    if budget <= 0:
        return Text("")
    clipped = text.copy()
    if clipped.cell_len <= budget:
        return clipped
    clipped.truncate(max(1, budget), overflow="ellipsis", pad=False)
    return clipped


def visible_len(text: str) -> int:
    """Length of ``text`` ignoring Rich markup tags."""
    out = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text):
            out.append(text[i + 1])
            i += 2
            continue
        if ch == "[":
            end = text.find("]", i)
            if end != -1:
                i = end + 1
                continue
        out.append(ch)
        i += 1
    return len(out)


def strip_markup(text: str) -> str:
    """Plain text of a possibly-marked-up string."""
    try:
        return Text.from_markup(text).plain
    except Exception:
        return text


def top_border(
    icon: str,
    name: str,
    detail: str | Text,
    width: int,
    *,
    running: bool = False,
    failed: bool = False,
    status: str = "",
    badge: str = "",
) -> Text:
    """``╭─ {icon} {Name} {detail} PY ───── {status} ─╮``.

    ``badge`` is a short language chip (``PY``, ``TS``) and ``status`` is the
    state glyph pinned top-right, so a finished call reads its outcome from
    the header the way an editor tab does.
    """
    colors = _colors()
    w = box_width(width)
    dim = str(colors.dim)

    name_style = colors.failed if failed else (colors.running if running else colors.muted)
    icon_style = str(colors.accent) if not (running or failed) else str(name_style)
    name_style = f"bold {name_style}"

    prefix = f"{CORNER_TL}{BOX_H} "

    right = Text()
    if status:
        status_color = colors.failed if failed else (colors.running if running else colors.success)
        right.append(f" {status} ", style=Style(color=str(status_color), bold=True))
        right.append(CORNER_TR, style=dim)
    else:
        right.append(f" {CORNER_TR}", style=dim)

    head = Text()
    head.append(prefix, style=dim)
    head.append(f"{icon} ", style=str(icon_style))
    head.append(name, style=str(name_style))

    badge_extra = (1 + Text(f" {badge} ").cell_len) if badge else 0
    detail_budget = w - head.cell_len - right.cell_len - badge_extra - (2 if badge else 3)
    if detail:
        if isinstance(detail, Text):
            head.append(" ")
            head.append_text(_fit_text(detail, detail_budget))
        elif detail_txt := _fit(detail, detail_budget):
            head.append(" ")
            head.append(detail_txt, style=dim)
    if badge:
        head.append(" ")
        head.append(
            f" {badge} ", style=Style(color=str(colors.muted), bgcolor=str(colors.panel_alt))
        )

    if not badge:
        head.append(" ", style=dim)
    fill = max(1, w - head.cell_len - right.cell_len)
    head.append(BOX_H * fill, style=dim)
    head.append_text(right)
    return head


def bottom_border(footer: str, width: int, *, hint: str = "", failed: bool = False) -> Text:
    """``╰─ {footer} ──────────── {hint} ─╯`` — footer left, hint right."""
    w = box_width(width)
    prefix = f"{CORNER_BL}{BOX_H} "
    suffix = f" {CORNER_BR}"
    dim = str(_colors().dim)

    try:
        footer_text = Text.from_markup(footer)
    except Exception:
        footer_text = Text(strip_markup(footer), style="dim")
    try:
        hint_text = Text.from_markup(hint) if hint else Text()
    except Exception:
        hint_text = Text(hint, style="dim")

    def fill_for(footer_t: Text, hint_t: Text) -> int:
        used = len(prefix) + footer_t.cell_len + 1 + len(suffix)
        if hint_t.cell_len:
            used += 1 + hint_t.cell_len
        return w - used

    fill = fill_for(footer_text, hint_text)
    if fill < 1 and hint_text.cell_len:
        hint_text = Text()
        fill = fill_for(footer_text, hint_text)
    if fill < 1:
        footer_text.truncate(max(4, footer_text.cell_len + fill), overflow="ellipsis", pad=False)
        fill = fill_for(footer_text, Text())

    body = Text()
    body.append(prefix, style=dim)
    body.append_text(footer_text)
    body.append(" ", style=dim)
    body.append(BOX_H * max(1, fill), style=dim)
    if hint_text.cell_len:
        body.append(" ", style=dim)
        body.append_text(hint_text)
    body.append(suffix, style=dim)
    return body


def box_line(content: Text | str, width: int, *, failed: bool = False) -> Text:
    """``│ {content padded} │`` — one boxed body line."""
    w = box_width(width)
    inner = w - 4  # two corners-as-sides + two padding spaces each side
    side = str(_colors().failed) if failed else "dim"
    out = Text()
    out.append(f"{BOX_V} ", style=side)
    if isinstance(content, str):
        # Hold the str in its own variable: assigning the parsed Text into
        # ``content`` inside ``try`` invalidates the ``isinstance`` narrowing,
        # so a bare ``Text(content)`` in the except branch would be flagged as
        # passing ``str | Text`` to ``Text.__init__``.
        raw: str = content
        try:
            content = Text.from_markup(raw)
        except Exception:
            content = Text(raw)
    if content.cell_len > inner:
        content = content.copy()
        content.truncate(max(1, inner), overflow="ellipsis", pad=False)
    pad = inner - content.cell_len
    out.append_text(content)
    if pad > 0:
        out.append(" " * pad)
    out.append(f" {BOX_V}", style=side)
    return out


def box_blank(width: int, *, failed: bool = False) -> Text:
    w = box_width(width)
    side = str(_colors().failed) if failed else "dim"
    return Text(f"{BOX_V}{' ' * (w - 2)}{BOX_V}", style=side)


def _soft_wrap_segments(line: Text, width: int) -> list[Text]:
    """Split one styled line into box-width ``width`` segments.

    Returns ``[line]`` when it already fits. Longer lines are soft-wrapped at
    word boundaries so a wide diff/code line reads like an editor's wrapped
    view (VS Code) instead of being clipped at the box edge; a single word
    wider than the box is hard-broken so nothing runs past the gutter. Styles
    -- including per-row diff backgrounds -- carry across the break.
    """
    if not line.plain or line.cell_len <= width:
        return [line]
    try:
        from rich.console import Console

        console = Console(width=width, force_terminal=True)
        wrapped = list(line.wrap(console, width=width))
        return wrapped or [line]
    except Exception:
        return [line]


#: Editor gutter prefixes emitted by ``highlighted_numbered_lines``
#: (``282 │ ``) and ``highlight_diff`` (``+ 1 │ ``, ``  2 │ ``).
_GUTTER_RE = re.compile(r"^[+\- ]*\d+\s*│\s")


def _wrap_body_line(line: Text, inner: int) -> list[Text]:
    """Soft-wrap one body line at ``inner`` cells.

    A line opening with an editor gutter keeps that gutter on the first
    segment and indents every continuation by the same width, so wrapped code
    stays aligned under the code column the way a VS Code soft wrap does —
    instead of jumping back to the box's left padding.
    """
    if not line.plain or line.cell_len <= inner:
        return [line]
    match = _GUTTER_RE.match(line.plain)
    if match is None:
        return _soft_wrap_segments(line, inner)
    gutter = match.group(0)
    gutter_len = len(gutter)  # digits, spaces, +, - and │ are all one cell
    if gutter_len >= inner - 8:
        return _soft_wrap_segments(line, inner)
    parts = line.divide([gutter_len])
    gutter_text = parts[0]
    content = parts[1] if len(parts) > 1 else Text()
    rows: list[Text] = []
    for index, segment in enumerate(_soft_wrap_segments(content, inner - gutter_len)):
        row = Text()
        if index == 0:
            row.append_text(gutter_text)
        else:
            row.append_text(Text(" " * gutter_len, style=content.style))
        row.append_text(segment)
        rows.append(row)
    return rows


def wrap_boxed(text: Text, width: int, *, failed: bool = False) -> Text:
    """Box every line of ``text``: ``│ line │`` rows joined by newlines.

    Lines wider than the box's inner width are soft-wrapped across several
    rows (an editor-style diff/code wrap) rather than truncated; guttered
    lines keep their code column aligned on the continuation rows.
    """
    w = box_width(width)
    inner = w - 4
    out = Text()
    rows: list[Text] = []
    for line in text.split("\n"):
        rows.extend(_wrap_body_line(line, inner))
    for i, row in enumerate(rows):
        out.append_text(box_line(row, width, failed=failed))
        if i < len(rows) - 1:
            out.append("\n")
    return out


_LANGUAGE_BADGES = {
    "py": "PY",
    "pyi": "PY",
    "pyw": "PY",
    "json": "JSON",
    "sh": "SH",
    "bash": "SH",
    "zsh": "SH",
    "fish": "SH",
    "ts": "TS",
    "tsx": "TSX",
    "js": "JS",
    "jsx": "JSX",
    "mjs": "JS",
    "cjs": "JS",
    "md": "MD",
    "rs": "RS",
    "go": "GO",
    "toml": "TOML",
    "yaml": "YAML",
    "yml": "YAML",
    "css": "CSS",
    "scss": "CSS",
    "html": "HTML",
    "xml": "XML",
    "sql": "SQL",
    "c": "C",
    "h": "C",
    "cpp": "C++",
    "hpp": "C++",
    "java": "JAVA",
    "kt": "KT",
    "rb": "RB",
    "lua": "LUA",
    "php": "PHP",
    "swift": "SWIFT",
    "vue": "VUE",
}


def _extension(path: str) -> str:
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def lexer_for_path(path: str | None) -> str | None:
    """Pygments lexer name for a ``path``, or ``None`` when Pygments cannot
    resolve one. Delegates to Pygments' own filename/extension database instead
    of a hand-kept table, so extension-less names (``Dockerfile``, ``Makefile``,
    ``Gemfile``) and every supported extension highlight instead of silently
    coming back plain.
    """
    if not path:
        return None
    from pygments.lexers import get_lexer_for_filename

    try:
        lexer = get_lexer_for_filename(path)
    except Exception:
        return None
    aliases = getattr(lexer, "aliases", None) or []
    if aliases:
        return aliases[0]
    return getattr(lexer, "name", None) or None


def language_badge(path: str | None) -> str:
    """Short uppercase chip for the header (``PY``, ``TS``, ``JSON``)."""
    if not path:
        return ""
    return _LANGUAGE_BADGES.get(_extension(path), "")


def _highlight_fragment(fragment: str, path: str = "", *, lexer: str | None = None) -> Text:
    """Syntax-highlight ``fragment`` without line wrapping.

    ``format_python_source`` wraps at the render width, which splits long
    lines into gutter-less continuation rows inside the numbered preview —
    so this goes through Pygments directly with ``word_wrap=False``.
    """
    if lexer is None:
        lexer = lexer_for_path(path)
    if lexer is None or not fragment.strip():
        return Text(fragment)
    try:
        from rich.console import Console
        from rich.syntax import Syntax

        from vtx.tui.formatting import get_python_theme

        syntax = Syntax(
            fragment.rstrip("\n"), lexer, theme=get_python_theme(), word_wrap=False, padding=0
        )
        console = Console(force_terminal=True, no_color=False, color_system="truecolor", width=500)
        with console.capture() as capture:
            console.print(syntax)
        return Text.from_ansi(capture.get().rstrip("\n"))
    except Exception:
        return Text(fragment)


def highlighted_numbered_lines(
    content: str, path: str, *, max_lines: int = 15, start_line: int = 1
) -> Text:
    """Editor-style ``12 │ code`` preview of ``content``'s tail.

    The fragment is highlighted as one block (Pygments tolerates incomplete
    syntax), then split so each line keeps its styles under a dim gutter.
    Bounded: only the last ``max_lines`` lines are processed. ``start_line``
    offsets the gutter when ``content`` is a slice of a larger file (read
    with ``offset``).
    """
    normalized = content.replace("\t", "    ").replace("\r", "")
    if not normalized:
        return Text()
    all_lines = normalized.split("\n")
    total = len(all_lines)
    start = max(0, total - max_lines)
    tail = all_lines[start:]
    gutter = max(1, len(str(start_line + total - 1)))
    colors = _colors()

    body = _highlight_fragment("\n".join(tail), path)
    body_lines = body.split("\n")

    rows: list[Text] = []
    if start > 0:
        rows.append(Text(f"{' ' * gutter} ⋮ {start_line + start} earlier lines", style="dim"))
    for i, line in enumerate(body_lines):
        num = str(start_line + start + i).rjust(gutter)
        row = Text()
        row.append(f"{num} ", style="dim")
        row.append(f"{BOX_V} ", style=str(colors.border))
        row.append_text(line)
        rows.append(row)

    out = Text()
    for i, row in enumerate(rows):
        out.append_text(row)
        if i < len(rows) - 1:
            out.append("\n")
    return out


_DIFF_LINE_RE = re.compile(r"^(?P<num>\s*\d+)  (?P<code>.*)$")


def _color_hex(color: Any) -> str | None:
    """Hex string of a Rich ``Color`` (``None`` for unset/unresolvable values)."""
    if color is None:
        return None
    try:
        return str(color.get_truecolor().hex)
    except Exception:
        return None


def _line_paint(line: Text) -> tuple[str | None, str | None]:
    """Background and foreground hex used by a formatted diff row."""
    bg: str | None = None
    fg: str | None = None
    for span in line.spans:
        style = span.style
        if isinstance(style, str):
            try:
                style = Style.parse(style)
            except Exception:
                continue
        if not isinstance(style, Style):
            continue
        if bg is None and style.bgcolor is not None:
            bg = _color_hex(style.bgcolor)
        if fg is None and style.color is not None:
            fg = _color_hex(style.color)
    return bg, fg


def highlight_diff(text: Text, path: str | None) -> Text:
    """Re-colour the code inside a formatted diff, keeping its line backgrounds.

    ``format_diff_display`` paints a whole added/removed line one hue, which
    throws away the syntax the reader is looking at. This restores token
    colours per line while the ``+``/``-`` gutter keeps the add/remove cue,
    so a diff reads like an editor's instead of a coloured transcript.
    """
    lexer = lexer_for_path(path)
    if lexer is None or len(text.plain) > MAX_HIGHLIGHT_CHARS:
        return text

    colors = _colors()
    added = str(colors.diff_added).lower()
    removed = str(colors.diff_removed).lower()
    border = str(colors.border)

    lines = text.split("\n")
    parsed: list[tuple[str, str, str, str | None] | None] = []
    for line in lines:
        bg, fg = _line_paint(line)
        plain = line.plain.replace(DIFF_BG_PAD_MARKER, "")
        match = _DIFF_LINE_RE.match(plain)
        if bg is None or match is None or not match.group("code").strip():
            parsed.append(None)
            continue
        code = match.group("code")
        if code.startswith(" "):
            code = code[1:]
        parsed.append((match.group("num"), code, bg, fg))

    code_block = "\n".join(entry[1] if entry else "" for entry in parsed)
    highlighted_lines = _highlight_fragment(code_block, path or "", lexer=lexer).split("\n")
    while len(highlighted_lines) < len(parsed):
        highlighted_lines.append(Text())

    out = Text()
    for index, (line, entry) in enumerate(zip(lines, parsed, strict=False)):
        if index:
            out.append("\n")
        if entry is None:
            out.append_text(line)
            continue
        num, _code, bg, fg = entry
        fg_hex = (fg or "").lower()
        if fg_hex == added:
            sign = "+"
        elif fg_hex == removed:
            sign = "-"
        else:
            sign = " "
        gutter_style = Style(color=fg or str(colors.dim), bold=True)
        row = Text(style=Style(bgcolor=bg))
        row.append(f"{sign}{num} ", style=gutter_style)
        row.append(f"{BOX_V} ", style=Style(color=border))
        row.append_text(highlighted_lines[index])
        out.append_text(row)
        out.append(DIFF_BG_PAD_MARKER, style=Style(bgcolor=bg))
    return out


def numbered_lines(content: str, *, max_lines: int = 15) -> list[str]:
    """``cat -n`` style numbered preview of file content (write tool)."""
    normalized = content.replace("\t", "    ").replace("\r", "")
    if not normalized:
        return []
    lines = normalized.split("\n")
    gutter = max(1, len(str(len(lines))))
    shown = lines[-max_lines:] if len(lines) > max_lines else lines
    out = []
    for i, line in enumerate(shown):
        num = str(len(lines) - len(shown) + i + 1).rjust(gutter)
        esc = line.replace("[", "\\[")
        out.append(f"[dim]{num} [/dim]{esc}")
    return out


def elapsed_footer(started: float, summary: str = "") -> str:
    """``0.4s · summary`` footer text for a settled tool call."""
    elapsed = time.monotonic() - started
    parts = [f"{elapsed:.1f}s"]
    if summary:
        parts.append(summary)
    return " · ".join(parts)


def running_footer(elapsed_s: float | None = None) -> str:
    """``◌ Running · 1.2s`` for the in-flight bottom border."""
    base = "[dim]◌ Running[/dim]"
    if elapsed_s is None:
        return base
    return f"{base}[dim] · {elapsed_s:.1f}s[/dim]"


def format_words_or_lines(text: str) -> str:
    """Compact ``~N words`` / ``N lines`` metric for footers."""
    lines = text.count("\n") + 1 if text else 0
    words = len(text.split())
    if words >= lines * 4:
        return f"~{words} words"
    return f"{lines} lines"
