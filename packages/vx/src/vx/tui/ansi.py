"""ANSI widths, wrapping and clipping for the vx TUI.

Ported from fx's ``display_width`` / ``assistant_wrap`` / ``row_text``
helpers. Only the pieces the UI needs live here; everything is stdlib.
"""

from __future__ import annotations

import re
import unicodedata

from vx.tui import theme as _theme

RESET = "\x1b[0m"
BOLD = "\x1b[1m"
DIM_OFF = "\x1b[22m"
ITALIC = "\x1b[3m"
ITALIC_OFF = "\x1b[23m"
UNDERLINE = "\x1b[4m"
UNDERLINE_OFF = "\x1b[24m"

_ANSI_RE = re.compile(r"\x1b\[[0-9;:?]*[ -/]*[@-~]")
_OSC_RE = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")


# -- palette accessors -------------------------------------------------------


def _t():
    return _theme.current()


def style(name: str) -> str:
    """The escape slot ``name`` of the active theme."""
    return getattr(_t(), name)


def dim() -> str:
    return _t().dim_style


def tag() -> str:
    return _t().tag_style


# -- widths ------------------------------------------------------------------


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", _OSC_RE.sub("", text))


def char_width(ch: str) -> int:
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def width(text: str) -> int:
    """Visible cells of ``text`` ignoring SGR/OSC sequences."""
    total = 0
    for ch in strip_ansi(text):
        total += char_width(ch)
    return total


visible_width = width


def clip(text: str, limit: int) -> str:
    """Keep at most ``limit`` cells of styled text, cutting on a unit boundary."""
    if limit <= 0:
        return ""
    out: list[str] = []
    used = 0
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\x1b":
            m = _ANSI_RE.match(text, i) or _OSC_RE.match(text, i)
            if m:
                out.append(m.group(0))
                i = m.end()
                continue
        cw = char_width(ch)
        if used + cw > limit:
            break
        out.append(ch)
        used += cw
        i += 1
    return "".join(out)


def clip_ellipsis(text: str, limit: int) -> str:
    """fx's trailing ellipsis projection: ``abc…`` inside ``limit`` cells."""
    if limit <= 0:
        return ""
    if width(text) <= limit:
        return text
    if limit == 1:
        return "…"
    return clip(text, limit - 1) + "…"


REVERSE = "\x1b[7m"
REVERSE_OFF = "\x1b[27m"


def highlight_matches(text: str, spans: list[tuple[int, int]]) -> str:
    """Reverse-video plain-text character spans without losing the row's SGR.

    ``27`` turns *only* reverse video off, so a match inside a coloured/spinner
    row keeps its colours on both sides. Spans are (start, end) indices into
    the text with escapes removed; escapes are copied through untouched.
    """
    events: dict[int, list[str]] = {}
    for start, end in sorted(spans):
        if end <= start:
            continue
        events.setdefault(start, []).append(REVERSE)
        events.setdefault(end, []).append(REVERSE_OFF)
    if not events:
        return text

    out: list[str] = []
    plain_i = 0
    i = 0
    for code in events.get(0, ()):
        out.append(code)
    while i < len(text):
        if text[i] == "\x1b":
            match = _ANSI_RE.match(text, i) or _OSC_RE.match(text, i)
            if match:
                out.append(match.group(0))
                i = match.end()
                continue
        out.append(text[i])
        plain_i += 1
        i += 1
        for code in events.get(plain_i, ()):
            out.append(code)
    return "".join(out)


def osc8(url: str, text: str) -> str:
    """Wrap ``text`` in an OSC 8 hyperlink so terminals can open it directly."""
    safe = "".join(ch for ch in str(url).strip() if ch.isprintable() and ch not in "\x1b\\")
    if not safe:
        return text
    return f"\x1b]8;;{safe}\x1b\\{text}\x1b]8;;\x1b\\"


def link_at(text: str, col: int) -> str | None:
    """The OSC 8 destination covering display column ``col`` of ``text``."""
    if col < 0:
        return None
    uri: str | None = None
    cell = 0
    i = 0
    while i < len(text):
        if text[i] == "\x1b":
            match = _OSC_RE.match(text, i)
            if match:
                raw = match.group(0)
                body = raw[2:-1] if raw.endswith("\x07") else raw[2:-2]
                if body.startswith("8;;"):
                    uri = body[3:] or None
                i = match.end()
                continue
            match = _ANSI_RE.match(text, i)
            if match:
                i = match.end()
                continue
        size = char_width(text[i])
        if uri is not None and cell <= col < cell + size:
            return uri
        cell += size
        i += 1
    return None


def _prefix_plain(text: str, limit: int) -> str:
    out = []
    used = 0
    for ch in text:
        cw = char_width(ch)
        if used + cw > limit:
            break
        out.append(ch)
        used += cw
    return "".join(out)


def _suffix_plain(text: str, limit: int) -> str:
    out: list[str] = []
    used = 0
    for ch in reversed(text):
        cw = char_width(ch)
        if used + cw > limit:
            break
        out.append(ch)
        used += cw
    return "".join(reversed(out))


def pad(text: str, target: int) -> str:
    """Pad a styled row with spaces to ``target`` cells."""
    missing = target - width(text)
    if missing <= 0:
        return text
    return text + " " * missing


# -- wrapping ----------------------------------------------------------------


def wrap(text: str, limit: int) -> list[str]:
    """Word-wrap styled text, keeping SGR state across continuation rows.

    fx reflows prose at word breaks and never breaks a word that fits; a word
    longer than the row is hard-split on display-unit boundaries.
    """
    if limit <= 0:
        return [text]
    rows: list[str] = []
    for paragraph in text.split("\n"):
        if not visible_width(paragraph):
            rows.append("")
            continue
        rows.extend(_wrap_paragraph(paragraph, limit))
    return rows


def _wrap_paragraph(paragraph: str, limit: int) -> list[str]:
    rows: list[str] = []
    cur: list[str] = []
    cur_w = 0
    word: list[str] = []
    word_w = 0
    active: list[str] = []

    def flush() -> None:
        nonlocal cur, cur_w, word, word_w
        if not word:
            return
        need = word_w + (1 if cur_w else 0)
        if cur_w and cur_w + need > limit:
            rows.append("".join(cur))
            cur = list(active)
            cur_w = 0
            if cur_w + word_w > limit:
                # A single unbreakable word wider than the row.  The word may
                # already hold escape sequences, which have no width and must
                # not be measured as characters.
                for piece in word:
                    if piece.startswith("\x1b"):
                        cur.append(piece)
                        continue
                    cw = char_width(piece)
                    if cw and cur_w + cw > limit:
                        rows.append("".join(cur))
                        cur = list(active)
                        cur_w = 0
                    cur.append(piece)
                    cur_w += cw
                word.clear()
                word_w = 0
                return
        elif cur_w:
            cur.append(" ")
            cur_w += 1
        cur.extend(word)
        cur_w += word_w
        word.clear()
        word_w = 0

    i = 0
    n = len(paragraph)
    while i < n:
        ch = paragraph[i]
        if ch == "\x1b":
            m = _ANSI_RE.match(paragraph, i) or _OSC_RE.match(paragraph, i)
            if m:
                seq = m.group(0)
                word.append(seq)
                if seq == RESET:
                    active.clear()
                elif seq.endswith("m"):
                    active.append(seq)
                i = m.end()
                continue
        if ch == " ":
            flush()
            i += 1
            continue
        cw = char_width(ch)
        if cw and cur_w + word_w + cw > limit and word_w == 0 and cur_w:
            # No room left on this row for the first unit of a word.
            flush()
        word.append(ch)
        word_w += cw
        i += 1
    flush()
    rows.append("".join(cur))
    return rows


def hard_wrap(text: str, limit: int) -> list[str]:
    """Character-wise wrap for literal text (code, paths)."""
    if limit <= 0:
        return [text]
    rows: list[str] = []
    cur: list[str] = []
    cur_w = 0
    for ch in text:
        if ch == "\n":
            rows.append("".join(cur))
            cur = []
            cur_w = 0
            continue
        cw = char_width(ch)
        if cw and cur_w + cw > limit:
            rows.append("".join(cur))
            cur = []
            cur_w = 0
        cur.append(ch)
        cur_w += cw
    rows.append("".join(cur))
    return rows


# -- ctx rich markup ---------------------------------------------------------

_TAG_RE = re.compile(r"\[(/?)([A-Za-z_][A-Za-z0-9_#-]*)\]")

_COLORS = {
    "black": 30,
    "red": 31,
    "green": 32,
    "yellow": 33,
    "blue": 34,
    "magenta": 35,
    "cyan": 36,
    "white": 37,
    "grey": 90,
    "gray": 90,
    "bright_black": 90,
    "bright_red": 91,
    "bright_green": 92,
    "bright_yellow": 93,
    "bright_blue": 94,
    "bright_magenta": 95,
    "bright_cyan": 96,
    "bright_white": 97,
}

_ATTRS = {"bold": 1, "dim": 2, "italic": 3, "underline": 4, "blink": 5, "reverse": 7, "strike": 9}


def _sgr_for_tag(name: str) -> str | None:
    # fx's palette is monochrome: named colors from ctx markup collapse onto
    # the theme's muted slots so tool output keeps fx's look.
    if name == "term-code":
        return _t().inline_code_open.strip("\x1b[m").replace("38;5;", "38;5;")
    if name in _ATTRS:
        return str(_ATTRS[name])
    if name in _COLORS:
        return _t().dim_style.strip("\x1b[m")
    if name.startswith("#") and len(name) == 7:
        try:
            r, g, b = int(name[1:3], 16), int(name[3:5], 16), int(name[5:7], 16)
        except ValueError:
            return None
        return f"38;2;{r};{g};{b}"
    return None


def markup_to_ansi(text: str) -> str:
    """Convert ctx rich markup (``[dim]x[/dim]``) into ANSI escapes."""
    if "[" not in text:
        return text
    out: list[str] = []
    active: list[tuple[str, str]] = []
    current = ""
    pos = 0

    def combined() -> str:
        codes: list[str] = []
        for _, sgr in active:
            for code in sgr.split(";"):
                if code and code not in codes:
                    codes.append(code)
        return ";".join(codes)

    for m in _TAG_RE.finditer(text):
        if m.start() > pos:
            out.append(text[pos : m.start()])
        closing = m.group(1) == "/"
        name = m.group(2)
        literal = False
        if closing:
            for i in range(len(active) - 1, -1, -1):
                if active[i][0] == name:
                    del active[i]
                    break
            else:
                literal = True
        else:
            sgr = _sgr_for_tag(name)
            if sgr is None:
                literal = True
            else:
                active.append((name, sgr))
        if literal:
            out.append(m.group(0))
        new = combined()
        if new != current:
            out.append(RESET if not new else f"\x1b[{new}m")
            current = new
        pos = m.end()
    if pos < len(text):
        out.append(text[pos:])
    if current:
        out.append(RESET)
    return "".join(out)
