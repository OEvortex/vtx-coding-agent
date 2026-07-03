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
Your summary MUST be comprehensive, high-fidelity, and detailed. Do NOT compress \
information so aggressively that critical technical context, logic, decisions, or \
code details are lost. A coding agent needs precise context to continue effectively.

Output MUST follow this exact structure — no preamble, no extra sections:

## Goal & Requirements
- [The user's objective and core requirements. Be specific, detailed, and non-generic.]
- [Copy verbatim any concrete checklists, plans, specs, or task rules provided by the user.]
- [List all user preferences, constraints, and instructions.]

## Technical Architecture & Context
- [Key discoveries about the codebase: architecture, modules, classes, and environment details.]
- [API / Interface Signatures: Exact signatures, types, or models designed, created, or modified.]
- [Key Algorithms / Logic: Detailed description of any complex logic or custom protocols \
implemented.]

## Decisions Made & Rationale
- [Key design and architectural choices made mid-conversation, with brief explanations.]
- [Rejected Alternatives: Explicitly detail any paths investigated but rejected, and WHY they \
were rejected (to prevent the next agent iteration from repeating the same investigation).]

## Troubleshooting & Debugging
- [Exact bugs encountered, stack traces, error messages, and their root causes.]
- [The specific fixes implemented and why they resolved the issue.]

## Accomplished & Active State
- [Bullet list of completed work items.]
- [Current work in progress: The exact state of the system right before compaction \
(e.g., active files being edited, current task focus, or compile/test issues).]
- [Verification & Testing: Test commands run, new tests added, and latest test status \
(passing/failing).]

## Action Plan
- [Immediate next 2-3 specific developer actions.]
- [Remaining checklist items and TODOs in order.]

## Relevant Files
- [Exact paths to files read, created, or edited. Group by directory. Include brief notes on \
their roles.]
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
