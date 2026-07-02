# Gateway

The gateway is the persistent background process that runs all channels, the WebUI, and the agent loop. It's the primary way to run vtx-claw.

## Starting the Gateway

```bash
# Foreground (default)
vtx-claw gateway

# Background (detached)
vtx-claw gateway --background

# Custom port
vtx-claw gateway --port 9000

# With verbose logging
vtx-claw gateway --verbose
```

Default port: `18790`

## Instance Isolation

Multiple gateway instances can coexist by computing a deterministic suffix from the workspace and config paths using SHA-1 hashing. Each instance gets:
- Separate state file: `gateway.<hash>.json`
- Separate log file: `gateway.<hash>.log`
- Separate PID tracking

## Process Lifecycle

### Start

1. Forks a new process (POSIX: `start_new_session=True`, Windows: `CREATE_NEW_PROCESS_GROUP`)
2. Writes state JSON: `pid`, `identity`, `started_at`, `port`, `workspace`, `config_path`
3. Begins serving channels + WebUI

### Status

```bash
vtx-claw gateway status
```

Checks if the PID is alive AND the process identity matches (prevents stale state from recycled PIDs).

### Stop

```bash
vtx-claw gateway stop
```

**POSIX**: Sends SIGTERM to the process group, waits for timeout, then SIGKILL.

**Windows**: Sends CTRL_BREAK_EVENT, then `taskkill /T`, then `taskkill /T /F`.

### Restart

```bash
vtx-claw gateway restart
```

Stops the current instance and starts a new one.

## Service Installation

Install the gateway as a system service that starts on boot:

```bash
vtx-claw gateway install-service
```

### Linux (systemd)

Creates `~/.config/systemd/user/vtx_claw-gateway.service`:

```ini
[Unit]
Description=vtx-claw Gateway
After=network-online.target

[Service]
Type=simple
Restart=always
RestartSec=10
NoNewPrivileges=yes
Environment=PYTHONUNBUFFERED=1
ExecStart=/path/to/python -m vtx_claw gateway --foreground

[Install]
WantedBy=default.target
```

Commands: `daemon-reload`, `enable`, `restart`

### macOS (launchd)

Creates `~/Library/LaunchAgents/ai.vtx_claw.gateway.plist`:

- `KeepAlive: {SuccessfulExit: false}` (restart on crash)
- `RunAtLoad` for auto-start
- Separate stdout/stderr logs

Commands: `bootstrap`, `enable`, `kickstart -k`

### Uninstall Service

```bash
vtx-claw gateway uninstall-service
```

## Heartbeat

The gateway runs a periodic heartbeat cron job (default: every 30 minutes) that:

1. Reads `HEARTBEAT.md` from the workspace
2. Sends a heartbeat message to the agent
3. Keeps recent messages in context

Configure in `gateway.heartbeat`:

```json
{
  "gateway": {
    "heartbeat": {
      "enabled": true,
      "interval_s": 1800,
      "keep_recent_messages": 8
    }
  }
}
```

## Logs

Gateway logs are stored at:
- `~/.vtx/claw/logs/gateway.<hash>.log`

View logs:
```bash
vtx-claw gateway logs
```

## Port Management

| Service | Default Port | Config Field |
|---------|-------------|--------------|
| Gateway | 18790 | `gateway.port` |
| WebUI | 8765 | `channels.websocket.port` |
| API Server | 8900 | `api.port` |
| Webhook | 8081 | `channels.telegram.webhook_listen_port` |

## Health Check

The gateway exposes a health endpoint at `http://localhost:<port>/health` when `health_server_enabled` is true (default).
