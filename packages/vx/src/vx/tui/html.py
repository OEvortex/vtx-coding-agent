"""HTML plus a small Textual-like CSS subset, rendered to terminal rows.

The API stays the same as the markdown renderer::

    render(html, cols, css="") -> list[str]

Parsing uses only stdlib ``html.parser``; styling uses vx's ANSI/theme helpers.
No browser, no external dependency, no script execution.

HTML: h1-h6, p/div/section/article/header/footer/main, ul/ol/li (nested),
blockquote, pre, table/tr/th/td, hr, br, img, plus the inline tags span, a
(OSC 8 link), b/strong, i/em/cite, u, s/del/strike, code/kbd/samp, mark,
small, sub/sup, abbr, q. HTML entities are decoded.

CSS: ``<style>`` blocks or the ``css=`` argument, with tag / ``.class`` /
``#id`` / compound / descendant selectors, comma lists, specificity,
``!important``, ``var(--name, fallback)`` custom properties, and these
properties: color, background-color, font-weight, font-style,
text-decoration, font-size, text-align, text-transform. Unknown declarations
are ignored. There is no box model, flex/grid, pseudo-class or at-rule
support - this styles terminal text, it does not lay out widgets.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from html import escape as _escape
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

_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_RULE_RE = re.compile(r"([^{}]+)\{([^{}]*)\}")
_VAR_RE = re.compile(r"var\(\s*(--[\w-]+)\s*(?:,\s*([^)]*))?\)")


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


def _align_cell(text: str, width: int, align: str) -> str:
    pad = max(0, width - ansi.width(text))
    if align == "right":
        return " " * pad + text
    if align == "center":
        left = pad // 2
        return " " * left + text + " " * (pad - left)
    return text + " " * pad


def _cell_align(node: _Node, default: str = "left") -> str:
    """The legacy ``align`` attribute or an inline ``text-align`` declaration."""
    align = node.attrs.get("align", "").lower()
    for decl in node.attrs.get("style", "").split(";"):
        if ":" in decl:
            prop, value = (part.strip().lower() for part in decl.split(":", 1))
            if prop == "text-align":
                align = value
    return align if align in ("left", "right", "center") else default


@dataclass(frozen=True)
class _Style:
    fg: str = ""
    bg: str = ""
    bold: bool = False
    italic: bool = False
    underline: bool = False
    strike: bool = False
    dim: bool = False
    align: str = "left"
    transform: str = ""

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


@dataclass(frozen=True)
class _Overrides:
    """A CSS delta: ``None`` means 'leave the inherited value alone'."""

    fg: str | None = None
    bg: str | None = None
    bold: bool | None = None
    italic: bool | None = None
    underline: bool | None = None
    strike: bool | None = None
    dim: bool | None = None
    align: str | None = None
    transform: str | None = None

    def merge(self, other: _Overrides) -> _Overrides:
        changes = {key: value for key, value in vars(other).items() if value is not None}
        return replace(self, **changes)

    def any(self) -> bool:
        return any(value is not None for value in vars(self).values())


def _apply(style: _Style, overrides: _Overrides | None) -> _Style:
    if overrides is None:
        return style
    changes = {key: value for key, value in vars(overrides).items() if value is not None}
    return replace(style, **changes)


def _resolve(value: str, variables: dict[str, str], depth: int = 0) -> str:
    def substitute(match: re.Match[str]) -> str:
        name = match.group(1)
        return variables.get(name, match.group(2) or "")

    resolved = _VAR_RE.sub(substitute, value)
    if depth < 5 and "var(" in resolved:
        return _resolve(resolved, variables, depth + 1)
    return resolved


def _set_property(overrides: _Overrides, prop: str, value: str) -> _Overrides:
    if prop in ("color", "foreground"):
        return replace(overrides, fg=value)
    if prop in ("background-color", "background"):
        return replace(overrides, bg=value)
    if prop == "font-weight":
        return replace(overrides, bold=value in ("bold", "bolder", "700", "800", "900"))
    if prop == "font-style":
        return replace(overrides, italic=value == "italic")
    if prop == "text-decoration":
        if value == "none":
            return replace(overrides, underline=False, strike=False)
        return replace(
            overrides,
            underline=True if "underline" in value else overrides.underline,
            strike=True if "line-through" in value else overrides.strike,
        )
    if prop == "font-size":
        return replace(overrides, dim=value in ("small", "smaller", "75%", "80%"))
    if prop == "text-align" and value in ("left", "right", "center"):
        return replace(overrides, align=value)
    if prop == "text-transform" and value in ("uppercase", "lowercase", "capitalize", "none"):
        return replace(overrides, transform=value)
    return overrides


def _parse_declarations(text: str) -> dict[str, tuple[str, bool]]:
    result: dict[str, tuple[str, bool]] = {}
    for decl in text.split(";"):
        if ":" not in decl:
            continue
        prop, value = (part.strip().lower() for part in decl.split(":", 1))
        important = value.endswith("!important")
        if important:
            value = value[: -len("!important")].strip()
        result[prop] = (value, important)
    return result


def _overrides_from(
    declarations: dict[str, tuple[str, bool]], variables: dict[str, str]
) -> tuple[_Overrides, _Overrides]:
    normal = _Overrides()
    important = _Overrides()
    for prop, (value, is_important) in declarations.items():
        if prop.startswith("--"):
            continue
        target = important if is_important else normal
        updated = _set_property(target, prop, _resolve(value, variables))
        if is_important:
            important = updated
        else:
            normal = updated
    return normal, important


@dataclass(frozen=True)
class _Part:
    tag: str = ""
    ident: str = ""
    classes: frozenset[str] = frozenset()

    def matches(self, node: _Node) -> bool:
        if node.tag is None:
            return False
        if self.tag and self.tag != node.tag:
            return False
        if self.ident and node.attrs.get("id", "") != self.ident:
            return False
        classes = set(node.attrs.get("class", "").split())
        return not self.classes or self.classes <= classes


@dataclass(frozen=True)
class _Selector:
    parts: tuple[_Part, ...]
    specificity: int

    def matches(self, node: _Node) -> bool:
        if not self.parts or not self.parts[-1].matches(node):
            return False
        ancestor = node.parent
        for part in reversed(self.parts[:-1]):
            while ancestor is not None and not part.matches(ancestor):
                ancestor = ancestor.parent
            if ancestor is None:
                return False
            ancestor = ancestor.parent
        return True


def _parse_selector(text: str) -> _Selector | None:
    text = text.strip()
    if not text or text.startswith(("@", ":")):
        return None
    parts: list[_Part] = []
    specificity = 0
    for token in text.split():
        tag, ident, classes = "", "", set()
        for match in re.finditer(r"[.#]?[\w-]+|\*", token):
            piece = match.group(0)
            if piece == "*":
                continue
            if piece.startswith("#"):
                ident, specificity = piece[1:], specificity + 100
            elif piece.startswith("."):
                classes.add(piece[1:])
                specificity += 10
            else:
                tag, specificity = piece.lower(), specificity + 1
        parts.append(_Part(tag, ident, frozenset(classes)))
    return _Selector(tuple(parts), specificity) if parts else None


class _Stylesheet:
    """Minimal CSS: selectors, specificity, ``!important`` and variables."""

    def __init__(self, css: str = "") -> None:
        self.variables: dict[str, str] = {}
        self.rules: list[tuple[_Selector, int, _Overrides, _Overrides]] = []
        self._parse(css)

    def _parse(self, css: str) -> None:
        parsed: list[tuple[str, dict[str, tuple[str, bool]]]] = []
        for match in _RULE_RE.finditer(_COMMENT_RE.sub("", css)):
            parsed.append((match.group(1), _parse_declarations(match.group(2))))
        for _, declarations in parsed:
            for prop, (value, _) in declarations.items():
                if prop.startswith("--"):
                    self.variables[prop] = value
        for order, (selector_text, declarations) in enumerate(parsed):
            normal, important = _overrides_from(declarations, self.variables)
            for raw in selector_text.split(","):
                selector = _parse_selector(raw)
                if selector is not None:
                    self.rules.append((selector, order, normal, important))

    def overrides_for(self, node: _Node) -> tuple[_Overrides, _Overrides]:
        normal: list[tuple[int, int, _Overrides]] = []
        important: list[tuple[int, int, _Overrides]] = []
        for selector, order, ordinary, urgent in self.rules:
            if not selector.matches(node):
                continue
            if ordinary.any():
                normal.append((selector.specificity, order, ordinary))
            if urgent.any():
                important.append((selector.specificity, order, urgent))
        merged = _Overrides()
        for _, _, overrides in sorted(normal, key=lambda rule: (rule[0], rule[1])):
            merged = merged.merge(overrides)
        urgent_merged = _Overrides()
        for _, _, overrides in sorted(important, key=lambda rule: (rule[0], rule[1])):
            urgent_merged = urgent_merged.merge(overrides)
        return merged, urgent_merged

    def declarations(self, text: str) -> tuple[_Overrides, _Overrides]:
        declarations = _parse_declarations(text)
        local = dict(self.variables)
        for prop, (value, _) in declarations.items():
            if prop.startswith("--"):
                local[prop] = _resolve(value, local)
        return _overrides_from(declarations, local)


@dataclass
class _Node:
    tag: str | None = None
    attrs: dict[str, str] = field(default_factory=dict)
    children: list[_Node] = field(default_factory=list)
    text: str = ""
    parent: _Node | None = field(default=None, repr=False)

    def content(self) -> str:
        return self.text if self.tag is None else "".join(c.content() for c in self.children)


class _Tree(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("root")
        self._stack = [self.root]
        self._skip = 0
        self.styles: list[str] = []
        self._style_parts: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "style" and not self._skip:
            self._skip = 1
            self._style_parts = []
            return
        if self._skip or tag in _SKIP_TAGS:
            self._skip += int(tag in _SKIP_TAGS)
            return
        node = _Node(tag, {k: v or "" for k, v in attrs}, parent=self._stack[-1])
        self._stack[-1].children.append(node)
        if tag not in _VOID_TAGS:
            self._stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if not self._skip:
            self._stack[-1].children.append(
                _Node(tag, {k: v or "" for k, v in attrs}, parent=self._stack[-1])
            )

    def handle_endtag(self, tag: str) -> None:
        if tag == "style" and self._style_parts is not None:
            self.styles.append("".join(self._style_parts))
            self._style_parts = None
            self._skip = 0
            return
        if tag in _SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
            return
        for index in range(len(self._stack) - 1, 0, -1):
            if self._stack[index].tag == tag:
                del self._stack[index:]
                return

    def handle_data(self, data: str) -> None:
        if self._style_parts is not None:
            self._style_parts.append(data)
            return
        if not self._skip:
            self._stack[-1].children.append(_Node(text=data, parent=self._stack[-1]))


class HTMLView:
    """Render an HTML fragment into styled terminal rows.

    ``css`` is an optional stylesheet applied after any ``<style>`` tags in
    the fragment; useful when the CSS lives in Python rather than HTML.
    """

    def __init__(self, cols: int, css: str = "") -> None:
        self.cols = max(20, cols)
        self._extra_css = css
        self._sheet = _Stylesheet()
        self._computed: dict[int, _Overrides] = {}

    def render(self, html: str) -> list[str]:
        tree = _Tree()
        tree.feed(html)
        tree.close()
        self._sheet = _Stylesheet("\n".join([*tree.styles, self._extra_css]))
        self._computed = {}
        self._resolve_styles(tree.root.children)
        rows: list[str] = []
        self._blocks(tree.root.children, rows, self._base(), 0)
        return self._tidy(rows)

    def _resolve_styles(self, nodes: list[_Node]) -> None:
        for node in nodes:
            css_normal, css_urgent = self._sheet.overrides_for(node)
            inline_normal, inline_urgent = self._sheet.declarations(node.attrs.get("style", ""))
            self._computed[id(node)] = (
                css_normal.merge(inline_normal).merge(css_urgent).merge(inline_urgent)
            )
            self._resolve_styles(node.children)

    # -- inline -------------------------------------------------------------

    def _base(self) -> _Style:
        return _Style(fg=theme.current().hint_style)

    @staticmethod
    def _transform(text: str, style: _Style) -> str:
        if style.transform == "uppercase":
            return text.upper()
        if style.transform == "lowercase":
            return text.lower()
        if style.transform == "capitalize":
            return text.title()
        return text

    def _inline(self, node: _Node, style: _Style) -> str:
        if node.tag is None:
            text = self._transform(re.sub(r"\s+", " ", node.text), style)
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
        child = _apply(child, self._computed.get(id(node)))

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

    @staticmethod
    def _aligned(rows: list[str], width: int, align: str) -> list[str]:
        if align == "left":
            return rows
        out = []
        for row in rows:
            pad = max(0, width - ansi.width(row))
            left = pad if align == "right" else pad // 2
            out.append(" " * left + row)
        return out

    def _blocks(self, nodes: list[_Node], rows: list[str], style: _Style, indent: int) -> None:
        width = max(8, self.cols - indent)
        for node in nodes:
            if node.tag is None:
                text = self._transform(re.sub(r"\s+", " ", node.text).strip(), style)
                if text:
                    rows.extend(
                        self._aligned(ansi.wrap(style.wrap(text), width), width, style.align)
                    )
                continue

            t = node.tag
            base = style.with_(bold=True) if t in _HEADINGS else style
            own = _apply(base, self._computed.get(id(node)))
            if t in _HEADINGS:
                level = int(t[1])
                head = self._inline_children(node, own)
                if level == 1:
                    head = f"{ansi.UNDERLINE}{head}{ansi.UNDERLINE_OFF}"
                rows.extend(self._aligned(ansi.wrap(head, width), width, own.align))
                if level <= 2:
                    rows.append(f"{ansi.dim()}{'─' * min(width, 64)}{ansi.RESET}")
                rows.append("")
            elif t in ("p", "div", "section", "article", "header", "footer", "main", "span"):
                text = self._inline_children(node, own).strip()
                if text:
                    rows.extend(self._aligned(ansi.wrap(text, width), width, own.align))
                rows.append("")
            elif t in ("ul", "ol"):
                self._list(node, rows, own, indent, t == "ol")
                rows.append("")
            elif t == "li":
                self._item(node, rows, own, indent, "•")
            elif t == "blockquote":
                inner: list[str] = []
                self._blocks(node.children, inner, own.with_(dim=True), 0)
                rail = f"{ansi.dim()}│ {ansi.RESET}"
                for line in inner:
                    rows.append(rail + line if line else rail.rstrip())
                rows.append("")
            elif t == "pre":
                self._pre(node, rows, own, indent)
                rows.append("")
            elif t == "table":
                self._table(node, rows, own)
                rows.append("")
            elif t == "hr":
                rows.append(f"{ansi.dim()}{'─' * min(width, 64)}{ansi.RESET}")
                rows.append("")
            elif t == "br":
                rows.append("")
            elif t == "img":
                rows.append(f"{ansi.dim()}[image: {node.attrs.get('alt', 'image')}]{ansi.RESET}")
            elif t in _INLINE_TAGS:
                text = self._inline(node, own).strip()
                if text:
                    rows.extend(ansi.wrap(text, width))
            else:
                self._blocks(node.children, rows, own, indent)

    def _list(
        self, node: _Node, rows: list[str], style: _Style, indent: int, ordered: bool
    ) -> None:
        items = [c for c in node.children if c.tag == "li"]
        for index, item in enumerate(items, 1):
            self._item(item, rows, style, indent, f"{index}." if ordered else "•")

    def _item(self, node: _Node, rows: list[str], style: _Style, indent: int, marker: str) -> None:
        style = _apply(style, self._computed.get(id(node)))
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
        classes = node.attrs.get("class", "").split()
        lang = classes[-1].removeprefix("language-")[:16] if classes else ""
        label = f"─ {lang} " if lang else "─ code "
        fill = "─" * max(0, min(self.cols, 60) - ansi.width(label))
        rows.append(f"{ansi.dim()}{label}{fill}{ansi.RESET}")
        code_style = style.with_(fg="#d6deeb")
        for line in node.content().splitlines() or [""]:
            for chunk in ansi.hard_wrap(line.expandtabs(4), max(8, self.cols - indent)):
                rows.append(code_style.wrap(chunk))
        rows.append(f"{ansi.dim()}{'─' * min(self.cols, 60)}{ansi.RESET}")

    def _table(self, node: _Node, rows: list[str], style: _Style) -> None:
        """Draw tables like ``markdown.render``: no outer rails, a bold header."""
        table_rows: list[list[tuple[str, bool, str]]] = []
        table_align = _cell_align(node)
        for tr in (c for c in node.children if c.tag == "tr"):
            row_align = _cell_align(tr, table_align)
            cells: list[tuple[str, bool, str]] = []
            for cell in (c for c in tr.children if c.tag in ("td", "th")):
                overrides = self._computed.get(id(cell))
                align = (
                    overrides.align
                    if overrides and overrides.align
                    else _cell_align(cell, row_align)
                )
                cells.append((self._inline_children(cell, style).strip(), cell.tag == "th", align))
            if cells:
                table_rows.append(cells)
        if not table_rows:
            return

        columns = max(len(row) for row in table_rows)
        for row in table_rows:
            row.extend([("", False, "left")] * (columns - len(row)))

        widths = [0] * columns
        for row in table_rows:
            for index, (text, _, _) in enumerate(row):
                widths[index] = max(widths[index], ansi.width(text))
        sep = f" {ansi.dim()}│{ansi.RESET} "
        total = sum(widths) + ansi.width(sep) * (columns - 1)
        while total > self.cols and max(widths) > 3:
            widest = widths.index(max(widths))
            widths[widest] -= 1
            total -= 1

        header = next((i for i, row in enumerate(table_rows) if any(h for _, h, _ in row)), 0)
        for index, row in enumerate(table_rows):
            parts = []
            for col, (text, _, align) in enumerate(row):
                cell = (
                    ansi.clip_ellipsis(text, widths[col])
                    if ansi.width(text) > widths[col]
                    else text
                )
                cell = _align_cell(cell, widths[col], align)
                if index == header:
                    cell = f"{ansi.BOLD}{cell}{ansi.DIM_OFF}"
                parts.append(cell)
            rows.append(sep.join(parts))
            if index == header:
                rule = f"{ansi.dim()}─┼─{ansi.RESET}".join("─" * w for w in widths)
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


def render(html: str, cols: int, css: str = "") -> list[str]:
    """Render an HTML fragment to styled rows, like ``markdown.render``."""
    return HTMLView(cols, css=css).render(html)


# -- high-level, markup-free builders ----------------------------------------


class _Raw(str):
    """Markup that must not be escaped again when nested."""


def raw(fragment: object) -> str:
    """Insert trusted markup verbatim, e.g. ``p(markup=f"{b('x')} ...")``."""
    return _Raw(str(fragment))


def _esc(value: object) -> str:
    if isinstance(value, _Raw):
        return str(value)
    return _escape(str(value), quote=False)


def _tag(name: str, content: object, **attrs: object) -> str:
    parts = []
    for key, value in attrs.items():
        if value is None or value is False:
            continue
        attr = "class" if key in ("cls", "class_") else key.rstrip("_").replace("_", "-")
        parts.append(f' {attr}="{_escape(str(value), quote=True)}"')
    open_tag = f"<{name}{''.join(parts)}>"
    if name in ("img", "hr", "br", "input", "meta", "link"):
        return open_tag
    return f"{open_tag}{_esc(content)}</{name}>"


def _inline(name: str, text: object, attrs: str = "") -> str:
    return _Raw(f"<{name}{attrs}>{_esc(text)}</{name}>")


def _b(text: object) -> str:
    return _inline("b", text)


def _i(text: object) -> str:
    return _inline("i", text)


def _u(text: object) -> str:
    return _inline("u", text)


def _s(text: object) -> str:
    return _inline("s", text)


def _mark(text: object) -> str:
    return _inline("mark", text)


def _small(text: object) -> str:
    return _inline("small", text)


def _code(text: object) -> str:
    return _inline("code", text)


def _span(text: object, style: str = "", cls: str | None = None) -> str:
    attrs = f' class="{_escape(cls, quote=True)}"' if cls else ""
    if style:
        attrs += f' style="{_escape(style, quote=True)}"'
    return _inline("span", text, attrs)


def _link(label: object, url: str, cls: str | None = None) -> str:
    attrs = f' class="{_escape(cls, quote=True)}"' if cls else ""
    return _Raw(f'<a href="{_escape(url, quote=True)}"{attrs}>{_esc(label)}</a>')


b, i, u, s = _b, _i, _u, _s
mark, small, code, span, link = _mark, _small, _code, _span, _link


class CSS:
    """Attachable stylesheet: ``CSS().add(".warn", color="red")``.

    Python keyword names map to CSS properties by turning ``_`` into ``-``.
    Only the properties the renderer understands have any effect.
    """

    def __init__(self, **variables: object) -> None:
        self.variables: dict[str, str] = {}
        self.rules: dict[str, dict[str, str]] = {}
        for name, value in variables.items():
            self.var(name, value)

    def var(self, name: str, value: object) -> CSS:
        key = name if name.startswith("--") else f"--{name}"
        self.variables[key] = str(value)
        return self

    def add(self, selector: str, **properties: object) -> CSS:
        rule = self.rules.setdefault(selector, {})
        for name, value in properties.items():
            rule[name.rstrip("_").replace("_", "-")] = str(value)
        return self

    def to_css(self) -> str:
        root = ""
        if self.variables:
            body = " ".join(f"{name}: {value};" for name, value in self.variables.items())
            root = f":root {{ {body} }}"
        rules = [
            f"{selector} {{ {' '.join(f'{k}: {v};' for k, v in props.items())} }}"
            for selector, props in self.rules.items()
        ]
        return "\n".join([root, *rules])

    def attach(self, html: HTML) -> HTML:
        return html.css(self)


class HTML:
    """Build a document from pieces; no raw HTML required.

    ``HTML(width=80).h1("Title").p("Hello", bold="world").ul(["one", "two"])``

    Sections can also be passed to the constructor (applied in the order
    given): ``HTML(width=80, h1="Title", p=["a", "b"], ul=["x"])``.
    Every builder returns ``self`` so calls can be chained.
    """

    def __init__(self, width: int = 80, css: CSS | str | None = None, **sections: object) -> None:
        self.width = width
        self._css: CSS | str | None = css
        self._parts: list[str] = []
        builders = {
            "h1": lambda value: self.h1(value),
            "h2": lambda value: self.h2(value),
            "h3": lambda value: self.h3(value),
            "p": lambda value: self.paragraphs(value),
            "ul": lambda value: self.ul(value),
            "ol": lambda value: self.ol(value),
            "table": lambda value: self.table(**value),
            "code": lambda value: self.code(value),
            "quote": lambda value: self.quote(value),
        }
        for name, value in sections.items():
            if name not in builders:
                raise ValueError(f"unknown section: {name}")
            builders[name](value)

    # -- document -----------------------------------------------------------

    def css(self, css: CSS | str) -> HTML:
        self._css = css
        return self

    def add(self, fragment: str) -> HTML:
        self._parts.append(fragment)
        return self

    def raw(self, fragment: str) -> HTML:
        return self.add(fragment)

    def to_html(self) -> str:
        return "\n".join(self._parts)

    def render(self) -> list[str]:
        stylesheet = self._css.to_css() if isinstance(self._css, CSS) else (self._css or "")
        return render(self.to_html(), self.width, css=stylesheet)

    def text(self) -> str:
        return "\n".join(self.render())

    # -- blocks -------------------------------------------------------------

    def heading(self, text: object, level: int = 1, cls: str | None = None) -> HTML:
        self._parts.append(_tag(f"h{level}", text, cls=cls))
        return self

    def h1(self, text: object, cls: str | None = None) -> HTML:
        return self.heading(text, 1, cls)

    def h2(self, text: object, cls: str | None = None) -> HTML:
        return self.heading(text, 2, cls)

    def h3(self, text: object, cls: str | None = None) -> HTML:
        return self.heading(text, 3, cls)

    def h4(self, text: object, cls: str | None = None) -> HTML:
        return self.heading(text, 4, cls)

    def h5(self, text: object, cls: str | None = None) -> HTML:
        return self.heading(text, 5, cls)

    def h6(self, text: object, cls: str | None = None) -> HTML:
        return self.heading(text, 6, cls)

    def p(
        self,
        text: object = "",
        *,
        bold: object = None,
        italic: object = None,
        code: object = None,
        link: tuple[object, str] | None = None,
        cls: str | None = None,
        style: str | None = None,
        markup: object = None,
    ) -> HTML:
        body = _esc(text)
        if bold is not None:
            body += " " + _b(bold)
        if italic is not None:
            body += " " + _i(italic)
        if code is not None:
            body += " " + _code(code)
        if link is not None:
            body += " " + _link(link[0], link[1])
        if markup:
            body += " " + str(markup)
        self._parts.append(_tag("p", raw(body), cls=cls, style=style))
        return self

    def paragraphs(self, items: object) -> HTML:
        for item in items if isinstance(items, (list, tuple)) else [items]:
            if isinstance(item, dict):
                self.p(**item)
            else:
                self.p(item)
        return self

    def _list(self, tag: str, items: object, cls: str | None = None) -> HTML:
        def item_markup(item: object) -> str:
            if isinstance(item, dict):
                text = _esc(item.get("text", ""))
                children = item.get("children") or item.get("items") or []
                nested = self._list(tag, children).to_html() if children else ""
                return _tag("li", raw(text + nested), cls=item.get("cls"))
            if isinstance(item, (list, tuple)):
                return _tag("li", raw(_esc(str(item[0])) + self._list(tag, item[1:]).to_html()))
            return _tag("li", item)

        attrs = f' class="{_escape(cls, quote=True)}"' if cls else ""
        items = items if isinstance(items, (list, tuple)) else [items]
        body = "".join(item_markup(item) for item in items)
        self._parts.append(f"<{tag}{attrs}>{body}</{tag}>")
        return self

    def ul(self, items: object, cls: str | None = None) -> HTML:
        return self._list("ul", items, cls)

    def ol(self, items: object, cls: str | None = None) -> HTML:
        return self._list("ol", items, cls)

    def table(
        self,
        headers: list[object],
        rows: list[list[object]],
        cls: str | None = None,
        align: dict[object, str] | None = None,
    ) -> HTML:
        alignment = align or {}
        head = "".join(
            _tag("th", name, align=alignment.get(index, alignment.get(str(name), "left")))
            for index, name in enumerate(headers)
        )
        body = "".join(
            "<tr>"
            + "".join(
                _tag(
                    "td",
                    value,
                    align=alignment.get(
                        index,
                        alignment.get(str(headers[index]) if index < len(headers) else "", "left"),
                    ),
                )
                for index, value in enumerate(row)
            )
            + "</tr>"
            for row in rows
        )
        self._parts.append(_tag("table", raw(f"<tr>{head}</tr>{body}"), cls=cls))
        return self

    def code(self, text: object, lang: str | None = None) -> HTML:
        cls = f"language-{lang}" if lang else None
        self._parts.append(_tag("pre", text, cls=cls))
        return self

    def quote(self, text: object, cls: str | None = None) -> HTML:
        self._parts.append(_tag("blockquote", text, cls=cls))
        return self

    def hr(self) -> HTML:
        self._parts.append("<hr>")
        return self

    def br(self) -> HTML:
        self._parts.append("<br>")
        return self

    def img(self, alt: str, width: int | None = None) -> HTML:
        self._parts.append(_tag("img", "", alt=alt, width=width))
        return self

    def link(self, label: object, url: str, cls: str | None = None) -> HTML:
        self._parts.append(_tag("p", raw(link(label, url, cls))))
        return self
