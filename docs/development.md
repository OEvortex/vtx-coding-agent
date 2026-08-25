# Development

## Setup

```bash
git clone https://github.com/OEvortex/vtx-coding-agent
cd vtx-coding-agent
uv sync              # creates .venv and installs everything, incl. dev deps
```

Python 3.12+. The project uses [uv](https://docs.astral.sh/uv/) with hatchling; the wheel packages are the four dirs under `src/`.

## Layout

```
src/
  ai/             # LLM providers + agent harness (ai.agent), SDK (ai.agent.sdk)
  coding_agent/   # CLI entry (coding_agent.cli:main), config, headless, themes
  core/           # types, events, permissions, compaction, tracing — no internal deps
  tui/            # Textual UI
tests/            # pytest suite mirroring src (tools/, ui/, sdk/, llm/, context/, extensions/)
examples/         # runnable examples: sdk/, extensions/, agents/
Site/             # marketing/docs website (Vite + React) that renders docs/*.md
scripts/          # install.sh / install.ps1, show_themes.py
.agents/skills/   # repo skills incl. the tmux e2e harness
```

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
- Keep the system prompt lean — prompt text lives in `src/ai/agent/prompts/identity.py`; token budget matters.
- `core` must not import from `ai`/`tui`/`coding_agent`; keep dependency direction one-way (`architecture.md`).
- Config schema changes need a new migration in `src/coding_agent/config.py` (`_migrate_vN_to_vN+1`) and a bump of `meta.config_version` in `defaults/config.yml`.
- Docs in `docs/*.md` are rendered by the website (`Site/src/content/docs/index.ts` indexes them) and linked from the README — update both when adding pages.

## E2E testing

The tmux harness lives in `.agents/skills/vtx-tmux-test/` — see [e2e-test-coverage-review.md](e2e-test-coverage-review.md).

## Releasing

Version lives in `pyproject.toml` (`vtx-coding-agent` on PyPI). Update `CHANGELOG.md`, tag, build with `uv build`, publish with `twine` (dev dep). `vtx update` self-updates end users via uv/pip.
