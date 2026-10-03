# vtx.core

The layer under everything: agent lifecycle events, the permission gate, the user config schema with its defaults and migrations, config and scratchpad paths, notification sounds, compaction and recap prompts, handoff prompts, the harness knob mirror, the theme registry, and version and update helpers. Seventeen modules, none of which implement an agent - each is a value type, a prompt builder, or a small utility that the engine, the TUI, and the CLI all need.

`vtx.core` re-exports events, permissions, notify, and scratchpad from its root (42 names in `__all__`). Everything else is imported from the submodule.

It is *nearly* a leaf. `config.py` reaches upward in exactly two places, both with function-local imports so no module-level cycle exists:

- `reload_config()` calls `vtx.agent.subagents.set_limit(cfg.task.max_concurrent)`, because a raised cap has to resize the live sub-agent queue, not just the next process.
- `set_model_provider_filter()` calls `vtx.ai.provider_catalog._load()`, to reject a typo before it persists.

Worth knowing before you move code between packages.

## Usage

The permission gate is the piece most call sites touch. It takes anything with a `permissions.mode`, so a test can pass a `SimpleNamespace` and not depend on the user's `~/.vtx/config.yml`.

```python
from types import SimpleNamespace

from vtx.core import check_permission

gate = SimpleNamespace(permissions=SimpleNamespace(mode="prompt"))


class Read:
    name = "read"
    mutating = False


class Bash:
    name = "bash"
    mutating = True


check_permission(Read(), {"path": "a.txt"}, gate)              # ALLOW
check_permission(Bash(), {"command": "ls -la"}, gate)           # ALLOW - safe allowlist
check_permission(Bash(), {"command": "git status"}, gate)       # ALLOW - safe git subcommand
check_permission(Bash(), {"command": "git push"}, gate)         # PROMPT
check_permission(Bash(), {"command": "rm -rf /"}, gate)         # PROMPT
```

With `mode="auto"` every tool returns `ALLOW` without the arguments being inspected at all. `check_permission(tool, arguments, config=None)` takes a `BaseTool` structurally - no import of the tool package - and `config=None` falls back to the process config.

Three results matter more than the allowlist itself:

- A non-mutating tool is `ALLOW` regardless of mode or arguments.
- `_is_safe_bash_command` rejects anything with a newline, a backtick, `$(`, `<(`, `>(`, or any of `;|&()><` before it looks at the first token. The shell metacharacters are the check; the allowlist is the second half.
- The allowlist is deliberately tiny: `basename cat date df diff dirname du file head id ls pwd realpath stat tail uname wc which whoami`, plus the read-only `git` subcommands `blame describe diff log ls-files ls-tree rev-parse shortlog show status`.

## Config

`vtx.core.config` owns the schema. Defaults come from `defaults/config.yml` loaded with `importlib.resources`; writes are atomic and a migration backs up the old file before replacing it.

```python
from vtx.core.config import config, get_config, set_permissions_mode

cfg = get_config()
cfg.ui.theme          # 'github-dark'
cfg.ui.colors         # resolved ColorsConfig for that theme
cfg.task.max_concurrent  # 4
cfg.binaries.gh       # True if gh was on PATH at import

set_permissions_mode("auto")   # writes YAML, reloads, returns the new Config
config.permissions.mode       # 'auto' - the proxy reads through
```

- `config` is a module-level proxy (`_ConfigProxy`) exposing `config.ui`, `config.llm`, and so on. It is the idiomatic entry point and always reflects the loaded `Config`.
- `get_config()` / `set_config(cfg)` / `reload_config()` manage a `ContextVar`-cached `Config`. `reload_config()` re-reads the file, re-syncs the harness knobs, and resizes the live sub-agent queue.
- `Config` is the validated wrapper. Its properties are `llm`, `ui`, `compaction`, `agent`, `permissions`, `notifications`, `recap`, `binaries`, `extensions`, `agents`, `task`; `deep_merge` and `merge_with_defaults` are static.
- `AVAILABLE_BINARIES: set[str]` is detected once at import. `update_available_binaries()` re-detects - needed if you installed a binary after startup. `Config.binaries` wraps the set with `.rg`, `.fd`, `.gh` properties and `.has()`.
- `CURRENT_CONFIG_VERSION` is `16`. Sixteen `_migrate_v*_to_v*` functions, from `v0_to_v1` through `v15_to_v16`, are chained by `_migrate_config_data()` to bring older files forward.
- Setters all return the reloaded `Config`: `set_theme`, `set_permissions_mode`, `set_thinking_lines`, `set_git_context`, `set_ponytail`, `set_colored_tool_badge`, `set_notifications_enabled`, `set_show_welcome_shortcuts`, `set_model_provider_filter`.
- Recency: `set_last_selected(...)`, `get_last_selected()`, `add_recent_model(provider, model_id)`, `get_recent_models()`.
- Also `reset_config()`, `consume_config_warnings()`, and re-exports of `get_config_dir()` / `get_agents_dir()` / `apply_harness_settings`.

The pydantic models behind all of that are `ConfigSchema` plus `MetaConfig`, `LLMConfig`, `UIConfig`, `CompactionConfig`, `AgentConfig`, `PermissionsConfig`, `NotificationsConfig`, `RecapConfig`, `AgentsConfig`, `TaskConfig`, `SystemPromptConfig`, `AuthConfig`, `TLSConfig`, `LastSelectedConfig`, `RecentModelsConfig`, and `RecentModelsEntry`. The closed-set literals are `PermissionMode` (`prompt`/`auto`), `OnOverflowMode` (`continue`/`pause`), `AuthMode` (`auto`/`required`/`none`), `NotificationMode` (`on`/`off`), and `ThinkingLinesOption` (`"1"`..`"5"`, `"none"`), `PERMISSION_MODES`, `NOTIFICATION_MODES`, and `THINKING_LINES_OPTIONS` give the valid values as tuples.

## Events

`vtx.core.events` holds 29 lifecycle dataclasses, every one with a `type: Literal[...]` discriminator: session (`SessionStartEvent`, `SessionEndEvent`), agent and turn boundaries (`AgentStartEvent`, `AgentEndEvent`, `TurnStartEvent`, `TurnEndEvent`), thinking and text streams (`ThinkingStartEvent`, `ThinkingDeltaEvent`, `ThinkingEndEvent`, `TextStartEvent`, `TextDeltaEvent`, `TextEndEvent`), tool lifecycle (`ToolStartEvent`, `ToolArgsDeltaEvent`, `ToolArgsTokenUpdateEvent`, `ToolOutputDeltaEvent`, `ToolEndEvent`, `ToolResultEvent`), the two interactive ones (`ToolApprovalEvent`, `AskUserEvent`), compaction (`CompactionStartEvent`, `CompactionProgressEvent`, `CompactionEndEvent`), and the catch-alls (`RetryEvent`, `ErrorEvent`, `WarningEvent`, `InterruptedEvent`, `BackgroundTaskCompletedEvent`, `HostNoticeEvent`).

```python
from vtx.core import TextDeltaEvent

event = TextDeltaEvent(delta="partial")
event.type   # 'text_delta' - the field is `delta`, not `text`
```

The shapes worth knowing:

- `ToolResultEvent` carries a `ToolResultMessage`, and therefore its `Usage`, `StopReason`, `file_changes`, and `ui_summary`.
- `ToolEndEvent` carries `display`, the string from the tool's own `format_call()`.
- `ToolApprovalEvent` and `AskUserEvent` are the only bidirectional events: each carries an `asyncio.Future` that the UI sets with `ApprovalResponse` or `AskUserResponse`. A single-question `ask_user` still arrives as a one-entry `questions` list.
- `CompactionStartEvent.trigger` is `"overflow"`, `"manual"`, or `"kernel"`.
- `ErrorEvent.error` is already a formatted string, from `vtx.protocol.format_error`.

This module defines the events; `vtx.agent` is what emits them.

## Compaction, recap, handoff

All three are one LLM call plus prompt text, and all three take a `BaseProvider` structurally.

```python
from vtx.core.compaction import SUMMARY_SECTIONS, is_overflow
from vtx.protocol import Usage

is_overflow(Usage(input_tokens=170_000), 200_000, 80.0)  # True
is_overflow(Usage(), 0, 80.0)                           # False - unknown window, never overflows
SUMMARY_SECTIONS[0]                                     # (1, 'Objective & Constraints')
len(SUMMARY_SECTIONS)                                   # 12
```

`is_overflow` sums input + output + cache read + cache write, and returns `False` for any `context_window <= 0` so an unknown window cannot trigger a compaction loop.

- `generate_summary(messages, provider, system_prompt=None, on_delta=None, focus_instructions=None)` makes one call over the whole conversation. `on_delta` receives `SummaryProgress` (`chars` plus `sections_started`), and legacy `<analysis>` / `<summary>` wrappers are stripped from the result. `SUMMARIZATION_PROMPT` is the prompt text and `SUMMARY_SECTIONS` the 12 `(number, title)` pairs it demands, ending in "Do Not Redo".
- `summary_progress(text)` reports which numbered sections a partial stream has started, skipping anything before a `<summary>` tag.
- `build_recap_context(messages, initial_task=None, compaction_summary=None)` returns a `RecapContext(messages, broader_context)` with oversized tool results edge-truncated, so one huge read cannot dominate the window. `generate_recap(context, provider)` is one cheap call, whitespace-normalised, `None` when empty. `has_meaningful_activity(messages)` wants 3+ tool invocations or 150+ assistant characters since the last user message, so a bare "done" does not trigger a recap.
- `HANDOFF_PROMPT_TEMPLATE` and `generate_handoff_prompt(messages, provider, system_prompt, query)` write a ready-to-send opening prompt for a new thread, in a fixed Task / Context / Relevant files / Constraints / Next steps shape.
- `message_text(message)` flattens any of the three message types to text.

## Paths, scratchpad, notifications

`vtx.core.paths` gives `CONFIG_DIR_NAME = "vtx"` and three resolvers. `get_config_dir()` uses `$XDG_CONFIG_HOME/vtx` only when the variable is explicitly set *and* the result is absolute, otherwise `$HOME/.vtx`, otherwise a `pwd` database, otherwise `cwd/.vtx` - it never writes to `/.vtx`. `get_agents_dir()` is `~/.agents`, and `shorten_path(path)` trims a path for display.

```python
from vtx.core import init_scratchpad, is_scratchpad_path

d = init_scratchpad("a1b2c3d4e5f6")   # ~/.vtx/scratchpads/vtx-scratchpad-a1b2c3d4
is_scratchpad_path(str(d / "notes.md"))  # True
is_scratchpad_path("/etc/passwd")        # False
```

`init_scratchpad` is idempotent per `session_id` and cached in-process; it returns `None` on `OSError` rather than raising. `is_scratchpad_path` resolves the path before comparing, so `..` traversal and symlinks cannot escape the directory. The directory name uses only the first 8 characters of the session id.

`notify(event)` plays `vtx/core/sounds/{completion,permission,error}.wav` through `afplay`, `paplay`/`aplay`, or PowerShell depending on platform, respecting `NotificationsConfig.volume`. `NotificationEvent` is a `Literal`, not a class: `"completion" | "permission" | "error"`. Best-effort - it never raises.

## Harness knobs

`vtx.core.harness_config` is how product-neutral numbers reach the engine without the engine importing a product package. `config.py` mirrors the user's YAML into it.

```python
from vtx.core.harness_config import get_harness_config

cfg = get_harness_config()
cfg.max_turns                            # 500
cfg.default_context_window               # 200_000
cfg.compaction_threshold_percent         # 80.0
cfg.compaction_on_overflow               # 'continue'
cfg.tool_call_idle_timeout_seconds       # 180.0
```

`HarnessConfig` is a plain dataclass with those five defaults. `get_harness_config()` / `set_harness_config(cfg)` read and replace the process-wide instance, and `apply_harness_settings(**kwargs)` merges only the non-`None` values into the live one.

## Themes and small utilities

`vtx.core.themes` owns the palette registry: `THEME_ORDER`, `get_theme_ids()`, `get_theme_options()` as `(id, label)` pairs, and `get_theme(theme_id)` which raises `ValueError` on an unknown id and returns a deep copy with the syntax colours already attached. The models are `ThemeConfig`, `ColorsConfig`, `SyntaxColorConfig`, and `BadgeColorConfig`. `Config.ui.colors` resolves through `get_theme(ui.theme)`.

```python
from vtx.core.bytes_util import format_bytes, parse_bytes, truncate_bytes

format_bytes(1536)          # '1.5 KB'
parse_bytes("4MB")          # 4194304
truncate_bytes("héllo", 3)  # 'h\ufffd' - a byte budget can cut a codepoint
```

`truncate_bytes` is byte-budgeted, not character-budgeted, so a multi-byte character can be replaced by U+FFFD rather than kept. `parse_bytes` raises `ValueError` on an unknown unit and `format_bytes` on a negative size. `vtx.core.image` handles provider image limits: `IMAGE_EXTENSIONS` (a dict of extension to MIME type), `MAX_BYTES` (4 MiB), `MAX_DIMENSION` (2000), `JPEG_QUALITY_STEPS` (`[85, 70, 55, 40]`, walked down until the result fits), `get_mime_type(path)`, `is_image_file(path)`, and `resize_image(data, mime_type)` / `read_and_process_image(path)`, both returning `(bytes, mime_type, warning)`. An already-small image still comes back with a note like `[640x480]`; and if Pillow is not installed both return the input unchanged.

`vtx.core.version` reads `PACKAGE_NAME` from `pyproject.toml` (falling back to `vtx-coding-agent`) and `VERSION`, which is `"editable"` for a local install; `format_version()` returns `"v-editable"` or `"v1.2.3"`. `vtx.core.update_check` has async `fetch_latest_pypi_version(package_name, timeout_seconds=4.0)`, `get_newer_pypi_version(package_name, current_version)`, and `is_newer_version(current, latest)` - all `None`/`False` on any failure, and the comparison is numeric `MAJOR.MINOR.PATCH` only. `vtx.core.self_update.self_update(package=...)` returns `(ok, message)` and picks `uv tool` / `pipx` / `uv` / `pip` from how the package was installed; `VTX_UPDATE_USE_PIP` forces pip.

## Not here

The agent loop, turns, and the session store are `vtx.agent`. Message and stream types are `vtx.protocol`. Providers and model catalogs are `vtx.ai`. Actual tool implementations are `vtx.agent.tools` and `vtx.coding_agent.tools` - `permissions.py` only checks a `BaseTool` structurally. Nothing here draws: `ToolApprovalEvent`, `AskUserEvent`, and the `ui_summary` helpers return data and `vtx.tui` renders it. Git and GitHub are `vtx.git`, which depends on core, not the reverse.