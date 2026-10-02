"""Confined script execution where the only capability is calling injected tools.

The model writes one Python program. The program can call only the tools this
sandbox was given, sequence them, branch, loop, and run independent calls
concurrently. It has no filesystem, network, subprocess, or import authority
beyond a small standard-library allowlist, so the only way to affect anything is
through a tool the host chose to expose.

Three parts, each owning one side of the boundary:

- :mod:`vtx.ai.agent.codemode.host` -- :class:`CodemodeSandbox`. Owns the
  process, the deadline, the abort signal, and every tool.
- :mod:`vtx.ai.agent.codemode.sandbox` -- the sandbox process, launched by file
  path so it imports nothing from this package. Installs the audit hook and
  the import guard, then executes the script.
- :mod:`vtx.ai.agent.codemode.declarations` -- what the model reads: signatures
  plus the BM25 ranker that finds the ones the budget could not inline.

Authority is the host's, and only the host's. A program cannot gain authority
through prose or generated code; it can only exercise authority already present
in the supplied tools. Do not expose a broad tool and expect the prompt to
restrict it -- that rule is what the audit hook enforces on everything else.

Usage::

    sandbox = CodemodeSandbox(tools=[my_tool], limits=Limits(timeout_ms=30_000))
    result = await sandbox.execute("return await tools.my_tool(x=1)")
    if result.ok:
        print(result.value)
    else:
        print(result.diagnostic.kind, result.diagnostic.message)
    await sandbox.close()
"""

from __future__ import annotations

from vtx.ai.agent.codemode.declarations import (
    SearchMatch,
    is_mcp_result_schema,
    rank,
    render_declarations,
    render_signature,
    structured_content_schema,
    to_identifier,
)
from vtx.ai.agent.codemode.errors import (
    CodemodeError,
    HostUnavailable,
    InvalidInput,
    InvalidOutput,
    SandboxError,
    ScriptAborted,
    ScriptError,
    ScriptStalled,
    ScriptTimeout,
    ToolError,
    UnknownTool,
)
from vtx.ai.agent.codemode.governance import ToolGovernance, governed_invoker
from vtx.ai.agent.codemode.host import SANDBOX_PATH, CodemodeSandbox, truncate_middle
from vtx.ai.agent.codemode.integration import adapt_tool, adapt_tools, base_tool_schema
from vtx.ai.agent.codemode.source import (
    CODEMODE_SOURCE_GRAMMAR,
    CodemodeSourceError,
    SourceOptions,
    clamp_int,
    clamp_timeout,
    parse_source,
)
from vtx.ai.agent.codemode.types import (
    MAX_STORE_TOTAL_CHARS,
    MAX_STORE_VALUE_CHARS,
    CodemodeTool,
    Diagnostic,
    Limits,
    Result,
    ToolCall,
)

__all__ = [
    "CODEMODE_SOURCE_GRAMMAR",
    "MAX_STORE_TOTAL_CHARS",
    "MAX_STORE_VALUE_CHARS",
    "SANDBOX_PATH",
    "CodemodeError",
    "CodemodeSandbox",
    "CodemodeSourceError",
    "CodemodeTool",
    "Diagnostic",
    "HostUnavailable",
    "InvalidInput",
    "InvalidOutput",
    "Limits",
    "Result",
    "SandboxError",
    "ScriptAborted",
    "ScriptError",
    "ScriptStalled",
    "ScriptTimeout",
    "SearchMatch",
    "SourceOptions",
    "ToolCall",
    "ToolError",
    "ToolGovernance",
    "UnknownTool",
    "adapt_tool",
    "adapt_tools",
    "base_tool_schema",
    "clamp_int",
    "clamp_timeout",
    "governed_invoker",
    "is_mcp_result_schema",
    "parse_source",
    "rank",
    "render_declarations",
    "render_signature",
    "structured_content_schema",
    "to_identifier",
    "truncate_middle",
]
