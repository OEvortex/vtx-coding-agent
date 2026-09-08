# Gateway Server & API Reference

OpenJarvis includes a multiplexed server that unifies WebSocket RPC control and OpenAI-compatible HTTP endpoints on a single port (default: `18789`).

---

## 🌐 Multiplexed Gateway Architecture

```
                    Client (Web UI / Mobile / CLI / IDE)
                                    │
                                    │ Port :18789
                    ┌───────────────▼───────────────┐
                    │      OpenJarvis Gateway       │
                    └───────┬───────────────┬───────┘
                            │               │
           HTTP REST API ───┘               └─── Duplex WebSocket (/ws)
           • /health                             • Handshake (connect -> hello-ok)
           • /v1/models                          • Duplex RPC (req/res)
           • /v1/chat/completions                • Real-time Event Streams
```

---

## 🔌 HTTP REST Endpoints

### 1. Health Check
```http
GET /health
```
**Response**:
```json
{
  "ok": true,
  "port": 18789,
  "status": "running"
}
```

### 2. Models List (OpenAI Compatible)
```http
GET /v1/models
```
**Response**:
```json
{
  "data": [
    { "id": "openjarvis", "object": "model" },
    { "id": "openjarvis/default", "object": "model" }
  ]
}
```

### 3. Chat Completions (OpenAI Compatible)
```http
POST /v1/chat/completions
Content-Type: application/json
Authorization: Bearer <your-token>

{
  "model": "openjarvis",
  "messages": [
    {"role": "user", "content": "Analyze performance logs from yesterday"}
  ],
  "stream": false
}
```

---

## ⚡ WebSocket RPC Protocol (`/ws`)

The WebSocket endpoint provides high-performance bi-directional communication:

### 1. Connection Handshake
Upon establishing a WebSocket connection, the client sends a `connect` frame:
```json
{
  "type": "connect",
  "client_name": "desktop-client",
  "auth_token": "your-token-or-pairing-secret"
}
```

The gateway responds with `hello-ok`:
```json
{
  "type": "hello-ok",
  "server_version": "0.1.0",
  "session_id": "main",
  "active_channels": ["telegram", "websocket"]
}
```

### 2. Request & Response Pattern
```json
{
  "id": "req-101",
  "type": "request",
  "action": "agent.prompt",
  "params": {
    "text": "Run pytest suite"
  }
}
```

### 3. Server-Sent Events (SSE over WS)
Real-time progress, thinking, and token streaming frames:
- `event: "progress"`: Intermediate tool executions.
- `event: "token"`: Incremental LLM token streaming.
- `event: "tick"`: Periodic heartbeat and health ping.
- `event: "cron"`: Notification when a scheduled task executes.

---

## 🔐 Authentication Modes

Set in `openjarvis.json` under `gateway.auth_mode`:

| Mode | Description |
| :--- | :--- |
| **`token`** *(Default)* | Requires `Authorization: Bearer <token>` or `?token=<token>` query parameter. |
| **`password`** | Requires HTTP Basic Auth or password header verification. |
| **`trusted-proxy`** | Relies on upstream reverse proxy headers (e.g. `X-Forwarded-For`, `X-Remote-User`). |
| **`none`** | Disables authentication (only recommended for loopback/development). |

