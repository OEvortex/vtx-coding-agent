# Getting Started with OpenJarvis

This guide walks you through setting up, configuring, and running OpenJarvis.

---

## ⚡ Quick Start

### 1. Prerequisites
OpenJarvis is built into VTX and managed via `uv`. Ensure you have:
- Python 3.10+
- `uv` installed (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
- An LLM API key (e.g. `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, or `OPENROUTER_API_KEY`)

### 2. Running a Single Agent Prompt
Execute a one-off query through the OpenJarvis agent CLI:

```bash
# Run a quick task in the current workspace
uv run vtx jarvis agent "Inspect current git status and list all open issues"

# Run with a specific model override
uv run vtx jarvis agent "Summarize recent project changes" --model openrouter/anthropic/claude-3.7-sonnet
```

### 3. Launching the OpenJarvis Gateway
The Gateway runs the long-lived daemon managing channels, WebSocket connections, HTTP API endpoints, and scheduled cron jobs:

```bash
# Start Gateway in foreground on default port (18789)
uv run vtx jarvis gateway start

# Or specify a custom port
uv run vtx jarvis gateway start --port 19000
```

---

## 🛠️ CLI Commands Reference

OpenJarvis commands are accessible via `vtx jarvis` or `vtx openjarvis`:

### Gateway Management
```bash
# Start the gateway daemon
vtx jarvis gateway start [--port 18789] [--foreground/--background]

# Check gateway status, bound port, active channels, and workspace
vtx jarvis gateway status
```

### Channel Management
```bash
# List all discovered messaging channels and their enabled state
vtx jarvis channels list

# Inspect channel connection health
vtx jarvis channels status
```

### Cron & Task Scheduling
```bash
# List all active scheduled jobs
vtx jarvis cron list

# Add a scheduled prompt (every hour)
vtx jarvis cron add --schedule "0 * * * *" --prompt "Run tests and notify on failures"

# Remove a job
vtx jarvis cron remove <job_id>
```

### Device Pairing & Security
```bash
# Generate a new pairing token for a web/desktop client
vtx jarvis pairing generate --name "MacBook Pro"

# List paired devices
vtx jarvis pairing list

# Revoke a device token
vtx jarvis pairing revoke <device_id>
```

---

## ⚙️ Initial Configuration (`openjarvis.json`)

Configuration is stored at `~/.vtx/openjarvis.json`. If it does not exist, OpenJarvis creates sensible defaults automatically.

Example minimal configuration:

```json
{
  "gateway": {
    "port": 18789,
    "bind": "127.0.0.1",
    "auth_mode": "token",
    "token": "your-secret-token"
  },
  "session": {
    "dm_scope": "per-channel-peer",
    "workspace": "/path/to/workspace"
  },
  "memory": {
    "skills_enabled": true,
    "fts5_enabled": true,
    "vector_enabled": true
  },
  "tools": {
    "image_generation": {
      "enabled": true,
      "provider": "openrouter",
      "model": "openai/gpt-5.4-image-2"
    },
    "my": {
      "enable": true,
      "allow_set": true
    }
  },
  "channels": {
    "telegram": {
      "enabled": true,
      "dm_policy": "pairing",
      "extra": {
        "token": "YOUR_TELEGRAM_BOT_TOKEN"
      }
    }
  }
}
```

