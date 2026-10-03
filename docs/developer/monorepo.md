# Monorepo structure

Vtx is a single Python distribution (`vtx-coding-agent`) built from ten import
packages under `src/vtx/`.

```
vtx-coding-agent/
├── src/vtx/
│   ├── protocol/       # leaf: message/stream types, tool contracts, error formatting
│   ├── telemetry/      # leaf: spans, processors, console + JSONL exporters
│   ├── git/            # gh CLI wrapper, GitHub App auth, branch metadata
│   ├── core/           # events, permissions, notifications, compaction, config +
│   │                   # defaults, themes, harness knobs, version
│   ├── ai/             # LLM layer only: providers/, oauth/, models, catalog,
│   │                   # rate limits, tool parsing
│   ├── codemode/       # the script sandbox, its isolation layers, the catalog
│   ├── agent/          # the harness: loop, turn, runtime, session, tools/,
│   │                   # prompts/, context/, goal/, agents/, hooks/, sdk/
│   ├── mcp/            # MCP client: transports (stdio, in-memory, streamable
│   │                   # HTTP), tool/resource exposure, OAuth, trust prompt
│   ├── tui/            # base Textual toolkit: input editing, fuzzy match,
│   │                   # overlay lists, block renderers
│   └── coding_agent/   # the product layer: cli.py, headless.py, the concrete
│                       # filesystem tools, builtin_skills/, and tui/
├── tests/              # pytest, mirroring the packages
├── docs/               # these docs
└── pyproject.toml      # hatchling; wheel packages = ["src/vtx"]
```

There is no `website/` or `examples/` directory in this repo.

## Dependency direction

See [architecture.md](../architecture.md) for the full DAG and the distinction
between module-level and function-local upward edges. In short: `protocol` and
`telemetry` are leaves, `core`/`git`/`ai` sit above them, `mcp`/`tui` are base
layers that depend on `agent.tools.base`, and `coding_agent` is the only package
that wires everything together.

## Entry points

- `vtx = vtx.coding_agent.cli:main` — parses flags, then dispatches to
  `coding_agent.tui.launch.run_tui` or `coding_agent.headless.run_headless`.
- SDK consumers import `from vtx.agent.sdk import Agent, Runner, tool`.

## Why this shape

The original monolith mixed provider plumbing with UI state, then buried the
whole runtime inside `vtx.ai`, which made the LLM package un-navigable. The
current split keeps:

- `core` and `ai` free of harness and product code, so tools and tests can use
  message types without an LLM stack;
- all agent logic in one place (`vtx.agent`) shared by TUI, headless, sub-agents
  and the SDK;
- the CLI/config/themes shell thin enough to swap (that's how headless mode
  exists at all);
- one module path per concern, so a refactor moves a module rather than leaving
  a shim behind.
