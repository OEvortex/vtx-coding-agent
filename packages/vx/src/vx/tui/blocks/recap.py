"""The session recap: where you left off, in the frame vx already owns.

A recap is the one block that is not part of the run -- it is a note about the
session, written between turns. Drawing it in the same box as ``/usage`` and
the terminal drawer makes that read at a glance: the rules carry the title, the
reason it appeared and the change totals, so the body is spent on prose.

The body goes through the markdown renderer, so a recap reads like a reply
rather than a report, and the box is two columns short of the transcript so it
never touches the panel's rule.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from vx.tui import ansi, markdown, theme
from vx.tui import panels as _panels
from vx.tui.blocks.base import Block

#: The box is this much narrower than the transcript it sits in.
SLACK = 2
#: A box narrower than this has no room for a rule and a corner.
MIN_WIDTH = 24
#: What the spinner turns while the draft is being written.
FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


@dataclass
class RecapCard(Block):
    """``╭─ ✦ recap ─── you asked ─╮`` with the summary inside.

    ``note`` is the why: ``you asked`` for ``/recap``, or how long the session
    was quiet before drafting one. ``drafting`` draws the same box mid-flight,
    so the card does not change shape when the text lands.
    """

    text: str = ""
    note: str = ""
    files: int = 0
    added: int = 0
    removed: int = 0
    drafting: bool = False
    #: When the draft started, so the spinner moves while the call runs.
    started: float = 0.0
    kind: str = "recap"

    def _title(self) -> str:
        t = theme.current()
        if self.drafting:
            index = int((time.monotonic() - self.started) * 10) % len(FRAMES)
            return f"{t.dim_style}{FRAMES[index]}{ansi.RESET} recap"
        return f"{t.tag_style}✦{ansi.RESET} {ansi.BOLD}recap{ansi.RESET}"

    def cacheable(self) -> bool:
        # The drafting box turns a spinner on the clock, so it cannot be held
        # still by the row cache; the settled card can.
        return not self.drafting

    def _facts(self) -> str:
        """``edited 2 files · +130 -20``: the totals, in a diff's two colours."""
        if not self.files:
            return ""
        t = theme.current()
        noun = "file" if self.files == 1 else "files"
        out = f"{t.dim_style}edited {self.files} {noun} · {ansi.RESET}"
        out += f"{t.diff_added_style}+{self.added}{ansi.RESET}"
        if self.removed:
            out += f" {t.diff_removed_style}-{self.removed}{ansi.RESET}"
        return out

    @staticmethod
    def _foot(facts: str, width: int) -> str:
        """``╰─ facts ──────╯``: the totals ride in the rule, not a body row."""
        t = theme.current()
        left = f"─ {facts} " if facts else "─"
        fill = "─" * max(0, width - 2 - ansi.width(left))
        return f"{t.dim_style}╰{ansi.RESET}{left}{t.dim_style}{fill}╯{ansi.RESET}"

    @staticmethod
    def _cell(text: str, width: int) -> str:
        """``│ … │``: one body row, padded or clipped to the frame."""
        t = theme.current()
        body = ansi.clip(text, max(1, width - 4))
        pad = " " * max(0, width - 4 - ansi.width(body))
        return f"{t.dim_style}│{ansi.RESET} {body}{pad} {t.dim_style}│{ansi.RESET}"

    def rows(self, width: int) -> list[str]:
        box = max(MIN_WIDTH, width - SLACK)
        if self.drafting:
            note = self.note or "drafting"
            return [
                _panels._ruled("╭─ ", self._title(), note, box),
                self._cell(f"{theme.current().dim_style}reading the session…{ansi.RESET}", box),
                self._foot("", box),
            ]
        inner = max(1, box - 4)
        body = markdown.render(self.text, inner) or [""]
        return [
            _panels._ruled("╭─ ", self._title(), self.note, box),
            *(self._cell(row, box) for row in body),
            self._foot(self._facts(), box),
        ]
