# vtx.coding_agent

The product layer: the `vtx` console script, the headless runner, the concrete built-in tools and their default registry, the built-in skills package, the handoff-agent loader, and the Textual interface under `vtx.coding_agent.tui`.

Everything here is wiring and choice. The engine it drives -- the loop, the session store, the provider adapters -- is `vtx.agent` and `vtx.ai`. Importing `vtx.coding_agent.tools` has the side effect of registering the built-in tools into the harness registry.

```
vtx.coding_agent          CLI, headless runner, built-in tools, skills, UI
   ├── vtx.agent          harness (loop, sessions, tools, goals, extensions)
   ├── vtx.ai             providers and the model catalog
   ├── vtx.core           config, permissions, events
   ├── vtx.git            branch metadata
   ├── vtx.mcp            MCP client
   └── vtx.tui            base UI toolkit
```

The UI is split out into `vtx.coding_agent.tui`; this package's non-UI modules know nothing about it.

## The console script

`pyproject.toml` declares `vtx = "vtx.coding_agent.cli:main"`, so `cli:main` is the entry point for both run surfaces.

Subcommands: `vtx update` (self-update to the latest stable PyPI release and exit), `vtx install <name>` (tries `vtx-<name>` then `<name>`; `--upgrade`), `vtx uninstall <name>`, `vtx list-extensions`.

Flags: `--model/-m`, `--provider`, `--api-key/-k`, `--base-url/-u`, `--prompt/-p` (one prompt, then exit; the value may be omitted or `-` to read stdin), `--openai-compat-auth`, `--anthropic-compat-auth` (`auto`/`required`/`none`), `--insecure-skip-verify`, `--continue/-c`, `--resume/-r`, `--extension/-e` (repeatable), `--no-extensions`, `--agent/-a`, `--agent-file` (repeatable), `--no-agents`, `--list-agents`, `--list-extensions`, `--version`.

With `-p` the run goes to `headless.run_headless`; otherwise to `tui.launch.run_tui`. `--continue` / `--resume` are rejected together with `-p`.

## Headless

`headless.py` exports `resolve_prompt`, `render_run`, `run_headless`, and `_exit_code`. `run_headless` returns an exit code derived from the `StopReason`: `0` for a normal stop, `1` for an error or an unmapped reason, `3` for a length stop, `2` for an empty prompt.

Headless cannot show approval prompts, so it forces auto-approval for the run without mutating the saved config.

## Built-in tools

`tools/__init__.py` instantiates the registry at import time:

```
read, edit, write, bash, find, grep, skill, web, web_search, ask_user, delegate_subagent, goal
```

`DEFAULT_TOOLS` is that list minus `grep`, which is registered but not enabled by default. `PARENT_ONLY_TOOLS` is `frozenset({"delegate_subagent", "goal"})`.

Every tool in `all_tools` is passed to `vtx.agent.tools.register_tool` with `is_default` from `DEFAULT_TOOLS` and `parent_only` from `PARENT_ONLY_TOOLS`, and `get_tool` is installed as the registry's default lookup. Importing `vtx.coding_agent.tools` is what makes the tools exist.

The seven filesystem tools are local to this package: `ReadTool`, `EditTool`, `WriteTool`, `BashTool`, `FindTool`, `GrepTool`, `SkillTool`. `WebTool`, `WebSearchTool`, `AskUserTool`, `TaskTool` and `GoalTool` come from `vtx.agent.tools`.

Also exported: `get_tools`, `get_tool`, `get_tools_with_extensions` (name-match replacement, so an extension can override a built-in), `tools_by_name`, `get_tool_definitions`, and the background-task re-exports (`BackgroundTaskManager`, `BackgroundTaskRecord`, `get_manager`, `set_manager`).

## Handoff agents

`agents/` loads switchable agents from `.vtx/agent/<name>.py`: a bundle of system-prompt instructions, model/provider/thinking/max-turns overrides, a tool allow/deny list, agent-scoped tools and slash commands, and agent-scoped extensions. `load_all_agents(cwd=..., configured=...)` returns `(loaded, errors)`, which is what `vtx --list-agents` prints.

## Built-in skills

`builtin_skills/` is registered into the harness by `register_skills_package("vtx.coding_agent")` at package import. It ships `cloud/google-colab`, `cloud/modal`, `code-review/review`, `general/github`, `meta/goal`, `meta/skill-builder`, and `setup/init`. `/goal` is one of these skills rather than a built-in command, which is why it is absent from the app's command router.

## The interface: `vtx.coding_agent.tui`

`__all__` exports `Vtx`, `run_tui`, `ChatLog`, `InfoBar`, `StatusLine`, `QueueDisplay`, `format_path`, `TreeSelector`, `CommandsMixin`, and `export_session_html`, all lazily.

| Module | Responsibility |
| --- | --- |
| `app.py` | `Vtx`, the `App` subclass: widget composition, runtime wiring, key bindings, input routing. Composes the mixins below. |
| `launch.py` | `run_tui(args)` and the exit summary printed after the app closes. |
| `startup.py` | Background startup chores: binary download, update check, file-path scan, git-branch refresh, launch warnings. |
| `chat.py` | `ChatLog`, the scrollback that mounts the `vtx.tui.blocks` widgets. |
| `widgets.py` | `InfoBar`, `StatusLine`, `QueueDisplay`, `FileChangesModal`, `format_path`, `get_git_branch`. |
| `status_lines.py` | The context-aware status-line text: model and agent lifecycle states, per-tool activity, per-tool error lines. |
| `tree.py` | `TreeSelector`, the session-tree navigator. |
| `queue_ui.py` | Pending and steer message queue state and its rendering. |
| `agent_runner.py` | Driving agent runs, forwarding agent events to the chat UI, and handling `!` / `!!` shell commands. |
| `agents_panel.py` | `AgentsPanel`, the pinned strip listing every sub-agent in flight. |
| `goal_agents.py` | The live sub-agent registry fed by the `delegate_subagent` progress callback, keyed by dispatch `tool_call_id`. |
| `goal_ui.py` | `GoalWidget` (the above-editor beacon) and `GoalDashboardScreen` (the `ctrl+shift+g` overlay), sharing one presentation model. |
| `session_ui.py` | Rendering persisted sessions into the chat log. |
| `completion_ui.py` | Completion list and selection-mode picker message handling. Its `@on` handlers are re-bound in the `Vtx` class body, because Textual's metaclass only scans the namespace of classes it creates. |
| `extension_ui.py` | `TextualExtensionUI`, implementing `ExtensionUIContext` over the app: confirm / select / input dialogs, chat notifications, and a persistent status/widget footer bar. |
| `export.py` | `export_session_html`, a standalone exporter that parses session JSONL directly and takes the tool registry as its only vtx dependency. |
| `recap.py` | Idle-time session recap: arms a 30s timer after a run, drafts a "where you left off" summary with a cheap one-off model call. |
| `app_protocol.py` | The protocol the agent runner and mixins are typed against. |

### Key bindings

From the `BINDINGS` table on `Vtx` in `app.py`:

| Key | Action |
| --- | --- |
| `ctrl+c` | Clear |
| `ctrl+d` | Delete session |
| `escape` | Interrupt |
| `left` / `right` | Tree page up / down |
| `ctrl+shift+g` | Toggle goal dashboard |
| `ctrl+t` | Cycle thinking level |
| `ctrl+o` | Toggle tool output |
| `ctrl+shift+t` | Toggle thinking |
| `alt+ctrl+p` | Cycle permission mode |
| `shift+tab` | Cycle handoff agent |
| `alt+ctrl+g` | Cycle tool group |

The editor's own bindings live on `InputBox` in `vtx.tui.input`.

### Slash commands

`commands/` splits handling by domain into `CommandsMixin`: `settings.py`, `models.py`, `sessions.py`, `auth.py`, `providers.py`, `agents.py`, `switch.py`, `update.py`, `reload.py`, `mcp.py`, and `goals.py`.

Routing is two-stage. `Vtx` first tries `CommandsMixin._handle_command`, so a built-in always wins a name collision with a skill; anything the router does not know is routed to the skill system, which is how `/goal` works.

Routable commands: `help`, `quit`/`exit`/`q`, `clear`, `model`, `provider`, `new`, `settings`, `themes`, `permissions`, `thinking`, `effort`, `notifications`, `ponytail`, `handoff`, `resume`, `tree`, `undo`, `redo`, `session`, `login`, `logout`, `export`, `copy`, `compact`, `recap`, `agent`, `switch`, `update`, `reload`, `mcp`. Extension commands get the last swing if nothing else handled the name.

`DEFAULT_COMMANDS` in `vtx.tui.autocomplete` is the autocomplete list, and it is deliberately **not** the same set. `/effort` is a routable alias of `/thinking` -- "effort" is what the wire calls it -- and it is not advertised in autocomplete, because `/settings` is the discoverable entry point for the thinking and permissions sub-commands. `/goal` and `/switch` are handled but likewise not advertised.

### Thinking levels

`/thinking` (and `ctrl+t`) operate on `resolve_thinking_levels` in `vtx.ai.thinking`, the single source of truth also used by session restore and model switching. The offered set is what the models.dev catalog advertises for the model, intersected with what the transport's effort enum can send and what the API style has a wire spelling for.

One rough edge is worth stating plainly. `get_model` matches model ids exactly, so `space-bunny-alpha` misses the catalog entry `stealth/space-bunny-alpha` and the picker falls back to the provider's raw enum, which offers levels the model marks unsupported. `SettingsCommands._thinking_availability` reports this rather than hiding it, with `catalog entry not found, check the exact model id`. The model id format is not flexible; there is no prefix matching to rescue a near-miss.

## Tests

```bash
uv run --no-sync python -m pytest -p no:cacheprovider tests/ui -q
uv run --no-sync python -m pytest -p no:cacheprovider tests/test_cli.py tests/test_headless.py tests/test_agent_profiles.py -q
```

`tests/ui` holds the app, panel, picker and widget tests; the CLI, headless runner and agent loader are tested from `tests/` at the top level.
