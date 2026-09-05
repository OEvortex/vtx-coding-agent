"""Session recap: draft a short "where you left off" summary after the user
has been idle or resumes a session.

The recap is a cheap one-off LLM call over a trimmed view of the recent
conversation (same pattern as compaction's ``generate_summary``), rendered as
a concise 1-2 sentence summary of the high-level task and progress so far.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from vtx.core.abc import BaseProvider
from vtx.core.types import (
    AssistantMessage,
    ImageContent,
    Message,
    TextContent,
    TextPart,
    ToolResultMessage,
    UserMessage,
)

# How many trailing messages to send to the recap model.
RECENT_MESSAGE_WINDOW = 30
# Tool results are truncated to head+tail edges before being sent.
TOOL_RESULT_EDGE_CHARS = 2000
INITIAL_TASK_EDGE_CHARS = 4000

RECAP_PROMPT = (
    "The user stepped away and is coming back. Write a concise 1-2 sentence "
    "summary of the high-level task and what has been done so far. "
    "Focus only on summarizing progress and current state. "
    "Do not suggest next steps, headers, bullet points, or commit recaps."
)


def message_text(message: Message) -> str:
    if isinstance(message, UserMessage):
        if isinstance(message.content, str):
            return message.content
        return "\n".join(part.text for part in message.content if isinstance(part, TextContent))
    if isinstance(message, AssistantMessage):
        return "".join(part.text for part in message.content if isinstance(part, TextContent))
    return ""


def _truncate_edges(text: str) -> str:
    limit = INITIAL_TASK_EDGE_CHARS * 2
    if len(text) <= limit:
        return text
    return (
        f"{text[:INITIAL_TASK_EDGE_CHARS]}\n… [middle omitted for recap] …\n"
        f"{text[-INITIAL_TASK_EDGE_CHARS:]}"
    )


@dataclass
class RecapContext:
    """Trimmed conversation view plus edge context for recap generation."""

    messages: list[Message] = field(default_factory=list)
    broader_context: str | None = None


def build_recap_context(
    messages: list[Message], initial_task: str | None = None, compaction_summary: str | None = None
) -> RecapContext:
    # Truncate oversized tool result texts so a single huge read does not
    # dominate the recap prompt.
    tool_limit = TOOL_RESULT_EDGE_CHARS * 2
    trimmed: list[Message] = []
    for message in messages:
        if not isinstance(message, ToolResultMessage):
            trimmed.append(message)
            continue
        needs_copy = any(
            isinstance(part, TextContent) and len(part.text) > tool_limit
            for part in message.content
        )
        if not needs_copy:
            trimmed.append(message)
            continue
        content: list[TextContent | ImageContent] = []
        for part in message.content:
            if isinstance(part, TextContent) and len(part.text) > tool_limit:
                content.append(
                    TextContent(
                        text=(
                            f"{part.text[:TOOL_RESULT_EDGE_CHARS]}"
                            "\n… [tool result truncated for recap] …\n"
                            f"{part.text[-TOOL_RESULT_EDGE_CHARS:]}"
                        )
                    )
                )
            else:
                content.append(part)
        trimmed.append(
            ToolResultMessage(
                tool_call_id=message.tool_call_id,
                tool_name=message.tool_name,
                content=content,
                is_error=message.is_error,
            )
        )

    start = max(0, len(trimmed) - RECENT_MESSAGE_WINDOW)
    while start > 0 and isinstance(trimmed[start], ToolResultMessage):
        start -= 1
    recent = trimmed[start:]
    if recent and isinstance(recent[0], AssistantMessage):
        recent = [UserMessage(content="(Earlier conversation omitted.)"), *recent]

    broader: list[str] = []
    recent_texts = {message_text(m).strip() for m in recent}
    if initial_task and initial_task.strip() not in recent_texts:
        broader.append(f"Initial user request:\n{_truncate_edges(initial_task)}")
    if compaction_summary:
        broader.append(f"Session summary:\n{compaction_summary}")

    return RecapContext(messages=recent, broader_context="\n\n".join(broader) if broader else None)


def has_meaningful_activity(messages: list[Message]) -> bool:
    """True when anything substantive happened since the last user message.

    Any non-empty assistant text counts immediately. Tool calls require at
    least 3 invocations so recap is not drafted after a single lookup.
    """
    MIN_TOOL_CALLS = 3
    last_user_idx = -1
    for i in range(len(messages) - 1, -1, -1):
        if isinstance(messages[i], UserMessage):
            last_user_idx = i
            break

    tail = messages[last_user_idx + 1 :] if last_user_idx >= 0 else messages
    tool_call_count = 0
    for message in tail:
        if isinstance(message, AssistantMessage):
            if message_text(message).strip():
                return True
            tool_call_count += sum(
                1 for part in message.content if getattr(part, "type", None) == "tool_call"
            )
        elif isinstance(message, ToolResultMessage):
            tool_call_count += 1
        if tool_call_count >= MIN_TOOL_CALLS:
            return True
    return False


async def generate_recap(context: RecapContext, provider: BaseProvider) -> str | None:
    prompt = RECAP_PROMPT
    if context.broader_context:
        prompt = f"Broader session context:\n{context.broader_context}\n\n{prompt}"

    llm_messages: list[Message] = [*context.messages, UserMessage(content=prompt)]
    stream = await provider.stream(llm_messages, system_prompt=None, tools=None)

    text_parts: list[str] = []
    async for part in stream:
        if isinstance(part, TextPart):
            text_parts.append(part.text)

    text = " ".join("".join(text_parts).split()).strip()
    return text or None
