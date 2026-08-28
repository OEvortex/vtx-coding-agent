# OpenJarvis Documentation

OpenJarvis is a VTX-native, multi-channel autonomous AI agent framework designed for long-running execution, persistent context, multi-platform messaging, extensible tool ecosystems, and enterprise-grade security.

```
                  ┌──────────────────────────────────────────────┐
                  │              OpenJarvis Gateway              │
                  │   WebSocket (RPC/Events) + HTTP API (18789)  │
                  └───────┬──────────────────────────────┬───────┘
                          │                              │
          ┌───────────────▼───────────────┐     ┌────────▼────────────────┐
          │       Channel Manager         │     │   AIAgent Loop Engine   │
          │  Telegram, Slack, Discord,    │     │  (VTX ReAct Execution)  │
          │  WhatsApp, Signal, Matrix...  │     └────────┬────────────────┘
          └───────────────┬───────────────┘              │
                          │     ┌────────────────────────┼────────────────┐
                          ▼     ▼                        ▼                ▼
                 ┌─────────────────┐           ┌──────────────────┐  ┌─────────────┐
                 │  Message Bus    │           │   Tool Registry  │  │   Memory    │
                 │  (Event System) │           │ Exec, Patch, MCP │  │ FTS5/Vector │
                 └─────────────────┘           └──────────────────┘  └─────────────┘
```

---

## 📚 Documentation Index

| Guide | Description |
| :--- | :--- |
| **[Getting Started](getting_started.md)** | Installation, quickstart, CLI commands, and initial setup. |
| **[Architecture](architecture.md)** | Core system architecture, subsystem interaction, and event pipelines. |
| **[Configuration Reference](configuration.md)** | Comprehensive reference for `openjarvis.json`, environment variables, and defaults. |
| **[Agent Runtime & Memory](agent_and_runtime.md)** | ReAct agent loop, session management, FTS5 full-text search, Vector memory, and Hooks. |
| **[Channels & Messaging](channels.md)** | Connect Telegram, Slack, Discord, WhatsApp, Signal, Matrix, Feishu, DingTalk, QQ, and WebSockets. |
| **[Tools Reference](tools_reference.md)** | Full reference of all native tools (Exec, ApplyPatch, Cron, CLI Apps, Image Gen, MCP, etc.). |
| **[Gateway & API](gateway_and_api.md)** | Multiplexed Gateway server, WebSocket control protocol, OpenAI-compatible REST API, and device pairing. |
| **[Cron & Scheduling](cron_and_scheduling.md)** | Recurring jobs, reminders, background exec sessions, and long-running goals. |
| **[Security & Sandboxing](security_and_sandboxing.md)** | Workspace containment, network SSRF defense, pairing authentication, and permission allowlists. |
| **[CLI Apps Engine](cli_apps.md)** | CLI-Anything integration and controlled subprocess execution. |

---

## 🌟 Key Capabilities

### 1. Universal Multi-Channel Ingress & Egress
OpenJarvis connects seamlessly with 15+ messaging platforms through a unified `ChannelManager` and asynchronous `MessageBus`:
- **Social & Chat**: Telegram, Discord, Slack, WhatsApp, Signal, Matrix
- **Enterprise**: Feishu (Lark), DingTalk, WeCom (Enterprise WeChat), Microsoft Teams
- **Web & Protocol**: Native WebSockets, Email (SMTP/IMAP), QQ / NapCat / MoChat

### 2. High-Performance Tool Ecosystem & MCP Integration
- **Execution & Code Editing**: `exec` shell execution with optional workspace containment, `apply_patch` for diff application.
- **Background Execution**: `write_stdin` and `list_exec_sessions` for interactive processes and daemons.
- **Scheduled Automations**: `cron` tool backed by JSON-persisted SQLite schedules and turn-continuation triggers.
- **Model Context Protocol (MCP)**: Native stdio and SSE MCP client with dynamic tool, prompt, and resource discovery.
- **Generative Media**: `generate_image` tool for multimodal generation with local artifact storage.
- **Self-Inspection**: `my` tool for inspecting and tuning agent parameters dynamically.

### 3. Dual-Layer Memory & Context Engine
- **Full-Text Search (FTS5 SQLite)**: High-speed full-text search indexing user turns and historical conversations.
- **Vector Embeddings**: Semantic retrieval across workspace notes and previous interactions.
- **Skills Management**: Automatic loading of modular skills with slash-command bindings.

### 4. Enterprise-Grade Security
- **Strict Workspace Containment**: Validated filesystem operations and path resolution preventing traversal attacks.
- **Network SSRF Protection**: Outbound IP validation blocking private networks (`10.0.0.0/8`, `127.0.0.0/8`, `192.168.0.0/16`, AWS metadata `169.254.169.254`).
- **Device Pairing**: Secure handshake authorization using cryptographically random pairing tokens.

