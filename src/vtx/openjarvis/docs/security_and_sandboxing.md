# Security & Sandboxing

OpenJarvis is designed with defense-in-depth security principles to safely run autonomous operations in local and multi-user environments.

---

## 🛡️ Security Architecture

```
                    ┌──────────────────────────────────────────────┐
                    │               Inbound Request                │
                    └──────────────────────┬───────────────────────┘
                                           │
                           [ 1. Device Pairing & Allowlist ]
                                           │
                    ┌──────────────────────▼───────────────────────┐
                    │              Agent ReAct Loop                │
                    └──────────────────────┬───────────────────────┘
                                           │
                    ┌──────────────────────┼───────────────────────┐
                    │                      │                       │
         [ 2. Workspace Policy ]  [ 3. Network SSRF ]    [ 4. Tool Boundary ]
         Path containment checks  Private IP filtering   Read-only & confirm
                    │                      │                       │
                    ▼                      ▼                       ▼
               Filesystem               Network                Execution
```

---

## 🔒 Security Layers

### 1. Workspace Boundary Enforcement (`workspace_policy.py`)
- When `tools.restrict_to_workspace` is enabled, all file operations (`read`, `write`, `edit`, `apply_patch`, and file targets in tools) must resolve strictly within `workspace`.
- Resolves all symbolic links and canonical paths before execution, raising `WorkspaceBoundaryError` on path traversal attempts (`../../etc/passwd`).

### 2. Network SSRF Defense (`network.py`)
- Every outbound network request (including MCP HTTP endpoints, Web search, and webhooks) passes through `validate_url_target()`.
- Explicitly blocks calls to:
  - Loopback (`127.0.0.0/8`, `::1`)
  - Private IP subnets (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`)
  - Cloud instance metadata services (`169.254.169.254`)

### 3. Device Pairing & Channel Allowlisting (`pairing/store.py`)
- Prevents unauthorized access when bots are exposed on public chat channels (Telegram, Discord).
- The pairing workflow requires out-of-band authorization via the local terminal:
  1. An unknown user sends a message to the bot.
  2. The bot responds with a cryptographically random 6-digit challenge code.
  3. The administrator approves the code via `vtx jarvis pairing approve <code>`.

