# vtx.mcp

A small, standalone Model Context Protocol client for vtx. It does not depend on the official MCP SDK, and the harness in `vtx.ai.agent` does not depend on this package.

The package provides a transport-neutral client core, stdio and Streamable HTTP transports, an in-memory testing transport, OAuth 2.1 sign-in, `mcp.json` loading with project trust, and an adapter that exposes a server's tools as vtx `BaseTool`s.

## Usage

```python
from vtx.mcp import McpClient, McpClientOptions, StdioTransport, StdioTransportOptions

transport = StdioTransport(
    StdioTransportOptions(
        command="npx",
        args=["-y", "@modelcontextprotocol/server-filesystem", "/workspace"],
    )
)
client = McpClient(
    McpClientOptions(
        name="vtx",
        version="1.0.0",
        roots=[Root(uri="file:///workspace", name="workspace")],
    )
)

await client.connect(transport)
tools = await client.list_tools()
result = await client.call_tool("search", {"query": "MCP"})
await client.close()
```

For a remote server, use `StreamableHttpTransport(StreamableHttpTransportOptions(url=..., headers=...))`. An `httpx.AsyncClient` can be injected for proxying or custom networking.

An MCP transport owns framing and I/O. It delivers individual JSON-RPC messages to `McpClient`; the client owns request correlation, initialization, timeouts, cancellation, server requests, and protocol-level helpers. Keeping that line sharp is what lets the same client drive a subprocess, an HTTP endpoint, and a test pair.

### Config and servers

Most callers do not build clients by hand. `McpManager` reads config, owns one connection per server, connects them at a bounded deadline, and hands back the live tools:

```python
from vtx.mcp import McpManager

manager = McpManager(cwd="/path/to/project", project_trusted=False)
tools = await manager.connect_all()   # bounded, never raises
# ... later ...
await manager.close()                  # closes child processes too
```

`connect_all` is bounded by `startup_wait_seconds` (10s by default). A server still connecting at the deadline reports `connecting` and contributes its tools when it lands, because one slow server must not hold up a session. `McpManager` never raises on a bad server: `statuses()` reports each one, and `/mcp` prints that report.

Servers come from two files, both optional:

| File | Scope |
| --- | --- |
| `~/.vtx/mcp.json` | every project |
| `<project>/.vtx/mcp.json` | this project only, and **only when the project is trusted** |

Project entries replace global entries of the same name. Both use the `mcpServers` shape every other MCP client reads, so a config you already have for Claude Desktop or Cursor copies over unchanged.

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "."]
    },
    "docs": {
      "url": "https://example.com/mcp",
      "headers": { "Authorization": "Bearer ${DOCS_TOKEN}" },
      "oauth": { "scope": "read" }
    }
  }
}
```

Set exactly one of `command` or `url`. Per-server keys: `enabled`, `timeout` (seconds), plus `args`/`env`/`cwd` for stdio or `headers`/`oauth` for HTTP. `$VAR` and `${VAR}` expand in `env` and `headers`, and an unset variable expands to empty rather than raising: a missing optional token should produce a 401 the user can see, not a crashed session. A malformed entry is dropped with a message in `LoadedMcpConfig.errors`; one bad server never takes out the rest.

A project `mcp.json` is a command vtx would run, so a file found in a repository the user merely opened is never read. Trust is granted by a person, once, per resolved path, with no implicit path to it. The store is keyed by `Path.resolve()`, so `~/proj`, `~/proj/`, and a symlink to it are one project, and a corrupt store fails closed.

### Tools for an LLM

`to_tool_content(result)` converts a `CallToolResult` to text and image content for a model, in the shape of `vtx.ai.providers`' `TextContent` and `ImageContent`. Text and images pass through, embedded text and image resources are unwrapped, and audio, resource links, and binary resources become short text placeholders. A result without content blocks but with `structuredContent` becomes its JSON.

`McpTool` wraps one remote tool as a vtx `BaseTool`, named `mcp__<server>__<tool>`:

```python
from vtx.mcp import McpTool, create_mcp_tool_name

tool = McpTool(
    server="filesystem",
    definition=definition,          # the raw MCP Tool object
    name=create_mcp_tool_name("filesystem", definition["name"], is_taken),
    caller=connection.call_tool,
    timeout_ms=60_000,
)
```

The name is sanitized to the 64 characters of `[A-Za-z0-9_-]` that providers accept; a collision or a truncation gets a hash of the original server and tool appended, so it stays unique rather than merely shorter.

Two annotations change behaviour rather than display. `readOnlyHint: true` marks the tool non-mutating, so it skips the approval prompt. `destructiveHint: true` forces one even under a permissive mode.

Output past `MCP_OUTPUT_MAX_BYTES` (20K) is truncated for the model, with the full text written to a temp file whose path is reported. The session tool-result budget is 200K, too generous to be the only limit on a third-party tool, and a pointer to the whole payload beats the first 20K of a database dump.

`resources.py` adds three session-level tools -- `list_mcp_resources`, `list_mcp_resource_templates`, `read_mcp_resource` -- because a client can list resources but without a tool the model can never ask for one. They take a `server` argument and span every connected server; three tools cost the same however many servers are up.

### OAuth

`vtx.mcp.oauth` provides the MCP OAuth client subset without depending on the official SDK:

```python
from vtx.mcp import StreamableHttpTransport, StreamableHttpTransportOptions
from vtx.mcp import McpAuthRequiredError
from vtx.mcp.oauth import (
    McpOAuthProvider,
    OAuthCallbackServer,
    OAuthClientMetadata,
    authorize_mcp,
    adapt_oauth_provider,
)

server_url = "https://mcp.example.com/mcp"
callback = await OAuthCallbackServer.listen()
provider = McpOAuthProvider(
    server_url=server_url,
    redirect_url=callback.redirect_url,
    client_metadata=OAuthClientMetadata(
        redirect_uris=[callback.redirect_url], client_name="vtx"
    ),
    on_redirect=open_browser,
)

def connect():
    client = McpClient(McpClientOptions(name="vtx", version="1.0.0"))
    return client, client.connect(
        StreamableHttpTransport(
            StreamableHttpTransportOptions(
                url=server_url, auth_provider=adapt_oauth_provider(provider)
            )
        )
    )

client, connected = connect()
try:
    await connected
except McpAuthRequiredError:
    state = await provider.state()
    waiting = asyncio.create_task(callback.wait_for_callback(state))
    if await authorize_mcp(provider, OAuthFlowOptions(server_url=server_url)) == "REDIRECT":
        await waiting

client, connected = connect()
await connected
```

Inject an `OAuthStateStore` into `McpOAuthProvider` for durable credentials; `FileOAuthStateStore` is the default and keeps every server's tokens in `~/.vtx/mcp-auth.json`, one entry per server. The package does not open a browser or choose where credentials live -- the caller does both, which is how `/mcp signin` opens the URL and shows the result.

A 401, or a 403 whose challenge reports `insufficient_scope`, hands the transport an `UnauthorizedContext` carrying the rejected response and the token that was tried. A token that is no longer current means another request already refreshed it, so retrying beats refreshing again.

The OAuth implementation is adapted from the MIT-licensed Model Context Protocol TypeScript SDK v1.29.0, as are the resource tools. Everything else is original.

## Supported protocol surface

- MCP protocol version `2025-11-25`, accepting servers that negotiate `2025-06-18`, `2025-03-26`, or `2024-11-05`
- initialization and `notifications/initialized`
- ping
- paginated `tools/list`, `resources/list`, and `resources/templates/list`
- `tools/call`, including structured content, and `resources/read`
- progress notifications and timeout renewal
- request cancellation, in both directions
- Streamable HTTP sessions, the server-to-client GET stream with reconnection, and resumption of dropped response streams with `Last-Event-ID`
- stdio shutdown per the spec (close stdin, then SIGTERM, then SIGKILL), applied to the server's whole process group
- server `ping` and `roots/list` requests, the roots list re-read on every call
- `notifications/tools/list_changed` and other notifications through the generic notification API
- OAuth protected-resource and authorization-server discovery
- PKCE authorization code flow, dynamic client registration, token refresh, and step-up authorization for `insufficient_scope`

Batch JSON-RPC messages, legacy HTTP+SSE, acting as a server, sampling, and resource subscriptions are outside the core.

## Operational notes

- A stdio server is a child process holding a pipe. Leaking one orphans the process with the pipe open, so `McpManager.close()` is not optional. The child starts in its own session and is signalled by process *group*, so a server spawned through `npx` or `uvx` leaves no grandchildren holding the pipe. On Windows, where there are no process groups and no graceful signals, the child is terminated directly.
- An HTTP response whose body is an SSE stream is fetched with `client.send(..., stream=True)` and closed explicitly; leaving it inside an `async with` would close the stream the moment `send()` returns, and `send()` returns long before the server answers.
- Messages are capped at 16 MiB each at the transport layer. stdio stderr is captured up to 64 KiB and attached to connection errors.
- A listener that raises is reported and the transport keeps running; a bad listener cannot take down a connection.
- A 404 that names a session vtx was using raises `McpSessionExpiredError` rather than a generic HTTP error, because the recovery is to start a new session, not to retry.

## Testing

```bash
uv run --no-sync python -m pytest -p no:cacheprovider tests/mcp -q
```

`vtx.mcp.transports.in_memory` exports `create_in_memory_transport_pair()` for client and adapter tests. Delivery hops the event loop, which reproduces the asynchrony of a real transport, so a test that would race against an in-process delivery cannot pass by accident.

`tests/mcp` covers client correlation, timeouts, and cancellation; each transport; config validation; project trust; tool adaptation; resources; OAuth; and manager lifecycle. `tests/mcp/fixtures` holds the fake servers, `helpers.py` the shared wiring.

## Adding a transport

Implement `start`, `send`, `close`, and the three listener registrations -- or subclass `TransportEvents` and inherit the bookkeeping. Optional `set_protocol_version` lets an HTTP transport echo `MCP-Protocol-Version` back. Nothing in `client.py` should need to change.
