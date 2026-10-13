# Slash Commands

This page documents every slash command the TUI recognizes, what each does, its arguments, and whether it costs a model turn. The router is `CommandsMixin._handle_command` in `src/vtx/tui/commands/__init__.py:46`.

Type `/` at the start of the input to see the command menu; it is built from `DEFAULT_COMMANDS` (`src/vtx/tui/autocomplete.py:380`), the registered skills, and the extension commands, in that order (`src/vtx/tui/app.py:486`).

## Three kinds of command

| Kind | Where it comes from | Costs a model turn |
|---|---|---|
| Built-in router command | `CommandsMixin` mixins in `src/vtx/tui/commands/` | No |
| Plugin / extension command | `api.register_command(...)` in a loaded extension (`src/vtx/plugins/api.py:292`) | No |
| Skill command | `register_cmd: true` in a `SKILL.md` frontmatter | Yes |

The router is consulted first, then extension commands, then the skill registry. Built-ins therefore always win a name collision: `_handle_command` tests every built-in name before it looks an extension up, so an extension can only answer names no built-in claims (`src/vtx/tui/commands/__init__.py:149`).

A command that is not recognized by the router falls through to the caller unchanged, which sends it to the model as a normal prompt (`src/vtx/tui/app.py:1076`).

## Built-in commands

### Session and conversation

| Command | Arguments | What it does | Model turn |
|---|---|---|---|
| `/help` | | Show the in-app help panel with its own command and keybinding list (`src/vtx/tui/chat.py:440`) | No |
| `/quit`, `/exit`, `/q` | | Exit the application (`src/vtx/tui/commands/__init__.py:51`) | No |
| `/clear` | | Start a fresh session beneath the current one, reset the token counters and file-change summary, and drop the codemode store (`src/vtx/tui/commands/sessions.py:27`) | No |
| `/new` | | Start a new conversation: clear the chat log, reset the status line, reload context (`src/vtx/tui/commands/sessions.py:51`) | No |
| `/resume` | | Open the session picker, with handoff children nested under their parent (`src/vtx/tui/commands/sessions.py:384`) | No |
| `/tree` | | Open the in-session tree navigator (`src/vtx/tui/commands/sessions.py:365`) | No |
| `/session` | | Show session id, directory, message counts, and token totals (`src/vtx/tui/commands/sessions.py:152`) | No |
| `/usage` | | Show token usage for this session, the last 30 days, and all time; runs in a worker because it reads every session log (`src/vtx/tui/commands/sessions.py:180`) | No |
| `/handoff` | `<query>` | Create a focused handoff session seeded with the current conversation and pre-fill the input with `<query>` (`src/vtx/tui/commands/sessions.py:90`) | No |
| `/undo` | | Rewind one turn: restore the agent's file edits and stage the boundary (`src/vtx/tui/commands/sessions.py:276`) | No |
| `/redo` | | Step forward again, or clear the revert at the newest turn (`src/vtx/tui/commands/sessions.py:303`) | No |
| `/compact` | `[focus]` | Compact the context now; any text steers the summary (`src/vtx/tui/commands/sessions.py:485`) | No |
| `/recap` | | Draft a "where you left off" summary with the current model (`src/vtx/tui/recap.py:211`) | No |
| `/export` | | Write the session to an HTML file in the working directory (`src/vtx/tui/commands/sessions.py:446`) | No |
| `/copy` | | Copy the last assistant message to the clipboard (`src/vtx/tui/commands/sessions.py:470`) | No |

`/handoff` requires a query; without one it prints `Usage: /handoff <query>. Example: /handoff implement phase two`. It refuses while the agent is running.

### Models, providers, and settings

| Command | Arguments | What it does | Model turn |
|---|---|---|---|
| `/model` | | Open the model picker, recent models first (`src/vtx/tui/commands/models.py:47`) | No |
| `/model refresh` | | Refresh the model catalog for every dynamic provider (`src/vtx/tui/commands/models.py:50`) | No |
| `/model refresh` | `<provider>` | Refresh one provider's catalog (`src/vtx/tui/commands/models.py:53`) | No |
| `/provider` | | Open the picker that scopes `/model` to one provider (`src/vtx/tui/commands/providers.py:16`) | No |
| `/provider` | `reset`\|`all`\|`clear` | Clear the provider filter (`src/vtx/tui/commands/providers.py:30`) | No |
| `/settings` | | Open the settings picker: themes, permissions, thinking, notifications, thinking lines, colored badge, git context, ponytail, welcome shortcuts (`src/vtx/tui/commands/settings.py:234`) | No |
| `/themes` | `[theme]` | Switch theme; with no argument open the theme picker (`src/vtx/tui/commands/settings.py:51`) | No |
| `/permissions` | `[prompt\|auto]` | Set permission mode; with no argument open the picker (`src/vtx/tui/commands/settings.py:76`) | No |
| `/thinking` | `[level]` | Set the thinking effort; with no argument open the picker (`src/vtx/tui/commands/settings.py:121`) | No |
| `/effort` | `[level]` | Alias for `/thinking` (`src/vtx/tui/commands/__init__.py:81`) | No |
| `/notifications` | `[on\|off]` | Toggle notification sounds (`src/vtx/tui/commands/settings.py:209`) | No |
| `/ponytail` | `[on\|off]` | Toggle the ponytail system-prompt mode. With no argument it inverts the current value (`src/vtx/tui/commands/settings.py:279`) | No |

`/permissions` accepts `prompt` and `auto`; an invalid value prints `Invalid permission mode: <x>. Use one of: prompt, auto` (`src/vtx/tui/commands/base.py:134`). `/thinking` accepts whatever levels `effective_thinking_levels` offers for the active model and prints that list as `Invalid thinking level: <x>. Use one of: ...` (`src/vtx/tui/commands/settings.py:135`). `/ponytail` accepts `on`/`true`/`1`/`enable` and `off`/`false`/`0`/`disable`, and inverts the current value with no argument; anything else prints `Usage: /ponytail [on|off]` (`src/vtx/tui/commands/settings.py:282`).

Turning ponytail or git context on only takes effect on the next new conversation or a restart; the command says so.

### Authentication

| Command | Arguments | What it does | Model turn |
|---|---|---|---|
| `/login` | | Open the login method picker: OAuth (GitHub Copilot, OpenAI Codex, Cline), an API key for any keyed provider, or a keyless/local provider (`src/vtx/tui/commands/auth.py:74`) | No |
| `/logout` | | Open the provider picker listing everything with stored credentials (`src/vtx/tui/commands/auth.py:426`) | No |

`/login` walks three sub-pickers. For an API key, the input box switches to paste mode; submitting a value starting with `/` cancels the entry. `/logout` prints `No providers logged in` when nothing is stored.

### Agents

| Command | Arguments | What it does | Model turn |
|---|---|---|---|
| `/agent` | | Open the agent picker; the first row is `(no agent)` (`src/vtx/tui/commands/agents.py:77`) | No |
| `/agent list`, `/agent ls` | | List every discovered agent with the active one marked (`src/vtx/tui/commands/agents.py:149`) | No |
| `/agent current`, `/agent status` | | Print the active agent, or `No active agent (default session profile).` (`src/vtx/tui/commands/agents.py:151`) | No |
| `/agent reload` | | Re-discover agents and re-apply the tool set, keeping the active agent if it still resolves (`src/vtx/tui/commands/agents.py:44`) | No |
| `/agent off`, `/agent none`, `/agent default` | | Deactivate the active agent (`src/vtx/tui/commands/agents.py:155`) | No |
| `/agent` | `<name>` | Activate a named agent; `Agent '<name>' not found.` when it does not resolve (`src/vtx/tui/commands/agents.py:157`) | No |
| `/switch` | `<name>` | Alias for `/agent <name>` (`src/vtx/tui/commands/switch.py:15`) | No |

### Runtime

| Command | Arguments | What it does | Model turn |
|---|---|---|---|
| `/reload` | | Re-apply config, extensions, agents, tools, skills, and MCP servers in place, then report what changed (`src/vtx/tui/commands/reload.py:54`) | No |
| `/update` | | Check for and install the latest vtx release (`src/vtx/tui/commands/update.py:15`) | No |
| `/mcp` | | List every configured MCP server, its state, and its tool count (`src/vtx/tui/commands/mcp.py:43`) | No |
| `/skills` | | List the loaded skills grouped by category, local ones marked (`src/vtx/tui/commands/catalog.py:34`) | No |
| `/tools` | | List the loaded tools: built-ins first, then one group per MCP server (`src/vtx/tui/commands/catalog.py:53`) | No |
| `/mcp reload` | | Re-read `mcp.json` and reconnect every server (`src/vtx/tui/commands/mcp.py:46`) | No |
| `/mcp reconnect` | `<name>` | Rebuild one server's connection and re-derive the tool set (`src/vtx/tui/commands/mcp.py:49`) | No |
| `/mcp enable` | `<name>` | Enable a server and write `enabled: true` into its `mcp.json` (`src/vtx/tui/commands/mcp.py:49`) | No |
| `/mcp disable` | `<name>` | Disable a server and write it to its `mcp.json` (`src/vtx/tui/commands/mcp.py:49`) | No |
| `/mcp signin` | `<name>` | Authorize a remote server; opens a browser and always prints the URL (`src/vtx/tui/commands/mcp.py:55`) | No |
| `/mcp signout` | `<name>` | Discard a server's stored credentials (`src/vtx/tui/commands/mcp.py:61`) | No |

`/mcp` also accepts the aliases `list`, `ls`, `sign-in`, `login`, `auth`, `sign-out`, and `logout`. An unknown action prints the usage line. `signin` on a stdio server is refused: it runs as a local process, so there is nothing to sign in to.

A project's `.vtx/mcp.json` is read on every start and its servers are connected, so there is no trust step to run. `/reload` refuses while a turn is running and says to press Escape first.

## Skill-registered commands

A skill becomes a slash command when its `SKILL.md` frontmatter sets `register_cmd: true` (`src/vtx/agent/context/skills.py:211`). The name of the command is the skill's `name`, not its directory path. Unlike the built-ins, these **do cost a model turn**: the skill body is rendered into a prompt with `render_skill_prompt` and sent to the agent (`src/vtx/tui/app.py:1135`).

```yaml
---
name: review
description: Review code changes and return prioritized, actionable findings
register_cmd: true
cmd_info: review code changes
---
```

| Value | Effect |
|---|---|
| `register_cmd: false` (default when absent) | The skill is context for the agent only; no `/<name>` command |
| `register_cmd: true` | Registers `/<name>` and keeps the skill in the system prompt |
| `register_cmd: only` | Registers `/<name>` but leaves the skill out of the system prompt, for heavy skills loaded on demand |

The label shown in the command menu is `cmd_info` when set, otherwise the `description` truncated to 32 characters (`MAX_CMD_INFO_LENGTH`, `src/vtx/agent/context/skills.py:27`) with `...` appended when it is longer (`src/vtx/tui/app.py:496`). A skill whose name collides with a built-in command is unreachable and is not listed (`src/vtx/tui/app.py:491`).

Skills are loaded per invocation from `<cwd-or-ancestor>/.agents/skills/` (walking up to the git root), `~/.agents/skills`, then `~/.vtx/skills`, which holds synchronised built-ins at the lowest priority (`src/vtx/agent/context/skills.py:409`, `src/vtx/agent/context/skills.py:309`). The built-in skills registered from Python packages are merged by name, and a project or user skill of the same name wins over the built-in (`src/vtx/agent/context/skills.py:499`, `src/vtx/tui/app.py:484`).

The built-in skills that register commands are:

| Command | Label (`cmd_info`) | Description |
|---|---|---|
| `/google-colab` | run code on Google Colab | Manage Google Colab sessions from the terminal using the `colab` CLI |
| `/modal` | run code on Modal cloud | Run Python code in the cloud with Modal serverless containers |
| `/review` | review code changes | Review code changes and return prioritized, actionable findings |
| `/github` | (description, truncated) | Run GitHub CLI commands, manage repositories, issues, PRs, releases, actions |
| `/goal` | create or manage a project goal | Create and manage persistent project goals with tasks, verification, and audits |
| `/skill-builder` | scaffold a new skill | Create a new Vtx skill (SKILL.md) with the correct frontmatter, body, and conventions |
| `/vtx-self-extend` | extend Vtx itself | Add capability to Vtx itself - a skill, a plugin row, or an extension |
| `/init` | guided AGENTS.md setup | Create or update AGENTS.md for this repository |

`/goal` additionally lazily injects the `goal` tool for the active turn (`src/vtx/tui/app.py:1139`).

A skill can also be invoked explicitly with the `/skill:<name> <query>` form. This only works for skills that set `register_cmd`; a skill without it is agent context alone and cannot be invoked this way (`src/vtx/tui/app.py:1098`):

```text
/skill:review
/skill:review the staged diff only
```

Arguments typed after the command name are passed through as the query: `$ARGUMENTS` in the skill body is replaced when present, otherwise the query is appended after a blank line. The body is wrapped in a `<skill name=... location=...>` element (`src/vtx/agent/context/skills.py:470`).

## Extension-registered commands

An extension registers a command with `api.register_command(name, description, handler)` (`src/vtx/plugins/api.py:292`). These dispatch without a model turn and are appended after the skills in the menu, prefixed with `[ext]` (`src/vtx/tui/app.py:509`).

The handler returns a `CommandOutcome`; its `output` string is posted to the chat as `/<name> (from <owner>): <output>`, and a handler that raises is reported as `Extension command /<name> failed: <msg>` rather than crashing the app (`src/vtx/tui/commands/__init__.py:166`).

## Shell commands

Not slash commands, but routed from the same input box (`src/vtx/tui/agent_runner.py:670`):

| Prefix | Behavior | Model turn |
|---|---|---|
| `!<cmd>` | Run `<cmd>` through the bash tool and show the output | No |
| `!!<cmd>` | Same, then send the command and its output to the agent | Yes |

Both are refused while a turn is running.

## Known gaps

- `/themes`, `/permissions`, `/thinking`, `/effort`, `/notifications`, and `/switch` are routed but are **not** in `DEFAULT_COMMANDS`, so they never appear in the autocomplete menu. `/themes`, `/permissions`, `/thinking` and `/notifications` are in `/help` instead, which uses a separate, partially overlapping list (`src/vtx/tui/chat.py:447`); `/effort` and `/switch` are in neither.
- `/help` and the autocomplete menu do not agree: `/help` omits `settings`, `ponytail`, `tree`, `undo`, `redo`, `recap`, `agent`, `reload`, and `mcp`.
- `/mcp` does not accept a server name with `/mcp list <name>`; the report is always all servers.
- `/reload` accepts an argument but ignores it.
- `/export` writes into the working directory and has no `--path` argument; the destination name is chosen by `export_session_html`.
- `/model refresh` reports per-provider counts but does not list which models changed.
- There is no `/hotkeys` or equivalent command that dumps the keybinding table; `/help` shows a hand-maintained subset. See [keybindings](keybindings.md).
- Skill command arguments are positional text only. There is no per-command argument schema or completion.
- Extension commands cannot declare keybindings through the command API; `api.register_shortcut` records a handler, but the TUI does not bind it to a key.
- Two comments claim extensions can shadow built-in commands. They cannot: every built-in name is tested first, so an extension only runs for names no built-in claims. See `src/vtx/tui/commands/__init__.py:146` and `src/vtx/tui/app.py:504`.

## See also

- [Command line](cli.md) for the executable's flags and subcommands
- [Keybindings](keybindings.md) for the TUI key map
- [Plugins](plugins.md) for the extension API that registers commands
- [Tools](tools.md) for the tool set the agent calls on its own
