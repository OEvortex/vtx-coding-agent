# Headless mode

Run one prompt non-interactively — for scripts and CI. Implemented in `src/coding_agent/headless.py`.

## Usage

```bash
vtx -p "Write unit tests for src/ai/agent/tools/task.py"
vtx --prompt                      # read the prompt from stdin
echo "explain this diff" | vtx -p
git diff | vtx -p "review this"
```

`-p` without a value (or with `-`) reads the prompt from stdin. `-p` cannot be combined with `--continue`/`--resume`.

## Output

The agent streams to stdout: assistant text plus one line per tool call/result. Errors go to stderr.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Completed (`stop`) |
| `1` | Error (`error`) |
| `3` | Hit the output-token limit (`length`) |

## Flags

All the usual session flags work:

```bash
vtx -p "summarize the repo" \
    --model gpt-5.5 \
    --provider openai-codex \
    --api-key "$KEY" \
    --agent code-review
```

Extensions, agents (`--agent`, `--agent-file`, `--no-agents`) and extensions flags (`-e`, `--no-extensions`) behave exactly as in TUI mode.
