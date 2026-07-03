# Tools

vtx-claw equips the agent with 18+ tools organized into categories. Tools are discovered via `ToolLoader` (`agent/tools/loader.py`) scanning `pkgutil` + `entry_points(group="vtx_claw.tools")`.

## Tool Categories

### File Operations

#### `read_file`

Read file contents with image support.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `path` | string | yes | Absolute file path |
| `offset` | int | no | Line offset for pagination |
| `limit` | int | no | Max lines to return |

#### `write_file`

Create or overwrite a file.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `path` | string | yes | Absolute file path |
| `content` | string | yes | File content |

#### `edit_file`

Edit a file by line range.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `path` | string | yes | Absolute file path |
| `start_line` | int | yes | Start line (1-indexed) |
| `end_line` | int | yes | End line (inclusive) |
| `new_content` | string | yes | Replacement content |

#### `list_dir`

List directory contents.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `path` | string | yes | Absolute directory path |
| `recursive` | bool | no | List recursively |

#### `apply_patch`

Apply structured file edits with diff stats.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `path` | string | yes | Target file path |
| `instructions` | list | yes | Edit instructions (add/replace/delete) |

### Search

#### `find_files`

Discover files by pattern.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `path` | string | no | Starting directory |
| `glob` | string | no | Glob pattern (e.g., `**/*.py`) |
| `type` | string | no | Type shorthand: `py`, `ts`, `js`, `json`, `md`, `go`, `rs`, `java`, `cpp`, `c`, `h`, `rb`, `php`, `sh`, `yaml`, `yml`, `toml`, `xml`, `html`, `css`, `sql` |
| `sort` | string | no | Sort by: `name`, `size`, `mtime` |
| `page` | int | no | Page number (pagination) |
| `page_size` | int | no | Results per page |

#### `grep`

Regex content search across files.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `pattern` | string | yes | Regex pattern |
| `path` | string | no | Directory to search |
| `include` | string | no | File glob filter |
| `output_mode` | string | no | `files_with_matches` (default), `content`, `count` |
| `context_lines` | int | no | Lines of context around matches |

### Shell Execution

#### `exec`

Run shell commands with workspace guards.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `command` | string | yes | Shell command |
| `timeout` | int | no | Timeout in seconds (default: 120) |
| `working_dir` | string | no | Working directory |
| `shell` | string | no | Shell override (sh/bash/zsh) |

Features:
- Workspace-scoped execution (prevents escape)
- Sandbox mode support
- Deny/allow pattern filtering
- Session mode for persistent shells

### Web

#### `web_search`

Search the web across 11 providers.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `query` | string | yes | Search query |
| `provider` | string | no | Provider override |

Supported providers: `duckduckgo`, `brave`, `tavily`, `searxng`, `jina`, `kagi`, `exa`, `olostep`, `bocha`, `volcengine`, `keenable`

#### `web_fetch`

Fetch and extract content from URLs.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `url` | string | yes | URL to fetch |
| `extract_mode` | string | no | `text`, `markdown`, `html` |

Features:
- SSRF protection (blocks private networks)
- Jina Reader integration for clean extraction
- Fallback to readability-lxml

### Messaging

#### `message`

Send messages across channels with media and inline keyboards.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `content` | string | yes | Message content |
| `channel` | string | no | Target channel |
| `chat_id` | string | no | Target chat |
| `media` | list | no | Media attachments |
| `buttons` | list | no | Inline keyboard buttons |

### Subagents

#### `spawn`

Create isolated subagent sessions.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `task` | string | yes | Task description |
| `label` | string | no | Human-readable label |
| `temperature` | float | no | Override temperature |

Subagents run in isolated sessions with their own history and context.

### Scheduling

#### `cron`

Manage scheduled jobs.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `action` | string | yes | `add`, `list`, `remove` |
| `message` | string | no | Message for agent (add) |
| `job_id` | string | no | Job ID (remove) |
| `every_seconds` | int | no | Repeat interval |
| `cron_expr` | string | no | Cron expression |
| `at` | string | no | ISO datetime for one-shot |
| `timezone` | string | no | Timezone |

### Self-Inspection

#### `my`

Inspect agent runtime state and configuration.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `action` | string | yes | `get` or `set` |
| `key` | string | no | State key |
| `value` | any | no | New value (set only) |

### Goals

#### `long_task`

Register a sustained objective on the session.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `goal` | string | yes | Goal description |
| `ui_summary` | string | no | User-facing summary |

#### `complete_goal`

Close an active goal with a recap.

### Image Generation

#### `generate_image`

Generate images via configured provider.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `prompt` | string | yes | Image description |
| `reference_images` | list | no | Reference images for editing |
| `aspect_ratio` | string | no | Aspect ratio |
| `image_size` | string | no | Output size |
| `count` | int | no | Number of images |

### CLI Apps

#### `cli_apps`

Run installed CLI applications.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | string | yes | App name |
| `args` | list | no | Command arguments |
| `json` | bool | no | Parse output as JSON |
| `working_dir` | string | no | Working directory |
| `timeout` | int | no | Timeout in seconds |

### MCP (Dynamic)

MCP servers configured in `tools.mcp_servers` are bridged as native tools with the `mcp_` prefix. See [mcp.md](mcp.md) for configuration.
