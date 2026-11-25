"""Usage summary cards and activity heatmap block."""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from vx.tui import ansi
from vx.tui.blocks.base import DAY_INITIALS, DAY_LABELLED, HEAT_GLYPHS, Block


def _heat_levels(values: list[int]) -> list[int]:
    """Three ascending cut points at the 50th, 75th and 90th percentile.

    Thresholds come from the data's own spread rather than fixed numbers, so a
    history of huge days and one of small days both show a gradient.
    """
    positives = sorted(v for v in values if v > 0)
    if not positives:
        return []
    return [
        positives[min(len(positives) - 1, int(len(positives) * fraction))]
        for fraction in (0.5, 0.75, 0.9)
    ]


def _heat_level(value: int, cuts: list[int]) -> int:
    level = 0
    for cut in cuts:
        if value >= cut:
            level += 1
    return min(level, len(HEAT_GLYPHS) - 1)


#: Card geometry, as ctx draws them: three summary cards in one row when the
#: terminal is wide enough for each to hold a total without clipping.
CARD_MIN_WIDTH = 28
CARD_MAX_WIDTH = 34
CARD_GAP = 2


def _usage_colors() -> Any:
    """ctx's palette, so ``/usage`` matches the reference byte for byte."""
    from ctx.core.config import config

    return config.ui.colors


def _usage_style(role: str, *, bold: bool = False) -> str:
    from vx.tui.theme import _fg

    with suppress(Exception):
        return _fg(getattr(_usage_colors(), role, ""), bold=bold)
    return "\x1b[1m" if bold else ""


def _total_line(usage: Any) -> str:
    """A compact total plus the exact count, so the arithmetic is checkable."""
    from ctx.term.formatting import format_tokens

    total = getattr(usage, "total_tokens", 0) or 0
    return f" {format_tokens(total)} · {total:,}"


def _usage_cells(title: str, usage: Any, inner: int, reset: str) -> list[str]:
    """The four inner rows of one summary card, each padded to ``inner``."""
    from ctx.term.formatting import format_tokens

    border = _usage_style("border")
    strong = _usage_style("fg", bold=True)
    quiet = _usage_style("dim")

    turns = getattr(usage, "turns", 0) or 0
    noun = "turn" if turns == 1 else "turns"
    lines = [
        (f" {_total_line(usage)}", strong),
        (f" {turns:,} {noun}", quiet),
        (
            f" ↑ {format_tokens(getattr(usage, 'input_tokens', 0) or 0)}"
            f"  ↓ {format_tokens(getattr(usage, 'output_tokens', 0) or 0)}"
            f"  ◈ {format_tokens(getattr(usage, 'cache_read_tokens', 0) or 0)}",
            quiet,
        ),
    ]
    out: list[str] = []
    for text, style in lines:
        text = ansi.clip(text, inner)
        fill = " " * max(0, inner - ansi.width(text))
        out.append(f"{border}│{reset}{style}{text}{reset}{fill}{border}│{reset}")
    top_title = ansi.clip(f"─ {title} ", inner)
    fill = "─" * max(0, inner - ansi.width(top_title))
    return [
        f"{border}╭{reset}{strong}{top_title}{reset}{border}{fill}╮{reset}",
        *out,
        f"{border}╰{'─' * inner}╯{reset}",
    ]


def _usage_cards(session: Any, report: Any, width: int) -> list[str]:
    """Session / last 30 days / lifetime, side by side or stacked."""
    reset = ansi.RESET
    summaries = (
        ("Session", session),
        ("Last 30 days", getattr(report, "last_30_days", None)),
        ("Lifetime", getattr(report, "lifetime", None)),
    )
    if all(usage is None for _, usage in summaries):
        return []
    summaries = tuple(
        (title, usage if usage is not None else _empty_usage()) for title, usage in summaries
    )

    if width >= CARD_MIN_WIDTH * 3 + CARD_GAP * 2:
        card_w = min(CARD_MAX_WIDTH, max(CARD_MIN_WIDTH, (width - CARD_GAP * 2) // 3))
        cards = [_usage_cells(title, usage, card_w - 2, reset) for title, usage in summaries]
        gap = " " * CARD_GAP
        return [gap.join(card[row] for card in cards) for row in range(len(cards[0]))]

    card_w = min(CARD_MAX_WIDTH, max(CARD_MIN_WIDTH, width - 2))
    out: list[str] = []
    for title, usage in summaries:
        if out:
            out.append("")
        out += _usage_cells(title, usage, card_w - 2, reset)
    return out


def _empty_usage() -> Any:
    from ctx.term.usage_stats import DailyUsage

    return DailyUsage()


def _usage_columns(report: Any, days: int, width: int) -> tuple[list[Any], Any]:
    end = getattr(report, "as_of", None) or date.today()
    start = end - timedelta(days=days - 1)
    start -= timedelta(days=(start.weekday() + 1) % 7)
    window_start = end - timedelta(days=days - 1)
    columns = [start + timedelta(days=7 * week) for week in range((end - start).days // 7 + 1)]
    fit = max(6, (width - 4 - 2) // 2)
    if len(columns) > fit:
        columns = columns[-fit:]
        window_start = max(window_start, columns[0])
    return columns, window_start


def _usage_heatmap(report: Any, days: int, width: int) -> list[str]:
    """The activity grid: weeks as columns, weekdays as rows."""
    end = getattr(report, "as_of", None) or date.today()
    columns, window_start = _usage_columns(report, days, width)
    day_map = getattr(report, "days", None) or {}
    cuts = _heat_levels([getattr(usage, "total_tokens", 0) for usage in day_map.values()])
    reset = ansi.RESET
    strong = _usage_style("fg", bold=True)
    quiet = _usage_style("dim")
    zero_shade = _usage_style("border")

    if days <= 31:
        subtitle = "Token activity · last 30 days"
    elif len(columns) >= 50:
        subtitle = "Token activity · last 12 months"
    else:
        months = max(1, round((end - columns[0]).days / 30.4))
        subtitle = f"Token activity · last {months} months"

    out = [f"{strong}{subtitle}{reset}", ""]

    # Month labels sit over the week each month starts in, when they fit.
    header = [" "] * (4 + len(columns) * 2)
    last_month = ""
    next_allowed = 4
    for index, week_start in enumerate(columns):
        month = ""
        for offset in range(7):
            visible = week_start + timedelta(days=offset)
            if window_start <= visible <= end:
                month = visible.strftime("%b")
                break
        if not month or month == last_month:
            continue
        position = 4 + index * 2
        if position < next_allowed:
            continue
        while len(header) < position + len(month):
            header.append(" ")
        for offset, char in enumerate(month):
            header[position + offset] = char
        next_allowed = position + len(month) + 1
        last_month = month
    out.append(f"{quiet}{''.join(header).rstrip()}{reset}")

    for weekday in range(7):
        label = f"  {DAY_INITIALS[weekday]} " if weekday in DAY_LABELLED else "    "
        cells: list[str] = []
        for week_start in columns:
            day = week_start + timedelta(days=weekday)
            if day < window_start or day > end:
                cells.append("  ")
                continue
            usage = day_map.get(day)
            total = getattr(usage, "total_tokens", 0) if usage is not None else 0
            level = _heat_level(total, cuts)
            shade = _heat_shade(level) if level else zero_shade
            cells.append(f"{shade}{HEAT_GLYPHS[level]}{reset} ")
        out.append(f"{quiet}{label}{reset}{''.join(cells)}".rstrip())

    legend = "".join(
        f"{_heat_shade(i) if i else zero_shade}{HEAT_GLYPHS[i]}{reset} "
        for i in range(len(HEAT_GLYPHS))
    )
    out.append(f"    {quiet}less {reset}{legend}{quiet}more{reset}")
    return out


def _heat_shade(level: int) -> str:
    """Blends of the background toward the accent, one per heat level."""
    from ctx.term.styles import _blend_hex

    from vx.tui.theme import _fg

    weights = (0.0, 0.25, 0.45, 0.7, 1.0)
    with suppress(Exception):
        colors = _usage_colors()
        return _fg(_blend_hex(colors.bg, colors.accent, overlay_weight=weights[level]))
    return _usage_style("accent")


@dataclass
class Usage(Block):
    """ctx's ``/usage``: summary cards over a year of token activity."""

    session: Any = None
    report: Any = None
    days: int = 365
    kind: str = "notice"

    def rows(self, width: int) -> list[str]:
        if self.report is None:
            return []
        reset = ansi.RESET
        out = [f"{_usage_style('fg', bold=True)}Token usage{reset}", ""]
        cards = _usage_cards(self.session, self.report, width)
        if cards:
            out += cards
            out.append("")
        out += _usage_heatmap(self.report, self.days, width)
        return out
