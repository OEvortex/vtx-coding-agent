"""fx's markdown rendering: headings, lists, fences, quotes, rules, tables.

The output rows carry no transcript gutter; the block layer adds fx's
two-column gutter and the ``│ `` command rail.
"""

from __future__ import annotations

import re

from vx.tui import ansi
from vx.tui import theme as _theme

_FENCE_RE = re.compile(r"^(?P<indent>[ ]{0,3})(?P<marker>`{3,}|~{3,})(?P<info>.*)$")
_HEADING_RE = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.*?)(?:[ \t]+#+)?[ \t]*$")
_RULE_RE = re.compile(r"^ {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$")
_QUOTE_RE = re.compile(r"^ {0,3}>[ ]?(.*)$")
_BULLET_RE = re.compile(r"^([ \t]*)([-*+•])[ \t]+(.*)$")
_ORDERED_RE = re.compile(r"^([ \t]*)(\d{1,3})[.)][ \t]+(.*)$")
_TABLE_SEP_RE = re.compile(r"^[ \t]*\|?[ \t]*:?-{2,}:?[ \t]*(\|[ \t]*:?-{2,}:?[ \t]*)*\|?[ \t]*$")

_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_BOLD_RE = re.compile(r"\*\*(?!\s)(.+?)(?<!\s)\*\*")
_UNDER_BOLD_RE = re.compile(r"(?<![\w_])__(?!\s)(.+?)(?<!\s)__(?![\w_])")
_ITALIC_RE = re.compile(r"(?<![*\w])\*(?!\s)(.+?)(?<!\s)\*(?![*\w])")
_UNDER_ITALIC_RE = re.compile(r"(?<![\w_])_(?!\s)(.+?)(?<!\s)_(?![\w_])")
_STRIKE_RE = re.compile(r"~~(?!\s)(.+?)(?<!\s)~~")
_LINK_RE = re.compile(r"\[([^\]\n]+)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_AUTOLINK_RE = re.compile(r"(?<![\w(\[])(https?://[^\s<>()\[\]]+)")


def _inline(text: str) -> str:
    """Inline markdown to ANSI, fx styles (white code, bold, italic, links).

    A single left-to-right scan, so generated escapes are never re-matched by
    a later rule (which is what makes naive repeated substitution mangle SGR).
    """
    t = _theme.current()
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\\" and i + 1 < n:
            out.append(text[i + 1])
            i += 2
            continue
        if ch == "`":
            end = text.find("`", i + 1)
            if end > i + 1:
                out.append(f"{t.inline_code_open}{text[i + 1 : end]}{t.inline_code_close}")
                i = end + 1
                continue
        if ch == "[":
            close = text.find("]", i + 1)
            if close != -1 and text.startswith("(", close + 1):
                end = text.find(")", close + 2)
                if end != -1:
                    label = text[i + 1 : close]
                    out.append(
                        f"{t.link_style}{ansi.UNDERLINE}{label}{ansi.UNDERLINE_OFF}\x1b[39m"
                    )
                    i = end + 1
                    continue
        if text.startswith("**", i) or text.startswith("__", i):
            mark = text[i : i + 2]
            end = text.find(mark, i + 2)
            if end > i + 2:
                out.append(f"{ansi.BOLD}{_inline(text[i + 2 : end])}{ansi.DIM_OFF}")
                i = end + 2
                continue
        if text.startswith("~~", i):
            end = text.find("~~", i + 2)
            if end > i + 2:
                out.append(f"\x1b[9m{text[i + 2 : end]}\x1b[29m")
                i = end + 2
                continue
        if (
            (ch == "*" or ch == "_")
            and i + 1 < n
            and text[i + 1] != ch
            and (i == 0 or not text[i - 1].isalnum() or ch == "*")
        ):
            end = text.find(ch, i + 1)
            if end > i + 1 and not text[i + 1].isspace():
                out.append(f"{ansi.ITALIC}{_inline(text[i + 1 : end])}{ansi.ITALIC_OFF}")
                i = end + 1
                continue
        if ch in "h" and text.startswith("http", i) and (i == 0 or not text[i - 1].isalnum()):
            j = i
            while j < n and text[j] not in " \t\n<>()[]":
                j += 1
            url = text[i:j].rstrip(".,;:")
            if len(url) > 8:
                out.append(f"{t.link_style}{ansi.UNDERLINE}{url}{ansi.UNDERLINE_OFF}\x1b[39m")
                i += len(url)
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def _row(text: str, cols: int) -> str:
    return ansi.clip_ellipsis(text, cols) if ansi.width(text) > cols else text


def _bullet(indent: int, body: str, cols: int) -> list[str]:
    lead = " " * indent
    marker = f"{ansi.dim()}• {ansi.DIM_OFF}"
    prefix_w = indent + 2
    rows = ansi.wrap(body, max(1, cols - prefix_w))
    out = []
    for i, r in enumerate(rows):
        if i == 0:
            out.append(lead + marker + r)
        else:
            out.append(" " * prefix_w + r)
    return out


def _ordered(indent: int, number: str, body: str, cols: int) -> list[str]:
    lead = " " * indent
    marker_text = f"{number})"
    field = max(4, len(marker_text))
    pad = " " * (field - len(marker_text))
    marker = f"{pad}{ansi.dim()}{marker_text}{ansi.DIM_OFF}"
    prefix_w = indent + field + 1
    rows = ansi.wrap(body, max(1, cols - prefix_w))
    out = []
    for i, r in enumerate(rows):
        if i == 0:
            out.append(lead + marker + " " + r)
        else:
            out.append(" " * prefix_w + r)
    return out


def _quote_row(body: str, cols: int) -> list[str]:
    """fx draws a quote as a dim rail with the quoted row beside it."""
    rail = f"{ansi.dim()}│ {ansi.DIM_OFF}"
    inner_width = max(1, cols - 2)
    bm = _BULLET_RE.match(body)
    if bm:
        inner = _bullet(0, _inline(bm.group(3)), max(1, inner_width - 2))
    else:
        om = _ORDERED_RE.match(body)
        if om:
            inner = _ordered(0, om.group(2), _inline(om.group(3)), max(1, inner_width - 3))
        else:
            inner = ansi.wrap(_inline(body), inner_width)
    return [f"{rail}{r}" for r in inner]


def _rule(cols: int) -> str:
    return f"{ansi.dim()}{'─' * max(1, cols)}{ansi.DIM_OFF}"


def _render_table(rows: list[list[str]], cols: int) -> list[str]:
    t = _theme.current()
    if not rows:
        return []
    columns = max(len(r) for r in rows)
    cells = [[_inline(c.strip()) for c in r] + [""] * (columns - len(r)) for r in rows]
    widths = [0] * columns
    for r in cells:
        for i, c in enumerate(r):
            widths[i] = max(widths[i], ansi.width(c))
    total = sum(widths) + 3 * columns + 1
    if total > cols:
        # Shrink the widest column until the box fits.
        while total > cols and max(widths) > 3:
            widest = widths.index(max(widths))
            widths[widest] -= 1
            total -= 1
    top = "┌" + "┬".join("─" * (w + 2) for w in widths) + "┐"
    mid = "├" + "┼".join("─" * (w + 2) for w in widths) + "┤"
    bottom = "└" + "┴".join("─" * (w + 2) for w in widths) + "┘"
    out = [f"{ansi.dim()}{top}{ansi.DIM_OFF}"]
    for index, r in enumerate(cells):
        parts = []
        for i, c in enumerate(r):
            parts.append(" " + ansi.pad(c, widths[i]).rstrip() + " ")
        out.append(
            f"{t.divider_style}│{ansi.RESET}"
            + f"{t.divider_style}│{ansi.RESET}".join(parts)
            + f"{t.divider_style}│{ansi.RESET}"
        )
        if index == 0:
            out.append(f"{ansi.dim()}{mid}{ansi.DIM_OFF}")
    out.append(f"{ansi.dim()}{bottom}{ansi.DIM_OFF}")
    return out


def _split_row(line: str) -> list[str]:
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|"):
        body = body[:-1]
    return body.split("|")


def render(text: str, cols: int) -> list[str]:
    """Render markdown to styled rows (no gutter, no trailing blanks)."""
    rows: list[str] = []
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    i = 0
    n = len(lines)
    paragraph: list[str] = []
    fence_marker: str | None = None
    fence_rows: list[str] = []
    table_rows: list[str] = []

    def flush_paragraph() -> None:
        nonlocal paragraph
        if not paragraph:
            return
        joined = " ".join(x.strip() for x in paragraph)
        paragraph = []
        rows.extend(ansi.wrap(_inline(joined), cols) or [""])

    def flush_table() -> None:
        nonlocal table_rows
        if not table_rows:
            return
        body = [r for r in table_rows if not _TABLE_SEP_RE.match(r)]
        rows.extend(_render_table([_split_row(r) for r in body], cols))
        table_rows = []

    while i < n:
        line = lines[i]
        i += 1

        if fence_marker is not None:
            m = _FENCE_RE.match(line)
            if (
                m
                and m.group("marker")[0] == fence_marker[0]
                and len(m.group("marker")) >= len(fence_marker)
            ):
                fence_marker = None
                continue
            fence_rows.append(line)
            continue

        if line.strip() and _TABLE_SEP_RE.match(line) and table_rows:
            table_rows.append(line)
            continue
        if line.strip().startswith("|") and line.count("|") >= 2:
            flush_paragraph()
            table_rows.append(line)
            continue
        flush_table()

        m = _FENCE_RE.match(line)
        if m:
            flush_paragraph()
            fence_marker = m.group("marker")
            lang = m.group("info").strip().split(" ")[0] if m.group("info").strip() else ""
            fence_rows = [f"\x00lang:{lang}"] if lang else []
            # Collect the body in one go so the code block stays literal.
            while i < n:
                inner = lines[i]
                close = _FENCE_RE.match(inner)
                if (
                    close
                    and close.group("marker")[0] == fence_marker[0]
                    and len(close.group("marker")) >= len(fence_marker)
                ):
                    i += 1
                    break
                fence_rows.append(inner)
                i += 1
            fence_marker = None
            rows.extend(_render_code(fence_rows, cols))
            fence_rows = []
            continue

        if not line.strip():
            flush_paragraph()
            if rows and rows[-1] != "":
                rows.append("")
            continue

        hm = _HEADING_RE.match(line)
        if hm:
            flush_paragraph()
            level = len(hm.group(1))
            body = _inline(hm.group(2).strip())
            if level == 1:
                styled = f"{ansi.BOLD}{ansi.UNDERLINE}{body}{ansi.UNDERLINE_OFF}{ansi.DIM_OFF}"
            else:
                styled = f"{ansi.BOLD}{body}{ansi.DIM_OFF}"
            rows.extend(ansi.wrap(styled, cols))
            continue

        if _RULE_RE.match(line):
            flush_paragraph()
            rows.append(_rule(cols))
            continue

        qm = _QUOTE_RE.match(line)
        if qm:
            flush_paragraph()
            rows.extend(_quote_row(qm.group(1), cols))
            continue

        bm = _BULLET_RE.match(line)
        if bm and (len(bm.group(1)) < 4 or True):
            flush_paragraph()
            indent = min(len(bm.group(1)), 8)
            rows.extend(_bullet(indent, _inline(bm.group(3)), cols))
            continue

        om = _ORDERED_RE.match(line)
        if om:
            flush_paragraph()
            indent = min(len(om.group(1)), 8)
            rows.extend(_ordered(indent, om.group(2), _inline(om.group(3)), cols))
            continue

        if line.startswith("    ") and not paragraph:
            # Indented code block.
            code_rows = [line[4:]]
            while i < n and (lines[i].startswith("    ") or not lines[i].strip()):
                code_rows.append(lines[i][4:] if lines[i].startswith("    ") else "")
                i += 1
            rows.extend(_render_code(code_rows, cols))
            continue

        paragraph.append(line)

    flush_paragraph()
    flush_table()

    while rows and rows[-1] == "":
        rows.pop()
    return rows


def _render_code(body: list[str], cols: int) -> list[str]:
    t = _theme.current()
    rail = f"{ansi.dim()}│ {ansi.DIM_OFF}"
    width = max(1, cols - 2)
    rows: list[str] = []
    for line in body:
        if line.startswith("\x00lang:"):
            continue
        for chunk in ansi.hard_wrap(line.expandtabs(4), width) if line else [""]:
            rows.append(rail + _highlight(chunk, t))
    return rows


_KEYWORDS = {
    "def",
    "class",
    "return",
    "if",
    "else",
    "elif",
    "for",
    "while",
    "try",
    "except",
    "finally",
    "with",
    "as",
    "import",
    "from",
    "pass",
    "raise",
    "yield",
    "await",
    "async",
    "lambda",
    "in",
    "is",
    "not",
    "and",
    "or",
    "None",
    "True",
    "False",
    "fn",
    "const",
    "var",
    "let",
    "pub",
    "struct",
    "enum",
    "impl",
    "match",
    "use",
    "mod",
    "func",
    "package",
    "type",
    "interface",
    "go",
    "defer",
    "range",
    "switch",
    "case",
    "break",
    "continue",
    "local",
    "then",
    "do",
    "done",
    "fi",
    "esac",
}
_COMMENT_PREFIXES = ("#", "//", "/*", "*", "--", ";;")


def _highlight(line: str, t) -> str:
    """A quiet fx-style highlight: comments dim, strings and numbers lighter."""
    stripped = line.lstrip()
    indent = line[: len(line) - len(stripped)]
    if stripped.startswith(_COMMENT_PREFIXES):
        return f"{indent}{t.syntax.comment_style}{stripped}{ansi.RESET}"
    out: list[str] = []
    i = 0
    n = len(line)
    while i < n:
        ch = line[i]
        if ch in "\"'`":
            j = i + 1
            while j < n and line[j] != ch:
                j += 2 if line[j] == "\\" else 1
            j = min(j + 1, n)
            out.append(f"{t.syntax.string_style}{line[i:j]}{ansi.RESET}")
            i = j
            continue
        if ch.isalpha() or ch == "_":
            j = i
            while j < n and (line[j].isalnum() or line[j] == "_"):
                j += 1
            word = line[i:j]
            if word in _KEYWORDS:
                out.append(f"{t.syntax.keyword_style}{word}{ansi.RESET}")
            else:
                out.append(word)
            i = j
            continue
        if ch.isdigit():
            j = i
            while j < n and (line[j].isdigit() or line[j] in "._"):
                j += 1
            out.append(f"{t.syntax.number_style}{line[i:j]}{ansi.RESET}")
            i = j
            continue
        out.append(ch)
        i += 1
    return "".join(out)
