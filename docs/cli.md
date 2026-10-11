# Command Line

This page documents every flag, subcommand, and exit code of the `vtx` executable, read from the argument parser in `src/vtx/coding_agent/cli.py:14`. Run `vtx --help`, or append `--help` to a subcommand, for the same interface in your installed version.

```sh
vtx [options]
vtx update
vtx plugin <subcommand> [--profile NAME]
```

The `vtx` script is the `vtx.coding_agent.cli:main` entry point (`pyproject.toml:112`). With no `--prompt`, `vtx` starts the terminal UI (`src/vtx/coding_agent/cli.py:302`).

## Global flags

`build_parser()` registers exactly the following options (`src/vtx/coding_agent/cli.py:52`-`src/vtx/coding_agent/cli.py:154`). "Default" is the argparse default; where a flag falls back to a config value, the resolving line is cited.

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
| `--no-extensions` | | flag | `False` | Skip auto-discovered extensions in `.vtx/extensions/` and `~/.vtx/agent/extensions/` |
| `--agent` | `-a` | NAME | `None` | Activate a handoff agent at session start (name of a `.vtx/agent/<name>.py`) |
| `--agent-file` | | PATH (repeatable) | `[]` | Load an additional agent file or package from PATH (repeatable) |
| `--no-agents` | | flag | `False` | Skip auto-discovered agents in `.vtx/agent/` and `~/.vtx/agent/` |
| `--list-agents` | | flag | `False` | List all available agents and exit |
| `--list-extensions` | | flag | `False` | List the extensions this composition loaded, then exit |
| `--profile` | | NAME | `None` | Plugin profile to boot (default: `'default'`; see `vtx plugin profiles`) |
| `--patch` | | PATH (repeatable) | `[]` | Apply a plugin patch layer after the profile's own layer (repeatable) |
| `--dump-config` | | flag | `False` | Print the composed plugin row list for the selected profile and exit |
| `--version` | | version action | | Real help: `show program's version number and exit`. Prints `vtx <VERSION>`, where `VERSION` is the string `editable` on an editable install |

`--prompt` is declared with `nargs="?"`, `const="-"`, so `-p` with no value reads the prompt from stdin (`src/vtx/coding_agent/cli.py:54`). Values are stripped before use (`src/vtx/coding_agent/headless.py:41`).

## Models, providers, and authentication

```sh
vtx --model gpt-5.5 --provider openai-codex
vtx --model qwen3-coder --base-url http://localhost:11434/v1 --provider ollama
vtx --api-key sk-... --model deepseek-chat --provider deepseek
```

`--provider` takes only the slugs in `PROVIDER_API_BY_NAME` (`src/vtx/ai/providers/__init__.py:4`), sorted:

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

Resolution order for the model is `--model`, then `last_selected.model_id`, then `config.llm.default_model` (`src/vtx/tui/app.py:145`); the same order applies headless (`src/vtx/coding_agent/headless.py:161`). `--provider` falls back to `last_selected.provider`, then `config.llm.default_provider` when `--model` is absent (`src/vtx/tui/app.py:146`).

`--api-key` is a non-persistent override for the run; it does not write the credential store. Use `/login` in the TUI to store a key (see [slash commands](slash-commands.md)).

`--insecure-skip-verify` writes `config.llm.tls.insecure_skip_verify = True` before the app starts (`src/vtx/coding_agent/cli.py:202`).

```sh
vtx --provider openrouter --insecure-skip-verify
```

## Sessions

```sh
vtx --continue
vtx --resume 3f9a1c
```

`-c/--continue` resumes the most recent session; `-r/--resume` takes a full session ID or a unique prefix (`src/vtx/coding_agent/cli.py:80`-`src/vtx/coding_agent/cli.py:92`). Both are forwarded to `Vtx` by `run_tui` (`src/vtx/tui/launch.py:90`, `src/vtx/tui/launch.py:91`).

Neither may be combined with `--prompt`:

```console
$ vtx -p "hi" -c
usage: vtx [--model MODEL] [--provider {16 slugs}] [--prompt [PROMPT]] ...
vtx: error: -c/--continue and -r/--resume are not supported with -p/--prompt
```

Exit code 2 (`src/vtx/coding_agent/cli.py:199`).

## Extensions and agents

```sh
vtx --extension ./tools/audit-logger/ --extension ~/hooks/gate.py
vtx --agent code-review
vtx --no-extensions --no-agents
vtx --list-agents
vtx --list-extensions
```

`--extension` and `--agent-file` are forwarded to `run_tui` as `extra_extension_paths` and `extra_agent_paths` (`src/vtx/tui/launch.py:94`, `src/vtx/tui/launch.py:97`). Agent paths are combined with `config.agents.files` (`src/vtx/tui/app.py:241`); on the one-shot path extension paths are combined with `config.extensions` (`src/vtx/coding_agent/cli.py:257`). `--no-extensions` and `--no-agents` turn off discovery of `.vtx/extensions/`, `~/.vtx/agent/extensions/`, `.vtx/agent/`, and `~/.vtx/agent/` (`src/vtx/coding_agent/cli.py:261`, `src/vtx/coding_agent/cli.py:295`, `src/vtx/tui/app.py:239`).

`--list-agents` prints one tab-separated row per agent (`name`, `description`, `path`) then exits 0 (`src/vtx/coding_agent/cli.py:205`). With nothing discovered it prints `No agents found.` Load errors go to stderr as `agent error: <msg>`.

`--list-extensions` prints one row per loaded extension, plus indented `tools:`, `commands:`, and `handlers:` lines (`src/vtx/coding_agent/cli.py:158`):

```console
$ vtx --list-extensions
No extensions loaded.
```

Extension errors print to stderr as `extension error: <msg>` and the exit code stays 0.

See [extensions](extensions.md) for the extension API and [plugins](plugins.md) for the row model behind it.

## Plugin composition

Three flags select and inspect the plugin row list the process boots from. Full background is in [plugins](plugins.md).

```sh
vtx --profile minimal
vtx --profile default --patch ./overrides.yml
vtx --dump-config
vtx --dump-config --profile minimal
```

`--profile` names the profile; when omitted, `boot()` reads `VTX_PROFILE`, then falls back to `default` (`src/vtx/plugins/bootstrap.py:38`). `--patch` layers are applied after the profile's own `cordis.yml`, in the order given (`src/vtx/plugins/profile.py:132`).

`--dump-config` prints `<id>\t[enabled|disabled]\t<name>` per row and exits 0 (`src/vtx/coding_agent/cli.py:244`). Rows discovered from extensions at boot are appended to the listing (`src/vtx/coding_agent/cli.py:235`).

```console
$ vtx --dump-config
tools	[enabled]	vtx.plugins.builtins.services.tools:PLUGIN
llm	[enabled]	vtx.plugins.builtins.services.llm:PLUGIN
...
```

A composition that fails to build exits 1 with `vtx: plugin composition failed: <msg>` on stderr (`src/vtx/coding_agent/cli.py:263`).

## Non-interactive mode

```sh
vtx -p "Summarize the diff"
echo "Review this patch" | vtx -p
```

`-p` runs one turn through `run_headless` and exits (`src/vtx/coding_agent/cli.py:270`). Only the final assistant text goes to stdout (`src/vtx/coding_agent/headless.py:89`); errors, extension errors, agent errors, and MCP warnings go to stderr.

Permission mode is forced to `auto` for the duration of the run: the config file is rewritten to `auto`, reloaded, and written back to its previous mode in a `finally` (`src/vtx/coding_agent/headless.py:149`, `src/vtx/coding_agent/headless.py:305`). Tool approval requests are auto-denied and reported (`src/vtx/coding_agent/headless.py:74`); `ask_user` questions are reported as unanswered (`src/vtx/coding_agent/headless.py:80`).

## Subcommand: `vtx update`

```sh
vtx update
```

Self-updates the `vtx-coding-agent` distribution to the latest PyPI release and exits (`src/vtx/coding_agent/cli.py:188`). The installer is chosen from the environment: `VTX_UPDATE_USE_PIP` forces pip, otherwise a detected `uv tool` install is upgraded with `uv tool upgrade`, a `pipx` install with `pipx upgrade`, then plain `uv pip install --upgrade`, then `pip` (`src/vtx/core/self_update.py:42`).

On success it prints `vtx update: <message>` and exits 0; on failure `vtx update failed: <message>` to stderr and exit 1 (`src/vtx/coding_agent/cli.py:192`, `src/vtx/coding_agent/cli.py:194`).

## Subcommand: `vtx plugin`

```sh
vtx plugin <profiles|init|path|enable|disable|install|allow-version|revoke-version|version-exemptions>
```

`vtx plugin` is dispatched before argparse sees the rest of the command line, so `--profile` may appear anywhere in the subcommand's arguments (`src/vtx/coding_agent/cli.py:181`, `src/vtx/coding_agent/plugin_cmd.py:205`).

Running `vtx plugin` with no arguments, or with `-h`/`--help`, prints the module docstring and the one-line command list, and exits 0 (`src/vtx/coding_agent/plugin_cmd.py:201`).

| Subcommand | Arguments | Behavior |
|---|---|---|
| `profiles` | | List profile templates with their bundle lists, every bundle with its row count, and the default profile (`plugin_cmd.py:39`) |
| `init` | `NAME` | Create `~/.vtx/profiles/NAME/` with `bundle.txt` and an empty `cordis.yml` patch layer (`plugin_cmd.py:217`, `profile.py:74`) |
| `path` | `NAME` | Print the profile's directory (`plugin_cmd.py:50`) |
| `enable` | `ROW_ID` | Clear the row's `disabled` flag in the profile's patch (`plugin_cmd.py:78`) |
| `disable` | `ROW_ID` | Set `disabled: true` on the row in the profile's patch (`plugin_cmd.py:78`) |
| `install` | `PACKAGE_OR_URL` | Install a plugin package or clone a git URL (`plugin_cmd.py:127`) |
| `allow-version` | `PACKAGE@VERSION` | Grant one exact package/version pair for one exact vtx version (`plugin_cmd.py:149`) |
| `revoke-version` | `PACKAGE@VERSION` | Revoke a previously granted exemption (`plugin_cmd.py:149`) |
| `version-exemptions` | | Print the profile's exemptions as JSON (`plugin_cmd.py:187`) |

Every failure prints `vtx plugin: <message>` on stderr and returns 1 (`src/vtx/coding_agent/plugin_cmd.py:34`).

### `vtx plugin profiles`

```console
$ vtx plugin profiles
profiles:
  default	base, tui
  headless	base
  minimal	minimal
bundles:
  base	29 rows
  tui	2 rows
  headless	0 rows
  minimal	4 rows

default profile: default
```

The profile templates and bundles come from `src/vtx/plugins/builtins/__init__.py:84`.

### `vtx plugin init` and `vtx plugin path`

```console
$ vtx plugin init demo
vtx plugin: initialised /home/you/.vtx/profiles/demo
$ vtx plugin path demo
/home/you/.vtx/profiles/demo
```

`init` writes `bundle.txt` from the profile template, or `DEFAULT_PROFILE_BUNDLES` (`base`, `tui`) when the name has no template (`src/vtx/plugins/profile.py:81`). An existing `cordis.yml` is left untouched (`src/vtx/plugins/profile.py:84`).

A profile name containing `/`, `\`, `.` or `..`, or starting with `-`, is rejected with `invalid profile name` (`src/vtx/plugins/profile.py:68`).

### `vtx plugin enable` / `disable`

```console
$ vtx plugin disable tool-grep
vtx plugin: tool-grep disabled in profile 'default'
$ vtx plugin enable tool-grep
vtx plugin: tool-grep enabled in profile 'default'
```

Both rewrite only the matching row's `disabled` flag in `cordis.yml`, appending an `override` entry when the row has none yet (`src/vtx/coding_agent/plugin_cmd.py:89`). Unknown rows are refused:

```console
$ vtx plugin enable bogus-row
vtx plugin: unknown row 'bogus-row'; run `vtx --dump-config` to list them
```

Missing arguments produce `enable needs a row id` / `disable needs a row id`.

Not every tool row disables its tool. `tool-web`, `tool-goal`, `tool-ask-user`, and
`tool-subagent` are inert: their tools are registered at import time with
`is_default=True` (`src/vtx/agent/tools/__init__.py:220-223`), so the row unmounts and
the tool stays live. Verify with the live tool set (`ctx.tools.assemble()`), not with
`[disabled]` in the row listing alone - see [plugins.md](plugins.md).

### `vtx plugin install`

```sh
vtx plugin install vtx-plugin-foo
vtx plugin install --upgrade vtx-plugin-foo
vtx plugin install https://github.com/org/repo
```

A spec starting with `http://`, `https://`, `git@`, `ssh://`, or `git://` is cloned with `git clone --depth 1` into a temp directory and installed from there (`src/vtx/coding_agent/plugin_cmd.py:135`, `src/vtx/plugins/install.py:285`). Anything else goes through `uv pip install`, with `--upgrade` appended when the flag is present (`src/vtx/plugins/install.py:262`).

The version gate reads the plugin's declared `vtx` requirement from `pyproject.toml` and refuses an incompatible package before downloading, printing the exact exemption command to grant it (`src/vtx/plugins/install.py:249`). A git or tarball spec cannot be judged until fetched, so it is verified after install and rolled back with `uv pip uninstall` when it fails (`src/vtx/plugins/install.py:271`).

On success the command prints the install message and the reminder to add a row to the profile:

```console
vtx plugin: installed vtx-plugin-foo
vtx plugin: add a row for it in ~/.vtx/profiles/default/cordis.yml with `- insert:`
```

### `vtx plugin allow-version` and `revoke-version`

```sh
vtx plugin allow-version vtx-plugin-foo@1.4.2 --vtx-version 1.2.2 --accept-risk
vtx plugin revoke-version vtx-plugin-foo@1.4.2 --vtx-version 1.2.2
```

`--vtx-version` is required. Both spellings `--vtx-version X` and `--vtx-version=X` are accepted (`src/vtx/coding_agent/plugin_cmd.py:158`).

`allow-version` prints a risk warning to stderr before writing anything, and refuses without `--accept-risk` (`src/vtx/plugins/install.py:77`). Grants are stored in `~/.vtx/profiles/<profile>/compatibility.json` and apply to exactly that package/version pair on exactly that vtx version.

```console
$ vtx plugin allow-version vtx-plugin-foo@1.4.2 --vtx-version 1.2.2
vtx plugin: usage: vtx plugin allow-version <package@version> --vtx-version <exact>
```

When the granted vtx version differs from the running one, a note follows the grant (`src/vtx/coding_agent/plugin_cmd.py:179`).

### `vtx plugin version-exemptions`

```console
$ vtx plugin version-exemptions
{}
```

Prints the exemptions mapping as indented JSON, exit 0. Unreadable entries are reported as warnings on stderr first (`src/vtx/plugins/install.py:54`).

## Exit codes

| Code | Meaning | Source |
|---|---|---|
| `0` | Clean exit, or a one-shot path (`--list-agents`, `--list-extensions`, `--dump-config`, `vtx update`, `vtx plugin ...`) | `src/vtx/coding_agent/cli.py:216` |
| `1` | `vtx update` failure, plugin composition failure, or any `vtx plugin` error | `src/vtx/coding_agent/cli.py:196`, `src/vtx/coding_agent/cli.py:265`, `src/vtx/coding_agent/plugin_cmd.py:35` |
| `2` | argparse error (including `-c`/`-r` with `-p`), empty headless prompt, or provider/agent initialization failure | `src/vtx/coding_agent/cli.py:200`, `src/vtx/coding_agent/headless.py:147`, `src/vtx/coding_agent/headless.py:274` |
| `3` | Headless run ended with stop reason `length` (context or output limit) | `src/vtx/coding_agent/headless.py:34` |

The headless exit code is derived from the run's `StopReason`; an unmapped reason falls back to 1 (`src/vtx/coding_agent/headless.py:37`).

## Known gaps

- `--prompt` has no `--output`/format flag. Exactly one invocation and exactly one final text dump; there is no JSON or event-stream mode as of this writing.
- `--thinking` / `--effort` is not a CLI flag. The level comes from `last_selected`, then `config.llm.default_thinking_level` (default `low`), and can be changed at runtime with `/thinking` or `ctrl+t`.
- `--model` takes an exact model id. There is no fuzzy matching or `provider/model` shorthand on the command line; pick interactively with `/model`.
- `--api-key` is not persisted and is ignored by any provider that resolves its key from the environment or the stored credential file.
- `--dump-config` prints rows as tab-separated text. A JSON form exists in the library (`dump_config_json`) but no flag exposes it.
- `vtx plugin` has no `list` or `remove` subcommand; installed plugins are removed with the package manager directly.
- The `commands:` line that `vtx plugin` prints advertises only `profiles | init | path | enable | disable` (`src/vtx/coding_agent/plugin_cmd.py:202`). `install`, `allow-version`, `revoke-version`, and `version-exemptions` are dispatched but are not listed; `vtx plugin -h` and `vtx plugin <unknown>` are the only ways to discover them.
- `--list-extensions` reports tools, commands, and handlers, but not shortcuts or flags registered by an extension.
- Extension and agent discovery silently skips unreadable directories; only the resulting per-entry errors are printed.
- The help text for `--provider` does not name the accepted slugs; only the `usage:` line lists them.
- `-p/--prompt` rewrites the saved config file to `permissions.mode: auto` and writes it back afterwards (`src/vtx/coding_agent/headless.py:154`, `src/vtx/coding_agent/headless.py:307`). It is restored, but two concurrent headless runs can restore whichever value they captured first. The comment at `src/vtx/coding_agent/headless.py:152` claims it does not mutate the config; it does, for the duration of the run.

## See also

- [Slash commands](slash-commands.md) for the in-session command router
- [Keybindings](keybindings.md) for the TUI key map
- [Configuration](configuration.md) for `~/.vtx/config.yml`
- [Tools](tools.md) for the built-in tool set
- [Plugins](plugins.md) for profiles, bundles, and rows
- [Headless mode](headless.md) for the non-interactive runner
