"""
Context compaction for long sessions.

When token usage exceeds a percentage of the context window, send the full
conversation to the LLM with a summarization prompt, then store the summary
as a CompactionEntry. The session.messages property filters to only show
messages after the compaction point.

Overflow formula:
    total_tokens >= (threshold_percent / 100) * context_window
"""

from ..core.types import Message, TextPart, Usage, UserMessage
from ..llm.base import BaseProvider

SUMMARIZATION_PROMPT = """You are summarizing a coding conversation so that \
another agent can pick up exactly where the previous one left off. \
Output MUST follow this exact structure — no preamble, no extra sections:

## Goal
[One or two sentences: the user's objective. Be specific, not generic.]

## Instructions
- [List EVERY concrete instruction, constraint, or preference the user stated.]
- [Include partial decisions made mid-conversation.]
- [Copy verbatim any plan, spec, or checklist the user provided.]

## Discoveries
- [Bugs found and their root causes.]
- [File paths, function names, class names — use exact identifiers.]
- [Configuration values, environment details, version numbers.]

## Accomplished
- [Bullet list of completed work.]
- [Current work in progress — what was happening RIGHT BEFORE compaction.]
- [Remaining steps, in order.]

## Relevant files
[Exact paths to files read, edited, or created. Group by directory if \
multiple files in the same dir are relevant.]
---"""


def is_overflow(usage: Usage, context_window: int, threshold_percent: float) -> bool:
    if context_window <= 0:
        return False
    count = (
        usage.input_tokens
        + usage.output_tokens
        + usage.cache_read_tokens
        + usage.cache_write_tokens
    )
    return count >= (threshold_percent / 100.0) * context_window


def _calculate_context_tokens(usage: Usage) -> int:
    return (
        usage.input_tokens
        + usage.output_tokens
        + usage.cache_read_tokens
        + usage.cache_write_tokens
    )


async def generate_summary(
    messages: list[Message], provider: BaseProvider, system_prompt: str | None = None
) -> str:
    """Send the full conversation + summarization prompt to the LLM, return summary text."""
    summary_messages: list[Message] = [*messages, UserMessage(content=SUMMARIZATION_PROMPT)]

    stream = await provider.stream(summary_messages, system_prompt=system_prompt, tools=None)

    text_parts: list[str] = []
    async for part in stream:
        if isinstance(part, TextPart):
            text_parts.append(part.text)

    return "".join(text_parts)
