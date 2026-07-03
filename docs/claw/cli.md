# CLI Reference

The `vtx-claw` CLI is built with Typer. Entry point: `vtx_claw.cli.commands:app`.

```bash
vtx-claw [OPTIONS] COMMAND [ARGS]
```

## Global Options

| Flag | Description |
|------|-------------|
| `--version` / `-v` | Print version and exit |
| `--help` | Show help |

## Commands

### `vtx-claw onboard`

Initialize vtx-claw configuration and workspace.

| Flag | Description |
|------|-------------|
| `--workspace` / `-w` | Workspace directory override |
| `--config` / `-c` | Path to config file |
| `--wizard` | Launch interactive setup wizard |

```bash
# First-time setup with wizard
vtx-claw onboard --wizard

# Non-interactive setup
vtx-claw onboard
```

### `vtx-claw agent`

Interact with the agent directly from the CLI.

| Flag | Description |
|------|-------------|
| `--message` / `-m` | Single message (run once and exit) |
| `--session` / `-s` | Session ID (default: `cli:direct`) |
| `--workspace` / `-w` | Workspace directory |
| `--config` / `-c` | Config file path |
| `--markdown` / `--no-markdown` | Render output as Markdown (default: yes) |
| `--logs` / `--no-logs` | Show runtime logs (default: no) |

```bash
# One-shot mode
vtx-claw agent -m "Write unit tests for src/app.py"

# Interactive REPL
vtx-claw agent
```

### `vtx-claw serve`

Start the OpenAI-compatible API server (`/v1/chat/completions`).

| Flag | Description |
|------|-------------|
| `--port` / `-p` | API server port (default: 8900) |
| `--host` / `-H` | Bind address (default: `127.0.0.1`) |
| `--timeout` / `-t` | Per-request timeout in seconds (default: 120) |
| `--verbose` / `-v` | Show runtime logs |
| `--workspace` / `-w` | Workspace directory |
| `--config` / `-c` | Config file path |

```bash
vtx-claw serve
# Endpoint: http://127.0.0.1:8900/v1/chat/completions
```

Requires `aiohttp`: `pip install 'vtx-claw[api]'`

### `vtx-claw status`

Show vtx-claw status (config, workspace, model, API keys).

```bash
vtx-claw status
```

---

## Gateway Subcommands

### `vtx-claw gateway`

Start the vtx-claw gateway (all channels + WebUI).

| Flag | Description |
|------|-------------|
| `--port` / `-p` | Gateway port (default: 18790) |
| `--workspace` / `-w` | Workspace directory |
| `--config` / `-c` | Config file path |
| `--verbose` / `-v` | Verbose output |
| `--foreground` | Run in foreground (default) |
| `--background` | Start as background process |

```bash
# Foreground (default)
vtx-claw gateway

# Background
vtx-claw gateway --background

# Custom port
vtx-claw gateway --port 9000
```

### `vtx-claw gateway status`

Check if the gateway is running.

```bash
vtx-claw gateway status
```

### `vtx-claw gateway stop`

Stop the running gateway.

```bash
vtx-claw gateway stop
```

### `vtx-claw gateway restart`

Restart the gateway.

```bash
vtx-claw gateway restart
```

### `vtx-claw gateway logs`

Show gateway logs.

```bash
vtx-claw gateway logs
```

### `vtx-claw gateway install-service`

Install the gateway as a system service.

| Flag | Description |
|------|-------------|
| `--manager` / `-m` | Service manager: `auto`, `systemd`, `launchd` |
| `--dry-run` | Show what would be created without executing |

```bash
# Auto-detect (systemd on Linux, launchd on macOS)
vtx-claw gateway install-service

# Preview without installing
vtx-claw gateway install-service --dry-run
```

**Linux (systemd):** Creates `~/.config/systemd/user/vtx_claw-gateway.service`

**macOS (launchd):** Creates `~/Library/LaunchAgents/ai.vtx_claw.gateway.plist`

### `vtx-claw gateway uninstall-service`

Remove the installed system service.

```bash
vtx-claw gateway uninstall-service
```

---

## Channel Subcommands

### `vtx-claw channels status`

Show status of all registered channels.

```bash
vtx-claw channels status
```

---

## Provider Subcommands

### `vtx-claw provider login <provider>`

Authenticate with an OAuth provider.

```bash
vtx-claw provider login openai-codex
vtx-claw provider login github-copilot
```

### `vtx-claw provider logout <provider>`

Log out from an OAuth provider.

```bash
vtx-claw provider logout openai-codex
```

---

## Plugin Subcommands

### `vtx-claw plugins list`

List installed plugins.

### `vtx-claw plugins install <path>`

Install a plugin from a local path.
