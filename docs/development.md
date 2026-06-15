# Development

This doc covers building, testing, linting, typechecking, and releasing Vtx itself. For the architecture map, see [architecture.md](architecture.md). For the user-facing feature docs, see the [docs index](README.md).

## Prerequisites

- **Python 3.12 or 3.13.** The `pyproject.toml` requires `>=3.12`. The CI matrix runs on 3.12 and 3.13.
- **[uv](https://docs.astral.sh/uv/).** Vtx uses `uv` for dependency management, virtualenvs, and CLI invocation. Install via the [official installer](https://docs.astral.sh/uv/getting-started/installation/).
- **Git.** For the test suite and the e2e harness.
- **ripgrep and fd (optional but recommended).** Vtx shells out to `rg` and `fd` for fast text/file search (via the `find` tool and direct bash use). Without them, the Python fallback is slower but functional. Install via your package manager (`brew install ripgrep fd-find`, `apt install ripgrep fd-find`, etc.).

## Clone and set up

```bash
git clone https://github.com/kuutsav/vtx
cd vtx
uv sync --dev
```

`uv sync --dev` creates a `.venv/`, installs the project in editable mode, and pulls in the dev dependency group (`pytest`, `pytest-asyncio`, `ruff`, `pyright`, `twine`).

If you prefer to install Vtx globally for your own use while keeping a dev checkout for contributions, use `uv tool install .` from inside the checkout (editable install) or `uv tool install vtx-coding-agent` for a stable install.

## Running the test suite

Run the full suite with the dev dependencies active — `pytest-asyncio` is in the dev group, so a bare `pytest` will fail every async test:

```bash
uv run python -m pytest
```

For a focused run (faster, easier to read output):

```bash
# Single test file
uv run python -m pytest tests/tools/test_read.py

# Single test by name
uv run python -m pytest tests/test_config_migration.py::test_migrate_v5_to_v6

# Pattern match
uv run python -m pytest -k "compaction"
```

Async tests are configured in `pyproject.toml` (`asyncio_mode` is implicit in the default config). If you see `async def functions are not natively supported` errors, you ran a `pytest` without the dev dependencies — re-run with `uv run python -m pytest`.

## Linting and formatting

Vtx uses [ruff](https://docs.astral.sh/ruff/) for both. The pre-commit config (`.pre-commit-config.yaml`) pins to `v0.15.16` — match that version locally to avoid churn.

```bash
# Format
uv run ruff format .

# Lint (with --fix for autofixable rules)
uv run ruff check . --fix

# Lint only
uv run ruff check .
```

Style choices that aren't default ruff:

- `line-length = 99` (slightly above 88)
- `quote-style = "double"`, `indent-style = "space"`
- `skip-magic-trailing-comma = true` (no magic trailing comma in formatter)
- per-file ignores:
  - `src/vtx/ui/latex.py`: `RUF001` (ambiguous unicode chars are intentional in LaTeX rendering)
  - `src/vtx/prompts/identity.py`, `src/vtx/prompts/env.py`: `E501` (line-length — these are prompt strings that read better as full sentences)

Always run `uv run ruff format .` after editing or creating files — it's also called out in `AGENTS.md`.

## Typechecking

Vtx uses `pyright` (in the dev group) for static type checking:

```bash
uvx ty check .
```

The project passes with no `type: ignore` comments — the codebase was modernized to remove them in 0.4.0 (commit `d2220ee`). When you add new code, don't reintroduce them; instead, narrow the type or use `typing.cast` if the underlying type really is wider.

## End-to-end tests

The tmux-based e2e harness lives in `.agents/skills/vtx-tmux-test/`. The coverage state and recommended additions are in [e2e-test-coverage-review.md](e2e-test-coverage-review.md).

The harness launches Vtx in a detached tmux session, drives it with `tmux send-keys`, and captures pane output to `/tmp/vtx-test-*.txt`. To run it locally:

```bash
# Make sure tmux is installed
which tmux

# Run the script directly
./.agents/skills/vtx-tmux-test/run-e2e-tests.sh

# Or with vtx-tmux-test as a registered slash command
# (open a Vtx session and type /vtx-tmux-test)
```

The current harness uses the real user config under `~/.vtx/`, which is risky for tests that mutate runtime settings. If you're adding new tests, isolate the user environment first by overriding `HOME` to a temp directory.

## Building a wheel

```bash
uv build
```

This produces `dist/vtx_coding_agent-<version>-py3-none-any.whl` and the matching `.tar.gz`. The `pyproject.toml` uses `hatchling` as the build backend; the wheel layout is just the `src/vtx/` package plus the bundled `defaults/` and `builtin_skills/`.

## Release process

The release process is run by the maintainer (`@kuutsav`). The general flow:

1. **Bump the version** in `src/vtx/version.py` (and the `pyproject.toml` `[project]` block — these must match).
2. **Update the changelog.** Add a new dated section to `CHANGELOG.md` with all the user-facing changes since the last release, in `Added` / `Changed` / `Fixed` / `Docs / Tests` buckets.
3. **Run the full pre-push checks:**

   ```bash
   uv run ruff format .
   uv run ruff check .
   uvx ty check .
   uv run python -m pytest
   ```

   All four must pass cleanly. Fix anything that doesn't.
4. **Build and publish:**

   ```bash
   uv build
   uv publish   # uses the PYPI_TOKEN env var; the workflow has this
   ```

5. **Tag the release:**

   ```bash
   git tag -a v0.4.2 -m "v0.4.2"
   git push origin v0.4.2
   ```

6. **Draft a GitHub release** with the changelog excerpt. The in-app update notice polls PyPI and will start showing the new version within the cache window (10 minutes) of the publish.

If a release breaks something, the standard fix is to publish a patch version (e.g. `0.4.2` → `0.4.3`) with a focused fix. Don't yank — PyPI yanks are rare and disruptive.

## CI

The CI workflow (`.github/workflows/ci.yml`) runs:

- `uv run ruff check .`
- `uv run ruff format --check .`
- `uvx ty check .`
- `uv run python -m pytest`

On Python 3.12 and 3.13, on Linux and macOS (and Windows for the paths-related tests, with skips for things that don't translate).

PRs are expected to keep CI green. If a test fails on CI but not locally, the most common cause is platform-specific behavior — check the OS, the Python version, and the working directory the test uses.

## Adding a new dependency

Runtime dependency:

1. Add to `[project]` `dependencies` in `pyproject.toml`. Pin with `>=` and a minor version: `requests>=2.32.0`. Avoid `==` pins for non-trivial libs.
2. `uv sync` to update the lockfile.
3. Use the import in code.
4. Add a test that exercises the new code path.
5. Update the relevant doc (README, [configuration.md](configuration.md), [providers.md](providers.md), etc.) if the new dependency is user-facing.

Dev-only dependency:

1. Add to `[dependency-groups] dev` in `pyproject.toml`.
2. `uv sync --dev`.
3. Same follow-up steps minus the doc update.

## Debugging

### In-process debugging

The runtime config is a `ContextVar`. For tests and repl work:

```python
from vtx import config, get_config, reload_config

# Read the loaded config
print(get_config().llm.default_model)

# Reload from disk
reload_config()

# Swap with a custom config (tests)
from vtx import set_config
set_config(my_test_config)
```

### The Textual app

Run Vtx with `TEXTUAL_DEBUG=1` to enable the Textual dev console and CSS hot-reload:

```bash
TEXTUAL_DEBUG=1 vtx
```

Press `Ctrl+\` to open the dev console.

### Async issues

If a test hangs, it's almost always a missing `await` or an unawaited task. The pattern for tool cancellation is in `_tool_utils.communicate_or_cancel` — copy that wrapper.

### Provider issues

For provider debugging, set the log level:

```bash
VTX_LOG_LEVEL=debug vtx
```

(Or whatever the current env var is — check `update_check.py` or the OAuth modules if you need a reference point.) Provider errors are normalized in the `sanitize.py` provider module — empty upstream errors get a readable fallback.

## Code style

From `AGENTS.md`:

- **No trivial docstrings.** Only add docstrings when explaining non-obvious behavior. The standard pydantic / class-header docstring on a tool is fine; an "initialize the user" docstring on a `__init__` is not.
- **`uv run` for Python scripts.** Don't use `python` directly — the venv is managed by uv.
- **Multiple commits per logical change.** When the user asks to commit, group by logical concern (e.g. one commit for the tool, one for the test, one for the doc).
- **Conventional commit prefixes:** `feat:`, `fix:`, `build:`, `docs:`, `test:`, `refactor:`, `chore:`.

## Repository conventions

- **`src/vtx/`** is the package layout. `pyproject.toml`'s `[tool.hatch.build.targets.wheel]` lists `packages = ["src/vtx"]`.
- **One module per tool/provider** under `tools/` and `llm/providers/`. Don't combine related ones into a single file.
- **Tests mirror the source tree.** `tests/tools/` covers `src/vtx/tools/`, `tests/llm/` covers `src/vtx/llm/`, `tests/ui/` covers `src/vtx/ui/`, `tests/core/` covers `src/vtx/core/`. Add a new test file next to the existing ones for the module you changed.
- **Don't push without running the pre-push checks.** The pre-push steps in `AGENTS.md` are the contract; maintainers will ask you to re-run them if you skip them.

## Where to ask questions

- **GitHub Issues** for bugs and feature requests.
- **GitHub Discussions** for design questions and "how do I..." threads.

For code questions, link to the file and line range rather than pasting the code — it's easier to keep the conversation in sync.
