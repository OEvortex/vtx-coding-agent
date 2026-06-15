"""Tool guidance section for the system prompt.

The default tool set contributes short usage hints (e.g. "Use read to
view files") that the model sees once per session. Each tool exposes a
``prompt_guidelines`` list; we deduplicate while preserving the first
appearance order so the rendered section stays stable across calls.
"""

from __future__ import annotations

from ..tools import BaseTool

TOOL_USAGE_HEADER = "# Tool usage"


def build_tool_guidelines_section(tools: list[BaseTool] | None) -> str:
    """Return the ``# Tool usage`` section, or ``""`` when there are none."""
    if not tools:
        return ""

    guidelines: list[str] = []
    seen: set[str] = set()
    for tool in tools:
        for guideline in tool.prompt_guidelines:
            if guideline in seen:
                continue
            guidelines.append(guideline)
            seen.add(guideline)

    if not guidelines:
        return ""

    return f"{TOOL_USAGE_HEADER}\n\n- " + "\n- ".join(guidelines)


__all__ = ["TOOL_USAGE_HEADER", "build_tool_guidelines_section"]
