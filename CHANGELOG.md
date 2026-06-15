# Changelog

All notable changes to Vtx are documented in this file. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
