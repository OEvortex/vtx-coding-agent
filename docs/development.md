# Development

## Setup

```bash
git clone https://github.com/OEvortex/vtx-coding-agent
cd vtx-coding-agent
uv sync              # creates .venv and installs everything, incl. dev deps
```

Python 3.12+. The project uses [uv](https://docs.astral.sh/uv/) with hatchling; the wheel packages the ten dirs under `src/vtx/`.

## Layout

```
src/vtx/
  protocol/       # LEAF: message/stream types, tool + provider contracts, error formatting
  telemetry/      # LEAF: tracing spans, processors, console + JSONL exporters
  git/            # gh CLI wrapper, GitHub App auth, branch metadata
  core/           # events, permissions, notifications, compaction, config + defaults,
                  #   themes, harness knobs, paths, version
  ai/             # LLM layer only: providers, OAuth, SDK adapters, model catalog
  codemode/       # confined script execution: sandbox, isolation, discovery
  agent/          # harness: loop, turn, session, runtime, tools, prompts, context,
                  #   extensions, hooks, goals, SDK
  mcp/            # MCP client: config, transports, exposure, OAuth, trust
  tui/            # Textual UI
  coding_agent/   # CLI entry (coding_agent.cli:main), headless, concrete fs tools
tests/            # pytest suite mirroring src (tools/, ui/, sdk/, llm/, context/, extensions/, core/, mcp/)
examples/         # runnable examples: sdk/, extensions/, agents/
scripts/          # install.sh / install.ps1, show_themes.py
.agents/skills/   # repo skills incl. the tmux e2e harness
```

Layering is one-way; see [architecture.md](architecture.md) for the dependency DAG.

## Everyday commands

```bash
uv run ruff format .            # format (run after every edit)
uv run ruff check .             # lint
uvx ty check .                  # type check (config in ty.toml)
uv run python -m pytest tests/test_permissions.py   # targeted tests
uv run vtx                      # run your checkout
```

Run only the tests relevant to your change; the full suite is slow.

## Conventions

- Commit prefixes: `feat:`, `fix:`, `docs:`, `refactor:`, `chore:`…
- Keep the system prompt lean — prompt text lives in `src/vtx/agent/prompts/identity.py`; token budget matters.
- `core` must not import from `ai`/`tui`/`coding_agent`; keep dependency direction one-way (`architecture.md`).
- Config schema changes need a new migration in `src/coding_agent/config.py` (`_migrate_vN_to_vN+1`) and a bump of `meta.config_version` in `defaults/config.yml`.
- New docs in `docs/*.md` are linked from the README — update the README index when adding pages.

## E2E testing

The tmux harness lives in `.agents/skills/vtx-tmux-test/` — see [e2e-test-coverage-review.md](e2e-test-coverage-review.md).

## Releasing

Version lives in `pyproject.toml` (`vtx-coding-agent` on PyPI). Update `CHANGELOG.md`, tag, build with `uv build`, publish with `twine` (dev dep). `vtx update` self-updates end users via uv/pip.
