# MCP Integration

agenite-claw supports the Model Context Protocol (MCP) for connecting to external tool servers.

## Configuration

MCP servers are configured in `tools.mcp_servers`:

```json
{
  "tools": {
    "mcp_servers": {
      "my-server": {
        "type": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem"],
        "env": {},
        "cwd": "/tmp",
        "tool_timeout": 30,
        "enabled_tools": ["*"]
      }
    }
  }
}
```

## Server Types

### stdio

Run an MCP server as a subprocess:

```json
{
  "type": "stdio",
  "command": "npx",
  "args": ["-y", "@modelcontextprotocol/server-filesystem"],
  "env": {
    "API_KEY": "value"
  },
  "cwd": "/path/to/working/dir"
}
```

### SSE (Server-Sent Events)

Connect to an HTTP SSE endpoint:

```json
{
  "type": "sse",
  "url": "http://localhost:3000/sse",
  "headers": {
    "Authorization": "Bearer token"
  }
}
```

### streamableHttp

Connect to a streamable HTTP endpoint:

```json
{
  "type": "streamableHttp",
  "url": "http://localhost:3000/mcp",
  "headers": {
    "Authorization": "Bearer token"
  }
}
```

## Configuration Fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `type` | string \| null | `null` | Server type (`stdio`, `sse`, `streamableHttp`). Auto-detected if omitted. |
| `command` | string | `""` | Stdio: command to run |
| `args` | list | `[]` | Stdio: command arguments |
| `env` | dict | `{}` | Stdio: extra environment variables |
| `cwd` | string | `""` | Stdio: working directory |
| `url` | string | `""` | HTTP/SSE: endpoint URL |
| `headers` | dict | `{}` | HTTP/SSE: custom headers |
| `tool_timeout` | int | `30` | Seconds before a tool call is cancelled |
| `enabled_tools` | list | `["*"]` | Tools to register (see below) |

## Tool Filtering

The `enabled_tools` field controls which tools are registered:

| Value | Description |
|-------|-------------|
| `["*"]` | All capabilities (tools, resources, prompts) |
| `["tool1", "tool2"]` | Only listed tools (no resources/prompts) |
| `["mcp_server_tool1"]` | Use wrapped name format |

### Tool Naming

MCP tools are bridged as native tools with the `mcp_` prefix:

- MCP tool name: `read_file`
- agenite-claw tool name: `mcp_my-server_read_file`

This prevents naming conflicts between MCP servers and built-in tools.

## Examples

### Filesystem Server

```json
{
  "tools": {
    "mcp_servers": {
      "filesystem": {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/files"],
        "tool_timeout": 10
      }
    }
  }
}
```

### GitHub Server

```json
{
  "tools": {
    "mcp_servers": {
      "github": {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-github"],
        "env": {
          "GITHUB_PERSONAL_ACCESS_TOKEN": "${GITHUB_TOKEN}"
        }
      }
    }
  }
}
```

### Remote SSE Server

```json
{
  "tools": {
    "mcp_servers": {
      "remote-tools": {
        "type": "sse",
        "url": "https://mcp.example.com/sse",
        "headers": {
          "Authorization": "Bearer ${MCP_TOKEN}"
        },
        "enabled_tools": ["search", "fetch"]
      }
    }
  }
}
```

## Timeout Handling

Tool calls are cancelled after `tool_timeout` seconds (default: 30). This prevents hung MCP servers from blocking the agent.

## Auto-Detection

If `type` is omitted, agenite-claw auto-detects the server type:
- If `command` is set → `stdio`
- If `url` is set with SSE patterns → `sse`
- If `url` is set with HTTP patterns → `streamableHttp`
