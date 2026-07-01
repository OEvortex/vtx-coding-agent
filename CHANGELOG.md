# Changelog

All notable changes to Vtx are documented in this file. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).


## [0.2.0] - 2026-06-30 — Hook System, Gitlawb Opengateway Provider, System Prompt & Tool Configuration, Recent Model Tracking

### Added

#### WebUI "default" access mode now reads vtx config permission mode
- When the WebUI site access mode is set to "default", it now reads `config.permissions.mode` from the vtx config to determine the workspace scope.
- If vtx config has `permissions.mode = "prompt"`, the workspace scope uses "restricted" (prompt mode).
- If vtx config has `permissions.mode = "auto"`, the workspace scope uses "full" (auto mode).
- Previously, the "default" mode always mapped to "full" (auto) because it used `restrict_to_workspace` which defaults to `False`, ignoring the user's vtx permission mode setting.
- Refactored `workspaces_payload()` to use `default_scope_for_webui()` for consistency.

#### Hook system — YAML-driven lifecycle hooks
- New `vtx.hooks` package with `HookRegistry`, `HookRuntime`, `HookConfigManager`,
  and `HookBridge` for reacting to session, tool, and lifecycle events.
- YAML configuration via `.vtx/hooks.yml` (project-local) or
  `~/.vtx/hooks.yml` (global). Category keys organize entries; the `event`
  field inside each entry determines when the hook fires.
- `HookBridge` connects YAML hook configs to the extension `EventBus` so they
  fire alongside extension handlers. Maps `session_start` → `SessionStart`,
  `tool_call` → `PreToolUse`, `tool_result` → `PostToolUse`,
  `compaction_start` → `PreCompact`, and more.
- **Blocking hooks**: `PreToolUse` and `PostToolUse` hooks can deny tool
  execution or suppress tool results by exiting non-zero (stderr becomes the
  denial reason shown to the agent).
- **Matcher patterns**: `bash*` (prefix), `*edit` (suffix), `read` (exact),
  or omit for all tools.
- **Once-only hooks**: `once: true` fires the hook on the first matching
  event only, then removes it.
- **Shell command execution**: `type: command` hooks run as async subprocesses
  with configurable timeouts (default 30s).
- Wired into both the TUI (`ui/app.py`) and headless mode (`headless.py`)
  automatically at startup.
- Added `TurnStart` and `TurnEnd` to the hook event catalog.
- Full reference: [docs/hooks.md](docs/hooks.md).

#### Gitlawb Opengateway provider support
- Registered Gitlawb Opengateway as a new OpenAI-compatible provider with base URL `https://opengateway.gitlawb.com/v1`.
- Added mapping to resolve `OPENGATEWAY_API_KEY` from environment variables.
- Configured auto-fetching of model lists from the gateway's `/models` endpoint.
- Known model fallbacks include MiMo V2.5/V2.5 Pro/V2 Flash, Gemini 3.1 Flash Lite, MiniMax M3, Qwen 3.7 Max, GLM 5.2, and Nemotron 3 Ultra Free.

#### Conduit OpenAI-compatible provider support
- Registered Conduit as a new OpenAI-compatible provider with base URL `https://conduit.ozdoev.net/api/v1`.
- Added mapping to resolve `CONDUIT_API_KEY` from environment variables.
- Configured auto-fetching of model lists from the `/models` endpoint with a 60-minute cache TTL.
- Marked as `openmodelendpoint: true` — model discovery works without an API key.

#### Dedicated system prompt
- Prompts package with custom system prompt for gateway usage.
- `identity.py` defines platform-specific sections: identity, messaging context, tool usage, memory guidance, skills guidance, output format, safety, error recovery, and task completion.
- `builder.py` assembles the system prompt following OSINT-AGENT's pattern (base → tooling → project context → skills → env).
- Agent runtime supports custom system prompt builder strategies.

#### Restricted tool set
- Tool configuration module with an approved tool list for the gateway.
- Approved tools: `read`, `write`, `edit`, `bash`, `fetch_webpage`, `web_search`, `skill`, and `mcp`.
- MCP proxy tool added with registry for server discovery and tool calling.

#### MCP integration
- MCP subsystem initialized in agent runtime with lazy connection by default.
- MCP proxy tool added to tool list when servers are configured.
- MCP registry shutdown on agent close for clean teardown.
- Configuration via `~/.vtx/mcp.yml` or `./.mcp.json`.

#### Recent model tracking in `/model` picker
- `/model` now surfaces your top 5 most recently used models at the top of
  the picker, regardless of any active `/provider` filter, making model
  switching seamless across different providers.
- Each recent model is marked with a `↻` prefix and sorted by recency (most
  recent first) so you can instantly re-select a previously used model.
- The list of recent models (up to 10 entries) is persisted across sessions
  in `~/.vtx/config.yml` under `recent_models.entries` and updated on every
  model switch.

#### Document and text file upload support in WebUI
- Added support for attaching document formats (PDF, DOCX, XLSX, PPTX) and plain text formats (.txt, .md, .csv, .json, .yaml, .yml, .toml, .ini, .cfg, .log) via the WebUI composer.
- Documents are read as data URLs on the client side using `FileReader` (bypassing the image encoder worker) and sent to the WebSocket server with a 50MB size limit.
 - Whitelisted document types and mapped file extensions dynamically in `media_decode.py` and `websocket.py` to ensure correct ingestion and parsing.

#### WebUI paste and drop support for documents
- Extended `extractImageFilesFromPaste` and `extractImageFilesFromDrop` to accept
  document files (PDF, Office, text formats) in addition to images, matching the
  server-side MIME/extension whitelist.
- Files are validated against `ACCEPTED_MIMES` and `SUPPORTED_DOCUMENT_EXTENSIONS`
  before being forwarded to the composer.

#### WebUI exported attachment constants
- Exported `IMAGE_MIMES`, `VIDEO_MIMES`, `DOCUMENT_MIMES`,
  `SUPPORTED_DOCUMENT_EXTENSIONS`, and `ACCEPTED_MIMES` from
  `useAttachedImages.ts` so WebUI components can share a single source of
  truth for attachment validation.

#### WebUI document upload accept attribute
- Updated the `<input accept>` attribute in `ThreadComposer` to include video
  and document MIME types plus file extensions, ensuring the OS file picker
  allows all supported attachment formats.

#### WebUI fixed extension extraction for edge-case filenames
- Guarded `lastIndexOf(".")` calls in `useAttachedImages.ts` and
  `useClipboardAndDrop.ts` so files without an extension are handled safely
  instead of producing an empty string slice from index `-1`.

#### Fixed Vite build output path
- Corrected `vite.config.ts` `outDir` to point to `../../vtx_claw/web/dist`
  instead of `../vtx-claw/web/dist`, fixing production WebUI builds after
  directory restructuring.

#### WebUI context token and window tracking
- The WebUI client now records `context_tokens` and `context_window` per chat
  and exposes them via `getContextTokens()` and `getContextWindow()`.
- `handle_turn_end` extracts the values from the agent runtime after each turn,
  falling back to last LLM usage when consolidator estimates are unavailable.
- Removed the manual `context_window_tokens` setting from WebUI settings and
  model-configuration updates; the value is now derived from the active model's
  catalog/runtime data automatically.

#### Agent loop runtime event fix
- `record_turn_runtime` now receives the full `AgentLoop` instance instead of
  only its `LLMRuntime`, restoring accurate runtime introspection for
  downstream WebUI turn metadata.

#### WebUI i18n updates
- Restructured English locale strings in `common.json` with expanded coverage
  for settings status, actions, BYOK, model overview, token usage, providers,
  apps, automations, OAuth, skills, voice, thread composer, chat actions,
  message states, lightbox, file preview, and error copy.

#### WebUI ThreadComposer and ThreadShell updates
- Updated composer and shell components to match the new i18n keys and
  context-token tracking behavior.

#### Brand asset updates
- Replaced bundled application icons and favicons.

#### WebUI multilingual locale updates
- Restructured locale strings in `es`, `fr`, `id`, `ja`, `ko`, `vi`, `zh-CN`, and `zh-TW`
  `common.json` files to match the new English i18n key layout and expanded coverage.

#### WebUI ThreadComposer context display
- The context circle now renders when `contextWindow` is known, using `contextTokens ?? 0`
  as a fallback instead of requiring both values to be non-null.

#### WebSocket context state hydration
- After subscribe, replay saved `context_tokens`/`context_window` from session metadata
  so refreshed WebUI clients restore context usage immediately.
- Push the active loop's default context window when the client does not yet have it.

#### Workspace access mode source channel awareness
- `default_access_mode` now accepts `source_channel`; for websocket connections it reads
  the WebUI default access mode from config, preserving the selected permission mode
  across reconnects.

### Changed
- Agent runtime supports custom system prompt builder strategies.
- System prompt is now messaging-platform-aware with concise formatting guidance.
- Tool surface restricted to approved subset (removed `find`, `grep`, `ask_user`, `task`, `background` from default tools).

### Fixed

#### Compaction context preservation
- Rewrote the post-compaction `continue` message to reinforce active task execution instead of offering an exit ramp. The old message ("stop and ask for clarification if unsure, summarise and be done") actively encouraged the model to quit.
- Changed the compacted view framing from a fake Q&A ("What did we do so far?") to a system status restoration message. The model no longer sees a question it never asked, which caused it to shift from active-task mode to retrospective mode.
- `GoalEntry` state is now injected into the compaction summary, so the model retains knowledge of the active `/goal` objective after context reset. Previously goal state was silently dropped.
- Fixed `token_totals().context_tokens` to use `max()` across all assistant messages instead of overwriting with the last message's count. The "tokens after" value now correctly reflects peak context size.
- Tightened the summarization prompt to enforce structured output (Goal / Instructions / Discoveries / Accomplished / Files) with requirements for exact identifiers, verbatim instruction preservation, and explicit "what was happening RIGHT BEFORE compaction" tracking.

#### Provider authentication error handling
- Improved error messages when provider API keys are invalid or rejected.
- The `/models` fetch now surfaces the provider's actual error detail (e.g. `invalid api key (2049)`) instead of a generic `Authentication required` message, so users can distinguish between a missing key and a rejected key.
- Added robust parsing for varied auth error payload shapes: handles both `{"error": {"message": "..."}}` and `{"error": "Unauthorized: ..."}` formats from providers like MiniMax and Cline.

#### TypeError in tool parameters decorator
- Fixed a TypeError where `tool_parameters` decorator attempted to dynamically mutate `cls.__dict__` directly, which is a read-only `mappingproxy` object. Replaced direct assignment with `setattr()`.

## [0.1.9] - 2026-06-29 — Fix Context Length Overflow & Remove Hardcoded Token Defaults

### Fixed

#### Context length overflow for models with inflated max_tokens
- When models.dev reports `max_tokens` close to the context window (e.g., 247808 for a 256k model), the API call now caps output to the tiered safe limit instead of blindly using the inflated value.
- Tiered `safe_max_output_tokens()` by context window: ≤128k → 8k, ≤256k → 16k, >256k → 32k.
- Applied in `context_length.py`, `_parse_models()`, and `_to_static_model()` — covers all model resolution paths.

#### Removed hardcoded token defaults across the codebase
- Removed `DEFAULT_CONTEXT_LENGTH` (131072), `DEFAULT_OUTPUT_TOKENS` (16384), `DEFAULT_CONTEXT_WINDOW` (128000), and `DEFAULT_MAX_TOKENS` (16384) magic constants.
- `ProviderConfig.max_tokens` changed from `int = 8192` to `int | None = None` — no more phantom limits when the model catalog provides accurate values.
- `Model.max_tokens` changed from `int` to `int | None` throughout the model resolution chain.
- `ProviderInfo.max_tokens` default changed from `8192` to `None`.
- SDK agent fallbacks changed from hardcoded `8192` to `None`, letting the catalog drive limits.
- Anthropic SDK removed `_MAX_TOKENS_DEFAULT = 4096` fallback; now sends `config.max_tokens` directly.
- Removed hardcoded `max_tokens` from provider.yaml entries for OpenAI, Anthropic, and Kilo.

#### default_context_window set to 0
- Changed `default_context_window` from `200000` to `0` to prevent an arbitrary hardcoded window that doesn't match any real model. Context window is now always resolved from the model catalog or models.dev.

#### Improved fuzzy model matching
- `context_length_manager.get_limits()` now normalizes hyphens when doing substring matching, so `step-3.7-flash` matches `stepfun/step-3.7-flash` reliably.

#### Replace print() with logging in hot paths
- `extensions.py`: `ExtensionRuntime.notify()` now uses `log.info()`/`log.warning()`/`log.error()` instead of `print(..., file=sys.stderr)`.
- `agents/api.py`: `AgentAPI.notify()` migrated to module logger with the same level mapping.
- `tools_manager.py`: `_ensure_tool()` download progress messages now go through `log.info()`/`log.error()`.
- `config.py`: `_record_config_warning()` now uses `log.warning()`; the message is still retained in `_config_warnings` for `consume_config_warnings()`.

#### Guard against zero context window in compaction
- `is_overflow()` in `compaction.py` returns `False` when `context_window <= 0`, preventing nonsensical overflow checks when the window is unresolved.

#### Show token count after compaction in TUI
- Updated the compaction TUI indicator to show the post-compaction token count (`[compaction] Compacted from X tokens >> Y tokens`) rather than a static string. Added `tokens_after` field to `CompactionEndEvent` and `CompactionEntry` tracking.

#### Grep tool crash when using ripgrep or system grep fallback
- Fixed `GrepTool.execute()` using sync `subprocess.Popen` with `communicate_or_cancel`, which expects an async `asyncio.subprocess.Process`. This caused a `TypeError: a coroutine was expected` on every rg/grep invocation, silently dropping the error and falling through to the Python fallback (or crashing entirely).
- Replaced `subprocess.Popen` with `asyncio.create_subprocess_exec` in both the ripgrep and system grep paths, matching the pattern used by `FindTool`.

#### Agent tool list leaking across profile switches
- Fixed `_apply_active_agent_to_runtime()` using `self.tools` (already filtered by the previous agent's `tools_allow`) as the baseline for recomputing the new agent's tools. This caused tools from a restrictive profile (e.g. plan's `grep`, `find`) to leak into subsequent agents that shouldn't have them.
- Changed the baseline to `DEFAULT_TOOLS`, so every agent switch starts from the full default tool set and applies its own `tools_allow`/`tools_deny` cleanly.

### Added

#### Kimchi OpenAI compatible provider support
- Registered Kimchi as a new OpenAI compatible provider with base URL `https://llm.kimchi.dev/openai/v1`.
- Added initial known model `openai/gpt-4o` and auto-fetch configuration for the provider's `/models` endpoint.
- Added mapping to resolve `KIMCHI_API_KEY` from environment variables.

#### Augmented handoff agent profiles with SDK-bridge fields
- Added `tools` field on `AgentDef`: raw SDK tools (`@tool` callables, `BaseTool` instances, or SDK `Agent` instances) are now auto-wrapped and merged into the profile's local tool set at load time. No `register(api)` required for simple tool contributions.
- Added `tool_groups` and `active_tool_group` fields on `AgentDef`: profiles can declare named tool-surface presets (e.g. `"read-only"` vs `"full"`) and cycle through them at runtime.
- Added `skills` field on `AgentDef`: per-profile skill auto-loading, filtering matching skill descriptions into the system prompt when the agent is active.
- Added `Alt+Ctrl+G` binding to cycle tool groups within the active profile. Shift+Tab still cycles full profiles.
- Added `tool_group_changed` event to the extension bus, carrying `agent` and `group` payloads.
- Added `_coerce_raw_tools()` in `agents/loader.py` for callable/BaseTool/Agent coercion logic.
- Added `cycle_tool_group()` to `AgentRegistry` and `cycle_active_tool_group()` to `ConversationRuntime`.
- Updated `_apply_active_agent_to_runtime()` to respect `tool_groups` (group allow list overrides `tools_allow`) and `_rebuild_system_prompt()` to filter skills per profile.
- Added example `examples/agents/data-engineer.py` demonstrating the new fields.

## [0.1.8] - 2026-06-26 — Provider API Key Resolution Fix

### Fixed

#### Provider-specific API key resolution
- Fixed `OpenAISDKProvider` always checking `OPENAI_API_KEY` instead of the correct env var for each provider (e.g. `ZYLOO_API_KEY` for Zyloo).
- Stored API keys from `/login` now work for all dynamic providers with a `base_url`, not just a hardcoded subset (airouter, opencode, kilo, tokenrouter).

## [0.1.7] - 2026-06-26 — Zyloo OpenAI Compatible Provider

Add Zyloo OpenAI compatible provider:

### Added

#### Zyloo OpenAI compatible provider support
- Registered Zyloo as a new OpenAI compatible provider with base URL `https://api.zyloo.io/v1`.
- Added mapping to resolve `ZYLOO_API_KEY` from environment variables.
- Configured auto-fetching of model lists from the provider's `/models` endpoint.

## [0.1.6] - 2026-06-25 — Goal Command for Autonomous Multi-Turn Objectives

Add comprehensive goal functionality for autonomous multi-turn objectives:

### Added

#### `/goal` slash command — autonomous multi-turn objectives
- New `/goal <condition>` slash command sets a completion condition; the agent keeps
  working across turns until a separate evaluator (the small fast model by default,
  falls back to the active model) judges the condition met against the transcript.
- Subcommands: `/goal` (status), `/goal pause`, `/goal resume`, `/goal clear`
  (aliases: `stop`, `off`, `reset`, `none`, `cancel`).
- Lifecycle states mirror Claude Code / Codex: `pursuing`, `paused`, `achieved`,
  `unmet`, `budget_limited`. The InfoBar shows `◎ /goal active · 5m` while running;
  the chat renders a one-line badge (`✓ Goal achieved`, `⏱ Budget exhausted`,
  `↻ Continuing`) on every state change.
- New `goal:` config block (`goal.enabled`, `goal.max_turns=100`, `goal.max_objective_chars=4000`,
  `goal.evaluator_provider`, `goal.evaluator_model`).
- Inline budget clause: append `or stop after N turns` / `or stop after N minutes`
  to the objective to cap the run.
- Goal state persists as a `GoalEntry` in the session JSONL; `vtx --resume` restores
  the active pursuing goal automatically.
- `--goal "<objective>"` CLI flag works in both TUI and headless modes; headless
  prints status transitions to stderr so scripts can still pipe stdout.
- See [docs/goal.md](docs/goal.md).

### Changed

#### Background tasks now deliver results automatically
- Removed the `task_output` tool. Background sub-agents launched with `Task(background=True)`
  now deliver their final answer automatically via the completion notification that arrives
  between turns.
- The completion notification now includes the full final answer text instead of instructing
  the user to retrieve it via the removed tool.
- Background tasks are marked as notified in the disk record to prevent duplicate delivery.

## [0.1.5] - 2026-06-18 — Rate Limit Manager, Safe Max-Output & Gateway Fixes

Internal rate-limit manager with exponential backoff, safe max-output token limits by context window tier, Kilo transient error retries, and built-in skill filesystem syncing.

### Added

#### Internal rate-limit manager with automatic retries
- Added `RateLimitManager` (`src/vtx/llm/rate_limit.py`) that intercepts
  429 / rate-limit errors at the provider stream layer and retries with
  exponential backoff + jitter, transparently to the caller.
- `BaseProvider.stream()` now delegates to `rate_limit_manager.retry_stream()`,
  which re-calls `_stream_impl` on rate-limit errors up to 5 times by default.
- Respects `Retry-After` headers from the server when present.

#### Safe max-output token limits by context window tier
- Replaced the ad-hoc safety cap in `dynamic_models.py` with a centralized
  `safe_max_output_tokens()` function that tiers max output tokens by context
  window size: 128k → 8k, 256k → 16k, >256k → 32k, 1M → 64k.
- Auto-compaction now triggers at `context_window - max_output_tokens` instead
  of a fixed percentage, ensuring the model always has enough room for its
  max output regardless of window size.

#### Built-in skills synced to `~/.vtx/skills/` for reliable filesystem access
- Built-in skills are now auto-copied from the package to `~/.vtx/skills/` on
  every `Context.load()` and `Context.reload()`, so they live on the real
  filesystem and are readable by any tool.
- `load_skills()` now scans `~/.vtx/skills/` as a third discovery location
  (after project and user-global dirs), marking synced skills as `bundled=True`.
- `find_skill_dir()` in the skill tool searches `~/.vtx/skills/` between the
  user-global and package built-in locations, ensuring the model's `view`
  action resolves to the synced copy on disk.

### Changed

- Removed `RateLimitError` from `should_retry_for_error()` in OpenAI and
  Anthropic providers since rate limits are now handled internally by the
  rate limit manager.
- `is_overflow()` in `compaction.py` now accepts `max_output_tokens` instead
  of `threshold_percent`.

### Fixed

#### Kilo "Provider returned error" now retried automatically
- The OpenAI SDK raises `APIError("Provider returned error")` when an upstream
  gateway like Kilo fails transiently. This was previously uncaught by any retry
  path and surfaced directly to the user. Now retried at three layers:
  SDK-level `_is_transient_error`, provider-level `should_retry_for_error`,
  and the rate limit manager's `is_rate_limit_error`.
- Also catches "overloaded" and "capacity" transient errors from gateways.

#### Skill tool `file_path="None"` string normalization
- Added a Pydantic `model_validator` to `SkillParams` that converts the literal
  string `"None"` to Python `None` for nullable fields (`name`, `content`,
  `old_string`, `new_string`, `file_path`). Previously the LLM could pass
  `"file_path": "None"` which evaluated as truthy, causing the tool to construct
  paths like `.../review/None` instead of falling back to `"SKILL.md"`.

## [0.1.4] - 2026-06-18 — Handoff Agents, Background Tasks & Subagents

Switchable handoff agents, a built-in `Task` tool for Claude Code-style subagents, background concurrent task execution, a `grep` tool, custom TUI blocks for extension tools, and a context-length safety cap for gateway providers.

### Fixed

#### Context-length safety cap for gateway providers (Kilo, OpenRouter, etc.)
- Added a safety cap in `dynamic_models.py`'s `_parse_models()` and `_to_static_model()`
  that prevents `max_tokens` from equaling or exceeding `context_window`. Gateway
  providers like Kilo often return `max_completion_tokens == context_length` for free
  models (both 262144). With no input-token buffer, the API call would fail with a
  `400 context_length_exceeded` error when input tokens consumed part of the window.
  The cap reserves 8K for large context windows (>= 32K) and 4K for smaller ones,
  with a floor of `context_window // 2`.

### Added

#### Built-in `plan` Agent Profile and `grep` Tool
- Added a built-in `plan` agent profile configured for read-only plan formulation and investigation using a detailed system prompt, high-thinking level, and search/read tools.
- Created a built-in `grep` tool which utilizes `ripgrep (rg)` to perform efficient regex and pattern searching across files.
- Enhanced tool composition to allow any agent profile's `tools_allow` to dynamically pull in registered tools (like `grep`) that are not present in the default session toolset.
- Updated agent switching/cycling order to prioritize built-in profiles first, followed by user-defined profiles sorted alphabetically.
- Suppressed info messages in the TUI log when switching or cycling agent profiles to provide a quieter and cleaner user experience.


#### Custom TUI blocks for tools
- Extensions and agents can now ship a custom Textual block for any
  tool they register. Pass `ui_block=YourBlock` to
  `api.register_tool(...)`, `api.register_local_tool(...)`, or
  `api.local_tool(...)`, and the chat log instantiates `YourBlock`
  instead of the default `vtx.ui.blocks.ToolBlock` when the LLM
  invokes the tool.
- The bound `BaseTool` is exposed as `self.tool` on the custom block
  after construction, so the block can call back into the tool
  (`self.tool.format_call(params)`, `self.tool.format_preview(params)`,
  etc.). All existing `ToolBlock` hooks (`set_result`, `show_approval`,
  `hide_approval`, `update_call_msg`, `set_task_progress`) are
  inherited — override only what you need.
- New example: `examples/extensions/custom_tool_block.py` registers
  a `my_table` tool whose result renders as a Rich `Table` inside
  the chat log.
- New docs section: `docs/extensions.md#custom-tui-rendering`.
- New tests: `tests/ui/test_custom_tool_blocks.py` covers the
  registration APIs, the `BaseTool.ui_block` attribute, the chat-log
  routing, and end-to-end `set_result` dispatch.

#### `Task` tool: Claude Code-style subagents in the TUI
- The ``Task`` tool is now a default built-in tool in vtx. The LLM has
  a `task` tool available out of the box to delegate well-scoped tasks
  to isolated sub-agents.
- Tool surface (mirrors Claude Code's ``Task``): ``description``
  (3-5 word label), ``prompt`` (the instructions),
  ``subagent_type`` (e.g. ``general-purpose``, ``Explore``,
  ``Plan``, or any name from ``.vtx/agent/``), and an optional
  ``model`` override. v1 is synchronous: the parent blocks until
  the sub-agent finishes and receives its final output as the
  tool result.
- **New vtx platform feature — :mod:`vtx.dispatcher`.** Generic
  in-process dispatcher context that the runtime populates on
  every state change (init, agent change, model change,
  thinking-level change). Any extension that wants to dispatch
  sub-agents reads ``vtx.dispatcher.get_context()`` to find the
  parent's provider, model, cwd, agent-registry, etc. The
  built-in ``Task`` tool is the first consumer; other
  dispatching tools can use the same slot.
- **Sub-agent → main agent contract**: the ``Task`` tool returns
  ONLY the sub-agent's final text to the parent LLM. No
  preamble, no transcript of tool calls, no "sub-agent made N
  tool calls" framing, no truncation markers. The sub-agent's
  system prompt is augmented with a directive that tells it to
  give a focused, self-contained answer. The full transcript
  (turns, tool calls, tokens, session id) is preserved in
  `ui_details` for the TUI only — the LLM never sees it. If
  you need to debug a sub-agent after the fact, its full
  session is at `~/.vtx/tasks/<safe_cwd>/<id>.jsonl`.
- Built-in `subagent_type` presets: `general-purpose` (balanced), `Explore`
  (read-only repo navigation), `Plan` (read-only, plan-focused). User-
  defined agents from `.vtx/agent/<name>.py` are also accepted by name.
- Sub-agent sessions are persisted under
  `~/.vtx/tasks/<safe_cwd>/<timestamp>_<id>.jsonl`, isolated from the
  parent. Sub-agent instructions, tool allow/deny, model, provider, and
  thinking level are all inherited from the chosen profile.
- Live progress: the sub-agent's text and tool-call deltas stream into
  a nested progress label under the parent `Task` tool block. The
  parent `Task` block also gets a `ui_details` transcript (collapsible)
  with the full sub-agent run: turns, tool calls, and final text.
- Cancellation: the sub-agent's `cancel_event` is the same as the
  parent's. ESC in the TUI cancels the whole stack.
- New config section `task: { subagent_presets: [ ... ] }` (config
  version 9 → 10). Users can override the three built-in presets or
  add their own by name.
- New example: `examples/agents/explorer.py` — a read-only sub-agent
  profile that pairs naturally with the `Explore` preset.
- New tests: `tests/tools/test_task.py` covers params validation,
  preset fallback, sub-agent construction, the parent-context
  handoff, the final-answer system-prompt directive, and the
  no-metadata-leak guarantee on the LLM-facing tool result.

#### Switchable handoff agents (`.vtx/agent/<name>.py`)
- New `vtx.agents` package — a "profile" type that bundles instructions,
  optional model/provider/thinking overrides, a tool allow/deny list, and
  agent-scoped local tools, slash commands, and permission gates. Drop a
  Python file at `<project>/.vtx/agent/<name>.py` (or
  `~/.vtx/agent/<name>.py` for global) to make it discoverable. Project
  wins on name collision.
- Agent file format: module-level `AGENT = AgentDef(...)` constant for
  the static profile, plus an optional `register(api)` for imperative
  side effects. `AGENT.name` must match the file stem.
- `AgentAPI` mirrors `ExtensionAPI`: `local_tool(...)`,
  `local_command(...)`, `permission_gate(...)`, `on(event, handler)`,
  `on_agent_change(...)`. `local_tool` and `local_command` support both
  function-call and decorator forms.
- `AgentAPI.permission_gate(...)` accepts a small expression mini-language
  (`<path> matches '<literal>'`, `<path> == '<literal>'`) **or** a Python
  predicate, layered on top of the AgentDef's declarative
  `permission_gates`.
- TUI: `Shift+Tab` cycles through the discovered agents (`[None, *names]`,
  alphabetical). Active agent is shown in the info bar as `@ <name>`.
  Permission-mode cycling moved from `Shift+Tab` to `Alt+Ctrl+P` to
  make room. `Ctrl+Shift+P` is intentionally left free for a future
  command palette.
- New `/agent` slash command: `list`, `current`, `reload`, `off`,
  `<name>`, or no args to open the picker. Registered in the slash
  command palette.
- New events on the extensions bus: `agent_activated` (when an agent
  becomes active) and `agent_changed` (on switch). Both are in
  `ALL_EVENTS` and any extension can subscribe.
- `ExtensionAPI.register_local_tool(agent=..., name=..., ...)` — for
  extensions that aren't themselves agent files but want to contribute
  a tool scoped to a specific agent. The runtime merges these into the
  same per-agent bucket used by `AgentAPI.local_tool`.
- New config: `agents: { default, switch_mode, files }` and
  `last_selected.agent`. Config version bumped 8 → 9 with a no-op
  migration. Empty defaults; pre-v9 users get an empty `agents` block.
- New CLI flags: `--agent NAME`, `--agent-file PATH` (repeatable),
  `--no-agents`, `--list-agents`. Env var `VTX_AGENT=NAME` mirrors
  `--agent`.
- Active agent is persisted to `last_selected.agent` and rehydrated on
  next launch (when no `--agent` flag is passed).
- 49 new tests under `tests/test_agent_profiles*.py`. The `pytest` suite
  now runs 1145 tests, all green.
- 4 runnable examples under `examples/agents/`: `code-review.py`,
  `yolo.py`, `security-audit.py` (pulls in an agent-scoped extension),
  `security_extensions.py` (cross-agent `register_local_tool`).
- A working demo at `.vtx/agent/code-review.py` in this repo (real
  `git diff --stat`, review checklist, two permission gates, an
  `agent_activated` lifecycle hook).
- New doc: [docs/agents.md](docs/agents.md) covers authoring, the
  `AgentAPI` surface, discovery, the active tool-set formula, activation
  modes, CLI, TUI, events, configuration, and the cross-agent extension
  pattern.

### Changed

- `Shift+Tab` now cycles handoff agents. Permission-mode cycling moved
  to `Alt+Ctrl+P` (single chord, leaves `Ctrl+Shift+P` free for a
  future command palette). The previous 0.1.3 changelog mention of
  `Shift+Tab` cycling permissions is stale and refers to the pre-agents
  behavior.
- `build_system_prompt(...)` gained `extra_instructions` and
  `extra_instructions_mode` arguments used by the active agent. The
  base identity and AGENTS.md / skills / git / env sections are still
  composed in the same order; the agent's instructions slot in
  immediately after the base.
- `LoadedExtensions` gained a `local_tools_for(agent_name)` helper for
  merging cross-agent contributions from session extensions.

### Fixed

- `compose_active_tools` now correctly exempts an agent's own local
  tools from its `tools_allow` / `tools_deny` filters. Previously a
  restrictive `tools_allow` could strip the agent's own contributions
  (e.g. `pr_summary` was dropped from the active tool list when the
  `code-review` agent's `tools_allow` didn't include it).

## [0.1.3] - 2026-06-17 — VTX Agentic SDK

Introduces `vtx.sdk`, a programmatic, multi-agent interface built on top of
Vtx's lean runtime, plus cloud-backed built-in skills for Colab and Modal and
a `/provider` filter that scopes the `/model` picker to one gateway.

### Added

#### VTX Agentic SDK (`vtx.sdk`)
- New `vtx.sdk` package: a programmatic, multi-agent interface built
  on top of Vtx's lean runtime. Lets users build agentic applications
  using Vtx's tool registry, provider catalog, and session store
  without the TUI.
- Primitives: `Agent`, `Runner` (sync / async / streamed),
  `tool` decorator, `Handoff` / `agent.as_tool()`,
  `input_guardrail` / `output_guardrail` / `tool_input_guardrail` /
  `tool_output_guardrail`, `Session` Protocol with `InMemorySession`
  and `JSONLSession` backends, `Tracing` (Trace / Span / processors,
  with `ConsoleTraceProcessor` and `JSONLTraceProcessor` built-ins),
  `RunState` for resumable human-in-the-loop, `PermissionPolicy`
  (AutoApprove / AllowlistApprove / PromptApprove), structured
  `output_type` via Pydantic, and `load_vtx_skills()` for the
  existing `.agents/skills/` format.
- 8 runnable examples under `examples/sdk/` (quickstart, multi-agent
  handoff, multi-agent manager, guardrails, sessions, approvals,
  tracing, skills).
- Full reference under `docs/sdk/`: README, agents, runner, tools,
  multi_agent, sessions, guardrails, approvals, tracing, permissions,
  skills.
- 107 tests under `tests/sdk/`. Net new code: ~3,800 LoC. The TUI/CLI
  surface is untouched.

##### SDK provider refactor
- The `Agent` constructor no longer takes separate `provider_name`,
  `api_key`, `base_url`, or `thinking_level` fields. All provider
  configuration flows through a single `provider` parameter that
  accepts either a Vtx `BaseProvider` instance, a dict, or `None`.
- The dict shape distinguishes **built-in** vs **custom** providers:
  - **Built-in** providers (declared in Vtx's `provider.yaml`) need
    only `{name, api_key}` plus optional `ProviderConfig` overrides.
  - **Custom** / non-builtin providers require
    `{name, sdk, base_url, api_key}` — `sdk` is the SDK transport
    mode (`"openai"`, `"anthropic"`, …) and `base_url` is the
    endpoint.
- The user-facing import path is now consistently `from vtx.sdk
  import ...` (the old `vtx_sdk` references in docs and the SDK
  package's docstring have been corrected). 14 tests cover the new
  field shape (built-in, custom, env fallback, clone round-trip,
  BaseProvider instance).

#### Built-in skills
- `google-colab` — manage Google Colab sessions from the terminal: create/stop
  sessions, run code on GPU/TPU (T4, L4, A100, H100, v5e1, v6e1), upload/download
  files, install packages, mount Google Drive, and export session logs. Registered
  as `/google-colab` slash command.
- `modal` — run Python in the cloud with Modal serverless containers: GPU/TPU
  execution, autoscaling, persistent volumes, secrets, web endpoints, scheduled
  jobs, and batch processing. Registered as `/modal` slash command.

#### Provider filter for `/model`
- New `/provider` slash command opens a single-select dropdown (like `/login`)
  that scopes the `/model` picker to a single provider. The first row
  "All providers" clears the filter. `/provider reset` is a shortcut for the
  same clear-filter action.
- New `ui.model_provider_filter: str` config field (config version bumped 7→8
  with a migration that backfills `""`). Empty = show every provider; a slug
  restricts `/model` to that one provider. Unknown slugs are dropped on write.
  Managed by `/provider` or `set_model_provider_filter(...)`.

#### Internal
- New `ask_user` tool for interactive single/multi-select prompts (also
  available from the SDK via the `tool` decorator). Surfaces in the TUI as
  an `AskUserView` with arrow-key / number-key selection and Enter to confirm.

### Changed
- Restructured `builtin_skills/` to use category folders (`cloud/`, `code-review/`,
  `general/`, `meta/`, `setup/`). Skill discovery now recursively scans up to 2
  levels deep for builtins, while project/user skills remain single-level.
- `/model refresh` (no slug) now reports `Refreshing all N providers...` and
  handles the empty-provider-set case with a clear message. The per-provider
  count format is unchanged.
- Renamed the SDK's internal `function_tool` decorator to `tool` to match the
  public SDK surface. The `tool` name now consistently identifies the
  decorator across the SDK and the runtime.
- Auto-compaction now triggers on a **percentage** of the model's context
  window (default 75%) instead of a fixed token buffer. This keeps compaction
  cadence consistent across providers with very different context windows.
- AGENTS.md now instructs agents to look up a tool in the **agent's tool
  list** before the global registry, so extension tool overrides are honored
  on every turn.

### Fixed
- `get_all_models()` and `get_all_models_with_dynamic()` no longer return
  every dynamic model twice. The catalog and dynamic lists shared the same
  cache via `get_fetched_models` + `get_dynamic_models`; a new
  `dedupe_models()` helper keeps the first occurrence of each
  `(provider, id)` pair. Fixes the duplicate rows in the `/model` picker
  (2286 duplicates eliminated with a populated cache).
- Bundled skills are now included in the system prompt, so the model can
  discover and call them without an extra load step.
- The `ask_user` "Other" input is now actually focusable. Previously the
  inline `Input` was shown but the chat input kept focus, so typing went
  into the chat buffer and the user could never type a custom answer.
  The `ToolBlock` now moves focus to the inline input when the user
  navigates to the "Other" row and returns it to the chat input when
  they navigate away. The `AskUserInput` only forwards picker keys
  while it is hidden, and `escape` is always forwarded so the user can
  still cancel the prompt.

## [0.1.2] - 2026-06-16 — Extension System

Adds a first-class extension API that lets users add new LLM-callable tools,
intercept and modify tool calls, react to lifecycle events, and register new
slash commands — all without forking vtx. Modeled on the pi agent's extension
hooks, but native to vtx's Python stack.

### Added

#### Extension system
- `vtx.extensions` module: `ExtensionAPI`, `EventBus`, `ExtensionTool`,
  `ExtensionCommand`, file/package loader, and discovery.
- Auto-discovery from `<cwd>/.vtx/extensions/*.py` and
  `~/.vtx/agent/extensions/*.py` (with package-style `__init__.py` support).
  Project-local extensions take precedence over global ones.
- New `extensions:` list in `config.yml` for user-configured extension paths.
- New `--extension PATH` (repeatable) and `--no-extensions` CLI flags.
- Events: `session_start`, `session_end`, `agent_start`, `agent_end`,
  `turn_start`, `turn_end`, `tool_call`, `tool_result`, `compaction_start`,
  `compaction_end`.
- Blocking semantics: a `tool_call` handler can return `{"block": True,
  "reason": "..."}` to deny the call, or `{"args": {...}}` to rewrite the
  arguments before execution. A `tool_result` handler can return
  `{"output": "..."}` to rewrite the text the LLM sees. Modifications are
  chained across handlers.
- Tool override: an extension that registers a tool with the same name as a
  built-in (e.g. `read`, `bash`) replaces it. The extension version's
  description and parameter schema are what the LLM sees.
- Sync-only `session_start` / `session_end` emit so startup and shutdown can
  fire handlers without spinning up an extra event loop.
- Handler exceptions are caught and logged to stderr; they never crash the
  agent loop.
- Example extensions under `examples/extensions/`: `hello.py`,
  `permission_gate.py`, `auto_commit.py`, `tool_override.py`,
  `log_tool_calls.py`.
- Full reference: [docs/extensions.md](docs/extensions.md).

#### LLM providers
- 31 new gateways in `src/vtx/llm/provider.yaml`, taking the catalog from 18
  to 49 entries. All require authentication (`api_key_env` set on every
  entry; ollama is the only key-less provider, via `api_key_optional: true`
  for local use). All fetch their model list dynamically from the
  provider's `/models` endpoint with a 10-minute parser cooldown.
- OpenAI-compatible: AIHubMix (`AIHUBMIX_API_KEY`, custom `APP-Code` header),
  Apertis, Baseten, Berget AI, Blackbox AI, Cline (custom `/ai/cline/models`
  endpoint), Chutes AI, Cortecs, Crof, Dialagram, Dinference, Friendli,
  HicapAI, Jiekou, Knox, LightningAI, LLMGateway, MegaNova, Moark,
  ModelScope, MoonshotAI, NanoGPT, Pollinations AI, Routing.run, Seraphyn,
  Sherlock (CloudFerro), Vercel AI, Zenmux, Clarifai.
- Anthropic-compatible: FastRouter.
- All entries appear automatically in `/login` and the model picker — no
  other code changes needed because `provider.yaml` is the single source of
  truth.

#### Internal
- Config schema bumped to v7 with a v6 → v7 migration that initializes
  `extensions: []` and tolerates the first-pass dict form.
- `Loop` and `_TurnRunner` now accept an optional `EventBus`; events are
  fired at the right points without changing existing event payload shape.
- `tools.get_tools_with_extensions(default_names, extension_tools)` merges
  built-ins and extension tools with extension names winning.
- `commands.__init__` routes `/<name>` to extension-registered commands after
  built-ins get a chance to handle them.

## [0.1.1] - 2026-06-15 — Initial Public Release

The first public release of Vtx, a minimalist, developer-first coding agent
harness with a Textual TUI and a headless CLI. A complete agent loop with
multi-provider LLM support, a focused tool set, and a session model designed
to keep the model's context window free for what matters.

### Added

#### TUI and CLI
- Interactive Terminal User Interface built on [Textual](https://textual.textualize.io/),
  with a streaming chat log, collapsible thinking blocks, info bar, command
  palette, completion UI, and keyboard-driven session tree.
- Headless one-shot mode via `vtx -p "..."` for batched and CI use, with
  forced `auto` permission mode and restored prior mode on exit.
- Markdown rendering, LaTeX-to-Unicode, syntax highlighting, theming system,
  and clip-board copy/paste.

#### Tool set (5 core + 3 optional)
- `read` — paginated file/directory reader with image decode (`.png`, `.jpg`,
  `.jpeg`, `.gif`, `.webp`, `.bmp`) inline.
- `edit` — exact text replacement with 4-line context diff and approval-time
  preview.
- `write` — full file write with atomic temp-file + rename.
- `bash` — subprocess execution with cancellation, timeout, ANSI stripping,
  and tail truncation.
- `find` — glob-based file discovery via `fd` (with a `pathlib` fallback).
- `web_search` — semantic web search via the [Exa](https://exa.ai) MCP
  endpoint (no API key required).
- `web_fetch` — URL fetch returning clean markdown via the Exa MCP endpoint.
- `skill` — load and execute a registered skill (slash command, file-based
  prompt, or arbitrary markdown instruction).

#### LLM providers
- Catalog of **18 providers** sourced from `src/vtx/llm/provider.yaml`:
  Anthropic, OpenAI, DeepSeek, ZhiPu, OpenRouter, Groq, Together AI,
  Fireworks AI, Mistral AI, NVIDIA NIM, DeepInfra, Hugging Face, Ollama
  (local), Aerolink, Airouter, OpenCode Zen, Kilo Gateway, TokenRouter.
- Two OAuth flows with long-lived token storage:
  **GitHub Copilot** (device flow, with `gh` reuse) and **OpenAI Codex**
  (PKCE on `http://localhost:1455/auth/callback`).
- Dynamic model catalog fetching from each provider's `/models` endpoint,
  cached to `~/.vtx/models/<provider>.json` with per-provider TTL.
- `provider.yaml` is the single source of truth for providers; new entries
  appear in `/login` and the model picker automatically.
- `api_key_optional: true` for no-auth providers (ollama).
- `openmodelendpoint: true` for gateways whose `/models` catalog is
  publicly browsable (kilo, opencode, ollama).
- `headers: { ... }` per-provider extra request headers (e.g. Kilo's
  `X-KILOCODE-EDITORNAME`).
- `fetch_models: true` + `model_parser` (array path, id/name/context/output
  field names, cooldown) per provider.
- Per-model overrides via `context_length.py` for vision/thinking/tool/audio
  capabilities and max output tokens.

#### Slash commands
- `/login` and `/logout` — manage OAuth credentials and API keys for any
  provider; the picker shows all catalog providers plus status ("no key
  needed", "key required", "key stored", or "<ENV> set").
- `/model` — pick the active model from the merged static + dynamic catalog;
  `/model refresh [provider]` to force-fetch a fresh catalog.
- `/permissions` — toggle `prompt` / `auto`.
- `/thinking` — set the model's reasoning effort.
- `/themes` — switch between built-in themes.
- `/notifications` — configure update / launch notifications.
- `/new`, `/resume`, `/tree` — start, resume, or browse the session tree.
- `/session info` — show metadata for the current session.
- `/handoff` — fork the current conversation into a new session.
- `/compact` — context-length-driven compaction.
- `/export` — render the current session to standalone HTML.
- `/copy` / `/clear` — clipboard copy and chat-log reset.

#### Sessions
- Every conversation persisted as append-only JSONL at
  `~/.vtx/sessions/<safe-cwd>/<session-id>.jsonl` (mode `0700`).
- System prompt, tool list, and initial thinking level captured in the
  session header so resumes reconstruct the same environment.
- Tree navigation across branched and resumed sessions.
- Compaction with token-budget-driven summarization.
- Handoff to a new session while preserving history.

#### Permissions and security
- Two-mode system: `prompt` (default; asks before mutating tools) and
  `auto` (no prompts).
- Safe-command allowlist in `prompt` mode — read-only commands and read-only
  git subcommands pass without prompting.
- Non-interactive mode auto-denies any tool that still asks for approval.
- Each tool declares `mutating: bool`; read-only tools never prompt.

#### Context layering
- `AGENTS.md` discovery and loading (global + per-repo).
- Skills system: file-based prompts loaded on demand, project- and
  user-scoped, registered as slash commands.
- Built-in skills: `/init`, `/review`, `/skill-builder`.

#### Optional binaries
- Auto-discovers `fd` and `rg` on `PATH` (or in `~/.vtx/bin/`); falls back
  to `pathlib` / `re` walks when missing.

### Packaging and distribution
- Python 3.12+ (CI-tested on 3.12, 3.13, 3.14).
- Managed with [uv](https://docs.astral.sh/uv/); installed as a global tool
  via `uv tool install vtx-coding-agent`.
- Licensed under **Apache License 2.0**.

[0.1.4]: https://github.com/OEvortex/vtx-coding-agent/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/OEvortex/vtx-coding-agent/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/OEvortex/vtx-coding-agent/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/OEvortex/vtx-coding-agent/releases/tag/v0.1.1
