"""HTML to styled terminal rows, the sibling of ``vx.tui.markdown``.

A deliberately small subset, parsed with stdlib ``html.parser`` and painted
with vx's ANSI/theme helpers. No scripts, styles or downloads are executed.

Supported block tags: h1-h6, p, div/section/article/header/footer/main,
ul/ol/li (nested), blockquote, pre, table/tr/th/td, hr, br, img.
Supported inline tags: span, a (OSC 8 hyperlink), b/strong, i/em/cite, u,
s/del/strike, code/kbd/samp, mark, small, sub/sup, abbr, q.
Supported inline CSS: color, background-color, font-weight, font-style,
text-decoration, font-size.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from html.parser import HTMLParser

from vx.tui import ansi, theme

_VOID_TAGS = {"br", "hr", "img", "input", "meta", "link"}
_SKIP_TAGS = {"head", "style", "script", "template", "noscript"}
_INLINE_TAGS = {
    "span",
    "a",
    "b",
    "strong",
    "i",
    "em",
    "cite",
    "u",
    "s",
    "del",
    "strike",
    "code",
    "kbd",
    "samp",
    "mark",
    "small",
    "sub",
    "sup",
    "abbr",
    "q",
}
_HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
_NAMED_COLORS = {
    "red": "#ff6b6b",
    "green": "#51cf66",
    "blue": "#5c9cff",
    "yellow": "#fcc419",
    "orange": "#ff922b",
    "purple": "#b197fc",
    "cyan": "#66d9e8",
    "magenta": "#e879f9",
    "grey": "#909aa4",
    "gray": "#909aa4",
    "white": "#f5f5f5",
    "black": "#1a1a1a",
}


def _paint(value: str, *, bg: bool = False) -> str:
    """Turn a color spec (named, #rrggbb or an existing SGR) into an escape."""
    if not value:
        return ""
    if value.startswith("\x1b"):
        return value
    value = _NAMED_COLORS.get(value.strip().lower(), value)
    if not value.startswith("#") or len(value) != 7:
        return ""
    try:
        r, g, b = (int(value[i : i + 2], 16) for i in (1, 3, 5))
    except ValueError:
        return ""
    return f"\x1b[{48 if bg else 38};2;{r};{g};{b}m"


@dataclass(frozen=True)
class _Style:
    fg: str = ""
    bg: str = ""
    bold: bool = False
    italic: bool = False
    underline: bool = False
    strike: bool = False
    dim: bool = False

    def with_(self, **changes: object) -> _Style:
        return replace(self, **changes)

    def open(self) -> str:
        codes = []
        if self.bold:
            codes.append("1")
        if self.dim:
            codes.append("2")
        if self.italic:
            codes.append("3")
        if self.underline:
            codes.append("4")
        if self.strike:
            codes.append("9")
        attrs = f"\x1b[{';'.join(codes)}m" if codes else ""
        return _paint(self.fg) + _paint(self.bg, bg=True) + attrs

    def wrap(self, text: str) -> str:
        prefix = self.open()
        return f"{prefix}{text}{ansi.RESET}" if prefix else text


@dataclass
class _Node:
    tag: str | None = None
    attrs: dict[str, str] = field(default_factory=dict)
    children: list[_Node] = field(default_factory=list)
    text: str = ""

    def content(self) -> str:
        return self.text if self.tag is None else "".join(c.content() for c in self.children)


class _Tree(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("root")
        self._stack = [self.root]
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._skip or tag in _SKIP_TAGS:
            self._skip += int(tag in _SKIP_TAGS)
            return
        node = _Node(tag, {k: v or "" for k, v in attrs})
        self._stack[-1].children.append(node)
        if tag not in _VOID_TAGS:
            self._stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if not self._skip:
            self._stack[-1].children.append(_Node(tag, {k: v or "" for k, v in attrs}))

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
            return
        for i in range(len(self._stack) - 1, 0, -1):
            if self._stack[i].tag == tag:
                del self._stack[i:]
                return

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self._stack[-1].children.append(_Node(text=data))


class HTMLView:
    """Render HTML into styled terminal rows using vx's ANSI helpers."""

    def __init__(self, cols: int) -> None:
        self.cols = max(20, cols)

    def render(self, html: str) -> list[str]:
        tree = _Tree()
        tree.feed(html)
        tree.close()
        rows: list[str] = []
        self._blocks(tree.root.children, rows, self._base(), 0)
        return self._tidy(rows)

    # -- inline -------------------------------------------------------------

    def _base(self) -> _Style:
        return _Style(fg=theme.current().hint_style)

    def _inline_style(self, node: _Node, style: _Style) -> _Style:
        for decl in node.attrs.get("style", "").split(";"):
            if ":" not in decl:
                continue
            prop, value = (part.strip().lower() for part in decl.split(":", 1))
            if prop in ("color", "foreground"):
                style = style.with_(fg=value)
            elif prop in ("background-color", "background"):
                style = style.with_(bg=value)
            elif prop == "font-weight" and value in ("bold", "bolder", "700", "800", "900"):
                style = style.with_(bold=True)
            elif prop == "font-style" and value == "italic":
                style = style.with_(italic=True)
            elif prop == "text-decoration":
                style = style.with_(
                    underline="underline" in value, strike=style.strike or "line-through" in value
                )
            elif prop == "font-size" and value in ("small", "smaller"):
                style = style.with_(dim=True)
        return style

    def _inline(self, node: _Node, style: _Style) -> str:
        if node.tag is None:
            text = re.sub(r"\s+", " ", node.text)
            return style.wrap(text) if text and style.open() else text

        t = node.tag
        child = style
        if t in ("b", "strong"):
            child = style.with_(bold=True)
        elif t in ("i", "em", "cite"):
            child = style.with_(italic=True)
        elif t == "u":
            child = style.with_(underline=True)
        elif t in ("s", "del", "strike"):
            child = style.with_(strike=True)
        elif t == "small":
            child = style.with_(dim=True)
        elif t == "mark":
            child = style.with_(fg="#1a1a1a", bg=_NAMED_COLORS["yellow"])
        elif t in ("code", "kbd", "samp"):
            child = style.with_(fg="#ff9e9e", bg="#262a33")
        elif t == "a":
            child = style.with_(fg=theme.current().link_style, underline=True)
        elif t == "span":
            child = self._inline_style(node, style)

        if t == "img":
            alt = node.attrs.get("alt", "image")
            return f"{theme.current().dim_style}[image: {alt}]{ansi.RESET}"

        body = "".join(self._inline(c, child) for c in node.children)
        if t == "a" and node.attrs.get("href"):
            return ansi.osc8(node.attrs["href"], body)
        return body

    def _inline_children(self, node: _Node, style: _Style) -> str:
        return "".join(self._inline(c, style) for c in node.children)

    # -- blocks -------------------------------------------------------------

    def _blocks(self, nodes: list[_Node], rows: list[str], style: _Style, indent: int) -> None:
        width = max(8, self.cols - indent)
        for node in nodes:
            if node.tag is None:
                text = re.sub(r"\s+", " ", node.text).strip()
                if text:
                    rows.extend(ansi.wrap(style.wrap(text), width))
                continue

            t = node.tag
            if t in _HEADINGS:
                level = int(t[1])
                head = self._inline_children(node, style.with_(bold=True))
                if level == 1:
                    head = f"{ansi.UNDERLINE}{head}{ansi.UNDERLINE_OFF}"
                rows.extend(ansi.wrap(head, width))
                if level <= 2:
                    rows.append(f"{ansi.dim()}{'─' * min(width, 64)}{ansi.RESET}")
                rows.append("")
            elif t in ("p", "div", "section", "article", "header", "footer", "main", "span"):
                text = self._inline_children(node, self._inline_style(node, style)).strip()
                if text:
                    rows.extend(ansi.wrap(text, width))
                rows.append("")
            elif t in ("ul", "ol"):
                self._list(node, rows, style, indent, t == "ol")
                rows.append("")
            elif t == "li":
                self._item(node, rows, style, indent, "•")
            elif t == "blockquote":
                inner: list[str] = []
                self._blocks(node.children, inner, style.with_(dim=True), 0)
                rail = f"{ansi.dim()}│ {ansi.RESET}"
                for line in inner:
                    rows.append(rail + line if line else rail.rstrip())
                rows.append("")
            elif t == "pre":
                self._pre(node, rows, style, indent)
                rows.append("")
            elif t == "table":
                self._table(node, rows, style)
                rows.append("")
            elif t == "hr":
                rows.append(f"{ansi.dim()}{'─' * min(width, 64)}{ansi.RESET}")
                rows.append("")
            elif t == "br":
                rows.append("")
            elif t == "img":
                rows.append(f"{ansi.dim()}[image: {node.attrs.get('alt', 'image')}]{ansi.RESET}")
            elif t in _INLINE_TAGS:
                text = self._inline(node, style).strip()
                if text:
                    rows.extend(ansi.wrap(text, width))
            else:
                self._blocks(node.children, rows, style, indent)

    def _list(
        self, node: _Node, rows: list[str], style: _Style, indent: int, ordered: bool
    ) -> None:
        items = [c for c in node.children if c.tag == "li"]
        for index, item in enumerate(items, 1):
            self._item(item, rows, style, indent, f"{index}." if ordered else "•")

    def _item(self, node: _Node, rows: list[str], style: _Style, indent: int, marker: str) -> None:
        lead = " " * indent
        bullet = f"{theme.current().dim_style}{marker}{ansi.RESET} "
        nested = [c for c in node.children if c.tag in ("ul", "ol")]
        inline_text = self._inline_children(
            _Node(children=[c for c in node.children if c not in nested]), style
        ).strip()
        prefix_w = indent + ansi.width(marker) + 1
        if inline_text:
            lines = ansi.wrap(inline_text, max(8, self.cols - prefix_w))
            rows.append(lead + bullet + lines[0])
            rows.extend(" " * prefix_w + line for line in lines[1:])
        for sub in nested:
            self._list(sub, rows, style, indent + 2, sub.tag == "ol")

    def _pre(self, node: _Node, rows: list[str], style: _Style, indent: int) -> None:
        lang = node.attrs.get("class", "").split()[-1].removeprefix("language-")[:16]
        label = f"─ {lang} " if lang else "─ code "
        fill = "─" * max(0, min(self.cols, 60) - ansi.width(label))
        rows.append(f"{ansi.dim()}{label}{fill}{ansi.RESET}")
        code_style = style.with_(fg="#d6deeb")
        for line in node.content().splitlines() or [""]:
            for chunk in ansi.hard_wrap(line.expandtabs(4), max(8, self.cols - indent)):
                rows.append(code_style.wrap(chunk))
        rows.append(f"{ansi.dim()}{'─' * min(self.cols, 60)}{ansi.RESET}")

    def _table(self, node: _Node, rows: list[str], style: _Style) -> None:
        table_rows: list[list[tuple[str, bool]]] = []
        for tr in (c for c in node.children if c.tag == "tr"):
            cells: list[tuple[str, bool]] = []
            for cell in (c for c in tr.children if c.tag in ("td", "th")):
                cells.append((self._inline_children(cell, style).strip(), cell.tag == "th"))
            if cells:
                table_rows.append(cells)
        if not table_rows:
            return

        columns = max(len(row) for row in table_rows)
        widths = [0] * columns
        for row in table_rows:
            for index, (text, _) in enumerate(row):
                widths[index] = max(widths[index], ansi.width(text))
        total = sum(widths) + 3 * columns + 1
        if total > self.cols:
            excess = total - self.cols
            while excess > 0 and max(widths) > 4:
                widest = widths.index(max(widths))
                widths[widest] -= 1
                excess -= 1

        rule = f"{ansi.dim()}{'─' * min(self.cols, max(8, total))}{ansi.RESET}"
        sep = f" {ansi.dim()}│{ansi.RESET} "
        rows.append(rule)
        for index, row in enumerate(table_rows):
            parts = []
            for col, (text, header) in enumerate(row):
                cell = ansi.clip_ellipsis(text, widths[col])
                cell += " " * max(0, widths[col] - ansi.width(cell))
                if header:
                    cell = f"{ansi.BOLD}{cell}{ansi.DIM_OFF}"
                parts.append(cell)
            rows.append(
                f"{ansi.dim()}│{ansi.RESET} " + sep.join(parts) + f" {ansi.dim()}│{ansi.RESET}"
            )
            if index == 0:
                rows.append(
                    f"{ansi.dim()}│{ansi.RESET}"
                    + f"{ansi.dim()}─┼─{ansi.RESET}".join("─" * w for w in widths)
                    + f"{ansi.dim()}│{ansi.RESET}"
                )
        rows.append(rule)

    def _tidy(self, rows: list[str]) -> list[str]:
        out: list[str] = []
        for row in rows:
            if not row and (not out or not out[-1]):
                continue
            out.append(row)
        while out and not out[-1]:
            out.pop()
        return out


def render(html: str, cols: int) -> list[str]:
    """Render an HTML fragment to styled rows, like ``markdown.render``."""
    return HTMLView(cols).render(html)
