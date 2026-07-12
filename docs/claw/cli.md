# CLI Reference

The `agenite-claw` CLI is built with Typer. Entry point: `agenite_claw.cli.commands:app`.

```bash
agenite-claw [OPTIONS] COMMAND [ARGS]
```

## Global Options

| Flag | Description |
|------|-------------|
| `--version` / `-v` | Print version and exit |
| `--help` | Show help |

## Commands

### `agenite-claw onboard`

Initialize agenite-claw configuration and workspace.

| Flag | Description |
|------|-------------|
| `--workspace` / `-w` | Workspace directory override |
| `--config` / `-c` | Path to config file |
| `--wizard` | Launch interactive setup wizard |

```bash
# First-time setup with wizard
agenite-claw onboard --wizard

# Non-interactive setup
agenite-claw onboard
```

### `agenite-claw agent`

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
agenite-claw agent -m "Write unit tests for src/app.py"

# Interactive REPL
agenite-claw agent
```

### `agenite-claw serve`

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
agenite-claw serve
# Endpoint: http://127.0.0.1:8900/v1/chat/completions
```

Requires `aiohttp`: `pip install 'agenite-claw[api]'`

### `agenite-claw status`

Show agenite-claw status (config, workspace, model, API keys).

```bash
agenite-claw status
```

---

## Gateway Subcommands

### `agenite-claw gateway`

Start the agenite-claw gateway (all channels + WebUI).

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
agenite-claw gateway

# Background
agenite-claw gateway --background

# Custom port
agenite-claw gateway --port 9000
```

### `agenite-claw gateway status`

Check if the gateway is running.

```bash
agenite-claw gateway status
```

### `agenite-claw gateway stop`

Stop the running gateway.

```bash
agenite-claw gateway stop
```

### `agenite-claw gateway restart`

Restart the gateway.

```bash
agenite-claw gateway restart
```

### `agenite-claw gateway logs`

Show gateway logs.

```bash
agenite-claw gateway logs
```

### `agenite-claw gateway install-service`

Install the gateway as a system service.

| Flag | Description |
|------|-------------|
| `--manager` / `-m` | Service manager: `auto`, `systemd`, `launchd` |
| `--dry-run` | Show what would be created without executing |

```bash
# Auto-detect (systemd on Linux, launchd on macOS)
agenite-claw gateway install-service

# Preview without installing
agenite-claw gateway install-service --dry-run
```

**Linux (systemd):** Creates `~/.config/systemd/user/agenite_claw-gateway.service`

**macOS (launchd):** Creates `~/Library/LaunchAgents/ai.agenite_claw.gateway.plist`

### `agenite-claw gateway uninstall-service`

Remove the installed system service.

```bash
agenite-claw gateway uninstall-service
```

---

## Channel Subcommands

### `agenite-claw channels status`

Show status of all registered channels.

```bash
agenite-claw channels status
```

---

## Provider Subcommands

### `agenite-claw provider login <provider>`

Authenticate with an OAuth provider.

```bash
agenite-claw provider login openai-codex
agenite-claw provider login github-copilot
```

### `agenite-claw provider logout <provider>`

Log out from an OAuth provider.

```bash
agenite-claw provider logout openai-codex
```

---

## Plugin Subcommands

### `agenite-claw plugins list`

List installed plugins.

### `agenite-claw plugins install <path>`

Install a plugin from a local path.
