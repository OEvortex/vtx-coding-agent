# CLI Apps Engine

The OpenJarvis CLI Apps engine provides a secure, structured interface for discovering and running CLI applications without exposing raw shell vulnerabilities.

---

## 🚀 Overview & Architecture

The CLI Apps engine implements the **CLI-Anything** protocol (`vtx.openjarvis.apps.protocol`):
- Eliminates shell injection by passing structured `argv` arrays directly to the executable.
- Enforces execution timeouts and output buffering limits.
- Exposes structured help and subcommand schemas to the LLM.

```mermaid
graph LR
    Agent[Agent Loop] -->|Calls run_cli_app(app, argv)| Tool[CliAppsTool]
    Tool -->|Validates App Name & Args| Manager[CliAppManager]
    Manager -->|Spawns Subprocess| Subprocess[Controlled Process: binary + argv]
    Subprocess -->|Captures stdout/stderr| Manager
    Manager -->|Returns Structured Output| Agent
```

---

## 📦 Using `run_cli_app` in Agent Turns

The agent uses `run_cli_app` instead of raw shell scripts when interacting with dedicated CLI tools:

```json
{
  "app": "git",
  "argv": ["status", "--short"]
}
```

```json
{
  "app": "docker",
  "argv": ["ps", "--format", "{{.ID}}: {{.Image}}"]
}
```

---

## 🛠️ Registering Custom CLI Apps

Custom CLI apps can be registered in `~/.vtx/openjarvis/cli-apps/` with a manifest:

```json
{
  "name": "my-tool",
  "binary": "/usr/local/bin/my-tool",
  "description": "Company internal deployment tool",
  "timeout_seconds": 60,
  "env": {
    "ENV_MODE": "production"
  }
}
```

