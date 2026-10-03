# vtx.core

Shared foundations: agent lifecycle events, the permission gate, notifications, the scratchpad directory, compaction and recap prompts, handoff prompts, config paths, the user config schema with its defaults and migrations, the theme palette registry, harness knobs, and version/update helpers.

Seventeen modules (`__init__.py` plus 16), none of which implement an agent. Everything here is either a value type, a prompt builder, or a small utility that the agent engine, the TUI, and the CLI all need.

**`vtx.core` is not a pure foundation.** `core/config.py` reaches upward with function-local imports in exactly two places:

- `reload_config()` calls `vtx.agent.subagents.set_limit(cfg.task.max_concurrent)`, because a raised cap has to resize the live sub-agent queue, not just the next process.
- `set_model_provider_filter()` calls `vtx.ai.provider_catalog._load()`, to reject a typo before it persists.

Both are function-local, so no module-level cycle exists, but the dependency is real and worth knowing before you move code between these packages.

Not responsible for:

- **The agent loop, turns, or the session store.** `vtx.agent` owns those. `core/events.py` defines the event dataclasses the loop emits; it does not emit them.
- **Message and stream types.** Those are in `vtx.protocol`. `core/events.py` imports `ToolResultMessage`, `Usage`, `FileChanges`, and `StopReason` from there.
- **Providers and model catalogs.** `vtx.ai`.
- **Actual tool implementations.** `core/permissions.py` checks a `BaseTool` structurally; the tools themselves are in `vtx.agent.tools` / `vtx.coding_agent.tools`.
- **Rendering.** Nothing here draws. `ToolApprovalEvent`, `AskUserEvent`, and the `ui_summary` helpers return data; `vtx.tui` and `vtx.coding_agent.tui` render it.
- **Git and GitHub.** `vtx.git`, which depends on core (not the reverse).

## Dependencies

- Imports: `pydantic`, `yaml`, `aiohttp` (update check), `Pillow` (image resize), and `vtx.protocol` (6 files). Upward, function-locally, `vtx.agent.subagents` and `vtx.ai.provider_catalog` from `config.py` as described above.
- Imported by: `vtx.coding_agent` (25 files), `vtx.agent` (22), `vtx.tui` (9), `vtx.ai` (9), `vtx.mcp` (5), `vtx.git` (2), `vtx.codemode` (1).

## Public surface

### `vtx.core` (package root, 42 names in `__all__`)

The re-export is events + permissions + notify + scratchpad:

- **Events** (29 dataclasses, all with a `type: Literal[...]` discriminator): `SessionStartEvent`, `SessionEndEvent`, `AgentStartEvent`, `AgentEndEvent`, `TurnStartEvent`, `TurnEndEvent`, `ThinkingStartEvent`, `ThinkingDeltaEvent`, `ThinkingEndEvent`, `TextStartEvent`, `TextDeltaEvent`, `TextEndEvent`, `ToolStartEvent`, `ToolArgsDeltaEvent`, `ToolArgsTokenUpdateEvent`, `ToolEndEvent`, `ToolOutputDeltaEvent`, `ToolResultEvent`, `ToolApprovalEvent`, `AskUserEvent`, `CompactionStartEvent`, `CompactionProgressEvent`, `CompactionEndEvent`, `RetryEvent`, `ErrorEvent`, `WarningEvent`, `InterruptedEvent`, `BackgroundTaskCompletedEvent`, `HostNoticeEvent`.
- **Permissions**: `check_permission`, `PermissionDecision`, `ApprovalResponse`, `AskUserQuestion`, `AskUserOption`, `AskUserAnswer`, `AskUserResponse`, and the private-but-exported `_is_safe_bash_command`.
- **Notifications**: `NotificationEvent`, `notify`.
- **Scratchpad**: `init_scratchpad`, `get_scratchpad_dir`, `is_scratchpad_path`.

### `vtx.core.config`

| Name | Description |
|------|-------------|
| `config` | Module-level proxy (`_ConfigProxy`) exposing `config.ui`, `config.llm`, etc. The idiomatic entry point. |
| `get_config() -> Config` / `set_config(Config)` / `reload_config() -> Config` | Contextvar-cached config. `reload_config()` re-reads the file, re-syncs harness knobs, and resizes the sub-agent queue. |
| `Config` | Validated wrapper. Properties: `llm`, `ui`, `compaction`, `agent`, `permissions`, `notifications`, `recap`, `binaries`, `extensions`, `agents`, `task`. Statics: `deep_merge`, `merge_with_defaults`. |
| `ConfigSchema` + section models | Pydantic models: `MetaConfig`, `LLMConfig`, `UIConfig`, `CompactionConfig`, `AgentConfig`, `PermissionsConfig`, `NotificationsConfig`, `RecapConfig`, `LastSelectedConfig`, `RecentModelsConfig`/`RecentModelsEntry`, `AgentsConfig`, `TaskConfig`, `SystemPromptConfig`, `AuthConfig`, `TLSConfig`. |
| Literal types | `PermissionMode` (`prompt`/`auto`), `OnOverflowMode` (`continue`/`pause`), `AuthMode` (`auto`/`required`/`none`), `NotificationMode` (`on`/`off`), `ThinkingLinesOption` (`"1"`..`"5"`, `"none"`). Plus the tuples `PERMISSION_MODES`, `NOTIFICATION_MODES`, `THINKING_LINES_OPTIONS`. |
| `AVAILABLE_BINARIES: set[str]` | Detected once at import. `update_available_binaries()` re-detects. `Config.binaries` wraps it with `.rg`, `.fd`, `.gh` properties and `.has()`. |
| `CURRENT_CONFIG_VERSION` | `16` as of this writing. Fifteen ordered `_migrate_v*_to_v*` functions in the same module bring older files forward. |
| Setters (all return the reloaded `Config`) | `set_theme`, `set_show_welcome_shortcuts`, `set_permissions_mode`, `set_thinking_lines`, `set_git_context`, `set_ponytail`, `set_colored_tool_badge`, `set_notifications_enabled`, `set_model_provider_filter`. |
| Recency | `set_last_selected(...)`, `get_last_selected()`, `add_recent_model(provider, model_id)`, `get_recent_models()`. |
| Other | `reset_config()`, `consume_config_warnings()`, `get_config_dir()`, `get_agents_dir()`, `apply_harness_settings` (re-exported). |

Defaults come from `vtx/core/defaults/config.yml`, loaded with `importlib.resources`. Writes are atomic, and a migration backs up the old file before replacing it.

### `vtx.core.harness_config`

The product-neutral knobs the agent engine reads, so the engine never has to import a product package. `config.py` mirrors user YAML into it.

| Name | Description |
|------|-------------|
| `HarnessConfig` | Dataclass: `max_turns=500`, `default_context_window=200_000`, `compaction_threshold_percent=80.0`, `compaction_on_overflow="continue"`, `tool_call_idle_timeout_seconds=180.0`. |
| `get_harness_config()` / `set_harness_config(cfg)` | Read/replace the process-wide instance. |
| `apply_harness_settings(**kwargs)` | Merge non-`None` values into the live instance. |

### `vtx.core.events`

The 29 lifecycle dataclasses listed above. Notable shapes:

- `ToolResultEvent` carries a `ToolResultMessage` (and therefore its `Usage`, `StopReason`, `file_changes`, and `ui_summary`).
- `ToolEndEvent` carries `display`, the string from the tool's own `format_call()`.
- `ToolApprovalEvent` and `AskUserEvent` are the two *bidirectional* events: each carries an `asyncio.Future` (`ApprovalResponse` / `AskUserResponse`) that the UI sets. A single-question `ask_user` call still arrives as a one-entry `questions` list.
- `CompactionStartEvent.trigger` is `"overflow" | "manual" | "kernel"`.
- `ErrorEvent.error` is an already-formatted string (see `vtx.protocol.format_error`).

### `vtx.core.permissions`

| Name | Description |
|------|-------------|
| `check_permission(tool, arguments, config=None) -> PermissionDecision` | `ALLOW` when mode is `auto`, when `tool.mutating` is false, or when the bash command is on the safe list. Otherwise `PROMPT`. |
| `PermissionDecision` | `ALLOW` / `PROMPT`. |
| `ApprovalResponse` | `APPROVE` / `DENY`. |
| `AskUserQuestion`, `AskUserOption` | Frozen dataclasses for one question and one selectable option. |
| `AskUserAnswer` | Frozen. `kind` is `option`, `custom`, or `multi`; `scalar()` and `segment()` render it. |
| `AskUserResponse` | Frozen. Holds both the legacy `selections`/`custom_text` shape and the questionnaire `answers` tuple. `is_empty` means dismissed. `format_for_llm()` builds the envelope; `ui_summary()` builds a one-line header. |
| `_is_safe_bash_command(command)` | Rejects newlines, backticks, `$(`, `<(`, `>(`, and `;\|&()><`, then requires the first token to be in `SAFE_COMMANDS` or a `git` subcommand in `SAFE_GIT_SUBCOMMANDS`. |

The allowlist is deliberately small: `cat head tail ls pwd wc diff which file stat du df whoami id uname date realpath dirname basename`, plus read-only `git status/diff/log/show/rev-parse/describe/ls-files/ls-tree/blame/shortlog`.

### `vtx.core.notify`

`NotificationEvent` is a `Literal`, not a class: `"completion" | "permission" | "error"`. `notify(event)` plays `vtx/core/sounds/{completion,permission,error}.wav` through `afplay`, `paplay`/`aplay`, or PowerShell depending on platform, respecting `NotificationsConfig.volume`. Best-effort: never raises.

### `vtx.core.scratchpad`

`init_scratchpad(session_id)` creates `~/.vtx/scratchpads/vtx-scratchpad-<first 8 chars of session_id>` (idempotent, cached in-process, `None` on `OSError`). `get_scratchpad_dir(session_id)` reads the cache. `is_scratchpad_path(path)` resolves the path first, so traversal and symlinks cannot escape.

### `vtx.core.compaction`

| Name | Description |
|------|-------------|
| `generate_summary(messages, provider, system_prompt=None, on_delta=None, focus_instructions=None) -> str` | One LLM call over the full conversation. `on_delta` receives `SummaryProgress` snapshots. Legacy `<analysis>`/`<summary>` wrappers are stripped from the result. |
| `SummaryProgress` | `chars` + `sections_started: list[tuple[int, str]]`. |
| `summary_progress(text) -> list[tuple[int, str]]` | Which numbered sections a partial stream has started, skipping anything before a `<summary>` tag. |
| `is_overflow(usage, context_window, threshold_percent) -> bool` | False when `context_window <= 0`. Sums input + output + cache read + cache write. |
| `SUMMARY_SECTIONS` | The 12 `(number, title)` pairs the summary prompt demands, ending in "Do Not Redo". |
| `SUMMARIZATION_PROMPT` | The prompt text itself. |

### `vtx.core.recap`

| Name | Description |
|------|-------------|
| `build_recap_context(messages, initial_task=None, compaction_summary=None) -> RecapContext` | Trimmed message window plus optional `broader_context`. Oversized tool results are edge-truncated so one huge read cannot dominate. |
| `RecapContext` | Dataclass: `messages`, `broader_context`. |
| `generate_recap(context, provider) -> str \| None` | One cheap LLM call, whitespace-normalised, `None` when empty. |
| `has_meaningful_activity(messages) -> bool` | Needs 3+ tool invocations or 150+ assistant characters since the last user message, so "done" does not trigger a recap. |
| `message_text(message) -> str` | Flatten a message to text. |

### `vtx.core.handoff`

`HANDOFF_PROMPT_TEMPLATE` and `generate_handoff_prompt(messages, provider, system_prompt, query) -> str`. One LLM call that writes a ready-to-send opening prompt for a new thread, with a fixed Task/Context/Relevant files/Constraints/Next steps output shape.

### `vtx.core.paths`

`CONFIG_DIR_NAME = "vtx"`, `get_config_dir()` (`$XDG_CONFIG_HOME/vtx` only when explicitly set and absolute, else `$HOME/.vtx`, else `pwd` database, else `cwd/.vtx`; it never writes to `/.vtx`), `get_agents_dir()` (`~/.agents`), `shorten_path(path)` for display.

### `vtx.core.themes`

`THEME_ORDER`, `get_theme_ids()`, `get_theme_options() -> list[(id, label)]`, `get_theme(theme_id) -> ThemeConfig` (raises `ValueError` on an unknown id; returns a deep copy with the theme's syntax colours already attached). Models: `ThemeConfig`, `ColorsConfig`, `SyntaxColorConfig`, `BadgeColorConfig`. `Config.ui.colors` resolves through `get_theme(ui.theme)`.

### Small utilities

| Module | Names |
|--------|-------|
| `vtx.core.bytes_util` | `format_bytes(int) -> str` (binary units, raises on negative), `parse_bytes("4MB") -> int` (raises `ValueError` on unknown unit), `truncate_bytes(str, max_bytes) -> str` (UTF-8 safe). |
| `vtx.core.image` | `IMAGE_EXTENSIONS`, `MAX_BYTES` (4 MiB), `MAX_DIMENSION` (2000), `JPEG_QUALITY_STEPS`, `get_mime_type(path)`, `is_image_file(path)`, `resize_image(data, mime_type) -> (bytes, mime_type, warning)`, `read_and_process_image(path) -> (b64, mime_type, warning)`. |
| `vtx.core.version` | `PACKAGE_NAME` (read from `pyproject.toml`, falling back to `vtx-coding-agent`), `VERSION` (`"editable"` for a local editable install), `format_version()` -> `"v-editable"` or `"v1.2.3"`. |
| `vtx.core.update_check` | `is_newer_version(current, latest)` (numeric `MAJOR.MINOR.PATCH` only), `fetch_latest_pypi_version(package_name, timeout_seconds=4.0)`, `get_newer_pypi_version(package_name, current_version)`. Both async, both return `None` on any failure. |
| `vtx.core.self_update` | `self_update(package="vtx-coding-agent") -> (ok, message)`. Picks `uv tool` / `pipx` / `uv` / `pip` from how the package is installed; `VTX_UPDATE_USE_PIP` forces pip. |

## Usage

Reading and writing config:

```python
from vtx.core.config import config, get_config, set_permissions_mode

cfg = get_config()
cfg.ui.theme                    # "gruvbox-dark"
cfg.ui.colors                  # resolved ColorsConfig for that theme
cfg.permissions.mode            # "prompt" | "auto"
cfg.task.max_concurrent         # 4
cfg.binaries.rg                 # True if ripgrep was found at import

set_permissions_mode("auto")    # writes YAML atomically, reloads, returns Config
print(config.permissions.mode)  # "auto"
```

The permission gate. Pass a duck-typed config so the result does not depend on the
user's own `~/.vtx/config.yml` - anything with a `permissions.mode` works:

```python
from types import SimpleNamespace
from vtx.core.permissions import check_permission

cfg = SimpleNamespace(permissions=SimpleNamespace(mode="prompt"))

class Reader:
    name = "read"
    mutating = False

class Bash:
    name = "bash"
    mutating = True

check_permission(Reader(), {"path": "a.txt"}, cfg)          # PermissionDecision.ALLOW
check_permission(Bash(), {"command": "ls -la"}, cfg)         # PermissionDecision.ALLOW
check_permission(Bash(), {"command": "rm -rf /"}, cfg)      # PermissionDecision.PROMPT
check_permission(Bash(), {"command": "git status"}, cfg)     # PermissionDecision.ALLOW
```

With `mode="auto"` every tool returns `ALLOW` without inspection.

Emitting an event - the loop's job, the type's home:

```python
from vtx.core import TextDeltaEvent

event = TextDeltaEvent(delta="partial")   # field is `delta`, not `text`
assert event.type == "text_delta"
```

Scratchpad containment:

```python
from vtx.core import init_scratchpad, is_scratchpad_path

d = init_scratchpad("a1b2c3d4e5f6")   # ~/.vtx/scratchpads/vtx-scratchpad-a1b2c3d4
is_scratchpad_path(str(d / "notes.md"))  # True
```

Compaction overflow, without any LLM call:

```python
from vtx.core.compaction import is_overflow, SUMMARY_SECTIONS
from vtx.protocol import Usage

is_overflow(Usage(input_tokens=170_000), 200_000, 80.0)  # True
[SUMMARY_SECTIONS[0]]  # (1, 'Objective & Constraints')
```

The harness knobs a lower layer reads:

```python
from vtx.core.harness_config import get_harness_config

get_harness_config().max_turns  # 500
```