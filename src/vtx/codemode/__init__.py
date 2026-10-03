"""Script execution: the model writes one Python program, and it runs.

The program can call the tools this sandbox was given, sequence them, branch,
loop, and run independent calls concurrently -- and it can do everything else
Python can, because it runs as the same user as the agent: full standard
library, installed packages, files, subprocesses, network. What it cannot reach
is the host's *live state*: the running agent, the session, the TUI and the
injected tools live in the other process. (The ``vtx`` package itself is
importable -- it is installed -- so a script can read the harness's source; it
gets an unconnected module, not a running agent. That is the same arrangement
prime-agent's REPL uses to hand cells an ``emit()`` helper.)

Three parts, each owning one side of it:

- :mod:`vtx.codemode.host` -- :class:`CodemodeSandbox`. Owns the
  process, the deadline, the abort signal, and every tool.
- :mod:`vtx.codemode.sandbox` -- the script process, launched by file
  path so it imports nothing from this package. Reserves the protocol fds, then
  executes the script.
- :mod:`vtx.codemode.declarations` -- what the model reads: signatures
  plus the BM25 ranker that finds the ones the budget could not inline.

The process boundary does the work the removed confinement used to do, and it
does one thing the confinement could not: a deadline is a ``kill``. A script
stuck in ``while True:`` or a blocking syscall ends the same way.

Because a script can touch the filesystem directly, "expose only the tools you
want reachable" no longer constrains what a script can *do* -- only what it can
*do through the harness*. Keep the permission gate in front of tool calls
(:mod:`vtx.codemode.governance`), and treat the exposure decision as a
choice about the user's machine rather than a security control.

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

from vtx.codemode.declarations import (
    SearchMatch,
    is_mcp_result_schema,
    rank,
    render_declarations,
    render_signature,
    structured_content_schema,
    to_identifier,
)
from vtx.codemode.errors import (
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
from vtx.codemode.governance import ToolGovernance, governed_invoker
from vtx.codemode.host import SANDBOX_PATH, CodemodeSandbox, truncate_middle
from vtx.codemode.integration import adapt_tool, adapt_tools, base_tool_schema
from vtx.codemode.source import (
    CODEMODE_SOURCE_GRAMMAR,
    CodemodeSourceError,
    SourceOptions,
    clamp_int,
    clamp_timeout,
    parse_source,
)
from vtx.codemode.types import (
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
