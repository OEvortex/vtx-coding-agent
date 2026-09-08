# Tools Reference

OpenJarvis provides a rich ecosystem of native tools, bridging execution capabilities with safety constraints.

---

## 🧰 Native Tool Catalog

### 1. `exec` (`ExecTool`)
Runs shell commands within the current workspace with execution timeouts, output truncation, and environment isolation.
- **Parameters**:
  - `command` (*string*, required): Shell command line to execute.
  - `timeout` (*integer*, optional): Maximum execution seconds (default: 120).
- **Behavior**: Captures `stdout` and `stderr`, handles non-zero exit codes cleanly, and enforces workspace containment when `restrict_to_workspace` is enabled.

### 2. `apply_patch` (`ApplyPatchTool`)
Applies structured line-oriented patches or diffs across multiple files atomically.
- **Parameters**:
  - `patch` (*string*, required): Patch content formatted in standard or unified diff syntax.
- **Behavior**: Validates file paths, computes target chunks, verifies pre-conditions, and writes changes atomically.

### 3. `list_exec_sessions` & `write_stdin` (`exec_session.py`)
Provides interactive and daemon process control:
- `list_exec_sessions`: Lists all active background sessions, status, PID, and elapsed runtime.
- `write_stdin`: Writes inputs (or control signals) into a running exec session and polls the latest output buffer.

### 4. `cron` (`CronTool`)
Schedules automated reminders or recurring tasks:
- **Parameters**:
  - `action` (*string*, enum: `["add", "list", "remove", "pause", "resume"]`)
  - `schedule` (*string*, optional): Cron expression (e.g. `0 9 * * 1-5`) or ISO timestamp for one-time reminders.
  - `prompt` (*string*, optional): Prompt executed when the trigger fires.

### 5. `run_cli_app` (`CliAppsTool`)
Runs installed CLI-Anything compliant applications via controlled subprocesses:
- **Parameters**:
  - `app` (*string*, required): Name of the CLI application (e.g. `git`, `docker`, `kubectl`, or custom CLI app).
  - `argv` (*array of strings*, required): Arguments list passed directly to the binary without shell interpolation.

### 6. `generate_image` (`ImageGenerationTool`)
Generates high-resolution images via OpenRouter/OpenAI and stores them as local media artifacts:
- **Parameters**:
  - `prompt` (*string*, required): Visual generation prompt.
  - `aspect_ratio` (*string*, optional): e.g. `"1:1"`, `"16:9"`, `"9:16"`, `"4:3"`.
  - `image_size` (*string*, optional): e.g. `"1K"`, `"2K"`, `"4K"`.
  - `count` (*integer*, optional): Number of images (1-8).

### 7. `my` (`MyTool`)
Self-inspection tool allowing the agent to audit and dynamically modify its own runtime state:
- **Parameters**:
  - `action` (*string*, enum: `["check", "set"]`)
  - `key` (*string*, optional): Target attribute dot-path (e.g. `max_iterations`, `model_preset`, `workspace`).
  - `value` (*any*, optional): New value when `action="set"`.

### 8. MCP Wrappers (`mcp.py`)
Dynamic tool wrappers connecting OpenJarvis to Model Context Protocol servers:
- Automatically discovers tools, resources, and prompts exposed by stdio and SSE MCP servers.
- Sanitizes schemas for LLM provider compliance.

---

## 🛠️ Creating Custom OpenJarvis Tools

To define a new native tool, subclass `Tool`, declare schemas with `@tool_parameters`, and place the file in `src/vtx/openjarvis/tools/`:

```python
from typing import Any
from vtx.openjarvis.tools.base import Tool, tool_parameters
from vtx.openjarvis.tools.schema import StringSchema, IntegerSchema, tool_parameters_schema

@tool_parameters(
    tool_parameters_schema(
        query=StringSchema("Search query text", min_length=1),
        limit=IntegerSchema("Maximum number of results to return", minimum=1, maximum=50),
        required=["query"]
    )
)
class CustomSearchTool(Tool):
    """Search external knowledge base."""

    @property
    def name(self) -> str:
        return "custom_search"

    @property
    def description(self) -> str:
        return "Search an internal company knowledge base by query."

    async def execute(self, query: str, limit: int = 10, **kwargs: Any) -> str:
        # Implementation logic
        return f"Found {limit} results for '{query}'"
```

The tool will be automatically discovered on runtime startup by `ToolLoader`.

