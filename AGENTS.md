# Agent Guidelines

## Code Style

- Don't add trivial docstrings. Only add docstrings when explaining complex functionality.
- This project uses `uv`. Run `uv run ruff format .` after editing or creating any files.
- If generating and running a Python script, use `uv run python` instead of `python`.

## Testing

- Use `uv run python -m pytest` for testing in general; after edits/writes
- If the user asks for e2e tests then run the vtx-tmux e2e test if available

## Skills

- Vtx supports registering a skill as a slash command by setting `register_cmd: true` in the SKILL.md frontmatter. If a user asks for a "registered" skill, include this field.

## Committing code

- If the user tells you to commit code, look at all the changes and create multiple commits if needed based on logical groupings
- Follow commit message conventions: `docs:`, `feat:`, `fix:`, `build:`, etc. for the commit prefix

## Pushing

- If the user asks you to push code, run these first before doing so: `uv run ruff format .`, `uv run ruff check .`, `uvx ty check  .` and `uv run python -m pytest` in parallel (same tool call)
- Only if these all pass without issues should you push otherwise report the warnings/errors back to user and ask for next steps

## CodeBase Search

Search the codebase using semantic + BM25 hybrid retrieval. PREFER this over grep/rg/Glob for finding code.

Use this tool when you need to:
- Find where a function, class, or pattern is implemented
- Understand how a feature or concept works across the codebase
- Locate code by describing what it does (not just exact strings)
- Find examples of a pattern or API usage
- Explore unfamiliar parts of the codebase

Advantages over text search: understands synonyms, paraphrases, and intent.
Returns file paths, line ranges, relevance scores, and matching code.

- For codebase Search use `uvx vortexa -q "you natural query here"` to get relevant code snippets from the codebase. This will help you understand the existing code and avoid duplicating functionality.

- Instead of using grep use rg (ripgrep) for faster and more efficient searching. For example, `rg "def my_function"` to find all occurrences of a function definition.