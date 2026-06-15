# Tools

Vtx ships with **5 core tools** enabled by default. The full registry lives in [`src/vtx/tools/__init__.py`](../src/vtx/tools/__init__.py). Each tool is a Pydantic-typed async function exposed to the LLM as a JSON tool definition.

## Tool summary

| Tool | Mutating | Default | Icon | Purpose |
| --- | --- | --- | --- | --- |
| `read` | no | yes | (varies) | Read a file or directory, with pagination and image support |
| `edit` | yes | yes | `←` | Exact text replacement in a file |
| `write` | yes | yes | `+` | Create or fully overwrite a file |
| `bash` | yes | yes | `$` | Run a shell command |
| `find` | no | yes | (varies) | Glob-based file discovery |

## Common conventions

Every tool:

- Receives arguments as a **Pydantic model** (validated, type-checked, JSON-serialized to the model).
- Returns a `ToolResult` with `content`, `ui_summary`, and `ui_details` for the chat UI.
- Can be **cancelled** by a long-running agent cancellation event.
- Declares `mutating: bool` for the permission system. Non-mutating tools never trigger a confirmation prompt.
- Exposes a `prompt_guidelines` tuple that the system prompt picks up under `# Tool usage` — these are short hints that tell the model "use this tool for X, not that one for Y".

The tool block in the chat shows the icon, the tool name (in the theme's badge color), a one-line call summary, and the result summary. Long results are truncated and collapsible.

## `read`

Read a file or directory with pagination, or load an image and show it inline.

**Mutating:** no (read-only, never prompts).

**Parameters:**

| Name | Type | Required | Notes |
| --- | --- | --- | --- |
| `path` | str | yes | Absolute path of the file or directory. |
| `offset` | int | no | Line number to start at. Use for large files. |
| `limit` | int | no | Maximum lines to return. |

**Limits (in-process):**

- `MAX_CHARS_PER_LINE = 2000` (longer lines are truncated)
- `MAX_LINES_PER_FILE = 2000` (capped return per call)
- `MAX_DIRECTORY_ROWS = 1000`

**Behavior:**

- **Text files** — return numbered lines. Use `offset` + `limit` to read in chunks. If the path is a directory, returns a directory listing (respecting `.gitignore`).
- **Image files** — `.png`, `.jpg`, `.jpeg`, `.gif`, `.webp`, `.bmp` are decoded, resized to fit, and rendered inline. The model sees a `ImageContent` block. See `_read_image.py` for the resize rules.
- **Large output** — truncated with a `[... N more lines ...]` marker. Re-read with `offset` to continue.

**When to use:** always for files you haven't seen this session. Pair with `rg` (via bash) or the `find` tool to locate the right region first, then `read` with `offset` + `limit`.

## `edit`

Exact text replacement in a file. Best for surgical changes.

**Mutating:** yes (prompts in `prompt` mode).

**Parameters:**

| Name | Type | Required | Notes |
| --- | --- | --- | --- |
| `path` | str | yes | Absolute path. |
| `old_string` | str | yes | The text to replace. Must be unique unless `replace_all` is set. |
| `new_string` | str | yes | The replacement. Must differ from `old_string`. |
| `replace_all` | bool | no | Default `false`. If true, replaces every occurrence. |

**Behavior:**

- The tool reads the file, looks for `old_string`, and replaces it with `new_string`.
- `old_string` must match exactly (including whitespace). Read the file first to confirm the exact bytes.
- A 4-line context diff is rendered in the chat so you can verify what changed.
- The tool's preview during the approval prompt shows the proposed diff.

**When to use:** any change that's localized — rename a symbol, fix a typo, add a parameter. The model is instructed in the system prompt to prefer `edit` over `write` when only a small region changes.

## `write`

Create a new file or completely overwrite an existing one.

**Mutating:** yes (prompts in `prompt` mode).

**Parameters:**

| Name | Type | Required | Notes |
| --- | --- | --- | --- |
| `path` | str | yes | Absolute path. |
| `content` | str | yes | The full new file content. |

**Behavior:**

- Writes the file in a single call, atomically (temp file + rename on POSIX).
- Tracked in the file-change counter for the info bar.
- Shows a "[file created]" / "[file overwritten]" indicator in the chat.

**When to use:** new files, or when the change is so large that `edit` would need 20+ calls. The model is told not to use shell `echo` / heredoc / `cat` for writes.

## `bash`

Run a shell command. The workhorse tool for builds, tests, git, package managers, and ad-hoc scripts.

**Mutating:** depends on the command (see [permissions.md](permissions.md)).

**Parameters:**

| Name | Type | Required | Notes |
| --- | --- | --- | --- |
| `command` | str | yes | The full shell command line. |
| `cwd` | str | no | Working directory. Defaults to the project root. |
| `timeout` | int | no | Seconds. Default 180. |

**Limits (in-process):**

- `DEFAULT_TIMEOUT = 180` seconds
- `MAX_OUTPUT_BYTES = 50 * 1024` (50 KiB)
- `MAX_OUTPUT_LINES = 2000`

**Behavior:**

- The command is parsed with `shlex` (POSIX) to look for safe-command allowlist matches before the permission check fires.
- ANSI escape codes are stripped from the captured output before display.
- Long output is truncated with a marker showing how many lines/bytes were cut.
- Cancellation works through the agent's cancel event — the subprocess is sent `SIGINT`/`SIGTERM` cleanly.
- On Windows, signals are emulated via `subprocess.CREATE_NEW_PROCESS_GROUP`.

**When to use:** any operation that isn't a Vtx tool. Tests (`pytest`), builds, `git`, `npm install`, `docker`, etc. The model is told to avoid `cat`/`head`/`tail` (use `read`), file search via bash (use the `find` tool), and content search via bash (use `rg` directly — Vtx shells out to it for the `find` tool's content side and recommends it for fast shell text search).

## `find`

Glob-based file discovery. Wraps `fd` if available, falls back to `pathlib`.

**Mutating:** no (read-only, never prompts).

**Parameters:**

| Name | Type | Required | Notes |
| --- | --- | --- | --- |
| `pattern` | str | yes | Glob, e.g. `*.py`, `**/*.spec.ts`, `src/**/test_*.py`. |
| `path` | str | no | Directory to search. Defaults to the project root. |

**Limits:**

- `MAX_RESULTS = 100`
- `MAX_OUTPUT_BYTES = 20 * 1024`

**Behavior:**

- Respects `.gitignore` (when `fd` is available).
- Returns paths as absolute paths, with `~` shortened for display.

**When to use:** "where is this file?", "list all the tests", "find every config". Replaces `find ... | head` and `ls -R` in the agent's tool belt.

## Tool binaries

`find` will use `fd` if it's on `PATH` (with a `pathlib` fallback otherwise). Vtx also bundles a `rg` download — it's not used by any built-in tool, but the binary lands in `~/.vtx/bin/` for shell text search via `rg ...`. Vtx detects both at startup and prints a launch warning if either is missing (with a one-line install hint per platform). The `find` tool still works without `fd` — the Python fallback is slower but functionally equivalent.

You can also drop them into `~/.vtx/bin/` if you want them auto-discovered without touching the system `PATH`.

## Writing your own tool

The `BaseTool` interface is small. To add a new tool:

```python
# src/vtx/tools/my_tool.py
from pydantic import BaseModel, Field
from ..core.types import ToolResult
from .base import BaseTool


class MyParams(BaseModel):
    path: str = Field(description="Absolute path of the file to act on")


class MyTool(BaseTool):
    name = "my_tool"
    tool_icon = "*"
    mutating = True
    description = "One-line description the model sees in the tool list."
    params = MyParams
    prompt_guidelines = ("Use my_tool for X, not bash",)

    async def execute(self, params: MyParams, cancel_event=None) -> ToolResult:
        # do the work
        return ToolResult(content=..., ui_summary="...", ui_details=...)
```

Then register it in [`src/vtx/tools/__init__.py`](../src/vtx/tools/__init__.py) (`all_tools` and `DEFAULT_TOOLS`).

For the system-prompt side: `prompt_guidelines` lines get aggregated into the `# Tool usage` section of the system prompt. Keep each line short and concrete ("Use find to locate a file, not bash find / ls").

## Cancellation and timeouts

Every tool's `execute` accepts an `cancel_event: asyncio.Event` (passed by the runtime). When the user hits `Esc` mid-run, the event fires and the tool is expected to abort cleanly. The base implementation `_tool_utils.communicate_or_cancel` is the canonical way to wrap a subprocess call with cancellation. See the existing tools for the pattern.

For tools that spawn a long-running process (mostly `bash`), the `timeout` parameter caps the wall-clock time. Cancellation is independent — even before the timeout, an `Esc` will tear the subprocess down.
