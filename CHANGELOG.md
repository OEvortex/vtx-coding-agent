# Changelog

All notable changes to Vtx are documented in this file. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.2.0] - 2026-09-08

### Added
- **RLM mode with persistent IPython REPL** — new `rlm` mode that collapses the agent toolset to a single `ipython` tool backed by a long-lived IPython subprocess; variables, imports, and side effects persist across turns, matching Prime Agent's REPL-first workflow.
- **IPython manager with kernel pool** — `IpythonManager` maintains a pool of persistent IPython kernels keyed by session, enabling parallel subagent execution without restarting the REPL; output streaming uses a newline-delimited JSON protocol.
- **Synchronous thread-based runtime** — `IpythonRuntime` runs cell execution in a thread executor and uses a blocking stdout reader plus Queue, eliminating async reader race conditions and reducing latency compared to the previous async approach.
- **Settings UI toggle for RLM mode** — mode selection moved from a slash command to the Settings panel (`mode: standard | rlm`), streamlining the UI surface.
- **RLM prompt infrastructure** — dedicated `rlm_master_prompt.md`, prompt builder, and system prompt for the restricted-tool RLM runtime.
- **Tool-call RPC bridge** — the REPL can now invoke main-process tools (`web_search`, `goal_*`, `rlm`, etc.) via a `tool_call`/`tool_result` JSON protocol over stdin/stdout, so helper functions actually execute instead of raising `NameError`.

### Changed
- **`python_repl` renamed to `ipython`** across tools, prompts, config, and manager modules to reflect the persistent-kernel model.
- **RLM mode restricts runtime tools** — when `mode=rlm`, the active tool list is reduced to the single `ipython` tool, removing standard coding-agent tools for the duration of the session.
- **Dedicated stdin reader thread** — the runtime now uses a daemon thread + `asyncio.Queue` for stdin instead of `run_in_executor(readline)`, preventing deadlocks when a worker thread blocks waiting for a tool result.

### Fixed
- **Double-execution of trailing expressions** — the runtime now skips the eval block when the expression contains a `Call` or `Yield`, preventing outputs like `print(2+2)` from running twice and breaking tool-call loops.
- **Premature turn ending in RLM mode** — empty-cell synthesis, errored tuple returns, and `mutating=False` on the `IpythonTool` were corrected so the runtime waits for real output instead of ending the turn early.
- **IPython runtime async blocking** — cells now execute in a worker thread, and trailing-expression detection uses `ast.parse` so the asyncio loop stays free and the runtime doesn't hang on blocking calls.
- **`IpythonManager.execute()` keyword argument mismatch** — `tool_executor` is now accepted and forwarded to `IpythonKernel.execute()`, fixing the unexpected-keyword error when REPL helpers dispatch tool calls.
- **REPL helper functions not actually registered** — `web_search`, `goal_get`, `goal_update`, `goal_set_tasks`, `rlm`, and `call_tool` are now injected into the REPL namespace, matching what the RLM system prompt documents.

## [1.1.3] - 2026-09-07

### Fixed
- **Source distribution bloat** — excluded `Site/node_modules` from the sdist, reducing package size from ~41MB to ~5.4MB.

## [1.1.2] - 2026-09-07

### Added
- **Goal archive action** — `goal(action="archive")` lets the agent kill/focus the current goal on demand without requiring completion audit.
- **OpenJarvis standalone packaging** — `src/vtx/openjarvis/pyproject.toml` so OpenJarvis can be built/published independently from the core `vtx-coding-agent` package.
- **Codex CLI metadata** — provider requests now include Codex CLI version and originator headers via the Responses API transport; `max_output_tokens` is omitted for Codex payloads.
- **Experiential Labs provider** — registered `experientiallabs` as a new OpenAI-compatible provider with base URL `https://api.experientiallabs.ai`, resolving `EXPERIENTIALLABS_API_KEY` from the environment.
- **Thinking-level propagation tests** — added coverage for `reasoning_options` parsing into `TokenLimits.thinking_level_map` and for propagating that map through provider catalog/model construction.

### Changed
- **Removed direct goal slash commands from TUI routing** — lifecycle actions (`pause`, `resume`, `clear`, `settings`, etc.) are no longer exposed as `/goal-*` commands; users drive them through the agent via the `goal` tool or `Esc` while running.
- **Core `pyproject.toml` trimmed** — OpenJarvis entry points and optional dependency groups moved to the OpenJarvis package manifest.
- **Reasoning metadata flows through model resolution** — `ContextLengthManager`, `provider_catalog`, and `model_fetcher` now preserve `thinking_level_map` from token limits into resolved `Model` objects, using limit-derived values when provider entries omit them.
- **OpenAI-family thinking enablement** — expanded OpenAI SDK thinking support to include the base `openai` provider slug alongside `openai-codex` and `openai-responses`, aligning wire-parameter behavior with provider resolution.
- **Codex OAuth consolidation** — OAuth implementation unified under the `codex` provider slug; legacy `openai` credential aliases removed; default thinking level applied for models without explicit effort options.
- **InfoBar 2-row layout** — CompactFooter renamed to InfoBar and merged into a 2-row layout across TUI widgets, app composition, commands, and styles.

### Fixed
- **Pydantic field shadowing in `run_cli_app`** — renamed the `json` parameter to `json_output` in the CLI Apps tool schema and executor, removing the `Run_Cli_App_Params` shadowing warning on startup.
- **ExperientialLabs model discovery** — corrected the provider to use `/api/models` instead of `/models`, and added `flatten_field: model` parser support so `_parse_models` unwraps nested response objects before extracting `id`, `name`, `context_length`, and `max_output_tokens`. Restores live model fetching for providers whose `/models` payload wraps each entry in a nested object.
- **Goal completion pause & auditor deadlock** — fixed an issue where `goal(action="update", status="complete")` erroneously marked the goal status as `paused` during the completion review.
- **Subagent & auditor approval handling** — auditor and task subagent loops now automatically resolve `ToolApprovalEvent` and `AskUserEvent` futures, preventing sub-agents and completion audits from hanging indefinitely on approval gates.
- **Session-start hooks/extensions never fired in the TUI** — `session_start` was emitted with the sync-only bus emit, which silently skips async handlers; it now emits through an async worker so YAML hooks (`SessionStart`) and async extension handlers run.
- **Headless runs never emitted session lifecycle events** — `vtx -p ...` now fires `session_start` / `session_end` on the extension EventBus, so `SessionStart`/`SessionEnd` hooks work in non-interactive mode too.
- **`PostToolUseFailure` and permission hooks were unreachable** — the tool-result bus event now also dispatches `PostToolUseFailure` (only when the tool errored), and the approval gate emits new `permission_request` / `permission_denied` bus events so `PermissionRequest` / `PermissionDenied` YAML hooks fire.
- **Type-checker diagnostics in extension wiring** — `find_dynamic_model` imported from its actual module (`vtx.ai.dynamic_models`), narrowed `api_type` to `ApiType` in the session-title extension, asserted the non-None `ExtensionRunner` before binding actions, and replaced the nonexistent `runtime.set_model` reference with a catalog-resolving adapter over `ConversationRuntime.switch_model`.
- **Stale goal focus on session resume** — starting a fresh session after resuming one without goal state now clears the previous focused-goal id instead of silently keeping it active.

## [1.1.0] - 2026-08-26

### Added
- **Clipboard image paste** — `ctrl+v` pastes images from the system clipboard into the input as `[image #N]` placeholders (up to 5 per message); queued and steer messages carry their attached images through editing and replay.
- **Image-aware submissions** — images are ordered before text in user messages for better grounding, text is optional for image-only submissions, and the welcome-screen shortcut list documents `ctrl+v`.
- **`vtx.core.bytes_util`** — shared byte-size formatting/parsing helpers (`format_bytes`, `parse_bytes`) with dedicated tests.
- **Persistent goal system (`vtx-goal`)** — durable, file-backed objectives for long-running work, ported from the pi-goal-x workflow. `/goal [seed]` and `/sisyphus [seed]` run a guided draft (clarify → propose → confirm) while `/goal-direct` / `/sisyphus-direct` create immediately; goals live as editable markdown under `.vtx/goals/` with an append-only activity ledger and survive across sessions (session-scoped focus via `/goal-focus`, multiple open goals per project). The agent drives everything through a single action-based `goal` tool (`create | get | update | set_tasks | update_task`) with id-stable parent-linked task trees and per-task verification contracts. An above-editor status beacon shows live progress (status · elapsed/tokens · task window · current task · contract · file), `Ctrl+Shift+G` posts the expanded dashboard, auto-continue checkpoint turns keep the agent working toward an active goal until it genuinely finishes — pressing `Esc` pauses — and completion runs an independent auditor sub-agent that inspects the workspace before archiving (`<approved/>` archives; changes-required feedback stays attached). Manage with `/goal-list`, `/goal-status [verbose|health]`, `/goal-unfocus`, `/goal-tweak`, `/goal-pause`, `/goal-resume`, `/goal-clear`, `/goal-cancel`, and `/goal-settings`. See [docs/goals.md](docs/goals.md).
- **Idle session recap** — after an agent run finishes and you stay idle (`recap.idle_seconds`, default 30s), or when you resume a session, vtx drafts a 1–3 sentence "where you left off" summary using the current model and renders it in the chat log; typing clears it. New `/recap` slash command drafts one on demand, and `recap.enabled: false` opts out.
- **OpenAI Responses API adapter & unified reasoning resolution** — new Responses transport adapter with unified reasoning-effort resolution; the Responses transport is now routed through the official `openai` SDK.
- **Extended-thinking parity and token tracking** — per-model reasoning-effort detection from models.dev drives thinking levels on any verified provider, with stream cleanup and usage-tracking parity across transports.
- **Interactive UI primitives for extensions (`ctx.ui`)** — handlers declared as `(event, payload, ctx)` now receive a full context whose `ctx.ui` exposes interactive dialogs: `await ctx.ui.confirm()`, `await ctx.ui.select()`, `await ctx.ui.input()` (real modal dialogs backed by the Textual TUI, safe no-op defaults in headless mode), `ctx.ui.notify()` rendered in the chat log, and `ctx.ui.setStatus()` / `ctx.ui.setWidget()` for a persistent footer bar. All dialogs support `timeout` and abort-`signal` kwargs.
- **`await ctx.ui.custom(component)`** — show arbitrary Textual widgets or `ModalScreen`s modally from an extension and await the dismissed result.
- **Provider request hooks** — new `before_provider_headers` and `before_provider_request` extension events fire once per outgoing LLM request across all transports (OpenAI SDK, Anthropic HTTP). Handlers can inject/override/delete HTTP headers and inspect or fully replace the wire payload; later handlers chain off earlier replacements; retries reuse prepared values without re-firing handlers. Backed by a transport-level registry (`vtx.ai.provider_hooks`) bridged onto the extension bus automatically.
- **Cline provider OAuth login** — Cline (WorkOS) added to `/login` with full OAuth flow, credential storage, and free-model detection reflected in model listings.
- **`/update` command** — check for and install the latest vtx release from inside the TUI.
- **Configurable models endpoint & unified provider refresh** — `/model refresh` now covers dynamic and legacy catalog providers through one path, with a configurable models endpoint per provider.
- **Task tool UI/UX parity** — redesigned the Task tool block rendering with `▸ <subagent_name> <description>` header formatting, live 80ms braille spinner animation with turn count (`↻5≤30`), active tool/text activity line (`⎿ reading…`), token metrics, execution duration tracking, and collapsible/expandable output formatting.

### Changed
- **Harness/coding-agent package split** — `vtx.ai.agent` is now a product-neutral harness (loop, turn engine, session store, tool contracts, extensions/hooks, SDK); concrete built-in tools, prompt/context assembly, subagent definitions, and the runtime composition root moved to `vtx.coding_agent`. The harness no longer imports product code: system-prompt building, context loading, the tool registry, and user config knobs are injected, with harness-owned defaults mirroring user YAML.
- **ask_user dialog extracted** — shared dialog logic moved to `vtx.tui.ask_user` with dedicated test coverage.
- **API type unification** — duplicate `openai-completions` folded into `openai-sdk`; fetched-model cache now carries thinking/free metadata for picker rendering.
- **Handoff prompt in handoff link details** — handoff link blocks now include the handoff prompt in their details view.
- **Simplified Hatch wheel packaging configuration.**
- **`api.notify()` routes through the TUI chat log** when a UI is installed instead of only logging.

### Fixed
- **Auto-compaction honored the 200k default instead of the model's real context window** — the engine now resolves the active model's catalog context window on every agent creation and `/model` switch, so 1M-context models compact near their true limit (~800k at the default 80% threshold) instead of ~160k; unknown models still fall back to `agent.default_context_window`.
- **`/model` picker now shows the last-selected model** — the last-selected model is now remembered across sessions and pre-selected in the `/model` picker; if the last-selected model is no longer available, the default model is selected instead.
- **ctrl+t now properlly cycles between thinking levels** — the `ctrl+t` shortcut now cycles between models thinking levels, and the current level is displayed in the status bar.
- **Stale provider labels on resumed sessions** — sessions recorded under a wrong provider label (e.g. `openai` for a custom gateway like kilo) are healed at initialize time, keeping lookups, pricing, and context-window resolution on the right catalog entry; unknown models no longer silently relabel the provider as the engine class name.
- **Event class map ImportError** — `_get_event_class_map()` self-imported event classes from `vtx.ai.agent.extensions`; it now imports agent/turn lifecycle events from `vtx.core.events`, fixing crashes on first event-object lookup.
- **Restored missing `get_valid_openai_credentials` export** from `vtx.ai` (accidentally dropped during the cline OAuth refactor; `/login` OpenAI flow depended on it).
- **Removed duplicated `_emit_error` definition** in the extension runner.

### Removed
- **Placeholder hook events** — trimmed `HOOK_EVENTS` from 30 to the 11 events vtx actually implements (`UserPromptSubmit`, `SubagentStart/Stop`, `Stop/StopFailure`, `Notification`, `PostSampling`, `Setup`, `InstructionsLoaded`, `CwdChanged`, `FileChanged`, `Worktree*`, `ConfigChange`, `Task*`, `TeammateIdle`, `Elicitation*` removed); configs using unsupported events are now rejected at load instead of being silently ignored. Docs updated.
- **Dead code cleanup** — unused `original_text` parameter of `_handle_shell_command` (callers/tests updated), a no-op `if False: pass` block in the SDK agent module, and the string-based `progress_bar()` helper superseded by the themed `progress_bar_text()`.
- **Supercode provider** — removed the Supercode proxy provider, its OAuth flow (`vtx.ai.oauth.supercode`), SDK adapter (`vtx.ai.sdk.supercode`), provider implementation (`vtx.ai.providers.supercode`), associated tests, and catalog/docs references.
- **Notes in `ask_user` dialog** — removed the per-question note input editor (`n` key shortcut, note input widget, and note markers) and questionnaire global note handling across the TUI dialog, data envelopes, and models.
