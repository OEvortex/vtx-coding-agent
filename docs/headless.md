# Headless

Headless runs one prompt non-interactively and exits. This page covers how the prompt reaches the agent, what is written to stdout and stderr, the exit codes, and how to script it in CI.

It is the automation surface: `vtx -p "..."` gives you a single turn with no TUI, no approval prompts, and no waiting on a human. For everything the TUI adds, read [cli.md](cli.md); for the flags a provider config can override, read [configuration.md](configuration.md).

## Passing the prompt

The flag takes a value in any of three ways (`src/vtx/coding_agent/cli.py:54-62`):

```python
parser.add_argument(
    "--prompt",
    "-p",
    nargs="?",
    const="-",
    default=None,
    help="Run a single prompt non-interactively, then exit "
    "(omit the value or pipe stdin to read the prompt from stdin)",
)
```

| Form | Behavior |
|---|---|
| `-p "summarize this diff"` | The literal value is the prompt |
| `-p` with no value | `const="-"` marks stdin; Vtx reads stdin to EOF and strips it (`src/vtx/coding_agent/headless.py:41-44`) |
| `echo "..." \\| vtx -p` | Same as above: the flag is present with no value, so stdin is read |

Resolution is exactly three lines, and both stdin paths strip the result (`src/vtx/coding_agent/headless.py:41-44`):

```python
def resolve_prompt(prompt_arg: str, *, stdin: TextIO) -> str:
    if prompt_arg == "-":
        return stdin.read().strip()
    return prompt_arg.strip()
```

Omit `-p` entirely and Vtx starts the TUI instead.

## Flags

| Flag | Meaning |
|---|---|
| `-p`, `--prompt` | The prompt, or omit the value to read stdin |
| `-m`, `--model` | Model id |
| `--provider` | Provider name; one of the keys of `PROVIDER_API_BY_NAME` (`src/vtx/coding_agent/cli.py:52`) |
| `-k`, `--api-key` | API key |
| `-u`, `--base-url` | Base URL for the API |
| `--openai-compat-auth` | `auto` / `required` / `none` for OpenAI-compatible endpoints (`src/vtx/coding_agent/cli.py:63-67`) |
| `--anthropic-compat-auth` | Same three modes for Anthropic-compatible endpoints |
| `--insecure-skip-verify` | Skip TLS verification, e.g. self-signed certs on local providers |
| `-c`, `--continue` | Resume the most recent session. **Not usable with `-p`** |
| `-r`, `--resume` | Resume a specific session ID. **Not usable with `-p`** |
| `-e`, `--extension` | Load a Python extension file or package (repeatable) |
| `--no-extensions` | Skip discovery of `.vtx/extensions/` and `~/.vtx/agent/extensions/` |
| `-a`, `--agent` | Activate a named agent |
| `--agent-file` | Extra agent file paths |
| `--no-agents` | Skip agent discovery |
| `--list-agents` | Print discovered agents and exit |
| `--list-extensions` | Print discovered extensions and exit |
| `--profile` | Plugin profile to boot |
| `--patch` | Extra composition patch file |
| `--dump-config` | Print the composed plugin row list and exit (`src/vtx/coding_agent/cli.py:150-156`) |
| `--version` | Print `vtx <version>` |

`--continue` and `--resume` with `-p` is a hard parser error, not a runtime one (`src/vtx/coding_agent/cli.py:199-200`):

```python
if args.prompt is not None and (args.continue_recent or args.resume_session):
    parser.error("-c/--continue and -r/--resume are not supported with -p/--prompt")
```

## Output

Stream events run through `render_run`, which writes the **final assistant text only**, to stdout, and only when the run stopped cleanly (`src/vtx/coding_agent/headless.py:47-92`):

```python
if stop == StopReason.STOP and final_text:
    print(final_text, file=out)
```

So parsing stdout gets you exactly one model message without any interim reasoning or tool output. Tool progress, errors, warnings, and MCP startup go to stderr:

| Event | Stream | Line |
|---|---|---|
| `ErrorEvent` | stderr | `error: {error}` (`headless.py:72-73`) |
| `ToolApprovalEvent` | stderr | `error: '<tool>' requires approval, denied (non-interactive mode)`, and the future is set to `DENY` (`headless.py:74-79`) |
| `AskUserEvent` | stderr | `error: agent asked a question in non-interactive mode: <questions>`, answered with an empty response (`headless.py:80-86`) |
| MCP startup | stderr | `mcp: N tool(s) from M server(s)` (`headless.py:268`) |

Approvals are auto-**denied** rather than auto-approved, so a tool that needs permission halts that call instead of silently running. Questions the agent asks are answered with an empty response, and the turn runner records a skipped tool result with a hint to the LLM (`src/vtx/coding_agent/headless.py:81-84`).

## Exit codes

| Code | Meaning |
|---|---|
| `0` | `StopReason.STOP`  --  a clean turn |
| `1` | `StopReason.ERROR`, or any stop reason not in the table |
| `3` | `StopReason.LENGTH`  --  hit the output limit |
| `2` | Not a stop reason: an empty prompt, an MCP-independent provider or agent-init failure |

(`src/vtx/coding_agent/headless.py:34-38`, `src/vtx/coding_agent/headless.py:145-147`, `283-285`)

```python
_EXIT_CODES = {StopReason.STOP: 0, StopReason.ERROR: 1, StopReason.LENGTH: 3}

def _exit_code(stop: StopReason) -> int:
    return _EXIT_CODES.get(stop, 1)
```

Distinguish the ones you own from the ones Vtx owns: `0`/`1`/`3` are model outcomes, `2` is your prompt or your config being wrong.

## Permissions during a run

Headless cannot show approval prompts, so it forces `permissions.mode = auto` for the duration of the run only. It rewrites the config, runs, then restores the previous mode in a `finally`  --  the saved file is never left in the auto state and the mode is not persisted (`src/vtx/coding_agent/headless.py:150-157`, `305-312`).

## MCP

MCP connects before the run. A broken server prints `warning: MCP startup failed: ...` on stderr and the run continues (`src/vtx/coding_agent/headless.py:257-262`). Config problems from any server are printed as `warning: MCP config: ...` (`headless.py:269-270`).

Both `~/.vtx/mcp.json` and `<cwd>/.vtx/mcp.json` are read, the same as in a TUI session, and their servers are connected with nothing to grant. There is one OAuth caveat: `signin` is interactive, so a remote server needing a browser must be signed in from the TUI once, after which `~/.vtx/mcp-auth.json` carries the tokens and a scripted run reuses them. See [mcp.md](mcp.md).

## Scripted example

Summarize a diff in CI, with every failure mode handled:

```bash
#!/usr/bin/env bash
set -uo pipefail

DIFF=$(git diff main...HEAD --unified=0)
if [ -z "$DIFF" ]; then
  echo "no changes to review" >&2
  exit 0
fi

PROMPT="Review this diff for correctness bugs only. Reply with a list of
file:line and one sentence each, or 'no issues found'."

set +e
REVIEW=$(printf '%s\n\n%s' "$PROMPT" "$DIFF" | vtx -p -m gpt-4o-mini 2>review.err)
STATUS=$?
set -e

cat review.err >&2          # tool progress, MCP warnings, denial notices

case "$STATUS" in
  0) echo "$REVIEW" >> "$GITHUB_STEP_SUMMARY" ;;
  2) echo "bad prompt or config" >&2; exit 1 ;;
  3) echo "review truncated (output limit); narrow the diff" >&2; exit 1 ;;
  *) echo "agent error: $REVIEW" >&2; exit 1 ;;
esac
```

The same prompt from a value instead of stdin:

```bash
vtx -p "list every TODO comment in src/" --no-extensions
```

Note the redirection order: `2>review.err` keeps stdout clean for capture, which is the property `render_run` guarantees (`src/vtx/coding_agent/headless.py:89-90`).

### Session lifecycle

Headless runs emit `session_start` and `session_end` so YAML hooks and extensions see them like any other run (`src/vtx/coding_agent/headless.py:286-302`). Extensions are loaded before the run and unloaded after, so an extension that registers tools via `-e` is available to the prompt but not baked into the CLI process.

## Known gaps

- Approvals are denied, not queued. There is no way to pre-approve a tool for a headless run except by setting `permissions.mode: auto` in your config beforehand, which then also affects interactive sessions.
- `--continue` and `--resume` are unusable with `-p`, so a headless run cannot resume a TUI session. It always starts a fresh session.
- The `session_start`/`session_end` emission is wrapped in `contextlib.suppress(Exception)` in teardown (`src/vtx/coding_agent/headless.py:296-302`), so a failing hook is invisible to your script  --  it will not change the exit code.
- A denial of an approval is reported on stderr but does not by itself set a nonzero exit: the run continues and ends `STOP`, which is exit `0`. Check stderr, not just the status.
