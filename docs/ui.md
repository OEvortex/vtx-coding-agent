# Terminal UI (TUI)

Vtx ships a keyboard-driven Terminal UI built on [Textual](https://textual.textualize.io/). Launch it with:

```bash
vtx
```

## Input box

- Type a message and press `Enter` to queue it.
- `↑/↓` — browse input history.
- `Shift+Enter` — add a newline.
- `Tab` — complete file/dir paths.
- `Alt+Enter` — steer (inject guidance without submitting a new task).
- `Ctrl+C` — clear the input box (press twice quickly to exit).
- `Esc` — interrupt the running agent.

## Shell execution from input

- `!ls -la` — run a command and show its output in the chat.
- `!!pytest` — run a command, show output, and feed that output to the model for analysis.

## Slash commands

Type `/` to open the command menu:

- `/new` — fresh conversation, reload project context.
- `/resume` — interactive session-history browser.
- `/model` — switch model and provider.
- `/session` — session statistics and token usage.
- `/compact` — manual context compaction.
- `/handoff <query>` — summarize session and start a clean one with that context (see [agents.md](agents.md)).
- `/themes` — switch color scheme (see [theming.md](theming.md)).
- `/permissions` — toggle `prompt` vs `auto` mode.
- `/export` — export transcript to standalone HTML.
- `/extensions` — list loaded extensions.

## Thinking blocks

Finalized reasoning is collapsed to a single line (`ui.collapse_thinking`, `ui.thinking_lines`) to keep the workspace readable. Use `Ctrl+T` to cycle thinking level and `Ctrl+Shift+T` to toggle it.

## Public API

The TUI can be embedded or built upon via `vtx.ui` (see `src/vtx/ui/app.py`). The SDK also exposes a streamed runner for custom UIs — see the [SDK docs](sdk/README.md).
