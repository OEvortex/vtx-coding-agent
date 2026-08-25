"""Pi-style glyphs, formatters, and renderers for the Task tool UI.

Ported from gotgenes/pi-packages ``pi-subagents`` (``ui/glyphs.ts``,
``ui/display.ts``, ``tools/result-renderer.ts``) so VTX's Task tool block
renders running/finished sub-agents with the same visual vocabulary:
braille spinners, ``↻`` turn counts, ``·``-separated stats, and ``⎿``
continuation lines.

All render functions are pure: they take plain data dicts and return
Rich :class:`Text`. No timers, no widget state.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.style import Style
from rich.text import Text

from vtx.coding_agent.config import config

if TYPE_CHECKING:
    from vtx.coding_agent.config import ColorsConfig

# Semantic indicator glyphs (see ref/pi-packages .../src/ui/glyphs.ts).
GLYPHS = {
    "turns": "↻",
    "compactions": "⇊",
    "success": "✓",
    "failure": "✗",
    "stopped": "■",
    "sub_line": "⎿",
    "tool_call": "▸",
    "streaming": "◍",
    "queued": "◦",
}

# Braille spinner frames for the animated running indicator.
SPINNER = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

# Tool name -> human-readable action, used for live activity descriptions.
TOOL_DISPLAY = {
    "read": "reading",
    "bash": "running command",
    "edit": "editing",
    "write": "writing",
    "grep": "searching",
    "find": "finding files",
    "glob": "finding files",
    "ls": "listing",
}


def format_tokens(count: int) -> str:
    """Format a token count compactly: "33.8k token", "1.2M token"."""
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M token"
    if count >= 1_000:
        return f"{count / 1_000:.1f}k token"
    return f"{count} token"


def format_turns(turns: int, max_turns: int | None = None) -> str:
    """Format turn count with optional limit: "↻5≤30" or "↻5"."""
    if max_turns:
        return f"{GLYPHS['turns']}{turns}≤{max_turns}"
    return f"{GLYPHS['turns']}{turns}"


def format_ms(ms: float) -> str:
    return f"{ms / 1000:.1f}s"


def short_model(model: str | None) -> str | None:
    if not model:
        return None
    return model.rsplit("/", 1)[-1]


def describe_activity(active_tool: str | None, last_text: str) -> str:
    """Human-readable activity line: current tool action or text snippet."""
    if active_tool:
        return f"{TOOL_DISPLAY.get(active_tool, active_tool)}…"
    snippet = next((line.strip() for line in last_text.splitlines() if line.strip()), "")
    if snippet:
        return snippet[:60] + ("…" if len(snippet) > 60 else "")
    return "thinking…"


def stats_parts(stats: dict) -> list[str]:
    """Ordered stat fields: model · ↻turns · tool uses · tokens."""
    parts: list[str] = []
    model = short_model(stats.get("model"))
    if model:
        parts.append(model)
    turns = stats.get("turns") or 0
    if turns > 0:
        parts.append(format_turns(turns, stats.get("max_turns")))
    tool_uses = stats.get("tool_uses") or 0
    if tool_uses > 0:
        parts.append(f"{tool_uses} tool use{'' if tool_uses == 1 else 's'}")
    tokens = stats.get("tokens") or 0
    if tokens > 0:
        parts.append(format_tokens(tokens))
    return parts


def render_live(stats: dict, frame: int, elapsed_ms: float | None) -> Text:
    """Running agent: spinner + dim stats, then a ⎿ activity continuation."""
    colors: ColorsConfig = config.ui.colors
    parts = list(stats_parts(stats))
    if elapsed_ms is not None:
        parts.append(format_ms(elapsed_ms))

    text = Text()
    spinner = SPINNER[frame % len(SPINNER)]
    text.append(spinner, style=Style(color=colors.accent))
    if parts:
        text.append(" " + " · ".join(parts), style=Style(color=colors.dim))

    activity = describe_activity(stats.get("active_tool"), stats.get("last_text", ""))
    text.append(f"\n  {GLYPHS['sub_line']}  {activity}", style=Style(color=colors.dim))
    return text


def render_finished(stats: dict, success: bool | None, elapsed_ms: float | None) -> Text:
    """Finished agent: outcome icon + stats, then a ⎿ status continuation."""
    colors = config.ui.colors
    stop_label = stats.get("stop_label")

    error_msg = stats.get("error")
    if stop_label in ("interrupted", "cancelled"):
        icon_style = Style(color=colors.dim)
        icon = GLYPHS["stopped"]
        detail = "Stopped"
    elif success is False or stop_label == "error":
        icon_style = Style(color=colors.failed)
        icon = GLYPHS["failure"]
        detail = f"Error: {error_msg}" if error_msg else "Error"
    elif stop_label == "length":
        icon_style = Style(color=colors.notice, bold=True)
        icon = GLYPHS["success"]
        detail = "Wrapped up (turn limit)"
    else:
        icon_style = Style(color=colors.success)
        icon = GLYPHS["success"]
        detail = "Done"

    parts = list(stats_parts(stats))
    if elapsed_ms is not None:
        parts.append(format_ms(elapsed_ms))

    text = Text()
    text.append(icon, style=icon_style)
    if parts:
        text.append(" " + " · ".join(parts), style=Style(color=colors.dim))
    text.append(f"\n  {GLYPHS['sub_line']}  {detail}", style=Style(color=colors.dim))
    return text


def render_background(task_id: str) -> Text:
    """Background launch acknowledgement line."""
    return Text(
        f"  {GLYPHS['sub_line']}  Running in background (ID: {task_id})",
        style=Style(color=config.ui.colors.dim),
    )
