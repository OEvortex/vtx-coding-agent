# Agent Guidelines

## Code Style

- Don't add trivial docstrings. Only add docstrings when explaining complex functionality.
- This project uses `uv`. Run `uv run ruff format .` after editing or creating any files.
- If generating and running a Python script, use `uv run python` instead of `python`.

## Testing

- If the user asks for e2e tests then run the vtx-tmux e2e test if available
- Never run the full test suite unless the user explicitly asks for it. It is slow and resource intensive.
- Test only those files that are relevant to the changes made. If you are unsure, ask the user for clarification.
- Don't run tests that are not relevant to the changes made. If you are unsure, ask the user for clarification.

## Skills

- Vtx supports registering a skill as a slash command by setting `register_cmd: true` in the SKILL.md frontmatter. If a user asks for a "registered" skill, include this field.

## Committing code

- If the user tells you to commit code, look at all the changes and create multiple commits if needed based on logical groupings
- Follow commit message conventions: `docs:`, `feat:`, `fix:`, `build:`, etc. for the commit prefix

## Pushing

- If the user asks to push code, run these checks first, in this order, one at a time:
  1. `uv run ruff format .`
  2. `uv run ruff check .`
  3. `uvx ty check .`
  4. `uv run python -m pytest <paths for the test files relevant to the staged changes>`
- Never run the full test suite as part of a push check. Target only the test files that cover what changed; if you cannot tell which tests apply, ask the user.
- If any step fails, stop and report the warnings/errors back to the user, then ask for next steps. Only push when every step passes without issues.

## Codebase Search

Use vortexa in bash (instead of repo-wide grep/rg) to search code or understand a repo. It
indexes the current directory (or pass --root <dir>).
Vortexa is super fast and accurate at code searching and finding relevant context. It can be used to find code, understand code, and explore code relationships so prefer using it.

  vortexa resolve "<query>" --plain          # default: matches + tests + callers/callees + deps
  vortexa search "<query>" --hybrid --plain  # ranked hits + per-file graph context
  vortexa explain "<file>:<line>|<symbol>"   # deep dive into a known location

Use vortexa to find code, understand code, and explore code relationships so prefer using it over grep/rg to find exact code files and lines and then use rg to search within those files if needed and read necessary files to understand the code. Vortexa is super fast and accurate at code searching and finding relevant context.