"""The instant first frame.

``vx`` used to spend about a second importing ``ctx`` before a single cell was
painted, so the terminal sat empty. This module paints the same shell the app
paints (welcome line, rules, composer, hint row) using only ``vx.tui``, and the
real frame simply replaces it once the runtime is up.
"""

from __future__ import annotations

from vx.brand import ITALIC_BRAND, VERSION
from vx.tui import ansi, footer, theme
from vx.tui.canvas import Painter
from vx.tui.terminal import Terminal


def _welcome_rows(width: int) -> list[str]:
    """The welcome line, drawn without importing ``vx.tui.blocks`` (~55 ms).

    ``blocks.Welcome`` renders exactly this; the splash is the one frame that
    must be on screen before anything else loads, so it repeats the two lines
    rather than paying for the block library first.
    """
    t = theme.current()
    return ansi.wrap(
        f"{t.subtitle_style}{ITALIC_BRAND}{ansi.RESET}"
        f"{t.dim_style} v{VERSION} · Run /help for commands{ansi.RESET}",
        width,
    )


def paint(term: Terminal, *, cwd: str, note: str = "starting") -> Painter:
    """Draw the boot frame and return the painter it left behind."""
    theme.apply_theme()
    painter = Painter()
    rows, cols = term.size()
    cols = max(20, cols)
    rows = max(4, rows)

    composer, _caret_row, _caret_col = footer.composer_rows("", 0, cols, 1)
    hint = footer.hint_row(
        cols=cols,
        permission="",
        model="",
        effort="",
        context_used=0,
        context_total=None,
        workspace=cwd,
        branch=None,
        right=note,
    )
    foot = [footer.rule(cols), *composer, footer.rule(cols), hint]
    area = max(1, rows - len(foot))

    welcome = _welcome_rows(cols)
    transcript = welcome + [""] * (area - len(welcome))

    frame = transcript + foot
    frame = frame[:rows] if len(frame) > rows else frame + [""] * (rows - len(frame))
    term.write(painter.paint(frame, cols, None))
    return painter
