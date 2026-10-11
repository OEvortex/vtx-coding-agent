# Development

This page covers the repository layout, the commands used to format, lint, type-check and test, and how to extend the harness by adding a tool or a plugin row.

Read [plugins.md](plugins.md) for the composition model in depth, [tools.md](tools.md) for the built-in tool surface, and [storage-layout.md](storage-layout.md) for where each piece writes on disk.

## Repository layout

The package is a src layout with one package per subsystem (`pyproject.toml:5-6`).

| Path | Owns |
|---|---|
| `src/vtx/core/` | Config schema and migration, path resolution, themes, notifications (`src/vtx/core/__init__.py:1`) |
| `src/vtx/agent/` | The product-neutral harness: turn loop, sessions, tools, extensions, SDK (`src/vtx/agent/__init__.py:1`) |
| `src/vtx/ai/` | Provider layer: base provider, catalog, dynamic model limits, OAuth |
| `src/vtx/codemode/` | Script execution in a confined subprocess (`src/vtx/codemode/__init__.py:1`) |
| `src/vtx/coding_agent/` | The vtx product: CLI, headless runner, agents, tool wiring, `vtx plugin` |
| `src/vtx/mcp/` | Model Context Protocol client, transports, trust, exposure (`src/vtx/mcp/__init__.py:1`) |
| `src/vtx/plugins/` | The plugin kernel: rows, profiles, host, extension loader (`src/vtx/plugins/__init__.py:1`) |
| `src/vtx/protocol/` | Wire vocabulary: message and event types shared by every layer |
| `src/vtx/telemetry/` | `Trace` / `Span` primitives and the processor chain (`src/vtx/telemetry/__init__.py:1`) |
| `src/vtx/tui/` | The Textual interface (`src/vtx/tui/__init__.py:1`) |
| `src/vtx/git/` | Git metadata, the `gh` CLI wrapper, GitHub App auth (`src/vtx/git/__init__.py:1`) |
| `tests/` | Mirrors the packages: `tests/plugins/`, `tests/mcp/`, `tests/sdk/`, `tests/extensions/`, `tests/context/`, `tests/llm/`, `tests/core/`, plus top-level `test_*.py` |

Each package carries a `README.md` describing its own internals; those are the place to look before changing a subsystem.

## Set up

Python 3.12 or newer (`pyproject.toml:26`) and [`uv`](https://docs.astral.sh/uv/). From a clone:

```bash
uv sync
uv run vtx --version
```

`uv sync` installs the project plus the `dev` dependency group (`pyproject.toml:100-109`), which is where `ruff`, `pytest` and friends come from. The console script maps to `vtx.coding_agent.cli:main` (`pyproject.toml:111-112`).

`scripts/install.sh` is the end-user installer, not a dev entry point: it installs `vtx-coding-agent` from PyPI or a git URL into whatever Python it finds (`scripts/install.sh:216-219`).

## The four commands

| Command | What it does |
|---|---|
| `uv run ruff format .` | Formats every file. Run it after editing or creating any file (`AGENTS.md:6`) |
| `uv run ruff check .` | Lints with the rule set in `pyproject.toml:118-129` |
| `uvx ty check .` | Type-checks `src/` (`ty.toml` excludes `tests/` and `.venv/`) |
| `uv run python -m pytest <paths>` | Runs the named test files |

The settings that matter:

- Line length 99, target Python 3.12 (`pyproject.toml:115-116`).
- Lint rules: `E`, `W`, `F`, `I`, `N`, `UP`, `B`, `SIM`, `RUF` (`pyproject.toml:119-129`).
- Format: double quotes, space indent, no magic trailing comma (`pyproject.toml:146-149`).
- Pytest runs with `--import-mode=importlib` and collects from `tests/` (`pyproject.toml:151-155`).

### Test policy

- Never run the full test suite unless you were explicitly asked to. It is slow and resource intensive (`AGENTS.md:12`).
- Run only the files that cover what changed; ask when you cannot tell which ones apply (`AGENTS.md:13-14`).
- Use `uv run python`, not `python`, for any script you generate (`AGENTS.md:7`).

## How a composition boots

`boot()` is the single entry point for mounting a composition (`src/vtx/plugins/bootstrap.py:22-51`):

```python
from vtx.plugins import boot, active_context, shutdown

host = boot()                      # profile default, or $VTX_PROFILE
ctx = active_context()             # host.base
...
shutdown()
```

What it does, in order:

1. Picks the profile name from the argument, then `VTX_PROFILE`, then `default` (`src/vtx/plugins/bootstrap.py:35`).
2. Composes the row list with `load_profile(name, patches)` (`src/vtx/plugins/bootstrap.py:36`, `src/vtx/plugins/profile.py:123-139`).
3. Runs extension discovery *before* mount so discovered rows mount in the same pass (`src/vtx/plugins/bootstrap.py:38-46`).
4. Mounts and stores the host as the active one (`src/vtx/plugins/bootstrap.py:47-51`).

### Bundles and profiles

A profile is an ordered list of bundles; a bundle is a list of rows.

| Bundle | Contents |
|---|---|
| `base` | Services (`tools`, `llm`, `sessions`, `permissions`, `prompts`, `compaction`, `skills`, `telemetry`, `sandbox`, `mcp`, `agents`, `agent-loop`, `session-projections`, `goals`, `extensions`, `hooks`) plus the tool rows (`src/vtx/plugins/builtins/__init__.py:20-58`) |
| `tui` | `ui` and `commands` rows (`src/vtx/plugins/builtins/__init__.py:62-65`) |
| `headless` | Adds nothing; a serverless run boots `base` alone (`src/vtx/plugins/builtins/__init__.py:69-70`) |
| `minimal` | `tools`, `llm`, `tool-read`, `tool-bash` — the smallest composition with a registry, a provider seam and a shell (`src/vtx/plugins/builtins/__init__.py:73-82`) |

| Profile | Bundles |
|---|---|
| `default` | `base`, `tui` |
| `headless` | `base` |
| `minimal` | `minimal` |

A profile with no shipped template starts from `("base", "tui")` (`src/vtx/plugins/builtins/__init__.py:100`). Templates live on disk per profile; `vtx plugin init NAME` creates one (`src/vtx/coding_agent/plugin_cmd.py:217-221`).

### Rows and patch layers

A row is `id`, `name`, `disabled`, `config`, `meta` (`src/vtx/plugins/loader.py:108-115`). Layers apply in order and the last write for an id wins (`src/vtx/plugins/profile.py:5-15`). The patch operations (`src/vtx/plugins/loader.py:241-251`):

| Op | Effect |
|---|---|
| `insert` | Append a row; if the id exists it is a whole replacement |
| `set` | Same as `insert`, spelled for readability at the top of a layer |
| `replace` | Requires the id to exist |
| `override` | Requires the id to exist; replaces only the named fields, merging `config` |
| `remove` | Drops the id; fails if it does not exist |

To see the composed row list for a profile:

```bash
vtx --dump-config
vtx plugin profiles
vtx plugin path default
```

`--dump-config` prints the profile layers and then the extension rows discovered at boot, which are not stored in the profile (`src/vtx/coding_agent/cli.py:221-239`).

## Add a tool

A tool is a `BaseTool` subclass plus one row that mounts it. Two files.

### 1. The tool

`BaseTool` declares `name`, `params`, `description`, `mutating`, `tool_icon`, `prompt_guidelines`, `needs_approval`, `ui_block` (`src/vtx/agent/tools/base.py:9-31`) and one abstract `execute` (`src/vtx/agent/tools/base.py:33-36`).

```python
# mypkg/tools.py
from __future__ import annotations

import asyncio

from pydantic import BaseModel

from vtx.agent.tools.base import BaseTool
from vtx.protocol.types import ToolResult


class WordCountParams(BaseModel):
    text: str


class WordCountTool(BaseTool[WordCountParams]):
    name = "wordcount"
    params = WordCountParams
    mutating = False
    description = "Count the whitespace-separated words in a string."

    async def execute(
        self, params: WordCountParams, cancel_event: asyncio.Event | None = None
    ) -> ToolResult:
        return ToolResult(success=True, result=str(len(params.text.split())))
```

`ToolResult` takes `success`, `result`, and the optional UI fields (`src/vtx/protocol/types.py:196-205`).

### 2. The row

```python
# mypkg/plugins.py
from __future__ import annotations

from vtx.plugins.builtins.tools._register import mount_tool
from vtx.plugins.plugin import plugin

from mypkg.tools import WordCountTool


@plugin(name="tool-wordcount", inject=("tools",))
def apply(ctx, config):
    mount_tool(ctx, WordCountTool(), is_default=False, parent_only=False)


PLUGIN = apply
```

`mount_tool` registers the tool and unwinds the registration with the row, so disabling and re-enabling leaves exactly one `wordcount` instead of two (`src/vtx/plugins/builtins/tools/_register.py:13-18`). `inject=("tools",)` is what delays the row until `ctx.tools` exists (`src/vtx/plugins/plugin.py:43-55`).

### 3. Mount it in your profile

Append an `insert` to `~/.vtx/profiles/default/cordis.yml`:

```yaml
- insert:
    - id: tool-wordcount
      name: mypkg.plugins:PLUGIN
```

That is the same instruction `vtx plugin install` prints after a successful install (`src/vtx/coding_agent/plugin_cmd.py:141-146`). Then toggle and verify:

```bash
vtx --dump-config | grep wordcount
vtx plugin disable tool-wordcount     # writes disabled: true into cordis.yml
vtx plugin enable tool-wordcount
```

`enable` and `disable` rewrite only the `disabled` flag of the matching row (`src/vtx/coding_agent/plugin_cmd.py:78-100`).

### 4. Test it

```python
# tests/test_wordcount.py
import asyncio

from mypkg.tools import WordCountTool, WordCountParams


def test_wordcount():
    result = asyncio.run(WordCountTool().execute(WordCountParams(text="one two  three")))
    assert result.success
    assert result.result == "3"
```

Run only that file:

```bash
uv run python -m pytest tests/test_wordcount.py
```

## Add a service row

A service row provides one `ctx.<key>`. The fifteen standard keys are the ones with a typed property on `Context`; any other key works too (`src/vtx/plugins/context.py:21-42`, `:74-87`). The rows in `src/vtx/plugins/builtins/services/` are the reference implementations — each claims exactly one key.

```python
# mypkg/services.py
from __future__ import annotations

from pydantic import BaseModel

from vtx.plugins.plugin import plugin


class SlugConfig(BaseModel):
    prefix: str = "run"


class SlugService:
    def __init__(self) -> None:
        self.calls = 0

    def slug(self, name: str) -> str:
        self.calls += 1
        return self.prefix + "-" + name


@plugin(name="slug", config=SlugConfig)
def apply(ctx, config: SlugConfig | None):
    ctx.provide("slug", SlugService())


PLUGIN = apply
```

Row config is validated against the pydantic model the plugin declares (`src/vtx/plugins/loader.py:295-300`); a plugin with no declared schema accepts any mapping. Override just the fields you care about:

```yaml
- override:
    - id: slug
      config:
        prefix: "job"
```

## Hot reload

`/reload` re-applies config, extensions, agents, tools, skills and MCP in place (`src/vtx/tui/commands/reload.py:54-195`). The order is the safety property (`src/vtx/tui/commands/reload.py:10-14`):

1. Refuse while a turn is running, rather than swapping the tool set under a live loop (`src/vtx/tui/commands/reload.py:62-68`).
2. Emit `session_shutdown` so extensions can release what they cached (`src/vtx/tui/commands/reload.py:79-85`).
3. Drop imported extension modules — everything prefixed `vtx_ext_`, which is what makes a new submodule visible (`src/vtx/tui/commands/reload.py:93`, `:288-295`).
4. Reload config first, because extension and agent discovery read it (`src/vtx/tui/commands/reload.py:97-98`).
5. Reload extensions with `fresh=True`, which also drops each package's `__pycache__` (`src/vtx/tui/commands/reload.py:105-107`).
6. Reload agents and re-register them on the new bus (`src/vtx/tui/commands/reload.py:116-122`).
7. Rebuild the tool set and rebind the runtime, so a tool edit in `config.yml` switches the live surface over (`src/vtx/tui/commands/reload.py:143-159`).
8. Call `refresh()` on any tool that has it; `codemode` snapshots the tool set to build its sandbox and needs to be told (`src/vtx/tui/commands/reload.py:164-167`).
9. Reconnect MCP in a worker, because a connection can take seconds and `mcp.json` is not part of `config.yml` (`src/vtx/tui/commands/reload.py:173-179`).
10. Push the settings that live outside the config object — theme, permission mode, thinking lines, tool badge, notifications, ponytail, git context (`src/vtx/tui/commands/reload.py:182`, `:231-251`).
11. Emit `session_start` so extensions re-initialize against the new module objects (`src/vtx/tui/commands/reload.py:187-193`).

The command then prints a before/after diff: added and removed tools, skill and agent counts, and which settings were applied (`src/vtx/tui/commands/reload.py:297-324`).

## Known gaps

- A plugin row's code is resolved with `importlib.import_module` and never invalidated (`src/vtx/plugins/loader.py:82-88`), so editing an installed plugin package needs a restart. Only extensions get the `sys.modules` treatment, and only for the `vtx_ext_` prefix (`src/vtx/tui/commands/reload.py:288-295`).
- `/reload` is refused outright while a turn is running; there is no queueing, so a long turn delays your reload (`src/vtx/tui/commands/reload.py:62-68`).
- The reload report only names tools, skills, agents, slash commands and seven settings. Everything else that config.yml controls is re-read silently, with no confirmation that it took effect (`src/vtx/tui/commands/reload.py:297-324`).
- There is no task runner, Makefile or CI entry point in the repo. The four commands above are the whole interface, and `scripts/` holds an end-user installer plus ad-hoc scripts that pytest does not collect (`pyproject.toml:151-155`).
- `ty.toml` excludes `tests/`, so type errors in test code are never reported by the documented check (`ty.toml:1-5`).
