<pre align="center">
 ██╗   ██╗████████╗██╗  ██╗
 ██║   ██║╚══██╔══╝╚██╗██╔╝
 ██║   ██║   ██║    ╚███╔╝ 
 ╚██╗ ██╔╝   ██║    ██╔██╗ 
  ╚████╔╝    ██║   ██╔╝ ██╗
   ╚═══╝     ╚═╝   ╚═╝  ╚═╝
</pre>
<p align="center"><b>Minimalist & Modular Coding Agent</b></p>
<p align="center">
  <a href="https://pypi.org/project/vtx-coding-agent/"><img alt="PyPI" src="https://img.shields.io/pypi/v/vtx-coding-agent?style=flat-square" /></a>
  <a href="https://www.python.org/downloads/release/python-3120/"><img alt="Python" src="https://img.shields.io/badge/python-3.12%2B-blue?style=flat-square" /></a>
  <a href="LICENSE"><img alt="License" src="https://img.shields.io/badge/license-Apache%202.0-blue?style=flat-square" /></a>
</p>

---

**Vtx** is a minimalist, developer-first coding agent harness that delivers maximum capability with minimum overhead. 

Unlike heavy agentic frameworks that load thousands of hidden tokens, Vtx is transparent about its footprint. The Vtx-authored base prompt is roughly **2,000 tokens**, and the full runtime (base + tool guidelines + env block) is around **~2,200 tokens**. Composed prompts in real projects typically land in the **2,000–3,500 token** range once `AGENTS.md` and skill descriptions are attached.

By keeping the core prompt lean, Vtx leaves the model's context window open for what matters most: **your code, your project files, and your task context**.

---

## ⚡ Key Features

- **TUI & CLI Interfaces**: Work inside a keyboard-driven Terminal User Interface (TUI) powered by Textual, or run one-off prompts headlessly via the CLI.
- **Surgical Tools**: Armed with 6 core local files/terminal tools plus 2 optional web search & fetch tools.
- **Dynamic Context Layering**: Automatically loads repository-specific guidelines from `AGENTS.md` and triggers custom instructions via modular `Skills`.
- **Flexible Model Support**: Compatible with Hosted APIs (OpenAI, Anthropic, Azure, DeepSeek, ZhiPu) as well as unauthenticated local endpoints (Ollama, llama-server).
- **Collapsible Thinking Blocks**: TUI elegantly collapses finalized thinking chains to keep your workspace readable.
- **Secure Sandboxed Control**: Supports both `prompt` (confirmation before mutating changes) and `auto` permission modes.
- **Self-Extensible**: Drop a Python file in `~/.vtx/agent/extensions/` to add tools, intercept tool calls, register slash commands, and react to lifecycle events. See [docs/extensions.md](docs/extensions.md).

---

## 🚀 Quick Start

### Install
Installs Vtx as a global CLI tool using `uv`:
```bash
uv tool install vtx-coding-agent
```

### Run
Launch the interactive Terminal UI:
```bash
vtx
```

---

## 📖 CLI Usage

```text
usage: vtx [-h] [--model MODEL]
           [--provider {airouter,azure-ai-foundry,deepseek,github-copilot,kilo,openai,openai-codex,openai-responses,opencode,tokenrouter,zhipu}]
           [--prompt [PROMPT]] [--api-key API_KEY] [--base-url BASE_URL]
           [--openai-compat-auth {auto,required,none}]
           [--anthropic-compat-auth {auto,required,none}]
           [--insecure-skip-verify] [--continue] [--resume RESUME_SESSION]
           [--version]

options:
  -h, --help            show this help message and exit
  --model, -m MODEL     Model to use
  --provider PROVIDER   Provider to use
  --prompt, -p [PROMPT] Run a single prompt non-interactively, then exit (omit
                        the value or pipe stdin to read the prompt from stdin)
  --api-key, -k API_KEY API key to use
  --base-url, -u BASE_URL Base URL for API endpoints
  --insecure-skip-verify Skip TLS verification (useful for local self-signed certs)
  --continue, -c        Resume the most recent session
  --resume, -r ID       Resume a specific session by ID
```

### Common Examples
```bash
# Explicitly choose provider and model
vtx --provider openai -m gpt-4o

# Resume your last active session
vtx -c

# Run a single task non-interactively (headless mode)
vtx -p "Write unit tests for src/vtx/utils.py"
```

---

## 🛠️ The Toolset

Vtx equips the model with a compact and predictable set of tools:

### Core Tools (Enabled by default)
| Tool | Action | Description |
|---|---|---|
| `read` | Pagination & Image support | Read file contents efficiently without wasting tokens. |
| `edit` | Search-and-replace block | Apply surgical, precise edits to existing code files. |
| `write` | Write full contents | Create new files or perform complete rewrites. |
| `bash` | Command execution | Run tests, build steps, git commands, and scripts. |
| `find` | Glob file discovery | Locate files using project-aware `.gitignore` rules. |

---

## ⚙️ Configuration

Vtx stores its settings in a single YAML file:
```text
~/.vtx/config.yml
```
It is generated automatically on the first run. The default config with detailed inline comments is available at [`src/vtx/defaults/config.yml`](src/vtx/defaults/config.yml).

### Configuration Schema
```yaml
meta:
  config_version: 6

llm:
  default_provider: "openai"       # openai, deepseek, github-copilot, etc.
  default_model: "gpt-4o"
  default_base_url: ""             # override for local endpoints (e.g., http://localhost:11434/v1)
  default_thinking_level: "low"    # none, minimal, low, medium, high, xhigh
  tool_call_idle_timeout_seconds: 180
  request_timeout_seconds: 600

  auth:
    openai_compat: "auto"          # auto, required, none
    anthropic_compat: "auto"

  tls:
    insecure_skip_verify: false

  system_prompt:
    git_context: true
    content: ""                    # leave blank to use the built-in system prompt

compaction:
  on_overflow: "continue"          # continue (automatic compaction) or pause
  buffer_tokens: 20000

agent:
  max_turns: 500
  default_context_window: 200000

ui:
  theme: "gruvbox-dark"
  collapse_thinking: true
  thinking_lines: "1"
  colored_tool_badge: true
  show_welcome_shortcuts: true
  hidden_models: []
  model_provider_filter: ""         # empty = all providers; set to one slug to scope /model to that provider

permissions:
  mode: "prompt"                   # prompt (ask before modifying files/running bash) or auto (unrestricted)

notifications:
  enabled: true
  volume: 0.5
```

---

## 💻 Terminal UI (TUI) Interactions

Vtx features a keyboard-friendly interactive interface:

### Slash Commands
Type `/` at the start of the input box to access core commands:
- `/new` — Start a fresh conversation and reload project context.
- `/resume` — Interactive session history browser.
- `/model` — Switch models and providers on the fly.
- `/session` — Display active session statistics and token usage.
- `/compact` — Trigger manual context compaction.
- `/handoff <query>` — Summarize the current session and start a new, clean session with that context.
- `/themes` — Switch between 24+ built-in color schemes (e.g., `dracula`, `tokyo-night`, `catppuccin`).
- `/permissions` — Toggle permission mode (`prompt` vs `auto`).
- `/export` — Export the current chat transcript to a beautiful standalone HTML file.

### Direct Shell Execution
Run terminal commands directly from the input box:
- `!ls -la` — Execute a command and view output in the chat window.
- `!!pytest` — Execute a command, display output, and send that output to the LLM for immediate analysis.

---

## 📝 Layering Context: AGENTS.md & Skills

### AGENTS.md / CLAUDE.md
Vtx discovers instructions dynamically from your environment. It checks files named `AGENTS.md` or `CLAUDE.md` in:
1. Your global configuration folder (`~/.vtx/AGENTS.md`)
2. Ancestor folders from the git root down to the current working directory.

Use this file to specify project guidelines, code styling preferences, or test runner commands.

### Custom Skills
Skills are reusable instruction directories loaded from `.agents/skills/` (project-level) or `~/.agents/skills/` (global). Each skill contains a `SKILL.md` file:

```markdown
---
name: deploy-project
description: Instructions on how to deploy this project
register_cmd: true
cmd_info: Run project deployment steps
---

# Deploy Project
To deploy, the agent should run:
1. `uv run python build.py`
2. `git push origin main`
```
Setting `register_cmd: true` registers the skill as a slash command (`/deploy-project`) in the TUI command menu.

---

## 📚 Reference Docs

For deeper information, consult the topic-specific files in the [`docs/`](docs/) directory:

- [docs/configuration.md](docs/configuration.md) — Reference for all config keys, schemas, and migrations.
- [docs/providers.md](docs/providers.md) — Authentication setup, environment keys, and dynamic LLM gateways.
- [docs/tools.md](docs/tools.md) — Complete tool parameter specs, mutating flags, and pre-requisites.
- [docs/permissions.md](docs/permissions.md) — Safe-command lists and user approval heuristics.
- [docs/sessions.md](docs/sessions.md) — Session JSONL format, history files, handoff guides, and compaction.
- [docs/skills.md](docs/skills.md) — Authoring custom Skills, argument parsing, and command mapping.
- [docs/extensions.md](docs/extensions.md) — Python extension API: add tools, intercept tool calls, register slash commands.
- [docs/theming.md](docs/theming.md) — Catalog of the 24+ built-in themes and color tokens.
- [docs/headless.md](docs/headless.md) — Non-interactive execution, piped input streams, and exit codes.
- [docs/storage-layout.md](docs/storage-layout.md) — Complete directory mapping of files on disk.
- [docs/local-models.md](docs/local-models.md) — Running Vtx against local models (llama.cpp, Ollama).
- [docs/architecture.md](docs/architecture.md) — Codebase architecture map, message structures, and runtime loop.
- [docs/development.md](docs/development.md) — Building, testing, linting, and maintaining Vtx.
- [docs/sdk/](docs/sdk/) — The VTX Agentic SDK: programmatic multi-agent interface built on Vtx's runtime.

---

## 📄 License

Apache License 2.0
