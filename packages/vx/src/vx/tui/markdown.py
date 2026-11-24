"""fx's markdown rendering: headings, lists, fences, quotes, rules, tables.

The output rows carry no transcript gutter; the block layer adds fx's
two-column gutter and the ``│ `` command rail.
"""

from __future__ import annotations

import re
from contextlib import suppress

from vx.tui import ansi
from vx.tui import theme as _theme

_FENCE_RE = re.compile(r"^(?P<indent>[ ]{0,3})(?P<marker>`{3,}|~{3,})(?P<info>.*)$")
_HEADING_RE = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.*?)(?:[ \t]+#+)?[ \t]*$")
_RULE_RE = re.compile(r"^ {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$")
_QUOTE_RE = re.compile(r"^ {0,3}>[ ]?(.*)$")
_BULLET_RE = re.compile(r"^([ \t]*)([-*+•])[ \t]+(.*)$")
_ORDERED_RE = re.compile(r"^([ \t]*)(\d{1,3})[.)][ \t]+(.*)$")
_TABLE_SEP_RE = re.compile(r"^[ \t]*\|?[ \t]*:?-{2,}:?[ \t]*(\|[ \t]*:?-{2,}:?[ \t]*)*\|?[ \t]*$")
_TASK_RE = re.compile(r"^\[(?P<mark>[ xX])\][ \t]+(?P<body>.*)$")
_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_HR_HTML_RE = re.compile(r"^<hr\s*/?>$", re.IGNORECASE)
_BR_LINE = "\x00br\x00"

_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_BOLD_RE = re.compile(r"\*\*(?!\s)(.+?)(?<!\s)\*\*")
_UNDER_BOLD_RE = re.compile(r"(?<![\w_])__(?!\s)(.+?)(?<!\s)__(?![\w_])")
_ITALIC_RE = re.compile(r"(?<![*\w])\*(?!\s)(.+?)(?<!\s)\*(?![*\w])")
_UNDER_ITALIC_RE = re.compile(r"(?<![\w_])_(?!\s)(.+?)(?<!\s)_(?![\w_])")
_STRIKE_RE = re.compile(r"~~(?!\s)(.+?)(?<!\s)~~")
_LINK_RE = re.compile(r"\[([^\]\n]+)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")


def _link_destination(text: str, start: int) -> tuple[str, int] | None:
    """Parse a markdown link destination that may be angle-bracketed."""
    n = len(text)
    if start >= n:
        return None
    if text[start] == "<":
        end = text.find(">", start + 1)
        if end == -1:
            return None
        return text[start + 1 : end], end + 1
    j = start
    depth = 0
    while j < n:
        ch = text[j]
        if ch in " \t\n":
            break
        if ch == "(":
            depth += 1
        elif ch == ")":
            if depth == 0:
                break
            depth -= 1
        j += 1
    if j == start:
        return None
    return text[start:j], j


def _link_label(text: str, start: int) -> int | None:
    """Return index of matching ']' for a label starting at ``start``."""
    depth = 0
    i = start
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\\" and i + 1 < n:
            i += 2
            continue
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return None


def _inline(text: str) -> str:
    """Inline markdown to ANSI (code, links/images, emphasis, autolinks)."""
    t = _theme.current()
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\\" and i + 1 < n and text[i + 1] in "\\`*_{}[]()#+-.!|>~":
            out.append(text[i + 1])
            i += 2
            continue
        if ch == "`":
            # Multi-backtick code span.
            tick_end = i
            while tick_end < n and text[tick_end] == "`":
                tick_end += 1
            ticks = text[i:tick_end]
            end = text.find(ticks, tick_end)
            if end != -1:
                code = text[tick_end:end]
                if len(code) >= 2 and code[0] == " " and code[-1] == " ":
                    code = code[1:-1]
                out.append(f"{t.inline_code_open}{code}{t.inline_code_close}")
                i = end + len(ticks)
                continue
        if ch == "!" and i + 1 < n and text[i + 1] == "[":
            close = _link_label(text, i + 1)
            if close is not None and close + 1 < n and text[close + 1] == "(":
                dest = _link_destination(text, close + 2)
                if dest is not None:
                    url, after = dest
                    if after < n and text[after] == ")":
                        alt = text[i + 2 : close].strip() or "image"
                        out.append(f"{t.dim_style}image: {alt}{ansi.RESET}")
                        i = after + 1
                        continue
        if ch == "[":
            close = _link_label(text, i)
            if close is not None and close + 1 < n and text[close + 1] == "(":
                dest = _link_destination(text, close + 2)
                if dest is not None:
                    _, after = dest
                    if after < n and text[after] == ")":
                        label = _inline(text[i + 1 : close])
                        out.append(
                            f"{t.link_style}{ansi.UNDERLINE}{label}{ansi.UNDERLINE_OFF}\x1b[39m"
                        )
                        i = after + 1
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
                out.append(f"\x1b[9m{_inline(text[i + 2 : end])}\x1b[29m")
                i = end + 2
                continue
        if (ch == "*" or ch == "_") and i + 1 < n and text[i + 1] not in " \t":
            # Basic delimiter rule: avoid mid-word _ for snake_case.
            if ch == "_" and i > 0 and (text[i - 1].isalnum() or text[i - 1] == "_"):
                out.append(ch)
                i += 1
                continue
            end = i + 1
            found = -1
            while end < n:
                if text[end] == ch and not text[end - 1].isspace():
                    if (
                        ch == "_"
                        and end + 1 < n
                        and (text[end + 1].isalnum() or text[end + 1] == "_")
                    ):
                        end += 1
                        continue
                    found = end
                    break
                end += 1
            if found > i + 1:
                out.append(f"{ansi.ITALIC}{_inline(text[i + 1 : found])}{ansi.ITALIC_OFF}")
                i = found + 1
                continue
        if (text.startswith("http://", i) or text.startswith("https://", i)) and (
            i == 0 or not (text[i - 1].isalnum() or text[i - 1] in "_[(")
        ):
            j = i
            while j < n and text[j] not in " \t\n<>\"'":
                j += 1
            url = text[i:j]
            trail = ""
            while url and url[-1] in ".,;:!?)]}":
                # Keep balanced parens in URLs like Wikipedia links.
                if url[-1] == ")" and url.count("(") >= url.count(")"):
                    break
                trail = url[-1] + trail
                url = url[:-1]
            if len(url) > 8:
                out.append(f"{t.link_style}{ansi.UNDERLINE}{url}{ansi.UNDERLINE_OFF}\x1b[39m")
                if trail:
                    out.append(trail)
                i += len(url) + len(trail)
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def _row(text: str, cols: int) -> str:
    return ansi.clip_ellipsis(text, cols) if ansi.width(text) > cols else text


def _task_prefix(body: str) -> tuple[str | None, str]:
    m = _TASK_RE.match(body)
    if not m:
        return None, body
    mark = "x" if m.group("mark").lower() == "x" else " "
    return f"[{mark}]", m.group("body")


def _latex(line: str) -> str:
    """Unicode math for a non-fenced line, when the helper is importable."""
    with suppress(Exception):
        from ctx.term.latex import preprocess_latex

        return preprocess_latex(line)
    return line


_TASK_RE = re.compile(r"^\[([ xX])\][ \t]+(.*)$")


def _checkbox(indent: int, mark: str, body: str, done: bool, cols: int) -> list[str]:
    """A task row: ``☑ title``, with a finished task dimmed."""
    t = _theme.current()
    lead = " " * indent
    style = t.dim_style if done else ""
    tail = ansi.RESET if done else ""
    marker = f"{t.dim_style}{mark}{ansi.RESET} "
    prefix_w = indent + ansi.width(mark) + 1
    rows = ansi.wrap(body, max(1, cols - prefix_w))
    out: list[str] = []
    for index, row in enumerate(rows):
        if index == 0:
            out.append(f"{lead}{marker}{style}{row}{tail}")
        else:
            out.append(" " * prefix_w + f"{style}{row}{tail}")
    return out


def _bullet(indent: int, body: str, cols: int) -> list[str]:
    lead = " " * indent
    task, body = _task_prefix(body)
    marker_text = f"{task} " if task else "• "
    marker = f"{ansi.dim()}{marker_text}{ansi.RESET}"
    prefix_w = indent + ansi.width(marker_text)
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
    marker = f"{pad}{ansi.dim()}{marker_text}{ansi.RESET}"
    prefix_w = indent + field + 1
    rows = ansi.wrap(body, max(1, cols - prefix_w))
    out = []
    for i, r in enumerate(rows):
        if i == 0:
            out.append(lead + marker + " " + r)
        else:
            out.append(" " * prefix_w + r)
    return out


def _quote_block(lines_in: list[str], cols: int) -> list[str]:
    """Render quoted content with a dim rail, supporting nested markdown."""
    rail = f"{ansi.dim()}│ {ansi.RESET}"
    inner_width = max(1, cols - 2)
    inner = render("\n".join(lines_in), inner_width)
    if not inner:
        return [rail.rstrip()]
    return [f"{rail}{r}" if r else rail.rstrip() for r in inner]


#: fx draws a horizontal rule narrower than the transcript
#: (ansi.horizontal_rule_width), so it reads as a break, not a divider.
RULE_WIDTH = 60


def _rule(cols: int) -> str:
    width = min(max(1, cols), RULE_WIDTH)
    return f"{ansi.dim()}{'─' * width}{ansi.RESET}"


def _table_alignments(sep_line: str, columns: int) -> list[str]:
    cells = [c.strip() for c in _split_row(sep_line)]
    aligns: list[str] = []
    for i in range(columns):
        cell = cells[i] if i < len(cells) else ""
        left = cell.startswith(":")
        right = cell.endswith(":")
        if left and right:
            aligns.append("center")
        elif right:
            aligns.append("right")
        else:
            aligns.append("left")
    return aligns


def _align_cell(text: str, width: int, align: str) -> str:
    pad = max(0, width - ansi.width(text))
    if align == "right":
        return " " * pad + text
    if align == "center":
        left = pad // 2
        return " " * left + text + " " * (pad - left)
    return text + " " * pad


def _render_table(rows: list[list[str]], sep_line: str | None, cols: int) -> list[str]:
    """Render a markdown table with alignment and width-aware shrinking."""
    if not rows:
        return []
    columns = max(len(r) for r in rows)
    aligns = _table_alignments(sep_line or "", columns)
    cells = [[_inline(c.strip()) for c in r] + [""] * (columns - len(r)) for r in rows]
    widths = [0] * columns
    for r in cells:
        for i, c in enumerate(r):
            widths[i] = max(widths[i], ansi.width(c))
    sep = f" {ansi.dim()}│{ansi.RESET} "
    total = sum(widths) + ansi.width(sep) * (columns - 1)
    if total > cols:
        while total > cols and max(widths) > 3:
            widest = widths.index(max(widths))
            widths[widest] -= 1
            total -= 1
    out: list[str] = []
    for index, r in enumerate(cells):
        parts = []
        for i, c in enumerate(r):
            cell = ansi.clip_ellipsis(c, widths[i]) if ansi.width(c) > widths[i] else c
            cell = _align_cell(cell, widths[i], aligns[i] if i < len(aligns) else "left")
            if index == 0:
                cell = f"{ansi.BOLD}{cell}{ansi.DIM_OFF}"
            parts.append(cell)
        out.append(sep.join(parts))
        if index == 0:
            rule = f"{ansi.dim()}─┼─{ansi.RESET}".join("─" * w for w in widths)
            out.append(rule)
    return out


def _split_row(line: str) -> list[str]:
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|"):
        body = body[:-1]
    return body.split("|")


def _replace_html_breaks(line: str) -> str:
    """Replace safe <br> tags outside code spans with a hard-break sentinel."""
    if "<" not in line or not _BR_RE.search(line):
        return line
    out: list[str] = []
    i = 0
    n = len(line)
    while i < n:
        ch = line[i]
        if ch == "`":
            tick_end = i
            while tick_end < n and line[tick_end] == "`":
                tick_end += 1
            ticks = line[i:tick_end]
            end = line.find(ticks, tick_end)
            if end != -1:
                end += len(ticks)
                out.append(line[i:end])
                i = end
                continue
        if ch == "<":
            m = _BR_RE.match(line, i)
            if m:
                out.append(_BR_LINE)
                i = m.end()
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def render(text: str, cols: int) -> list[str]:
    """Render markdown to styled rows (no gutter, no trailing blanks)."""
    rows: list[str] = []
    raw_lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    lines: list[str] = []
    in_fence = False
    fence_char = ""
    fence_len = 0
    for raw in raw_lines:
        fm = _FENCE_RE.match(raw)
        if in_fence:
            lines.append(raw)
            if (
                fm
                and fm.group("marker")[0] == fence_char
                and len(fm.group("marker")) >= fence_len
                and not fm.group("info").strip()
            ):
                in_fence = False
            continue
        if fm:
            in_fence = True
            fence_char = fm.group("marker")[0]
            fence_len = len(fm.group("marker"))
            lines.append(raw)
            continue
        lines.append(_replace_html_breaks(raw))

    # fx turns LaTeX math into Unicode so a formula reads as symbols rather than
    # as backslash words; the helper leaves fenced code alone.
    lines = [_latex(line) for line in lines]

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
        for chunk in joined.split(_BR_LINE):
            chunk = chunk.strip()
            if not chunk:
                if rows and rows[-1] != "":
                    rows.append("")
                continue
            rows.extend(ansi.wrap(_inline(chunk), cols) or [""])

    def flush_table() -> None:
        nonlocal table_rows
        if not table_rows:
            return
        sep_line = next((r for r in table_rows if _TABLE_SEP_RE.match(r)), None)
        body = [r for r in table_rows if not _TABLE_SEP_RE.match(r)]
        rows.extend(_render_table([_split_row(r) for r in body], sep_line, cols))
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
                and not m.group("info").strip()
            ):
                fence_marker = None
                rows.extend(_render_code(fence_rows, cols))
                fence_rows = []
                continue
            fence_rows.append(line)
            continue
        if line.strip() == _BR_LINE:
            flush_paragraph()
            if rows and rows[-1] != "":
                rows.append("")
            continue

        stripped = line.strip()
        is_table_sep = bool(stripped and _TABLE_SEP_RE.match(line))
        is_table_row = "|" in line and (
            stripped.startswith("|")
            or is_table_sep
            or bool(table_rows)
            or (i < n and _TABLE_SEP_RE.match(lines[i]))
        )
        if is_table_row and not _FENCE_RE.match(line):
            if is_table_sep and not table_rows:
                # Orphan separator without a header row; render as plain text.
                flush_paragraph()
                rows.extend(ansi.wrap(_inline(stripped), cols) or [""])
                continue
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
            continue

        if not line.strip():
            flush_paragraph()
            if rows and rows[-1] != "":
                rows.append("")
            continue
        if line.strip() == _BR_LINE:
            flush_paragraph()
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

        if _RULE_RE.match(line) or _HR_HTML_RE.match(line.strip()):
            flush_paragraph()
            rows.append(_rule(cols))
            continue

        qm = _QUOTE_RE.match(line)
        if qm:
            flush_paragraph()
            quote_lines = [qm.group(1)]
            while i < n:
                nxt = lines[i]
                if not nxt.strip():
                    break
                if _FENCE_RE.match(nxt) or _HEADING_RE.match(nxt) or _RULE_RE.match(nxt):
                    break
                if _BULLET_RE.match(nxt) or _ORDERED_RE.match(nxt):
                    break
                qn = _QUOTE_RE.match(nxt)
                if qn:
                    quote_lines.append(qn.group(1))
                    i += 1
                    continue
                # Lazy continuation line.
                quote_lines.append(nxt.strip())
                i += 1
            rows.extend(_quote_block(quote_lines, cols))
            continue

        bm = _BULLET_RE.match(line)

        if bm and (len(bm.group(1)) < 4 or True):
            flush_paragraph()
            indent = min(len(bm.group(1)), 8)
            task = _TASK_RE.match(bm.group(3))
            if task:
                # A task list reads as a checkbox, like fx's `- [x] done`.
                done = task.group(1).lower() == "x"
                mark = "☑" if done else "☐"
                body = _inline(task.group(2))
                rows.extend(_checkbox(indent, mark, body, done, cols))
                continue
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
    if fence_marker is not None:
        rows.extend(_render_code(fence_rows, cols))

    while rows and rows[-1] == "":
        rows.pop()
    return rows


def code_panel(body: list[str], language: str, cols: int) -> list[str]:
    """fx's transcript code panel: ``─ zig ─`` over the code and a rule below.

    Mirrors ``renderCodeBlockForTranscript`` in
    src/ui/render_engine/transcript_blocks.zig: dim rules, no side rails, and
    a panel as wide as the code or its label, whichever is wider.
    """
    code = [line for line in body if not line.startswith("\x00lang:")]
    max_code = max((ansi.width(line) for line in code), default=0)
    label_width = min(ansi.width(language), max(0, cols - 4)) if language else 0
    panel = min(cols, max(6, max_code, label_width + 4))
    if panel <= 0:
        return []
    rows: list[str] = [_panel_header(language, panel)]
    for line in code or [""]:
        for chunk in ansi.hard_wrap(line.expandtabs(4), max(1, cols - 2)) if line else [""]:
            rows.append(_highlight(chunk, _theme.current()))
    rows.append(f"{ansi.dim()}{'─' * panel}{ansi.RESET}")
    return rows


def _panel_header(language: str, panel: int) -> str:
    label = ansi.clip(language, panel - 4) if language else ""
    if language and not label:
        label = "?"
    if not label:
        return f"{ansi.dim()}{'─' * panel}{ansi.RESET}"
    fill = max(0, panel - 3 - ansi.width(label))
    return f"{ansi.dim()}─ {label} {'─' * fill}{ansi.RESET}"


def _render_code(body: list[str], cols: int) -> list[str]:
    language = ""
    if body and body[0].startswith("\x00lang:"):
        language = body[0][len("\x00lang:") :]
    return code_panel(body, language, cols)


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
