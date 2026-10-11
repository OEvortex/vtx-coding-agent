# Quickstart

Vtx runs in your terminal and works with files on your machine. You need access to a
model through a supported provider: an API key, an OAuth subscription, or a local
model.

## 1. Install Vtx

Vtx is distributed on PyPI as `vtx-coding-agent` and installs as the `vtx` command.

```bash
uv tool install vtx-coding-agent
```

Verify the installation:

```bash
vtx --version
```

If you installed from a source checkout instead, use the editable install described in
[development.md](development.md).

## 2. Connect a model

Vtx needs a provider before it can answer anything. The shipped catalog has 62 providers;
`--provider` accepts 16 of their API-family slugs, and dropping a YAML file into
`~/.vtx/providers/` adds your own. See [providers.md](providers.md) for the full list and
each one's auth method.

```bash
vtx --provider anthropic --model claude-sonnet-4-5
```

Put the credential in the environment rather than the command line, so it does not
land in your shell history:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
vtx
```

For a provider that takes an API key ad hoc, pass it once:

```bash
vtx --provider openrouter --model anthropic/claude-sonnet-4.5 --api-key sk-or-...
```

To use a local model, point Vtx at the endpoint:

```bash
vtx --provider ollama --model qwen2.5-coder --base-url http://localhost:11434
```

## 3. Start Vtx

Change to the folder you want Vtx to work with, then start it:

```bash
cd /path/to/your/project
vtx
```

The status line names the active model and provider. If a project has an `AGENTS.md`,
Vtx reads it as project instructions before your first turn.

## 4. Do a first task

Ask for something concrete. Vtx reads files before it edits them, and it will ask you
before it runs a mutating tool unless you have changed the permission mode.

```text
Add a --dry-run flag to the deploy script and update its usage text.
```

Watch what happens: Vtx calls `read` on the script, then `edit`, then reports the
change. Every tool call is visible in the transcript, so you can see exactly what it
did and why.

Interrupt a turn with `Esc`. If it is waiting on a permission prompt, answering the
prompt is enough.

## 5. Let Vtx extend itself

Vtx ships a skill that teaches the model which extension surface to reach for when it
lacks a capability. Ask for it by name:

```text
/vtx-self-extend: give yourself a command that runs the test suite
```

That skill picks the smallest sufficient surface - a skill for instructions, a plugin
row for real behavior - and mounts it. It deliberately will not upgrade the Vtx
package itself; that stays a user action through `/update`. Read
[skills.md](skills.md) and [plugins.md](plugins.md) for the authoring contracts.

## 6. Resume and branch

Every session is durable. Resume the most recent one with `-c`, or a specific one by
id:

```bash
vtx -c
vtx -r 3f2a1b
```

Fork a session in the TUI to explore a different approach without losing the original.
See [sessions.md](sessions.md).

## Choose how to customize Vtx

| You want to | Use |
|---|---|
| A reusable procedure or checklist | A [skill](skills.md) |
| A new tool or slash command | A [plugin row](plugins.md) |
| To change what the model is told | A prompt section in a plugin row, or `AGENTS.md` |
| To swap a core seam (tools, agent loop, prompts) | A plugin row replacing that row id |
| A different model provider | [Providers](providers.md) config |
| A different look | [Theming](theming.md) |

## Where to go next

- [Tools](tools.md) - the complete, schema-accurate reference for what the model can
  call. Start here if you are evaluating Vtx.
- [How Vtx works](how-vtx-works.md) - the composition model and the agent loop.
- [Headless](headless.md) - the one-prompt-in, one-exit-code-out mode for CI.
