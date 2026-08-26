# Monorepo structure

Vtx is a single Python distribution (`vtx-coding-agent`) built from four import packages under `src/`, plus a website and examples.

```
vtx-coding-agent/
├── src/
│   ├── ai/             # LLM layer + agent harness
│   │   ├── providers/  #   openai_sdk, anthropic_sdk, mock, sanitize
│   │   ├── sdk/        #   thin LLM SDK adapters (openai, anthropic)
│   │   ├── oauth/      #   copilot, openai, dynamic logins
│   │   └── agent/      #   the harness: loop, turn, runtime, session,
│   │       │           #   tools/, prompts/, context/, extensions/,
│   │       │           #   agents/ (handoff profiles), hooks/
│   │       └── sdk/    #   the VTX Agentic SDK (docs/sdk/)
│   ├── coding_agent/   # app shell: cli.py (entry point), config.py,
│   │                   # headless.py, themes.py, defaults/config.yml
│   ├── core/           # types, events, permissions, compaction, handoff,
│   │                   # paths, notify, scratchpad, tracing/ — imports nothing internal
│   └── tui/            # Textual app: app.py + mixins, chat/blocks rendering,
│                       # input, commands/, tree selector, export
├── tests/              # pytest: tools/, ui/, sdk/, llm/, context/, extensions/
├── examples/           # sdk/, extensions/, agents/ runnable samples
├── Site/               # vite+react website that renders docs/*.md
├── docs/               # these docs (indexed by Site/src/content/docs)
└── pyproject.toml      # hatchling; wheel packages = the four src dirs
```

## Dependency direction

`core` ← `ai` ← `coding_agent` / `tui`. `core` never imports from the other three; `ai` never imports `tui`. The TUI talks to the harness through `ConversationRuntime` and typed events only.

## Entry points

- `vtx = coding_agent.cli:main` — parses flags, then dispatches to `tui.launch.run_tui` or `coding_agent.headless.run_headless`.
- SDK consumers import `from vtx.ai.agent.sdk import Agent, Runner, tool`.

## Why this shape

The pre-split monolith mixed provider plumbing with UI state. The split keeps:

- `core` dependency-free so tools/tests can use message types without an LLM stack;
- all agent logic in one place (`ai.agent`) shared by TUI, headless, sub-agents and the SDK;
- the CLI/config/themes shell thin enough to swap (that's how headless mode exists at all).
