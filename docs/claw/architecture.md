# Architecture

## Package Structure

vtx-claw lives under `src/vtx_claw/` with the following subpackages:

```
src/vtx_claw/
├── __init__.py              # Version, logo, lazy imports
├── __main__.py              # python -m vtx_claw entry point
├── _vtx_bridge.py           # Bridges vtx-core config into vtx-claw
├── config_base.py           # Shared Pydantic Base model
│
├── agent/                   # Core agent execution
│   ├── loop.py              # AgentLoop — orchestrates LLM calls + tool execution
│   ├── runner.py            # AgentRunner — handles single iterations
│   ├── context.py           # Context builder — system prompt + tool definitions
│   ├── memory.py            # Persistent memory store (SOUL.md, USER.md, MEMORY.md)
│   ├── subagent.py          # Subagent spawning and management
│   ├── hooks.py             # AgentHook lifecycle callbacks
│   ├── auto_compact.py      # Automatic context compaction
│   └── tools/               # All agent tools (see tools.md)
│
├── api/                     # OpenAI-compatible API server (/v1/chat/completions)
├── apps/                    # CLI app protocol abstractions
├── audio/                   # Audio transcription registry
├── bus/                     # Async message bus (inbound + outbound queues)
├── channels/                # 16 chat platform adapters
├── cli/                     # Typer CLI framework
├── command/                 # In-chat slash command router
├── config/                  # Config schema, loader, paths
├── cron/                    # Scheduled job infrastructure
├── gateway/                 # Gateway process lifecycle
├── pairing/                 # DM device pairing
├── providers/               # LLM provider abstraction
├── sdk/                     # Client libraries for external consumers
├── security/                # SSRF protection, workspace sandboxing
├── session/                 # Conversation persistence (JSONL)
├── skills/                  # Built-in skill definitions
├── templates/               # Workspace template files
├── utils/                   # ~25 utility modules
├── web/                     # Bundled web frontend assets
└── webui/                   # WebSocket-served WebUI backend
```

## Message Bus

The message bus (`bus/queue.py`) decouples channels from the agent using two async queues:

- **Inbound queue**: Channel → Agent. Carries user messages, commands, and system events.
- **Outbound queue**: Agent → Channel. Carries assistant responses, tool hints, and status updates.

Each message is an `OutboundMessage` or `InboundMessage` dataclass with fields for `content`, `session_key`, `channel`, `chat_id`, `sender_id`, and delivery metadata.

## Request Flow

```
User sends message on Telegram
    │
    ▼
TelegramChannel._on_message()
    │  Creates InboundMessage with session_key="telegram:<chat_id>"
    │  Publishes to inbound queue
    ▼
ChannelManager dispatches to AgentLoop
    │  Resolves session via SessionManager
    │  Loads history from JSONL
    │  Builds context (system prompt + tools + memory)
    ▼
AgentLoop.run()
    │  Calls LLM provider with messages + tools
    │  Handles tool calls (exec, read, edit, etc.)
    │  Streams progress back via outbound queue
    │  Auto-compacts if context nears limit
    ▼
Outbound queue → ChannelManager
    │  Routes to correct channel by session_key
    ▼
TelegramChannel.send()
    │  Splits markdown, renders HTML
    │  Sends via python-telegram-bot
    ▼
User sees response in Telegram
```

## Agent Loop

The agent loop (`agent/loop.py`, ~1890 lines) is the core execution engine:

1. **Context building**: Assembles system prompt from AGENTS.md, skills, memory, and environment variables.
2. **LLM call**: Sends messages to the configured provider with tool definitions.
3. **Tool execution**: Processes tool calls sequentially, respecting permission mode.
4. **Progress streaming**: Sends incremental updates to the channel during execution.
5. **Auto-compaction**: Triggers context compression when token usage exceeds threshold.
6. **Goal evaluation**: Checks sustained-goal completion conditions.

## Session Management

Sessions are stored as append-only JSONL files under `~/.vtx/claw/workspace/sessions/`:

- **Key format**: `channel:chat_id` (e.g., `telegram:123456`)
- **Storage**: Base64-encoded key as filename
- **Compaction**: `last_consolidated` pointer tracks which messages are summarized
- **Cap**: Maximum 2000 messages per session file

## Design Principles

- **Channel-agnostic**: The agent doesn't know which channel a message came from. Channels are thin adapters.
- **Async-first**: All I/O is async (asyncio). The bus, agent loop, and channels run on a shared event loop.
- **Config-driven**: Everything is configured via a single JSON file. No code changes needed for new providers or channels.
- **Session-persistent**: Conversations survive restarts. Sessions can be resumed, forked, or exported.
- **Secure by default**: SSRF protection, workspace sandboxing, and permission modes are enabled out of the box.
