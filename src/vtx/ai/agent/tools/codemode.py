"""The ``codemode`` tool: run a confined script that composes tool calls.

Registered like any other built-in, but the interesting part is what it *is*.
Where every other tool is one operation, this is an interpreter: the model
hands it a program, and the program calls the other tools. The payoff is that
N operations cost one model turn instead of N, and that filtering,
aggregation, and fan-out happen in code rather than in context.

The sandbox is
:mod:`vtx.ai.agent.codemode` -- a subprocess with an audit hook, an import
allowlist, and a stripped ``__builtins__``, so "the script may only call the
tools I injected" is enforced rather than requested.

What this module owns is the *tool surface*: the parameter schema, the
model-facing description, and the wiring from the harness's tool list into the
sandbox. The sandbox itself knows nothing about tools, sessions, or the TUI.
"""

from __future__ import annotations

import asyncio
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from vtx.ai.agent.codemode import (
    CODEMODE_SOURCE_GRAMMAR,
    CodemodeSandbox,
    Limits,
    adapt_tools,
    parse_source,
)

# Aliased, because this module defines a harness tool also called CodemodeTool.
# Unaliased, the class below shadows the sandbox type and every annotation
# mentioning it silently refers to the wrong one.
from vtx.ai.agent.codemode import CodemodeTool as SandboxTool
from vtx.ai.agent.codemode.errors import (
    ABORTED,
    INVALID_INPUT,
    SANDBOX,
    SCRIPT,
    TIMEOUT,
    UNKNOWN_TOOL,
)
from vtx.ai.agent.codemode.types import Diagnostic
from vtx.ai.agent.tools.base import BaseTool
from vtx.core.types import ToolResult

CODEMODE_TOOL_NAME = "codemode"

#: The tool the script uses to discover the others. Named to match what the
#: sandbox injects, so the description and the implementation cannot disagree.
SEARCH_HELPER = "search"


class CodemodeParams(BaseModel):
    code: str = Field(
        description=(
            "A Python script, written as a function body: `return` at the top "
            "level works, and so does top-level `await`."
        )
    )


#: Recoveries worth spelling out, because each kind means a different next move.
#: A single "the script failed" would leave the model guessing.
_RECOVERY = {
    SCRIPT: "The traceback names a line in your own source; fix that line.",
    UNKNOWN_TOOL: (
        "No such tool here. Call `tools.search(...)` to list what exists — the "
        "catalog below may be partial."
    ),
    INVALID_INPUT: "The arguments were wrong. Re-read the signature and fix them.",
    TIMEOUT: (
        "It ran too long. Do less per call: fetch less, or split the work across "
        "several codemode calls."
    ),
    ABORTED: "You interrupted it.",
}

DESCRIPTION_TEMPLATE = """\
Run a Python script that calls this agent's tools.

Write the script as a function body. `return` the value you want back and `await`
any tool call, including several at once with `asyncio.gather`. Use it when a task
needs more than one tool call, or when the work between calls is filtering,
sorting, or aggregation — code does that for free instead of spending your context
on it.

{recovery}

Inside the script:
- `tools.<name>(**kwargs)` calls a tool. Its result comes back as plain JSON data.
- `tools.search(query=..., limit=...)` finds tools, when the list below is partial.
- `text(value)` appends to the output shown to you. `print` is discarded.
- `store(key, value)` / `load(key)` carry JSON values between codemode calls;
  writes are kept only if the script returns successfully.

The sandbox has no filesystem, network, subprocess, `eval`/`exec`/`compile`, or
`open`, and can only `import` a short standard-library allowlist. It has no
filesystem authority at all — every file operation is a tool call.

Side effects are real: if the script fails partway, earlier tool calls are not
undone.

{catalog}"""


class CodemodeTool(BaseTool):
    """Run a confined script that composes the agent's other tools."""

    name = CODEMODE_TOOL_NAME
    description = "Run a Python script that calls this agent's tools."
    params = CodemodeParams
    mutating = True
    """Scripts call other tools, which may mutate. Marked mutating so the
    permission gate covers what a script does, not just that it ran."""
    needs_approval = False
    tool_icon = "⟩"

    #: A capable model writes the script as raw text rather than as a
    #: JSON-escaped string, which is most of the value of grammar-constrained
    #: decoding here. OpenAI's Chat Completions path is the one wired today; a
    #: provider without an entry below is sent unconstrained.
    constrained_sampling: ClassVar[dict[str, Any]] = {
        "type": "grammar",
        "variants": {"openai": CODEMODE_SOURCE_GRAMMAR},
    }

    #: The parts of the sandbox contract worth stating as prose, beyond what the
    #: catalog already shows. Kept short: this lands in the prompt every turn.
    prompt_guidelines = (
        "When a task needs several tool calls, or filtering between them, write one "
        "`codemode` script instead of chaining tool calls.",
        "Inside a `codemode` script, aggregate and filter in Python rather than "
        "calling a tool to do it, and use `asyncio.gather` for independent calls.",
        "`tools.search` lists the tools available to a script; use it before "
        "concluding a capability is missing.",
    )

    def __init__(
        self, *, timeout_ms: int | None = 30_000, catalog_budget_tokens: int = 2000
    ) -> None:
        self._timeout_ms = timeout_ms
        self._catalog_budget = catalog_budget_tokens
        # One sandbox for the session: the tool set only changes on a reload, and
        # the sandbox is reusable by design, so this is construction, not per-call.
        self._sandbox: CodemodeSandbox | None = None
        # Carried between calls. Host-owned, because the sandbox persists nothing.
        self._store: dict[str, Any] = {}

    def sandbox_tools(self) -> list[SandboxTool]:
        from vtx.ai.agent.tools import get_all_tools

        """The harness tool set, as sandbox tools.

        Excludes ``codemode`` itself: a script that could start a script could
        nest without limit, and the nested run would have no deadline of its own
        worth speaking of. ``bash`` is included deliberately — it is the tool
        through which a script reaches the shell, and the permission gate has
        already ruled on it by the time the script runs.
        """
        return adapt_tools(
            [t for name, t in get_all_tools().items() if name != CODEMODE_TOOL_NAME]
        )

    def _get_sandbox(self) -> CodemodeSandbox:
        if self._sandbox is None:
            self._sandbox = CodemodeSandbox(
                tools=self.sandbox_tools(),
                limits=Limits(timeout_ms=self._timeout_ms),
                catalog_budget_tokens=self._catalog_budget,
            )
        return self._sandbox

    def refresh(self) -> None:
        """Rebuild the sandbox after the tool set changes.

        Called on reload. Without it a newly added tool stays invisible to
        scripts until the session ends, which reads as the reload not having
        worked.
        """
        self._sandbox = None

    @property
    def store(self) -> dict[str, Any]:
        return self._store

    def clear_store(self) -> None:
        """Drop the session store. For ``/clear``, where carrying state across
        would silently re-inject results from the previous conversation."""
        self._store.clear()

    # -- description ------------------------------------------------------

    def build_description(self) -> str:
        """Render the model-facing description for the *current* tool set.

        Computed on demand rather than at construction because the tool set
        changes on reload, and a description listing tools that no longer exist
        is worse than no catalog at all.
        """
        sandbox = self._get_sandbox()
        return DESCRIPTION_TEMPLATE.format(
            recovery="\n".join(f"- {msg}" for msg in _RECOVERY.values()),
            catalog=f"Tools available to a script:\n\n{sandbox.instructions()}",
        )

    def format_call(self, params: CodemodeParams) -> str:
        first = params.code.strip().split("\n", 1)[0]
        return f"script: {first[:60]}" if first else "script: (empty)"

    def format_preview(self, params: CodemodeParams) -> str | None:
        """The script itself, at approval time.

        The script is the whole of what it will do, so approving a one-line
        summary would approve something the user cannot see. Capped because the
        approval widget is a scrollback panel, not a file viewer.
        """
        text = params.code.strip()
        return text if len(text) <= 4000 else text[:4000] + "\n# ... truncated"

    # -- execution --------------------------------------------------------

    async def execute(
        self, params: CodemodeParams, cancel_event: asyncio.Event | None = None
    ) -> ToolResult:
        try:
            source = parse_source(params.code)
        except Exception as exc:
            return ToolResult(
                success=False,
                result=f"Could not read the script: {exc}",
                ui_summary="Invalid script header",
            )

        sandbox = self._get_sandbox()
        result = await sandbox.execute(
            source.code,
            store=self._store,
            timeout_ms=source.timeout_ms or self._timeout_ms,
            signal=cancel_event,
        )

        if not result.ok:
            # `ok=False` guarantees a diagnostic, but the type cannot see that, and
            # the fallback keeps a malformed result from raising out of `execute`.
            diagnostic = result.diagnostic or Diagnostic(
                kind=SANDBOX, message="Unknown script failure"
            )
            recovery = _RECOVERY.get(diagnostic.kind, "")
            body = [f"{diagnostic.kind}: {diagnostic.message}"]
            if diagnostic.stack:
                body.append(diagnostic.stack)
            if recovery:
                body.append(recovery)
            return ToolResult(
                success=False,
                result="\n".join(body),
                ui_summary=f"Script failed ({diagnostic.kind})",
            )

        # Commit the store only on success: a script that half-ran must not leave
        # state behind that the model believes was never written.
        result.apply_to_store(self._store)

        parts = [item.get("text", "") for item in result.output if item.get("type") == "text"]
        value_repr = _render_value(result.value)
        if value_repr is not None:
            parts.append(value_repr)
        calls = ", ".join(c.name for c in result.calls)
        summary = f"Script ok ({len(result.calls)} tool calls)"
        return ToolResult(
            success=True,
            result="\n".join(p for p in parts if p),
            ui_summary=f"{summary}: {calls}" if calls else summary,
        )


def _render_value(value: Any) -> str | None:
    """The returned value as model-facing text, or ``None`` for nothing."""
    import json

    if value is None:
        return None
    try:
        return json.dumps(value, indent=2, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)


__all__ = ["CODEMODE_TOOL_NAME", "SEARCH_HELPER", "CodemodeParams", "CodemodeTool"]
