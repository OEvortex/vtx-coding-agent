# MCP servers

Vtx speaks the [Model Context Protocol](https://modelcontextprotocol.io), so any
MCP server's tools show up alongside the built-in ones. Servers are declared in
`mcp.json` and connected at session start.

## Configuration

Two files, both optional:

| File | Scope |
| --- | --- |
| `~/.vtx/mcp.json` | every project |
| `<project>/.vtx/mcp.json` | this project only, and **only when the project is trusted** |

Project entries replace global entries with the same name. Both use the
`mcpServers` shape every other MCP client reads, so a config you already have
for another agent copies over unchanged:

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "."]
    },
    "docs": {
      "url": "https://example.com/mcp",
      "headers": { "Authorization": "Bearer ${DOCS_TOKEN}" }
    }
  }
}
```

### Per-server keys

| Key | Applies to | Meaning |
| --- | --- | --- |
| `command` | stdio | Executable to run. Required, and mutually exclusive with `url`. |
| `args` | stdio | Arguments. Default `[]`. |
| `env` | stdio | Extra environment. `$VAR` / `${VAR}` are expanded. |
| `cwd` | stdio | Working directory. |
| `url` | http | `http(s)` endpoint. Required, and mutually exclusive with `command`. |
| `headers` | http | Extra request headers. `$VAR` / `${VAR}` are expanded. |
| `oauth` | http | `clientId`, `clientSecret`, `callbackPort`, `scope` for servers without dynamic registration. |
| `enabled` | both | `false` keeps the entry without connecting it, so it can be re-enabled later. |
| `timeout` | both | Per-request timeout in seconds. Default `60`. Progress notifications reset it. |

An unset `$VAR` expands to empty rather than failing, so a missing optional
token leaves the header empty and lets the server answer 401 — which vtx reports
as "needs sign-in" rather than crashing the session.

### Project files and trust

A project MCP server is a command vtx would **execute**. Reading one out of a
repository the user merely opened would mean running code they did not ask to
run, so `<project>/.vtx/mcp.json` is ignored until the project is trusted. A
global `~/.vtx/mcp.json` is always read, because the user wrote it themselves.

## Commands

| Command | Does |
| --- | --- |
| `/mcp` | list every server, its state, and its tool count |
| `/mcp reload` | re-read `mcp.json` and reconnect |
| `/mcp reconnect <name>` | rebuild one server's connection |
| `/mcp enable <name>` | enable a server and save it to its `mcp.json` |
| `/mcp disable <name>` | disable a server and save it to its `mcp.json` |

`/reload` also picks up `mcp.json` edits, and reports the resulting tool count.

Server states:

| State | Meaning |
| --- | --- |
| `connected` | live, with its tools in the model surface |
| `connecting` | still starting; its tools appear when it lands |
| `disconnected` | the transport dropped; the next call reconnects |
| `needs-auth` | the server wants OAuth sign-in (remote only) |
| `failed` | it did not start; `/mcp` shows why |
| `disabled` | configured but switched off |

## How a server's tools behave

**Names.** `mcp__<server>__<tool>`, sanitized to the 64 characters of
`[A-Za-z0-9_-]` that providers accept. A name that is too long, or that
collides after sanitizing (`a.b` and `a_b` both become `a_b`), gets a short
hash suffix derived from the original server and tool. A built-in vtx tool name
is never shadowed.

**Permissions.** A tool annotated `readOnlyHint: true` is registered as
non-mutating and skips the approval prompt. `destructiveHint: true` forces one
even under a permissive mode. Tools with no annotations are treated as mutating,
because a server that says nothing has not told us it is safe.

**Output.** Model-facing text is capped at 20 KB. Longer output keeps its head
and tail with the middle elided, and the full text is written to a private
temp file (mode 0600) whose path is handed to the model — a pointer it can
actually act on, rather than the first 20 KB of a large payload. Binary
resources are spilled the same way; images are passed through as real image
content; audio and resource links become short text placeholders.

**Cancellation.** `Esc` during a tool call cancels the request and tells the
server to stop, using the same interrupt path as every other vtx tool.

## Failure behaviour

A server that is slow, broken, or missing never stops a session. Startup waits
up to 10 seconds for all servers in parallel, then proceeds — a straggler
reports `connecting` and contributes its tools when it lands. Failures are
reported once as launch warnings and are visible on `/mcp`.

Closing a session closes every server. That matters more than it sounds: a
stdio server is a child process holding a pipe, so leaking one orphans the
process. Shutdown follows the MCP spec — close stdin, wait, `SIGTERM`, then
`SIGKILL` — and signals the whole **process group**, so a server launched
through a wrapper like `npx` or `uvx` does not leave grandchildren behind.

## Using the client directly

The tool and config layers are conveniences. The client itself is
transport-neutral and usable on its own:

```python
from vtx.mcp import McpClient, McpClientOptions, StdioTransport, StdioTransportOptions

client = McpClient(McpClientOptions(name="my-app", version="1.0.0"))
await client.connect(
    StdioTransport(StdioTransportOptions(command="npx", args=["-y", "some-mcp-server"]))
)
tools = await client.list_tools()
result = await client.call_tool("search", {"query": "MCP"})
await client.close()
```

`vtx.mcp` is a top-level package, a sibling of `vtx.coding_agent` and
`vtx.tui`. The harness (`vtx.ai.agent`) never imports it; the product layer
wires it up.

## Not supported

Deliberately out of scope, matching the reference client this is modelled on:

- batched JSON-RPC messages
- the legacy HTTP+SSE transport (pre-2025-03-26)
- acting as an MCP **server** — vtx is a client
- server-initiated sampling and elicitation
- MCP tasks
- OAuth sign-in: a remote server that answers 401 reports `needs-auth`, but the
  interactive authorization flow is not implemented
