"""The ``codemode`` tool: run a script that composes tool calls.

Registered like any other built-in, but the interesting part is what it *is*.
Where every other tool is one operation, this is an interpreter: the model
hands it a program, and the program calls the other tools. The payoff is that
N operations cost one model turn instead of N, and that filtering,
aggregation, and fan-out happen in code rather than in context.

The sandbox is
:mod:`vtx.codemode` -- a subprocess running real Python, with the
user's own privileges and the whole standard library, into which the host
injects the tool bridge. The harness is not reachable from inside it, which is
what keeps "the script may only call the tools I injected" true of the harness
without pretending to be an operating-system boundary.

What this module owns is the *tool surface*: the parameter schema, the
model-facing description, and the wiring from the harness's tool list into the
sandbox. The sandbox itself knows nothing about tools, sessions, or the TUI.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable, Sequence
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from vtx.agent.tools.base import BaseTool
from vtx.codemode import CODEMODE_SOURCE_GRAMMAR, CodemodeSandbox, Limits, parse_source

# Aliased, because this module defines a harness tool also called CodemodeTool.
# Unaliased, the class below shadows the sandbox type and every annotation
# mentioning it silently refers to the wrong one.
from vtx.codemode import CodemodeTool as SandboxTool
from vtx.codemode.errors import (
    ABORTED,
    INVALID_INPUT,
    SANDBOX,
    SCRIPT,
    STALLED,
    TIMEOUT,
    UNKNOWN_TOOL,
)
from vtx.codemode.types import Diagnostic
from vtx.protocol.types import ImageContent, ToolResult

CODEMODE_TOOL_NAME = "codemode"


def _registered_tools() -> list[BaseTool]:
    from vtx.agent.tools import get_all_tools

    return list(get_all_tools().values())


def _exposure_of(tool: BaseTool) -> str | None:
    """A host tool's declared exposure, or ``None`` for a built-in.

    A missing attribute means "not an MCP tool", and built-ins are always
    listed. Read from the host tool because that is where the MCP adapter sets
    it; the sandbox tool inherits the answer through ``adapt_tools``.
    """
    return getattr(tool, "exposure", None)


def _reachable_from_script(tool: BaseTool) -> bool:
    """Whether a script may call this tool at all.

    A built-in has no exposure and is always reachable. An MCP tool is reachable
    only when its exposure says so, and ``hidden`` is the one value that means no
    -- everything else is either listed or findable.
    """
    from vtx.mcp.exposure import SCRIPT_CALLABLE

    exposure = _exposure_of(tool)
    return exposure is None or exposure in SCRIPT_CALLABLE


def _accepted_kwargs(tool: BaseTool, cancel_event: asyncio.Event | None) -> dict[str, Any]:
    """Only the kwargs ``tool.execute`` actually accepts.

    The turn loop does the same filtering. A tool that takes no ``cancel_event``
    would raise ``TypeError`` if handed one, and that would surface to the model
    as the tool failing rather than as a wiring detail.
    """
    kwargs: dict[str, Any] = {}
    try:
        parameters = inspect.signature(tool.execute).parameters
    except (TypeError, ValueError):
        return kwargs
    if cancel_event is not None and "cancel_event" in parameters:
        kwargs["cancel_event"] = cancel_event
    return kwargs


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
    STALLED: (
        "You awaited something that can never complete. Await a tool call, or do not await at all."
    ),
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
- `image(value)` appends an image you can see. It takes a base64 data URI or an
  image block taken straight from an MCP result.
- `store(key, value)` / `load(key)` carry JSON values between codemode calls;
  writes are kept only if the script returns successfully.
- A first line of `# @options: {{"max_tool_calls": 20}}` lowers your own budgets.
  You can ask for less than the limits below, never more.

The script is real Python: full standard library, installed packages, the
filesystem, subprocesses, and the network, running as the same user as any
command in this session. `print` and stderr are discarded — `text(value)` is the
only output the model reads. The running agent and its tools are not reachable
from inside the script, so every tool still goes through
`tools.<name>(...)`.

MCP tools, when connected, are grouped by server below and return a
`CallToolResult` rather than a string. Read `structuredContent` when it is
present, and check `isError` before trusting a result.

Side effects are real: if the script fails partway, earlier tool calls are not
undone. A tool that needs your user's approval cannot be called from here — call
it directly so they can approve it.

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
        self,
        *,
        timeout_ms: int | None = 30_000,
        catalog_budget_tokens: int = 2000,
        max_tool_calls: int | None = 200,
        max_output_tokens: int | None = 8_000,
        memory_limit_bytes: int | None = 512 * 1024 * 1024,
    ) -> None:
        self._timeout_ms = timeout_ms
        self._catalog_budget = catalog_budget_tokens
        self._limits = Limits(
            timeout_ms=timeout_ms,
            max_tool_calls=max_tool_calls,
            max_output_tokens=max_output_tokens,
            memory_limit_bytes=memory_limit_bytes,
        )
        # One sandbox for the session: the tool set only changes on a reload, and
        # the sandbox is reusable by design, so this is construction, not per-call.
        self._sandbox: CodemodeSandbox | None = None
        #: Identity of the tool set the cached sandbox was built from. MCP
        #: servers connect, disconnect, and gain and lose tools while a session
        #: runs, and a sandbox holding a stale list is invisible failure: a tool
        #: the model can see in its own tool list raises ``UnknownTool`` from
        #: inside a script. Comparing identities each turn is cheaper than
        #: remembering to invalidate.
        self._fingerprint: tuple[tuple[str, int], ...] = ()
        #: Supplies the live session tool list. Set by the runtime; ``None``
        #: means fall back to the global registry, which is right for a bare
        #: harness with no MCP.
        self.tool_source: Callable[[], Sequence[BaseTool]] | None = None
        #: Extensions, so nested calls emit the same hooks the model's own do.
        self.extensions: Any = None
        #: The host's permission gate. A script cannot answer a prompt, so a
        #: gated call is refused with an explanation rather than run.
        self.permission: Callable[[BaseTool, dict[str, Any]], Any] | None = None
        #: Cancellation for nested calls, shared with the turn that started it.
        self.cancel_event: asyncio.Event | None = None
        # Carried between calls. Host-owned, because the sandbox persists nothing.
        self._store: dict[str, Any] = {}

    # -- tool set ---------------------------------------------------------

    def session_tools(self) -> list[SandboxTool]:
        """Every tool this session can call, as sandbox tools.

        Excludes ``codemode`` itself: a script that could start a script could
        nest without limit, and the nested run would have no deadline of its own
        worth speaking of. ``bash`` is included deliberately -- it is the tool
        through which a script reaches the shell, and the permission gate has
        already ruled on it by the time the script runs.

        MCP tools are included when the session has any, which is the whole
        point of the exposure taxonomy: an MCP server's tools reach the model
        through a script rather than through the transcript, because a connected
        server can offer more tools than fit in a prompt.

        A tool marked ``hidden`` is excluded here, not merely unlisted. It is the
        one exposure that means *unreachable*; a tool that can still be called by
        name from inside a script is not hidden, it is just unadvertised, and
        treating the two the same would make the setting a suggestion that a
        model defeats by reading its own declarations.
        """
        from vtx.codemode.integration import adapt_tools
        from vtx.mcp.exposure import SCRIPT_LISTED

        source = self.tool_source
        tools = list(source()) if source is not None else list(_registered_tools())
        selected = [t for t in tools if t.name != CODEMODE_TOOL_NAME and _reachable_from_script(t)]
        return adapt_tools(
            selected,
            invoke=self._invoke,
            listed=lambda tool: _exposure_of(tool) in SCRIPT_LISTED or _exposure_of(tool) is None,
        )

    async def _invoke(self, tool: BaseTool, args: dict[str, Any]) -> Any:
        """Run one tool on the script's behalf, with the host's gates applied.

        Routes through :mod:`vtx.codemode.governance` so a call made
        from inside a script emits the same extension hooks, answers to the same
        permission decision, and is recorded the same way as a call the model
        made directly. Calling ``tool.execute`` here instead would be a way to
        do anything any exposed tool could, with no second look -- which is the
        one thing an approved ``codemode`` call must not be.
        """
        return await self._governed(tool, args)

    def _governed(self, tool: BaseTool, args: dict[str, Any]) -> Any:
        from vtx.codemode.governance import ToolGovernance

        async def run(target: BaseTool, arguments: dict[str, Any]) -> Any:
            params = target.params.model_validate(arguments)
            return await target.execute(params, **_accepted_kwargs(target, self.cancel_event))

        return ToolGovernance(
            tool,
            run=run,
            extensions=self.extensions,
            permission=self.permission,
            cancel_event=self.cancel_event,
        )(args, None)

    def _get_sandbox(self) -> CodemodeSandbox:
        tools = self.session_tools()
        fingerprint = tuple((tool.name, id(tool)) for tool in tools)
        if self._sandbox is None or fingerprint != self._fingerprint:
            self._sandbox = CodemodeSandbox(
                tools=tools,
                limits=self._limits,
                catalog_budget_tokens=self._catalog_budget,
                listed=[tool.name for tool in tools if tool.listed],
            )
            self._fingerprint = fingerprint
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
        self.cancel_event = cancel_event
        result = await sandbox.execute(
            source.code,
            store=self._store,
            timeout_ms=source.timeout_ms,
            max_tool_calls=source.max_tool_calls,
            max_output_tokens=source.max_output_tokens,
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
                images=_result_images(result) or None,
                ui_summary=_failure_summary(result, diagnostic),
            )

        # Commit the store only on success: a script that half-ran must not leave
        # state behind that the model believes was never written.
        result.apply_to_store(self._store)

        images = _result_images(result)
        parts = [item.get("text", "") for item in result.output if item.get("type") == "text"]
        value_repr = _render_value(result.value)
        if value_repr is not None:
            parts.append(value_repr)
        body = "\n".join(p for p in parts if p)
        if result.output_truncated:
            # Said out loud, because a silently shortened result is read as a
            # complete one and the model will reason from a table it never saw
            # the whole of.
            body = f"{body}\n\n[Output truncated to fit the model's context.]"
        return ToolResult(
            success=True, result=body, images=images or None, ui_summary=_success_summary(result)
        )


def _result_images(result: Any) -> list[ImageContent]:
    """Images the script emitted with ``image()``.

    This is how a picture gets from a tool, through a script, to the model. A
    tool that returns a screenshot used to be flattened to text before a script
    ever saw it, so a script had no way to forward one; ``image()`` accepts the
    block unchanged and it arrives as real image content.
    """
    images: list[ImageContent] = []
    for item in result.output:
        if item.get("type") != "image":
            continue
        data = item.get("data")
        if not isinstance(data, str) or not data:
            continue
        mime = item.get("mimeType")
        images.append(
            ImageContent(
                data=data, mime_type=mime if isinstance(mime, str) and mime else "image/png"
            )
        )
    return images


def _success_summary(result: Any) -> str:
    """The one-line TUI summary: how many calls, which, and how long.

    Names rather than just a count, because a script that failed after two of
    forty calls is a very different thing from one that made two calls, and the
    durations are what tell a model which call to avoid.
    """
    calls = result.calls
    base = f"Script ok ({len(calls)} tool calls)"
    if not calls:
        return base
    names = ", ".join(_call_label(call) for call in calls[:6])
    if len(calls) > 6:
        names += f", +{len(calls) - 6} more"
    return f"{base}: {names}"


def _call_label(call: Any) -> str:
    if call.ok:
        return call.name
    return f"{call.name} ({call.kind or 'error'})"


def _failure_summary(result: Any, diagnostic: Diagnostic) -> str:
    """The one-line TUI summary for a failed run.

    Includes the calls that already happened. A script that failed partway has
    usually already had real side effects, and a summary that says only "failed"
    leaves the user with no way to know whether the half that ran changed
    anything.
    """
    summary = f"Script failed ({diagnostic.kind})"
    done = [call for call in result.calls if not call.ok]
    if done:
        names = ", ".join(_call_label(call) for call in done[:4])
        return f"{summary}; failed calls: {names}"
    if result.calls:
        return f"{summary} after {len(result.calls)} calls (not undone)"
    return summary


def _render_value(value: Any) -> str | None:
    """The returned value as model-facing text, or ``None`` for nothing."""
    import json

    if value is None:
        return None
    # A returned string is the common case (`return "..."`). JSON-encoding it
    # escapes every newline to a literal \n and wraps the whole value in quotes,
    # so a script returning prose came back as one unreadable escaped line - and
    # the newlines were already gone before anything reached the renderer.
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, indent=2, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)


__all__ = ["CODEMODE_TOOL_NAME", "SEARCH_HELPER", "CodemodeParams", "CodemodeTool"]
