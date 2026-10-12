# MCP

MCP connects Vtx to external tool servers over the Model Context Protocol. This page covers the config format, the three transports, authentication including OAuth, the project trust model, how tools are exposed to the model, resource access, and the `/mcp` commands that manage it all.

The harness itself never imports `vtx.mcp`. The product layer wires a manager and registers each server's tools into the tool registry as ordinary `BaseTool` instances (`src/vtx/mcp/manager.py:500`), so a remote call flows through the same permission gate, argument validation, and TUI rendering as a local one (`src/vtx/mcp/tool.py:1-11`).

Read [codemode.md](codemode.md) for what `exposure: codemode` means in practice, [configuration.md](configuration.md) for the file settings this page shares, and [headless.md](headless.md) for how servers connect in a scripted run.

## Configuration

Vtx reads two files, both shaped the same:

| Scope | Path | Read when |
|---|---|---|
| Global | `~/.vtx/mcp.json` | Always |
| Project | `<cwd>/.vtx/mcp.json` | Only when the project is trusted (`src/vtx/mcp/config.py:309`) |

Both hold a single top-level `mcpServers` key. A project entry **replaces** a global entry with the same name; the project file is read second, so the replacement is the precedence rule (`src/vtx/mcp/config.py:289-291`).

```json
{
  "mcpServers": {
    "fs": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "."],
      "timeout": 60
    },
    "linear": {
      "url": "https://mcp.linear.app/mcp",
      "headers": {"Authorization": "Bearer $LINEAR_TOKEN"},
      "oauth": {"scope": "read write"},
      "exposure": "codemode",
      "tool_exposure": {
        "create_issue": "direct",
        "delete_*": "hidden"
      }
    },
    "disabled-one": {
      "command": "some-server",
      "enabled": false
    }
  }
}
```

Server names must match `[A-Za-z0-9_-]+` (`src/vtx/mcp/config.py:183`).

### Server fields

| Field | Type | Default | Notes |
|---|---|---|---|
| `command` | string | - | stdio launch. Mutually exclusive with `url` |
| `args` | string[] | `[]` | |
| `url` | string | - | HTTP launch. Mutually exclusive with `command` |
| `env` | object of strings | `{}` | `$VAR` and `${VAR}` expanded (`src/vtx/mcp/config.py:63`) |
| `cwd` | string | - | stdio only |
| `headers` | object of strings | `{}` | HTTP only, same `$VAR` expansion |
| `oauth` | object | `null` | HTTP only. See [Auth](#auth) |
| `enabled` | boolean | `true` | A disabled server is still listed so it can be re-enabled (`src/vtx/mcp/config.py:302`) |
| `timeout` | integer | `60` | Seconds |
| `exposure` | string | `null` | Server default. See [Tool exposure](#tool-exposure) |
| `tool_exposure` | object | `{}` | Per-tool overrides keyed by name or pattern |

Setting both `command` and `url`, or neither, fails with `set exactly one of "command" or "url"` (`src/vtx/mcp/config.py:189`). A stdio server that also declares `url`, `headers`, or `oauth` fails (`src/vtx/mcp/config.py:221`). An unset `$VAR` expands to empty rather than raising, so a missing optional token starts the server and lets it answer 401 (`src/vtx/mcp/config.py:120-127`).

Env and header expansion resolve later, not at parse time: call `config.resolved_env()` and `config.resolved_headers()` (`src/vtx/mcp/config.py:117-126`).

`expose` fields, not config: a value that is not one of the five exposures is reported as an error rather than silently coerced (`src/vtx/mcp/exposure.py:107-114`).

## The three transports

Pick a transport by how the server is described in config, not by an explicit choice. `McpServerConnection` decides from `config.is_stdio` (`src/vtx/mcp/manager.py:78`).

### stdio

Launches the server as a child process and speaks line-delimited JSON on its stdin/stdout (`src/vtx/mcp/transports/stdio.py:119`).

| `StdioTransportOptions` field | Default | Meaning |
|---|---|---|
| `command` | - | Executable |
| `args` | `[]` | Arguments |
| `cwd` | `None` | |
| `env` | `None` | The child's environment |
| `inherit_env` | `True` | `False` gives the child only `env`, not vtx's environment |
| `stderr` | `"pipe"` | `"inherit"` sends the server's stderr to vtx's own |
| `on_stderr` | `None` | Callback for captured stderr |
| `max_message_bytes` | `DEFAULT_MAX_MESSAGE_BYTES` | |
| `max_stderr_bytes` | `DEFAULT_MAX_STDERR_BYTES` | |
| `close_timeout_ms` | `DEFAULT_CLOSE_TIMEOUT_MS` | Between SIGTERM and SIGKILL on shutdown |

(`src/vtx/mcp/transports/stdio.py:103-118`)

### streamable HTTP

POSTs JSON-RPC to a URL, with an optional GET stream for server-initiated messages (`src/vtx/mcp/transports/streamable_http.py:146`).

| `StreamableHttpTransportOptions` field | Default | Meaning |
|---|---|---|
| `url` | - | The MCP endpoint |
| `headers` | `{}` | Sent on every request |
| `client` | `None` | Injected `httpx.AsyncClient`; one is created if absent |
| `open_get_stream` | `True` | Keep a GET stream open for server-initiated messages |
| `max_message_bytes` | `DEFAULT_MAX_MESSAGE_BYTES` | |
| `auth_provider` | `None` | See [Auth](#auth) |
| `reconnect` | `None` | `ReconnectOptions`: `initial_delay_ms`, `max_delay_ms`, `max_retries` |

HTTP failures are typed so the caller can react: `McpAuthRequiredError(www_authenticate, body)` for 401, `McpSessionExpiredError` for an expired session, and the generic `McpHttpError(status, message, body)` (`src/vtx/mcp/transports/streamable_http.py:53-78`).

### in-memory

A loopback pair for tests and for servers hosted inside your own process: `create_in_memory_transport_pair()` returns two connected `InMemoryTransport` instances (`src/vtx/mcp/transports/in_memory.py:18,65`).

## Auth

For a plain bearer token, use config:

```json
"headers": {"Authorization": "Bearer $MY_TOKEN"}
```

That covers static credentials and needs no code. The `AuthProvider` protocol exists for anything that must refresh, and the HTTP transport accepts one (`src/vtx/mcp/auth.py:29-34`):

```python
@runtime_checkable
class AuthProvider(Protocol):
    async def token(self) -> str | None: ...
    async def on_unauthorized(self, context: UnauthorizedContext) -> None: ...
```

`UnauthorizedContext` carries `response`, `server_url`, `token`, and `fetch` (`src/vtx/mcp/auth.py:14`). If `context.token` is already **different** from the token that just failed, a concurrent refresh won and there is nothing to do  --  the transport re-runs the request rather than re-authenticating (`src/vtx/mcp/auth.py:13-26`).

The transport re-authenticates on a 401, and on a 403 whose `WWW-Authenticate` advertises a scope, as a step-up.

### OAuth

`vtx.mcp.oauth` is a self-contained OAuth 2.1 client for remote servers, kept in its own package so a session that never authenticates never imports it (`src/vtx/mcp/oauth/__init__.py:3-4`). It implements the protected-resource and authorization-server metadata discovery from the MCP spec, adaptive discovery from the server URL when discovery itself is unavailable, PKCE, dynamic client registration, and token refresh.

In config, `oauth` carries the client settings for a server that does not support dynamic client registration. Without `client_id`, the flow registers a client with the authorization server first (`src/vtx/mcp/config.py:76-85`):

```json
"oauth": {
  "client_id": "abc123",
  "client_secret": "$CLIENT_SECRET",
  "scope": "read write",
  "callback_port": 8974
}
```

Credentials land in `~/.vtx/mcp-auth.json` through `FileOAuthStateStore` (`CREDENTIALS_FILENAME = "mcp-auth.json"`, `src/vtx/mcp/oauth/provider.py:45,98`). Swap in `MemoryOAuthStateStore` for tests (`src/vtx/mcp/oauth/provider.py:75,90`).

Let a remote server drive the browser by wiring the flow to a transport:

```python
from vtx.mcp.oauth import McpOAuthProvider, adapt_oauth_provider

transport = StreamableHttpTransport(
    StreamableHttpTransportOptions(
        url="https://mcp.example.com/mcp",
        auth_provider=adapt_oauth_provider(
            McpOAuthProvider(server_url="https://mcp.example.com/mcp"),
            open_url=open_in_browser,
        ),
    )
)
```

Or drive the whole thing yourself with `OAuthCallbackServer.listen()`, `authorize_mcp(provider, OAuthFlowOptions(...))`, `generate_pkce`, `exchange_authorization_code`, `refresh_authorization`, `register_client`, and `start_authorization` (`src/vtx/mcp/oauth/__init__.py:9-23`). Treat an authorization error as terminal only when it is one of `OAuthInsecureEndpointError` (a plaintext endpoint), `OAuthIssuerMismatchError` (the metadata issuer does not match the server), or `OAuthRegistrationError` (`src/vtx/mcp/oauth/__init__.py:52-59`).

## Trust

A project `mcp.json` can launch code, so Vtx never reads it implicitly. A project becomes trusted once per path, stored in `~/.vtx/trusted-projects.json` (`TRUST_STORE_FILENAME`, `src/vtx/mcp/trust.py:33`). `project_key(cwd)` is the `resolve()`d path, so a symlinked entry cannot smuggle in a different directory (`src/vtx/mcp/trust.py:36`). A corrupt store fails closed: it reads as empty and every project is untrusted (`src/vtx/mcp/trust.py:101-119`).

`/mcp trust` is deliberately two-step. The first press parses the project file **without** connecting to anything and lists exactly what it would start:

```text
This project would start:
  fs: npx -y @modelcontextprotocol/server-filesystem .
  linear: https://mcp.linear.app/mcp

Trust /home/me/proj/.vtx/mcp.json and start these? /mcp trust again to confirm.
```

(`src/vtx/tui/commands/mcp.py:218-243`). A single press that both showed the commands and granted would let anyone who could get one keystroke in run them (`src/vtx/tui/commands/mcp.py:227-229`). `inspect_project_config` is the function that makes this possible  --  it parses and validates but refuses to connect (`src/vtx/mcp/config.py:318-330`).

## Tool exposure

Every tool a server offers is reachable from a codemode script. Exposure controls whether it is also **declared to the model** as an ordinary tool, because declaring a two-hundred-tool server tool-by-tool costs two hundred definitions on every turn (`src/vtx/mcp/exposure.py:73-77`).

| Exposure | Declared to model | Listed in codemode catalog | Findable by in-sandbox search | Findable by `tool_search` |
|---|---|---|---|---|
| `direct` | yes | no | yes | no |
| `codemode` (default) | no | yes | yes | yes |
| `codemode-deferred` | no | no | yes | yes |
| `deferred` | no | no | yes | yes |
| `hidden` | no | no | no | no |

(`src/vtx/mcp/exposure.py:56-87`)

`direct` is script-callable but deliberately unlisted in the catalog: advertising it there would describe something the model already has in front of it (`src/vtx/mcp/exposure.py:63-70`). A deferred tool is unlisted but still findable, or "deferred" would mean "unfindable" and the only route left would be guessing the name (`src/vtx/mcp/exposure.py:82-86`).

The two discovery routes differ in what they can *do*. `tools.search` runs inside a script, so a tool it finds is callable in that script and nowhere else. `tool_search` runs as an ordinary tool call and **loads** its matches, so they are declared to the model on the next call -- which is the only way a `deferred` tool ever becomes a direct call. It is declared only when there is something to find: a session whose servers are all `direct` or `hidden` never sees it (`src/vtx/agent/runtime.py:770`).

An individual tool's exposure resolves exact-name-beats-pattern, then first-pattern-in-declaration-order among the patterns, then the server's own `exposure`, then `codemode` (`src/vtx/mcp/exposure.py:158-172`). The ordering rule is stated rather than left to dict order: `{"delete_*": "hidden", "*": "codemode"}` reads as "everything codemode except deletes", which is the opposite of what flipped iteration order would mean (`src/vtx/mcp/exposure.py:161-165`).

### Tool naming

A tool is named `mcp__<server>__<tool>`, sanitized to `[A-Za-z0-9_-]` and capped at 64 characters for provider limits. Sanitizing can collide (`a.b` and `a_b` both become `a_b`), so a name already taken or too long gets an 8-character hash suffix derived from the *original* server and tool, which keeps it unique rather than merely shorter (`src/vtx/mcp/tool.py:52-75`).

Results are capped at `MCP_OUTPUT_MAX_BYTES = 20 * 1024` and truncated in the middle so both ends survive (`src/vtx/mcp/tool.py:42`, `114-132`). A large opaque payload is written to a file and replaced with a resource link the model can read instead  --  strictly more useful than the first 20K of a dump (`src/vtx/mcp/tool.py:96-113`, `164-174`).

Two annotations change behavior rather than display: `readOnlyHint` makes the tool non-mutating so it skips the approval prompt, and `destructiveHint` forces one even under a permissive mode (`src/vtx/mcp/tool.py:8-11`).

## Resources

Three session-level tools let the model use a server's resources without declaring each URI (`src/vtx/mcp/resources.py:35-37`):

| Tool | Argument | Returns |
|---|---|---|
| `list_mcp_resources` | `server` | The server's resources |
| `list_mcp_resource_templates` | `server` | Its URI templates |
| `read_mcp_resource` | `server`, `uri` | The contents |

MCP App resources  --  `ui://` URIs, or a mime type carrying `profile=mcp-app`  --  are filtered out of resource listings because they are UI payloads, not model-readable content (`src/vtx/mcp/resources.py:44-55`). A resource tool spanning several servers resolves to its **widest** member's exposure, so a group never resolves to its narrowest (`src/vtx/mcp/exposure.py:90-105`).

## The `/mcp` commands

There is no `vtx mcp` CLI subcommand. MCP is managed from the TUI.

| Command | Aliases | Effect |
|---|---|---|
| `/mcp` | `list`, `ls` | Report every server, its state, and its tool count |
| `/mcp reload` | | Re-read `mcp.json` and reconnect |
| `/mcp reconnect <name>` | | Reconnect one server |
| `/mcp signin <name>` | `sign-in`, `login`, `auth` | Authorize a remote server; opens a browser |
| `/mcp signout <name>` | `sign-out`, `logout` | Discard the server's stored credentials |
| `/mcp trust` | | Two-step grant for this project's `mcp.json` |
| `/mcp untrust` | | Stop reading the project file |
| `/mcp enable <name>` | | Enable and save to its `mcp.json` |
| `/mcp disable <name>` | | Disable and save |

(`src/vtx/tui/commands/mcp.py:7-16`, `src/vtx/tui/commands/mcp.py:42-78`)

`/mcp signin` on a stdio server is refused with `runs as a local process, so there is nothing to sign in to` (`src/vtx/tui/commands/mcp.py:268-274`). The authorize URL is echoed either way: a browser is a convenience, the terminal is the channel that is certain to be readable (`src/vtx/tui/commands/mcp.py:277-285`). A sign-in that reached a redirect but never finished leaves `authorization_url` on the status so `/mcp` can show the link again (`src/vtx/tui/commands/mcp.py:104-113`).

An untrusted project file is reported as untrusted rather than silently ignored, so a checked-in `mcp.json` does not look like it had no effect (`src/vtx/tui/commands/mcp.py:89-97`).

## Statuses

`McpServerStatus` is what `/mcp` prints, and it never raises because it is a report (`src/vtx/mcp/manager.py:50-71`):

| Field | Meaning |
|---|---|
| `name` | Server name |
| `state` | `disabled`, `connected`, `connecting`, `needs-auth`, `error` |
| `error` | Failure detail |
| `tool_count` | Tools the server contributed |
| `instructions` | Server-provided usage instructions |
| `source` | Which `mcp.json` the entry came from |
| `scope` | `global` or `project` |
| `authorization_url` | The URL a pending sign-in needs |

`startup_wait_seconds` bounds `connect_all`. A server still connecting at the deadline reports `connecting`, keeps running in the background, and contributes its tools when it lands  --  one slow server must not hold up a session (`src/vtx/mcp/manager.py:452-457`, `500-514`).

## Worked example

Stand up a local filesystem server and a remote OAuth one, then connect from Python.

First `<repo>/.vtx/mcp.json`:

```json
{
  "mcpServers": {
    "fs": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "."],
      "timeout": 30,
      "tool_exposure": {"delete_file": "hidden"}
    },
    "linear": {
      "url": "https://mcp.linear.app/mcp",
      "exposure": "codemode-deferred",
      "tool_exposure": {"list_issues": "direct"}
    }
  }
}
```

Then connect and list the resulting tools:

```python
import asyncio

from vtx.mcp.manager import McpManager


async def main():
    # global `~/.vtx/mcp.json` plus `<cwd>/.vtx/mcp.json`, both validated on read
    manager = McpManager(cwd=".", project_trusted=True)
    print("config dir:", manager.config_dir)
    for message in manager.errors:
        print("config error:", message)
    tools = await manager.connect_all()

    for status in manager.statuses():
        print(f"{status.name} [{status.scope}] {status.describe()}")

    for tool in tools:
        print("tool:", tool.name, "| mutating:", tool.mutating)

    await manager.close()


asyncio.run(main())
```

In a TUI session the same configuration is reached with `/mcp trust` (twice), then confirmed with `/mcp`. In a CI run, codemode connects the servers before the agent starts and prints `mcp: N tool(s) from M server(s)` to stderr  --  see [headless.md](headless.md).

## Known gaps

- MCP is TUI-only. There is no `vtx mcp` subcommand, and no headless flag to sign in to a remote server; `/mcp signin` must be run from the interactive session first, after which `~/.vtx/mcp-auth.json` carries the tokens.
- `validate_exposures` reports the first bad field rather than all of them, so fixing one misconfigured `tool_exposure` may surface another on the next reload (`src/vtx/mcp/exposure.py:117-155`).
- `adapt_tool`-style OAuth wiring needs you to supply `open_url` or your own browser plumbing; nothing in `vtx.mcp` opens a browser on its own except the TUI command path (`src/vtx/tui/commands/mcp.py:277-285`).
