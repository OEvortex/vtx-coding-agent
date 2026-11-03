from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from rich.text import Text

from vtx.core.config import config

#: How many distinct tool names a summary line spells out before it starts
#: eliding the rest. Four keeps the line readable on a narrow terminal.
MAX_NAMED_TOOLS = 4


@dataclass
class RunSummaryStats:
    thoughts: int = 0
    tools: int = 0
    tool_names: tuple[str, ...] = field(default_factory=tuple)
    edit_additions: int = 0
    edit_removals: int = 0
    failures: int = 0

    @property
    def has_diff(self) -> bool:
        return self.edit_additions > 0 or self.edit_removals > 0


def _format_tool_names(names: tuple[str, ...]) -> str:
    """Compress call order into ``read ×2, grep, bash``.

    Names keep first-call order; a repeated tool is folded into one entry with
    a multiplier so the label stays short without hiding what actually ran.
    """
    counts = Counter(names)
    distinct = list(dict.fromkeys(names))
    shown = []
    for name in distinct[:MAX_NAMED_TOOLS]:
        count = counts[name]
        shown.append(f"{name} ×{count}" if count > 1 else name)
    if len(distinct) > MAX_NAMED_TOOLS:
        shown.append(f"+{len(distinct) - MAX_NAMED_TOOLS} more")
    return ", ".join(shown)


def format_merged_summary_text(stats: RunSummaryStats, glyph: str = "◈") -> Text:
    parts: list[str] = []
    if stats.thoughts > 0:
        noun = "time" if stats.thoughts == 1 else "times"
        parts.append(f"Thought {stats.thoughts} {noun}")

    if stats.tools > 0:
        noun = "tool" if stats.tools == 1 else "tools"
        called = f"Called {stats.tools} {noun}"
        if stats.tool_names:
            called += f" ({_format_tool_names(stats.tool_names)})"
        parts.append(called)

    if stats.has_diff:
        parts.append(f"Edit +{stats.edit_additions} -{stats.edit_removals}")

    if stats.failures > 0:
        noun = "failure" if stats.failures == 1 else "failures"
        parts.append(f"{stats.failures} {noun}")

    colors = config.ui.colors
    result = Text()
    result.append(f"{glyph} ", style=f"{colors.accent} bold")

    if not parts:
        result.append("No activity", style=f"{colors.dim} italic")
        return result

    for i, part in enumerate(parts):
        if i > 0:
            result.append(" · ", style=colors.dim)
        if part.startswith("Edit "):
            result.append("Edit ", style=colors.dim)
            rem = part[len("Edit ") :]
            add_str, rem_str = rem.split(" ")
            result.append(add_str, style=colors.success)
            result.append(" ")
            result.append(rem_str, style=colors.failed)
        elif "failure" in part:
            result.append(part, style=colors.failed)
        else:
            result.append(part, style=f"{colors.dim} italic")

    return result
