"""Tool guidance section for the system prompt.

The default tool set contributes short usage hints that the model sees
once per session. Each tool exposes a ``prompt_guidelines`` list; we
deduplicate while preserving the first appearance order so the rendered
section stays stable across calls.
"""

from __future__ import annotations

from vtx.ai.agent.tools.base import BaseTool

TOOL_USAGE_HEADER = "# Tool usage"


def build_tool_guidelines_section(tools: list[BaseTool] | None) -> str:
    """Return the ``# Tool usage`` section, or ``""`` when there are none."""
    if not tools:
        return ""

    guidelines: list[str] = []
    seen: set[str] = set()
    for tool in tools:
        # A tool that writes `prompt_guidelines = ("a" "b")` without the trailing
        # comma gets a str, not a tuple, and iterating it yields one bullet per
        # character. Normalize rather than trust: the failure is invisible at the
        # definition site and ruins the whole section at the prompt site.
        raw = tool.prompt_guidelines
        entries = (raw,) if isinstance(raw, str) else raw or ()
        for guideline in entries:
            if guideline in seen:
                continue
            guidelines.append(guideline)
            seen.add(guideline)

    if not guidelines:
        return ""

    return f"{TOOL_USAGE_HEADER}\n\n- " + "\n- ".join(guidelines)


__all__ = ["TOOL_USAGE_HEADER", "build_tool_guidelines_section"]
