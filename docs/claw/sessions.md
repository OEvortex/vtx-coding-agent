# Sessions

Sessions store conversation history as append-only JSONL files. Each session is identified by a unique key derived from the channel and chat ID.

## Session Keys

| Channel | Key Format | Example |
|---------|-----------|---------|
| Telegram | `telegram:<chat_id>` | `telegram:123456` |
| Discord | `discord:<channel_id>` | `discord:789012` |
| Slack | `slack:<channel_id>` | `slack:C01234567` |
| WebSocket | `websocket:<uuid>` | `websocket:a1b2c3d4` |
| CLI | `cli:direct` | `cli:direct` |
| Email | `email:<address>` | `email:user@example.com` |

## Storage Format

Sessions are stored as append-only JSONL files under `~/.vtx/claw/workspace/sessions/`:

```
~/.vtx/claw/workspace/sessions/
├── <base64-encoded-key>.jsonl
├── <base64-encoded-key>.jsonl
└── ...
```

### File Structure

```jsonl
{"_type": "metadata", "key": "telegram:123456", "created_at": "2024-01-01T00:00:00Z", "updated_at": "2024-01-01T01:00:00Z", "metadata": {}, "last_consolidated": 0}
{"role": "user", "content": "Hello!", "timestamp": "2024-01-01T00:00:00Z"}
{"role": "assistant", "content": "Hi there!", "timestamp": "2024-01-01T00:00:01Z"}
{"role": "user", "content": "How are you?", "timestamp": "2024-01-01T00:01:00Z"}
{"role": "assistant", "content": "I'm doing well!", "tool_calls": [...], "timestamp": "2024-01-01T00:01:01Z"}
```

### Metadata Line

| Field | Description |
|-------|-------------|
| `_type` | Always `"metadata"` |
| `key` | Session key |
| `created_at` | Session creation time |
| `updated_at` | Last update time |
| `metadata` | Arbitrary metadata dict |
| `last_consolidated` | Index of last consolidated message |

## Compaction

### Automatic Compaction

When token usage exceeds `agents.defaults.session_ttl_minutes` (default: 15 min idle) or `context_window_tokens * consolidation_ratio`, older messages are summarized and archived.

### `last_consolidated` Pointer

Messages before this index are summarized in metadata. Only messages from `last_consolidated` forward are replayed into context.

### Manual Compaction

In chat: `/compact`

## File Cap

Sessions are capped at 2000 messages (`FILE_MAX_MESSAGES`). When exceeded:
1. Oldest messages are dropped
2. Non-consolidated messages are archived via `on_archive` callback

## History Replay

The `get_history()` method reconstructs conversation history:

1. Reads JSONL from `last_consolidated` forward
2. Strips internal messages (`_channel_delivery`, `_command`)
3. Sanitizes assistant text (removes timestamps, image breadcrumbs)
4. Injects media breadcrumbs for user attachments
5. Ensures history starts at a user message
6. Applies token budget: `min(2000, max(120, context_window_tokens // 100))`

## Session Forking

Sessions can be forked to create a new conversation from a specific point:

1. Deep-copies messages up to a user-message index
2. Strips volatile metadata (`goal_state`, `pending_user_turn`, `title`)
3. Adjusts `last_consolidated` counter
4. Writes new JSONL file with new session key

## Volatile Metadata

These metadata keys are stripped during forking:

- `goal_state` — Active goal tracking
- `pending_user_turn` — Pending user message
- `runtime_checkpoint` — Runtime state
- `thread_goal` — Thread goal
- `title` — Session title
- `title_user_edited` — User-edited title flag

## Goal State

Sustained goals track long-running objectives:

```json
{
  "goal_state": {
    "goal": "Refactor the auth module",
    "started_at": "2024-01-01T00:00:00Z",
    "status": "active"
  }
}
```

Goals are managed via:
- `/goal <text>` — Start a goal
- `long_task` tool — Register from agent
- `complete_goal` tool — Close with recap

## Resume Sessions

Resume a previous session:

```bash
# Resume most recent
agenite-claw agent -c

# Resume specific session
agenite-claw agent --session <session-id>
```
