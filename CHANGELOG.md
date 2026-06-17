# Changelog

All notable changes to Vtx are documented in this file. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

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

[0.1.1]: https://github.com/OEvortex/vtx-coding-agent/releases/tag/v0.1.1
