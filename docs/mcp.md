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
| `oauth` | http | `clientId`, `clientSecret`, `callbackPort`, `scope` — only needed without dynamic registration. |
| `enabled` | both | `false` keeps the entry without connecting it, so it can be re-enabled later. |
| `timeout` | both | Per-request timeout in seconds. Default `60`. Progress notifications reset it. |
| `exposure` | both | How this server's tools reach the model. Default `codemode`. See below. |
| `tool_exposure` | both | Per-tool overrides of `exposure`, by exact name or `*` pattern. |

### How a server's tools reach the model

A connected server can publish far more tools than fit in a prompt. Declaring
them all is unaffordable; declaring none makes the integration worthless. So
each server says how its tools are offered, and each tool can override it.

| `exposure` | Declared to the model | Callable from a script | Listed for the model |
| --- | --- | --- | --- |
| `direct` | yes | yes | — |
| `codemode` | no | yes | **yes** |
| `codemode-deferred` | no | yes | no |
| `deferred` | no | yes | no, but `tool_search` finds it |
| `hidden` | no | no | no |

`codemode` is the default. It is the setting that makes a large tool set usable:
the model writes one script that calls several of them, pays for one turn, and
the intermediate results never enter the transcript at all. See
[codemode](codemode.md).

`tool_exposure` overrides per tool, and is what makes a broad server usable with
care. Keys are tool names as the server offers them, or `*` patterns:

```json
{
  "mcpServers": {
    "docs": {
      "url": "https://example.com/mcp",
      "exposure": "codemode",
      "tool_exposure": {
        "delete_*": "hidden",
        "search": "direct",
        "reindex": "deferred"
      }
    }
  }
}
```

An exact name beats a pattern. Among patterns, the **first one in declaration
order** wins — so put specific patterns first. `{"*": "codemode", "delete_*":
"hidden"}` resolves deletes by the `*`, not by `delete_*`; written the other way
round it means what it looks like.

`{"exposure": "hidden", "tool_exposure": {"search": "codemode"}}` is the shape
for a server you mostly want switched off: everything unreachable except the
tools you name.

### A tool that needs approval

A script has nobody to ask, so a tool the permission gate would prompt for is
**refused** inside a script, with a message telling the model to call it directly
so the user can approve it. A script is a way to do the ungated calls together,
not a way to get a gated one done unseen. A server that marks its tools
`readOnlyHint` is never gated, so annotating honestly is what keeps a tool
usable from a script.

An unset `$VAR` expands to empty rather than failing, so a missing optional
token leaves the header empty and lets the server answer 401 — which vtx reports
as "needs sign-in" rather than crashing the session.

### Project files and trust

A project MCP server is a command vtx would **execute**. Reading one out of a
repository the user merely opened would mean running code they did not ask to
run, so `<project>/.vtx/mcp.json` is ignored until you trust the project. A
global `~/.vtx/mcp.json` is always read, because you wrote it yourself.

`/mcp` says so when a project file exists but is not trusted, rather than
leaving a checked-in `mcp.json` looking like it had no effect.

`/mcp trust` needs two presses. The first lists every server the file defines
and the exact command each one would run; the second grants trust. Trust here
means "run these commands", so a prompt that does not name them is not a
decision you can actually make. `/mcp untrust` revokes it.

A decision is recorded per resolved project path in
`~/.vtx/trusted-projects.json`, and applies to later sessions in that project
only. Nothing grants it implicitly — not on first use, not because a file is
small, and not from anything inside the project directory. A project cannot
trust itself. If the store is unreadable, every project reads as untrusted.

## Commands

| Command | Does |
| --- | --- |
| `/mcp` | list every server, its state, and its tool count |
| `/mcp reload` | re-read `mcp.json` and reconnect |
| `/mcp reconnect <name>` | rebuild one server's connection |
| `/mcp signin <name>` | authorize a remote server (opens a browser) |
| `/mcp signout <name>` | discard that server's stored credentials |
| `/mcp trust` | show what this project would run, then allow it |
| `/mcp untrust` | stop reading this project's `mcp.json` |
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

## Resources

A server can also publish *resources* — files, schemas, application state —
rather than tools. Three session-level tools make those reachable:

| Tool | Does |
| --- | --- |
| `list_mcp_resources` | list resources, from one server or all of them |
| `list_mcp_resource_templates` | list parameterized resources, such as file patterns |
| `read_mcp_resource` | read one resource by server and URI |

They appear only when at least one server is connected. The names are the ones
models already know from other coding agents, and they take a `server` argument
rather than existing per server, so three tools cover any number of servers.

Listing with a `server` returns one page plus a `cursor` for the next; listing
without one walks every page of every server and reports any server that failed
under `errors` rather than hiding the ones that worked. MCP App user
interfaces (`ui://` URIs and `profile=mcp-app` HTML) are filtered out — they are
pages for a host to render, not content for a model.

Reading gives text and images for a model to consume, and writes a binary
resource to a private temp file and names the path.

## Signing in to a remote server

A remote server that answers `401` reports `needs-auth` and contributes no
tools. `/mcp signin <name>` runs the full OAuth 2.1 authorization-code flow with
PKCE:

1. vtx discovers the authorization server from the protected-resource metadata
   (RFC 9728) at `/.well-known/oauth-protected-resource`, falling back to the
   server's own origin when it publishes none.
2. It registers itself dynamically (RFC 7591) unless `oauth.clientId` is set.
3. A loopback listener is bound and your browser is opened at the authorize URL.
   The URL is always printed too — a browser is a convenience, the terminal is
   the channel you can definitely read.
4. The redirect is received, the code is exchanged, and the tokens are written
   to `~/.vtx/mcp-auth.json` (mode 0600).

Afterwards the transport sends the bearer token on every request and refreshes it
transparently when a `401` comes back. A refresh token is used in preference to
another browser round trip, so a second `/mcp signin` usually does not open a
browser at all. Two `401`s arriving at once share one refresh, and a request
whose token was already replaced is retried rather than refreshed again — with
rotating refresh tokens, a second refresh would fail *and* discard the new
grant.

Credentials are per server URL. `~/.vtx/mcp-auth.json` holds every server's, and
a provider will not read an entry belonging to a different URL.

Some things are deliberately refused. Credentials are never sent to a
non-HTTPS endpoint, except a loopback redirect URI (RFC 8252 §8.3). An issuer
that does not match the one requested is a fatal error rather than something to
work around, because sending a client secret to whatever answered would be the
wrong outcome either way. A server that returns 401 but whose authorization
metadata cannot be found still reports `needs-auth` — not `failed` — since
signing in is still the thing to do.

### Servers without dynamic registration

Set these in the server's `mcp.json` entry:

```json
{
  "mcpServers": {
    "docs": {
      "url": "https://example.com/mcp",
      "oauth": {
        "clientId": "…",
        "clientSecret": "…",
        "callbackPort": 8765,
        "scope": "read write"
      }
    }
  }
}
```

`clientId` skips registration. `clientSecret` is sent with the token request;
`token_endpoint_auth_method` picks how. `callbackPort` pins the loopback port,
which a server with pre-registered redirect URIs usually requires. `scope`
overrides the server's advertised scopes.

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
- client-ID metadata documents as an alternative to dynamic registration
