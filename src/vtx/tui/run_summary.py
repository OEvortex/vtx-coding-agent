from __future__ import annotations

from dataclasses import dataclass

from rich.text import Text

from vtx.core.config import config


@dataclass
class RunSummaryStats:
    thoughts: int = 0
    tools: int = 0
    edit_additions: int = 0
    edit_removals: int = 0
    failures: int = 0

    @property
    def has_diff(self) -> bool:
        return self.edit_additions > 0 or self.edit_removals > 0


def format_merged_summary_text(stats: RunSummaryStats, glyph: str = "◈") -> Text:
    parts: list[str] = []
    if stats.thoughts > 0:
        noun = "time" if stats.thoughts == 1 else "times"
        parts.append(f"Thought {stats.thoughts} {noun}")

    if stats.tools > 0:
        noun = "tool" if stats.tools == 1 else "tools"
        parts.append(f"Called {stats.tools} {noun}")

    if stats.has_diff:
        parts.append(f"Edit +{stats.edit_additions} -{stats.edit_removals}")

    if stats.failures > 0:
        noun = "failure" if stats.failures == 1 else "failures"
        parts.append(f"{stats.failures} {noun}")

    colors = config.ui.colors
    result = Text()
    result.append(f"{glyph} ", style=f"{colors.accent} bold")

    if not parts:
        result.append("Thought 0 times", style=f"{colors.dim} italic")
        return result

    for i, part in enumerate(parts):
        if i > 0:
            result.append(" · ", style=colors.dim)
        if "Edit +" in part:
            prefix = "Edit "
            result.append(prefix, style=colors.dim)
            rem = part[len(prefix) :]
            add_str, rem_str = rem.split(" ")
            result.append(add_str, style=colors.success)
            result.append(" ")
            result.append(rem_str, style=colors.failed)
        elif "failure" in part:
            result.append(part, style=colors.failed)
        else:
            result.append(part, style=f"{colors.dim} italic")

    return result
