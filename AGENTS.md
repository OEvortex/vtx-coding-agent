# Agent Guidelines

## Code Style

- Don't add trivial docstrings. Only add docstrings when explaining complex functionality.
- This project uses `uv`. Run `uv run ruff format .` after editing or creating any files.
- If generating and running a Python script, use `uv run python` instead of `python`.

## Testing

- After making changes, run only the tests relevant to those changes using `uv run python -m pytest path/to/test_file.py`
- If the user asks for e2e tests then run the vtx-tmux e2e test if available
- Never run the full test suite unless the user explicitly asks for it. It can take a long time to run and is not always necessary.

## Skills

- Vtx supports registering a skill as a slash command by setting `register_cmd: true` in the SKILL.md frontmatter. If a user asks for a "registered" skill, include this field.

## Committing code

- If the user tells you to commit code, look at all the changes and create multiple commits if needed based on logical groupings
- Follow commit message conventions: `docs:`, `feat:`, `fix:`, `build:`, etc. for the commit prefix

## Pushing

- If the user asks you to push code, run these first before doing so: `uv run ruff format .`, `uv run ruff check .`, `uvx ty check  .` and `uv run python -m pytest` in parallel (same tool call)
- Only if these all pass without issues should you push otherwise report the warnings/errors back to user and ask for next steps

## Codebase Search

Use vortexa (not grep/rg/file reads) to search code or understand a repo. It
indexes the current directory (or pass --root <dir>).

  vortexa resolve "<query>" --plain          # default: matches + tests + callers/callees + deps
  vortexa search "<query>" --hybrid --plain  # ranked hits + per-file graph context
  vortexa explain "<file>:<line>|<symbol>"   # deep dive into a known location

Install: pip install vortexa  (add [full] for tree-sitter AST chunking).
