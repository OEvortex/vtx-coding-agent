# vtx.protocol

The provider-agnostic wire vocabulary: message types, streaming parts, tool definitions, and the structural contracts every layer agrees on.

This package exists so that the agent engine, the LLM adapters, the tools, and the UI can all talk about the same objects without importing each other. It is a **leaf**: `grep -rhoE 'from vtx\.[a-z_]+' src/vtx/protocol` yields only `vtx.protocol`. Nothing here depends on config, the filesystem, the agent loop, or a UI.

Not responsible for:

- **Agent lifecycle events.** Those dataclasses live in `vtx.core.events` (`SessionStartEvent`, `ToolStartEvent`, ...). `vtx.protocol` only defines what a *provider* returns and what a *tool* is.
- **Persistence, sessions, history.** `vtx.agent` owns the session store; these types are serializable values passed to it.
- **Providers.** An actual LLM client lives in `vtx.ai.providers`. This package only declares the `BaseProvider` protocol they satisfy.
- **Tool implementations.** `BaseTool` is a two-field structural protocol (`name`, `mutating`) used solely for permission checks. Concrete tools live in `vtx.agent.tools` and `vtx.coding_agent.tools`.

## Dependencies

- Imports: standard library, `pydantic`, and `vtx.protocol` itself only.
- Imported by: `vtx.agent` (28 files), `vtx.coding_agent` (14), `vtx.core` (6), `vtx.ai` (4), `vtx.mcp` (3), `vtx.tui` (2), `src/vtx/__init__.py` (1).

## Public surface

`vtx.protocol.__all__` (25 names), all importable from the package root.

### Messages

| Name | Description |
|------|-------------|
| `Message` | Union alias: `UserMessage \| AssistantMessage \| ToolResultMessage`. |
| `UserMessage` | `role="user"`, content is a string or a list of `TextContent`/`ImageContent`. |
| `AssistantMessage` | `role="assistant"`, content is `TextContent`/`ThinkingContent`/`ToolCall`, plus optional `usage` and `stop_reason`. |
| `ToolResultMessage` | `role="tool_result"`, keyed by `tool_call_id`; carries `ui_summary`, `ui_details`, `ui_details_full`, `is_error`, `file_changes`. |
| `TextContent` | Text block inside a message. |
| `ThinkingContent` | Reasoning block, with an optional provider `signature`. |
| `ImageContent` | Base64 `data` plus `mime_type`. |
| `ToolCall` | Assistant-issued call: `id`, `name`, `arguments`. |

### Stream parts

`BaseProvider.stream()` is an async iterator over these; they are deltas to merge, not finished messages.

| Name | Description |
|------|-------------|
| `StreamPart` | Union alias over the six part types below. |
| `TextPart` | Text delta; `merge()` concatenates. |
| `ThinkPart` | Reasoning delta with `signature`; `merge()` concatenates text and keeps the first non-empty signature. |
| `ToolCallStart` | Opens a tool call: `id`, `name`, `index`, optional partial `arguments`. |
| `ToolCallDelta` | Appends `arguments_delta` to the call at `index`; `replace=True` overwrites instead. |
| `StreamDone` | Terminal part carrying a `StopReason`. |
| `StreamError` | Terminal part carrying an already-formatted error string. |

### Enums and usage

| Name | Description |
|------|-------------|
| `StopReason` | `StrEnum`: `stop`, `length`, `tool_use`, `error`, `interrupted`, `steer`. |
| `Usage` | Token counts; `total_tokens` sums input + output + cache read + cache write (reasoning tokens are excluded). |

### Tools

| Name | Description |
|------|-------------|
| `ToolDefinition` | `name`, `description`, JSON-Schema `parameters`, optional `constrained_sampling`. |
| `ToolParameter` | Simple `{type, description, enum}` descriptor. |
| `ConstrainedSampling` | Grammar-constrained decoding opt-in, keyed by provider slug; `for_provider()` returns `None` when that provider has no dialect, in which case the request is sent unconstrained. |
| `ToolResult` | A tool's return value: `success`, `result` (string for the model), `images`, UI fields, `file_changes`, and `structured` (the untruncated value a `codemode` script receives). |
| `FileChanges` | `{path, added, removed}` line counts for edit/write tools. |

### Contracts and helpers

| Name | Description |
|------|-------------|
| `BaseTool` | `runtime_checkable` Protocol: `name: str`, `mutating: bool = True`. The minimum a tool must expose to be permission-checked. |
| `BaseProvider` | `runtime_checkable` Protocol: `name: str` and `async def stream(messages, *, system_prompt=None, tools=None, temperature=None, max_tokens=None, thinking_level=None) -> AsyncIterator[object]`. |
| `format_error` | `(BaseException) -> str`. Bakes the exception type name into the message, since errors are flattened to strings at the event boundary. |

## Usage

Constructing a message and checking a tool against the gate:

```python
from vtx.protocol import AssistantMessage, TextContent, Usage
from vtx.protocol.abc import BaseTool

msg = AssistantMessage(
    content=[TextContent(text="Done.")],
    usage=Usage(input_tokens=120, output_tokens=8),
)
print(msg.usage.total_tokens)  # 128

class MyTool:
    name = "bash"
    mutating = True

print(isinstance(MyTool(), BaseTool))  # True
```

`format_error` behaviour, verbatim from `errors.py`:

```python
from vtx.protocol import format_error

format_error(RuntimeError("boom"))        # "RuntimeError: boom"
format_error(RuntimeError(""))            # "RuntimeError: failed without an error message"
format_error(ValueError("ValueError: x"))    # "ValueError: x"  (name not repeated)
```