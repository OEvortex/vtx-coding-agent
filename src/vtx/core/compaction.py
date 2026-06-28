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

SUMMARIZATION_PROMPT = """Provide a detailed prompt for continuing our \
conversation above. Focus on information that would be helpful for \
continuing the conversation, including what we did, what we're doing, \
which files we're working on, and what we're going to do next. \
The summary that you construct will be used so that another agent \
can read it and continue the work.

When constructing the summary, try to stick to this template:
---
## Goal

[What goal(s) is the user trying to accomplish?]

## Instructions

- [What important instructions did the user give you that are relevant]
- [If there is a plan or spec, include information about it
  so next agent can continue using it]

## Discoveries

[What notable things were learned during this conversation that would
be useful for the next agent to know when continuing the work]

## Accomplished

[What work has been completed, what work is still in progress,
and what work is left?]

## Relevant files / directories

[Construct a structured list of relevant files that have been read,
edited, or created that pertain to the task at hand. If all the files
in a directory are relevant, include the path to the directory.]
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
