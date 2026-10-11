# Storage layout

This page lists every path Vtx writes to and reads from, and names the function that resolves each one.

Paths come from the running code, not from convention, so treat the `Resolved by` column as the source of truth. For what the files mean, read [configuration.md](configuration.md), [sessions.md](sessions.md), [skills.md](skills.md), [goals.md](goals.md), and [mcp.md](mcp.md).

## The three roots

There are two home roots and one project root.

| Root | Function | Default |
|---|---|---|
| Global config | `get_config_dir()` (`src/vtx/core/config.py:377-380`) | `~/.vtx` |
| Legacy agents | `get_agents_dir()` (`src/vtx/core/paths.py:47-67`) | `~/.agents` |
| Project | literal, per subsystem | `<cwd>/.vtx` |

`get_config_dir()` resolves in this order and stops at the first hit (`src/vtx/core/paths.py:7-44`):

| # | Condition | Result |
|---|---|---|
| 1 | `XDG_CONFIG_HOME` is set and absolute | `$XDG_CONFIG_HOME/vtx` (`src/vtx/core/paths.py:13-19`) |
| 2 | `HOME` is set and is neither `""` nor `/` | `$HOME/.vtx` (`src/vtx/core/paths.py:20-24`) |
| 3 | `Path.home()` resolves to something other than `/` | `~/.vtx` (`src/vtx/core/paths.py:27-30`) |
| 4 | The `pwd` database has an entry for the uid | `<pw_dir>/.vtx` (`src/vtx/core/paths.py:34-41`) |
| 5 | Last resort | `<cwd>/.vtx` (`src/vtx/core/paths.py:44`) |

The directory name is the constant `CONFIG_DIR_NAME = "vtx"` (`src/vtx/core/paths.py:4`), and the XDG branch exists only for an explicitly set variable: unset, `~/.vtx` wins for backwards compatibility (`src/vtx/core/paths.py:14-17`). The chain exists so an empty `HOME` never resolves to `/.vtx`, which is not writable.

`get_agents_dir()` is `~/.agents` with the same `HOME` guard, because agents historically lived there (`src/vtx/core/paths.py:49`). It is still a live read root for skills (`src/vtx/agent/context/skills.py:20`).

## Global tree

Everything under `~/.vtx`, unless an env var above moved it.

| Path | What | Resolved by |
|---|---|---|
| `config.yml` | The whole config schema, versioned and migrated on load | `src/vtx/core/config.py:389-400` |
| `config.yml.bak.<ts>` | Pre-migration backup, `YYYYMMDDHHMMSS` in the name | `src/vtx/core/config.py:835-840` |
| `profiles/<name>/bundle.txt` | The profile's ordered bundle list | `src/vtx/plugins/profile.py:33-34`, `:79-83` |
| `profiles/<name>/cordis.yml` | The profile's patch layer | `src/vtx/plugins/profile.py:34`, `:64-66` |
| `profiles/<name>/compatibility.json` | `allow-version` grants | `src/vtx/plugins/install.py:31`, `:47-48` |
| `sessions/<safe_cwd>/<ts>_<id>.jsonl` | One file per session; the directory is `chmod 0700` | `src/vtx/agent/session.py:237-241`, `:894-895` |
| `tasks/<safe_cwd>/` | Background-task store and sub-agent results | `src/vtx/agent/background.py:100`, `src/vtx/agent/tools/task.py:213` |
| `snapshots/<digest>/` | Pre-edit file snapshots for revert | `src/vtx/agent/snapshot.py:184` |
| `skills/` | Skills installed into the vtx home; marked `bundled` on load | `src/vtx/agent/context/skills.py:351`, `:414-419` |
| `agent/*.py` | Global agent files and packages | `src/vtx/agent/agents/discovery.py:17`, `:54` |
| `agent/extensions/*.py` | Global extensions | `src/vtx/plugins/extensions.py:9`, `:288` |
| `mcp.json` | Global MCP server config | `src/vtx/mcp/config.py:53`, `:306` |
| `trusted-projects.json` | Which projects may run their own `mcp.json` | `src/vtx/mcp/trust.py:33`, `:62` |
| `mcp-auth.json` | MCP OAuth credentials | `src/vtx/mcp/oauth/provider.py:45`, `:98` |
| `hooks.yml` | Global YAML hooks | `src/vtx/plugins/builtins/hooks.py:29`, `:42` |
| `providers/*.yaml` | User-wide custom provider catalog | `src/vtx/ai/provider_catalog.py:436-441` |
| `prompt-history.jsonl` | Prompt history | `src/vtx/tui/prompt_history.py:12` |
| `telemetry.jsonl` | JSONL trace export, only when the telemetry row enables it | `src/vtx/plugins/builtins/services/telemetry.py:24` |
| `bin/` | Where managed binaries are looked for alongside `PATH` | `src/vtx/agent/tools_manager.py:21`, `src/vtx/core/config.py:417-420` |
| `gh_app/config.json`, `gh_app/bot_user_id.json` | GitHub App config and bot-id cache | `src/vtx/git/gh_app.py:57-59` |
| `models/<slug>.json` | Per-provider model catalog cache | `src/vtx/ai/dynamic_models.py:53`, `:307-313` |
| `models_dev_limits.json` | Context-length limits, fetched from models.dev and written after a successful fetch; several MB | `src/vtx/ai/context_length.py:21`, `:52-53`, `:68-72`, `:82` |
| `codex_auth.json` (legacy `openai_auth.json`) | Codex OAuth tokens | `src/vtx/ai/oauth/codex.py:68-71` |
| `copilot_auth.json` | Copilot OAuth token | `src/vtx/ai/oauth/copilot.py:49` |
| `cline_auth.json` | Cline OAuth token | `src/vtx/ai/oauth/cline.py:69` |
| `dynamic_auth.json` | Dynamic-provider API keys | `src/vtx/ai/oauth/dynamic.py:32`, `:52-72` |

Two of these are read rather than written as whole directories: the usage heatmap scans everything under `sessions/` (`src/vtx/tui/usage_stats.py:58-59`), and HTML export reads the same per-project session directory (`src/vtx/tui/export.py:182-183`).

Global `AGENTS.md` or `CLAUDE.md` context files are read straight out of `~/.vtx` (`src/vtx/agent/context/agent_mds.py:87-89`), so `~/.vtx/AGENTS.md` is global project instructions.

## Project tree

Everything here is relative to the working directory and is *not* moved by the config dir.

| Path | What | Resolved by |
|---|---|---|
| `.vtx/goals/active_goal_<ts>_<id>.md` | Open goals, human-editable markdown with a JSON metadata block | `src/vtx/agent/goal/storage.py:45-48`, `:71-74` |
| `.vtx/goals/archived/<same-name>.md` | Completed or cleared goals | `src/vtx/agent/goal/storage.py:51-54`, `:211-223` |
| `.vtx/goals/ledger.jsonl` | Append-only activity ledger | `src/vtx/agent/goal/storage.py:57-58`, `:297-309` |
| `.vtx/goals/settings.json` | Per-project goal settings | `src/vtx/agent/goal/storage.py:61-62`, `:391-409` |
| `.vtx/goals/leases/<session_id>.json` | Which live sessions own which goals; a lease older than 120s is stale | `src/vtx/agent/goal/storage.py:40`, `:231-289` |
| `.vtx/extensions/*.py` | Project extensions | `src/vtx/plugins/extensions.py:8`, `:304` |
| `.vtx/agent/*.py` | Project agents, walked up to the git root | `src/vtx/agent/agents/discovery.py:15`, `:46-51` |
| `.vtx/mcp.json` | Project MCP config, honored only once the project is trusted | `src/vtx/mcp/config.py:54`, `:309-315` |
| `.vtx/hooks.yml` | Project YAML hooks, loaded before the global one | `src/vtx/plugins/builtins/hooks.py:42-43` |
| `.vtx/providers/*.yaml` | Project-local custom provider catalog | `src/vtx/ai/provider_catalog.py:443-445` |
| `.agents/skills/<name>/SKILL.md` | Project skills, one directory per skill | `src/vtx/agent/context/skills.py:309-318`, `:407-411` |

The project roots are always scanned; the `extensions:` and `agents.files` lists in `config.yml` add to them rather than replace them (`src/vtx/core/config.py:250-256`).

### Session directory encoding

A session directory name is the working directory with `/` and `\` replaced by `-` and leading or trailing dashes stripped (`src/vtx/agent/session.py:238`):

```python
safe_cwd = cwd.replace("/", "-").replace("\\", "-").strip("-")
sessions_dir = get_config_dir() / "sessions" / safe_cwd
```

So `/home/me/proj` becomes `home-me-proj`, and the session files inside are named `<file_timestamp>_<session_id>.jsonl` where the timestamp is local time with dashes for separators (`src/vtx/agent/session.py:892-895`).

## Discovery order

Skills are merged from three roots, first seen wins, and a later collision is reported as a warning rather than silently replacing the earlier skill (`src/vtx/agent/context/skills.py:392-405`):

1. `<cwd>/.agents/skills/` and every ancestor up to and including the git root (`src/vtx/agent/context/skills.py:309-318`).
2. `~/.agents/skills/`.
3. `~/.vtx/skills/`, whose entries are flagged `bundled`.

Agents follow the same shape (`src/vtx/agent/agents/discovery.py:40-100`): project files walked from the working directory up to the git root deepest-first, then `~/.vtx/agent/`, then the explicitly configured paths. `_add` keeps the first path it sees for a name and drops every later duplicate (`src/vtx/agent/agents/discovery.py:60-72`), so a project agent shadows a global or configured agent of the same name. `load_all_agents` then assigns by name in list order (`src/vtx/agent/agents/loader.py:181-190`).

## Env vars that move a path

| Variable | Effect |
|---|---|
| `XDG_CONFIG_HOME` | Moves the global root to `$XDG_CONFIG_HOME/vtx` (`src/vtx/core/paths.py:13-19`) |
| `HOME` | Sets the global root to `$HOME/.vtx` (`src/vtx/core/paths.py:20-24`) |
| `VTX_MODELS_CACHE_DIR` | Replaces `~/.vtx/models` for every model catalog cache (`src/vtx/ai/dynamic_models.py:307-313`, `src/vtx/ai/model_fetcher.py:53-58`, `src/vtx/ai/oauth/cline.py:429-431`) |
| `VTX_PROFILE` | Selects the profile, and therefore which `profiles/<name>/` is read (`src/vtx/plugins/bootstrap.py:35`) |

## Worked example

Print the resolved layout of the machine you are on, with file sizes for everything except the three directories that grow:

```bash
uv run python - <<'PY'
from pathlib import Path

from vtx.core.paths import get_agents_dir, get_config_dir
from vtx.agent.goal.storage import goals_dir, ledger_path
from vtx.agent.session import Session
from vtx.mcp.config import load_mcp_config

root = get_config_dir()
print("global root:", root)
print("legacy agents dir:", get_agents_dir())

project = Path.cwd() / ".vtx"
print("project root:", project)
print("goals dir:", goals_dir(str(Path.cwd())))
print("goal ledger:", ledger_path(str(Path.cwd())))

sessions = Session.get_sessions_dir(str(Path.cwd()))
print("sessions dir:", sessions)
print("session files:", sorted(p.name for p in sessions.glob("*.jsonl")))

mcp = load_mcp_config(cwd=str(Path.cwd()), project_trusted=True)
print("mcp servers:", [s.name for s in mcp.servers])

SKIP = {"sessions", "tasks", "snapshots", "models"}
print("\nfiles:")
for path in sorted(root.rglob("*")):
    if path.is_dir() or SKIP & set(path.relative_to(root).parts):
        continue
    print(f"  {path.relative_to(root)}  {path.stat().st_size}B")
print("\ndirectories:")
for path in sorted(root.iterdir()):
    if path.is_dir():
        print(f"  {path.name}/")
PY
```

The interesting lines are the first four: they name the exact directories this installation resolves to, and they are the paths every row of the tables above is relative to. `Session.get_sessions_dir` creates the directory with mode `0700` if it does not exist (`src/vtx/agent/session.py:237-241`), so running this script has a side effect you can undo with `rmdir`.

## Known gaps

- Nothing under `~/.vtx` is encrypted or user-locked beyond the session directory's `0700`. Tokens in `codex_auth.json`, `copilot_auth.json` and `mcp-auth.json` are plain files, and the GitHub bot-id cache is written with default permissions (`src/vtx/git/README.md:87`).
- The dynamic-auth key file does not follow one root. It prefers `~/vtx/dynamic_auth.yml` (a bare `vtx` in the home directory, outside the config dir), then `$XDG_CONFIG_HOME/vtx/dynamic_auth.json`, then `~/.vtx/dynamic_auth.json` (`src/vtx/ai/oauth/dynamic.py:52-72`). Only the third is what the rest of the layout would predict.
- `get_agents_dir()` ignores `XDG_CONFIG_HOME` entirely, so setting it moves the config root but leaves skills reading `~/.agents/skills` (`src/vtx/core/paths.py:47-67`).
- Session directories are keyed by the resolved-at-session-start working directory, so moving a project invalidates its history rather than following it (`src/vtx/agent/session.py:238`).
- `~/.vtx/goals` exists on installs that ran an older build; the current code writes goals to `<cwd>/.vtx/goals` and never reads the global copy (`src/vtx/agent/goal/storage.py:45-48`).
- Goal leases are advisory: a lease is a file whose mtime is compared against a 120-second TTL, so a machine whose clock jumps can release someone else's goal early (`src/vtx/agent/goal/storage.py:38-40`, `:275-289`).
- Agent discovery promises more than the code delivers. `_add`'s comment says "configured beats auto-discovered", but configured paths are added last and `_add` keeps the first occurrence, so a configured agent loses to a project or global agent with the same name (`src/vtx/agent/agents/discovery.py:66-72`, `:96-100`). The `find_agent_paths` docstring also claims the loader dedupes "last seen wins", which never comes into play because duplicates are already gone (`src/vtx/agent/agents/discovery.py:44-47`, `src/vtx/agent/agents/loader.py:184-190`).
- `load_skills` reports a name collision but keeps going, so a mistyped duplicate skill is easy to miss unless you read the warnings (`src/vtx/agent/context/skills.py:392-405`).
