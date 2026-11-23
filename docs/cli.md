# Command Line

This page documents every flag, subcommand, and exit code of the `ctx` executable, read from the argument parser in `src/ctx/code/cli.py:18`. Run `ctx --help`, or append `--help` to a subcommand, for the same interface in your installed version.

```sh
ctx [options]
ctx acp
ctx auth status
ctx models list [--format json]
ctx config <list|get|set> ...
ctx update
ctx plugin <subcommand> [--profile NAME]
```

The `ctx` script is the `ctx.code.cli:main` entry point (`pyproject.toml:112`). With no `--prompt`, `ctx` starts the terminal UI (`src/ctx/code/cli.py:410`).

## Global flags

`build_parser()` registers exactly the following options (`src/ctx/code/cli.py:81`-`src/ctx/code/cli.py:183`). "Default" is the argparse default; where a flag falls back to a config value, the resolving line is cited.

| Flag | Short | Type | Default | Description |
|---|---|---|---|---|
| `--model` | `-m` | str | `None` | Model to use |
| `--provider` | | choice | `None` | Provider to use |
| `--prompt` | `-p` | str, `nargs="?"` | `None` | Run a single prompt non-interactively, then exit (omit the value or pipe stdin to read the prompt from stdin) |
| `--api-key` | `-k` | str | `None` | API key |
| `--base-url` | `-u` | str | `None` | Base URL for API |
| `--openai-compat-auth` | | choice | `None` | Auth mode for OpenAI-compatible endpoints |
| `--anthropic-compat-auth` | | choice | `None` | Auth mode for Anthropic-compatible endpoints |
| `--insecure-skip-verify` | | flag | `False` | Skip TLS verification (e.g. self-signed certs on local providers) |
| `--continue` | `-c` | flag | `False` | Resume the most recent session |
| `--resume` | `-r` | str | `None` | Resume a specific session by ID (full or unique prefix) |
| `--extension` | `-e` | PATH (repeatable) | `[]` | Load a Python extension file or package from PATH (repeatable) |
| `--no-extensions` | | flag | `False` | Skip auto-discovered extensions in `.ctx/extensions/` and `~/.ctx/agent/extensions/` |
| `--agent` | `-a` | NAME | `None` | Activate a handoff agent at session start (name of a `.ctx/agent/<name>.py`) |
| `--agent-file` | | PATH (repeatable) | `[]` | Load an additional agent file or package from PATH (repeatable) |
| `--no-agents` | | flag | `False` | Skip auto-discovered agents in `.ctx/agent/` and `~/.ctx/agent/` |
| `--list-agents` | | flag | `False` | List all available agents and exit |
| `--list-extensions` | | flag | `False` | List the extensions this composition loaded, then exit |
| `--profile` | | NAME | `None` | Plugin profile to boot (default: `'default'`; see `ctx plugin profiles`) |
| `--patch` | | PATH (repeatable) | `[]` | Apply a plugin patch layer after the profile's own layer (repeatable) |
| `--dump-config` | | flag | `False` | Print the composed plugin row list for the selected profile and exit |
| `--version` | | version action | | Real help: `show program's version number and exit`. Prints `ctx <VERSION>`, where `VERSION` is the string `editable` on an editable install |

`--prompt` is declared with `nargs="?"`, `const="-"`, so `-p` with no value reads the prompt from stdin (`src/ctx/code/cli.py:83`-`src/ctx/code/cli.py:91`). Values are stripped before use (`src/ctx/code/headless.py:41`).

## Models, providers, and authentication

```sh
ctx --model gpt-5.5 --provider openai-codex
ctx --model qwen3-coder --base-url http://localhost:11434/v1 --provider ollama
ctx --api-key sk-... --model deepseek-chat --provider deepseek
```

`--provider` takes only the slugs in `PROVIDER_API_BY_NAME` (`src/ctx/ai/providers/__init__.py:10`), sorted:

```text
aerolink, airouter, anthropic, codex, deepseek, kilo, ollama, openai,
openai-codex, openai-responses, opencode, opengateway, openrouter,
tokenrouter, zhipu, zyloo
```

An unknown slug is an argparse error (exit 2).

| Flag | Choices | Config fallback |
|---|---|---|
| `--openai-compat-auth` | `auto`, `required`, `none` | `config.llm.auth.openai_compat` (default `auto`) |
| `--anthropic-compat-auth` | `auto`, `required`, `none` | `config.llm.auth.anthropic_compat` (default `auto`) |

Resolution order for the model is `--model`, then `last_selected.model_id`, then `config.llm.default_model` (`src/ctx/term/app.py:144`); the same order applies headless (`src/ctx/code/headless.py:140`). `--provider` falls back to `last_selected.provider`, then `config.llm.default_provider` when `--model` is absent (`src/ctx/term/app.py:145`-`src/ctx/term/app.py:153`).

`--api-key` is a non-persistent override for the run; it does not write the credential store. Use `/login` in the TUI to store a key (see [slash commands](slash-commands.md)).

`--insecure-skip-verify` writes `config.llm.tls.insecure_skip_verify = True` before the app starts (`src/ctx/code/cli.py:310`).

```sh
ctx --provider openrouter --insecure-skip-verify
```

## Sessions

```sh
ctx --continue
ctx --resume 3f9a1c
```

`-c/--continue` resumes the most recent session; `-r/--resume` takes a full session ID or a unique prefix (`src/ctx/code/cli.py:109`-`src/ctx/code/cli.py:121`). Both are forwarded to `Ctx` by `run_tui` (`src/ctx/term/launch.py:84`, `src/ctx/term/launch.py:85`).

Neither may be combined with `--prompt`:

```console
$ ctx -p "hi" -c
usage: ctx [--model MODEL] [--provider {16 slugs}] [--prompt [PROMPT]] ...
ctx: error: -c/--continue and -r/--resume are not supported with -p/--prompt
```

Exit code 2 (`src/ctx/code/cli.py:307`-`src/ctx/code/cli.py:308`).

## Extensions and agents

```sh
ctx --extension ./tools/audit-logger/ --extension ~/hooks/gate.py
ctx --agent code-review
ctx --no-extensions --no-agents
ctx --list-agents
ctx --list-extensions
```

`--extension` and `--agent-file` are forwarded to `run_tui` as `extra_extension_paths` and `extra_agent_paths` (`src/ctx/term/launch.py:88`, `src/ctx/term/launch.py:91`). Agent paths are combined with `config.agents.files` (`src/ctx/term/app.py:246`-`src/ctx/term/app.py:251`); on the one-shot path extension paths are combined with `config.extensions` (`src/ctx/code/cli.py:382`-`src/ctx/code/cli.py:386`). `--no-extensions` and `--no-agents` turn off discovery of `.ctx/extensions/`, `~/.ctx/agent/extensions/`, `.ctx/agent/`, and `~/.ctx/agent/` (`src/ctx/code/cli.py:369`, `src/ctx/code/cli.py:385`, `src/ctx/code/cli.py:405`, `src/ctx/term/app.py:246`).

`--list-agents` prints one tab-separated row per agent (`name`, `description`, `path`) then exits 0 (`src/ctx/code/cli.py:313`-`src/ctx/code/cli.py:324`). With nothing discovered it prints `No agents found.` Load errors go to stderr as `agent error: <msg>`.

`--list-extensions` prints one row per loaded extension, plus indented `tools:`, `commands:`, and `handlers:` lines (`src/ctx/code/cli.py:187`-`src/ctx/code/cli.py:203`, dispatched at `src/ctx/code/cli.py:375`):

```console
$ ctx --list-extensions
No extensions loaded.
```

Extension errors print to stderr as `extension error: <msg>` and the exit code stays 0.

See [extensions](extensions.md) for the extension API and [plugins](plugins.md) for the row model behind it.

## Plugin composition

Three flags select and inspect the plugin row list the process boots from. Full background is in [plugins](plugins.md).

```sh
ctx --profile minimal
ctx --profile default --patch ./overrides.yml
ctx --dump-config
ctx --dump-config --profile minimal
```

`--profile` names the profile; when omitted, `boot()` reads `CTX_PROFILE`, then falls back to `default` (`src/ctx/plugins/bootstrap.py:38`). `--patch` layers are applied after the profile's own `cordis.yml`, in the order given (`src/ctx/plugins/profile.py:132`).

`--dump-config` prints `<id>\t[enabled|disabled]\t<name>` per row and exits 0 (`src/ctx/code/cli.py:352`-`src/ctx/code/cli.py:355`). Rows discovered from extensions at boot are appended to the listing (`src/ctx/code/cli.py:337`-`src/ctx/code/cli.py:351`).

```console
$ ctx --dump-config
tools	[enabled]	ctx.plugins.builtins.services.tools:PLUGIN
llm	[enabled]	ctx.plugins.builtins.services.llm:PLUGIN
...
```

A composition that fails to build exits 1 with `ctx: plugin composition failed: <msg>` on stderr (`src/ctx/code/cli.py:371`-`src/ctx/code/cli.py:373`).

## Non-interactive mode

```sh
ctx -p "Summarize the diff"
echo "Review this patch" | ctx -p
```

`-p` runs one turn through `run_headless` and exits (`src/ctx/code/cli.py:378`-`src/ctx/code/cli.py:408`). Only the final assistant text goes to stdout (`src/ctx/code/headless.py:89`-`src/ctx/code/headless.py:90`); errors, extension errors, agent errors, and MCP warnings go to stderr.

Permission mode is forced to `auto` for the duration of the run: the config file is rewritten to `auto`, reloaded, and written back to its previous mode in a `finally` (`src/ctx/code/headless.py:129`-`src/ctx/code/headless.py:136`, `src/ctx/code/headless.py:309`-`src/ctx/code/headless.py:313`). Tool approval requests are auto-denied and reported (`src/ctx/code/headless.py:74`-`src/ctx/code/headless.py:79`); `ask_user` questions are reported as unanswered (`src/ctx/code/headless.py:80`-`src/ctx/code/headless.py:86`).

## Subcommand: `ctx update`

```sh
ctx update
```

Self-updates the `ctx-code` distribution to the latest PyPI release and exits (`src/ctx/code/cli.py:223`-`src/ctx/code/cli.py:232`). The installer is chosen from the environment: `CTX_UPDATE_USE_PIP` forces pip, otherwise a detected `uv tool` install is upgraded with `uv tool upgrade`, a `pipx` install with `pipx upgrade`, then plain `uv pip install --upgrade`, then `pip` (`src/ctx/core/self_update.py:46`-`src/ctx/core/self_update.py:57`).

On success it prints `ctx update: <message>` and exits 0; on failure `ctx update failed: <message>` to stderr and exit 1 (`src/ctx/code/cli.py:228`, `src/ctx/code/cli.py:230`).

## Subcommand: `ctx plugin`

```sh
ctx plugin <profiles|init|path|enable|disable|install|allow-version|revoke-version|version-exemptions>
```

`ctx plugin` is dispatched before argparse sees the rest of the command line, so `--profile` may appear anywhere in the subcommand's arguments (`src/ctx/code/cli.py:210`-`src/ctx/code/cli.py:213`, `src/ctx/code/plugin_cmd.py:205`-`src/ctx/code/plugin_cmd.py:212`).

Running `ctx plugin` with no arguments, or with `-h`/`--help`, prints the module docstring and the one-line command list, and exits 0 (`src/ctx/code/plugin_cmd.py:201`-`src/ctx/code/plugin_cmd.py:204`).

| Subcommand | Arguments | Behavior |
|---|---|---|
| `profiles` | | List profile templates with their bundle lists, every bundle with its row count, and the default profile (`plugin_cmd.py:39`-`plugin_cmd.py:47`) |
| `init` | `NAME` | Create `~/.ctx/profiles/NAME/` with `bundle.txt` and a commented `cordis.yml` patch-layer template (`plugin_cmd.py:217`-`plugin_cmd.py:222`, `profile.py:74`-`profile.py:86`) |
| `path` | `NAME` | Print the profile's directory (`plugin_cmd.py:50`-`plugin_cmd.py:52`, `plugin_cmd.py:223`-`plugin_cmd.py:226`) |
| `enable` | `ROW_ID` | Clear the row's `disabled` flag in the profile's patch (`plugin_cmd.py:78`-`plugin_cmd.py:100`) |
| `disable` | `ROW_ID` | Set `disabled: true` on the row in the profile's patch (`plugin_cmd.py:78`-`plugin_cmd.py:100`) |
| `install` | `PACKAGE_OR_URL` | Install a plugin package or clone a git URL (`plugin_cmd.py:127`-`plugin_cmd.py:146`) |
| `allow-version` | `PACKAGE@VERSION` | Grant one exact package/version pair for one exact ctx version (`plugin_cmd.py:149`-`plugin_cmd.py:184`) |
| `revoke-version` | `PACKAGE@VERSION` | Revoke a previously granted exemption (`plugin_cmd.py:149`-`plugin_cmd.py:184`) |
| `version-exemptions` | | Print the profile's exemptions as JSON (`plugin_cmd.py:187`-`plugin_cmd.py:196`) |

Every failure prints `ctx plugin: <message>` on stderr and returns 1 (`src/ctx/code/plugin_cmd.py:34`-`src/ctx/code/plugin_cmd.py:36`).

### `ctx plugin profiles`

```console
$ ctx plugin profiles
profiles:
  default	base, tui
  headless	base
  minimal	minimal
bundles:
  base	27 rows
  tui	2 rows
  headless	0 rows
  minimal	4 rows

default profile: default
```

The profile templates and bundles come from `src/ctx/plugins/builtins/__init__.py:88`-`src/ctx/plugins/builtins/__init__.py:100`.

### `ctx plugin init` and `ctx plugin path`

```console
$ ctx plugin init demo
ctx plugin: initialised /home/you/.ctx/profiles/demo
$ ctx plugin path demo
/home/you/.ctx/profiles/demo
```

`init` writes `bundle.txt` from the profile template, or `DEFAULT_PROFILE_BUNDLES` (`base`, `tui`) when the name has no template (`src/ctx/plugins/profile.py:81`), and writes `cordis.yml` only when it does not already exist (`src/ctx/plugins/profile.py:83`-`src/ctx/plugins/profile.py:85`). The patch file starts as a commented template ending in `[]`, not an empty document.

A profile name containing `/`, `\`, `.` or `..`, or starting with `-`, is rejected with `invalid profile name` (`src/ctx/plugins/profile.py:68`-`src/ctx/plugins/profile.py:71`).

### `ctx plugin enable` / `disable`

```console
$ ctx plugin disable tool-grep
ctx plugin: tool-grep disabled in profile 'default'
$ ctx plugin enable tool-grep
ctx plugin: tool-grep enabled in profile 'default'
```

Both rewrite only the matching row's `disabled` flag in `cordis.yml`, appending an `override` entry when the row has none yet (`src/ctx/code/plugin_cmd.py:89`). Unknown rows are refused (`src/ctx/code/plugin_cmd.py:88`):

```console
$ ctx plugin enable bogus-row
ctx plugin: unknown row 'bogus-row'; run `ctx --dump-config` to list them
```

Missing arguments produce `enable needs a row id` / `disable needs a row id`.

Not every tool row disables its tool. `tool-web`, `tool-ask-user`, and
`tool-subagent` are inert: their tools are registered at import time with
`is_default=True` (`src/ctx/agent/tools/__init__.py:216`-`src/ctx/agent/tools/__init__.py:218`), so the row unmounts and
the tool stays live. Verify with the live tool set (`ctx.tools.assemble()`), not with
`[disabled]` in the row listing alone - see [plugins.md](plugins.md).

### `ctx plugin install`

```sh
ctx plugin install ctx-plugin-foo
ctx plugin install --upgrade ctx-plugin-foo
ctx plugin install https://github.com/org/repo
```

A spec starting with `http://`, `https://`, `git@`, `ssh://`, or `git://` is cloned with `git clone --depth 1` into a temp directory and installed from there (`src/ctx/code/plugin_cmd.py:135`, `src/ctx/plugins/install.py:287`-`src/ctx/plugins/install.py:294`). Anything else goes through `uv pip install` (run as `python -m uv pip install`), with `--upgrade` appended when the flag is present (`src/ctx/plugins/install.py:264`-`src/ctx/plugins/install.py:267`).

The version gate reads the plugin's declared `ctx` requirement from `pyproject.toml`, but only a local path or directory spec can be judged before anything is downloaded; a PyPI name, git URL, or tarball is marked "unjudged until installed" (`src/ctx/plugins/install.py:177`-`src/ctx/plugins/install.py:207`). An incompatible package is refused with the exact exemption command to grant it (`src/ctx/plugins/install.py:255`-`src/ctx/plugins/install.py:262`); a spec judged only after install is verified once on disk and rolled back with `uv pip uninstall` when it fails (`src/ctx/plugins/install.py:272`-`src/ctx/plugins/install.py:283`).

On success the command prints the install message and the reminder to add a row to the profile:

```console
ctx plugin: installed ctx-plugin-foo
ctx plugin: add a row for it in ~/.ctx/profiles/default/cordis.yml with `- insert:`
```

### `ctx plugin allow-version` and `revoke-version`

```sh
ctx plugin allow-version ctx-plugin-foo@1.4.2 --ctx-version 1.2.2 --accept-risk
ctx plugin revoke-version ctx-plugin-foo@1.4.2 --ctx-version 1.2.2
```

`--ctx-version` is required. Both spellings `--ctx-version X` and `--ctx-version=X` are accepted (`src/ctx/code/plugin_cmd.py:155`-`src/ctx/code/plugin_cmd.py:163`).

`allow-version` prints a risk warning to stderr before writing anything, and refuses without `--accept-risk` (`src/ctx/code/plugin_cmd.py:166`-`src/ctx/code/plugin_cmd.py:170`, `src/ctx/plugins/install.py:77`-`src/ctx/plugins/install.py:81`). Grants are stored in `~/.ctx/profiles/<profile>/compatibility.json` and apply to exactly that package/version pair on exactly that ctx version (`src/ctx/plugins/install.py:50`-`src/ctx/plugins/install.py:51`).

```console
$ ctx plugin allow-version ctx-plugin-foo@1.4.2 --ctx-version 1.2.2
ctx plugin: usage: ctx plugin allow-version <package@version> --ctx-version <exact>
```

When the granted ctx version differs from the running one, a note follows the grant (`src/ctx/code/plugin_cmd.py:179`-`src/ctx/code/plugin_cmd.py:183`).

### `ctx plugin version-exemptions`

```console
$ ctx plugin version-exemptions
{}
```

Prints the exemptions mapping as indented JSON, exit 0. Unreadable entries are reported as warnings on stderr first (`src/ctx/plugins/install.py:54`-`src/ctx/plugins/install.py:70`, printed at `src/ctx/code/plugin_cmd.py:192`-`src/ctx/code/plugin_cmd.py:194`).

## Subcommand: `ctx acp`

```sh
ctx acp
```

Runs ctx as an Agent Client Protocol (ACP) server over stdio: JSON-RPC 2.0, one object per line, for editors like Zed that speak the protocol (`src/ctx/code/acp.py:1`-`src/ctx/code/acp.py:13`, dispatched at `src/ctx/code/cli.py:217`-`src/ctx/code/cli.py:221`).

The server mounts the plugin composition before serving -- the `headless` profile, or `CTX_PROFILE` when set, with the `extensions:` list from `~/.ctx/config.yml` passed through. That mount is what registers the tools, because the coding agent's tools are plugin rows rather than harness defaults; without it an ACP session would offer the model only the three harness-native tools (`web`, `ask_user`, `delegate_subagent`) and no way to touch the workspace. Assembling from `ctx.tools` also means an edited row list stays authoritative, and the runtime picks up the composition's agent loop and prompt builder (`src/ctx/code/acp.py:81`, `:1331`).

The `usage_update` notification carries **context occupancy**, not spend: `used` is the token footprint of the conversation (what compaction compares against the window) and `size` is the active model's context window. It is sent after each turn, after a compaction shrinks the context, and once when a session is loaded so a client's meter is correct on resume. Identical consecutive readings are suppressed, and a reading with no context yet is not sent at all -- clients hide the meter on a zero rather than render `0%` (`AcpServer._emit_usage`).

## Subcommand: `ctx auth`

```sh
ctx auth status
```

The only `auth` subcommand is `status`: it prints `Logged in.` when the configured or env-detected provider has credentials, `Not logged in.` otherwise, and exits 0 (`src/ctx/code/cli.py:241`-`src/ctx/code/cli.py:249`). Login and logout flows are TUI slash commands (`/login`, `/logout`), not CLI subcommands.

## Subcommand: `ctx models`

```sh
ctx models list [--format json]
```

`models list` prints a JSON document with `families`, `providers`, and the active `provider_filter`, then exits 0 (`src/ctx/code/cli.py:251`-`src/ctx/code/cli.py:287`). `--format json` is accepted but the output is JSON either way.

## Subcommand: `ctx config`

```sh
ctx config list
ctx config get ui.model_provider_filter
ctx config set ui.theme '"dracula"'
ctx config set llm.default_model null
```

Reads and writes the persisted `~/.ctx/config.yml` without opening the TUI (`src/ctx/code/cli.py:289`-`src/ctx/code/cli.py:305`, setters in `src/ctx/core/config.py:1179`-`src/ctx/core/config.py:1216`):

| Subcommand | Behavior |
|---|---|
| `list` | Dump the persisted config file as indented JSON |
| `get KEY` | Print one dotted key's raw value as JSON (`null` when unset) |
| `set KEY VALUE` | Set one dotted key; `VALUE` is parsed as JSON with a bare-string fallback, and JSON `null` removes the key |

## Exit codes

| Code | Meaning | Source |
|---|---|---|
| `0` | Clean exit, or a one-shot path (`--list-agents`, `--list-extensions`, `--dump-config`, `ctx acp`, `ctx auth status`, `ctx models list`, `ctx config ...`, `ctx update`, `ctx plugin ...`) | `src/ctx/code/cli.py:221`, `src/ctx/code/cli.py:249`, `src/ctx/code/cli.py:287`, `src/ctx/code/cli.py:305`, `src/ctx/code/cli.py:324`, `src/ctx/code/cli.py:355` |
| `1` | `ctx update` failure, plugin composition failure, or any `ctx plugin` error | `src/ctx/code/cli.py:231`, `src/ctx/code/cli.py:373`, `src/ctx/code/plugin_cmd.py:36` |
| `2` | argparse error (including `-c`/`-r` with `-p`), empty headless prompt, or provider/agent initialization failure | `src/ctx/code/cli.py:308`, `src/ctx/code/headless.py:121`, `src/ctx/code/headless.py:277`, `src/ctx/code/headless.py:282`, `src/ctx/code/headless.py:286` |
| `3` | Headless run ended with stop reason `length` (context or output limit) | `src/ctx/code/headless.py:34` |

The headless exit code is derived from the run's `StopReason`; an unmapped reason falls back to 1 (`src/ctx/code/headless.py:37`).

## Known gaps

- `--prompt` has no `--output`/format flag. Exactly one invocation and exactly one final text dump; there is no JSON or event-stream mode as of this writing.
- `--thinking` / `--effort` is not a CLI flag. The level comes from `last_selected`, then `config.llm.default_thinking_level` (default `low`), and can be changed at runtime with `/thinking` or `ctrl+t`.
- `--model` takes an exact model id. There is no fuzzy matching or `provider/model` shorthand on the command line; pick interactively with `/model`.
- `--api-key` is not persisted and is ignored by any provider that resolves its key from the environment or the stored credential file.
- `--dump-config` prints rows as tab-separated text. A JSON form exists in the library (`dump_config_json`) but no flag exposes it.
- `ctx plugin` has no `list` or `remove` subcommand; installed plugins are removed with the package manager directly.
- The `commands:` line that `ctx plugin` prints advertises only `profiles | init NAME | path NAME | enable ROW | disable ROW` (`src/ctx/code/plugin_cmd.py:203`). `install`, `allow-version`, `revoke-version`, and `version-exemptions` are dispatched but are not listed; `ctx plugin -h` and `ctx plugin <unknown>` are the only ways to discover them.
- `ctx plugin` is only intercepted when `plugin` is the first argument. `ctx --profile x plugin profiles` parses through argparse, which knows the subcommand but never dispatches it, so the run falls through to the TUI.
- `--no-extensions` skips the two scan roots but installed-package entry points (`ctx.extensions`) are still discovered (`src/ctx/plugins/extensions.py:145`, `src/ctx/plugins/extensions.py:172`-`src/ctx/plugins/extensions.py:174`).
- `--list-extensions` reports tools, commands, and handlers, but not shortcuts or flags registered by an extension.
- Extension and agent discovery silently skips unreadable directories; only the resulting per-entry errors are printed.
- The help text for `--provider` does not name the accepted slugs; only the `usage:` line lists them.
- `-p/--prompt` rewrites the saved config file to `permissions.mode: auto` and writes it back afterwards (`src/ctx/code/headless.py:133`-`src/ctx/code/headless.py:135`, `src/ctx/code/headless.py:310`-`src/ctx/code/headless.py:313`). It is restored, but two concurrent headless runs can restore whichever value they captured first. The comment at `src/ctx/code/headless.py:130`-`src/ctx/code/headless.py:131` claims it does not mutate the config; it does, for the duration of the run.

## See also

- [Slash commands](slash-commands.md) for the in-session command router
- [Keybindings](keybindings.md) for the TUI key map
- [Configuration](configuration.md) for `~/.ctx/config.yml`
- [Tools](tools.md) for the built-in tool set
- [Plugins](plugins.md) for profiles, bundles, and rows
- [Headless mode](headless.md) for the non-interactive runner
