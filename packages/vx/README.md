# vx

A minimal coding agent built on the [ctx](https://github.com/OEvortex/ctx-coding-agent) harness (`ctx-sdk` + `ctx-agent`) with its own terminal UI. Same agent runtime as ctx — sessions, providers, tools, permissions — in a smaller, keyboard-first package.

Requires Python 3.12+.

## Install and run

```bash
uv tool install vx        # or: pipx install vx / pip install vx

vx                        # terminal UI
vx -p "fix the failing test"   # headless: one prompt, one answer, exit
```

Connect a model with `--provider`/`--model`, or export a provider key (`ANTHROPIC_API_KEY`, etc.) and start. Resume work with `ctx`-style session commands inside the TUI (`/session`, `/tree`, `/handoff`).

## Headless mode

`vx -p <prompt>` (or `vx -p` / `vx -p -` to read the prompt from stdin) runs one turn without the alternate screen: the answer goes to stdout, diagnostics to stderr, so it pipes cleanly. `--verbose` prints each tool call to stderr.

Approvals cannot be answered headlessly — every tool approval is denied and agent questions get an empty response — so run with the permission mode you intend.

Exit codes:

| Code | Meaning |
|---|---|
| 0 | Finished (`StopReason.STOP`) |
| 1 | Error, interrupted, or any other stop reason |
| 2 | Empty prompt, provider error, or agent init failure |
| 3 | Output hit the length limit (`StopReason.LENGTH`) |

## Environment variables

| Variable | Effect |
|---|---|
| `VX_AGENT` | Active agent profile for headless runs (falls back to last selection / config default) |
| `VX_THEME` / `FX_THEME` | Pin the TUI palette by name at startup |
| `VX_BOOT_TRACE` | Path to a file; each boot phase appends a timestamp for startup-latency measurement |
| `VX_DEBUG_FRAME` | Path to a file; every rendered frame is appended as JSON for debugging the TUI |
| `VX_UPDATE_USE_PIP` | Set to force `/update` down the `pip install --upgrade vx` path |

`VTX_PET_SHEET` / `VTX_PETS_DIR` configure the animated-pet graphics prototype (`pet.py`, `demo.py`); they are a development demo, not shipped UI surface.

## Documentation

- [docs/commands.md](docs/commands.md) — every slash command, aliases, and the command router
- [docs/keybindings.md](docs/keybindings.md) — the composer's full key map
- [docs/tools.md](docs/tools.md) — the built-in tool surface
- [docs/swarm.md](docs/swarm.md) — parallel agents on a shared board
- [docs/permissions.md](docs/permissions.md) — ask/auto modes, grants, and gating
- [docs/skills.md](docs/skills.md) — bundled and on-disk skill discovery
- [CHANGELOG.md](CHANGELOG.md) — release notes
