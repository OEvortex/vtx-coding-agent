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
  A coding agent that keeps its system prompt lean — around <b>~2,600 tokens</b> for the whole runtime —
  so your context window stays free for what matters: <i>your code</i>.
</p>

---

## Why Vtx?

Most coding agents bury you in thousands of hidden prompt tokens before you type a single line. **Vtx is transparent about its footprint.** The full runtime — base system prompt, tool guidelines, environment block, and all 11 tool definitions — fits in roughly **2,600 tokens** (o200k_base). That means:

- More of the model's context is spent on *your* files, not boilerplate instructions.
- Faster, cheaper turns with any provider you choose.
- A prompt you can actually read, audit, and shrink.

Vtx is also **modular**: a keyboard-driven TUI, a headless CLI, a Python SDK, and an optional gateway backend — pick the surface that fits the job.

---

## Two backends, one runtime

| Backend | What it's for | Powers |
| --- | --- | --- |
| **`vtx` native loop** | Single-session, event-stream agent loop with thinking streaming, tool permissions, and compaction. | The TUI + headless CLI (`vtx -p "..."`) |
| **`agenite_claw` gateway** | Production-grade multi-session loop: concurrent tool batching, context governance, crash-restore, subagents, MCP, cron, channel integrations. | The `agenite-claw` gateway, WebUI, and 16+ chat channels (`[claw]` extra) |

---

## Features

- **Lean by design** — ~2,600-token runtime; no hidden prompt bloat.
- **11 surgical tools** — `read`, `edit`, `write`, `bash`, `find`, `grep`, `skill`, `fetch_webpage`, `web_search`, `ask_user`, `task`.
- **TUI & CLI** — a Textual-powered terminal UI, plus a non-interactive headless mode for scripts and CI.
- **Any model, any endpoint** — 50+ built-in providers (OpenAI, Anthropic, Azure, DeepSeek, Copilot, Zhipu, Groq, Mistral, Together, Ollama, …) plus OpenAI/Anthropic-compatible custom providers and local models (Ollama, llama.cpp, vLLM).
- **Dynamic context** — auto-loads `AGENTS.md`/`CLAUDE.md` guidelines and triggers modular `Skills`.
- **Switchable handoff agents** — named profiles (review, security audit, fast impl) cycled live with `Shift+Tab`.
- **Task sub-agents** — delegate self-contained work to isolated sessions that stream progress back.
- **Safe by default** — `prompt` permission mode gates mutating tools; destructive commands are blocked.
- **Self-extensible** — drop a Python file to add tools, intercept calls, register slash commands, or hook lifecycle events.
- **Programmable SDK** — build multi-agent apps on the same runtime with `vtx.sdk`.

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
vtx -p "Write unit tests for src/vtx/utils.py"
```

Install the advanced gateway backend too:

```bash
uv tool install "vtx-coding-agent[claw]"
```

---

## The toolset

| Tool | Does | Tool | Does |
| --- | --- | --- | --- |
| `read` | Read/paginate files, view images | `fetch_webpage` | Fetch a URL as markdown |
| `edit` | Precise search-and-replace | `web_search` | Semantic web search |
| `write` | Create/overwrite files | `ask_user` | Ask a clarifying question |
| `bash` | Run commands in the cwd | `task` | Dispatch a sub-agent |
| `find` | Glob file discovery | `skill` | Manage skill workflows |
| `grep` | Regex search over files | | |

See [docs/tools.md](docs/tools.md) for full parameter specs.

---

## Permissions & switching agents

**Toggle permission mode on the fly.** Vtx gates mutating tools (`bash`, `edit`, `write`) behind a permission system. In the TUI:

- Press **`Alt+Ctrl+P`** to cycle between **`prompt`** (asks before mutating) and **`auto`** (unrestricted) mode.
- Type **`/permissions`** to open the permission menu and switch mode explicitly.
- Set the default in `config.yml` (`permissions.mode: prompt | auto`).

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
from vtx.sdk import Agent, Runner, tool

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
| Configuration | [docs/configuration.md](docs/configuration.md) |
| Providers & custom endpoints | [docs/providers.md](docs/providers.md) |
| Tools | [docs/tools.md](docs/tools.md) |
| Permissions | [docs/permissions.md](docs/permissions.md) |
| Sessions | [docs/sessions.md](docs/sessions.md) |
| Skills | [docs/skills.md](docs/skills.md) |
| Extensions | [docs/extensions.md](docs/extensions.md) |
| Handoff agents | [docs/agents.md](docs/agents.md) |
| Goals | [docs/goal.md](docs/goal.md) |
| Architecture | [docs/architecture.md](docs/architecture.md) |
| Local models | [docs/local-models.md](docs/local-models.md) |
| SDK | [docs/sdk/README.md](docs/sdk/README.md) |
| agenite-claw gateway | [docs/claw/README.md](docs/claw/README.md) |

---

## License

Apache License 2.0
