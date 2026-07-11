# Development

Vtx uses [`uv`](https://docs.astral.sh/uv/) for environment and task management.

## Setup

```bash
# Clone and enter the project
git clone <repo> && cd vtx-coding-agent

# Create the environment and install editable (shows "v-editable" in the TUI)
uv venv
source .venv/bin/activate
pip install -e .

# Or with the advanced gateway backend:
pip install -e ".[claw]"
```

When installed editable, `vtx --version` / the TUI shows `v-editable` instead of a release number (detected via the distribution's `direct_url.json`).

## Common commands

```bash
uv run ruff format .                       # format all files
uv run ruff check .                         # lint
uvx ty check .                             # type-check
uv run python -m pytest tests/path/to/test_file.py   # run relevant tests only
uv run python -m pytest                    # full suite (slow; avoid unless asked)
```

Run **only** the tests relevant to your change. The full suite can take a long time.

## Project layout

- `src/vtx/` — the core agent harness (see [architecture.md](architecture.md)).
- `src/vtx_claw/` — the advanced gateway backend (requires `[claw]`).
- `docs/` — documentation (this tree).
- `examples/` — runnable examples (`agents/`, `extensions/`, `sdk/`).
- `tests/` — the test suite.

## Editing docs

After editing or creating any file, run `uv run ruff format .`. Documentation lives in `docs/`; the SDK docs are under `docs/sdk/`.

## Before pushing

Run in parallel (from AGENTS.md): `uv run ruff format .`, `uv run ruff check .`, `uvx ty check .`, and `uv run python -m pytest`. Only push if all pass.
