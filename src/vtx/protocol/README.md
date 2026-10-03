# vtx.protocol

The wire vocabulary every layer of vtx agrees on: the three message kinds, the stream parts a provider yields, the tool definition and result shapes, and the two structural protocols (`BaseTool`, `BaseProvider`) that let the engine, the LLM adapters, the tools, and the UI pass the same objects around without importing each other. All of it is pydantic models plus two `runtime_checkable` protocols.

It is a leaf package: it imports the standard library, `pydantic`, and nothing else from vtx. Nothing here touches config, the filesystem, the agent loop, or a UI, so any layer can depend on it.

## Usage

The most common job is folding a provider's stream of deltas into one finished message. This runs as-is against a fake provider; swap `fake_provider()` for `provider.stream(messages, tools=...)`.

```python
import asyncio
import json
from collections.abc import AsyncIterator

from vtx.protocol import (
    AssistantMessage,
    StopReason,
    StreamDone,
    StreamError,
    TextContent,
    TextPart,
    ThinkPart,
    ToolCall,
    ToolCallDelta,
    ToolCallStart,
    Usage,
)


async def collect(parts: AsyncIterator) -> tuple[AssistantMessage, list[ToolCall]]:
    """Turn a provider's deltas into one finished message and its tool calls."""
    text, think, sig = "", "", None
    raw: dict[int, tuple[str, str, str]] = {}
    stop = StopReason.STOP

    async for part in parts:
        if isinstance(part, TextPart):
            text += part.text
        elif isinstance(part, ThinkPart):
            think += part.think
            sig = sig or part.signature
        elif isinstance(part, ToolCallStart):
            # `arguments` is a partial dict when the provider already parsed it.
            raw[part.index] = (
                part.id,
                part.name,
                "" if part.arguments is None else json.dumps(part.arguments),
            )
        elif isinstance(part, ToolCallDelta):
            id_, name_, accumulated = raw[part.index]
            raw[part.index] = (
                id_,
                name_,
                part.arguments_delta
                if part.replace
                else accumulated + part.arguments_delta,
            )
        elif isinstance(part, StreamDone):
            stop = part.stop_reason
        elif isinstance(part, StreamError):
            stop = StopReason.ERROR

    calls = [
        ToolCall(id=i, name=n, arguments=json.loads(raw_text))
        for _, (i, n, raw_text) in sorted(raw.items())
    ]
    return (
        AssistantMessage(
            content=[TextContent(text=text)] if text else [],
            usage=Usage(input_tokens=10, output_tokens=len(text.split())),
            stop_reason=stop,
        ),
        calls,
    )


async def fake_provider() -> AsyncIterator:
    yield TextPart(text="Hel")
    yield TextPart(text="lo")
    yield ToolCallStart(id="call_1", name="read", index=0)
    yield ToolCallDelta(index=0, arguments_delta='{"pa')
    yield ToolCallDelta(index=0, arguments_delta='th": "a.md"}')
    yield StreamDone(stop_reason=StopReason.TOOL_USE)


async def main() -> None:
    message, calls = await collect(fake_provider())
    print(message.content[0].text, message.usage.total_tokens, message.stop_reason)
    # Hello 11 tool_use
    print([(c.id, c.name, c.arguments) for c in calls])
    # [('call_1', 'read', {'path': 'a.md'})]


asyncio.run(main())
```

Two details that bite. `ToolCallDelta.arguments_delta` is a *string* fragment, so a call is reassembled as text and `json.loads`-ed at the end; `ToolCallStart.arguments` is already a dict, so seed the accumulator from `json.dumps` of it. And `index`, not `id`, correlates deltas with their start - providers reorder or interleave calls, ids do not repeat.

## Messages

`Message` is the union `UserMessage | AssistantMessage | ToolResultMessage`. Everything the session store persists is one of the three.

- `UserMessage` has `role="user"` and `content` that is a string or a list of `TextContent` and `ImageContent`.
- `AssistantMessage` has `role="assistant"`, `content` of `TextContent` / `ThinkingContent` / `ToolCall`, and optional `usage` and `stop_reason`.
- `ToolResultMessage` has `role="tool_result"` and is keyed by `tool_call_id` (plus `tool_name`). It carries the UI-side fields the TUI renders: `ui_summary`, `ui_details`, `ui_details_full`, `is_error`, and `file_changes`.
- `TextContent` is a text block, `ThinkingContent` is a reasoning block with an optional provider `signature`, and `ImageContent` is base64 `data` plus `mime_type`.
- `ToolCall` is what the assistant asked for: `id`, `name`, `arguments` (a parsed dict).

## Stream parts

`BaseProvider.stream()` is an async iterator over `StreamPart`, the union of the six part types. They are deltas to merge, never finished messages.

- `TextPart` carries `text`; `merge()` concatenates.
- `ThinkPart` carries `think` plus an optional `signature`. `merge()` concatenates and keeps the first non-empty signature (`self.signature or other.signature`), so an early signature survives later fragments that omit it.
- `ToolCallStart` opens a call with `id`, `name`, `index`, and optional already-parsed `arguments`.
- `ToolCallDelta` appends `arguments_delta` to the call at `index`. Set `replace=True` to overwrite the accumulated text instead of appending, which some providers need when they resend an argument block.
- `StreamDone` is terminal and carries a `StopReason`.
- `StreamError` is terminal and carries an already-formatted error string.

Merge helpers, verbatim:

```python
from vtx.protocol import TextPart, ThinkPart

TextPart(text="a").merge(TextPart(text="b")).text
# 'ab'

ThinkPart(think="a", signature="s1").merge(ThinkPart(think="b"))
# ThinkPart(think='ab', signature='s1')
```

`StopReason` is a `StrEnum` with `STOP`, `LENGTH`, `TOOL_USE`, `ERROR`, `INTERRUPTED`, and `STEER`. `Usage` holds token counts; `total_tokens` sums input + output + cache read + cache write and deliberately excludes `reasoning_tokens`, which providers already bill inside output.

## Tools

- `ToolDefinition` is what you send a provider: `name`, `description`, a JSON-Schema `parameters`, and optional `constrained_sampling`. `ToolParameter` is the simple `{type, description, enum}` descriptor for building that schema.
- `ConstrainedSampling` is grammar-constrained decoding, opt-in per tool and keyed by provider slug. `for_provider(slug)` returns the grammar for that provider or `None` when it has no dialect, and `None` means send the request unconstrained. The key is a discriminator, not a fallback chain.
- `ToolResult` is what a tool returns: `success`, `result` (the string handed to the model), `images`, the `ui_*` fields, `file_changes`, and `structured` - the untruncated value a `codemode` script receives. Splitting `result` from `structured` is what lets the model see a clipped summary while a script still gets the whole object.
- `FileChanges` is `{path, added, removed}` line counts, filled in by edit and write tools.

## Contracts

- `BaseTool` is a `runtime_checkable` protocol of `name: str` and `mutating: bool = True`. It is the minimum a tool must expose to be permission-checked, so a tool can be checked structurally with no import of the tool package.
- `BaseProvider` is a `runtime_checkable` protocol: `name: str` and `async def stream(messages, *, system_prompt=None, tools=None, temperature=None, max_tokens=None, thinking_level=None)`. Anything with that shape is a provider; there is no base class to subclass.

```python
from vtx.protocol.abc import BaseTool


class Read:
    name = "read"
    mutating = False


isinstance(Read(), BaseTool)  # True - no inheritance needed
```

- `format_error(exc)` flattens an exception to a string for the event boundary, keeping the type name so the UI and the model both see which error it was.

```python
from vtx.protocol import format_error

format_error(RuntimeError("boom"))         # 'RuntimeError: boom'
format_error(RuntimeError(""))             # 'RuntimeError: failed without an error message'
format_error(ValueError("ValueError: x"))  # 'ValueError: x' - a repeated name is not doubled
```

## Not here

Agent lifecycle events live in `vtx.core.events`; this package only defines what a provider returns and what a tool is. Sessions and history are `vtx.agent`. Provider implementations are in `vtx.ai.providers`, tool implementations in `vtx.agent.tools` and `vtx.coding_agent.tools`. Nothing in `vtx.protocol` imports any of them.