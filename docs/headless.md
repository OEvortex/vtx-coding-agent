# Headless mode

`vtx -p` (or `vtx --prompt`) runs a single prompt non-interactively and exits. It's the right mode for scripts, CI, git hooks, and any time you want the agent to run unattended.

## CLI surface

```text
vtx -p "summarize this module"
vtx --prompt "summarize this module"

# Read the prompt from stdin
cat task.md | vtx -p
echo "fix the failing test" | vtx --prompt -

# Override model and provider for this run only
vtx -p "fix the lint" --provider deepseek --model deepseek-v4-flash
vtx -p "explain this" --base-url http://localhost:5000/v1 --openai-compat-auth none
```

The `-p` flag accepts an optional argument. If you pass `-p` with no value, or `-p -`, Vtx reads the prompt from stdin. The leading space in `vtx -p "..."` is fine; the prompt is taken verbatim.

## What you can and can't combine

| Flag | Works with `-p`? | Notes |
| --- | --- | --- |
| `--model` / `-m` | yes | Per-run model override. |
| `--provider` | yes | Per-run provider override. |
| `--api-key` / `-k` | yes | Per-run API key. |
| `--base-url` / `-u` | yes | Per-run endpoint override. |
| `--openai-compat-auth` | yes | Per-run auth mode for OpenAI-compatible endpoints. |
| `--anthropic-compat-auth` | yes | Per-run auth mode for Anthropic-compatible endpoints. |
| `--insecure-skip-verify` | yes | Skip TLS verification for the run. |
| `--continue` / `-c` | **no** | Refuses with an argparse error. Headless mode doesn't have a session to continue. |
| `--resume` / `-r` | **no** | Same — no session restore in headless. |
| `--version` | yes | Just prints the version and exits, prompt is ignored. |

The incompatibility between `-p` and `-c`/`-r` is enforced in `cli.py` with `parser.error(...)` so you get a clean usage message rather than a runtime surprise.

## Behavior

- The full TUI is skipped. There's no session picker, no approval prompts, no streaming markdown rendering.
- Tools run **auto-approved** (the in-memory `permissions.mode` is forced to `"auto"` for the duration of the run, then restored). This is enforced because headless can't show a prompt.
- The agent loop runs to completion or until `agent.max_turns` is hit, whichever comes first.
- The final assistant text is printed to **stdout** on a clean finish.
- Errors and warnings are written to **stderr**.
- Tool results are still computed (so `write` actually writes, `bash` actually runs, etc.) but their UI rendering is suppressed.
- Approval events are auto-denied with a stderr message: `error: '<tool>' requires approval, denied (non-interactive mode)`.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Completed successfully (`StopReason.STOP`). The final response was printed to stdout. |
| `1` | Generic error during the run (`StopReason.ERROR`, unhandled exception, provider error). Details on stderr. |
| `2` | Startup error — empty prompt, provider/init failure, or model not configured. |
| `3` | Hit the `agent.max_turns` cap before completing. The partial transcript is on stderr. |

The mapping is defined in [`src/vtx/headless.py`](../src/vtx/headless.py) as:

```python
_EXIT_CODES = {
    StopReason.STOP: 0,
    StopReason.ERROR: 1,
    StopReason.LENGTH: 3,
}
```

Everything else maps to `1` (the safe default).

## stdout vs stderr

The contract is:

- **stdout** is the final assistant response. Capture it with `vtx -p "..." > out.txt`.
- **stderr** is everything else: errors, warnings, partial transcripts, the "approval denied" message. Capture it with `vtx -p "..." 2> err.txt` if you need to debug.

This means a healthy headless run can be piped to anything that consumes plain text:

```bash
# Save the response
vtx -p "summarize src/vtx/loop.py" > summary.txt

# Feed it into another tool
vtx -p "explain this error" < err.log | pbcopy

# Chain prompts
vtx -p "list the failing tests" | xargs -I {} vtx -p "fix test: {}"
```

## Config and overrides

Headless mode reads the same `~/.vtx/config.yml` as the TUI. CLI flags override config for the run. There's no in-process config write — your config is not modified by a headless run.

Things that are explicitly handled:

- **Config migration** runs as usual; if your config is at v5 and the build expects v6, it gets migrated with a backup, exactly like the TUI.
- **Auth files** are read from the same locations. The headless runner does not pre-flight OAuth.
- **Dynamic provider caches** are read; `/model refresh` is not exposed in headless mode (use the TUI to refresh, or call `vtx.llm.refresh_provider(name)` programmatically).

## Network and timeouts

- `request_timeout_seconds` from config applies.
- `tool_call_idle_timeout_seconds` applies per tool call.
- The agent loop has no global wall-clock cap beyond the LLM request timeout — a `bash` tool call can run for up to its own `timeout` parameter (default 180s).

If you're running Vtx headless behind a CI step, give the CI step a generous timeout. A long `agent.max_turns` plus slow tool calls can run for many minutes.

## Limitations vs. the TUI

| Feature | TUI | Headless |
| --- | --- | --- |
| Interactive prompts | yes | no (auto-allowed) |
| Slash commands | yes | no |
| Session persistence | yes | no |
| Compaction | yes | yes |
| `/export` | yes | no (session not created) |
| Streaming Markdown | yes | no (plain text final response) |
| Approval popup | yes | denied |
| Audio notifications | yes | no |

## When to use headless

- **CI lint-fix / test-fix bot**: `vtx -p "fix the lint errors"`.
- **One-off analysis**: `vtx -p "what does this function do" < module.py`.
- **Repo-wide audit**: `vtx -p "find any TODO comments mentioning performance"`.
- **Quick code review**: pipe the diff in: `git diff | vtx -p "review this diff"`.

For anything that needs multi-turn interaction, `/model` switching, or session persistence, use the TUI.
