"""Session recap: draft a short "where you left off" summary after the user
has been idle or resumes a session.

The recap is a cheap one-off LLM call over a trimmed view of the recent
conversation (same pattern as compaction's ``generate_summary``), rendered as
a concise 1-2 sentence summary of the high-level task and progress so far.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ctx.protocol.abc import BaseProvider
from ctx.protocol.types import (
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

# The digest keeps prose (what was said) and cuts bodies (what tools returned
# to say it): a recap is one or two sentences about the work, and a file the
# tools read almost never belongs in them.
DIGEST_MESSAGE_WINDOW = 40
DIGEST_TEXT_CHARS = 320
DIGEST_ARGUMENT_CHARS = 120
DIGEST_RESULT_CHARS = 200
DIGEST_MAX_CHARS = 4000

#: The recap is an aside about the session, not the assistant continuing it:
#: "describe" and "do not address the reader" are what keep the prose in the
#: third person. Measured against three wordings on real sessions, this one
#: drew no first-person voice and never came back empty.
RECAP_PROMPT = (
    "Summarize this session for someone picking the work back up: in one or two "
    "sentences, state what was asked and what now stands. "
    "Describe the session; do not speak in the first person and do not address "
    "the reader. "
    "No next steps, headers, bullets, or commit summaries."
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


def _squash(text: str) -> str:
    return " ".join(text.split())


def _clip(text: str, limit: int) -> str:
    text = _squash(text)
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


def _argument_line(arguments: dict[str, object] | None) -> str:
    """One identifying argument, so `read` still says which file."""
    if not arguments:
        return ""
    preferred = (
        "path",
        "file_path",
        "command",
        "pattern",
        "query",
        "url",
        "name",
        "description",
        "prompt",
    )
    value = next((arguments[key] for key in preferred if arguments.get(key)), None)
    if value is None:
        value = next((item for item in arguments.values() if item), None)
    return _clip(str(value), DIGEST_ARGUMENT_CHARS) if value is not None else ""


def _result_line(message: ToolResultMessage) -> str:
    text = "\n".join(
        part.text for part in message.content if isinstance(part, TextContent)
    ).strip()
    first = text.splitlines()[0] if text else ""
    if message.is_error:
        return _clip(f"error: {first}" if first else "error", DIGEST_RESULT_CHARS)
    return _clip(first, DIGEST_RESULT_CHARS)


def build_recap_digest(
    messages: list[Message], initial_task: str | None = None, compaction_summary: str | None = None
) -> RecapContext:
    """The same conversation as one flat page: prose kept, tool output cut.

    Full transcripts ask the model to re-read work it already narrated. A
    digest asks it to read the shape of the session - what the user wanted,
    what was said, which tools touched what - which is what a 1-2 sentence
    recap is actually about, at a fraction of the tokens.
    """
    recent = messages[-DIGEST_MESSAGE_WINDOW:]
    results = {
        message.tool_call_id: _result_line(message)
        for message in recent
        if isinstance(message, ToolResultMessage)
    }

    lines: list[str] = []
    for message in recent:
        if isinstance(message, UserMessage):
            text = _clip(message_text(message), DIGEST_TEXT_CHARS)
            if text:
                lines.append(f"user: {text}")
        elif isinstance(message, AssistantMessage):
            text = _clip(message_text(message), DIGEST_TEXT_CHARS)
            if text:
                lines.append(f"assistant: {text}")
            for part in message.content:
                if getattr(part, "type", None) != "tool_call":
                    continue
                call = part
                target = _argument_line(getattr(call, "arguments", None))
                result = results.get(getattr(call, "id", ""), "")
                name = getattr(call, "name", "tool")
                line = f"tool: {name}({target})" if target else f"tool: {name}"
                lines.append(f"{line} -> {result}" if result else line)

    dropped = 0
    while lines and len("\n".join(lines)) > DIGEST_MAX_CHARS:
        lines.pop(0)
        dropped += 1
    if dropped:
        lines.insert(0, f"({dropped} earlier entries omitted.)")

    digest = "\n".join(lines)
    broader: list[str] = []
    if initial_task and initial_task.strip() and initial_task.strip() not in digest:
        broader.append(f"Initial user request:\n{_truncate_edges(initial_task)}")
    if compaction_summary:
        broader.append(f"Session summary:\n{compaction_summary}")

    body = f"Session so far (most recent last):\n{digest}" if digest else ""
    return RecapContext(
        messages=[UserMessage(content=body)] if body else [],
        broader_context="\n\n".join(broader) if broader else None,
    )


def has_meaningful_activity(messages: list[Message]) -> bool:
    """True when substantive work happened since the last user message.

    Requires either enough assistant text (so short acks like "done" don't
    trigger a recap) or at least 3 tool invocations (so a single lookup
    doesn't trigger one).
    """
    min_tool_calls = 3
    min_assistant_chars = 150
    last_user_idx = -1
    for i in range(len(messages) - 1, -1, -1):
        if isinstance(messages[i], UserMessage):
            last_user_idx = i
            break

    tail = messages[last_user_idx + 1 :] if last_user_idx >= 0 else messages
    tool_call_count = 0
    assistant_chars = 0
    for message in tail:
        if isinstance(message, AssistantMessage):
            assistant_chars += len(message_text(message).strip())
            tool_call_count += sum(
                1 for part in message.content if getattr(part, "type", None) == "tool_call"
            )
        elif isinstance(message, ToolResultMessage):
            tool_call_count += 1
        if tool_call_count >= min_tool_calls:
            return True
    return assistant_chars >= min_assistant_chars


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
