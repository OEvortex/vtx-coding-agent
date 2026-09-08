# Configuration Reference

OpenJarvis stores its primary configuration in `~/.vtx/openjarvis.json`. You can also configure parameters via environment variables or runtime commands.

---

## 📄 Complete Configuration Schema

Below is an annotated example of all supported top-level sections in `openjarvis.json`:

```json
{
  "gateway": {
    "port": 18789,
    "bind": "loopback",
    "auth_mode": "token",
    "token": "your-auth-token-or-null",
    "verbose": false
  },
  "session": {
    "dm_scope": "per-channel-peer",
    "workspace": null
  },
  "memory": {
    "skills_enabled": true,
    "fts5_enabled": true,
    "honcho_enabled": false,
    "vector_enabled": true
  },
  "cron": {
    "enabled": true,
    "max_jobs": 100
  },
  "channels_defaults": {
    "group_policy": "allowlist",
    "heartbeat": true
  },
  "channels": {
    "telegram": {
      "enabled": true,
      "dm_policy": "pairing",
      "allow_from": ["12345678"],
      "group_policy": "allowlist",
      "group_allow_from": ["-100123456789"],
      "extra": {
        "token": "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
      }
    },
    "discord": {
      "enabled": false,
      "dm_policy": "allowlist",
      "allow_from": [],
      "extra": {
        "token": "DISCORD_BOT_TOKEN"
      }
    },
    "slack": {
      "enabled": false,
      "dm_policy": "open",
      "extra": {
        "bot_token": "xoxb-...",
        "app_token": "xapp-..."
      }
    }
  },
  "tools": {
    "restrict_to_workspace": false,
    "exec": {
      "timeout": 120,
      "sandbox": ""
    },
    "file": {
      "max_read_chars": 100000
    },
    "web": {
      "enable": true
    },
    "my": {
      "enable": true,
      "allow_set": true
    },
    "image_generation": {
      "enabled": true,
      "provider": "openrouter",
      "model": "openai/gpt-5.4-image-2",
      "default_aspect_ratio": "1:1",
      "default_image_size": "1K",
      "max_images_per_turn": 4,
      "save_dir": "generated"
    },
    "cli_apps": {
      "enabled": true,
      "timeout_seconds": 60
    }
  },
  "mcp_servers": {
    "filesystem_server": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/share"],
      "env": {}
    }
  },
  "workspace": "/home/vortex/projects",
  "model": "claude-3-7-sonnet-latest",
  "model_provider": "anthropic"
}
```

---

## ⚙️ Section Details

### 1. `gateway`
Configures the unified WebSocket/HTTP server.
- `port` (*integer*, default: `18789`): Port to bind.
- `bind` (*string*, default: `"loopback"`): Network interface: `"loopback"` (`127.0.0.1`), `"0.0.0.0"`, or `"auto"`.
- `auth_mode` (*string*, default: `"token"`): `"token"`, `"password"`, `"none"`, or `"trusted-proxy"`.
- `token` (*string | null*): Secret authorization token required in headers or WebSocket connection query.
- `verbose` (*boolean*, default: `false`): Enable verbose network logging.

### 2. `session`
Controls how conversation state is isolated across users and channels.
- `dm_scope` (*string*, default: `"per-channel-peer"`):
  - `"main"`: Single global conversation session shared across all channels.
  - `"per-peer"`: One session per individual user regardless of the platform.
  - `"per-channel-peer"`: Unique session per user per channel (e.g. `telegram:98765`).
  - `"per-account-channel-peer"`: Isolates by workspace, account, channel, and user.
- `workspace` (*string | null*): Explicit workspace directory override for session files.

### 3. `memory`
Controls conversational retrieval systems.
- `skills_enabled` (*boolean*, default: `true`): Discover and execute slash-command skills.
- `fts5_enabled` (*boolean*, default: `true`): Enable SQLite FTS5 full-text search indexing.
- `vector_enabled` (*boolean*, default: `true`): Enable vector embeddings search.
- `honcho_enabled` (*boolean*, default: `false`): Enable external Honcho state synchronizer.

### 4. `tools`
Configures built-in tool behavior.
- `restrict_to_workspace` (*boolean*, default: `false`): Strict containment blocking any tool actions outside the workspace directory.
- `image_generation`:
  - `enabled` (*boolean*, default: `false`): Enable the `generate_image` tool.
  - `provider` (*string*, default: `"openrouter"`): Image generation provider (`openrouter`, `openai`, etc.).
  - `model` (*string*, default: `"openai/gpt-5.4-image-2"`): Model identifier.
  - `max_images_per_turn` (*integer*, default: `4`, range: `1-8`): Max images per turn.
- `my`:
  - `enable` (*boolean*, default: `true`): Allow dynamic runtime inspection.
  - `allow_set` (*boolean*, default: `false`): Allow runtime mutation of model and limits.

### 5. `channels`
Mapping of channel names to account configurations.
- `dm_policy`: `"pairing"` (require device pairing code), `"allowlist"` (only IDs in `allow_from`), `"open"` (allow all DMs), or `"disabled"`.
- `group_policy`: `"allowlist"` (only group IDs in `group_allow_from`), `"open"`, or `"disabled"`.
- `extra`: Dictionary of channel-specific credentials and configuration options (e.g. API keys, webhook secrets).

