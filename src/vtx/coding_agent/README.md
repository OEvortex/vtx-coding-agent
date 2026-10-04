# vtx.coding_agent

The product layer: the `vtx` console script, the headless runner, the concrete built-in tools and their default registry, the built-in skills package, and the handoff-agent loader. Use it to run the agent, or to embed its tools in something else. The Textual interface is `vtx.tui`.

Everything here is wiring and choice. The engine it drives - the loop, the session store, the provider adapters - is `vtx.agent` and `vtx.ai`. Importing `vtx.coding_agent.tools` has the side effect of registering the built-in tools into the harness registry, so the tools exist the moment you import them.

```
vtx.coding_agent          CLI, headless runner, built-in tools, skills
   |- vtx.tui             the Textual interface (app shell + primitives)
   |- vtx.agent           harness (loop, sessions, tools, goals, extensions)
   |- vtx.ai              providers and the model catalog
   |- vtx.core            config, permissions, events
   |- vtx.git             branch metadata
   |- vtx.mcp             MCP client
```

This package holds the wiring and the choices; the UI is `vtx.tui`, documented in its own README. `cli.py:run_tui_command` is the only thing here that reaches for it.

## Usage

The tools are ordinary `BaseTool` instances, so anything that can drive a tool can drive these. Reach them through the registry:

```python
import asyncio

from vtx.coding_agent.tools import get_tool

bash = get_tool("bash")


async def main() -> None:
    result = await bash.execute(params=bash.params(command="echo hello", timeout=10))
    print(result.success, result.result)  # True hello


asyncio.run(main())
```

`get_tool(name)` is the registry's default lookup and returns `None` for an unknown name. `tool.params` is the pydantic input model; `execute()` returns a `ToolResult` with `success`, `result`, `ui_summary`, and optional `images`, `file_changes`, and `structured` fields. Each tool also carries `description`, `needs_approval`, `mutating`, and the presentation hooks `format_call`, `format_preview`, and `ui_block`.

The same registry feeds the agent loop, so a tool added here is immediately available to the model.

## The console script

`pyproject.toml` declares `vtx = "vtx.coding_agent.cli:main"`, so `cli:main` is the entry point for both run surfaces:

```bash
vtx                                    # interactive
vtx -p "explain this repo"             # one prompt, then exit
vtx -p - < prompt.md                   # prompt from stdin
vtx --continue                         # resume the most recent session
vtx --resume 4f2a -m anthropic/claude-opus-5
```

Subcommands are `vtx update` (self-update to the latest stable PyPI release and exit), `vtx install <name>` (tries `vtx-<name>` then `<name>`; `--upgrade`), `vtx uninstall <name>`, and `vtx list-extensions`.

Flags are `--model/-m`, `--provider`, `--api-key/-k`, `--base-url/-u`, `--prompt/-p`, `--openai-compat-auth` and `--anthropic-compat-auth` (`auto`/`required`/`none`), `--insecure-skip-verify`, `--continue/-c`, `--resume/-r`, `--extension/-e` (repeatable), `--no-extensions`, `--agent/-a`, `--agent-file` (repeatable), `--no-agents`, `--list-agents`, `--list-extensions`, and `--version`.

With `-p` the run goes to `headless.run_headless`; otherwise to `tui.launch.run_tui`. `--continue` and `--resume` are rejected together with `-p`.

## Headless

`headless.py` exports `resolve_prompt(prompt_arg, *, stdin)`, `render_run(events, *, out, err)`, and `run_headless(...)`. `run_headless` returns an exit code derived from the `StopReason`: `0` for a normal stop, `1` for an error or an unmapped reason, `3` for a length stop, and `2` for an empty prompt. `render_run` is the piece to reuse if you want the event stream without the CLI.

Headless cannot show approval prompts, so it forces auto-approval for the run by writing the config file, running, and restoring it; the saved config is not mutated. A project with a `.vtx/mcp.json` that is not trusted prints a warning to stderr rather than starting its servers.

## Built-in tools

`tools/__init__.py` instantiates the registry at import time. `all_tools` is `read`, `edit`, `write`, `bash`, `find`, `grep`, `skill`, `web`, `web_search`, `ask_user`, `delegate_subagent`, `goal`. `DEFAULT_TOOLS` is that list minus `grep`, which is registered but not enabled by default. `PARENT_ONLY_TOOLS` is `frozenset({"delegate_subagent", "goal"})` - those two are not offered to a sub-agent.

Every tool is passed to `vtx.agent.tools.register_tool` with `is_default` from `DEFAULT_TOOLS` and `parent_only` from `PARENT_ONLY_TOOLS`.

The seven filesystem tools are local to this package: `ReadTool`, `EditTool`, `WriteTool`, `BashTool`, `FindTool`, `GrepTool`, `SkillTool`. `WebTool`, `WebSearchTool`, `AskUserTool`, `TaskTool`, and `GoalTool` come from `vtx.agent.tools`.

The registry API is `get_tools(default_names)`, `get_tool(name)`, `get_tools_with_extensions(default_names, extension_tools=None)`, `tools_by_name`, and `get_tool_definitions`. `get_tools_with_extensions` replaces by name match, so an extension can override a built-in. The background-task re-exports (`BackgroundTaskManager`, `BackgroundTaskRecord`, `get_manager`, `set_manager`) are here too.

## Handoff agents and skills

`agents/` loads switchable agents from `.vtx/agent/<name>.py`: a bundle of system-prompt instructions, model/provider/thinking/max-turns overrides, a tool allow/deny list, agent-scoped tools and slash commands, and agent-scoped extensions. There are no built-in profiles. `load_all_agents(cwd=..., configured=...)` returns `(loaded, errors)` and collects errors rather than raising, so one bad agent file does not block the rest; `vtx --list-agents` prints that pair.

`builtin_skills/` is registered into the harness by `register_skills_package("vtx.coding_agent")` at package import. It ships `cloud/google-colab`, `cloud/modal`, `code-review/review`, `general/github`, `meta/goal`, `meta/skill-builder`, and `setup/init`. `/goal` is one of these skills rather than a built-in command, which is why it is absent from the app's command router.

## The interface: `vtx.tui`

See `vtx/tui/README.md`. `vtx.tui.__all__` exports `Vtx`, `run_tui`, `ChatLog`, `InfoBar`, `StatusLine`, `QueueDisplay`, `format_path`, `TreeSelector`, `CommandsMixin`, and `export_session_html`, all lazily.

<details><summary>Module map</summary>

- `app.py` holds `Vtx`, the `App` subclass that composes the widget tree, wires the runtime, and routes input. It is also where `BINDINGS` lives.
- `launch.py` has `run_tui(args)` and the exit summary printed after the app closes; `startup.py` has the background chores - binary download, update check, file-path scan, git-branch refresh, launch warnings.
- `chat.py` is `ChatLog`, the scrollback that mounts the `vtx.tui.blocks` widgets; `session_ui.py` renders persisted sessions into it.
- `widgets.py` has `InfoBar`, `StatusLine`, `QueueDisplay`, `FileChangesModal`, `format_path`, and `get_git_branch`; `status_lines.py` has the context-aware status-line text for model and agent lifecycle states, per-tool activity, and per-tool error lines.
- `tree.py` is `TreeSelector`, the session-tree navigator. `queue_ui.py` holds the pending and steer message queue state and its rendering. `agent_runner.py` drives agent runs, forwards agent events to the chat UI, and handles the `!` / `!!` shell commands.
- `agents_panel.py` is `AgentsPanel`, the pinned strip listing every sub-agent in flight; `goal_agents.py` is the live sub-agent registry fed by the `delegate_subagent` progress callback, keyed by dispatch `tool_call_id`.
- `goal_ui.py` has `GoalWidget` (the above-editor beacon) and `GoalDashboardScreen` (the `ctrl+shift+g` overlay), sharing one presentation model.
- `completion_ui.py` handles the completion-list and selection-mode picker messages. Its `@on` handlers are re-bound in the `Vtx` class body, because Textual's metaclass only scans the namespace of classes it creates.
- `extension_ui.py` is `TextualExtensionUI`, implementing `ExtensionUIContext` over the app: confirm / select / input dialogs, chat notifications, and a persistent status/widget footer bar.

</details>
- `export.py` is `export_session_html`, a standalone exporter that parses session JSONL directly and takes the tool registry as its only vtx dependency. `recap.py` arms a 30s idle timer after a run and drafts a "where you left off" summary with a cheap one-off model call. `app_protocol.py` is the protocol the agent runner and mixins are typed against.

### Key bindings

From the `BINDINGS` table on `Vtx`:

- `ctrl+c` clear, `ctrl+d` delete session, `escape` interrupt the running agent
- `left` / `right` tree page up / down
- `ctrl+t` cycle thinking level, `ctrl+shift+t` toggle thinking
- `ctrl+o` toggle tool output
- `ctrl+shift+g` toggle the goal dashboard
- `shift+tab` cycle handoff agent
- `alt+ctrl+p` cycle permission mode, `alt+ctrl+g` cycle tool group

The editor's own bindings live on `InputBox` in `vtx.tui.input`.

### Slash commands

`commands/` splits handling by domain into `CommandsMixin`: `settings.py`, `models.py`, `sessions.py`, `auth.py`, `providers.py`, `agents.py`, `switch.py`, `update.py`, `reload.py`, `mcp.py`, and `goals.py`.

Routing is two-stage. `Vtx` first tries `CommandsMixin._handle_command`, so a built-in always wins a name collision with a skill; anything the router does not know falls through to the skill system, which is how `/goal` works. Extension commands get the last swing, so an extension can shadow a built-in - and a handler that raises is reported in the chat log rather than crashing the app.

Routable commands are `help`, `quit`/`exit`/`q`, `clear`, `model`, `provider`, `new`, `settings`, `themes`, `permissions`, `thinking`, `effort`, `notifications`, `ponytail`, `handoff`, `resume`, `tree`, `undo`, `redo`, `session`, `login`, `logout`, `export`, `copy`, `compact`, `recap`, `agent`, `switch`, `update`, `reload`, and `mcp`.

`DEFAULT_COMMANDS` in `vtx.tui.autocomplete` is the autocomplete list, and it is deliberately **not** the same set. `/effort` is a routable alias of `/thinking` - "effort" is what the wire calls it - and it is not advertised in autocomplete, because `/settings` is the discoverable entry point for the thinking and permissions sub-commands. `/goal` and `/switch` are handled but likewise not advertised.

### Thinking levels

`/thinking` and `ctrl+t` operate on `resolve_thinking_levels` in `vtx.ai.thinking`, the single source of truth also used by session restore and model switching. The offered set is what the models.dev catalog advertises for the model, intersected with what the transport's effort enum can send and what the API style has a wire spelling for.

One rough edge is worth stating plainly. `get_model` matches model ids exactly, so `space-bunny-alpha` misses the catalog entry `stealth/space-bunny-alpha` and the picker falls back to the provider's raw enum, which offers levels the model marks unsupported. `SettingsCommands._thinking_availability` reports this rather than hiding it, with `catalog entry not found, check the exact model id`. The model id format is not flexible; there is no prefix matching to rescue a near-miss.

## Tests

```bash
uv run --no-sync python -m pytest -p no:cacheprovider tests/ui -q
uv run --no-sync python -m pytest -p no:cacheprovider tests/test_cli.py tests/test_headless.py tests/test_agent_profiles.py -q
```

`tests/ui` holds the app, panel, picker, and widget tests; the CLI, headless runner, and agent loader are tested from `tests/` at the top level.