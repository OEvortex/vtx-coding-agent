<div align="center">

<table align="center">
<tr><td align="center">

```
██╗   ██╗████████╗██╗  ██╗
██║   ██║╚══██╔══╝╚██╗██╔╝
██║   ██║   ██║    ╚███╔╝
╚██╗ ██╔╝   ██║    ██╔██╗
 ╚████╔╝    ██║   ██╔╝ ██╗
  ╚═══╝     ╚═╝   ╚═╝  ╚═╝
```

</td></tr>
</table>

</div>

<p align="center"><b>The minimalist, modular coding agent harness</b></p>
<p align="center"><b>Maximum capability. Minimum overhead.</b></p>

<p align="center">
  <a href="https://github.com/OEvortex/vtx-coding-agent"><img alt="GitHub" src="https://img.shields.io/github/stars/OEvortex/vtx-coding-agent?style=for-the-badge&label=Stars" /></a>
  <a href="https://pypi.org/project/vtx-coding-agent/"><img alt="PyPI" src="https://img.shields.io/pypi/v/vtx-coding-agent?style=for-the-badge" /></a>
  <a href="https://pypi.org/project/vtx-coding-agent/"><img alt="Downloads" src="https://img.shields.io/pypi/dm/vtx-coding-agent?style=for-the-badge" /></a>
  <a href="https://www.python.org/downloads/release/python-3120/"><img alt="Python" src="https://img.shields.io/badge/python-3.12%2B-blue?style=for-the-badge" /></a>
  <a href="LICENSE"><img alt="License" src="https://img.shields.io/badge/license-Apache%202.0-blue?style=for-the-badge" /></a>
</p>

<p align="center">
  A coding agent that keeps its system prompt lean — under <b>1k tokens</b> for the base system prompt —
  so your context window stays free for what matters: <i>your code</i>.
</p>

---

## Why Vtx?

Most coding agents bury you in thousands of hidden prompt tokens before you type a single line. **Vtx is transparent about its footprint.** The prompt text lives in one place — `src/vtx/agent/prompts/identity.py` — and is deliberately small: the base system prompt is well under **1k tokens**, before the environment block and tool definitions are added. That means:

- More of the model's context is spent on *your* files, not boilerplate instructions.
- Faster, cheaper turns with any provider you choose.
- A prompt you can actually read, audit, and shrink.

Vtx is also **modular**: a keyboard-driven TUI, a headless CLI, a Python SDK, and an optional extension manager — pick the surface that fits the job.

---

## Features

- **Lean by design** — a sub-1k-token base system prompt; no hidden prompt bloat.
- **10 surgical default tools** — `read`, `edit`, `write`, `bash`, `find`, `skill`, `web`, `ask_user`, `delegate_subagent`, `goal`. The harness also ships `codemode` and `tool_search`. (`grep` is a built-in but not enabled by default.)
- **TUI & CLI** — a Textual-powered terminal UI, plus a non-interactive headless mode for scripts and CI.
- **Any model, any endpoint** — 60+ built-in providers (OpenAI, Anthropic, DeepSeek, Copilot, Zhipu, Groq, Mistral, Together, Ollama, …) plus OpenAI/Anthropic-compatible custom providers and local models (Ollama, llama.cpp, vLLM).
- **Dynamic context** — auto-loads `AGENTS.md`/`CLAUDE.md` guidelines and triggers modular `Skills`.
- **Switchable handoff agents** — profiles you write in `.vtx/agent/<name>.py` (instructions, tool allow/deny list, optional model override), cycled live with `Shift+Tab`, or activated with `/agent <name>`. No profiles ship built in.
- **Task sub-agents** — delegate self-contained work to isolated sessions that stream progress back.
- **Persistent goals** — give the agent a durable, file-backed objective with a task tree, live status widget, auto-continue checkpoints, and an independent completion audit. See [docs/goals.md](docs/goals.md).
- **Safe by default** — `prompt` permission mode gates mutating tools; destructive commands are blocked.
- **Self-extensible** — drop a Python file to add tools, intercept calls, register slash commands, or hook lifecycle events.
- **YAML hooks** — declarative `.vtx/hooks.yml` for shell and HTTP lifecycle automation.
- **Extension manager** — install extensions and agent packages from PyPI or GitHub with `vtx install <name>`.

---

## Quick start

```bash
# Install with uv (recommended)
uv tool install vtx-coding-agent

# Or the one-liner installer
curl -fsSL https://raw.githubusercontent.com/OEvortex/vtx-coding-agent/main/scripts/install.sh | bash
```

Launch the terminal UI:

```bash
vtx
```

Run a single task headlessly:

```bash
vtx -p "Write unit tests for src/vtx/agent/tools/task.py"
```

---

## The toolset

| Tool | Does | Tool | Does |
| --- | --- | --- | --- |
| `read` | Read/paginate files, view images | `web` | Web search (Exa neural) |
| `edit` | Precise search-and-replace | `ask_user` | Ask a clarifying question |
| `write` | Create/overwrite files | `delegate_subagent` | Dispatch a sub-agent |
| `find` | Glob file discovery | `skill` | Manage skill workflows |
| `bash` | Run shell commands | `goal` | Persistent goals: plan, track, complete w/ audit |

See [docs/tools.md](docs/tools.md) for full parameter specs.

---

## Permissions & switching agents

**Toggle permission mode on the fly.** Vtx gates mutating tools (`bash`, `edit`, `write`) behind a permission system. In the TUI:

- Press **`Alt+Ctrl+P`** to cycle between **`prompt`** (asks before mutating) and **`auto`** (unrestricted) mode.
- Type **`/permissions`** to open the permission menu and switch mode explicitly.
- Set the default in `~/.vtx/config.yml` (`permissions.mode: prompt | auto`).

Destructive commands (`rm -rf`, `git reset --hard`, force-push, dropping tables) are blocked unless you explicitly ask. See [docs/permissions.md](docs/permissions.md).

**Switch handoff agents with `Shift+Tab`.** Define named profiles in `.vtx/agent/<name>.py` (e.g. `security-audit`, `code-review`, `explorer`) and cycle between them live — each bundles its own instructions, tool allow/deny list, and optional model override. See [docs/agents.md](docs/agents.md).

---

## Bring your own provider

Point Vtx at any OpenAI- or Anthropic-compatible endpoint — no source edits required:

```yaml
# .vtx/providers/acme.yaml
slug: acme
display_name: "Acme AI Gateway"
family: openai_compat
base_url: "https://ai.acme.internal/v1"
api_key_env: ACME_API_KEY
fetch_models: true
```

```bash
export ACME_API_KEY=sk-...
vtx --provider acme -m acme-large
```

Custom providers show up in the `/model` picker and auto-fetch their model catalog. Full reference in [docs/providers.md](docs/providers.md).

---

## Build agents programmatically

```python
from vtx.agent.sdk import Agent, Runner, tool

@tool
def get_weather(city: str) -> str:
    """Return the current weather for a city."""
    return f"Sunny in {city}"

agent = Agent(
    name="Weather bot",
    instructions="Be concise.",
    model="gpt-4o-mini",
    tools=[get_weather],
)

result = Runner.run_sync(agent, "Weather in Tokyo?")
print(result.final_output)
```

See the [SDK docs](docs/sdk/README.md).

---

## Documentation

| Topic | Link |
| --- | --- |
| Documentation index | [docs/README.md](docs/README.md) |
| Tour by audience | [docs/index.md](docs/index.md) |
| Architecture and the ten packages | [docs/architecture.md](docs/architecture.md) |

---

## Repository layout

One distribution (`vtx-coding-agent`), ten packages under `src/vtx`:

| Package | Role |
| --- | --- |
| `vtx.protocol` | Leaf. Message, stream and tool-contract types. Imports nothing from `vtx`. |
| `vtx.telemetry` | Leaf. Spans, processors, console and JSONL exporters. |
| `vtx.git` | Git and GitHub integration, `gh` CLI wrapper, GitHub App auth. |
| `vtx.core` | Agent events, permission gate, compaction, config schema and paths, theme registry. |
| `vtx.ai` | The LLM layer: provider catalog, OAuth, SDK adapters, model catalog, tool parsing. |
| `vtx.codemode` | The confined script sandbox and its tool discovery layer. |
| `vtx.agent` | The product-neutral harness: loop, turn engine, sessions, tool registry, prompts, extensions, goals, SDK. |
| `vtx.mcp` | MCP client: server config, transports, tool exposure, trust prompt. |
| `vtx.tui` | Base terminal-UI toolkit: editing, fuzzy matching, overlays, block renderers. Knows nothing about the agent. |
| `vtx.coding_agent` | The product layer: CLI entry point, headless runner, concrete filesystem tools, the TUI app. |

Dependencies flow `protocol`/`telemetry` -> `core`, `git`, `ai` -> `agent` -> `coding_agent`, with `mcp` and `tui` above. Most packages carry their own `README.md` with the public surface and internal notes; start with [docs/architecture.md](docs/architecture.md) for the full map.

---

## License

Apache License 2.0
