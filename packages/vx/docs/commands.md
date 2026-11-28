# Slash Commands

Every slash command vx recognizes, what each does, its arguments, and whether it costs a model turn. The router is `CommandsMixin.handle_slash` in `packages/vx/src/vx/commands/__init__.py:155`.

Type `/` at the start of the input to see the command picker; it is built from `SLASH_COMMANDS` (`commands/__init__.py:33`), which lists each command's name, description, and a category shown in the picker's metadata column.

## Three kinds of command

| Kind | Where it comes from | Costs a model turn |
|---|---|---|
| Built-in command | mixins in `packages/vx/src/vx/commands/` | No |
| Extension command | `all_commands` of the loaded ctx extensions | No |
| Skill command | `register_cmd: true` in a `SKILL.md` frontmatter | Yes |

The router tries built-ins first, then extension commands, then skill commands; an unknown name warns `unknown command: /<name>` rather than falling through to the model (`commands/__init__.py:205-213`).

Aliases in `SLASH_ALIASES` (`commands/__init__.py:97`) are resolved before dispatch: `/q` and `/exit` for `/quit`, `/effort` and `/think` for `/thinking`, `/perm` for `/permissions`, `/switch` for `/agent`, `/theme` for `/themes`, `/notification` for `/notifications`, `/ls` for `/tools`, `/commands` for `/help`.

Most commands need the agent runtime. The `NEEDS_RUNTIME` set (`commands/__init__.py:111`) is refused while the runtime is still starting with `still starting — the runtime is not ready yet`. `/help`, `/find`, `/board`, `/attach`, `/quit`, and `/update` work before it.

A command handler that raises is reported as `/<cmd> failed: <exc>` in the transcript; it never kills the app.

## General

| Command | Arguments | What it does |
|---|---|---|
| `/help` | | Open the command picker with commands, categories, and key bindings. Selecting a command fills the composer with `/<name> ` |
| `/find` | `[query]` | Open the transcript search (same UI as ctrl+f), prefilled with the query |
| `/quit` | | Exit vx. Aliases `/exit`, `/q` |

## Model

| Command | Arguments | What it does |
|---|---|---|
| `/model` | | Open the model picker: up to 5 recent models (marked `↻`), then the rest of the catalog sorted by provider, with provider, vision eye `◉`, and context size in the metadata column. Filterable |
| `/model <name>` | | Select a model by id or `provider::model`. An uncataloged `provider::model` builds an ad-hoc entry from the provider's catalog info |
| `/model refresh` | `[provider]` | Re-fetch the model catalog from dynamic providers, or one provider's |
| `/provider` | | Pick which provider `/model` shows; `All providers` clears the filter. `/provider reset`, `/provider all`, `/provider clear` clear it directly |
| `/thinking` | `[level]` | Set reasoning effort: `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max`, `default` — whichever the active model advertises. With no argument, a picker with per-level descriptions. Aliases `/effort`, `/think` |

`/provider` persists to `ui.model_provider_filter` in the config; a scoped picker reads that provider's catalog directly instead of materializing all ~6.4k models (`commands/models.py:78-83`).

## Security

| Command | Arguments | What it does |
|---|---|---|
| `/mode` | `[ask\|auto]` | Switch vx's permission mode. With no argument opens a picker of the `BUILTIN_MODES` from `permissions.py`; also syncs the ctx config (`prompt`/`auto`) so the hint row agrees |
| `/permissions` | `[mode]` | Same picker when the interactive panel is unavailable; with an argument switches to an id from `BUILTIN_MODES` (`prompt` maps to `ask`) |

## Account

| Command | Arguments | What it does |
|---|---|---|
| `/login` | | Three-way picker: OAuth (GitHub Copilot, OpenAI Codex, Cline, Devin, Junie, DeepAI), API key for any keyed provider, or keyless/local providers. `/login <provider>` jumps straight to that provider's flow |
| `/logout` | | Picker of providers with stored credentials; `/logout <provider>` clears one directly |

OAuth providers open a browser and print the authorization URL. API keys are entered through a secret prompt; when a stored key already exists the prompt offers Update / Clear / Cancel. After any login vx refreshes that provider's model list.

## Session

| Command | Arguments | What it does |
|---|---|---|
| `/new` | | Start a fresh session, reload context, clear the transcript |
| `/clear` | | Clear the transcript and start a new session **under** the current one (the session tree keeps the parent) |
| `/session` | | Session picker: saved sessions for this directory as a tree (handoff children nested under parents), with message counts and age. Filterable |
| `/tree` | | In-session tree navigator: every entry of the current session as a tree, current leaf marked `✓`. Picking one calls `navigate_tree`; if the node carried editor text it lands in the composer. Up to 200 rows |
| `/compact` | `[focus]` | Summarize the context window now; text steers the summary |
| `/handoff` | `<query>` | Create a handoff session seeded with this conversation and pre-fill the composer with `<query>`; press enter to send |
| `/undo` | | Rewind the last turn: restores the agent's file edits and stages the boundary, reporting files with A/M/D marks |
| `/redo` | | Step forward again, or clear the revert at the newest turn |
| `/usage` | | Token usage: this session, rolling month, and lifetime over a year heatmap. Runs in a worker thread because it reads every session log |
| `/export` | | Write the session to an HTML file in the working directory |
| `/copy` | | Copy the last assistant message to the clipboard |
| `/recap` | | Draft a "where you left off" summary into the composer box |
| `/attach` | `[path]` | Attach an image to the next prompt. No argument lists pending attachments; `clear`, `none`, `off` drops them. Same list ctrl+v fills |

`/new`, `/clear`, `/tree`, `/compact`, `/undo`, `/redo`, `/session`, and `/handoff` refuse while a turn is running (`cannot <verb> while the agent is running — press esc to interrupt`).

## Agents

| Command | Arguments | What it does |
|---|---|---|
| `/agent` | | Agent picker: `(no agent)` plus every discovered agent, active one marked `●`, tools in the metadata column. Agents come from `.ctx/agent/<name>.py` and `~/.ctx/agent/<name>.py` |
| `/agent list`, `/agent ls` | | List agents with the active one marked |
| `/agent current`, `/agent status` | | Print the active agent, or `no active agent (default session profile)` |
| `/agent reload` | | Re-discover agents, keep the active profile if it still resolves |
| `/agent <name>` | | Activate an agent; `off`, `none`, `default` deactivate |
| `/switch` | `<name>` | Alias for `/agent <name>` |

## Swarm

| Command | Arguments | What it does |
|---|---|---|
| `/board` | | Open the swarm board page: roster of workers, the shared feed, and the "needs you" permission asks. Works with no runtime — it renders an empty page when the conversation has no swarm |

## Workspace

| Command | Arguments | What it does |
|---|---|---|
| `/skills` | | List loaded skills grouped by category, `local ·` marking project skills, plus any `skill_warnings` |
| `/tools` | | List loaded tools: `built-in (n)` first, then one section per MCP server as `server > tool` |

## Extensions

| Command | Arguments | What it does |
|---|---|---|
| `/mcp` | | List MCP servers with state and tool count, plus config errors and sign-in URLs. Aliases `list`, `ls` |
| `/mcp reload` | | Re-read `mcp.json` and reconnect every server |
| `/mcp reconnect` | `<name>` | Rebuild one server's connection and re-sync tools |
| `/mcp enable` / `/mcp disable` | `<name>` | Flip `enabled` in the server's `mcp.json` and reconnect |
| `/mcp signin` | `<name>` | OAuth sign-in for a remote server; prints and opens the URL. Aliases `sign-in`, `login`, `auth` |
| `/mcp signout` | `<name>` | Clear a server's stored credentials. Aliases `sign-out`, `logout` |
| `/reload` | | Re-apply config, extensions, agents, tools, skills, hooks, providers, and MCP in place, then report a before/after diff. Refuses while a turn runs |
| `/update` | | Self-update vx to the latest release; restart to apply |

`/mcp` servers are configured in `mcp.json` under the ctx config dir; the list view prints an example when none exist.

## Appearance (via `/settings`)

| Command | Arguments | What it does |
|---|---|---|
| `/settings` | | Settings picker: colored-tool-badge, git-context, history (scrollback), notifications, recap, recap-idle, show-shortcuts, permissions, themes, thinking, thinking-lines. Most rows toggle in place and reopen the picker |
| `/themes` | `[name]` | Switch palette; no argument opens the theme picker. Alias `/theme` |
| `/notifications` | `[on\|off]` | Toggle notification sounds. Alias `/notification` |

`recap-idle` offers 15s/30s/1m/2m/5m quiet thresholds; `history` offers unlimited or a row cap (default unlimited, 500 rows trims the tail). `git-context` only applies to new conversations or after a restart — the command says so.

## Extension commands

An extension contributes commands through ctx's plugin API (`api.register_command`). They dispatch through `_extension_command` (`commands/__init__.py:257`): the handler runs with the typed arguments, its `output` is posted as `/<name> (from <owner>): <output>`, and a raise becomes `extension command /<name> failed: <msg>`. They never cost a model turn. They are not in `SLASH_COMMANDS`, so `/help` and the `/` picker do not list them — an extension command is reachable only by typing its exact name.

## Skill commands

A skill with `register_cmd: true` is reachable as `/<skill-name> <args>` (`_skill_command`, `commands/__init__.py:234`). Unlike built-ins this **does** cost a model turn: the skill body is rendered with `render_skill_prompt` and enqueued as a normal prompt, with a `UserTurn` row showing `/<name> <args>` in the transcript. See [skills.md](skills.md).

## Key bindings listed in `/help`

`/help` also lists the composer's key bindings (`KEY_BINDINGS`, `commands/__init__.py:72`): navigation and editing (arrows, word motion, undo, kill ring), transcript search and history, `alt+enter` for a newline without sending, `ctrl+d` to exit on an empty composer, `esc` to interrupt, `ctrl+c` twice to exit, and `tab`/`shift+tab` to cycle effort and permission mode. The full map is in [keybindings.md](keybindings.md).

## Composer escapes

Two prefixes act on the composer text before it is sent:

- `!cmd` runs `cmd` as a shell command and shows the output as a transcript block (`_handle_shell`, `app.py`); `!!cmd` runs it **and** sends the output to the model as context. `ctrl+o` expands a collapsed `!` block.
- `@path` opens a file picker over workspace paths (`atpath.py`); picking one inserts the path, and `@"quoted path"` handles spaces. `expand_paths` resolves the mentions into the prompt text.

## See also

- [skills.md](skills.md) for `register_cmd` skills
- [swarm.md](swarm.md) for the `/board` page and the swarm tools
- [tools.md](tools.md) for the tool surface
- [permissions.md](permissions.md) for the modes `/mode` and `/permissions` switch
