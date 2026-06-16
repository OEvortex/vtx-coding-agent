# Changelog

All notable changes to Vtx are documented in this file. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

### Changed
- Restructured `builtin_skills/` to use category folders (`cloud/`, `code-review/`,
  `general/`, `meta/`, `setup/`). Skill discovery now recursively scans up to 2
  levels deep for builtins, while project/user skills remain single-level.
- `/model refresh` (no slug) now reports `Refreshing all N providers...` and
  handles the empty-provider-set case with a clear message. The per-provider
  count format is unchanged.

### Fixed
- `get_all_models()` and `get_all_models_with_dynamic()` no longer return
  every dynamic model twice. The catalog and dynamic lists shared the same
  cache via `get_fetched_models` + `get_dynamic_models`; a new
  `dedupe_models()` helper keeps the first occurrence of each
  `(provider, id)` pair. Fixes the duplicate rows in the `/model` picker
  (2286 duplicates eliminated with a populated cache).

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
- `/permissions` — toggle `prompt` / `auto`; `Shift+Tab` cycles modes in the
  TUI.
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
