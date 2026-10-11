"""Compare strip_markdown_for_collapsed_text against markdown-it + Rich rendering."""

from markdown_it import MarkdownIt
from rich.console import Console
from rich.text import Text

from vtx.tui.formatting import format_markdown, strip_markdown_for_collapsed_text

md = MarkdownIt("commonmark")

CASES = [
    "L_O_L",
    "snake_case_name",
    "__init__.py",
    "def foo_bar(baz_qux):",
    "some/path/my_file_name.py",
    "_italic_",
    "__bold__",
    "**bold**",
    "*italic*",
    "a _italic_ b",
    "a __bold__ b",
    "(*paren*)",
    "`_code_`",
    "```\n_a_ stays\n```",
    "***both***",
    "a*b*c",
    "**L_O_L**",
    "**bold** and _italic_",
    "2 * 3 * 4",
    "trailing_",
    "_leading",
    "a ** b ** c",
    "____",
    "**",
    "_",
    "MULTI_word_identifier END",
    "**bold**snake_case**bold**",
    "__dunder__",
    "self._private_method()",
    "some _text with_ spaces",
    "foo_bar",
]

console = Console(force_terminal=False, no_color=True, width=200)


def reference(raw: str) -> str:
    """The text a real CommonMark parser shows, with markup resolved."""
    out = []
    for tok in md.parse(raw):
        if tok.type == "inline":
            for child in tok.children or ():
                if child.type in ("text", "code_inline"):
                    out.append(child.content)
                elif child.type == "softbreak":
                    out.append("\n")
                elif child.type == "hardbreak":
                    out.append("\n")
                elif child.type == "html_inline":
                    out.append(child.content)
        elif tok.type == "fence":
            out.append(tok.content)
        elif tok.type == "code_block":
            out.append(tok.content)
    return "".join(out)


fails = 0
for case in CASES:
    ours = strip_markdown_for_collapsed_text(case)
    ref = reference(case)
    rich_plain = format_markdown(case, width=200).plain.strip()
    status = "ok " if ours == ref else "DIFF"
    if ours != ref:
        fails += 1
    print(f"{status} {case!r}")
    print(f"      ours={ours!r}")
    print(f"      ref ={ref!r}")
    print(f"      rich={rich_plain!r}")

print(f"\n{fails} differences")
