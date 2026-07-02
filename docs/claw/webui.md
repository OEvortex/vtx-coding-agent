# WebUI

vtx-claw includes a built-in web interface served via WebSocket on the gateway port.

## Accessing the WebUI

```bash
vtx-claw gateway
# Open http://127.0.0.1:8765
```

## Architecture

The WebUI is a React/TypeScript single-page application that communicates with the gateway over a WebSocket multiplex protocol.

### Components

| Component | Purpose |
|-----------|---------|
| `webui/` | Python backend (27 modules) |
| `web/dist/` | Built React frontend |
| `channels/websocket.py` | WebSocket channel adapter |

## WebSocket Protocol

The WebSocket connection multiplexes multiple channels over a single connection:

- **Session management**: Create, resume, fork sessions
- **Message streaming**: Real-time agent responses
- **Settings**: Read/write configuration
- **Media**: HMAC-signed media URLs
- **Token usage**: Daily usage statistics

## Settings API

The WebUI exposes a settings API for managing configuration:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/settings` | GET | Get full settings payload |
| `/api/settings/update` | POST | Update settings |
| `/api/settings/model-presets` | GET | List model presets |
| `/api/settings/providers` | GET | List providers |

### Settings Sections

| Section | Restart Required |
|---------|-----------------|
| Agent defaults | `engineRestart` |
| Model presets | `none` |
| Provider settings | `none` |
| Web search | `none` |
| Image generation | `none` |
| Transcription | `none` |
| Network safety | `engineRestart` |

## Session Management

### Session List

The sidebar shows all sessions with:
- Title (auto-generated or user-edited)
- Last message preview
- Updated timestamp
- Token usage

### Session Forking

Fork a session from any point to create a new conversation:

1. Right-click a message → "Fork from here"
2. New session created with copied history
3. Original session unchanged

### Session Index

Sessions are indexed in `.webui_session_index.json` for fast sidebar rendering. The index is incrementally reconciled based on file mtime and size.

## Token Usage Tracking

Daily token usage is tracked and displayed in the WebUI:

| Metric | Description |
|--------|-------------|
| `prompt_tokens` | Input tokens |
| `completion_tokens` | Output tokens |
| `cached_tokens` | Cache hit tokens |
| `total_tokens` | Sum of all |
| `provider_tokens` | Provider-specific tokens |
| `estimated_tokens` | Estimated total |

Usage is classified by source: `user`, `api`, `cron`, `dream`, `system`.

### Streaks

The WebUI calculates:
- **Current streak**: Consecutive days with usage
- **Longest streak**: All-time record

## Media Serving

Media files are served via HMAC-signed URLs:

```
/api/media/<signature>/<base64-encoded-path>
```

Features:
- HMAC-SHA256 signature (truncated to 16 bytes)
- Byte-range support for video/image streaming
- SVG Content-Security-Policy sandbox
- MIME type allowlist

## Transcript System

WebUI transcripts are append-only JSONL files per session:

- **Rotation**: Automatic when file exceeds 8MB
- **Segmented storage**: Old turns rotated into numbered segments
- **Cursor-based pagination**: Efficient loading of conversation history

## Gateway Tokens

WebSocket and API authentication uses short-lived tokens:

- **Format**: `nbwt_<secrets.token_urlsafe(32)>`
- **TTL**: 300 seconds (configurable)
- **One-shot**: Consumed on validation (prevents replay)
- **Max pool**: 10,000 tokens per pool

## Configuration

```json
{
  "channels": {
    "websocket": {
      "enabled": true,
      "host": "127.0.0.1",
      "port": 8765,
      "token": "your-secret-token",
      "websocket_requires_token": true
    }
  }
}
```

## Development Mode

For WebUI development with Vite HMR:

```bash
cd src/claw/webui
bun install
bun run dev
# Opens at http://127.0.0.1:5173
```

The dev server proxies API traffic to `http://127.0.0.1:8765`.
