"""Per-iteration message repair before each model call.

A minimal, correct counterpart to ``vtx_claw``'s ``ContextGovernor``:
strip tool-result messages that have no matching assistant tool call (orphans
left by cancelled/partial turns) and enforce a soft budget on tool-result text
so a single huge tool output can't blow the context window. Offloaded results
are summarized in place rather than dropped.

This is a pure function over a message list — no I/O, no provider calls — so
it's safe to run every iteration and easy to test.
"""

from __future__ import annotations

from .core.types import (
    AssistantMessage,
    ImageContent,
    Message,
    TextContent,
    ToolCall,
    ToolResultMessage,
)

# ponytail: naive global character budget; tune or make configurable later.
_MAX_TOOL_RESULT_CHARS = 200_000


def _tool_call_ids(assistant: AssistantMessage) -> set[str]:
    return {tc.id for tc in assistant.content if isinstance(tc, ToolCall)}


def prepare_for_model(messages: list[Message]) -> list[Message]:
    """Return a repaired copy of ``messages`` ready for the model.

    - Drops ``ToolResultMessage`` entries whose ``tool_call_id`` is not matched
      by any preceding assistant tool call (orphans from cancelled turns).
    - Truncates any tool result whose text exceeds the per-result budget,
      replacing it with a short summary so the model keeps a pointer to the
      output without carrying all of it.
    """
    result: list[Message] = []
    known_call_ids: set[str] = set()

    for message in messages:
        if isinstance(message, AssistantMessage):
            known_call_ids |= _tool_call_ids(message)
            result.append(message)
            continue

        if isinstance(message, ToolResultMessage):
            # A result is an orphan if no preceding assistant message ever
            # produced a tool call with this id (e.g. left by a cancelled turn
            # whose assistant message was rolled back). Keep legit results.
            if message.tool_call_id not in known_call_ids:
                continue
            result.append(_budget_tool_result(message))
            continue

        result.append(message)

    return result


def _budget_tool_result(message: ToolResultMessage) -> ToolResultMessage:
    total_chars = sum(len(c.text) for c in message.content if isinstance(c, TextContent))
    if total_chars <= _MAX_TOOL_RESULT_CHARS:
        return message

    kept = 0
    truncated = False
    new_content: list[TextContent | ImageContent] = []
    for c in message.content:
        if not isinstance(c, TextContent):
            new_content.append(c)
            continue
        if truncated:
            continue
        if kept + len(c.text) <= _MAX_TOOL_RESULT_CHARS:
            new_content.append(c)
            kept += len(c.text)
        else:
            remaining = _MAX_TOOL_RESULT_CHARS - kept
            new_content.append(
                TextContent(
                    text=(
                        f"{c.text[:remaining]}\n\n[tool output truncated: "
                        f"{total_chars} chars total, showing first {_MAX_TOOL_RESULT_CHARS}]"
                    )
                )
            )
            truncated = True

    return ToolResultMessage(
        tool_call_id=message.tool_call_id,
        tool_name=message.tool_name,
        content=new_content,
        ui_summary=message.ui_summary,
        ui_details=message.ui_details,
        ui_details_full=message.ui_details_full,
        is_error=message.is_error,
        file_changes=message.file_changes,
    )
