"""pi-style boxed tool rendering — Python/Textual port.

Port of pi-style's ``shared/box.ts`` chrome: rounded borders with embedded
labels, dim frame, boxed body lines, and metrics footers. Pure functions over
Rich ``Text`` so callers can compose them into any widget.
"""

from __future__ import annotations

import time

from rich.text import Text

from vtx.core.config import config

BOX_H = "─"
BOX_V = "│"
CORNER_TL = "╭"
CORNER_TR = "╮"
CORNER_BL = "╰"
CORNER_BR = "╯"

#: pi-style caps tool boxes at ~100 columns regardless of terminal width.
MAX_BOX_WIDTH = 100
MIN_BOX_WIDTH = 30


def box_width(width: int | None) -> int:
    w = width or 80
    return max(MIN_BOX_WIDTH, min(w, MAX_BOX_WIDTH))


def _colors():
    return config.ui.colors


def _dim(text: str) -> str:
    return f"[dim]{text}[/dim]"


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


def _truncate(text: str, max_len: int) -> str:
    plain = strip_markup(text)
    if len(plain) <= max_len:
        return text
    return plain[: max(0, max_len - 1)] + "…"


def _fill(width: int, style: str = "dim") -> str:
    return f"[{style}]{BOX_H * width}[/{style}]"


def top_border(
    icon: str, name: str, detail: str, width: int, *, running: bool = False, failed: bool = False
) -> Text:
    """``╭─ {icon} {Name} {detail} ────────────╮`` with styled segments."""
    colors = _colors()
    w = box_width(width)
    inner = w - 2  # inside the two corners

    name_style = colors.failed if failed else (colors.running if running else colors.muted)
    icon_style = str(colors.accent) if not (running or failed) else str(name_style)
    name_style = f"bold {name_style}"

    prefix = f"{CORNER_TL}{BOX_H} "
    suffix = f" {CORNER_TR}"
    # Reserve: prefix + suffix + icon+space+name+space + at least one fill dash.
    fixed = len(prefix) + len(suffix) + len(icon) + 1 + len(name) + 1 + 1
    detail_budget = max(0, inner - (fixed - len(prefix) - len(suffix)))
    detail_txt = _truncate(detail, detail_budget) if detail else ""

    body = Text()
    body.append(prefix, style="dim")
    body.append(f"{icon} ", style=str(icon_style))
    body.append(name, style=str(name_style))
    if detail_txt:
        body.append(" ")
        body.append(detail_txt, style="dim")
    used = (
        len(prefix)
        + len(icon)
        + 1
        + len(name)
        + (1 + visible_len(detail_txt) if detail_txt else 0)
    )
    fill = max(0, w - used - len(suffix))
    body.append(BOX_H * fill, style="dim")
    body.append(suffix, style="dim")
    return body


def bottom_border(footer: str, width: int, *, hint: str = "", failed: bool = False) -> Text:
    """``╰─ {footer} ──────────── {hint} ─╯`` — footer left, hint right."""
    w = box_width(width)
    prefix = f"{CORNER_BL}{BOX_H} "
    suffix = f" {CORNER_BR}"
    hint_txt = hint or ""

    # Footer and hint are markup strings; measure their visible length.
    footer_vis = visible_len(footer)
    hint_vis = visible_len(hint_txt)

    min_fill = 1
    avail = w - len(prefix) - len(suffix) - footer_vis - hint_vis - (1 if hint_txt else 0)
    if avail < min_fill:
        # Too narrow: drop the hint, then truncate the footer.
        hint_txt, hint_vis = "", 0
        avail = w - len(prefix) - len(suffix) - footer_vis - 0
        if avail < min_fill:
            excess = min_fill - avail
            footer = _truncate(footer, max(4, footer_vis - excess))
            footer_vis = visible_len(footer)
            avail = w - len(prefix) - len(suffix) - footer_vis

    body = Text()
    body.append(prefix, style="dim")
    try:
        body.append_text(Text.from_markup(footer))
    except Exception:
        body.append(strip_markup(footer), style="dim")
    fill = max(0, w - len(prefix) - len(suffix) - footer_vis - hint_vis - (1 if hint_txt else 0))
    body.append(BOX_H * fill, style="dim")
    if hint_txt:
        body.append(" ")
        body.append(hint_txt, style="dim")
    body.append(suffix, style="dim")
    return body


def box_line(content: Text | str, width: int, *, failed: bool = False) -> Text:
    """``│ {content padded} │`` — one boxed body line."""
    w = box_width(width)
    inner = w - 4  # two corners-as-sides + two padding spaces each side
    side = "dim" if not failed else "failed"
    out = Text()
    out.append(f"{BOX_V} ", style=side)
    if isinstance(content, str):
        try:
            content = Text.from_markup(content)
        except Exception:
            content = Text(content)
    plain = content.plain
    if len(plain) > inner:
        content = Text(plain[: inner - 1] + "…")
        # Re-apply nothing — truncated lines lose styling, acceptable.
    pad = inner - len(content.plain)
    out.append_text(content)
    if pad > 0:
        out.append(" " * pad)
    out.append(f" {BOX_V}", style=side)
    return out


def box_blank(width: int, *, failed: bool = False) -> Text:
    w = box_width(width)
    side = "dim" if not failed else "failed"
    return Text(f"{BOX_V}{' ' * (w - 2)}{BOX_V}", style=side)


def wrap_boxed(text: Text, width: int, *, failed: bool = False) -> Text:
    """Box every line of ``text``: ``│ line │`` rows joined by newlines."""
    out = Text()
    lines = text.split("\n")
    for i, line in enumerate(lines):
        out.append_text(box_line(line, width, failed=failed))
        if i < len(lines) - 1:
            out.append("\n")
    return out


_LEXERS = {
    "py": "python",
    "pyi": "python",
    "json": "json",
    "sh": "bash",
    "bash": "bash",
    "zsh": "bash",
    "ts": "typescript",
    "tsx": "typescript",
    "js": "javascript",
    "jsx": "javascript",
    "md": "markdown",
}


def _highlight_fragment(fragment: str, path: str) -> Text:
    """Syntax-highlight ``fragment`` without line wrapping.

    ``format_python_source`` wraps at the render width, which splits long
    lines into gutter-less continuation rows inside the numbered preview —
    so this goes through Pygments directly with ``word_wrap=False``.
    """
    name = path.rsplit("/", 1)[-1]
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    lexer = _LEXERS.get(ext)
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


def highlighted_numbered_lines(content: str, path: str, *, max_lines: int = 15) -> Text:
    """``cat -n`` preview of ``content``'s tail, syntax-highlighted by ``path``.

    The fragment is highlighted as one block (Pygments tolerates incomplete
    syntax), then split so each line keeps its styles under a dim gutter.
    Bounded: only the last ~6KB / ``max_lines`` lines are processed.
    """
    normalized = content.replace("\t", "    ").replace("\r", "")
    if not normalized:
        return Text()
    all_lines = normalized.split("\n")
    total = len(all_lines)
    start = max(0, total - max_lines)
    tail = all_lines[start:]
    gutter = max(1, len(str(total)))

    body = _highlight_fragment("\n".join(tail), path)
    body_lines = body.split("\n")
    out = Text()
    for i, line in enumerate(body_lines):
        num = str(start + i + 1).rjust(gutter)
        out.append(f"{num} ", style="dim")
        out.append_text(line)
        out.append("\n")
    hidden = start
    if hidden > 0:
        out.append(f"… {hidden} earlier lines\n", style="dim")
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
