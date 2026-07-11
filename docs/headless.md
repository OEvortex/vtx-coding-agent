# Headless mode

Run Vtx non-interactively from the command line or a script — no TUI. This is ideal for CI, automation, and piping prompts via stdin.

## Basic usage

```bash
# Single prompt, then exit
vtx -p "Write unit tests for src/vtx/utils.py"

# Read the prompt from stdin
echo "Explain the auth flow" | vtx -p

# Resume a session
vtx -c              # most recent session for the cwd
vtx -r <id>         # specific session by id or id prefix
```

## Flags

| Flag | Meaning |
|------|---------|
| `-p, --prompt [PROMPT]` | Run a single prompt non-interactively, then exit (omit the value or pipe stdin to read from stdin) |
| `-m, --model MODEL` | Model to use |
| `--provider PROVIDER` | Provider slug |
| `-k, --api-key API_KEY` | API key |
| `-u, --base-url BASE_URL` | API base URL |
| `--openai-compat-auth {auto,required,none}` | OpenAI-compat auth mode |
| `--anthropic-compat-auth {auto,required,none}` | Anthropic-compat auth mode |
| `--insecure-skip-verify` | Skip TLS verification (local self-signed certs) |
| `--goal OBJECTIVE` | Enter goal mode with the given completion condition (see [goal.md](goal.md)) |
| `-c, --continue` | Resume the most recent session |
| `-r, --resume ID` | Resume a specific session |
| `--version` | Print the version and exit |

## Behavior notes

- Headless mode cannot show approval prompts, so it temporarily forces `permissions.mode: auto` for the run only — the saved config is not modified.
- Exit codes: `0` on success, `2` on empty prompt, and a non-zero code derived from the agent's stop reason otherwise.

## Programmatic headless runs

For richer control (streaming, custom tools, structured output), use the [SDK runner](sdk/runner.md) instead of the CLI.
