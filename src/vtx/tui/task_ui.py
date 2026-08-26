"""Glyphs, formatters, and renderers for the Task tool UI.

Renders VTX's Task tool block showing running/finished sub-agents with a
visual vocabulary of braille spinners, ``↻`` turn counts, ``·``-separated
stats, and ``⎿`` continuation lines.

All render functions are pure: they take plain data dicts and return
Rich :class:`Text`. No timers, no widget state.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from rich.style import Style
from rich.text import Text

from vtx.coding_agent.config import config

if TYPE_CHECKING:
    from vtx.coding_agent.config import ColorsConfig

# Semantic indicator glyphs.
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
    "badge": "◈",
}

# Braille spinner frames for the animated running indicator.
SPINNER = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

# Tool name -> human-readable action / noun.
TOOL_DISPLAY = {
    "read": "reading",
    "bash": "running command",
    "edit": "editing",
    "write": "writing",
    "grep": "searching",
    "find": "finding files",
    "glob": "finding files",
    "ls": "listing",
    "skill": "running skill",
    "web": "searching web",
    "ask_user": "asking user",
}

TOOL_NOUNS = {
    "read": ("read", "reads"),
    "bash": ("bash", "bash"),
    "edit": ("edit", "edits"),
    "write": ("write", "writes"),
    "grep": ("search", "searches"),
    "find": ("file search", "file searches"),
    "glob": ("file search", "file searches"),
    "ls": ("list", "lists"),
    "skill": ("skill", "skills"),
    "web": ("web search", "web searches"),
    "ask_user": ("user prompt", "user prompts"),
}


def format_tokens(count: int) -> str:
    """Format a token count compactly: "33.8k tokens", "1.2M tokens"."""
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M tokens"
    if count >= 1_000:
        return f"{count / 1_000:.1f}k tokens"
    return f"{count} token{'s' if count != 1 else ''}"


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


def format_tool_breakdown(tool_counts: dict[str, int] | None) -> str:
    """Format breakdown of tool calls: "8 tool calls (4 reads, 3 searches, 1 bash)"."""
    if not tool_counts:
        return "0 tool calls"
    total = sum(tool_counts.values())
    if total == 0:
        return "0 tool calls"

    parts: list[str] = []
    for tool_name, count in tool_counts.items():
        if count <= 0:
            continue
        singular, plural = TOOL_NOUNS.get(tool_name, (tool_name, f"{tool_name}s"))
        noun = singular if count == 1 else plural
        parts.append(f"{count} {noun}")

    if not parts:
        return f"{total} tool call{'s' if total != 1 else ''}"
    return f"{total} tool call{'s' if total != 1 else ''} ({', '.join(parts)})"


def extract_summary_line(text: str, max_chars: int = 90) -> str:
    """Extract a concise 1-line summary snippet from the subagent's answer."""
    if not text:
        return ""
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        # Strip bullets / quotes / markdown formatting
        cleaned = re.sub(r"^([*+-]\s*|>\s*|\d+\.\s*)", "", line).strip()
        cleaned = re.sub(r"[*_`]", "", cleaned).strip()
        if cleaned and len(cleaned) >= 5:
            if len(cleaned) > max_chars:
                return cleaned[: max_chars - 1].rstrip() + "…"
            return cleaned
    return ""


def detect_files_referenced(text: str, transcript: list[str] | None = None) -> int:
    """Count unique file paths referenced in output text and tool transcripts."""
    found: set[str] = set()
    sources = [text]
    if transcript:
        sources.extend(transcript)

    pattern = re.compile(
        r"(?:[\w\-./]+\.(?:py|ts|tsx|js|jsx|json|ya?ml|md|toml|rs|go|c|cpp|h|sh|css|html))\b"
    )
    for source in sources:
        for match in pattern.findall(source):
            clean = match.strip("`'\"(),;:")
            if "/" in clean or clean.endswith((".py", ".ts", ".tsx", ".md", ".json", ".yaml")):
                found.add(clean)
    return len(found)


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


def render_finished(
    stats: dict,
    success: bool | None,
    elapsed_ms: float | None,
    result_text: str = "",
    expanded: bool = False,
) -> Text:
    """Finished agent: outcome icon + stats, then a ⎿ status continuation."""
    colors = config.ui.colors
    stop_label = stats.get("stop_label")
    error_msg = stats.get("error")

    if expanded:
        text = Text()
        icon = (
            GLYPHS["stopped"]
            if stop_label in ("interrupted", "cancelled")
            else (
                GLYPHS["failure"]
                if (success is False or stop_label == "error")
                else GLYPHS["success"]
            )
        )
        icon_style = (
            Style(color=colors.dim)
            if stop_label in ("interrupted", "cancelled")
            else (
                Style(color=colors.failed)
                if (success is False or stop_label == "error")
                else Style(color=colors.success)
            )
        )
        text.append(icon, style=icon_style)
        parts = list(stats_parts(stats))
        if elapsed_ms is not None:
            parts.append(format_ms(elapsed_ms))
        if parts:
            text.append(" " + " · ".join(parts), style=Style(color=colors.dim))

        full_output = result_text or stats.get("final_text", "")
        if full_output:
            lines = full_output.splitlines()
            for line in lines[:60]:
                text.append(f"\n  {line}", style=Style(color=colors.dim))
            if len(lines) > 60:
                text.append(
                    "\n  ... (remaining transcript truncated)", style=Style(color=colors.muted)
                )
        return text

    # Collapsed view: structured summary layout matching enhanced proposal
    text = Text()
    turns = stats.get("turns") or 0
    tool_counts = stats.get("tool_counts") or {}
    tool_breakdown = format_tool_breakdown(tool_counts)
    if not tool_counts and stats.get("tool_uses"):
        tool_uses = stats.get("tool_uses")
        tool_breakdown = f"{tool_uses} tool use{'' if tool_uses == 1 else 's'}"

    # Line 1: Turns + Tool calls breakdown
    turns_str = f"{turns} turn{'s' if turns != 1 else ''}"
    text.append(
        f"  {GLYPHS['sub_line']} {GLYPHS['turns']} {turns_str} · {tool_breakdown}",
        style=Style(color=colors.dim),
    )

    # Line 2: Summary or Error
    if stop_label in ("interrupted", "cancelled"):
        text.append(f"\n  {GLYPHS['sub_line']} Stopped", style=Style(color=colors.dim))
    elif success is False or stop_label == "error":
        msg = f"Error: {error_msg}" if error_msg else "Sub-agent encountered an error."
        text.append(f"\n  {GLYPHS['sub_line']} {msg}", style=Style(color=colors.failed))
    else:
        full_output = result_text or stats.get("final_text", "")
        summary = extract_summary_line(full_output)
        if summary:
            text.append(
                f"\n  {GLYPHS['sub_line']} Summary: {summary}", style=Style(color=colors.dim)
            )
        else:
            detail = "Wrapped up (turn limit)" if stop_label == "length" else "Done"
            text.append(f"\n  {GLYPHS['sub_line']} {detail}", style=Style(color=colors.dim))

    # Line 3: Output & Transcript inspection hint
    full_output = result_text or stats.get("final_text", "")
    transcript = stats.get("transcript") or []
    files_count = detect_files_referenced(full_output, transcript)
    files_prefix = (
        f"{files_count} file{'s' if files_count != 1 else ''} referenced · "
        if files_count > 0
        else ""
    )
    text.append(
        f"\n  {GLYPHS['sub_line']} Output: {files_prefix}[ctrl+] to inspect full transcript]",
        style=Style(color=colors.dim),
    )

    return text


def render_background(task_id: str) -> Text:
    """Background launch acknowledgement line."""
    return Text(
        f"  {GLYPHS['sub_line']}  Running in background (ID: {task_id})",
        style=Style(color=config.ui.colors.dim),
    )
