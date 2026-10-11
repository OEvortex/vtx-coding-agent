"""Fuzz strip_markdown_for_collapsed_text against markdown-it.

Scoped to what the function actually does: one line of prose flattened to its
words. Block structure (fences, headings, lists) is handled elsewhere.
"""

import random
import sys

from markdown_it import MarkdownIt

from vtx.tui.formatting import strip_markdown_for_collapsed_text

md = MarkdownIt("commonmark")

ALPHABET = list("ab *_~.()1")


def reference(raw: str) -> str:
    out = []
    for tok in md.parse(raw):
        if tok.type == "inline":
            for child in tok.children or ():
                if child.type in ("text", "code_inline"):
                    out.append(child.content)
                elif child.type == "html_inline":
                    out.append(child.content)
        elif tok.type not in ("paragraph_open", "paragraph_close"):
            return None  # block structure we do not model
    return "".join(out)


rng = random.Random(1234)
bad = []
checked = 0
N = 300_000
for _ in range(N):
    raw = "".join(rng.choice(ALPHABET) for _ in range(rng.randint(1, 14)))
    if raw != raw.strip():
        continue  # paragraph rendering trims edges; this function does not
    ref = reference(raw)
    if ref is None:
        continue
    checked += 1
    ours = strip_markdown_for_collapsed_text(raw)
    if ours != ref:
        bad.append((raw, ours, ref))

print(f"{checked} single-line cases, {len(bad)} mismatches vs markdown-it")
for raw, ours, ref in bad[:40]:
    print(f"  {raw!r}\n    ours={ours!r}\n    ref ={ref!r}")

sys.exit(1 if bad else 0)
