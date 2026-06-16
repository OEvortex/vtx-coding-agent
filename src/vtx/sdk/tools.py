"""
function_tool — decorator that turns a Python function into a Vtx ``BaseTool``.

The decorator is the SDK's user-facing tool surface. It:

* Derives a Pydantic model from the function's type hints
* Generates a JSON Schema the LLM can call
* Builds a Vtx ``BaseTool`` subclass with ``execute`` calling the function
* Supports sync and async callables
* Supports ``needs_approval=True`` for human-in-the-loop
* Supports per-tool input/output guardrails
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar, get_type_hints

from pydantic import BaseModel, Field, create_model

from ..core.types import ToolResult
from ..tools.base import BaseTool
from .approvals import ToolApprovalItem
from .guardrails.types import ToolGuardrailSpec, normalize_tool_guardrail_specs

F = TypeVar("F", bound=Callable[..., Any])
# Match ``Optional[X]``/``Union[X, None]`` and strip the ``None``.
_OPTIONAL_RE = re.compile(r"Optional\[(?P<inner>[^\]]+)\]|^Union\[(?P<inner2>[^\]]+),\s*None\]$")


def _strip_optional(annotation: str) -> str:
    m = _OPTIONAL_RE.match(annotation.strip())
    if m:
        return m.group("inner") or m.group("inner2") or annotation
    return annotation


def _function_to_params_model(func: Callable[..., Any]) -> tuple[type[BaseModel], dict[str, Any]]:
    """Build a Pydantic model from a function's signature."""
    sig = inspect.signature(func)
    try:
        hints = get_type_hints(func, include_extras=False)
    except Exception:
        hints = {}

    fields: dict[str, Any] = {}
    for name, param in sig.parameters.items():
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            # Skip *args/**kwargs; we expose a clean Pydantic surface.
            continue
        annotation = hints.get(
            name, param.annotation if param.annotation is not inspect.Parameter.empty else Any
        )
        default = param.default if param.default is not inspect.Parameter.empty else ...
        description = _docstring_for_arg(func, name)
        fields[name] = (annotation, Field(default=default, description=description))

    if not fields:
        # Provide a no-arg tool signature.
        fields["input"] = (str | None, Field(default=None, description="Optional input"))

    module = getattr(func, "__module__", "vtx.sdk")
    qualname = getattr(func, "__qualname__", getattr(func, "__name__", "FunctionTool"))
    model_name = f"{qualname.title().replace('.', '_').replace('_', '')}_Params"
    model = create_model(model_name, **fields)  # type: ignore[call-overload]
    return model, {"module": module, "name": qualname}


_DOCSTRING_PARAM_RE = re.compile(
    r"^\s*:param\s+(?P<name>\w+)\s*:\s*(?P<desc>.+?)(?=:param|:returns|:return|:rtype|$)",
    re.MULTILINE | re.DOTALL,
)
_DOCSTRING_ARGSECTION_RE = re.compile(
    r"(?:^|\n)\s*(?:Args|Arguments|Parameters)\s*:\s*\n(?P<body>(?:\s+.+\n?)+)", re.MULTILINE
)
_DOCSTRING_GOOGLESTYLE_RE = re.compile(
    r"^\s*(?P<name>\w+)\s*:\s*(?P<desc>.+?)(?=\n\s*\w+\s*:|\n\s*$|\Z)", re.MULTILINE | re.DOTALL
)


def _docstring_for_arg(func: Callable[..., Any], name: str) -> str | None:
    doc = inspect.getdoc(func)
    if not doc:
        return None
    # Sphinx :param:
    _DOCSTRING_PARAM_RE.search(doc)
    section = _DOCSTRING_PARAM_RE.search(doc)
    if section:
        for line in doc.splitlines():
            stripped = line.strip()
            if stripped.startswith(f":param {name}:"):
                return stripped[len(f":param {name}:") :].strip()
    # Google / NumPy style
    args_match = _DOCSTRING_ARGSECTION_RE.search(doc)
    if args_match:
        body = args_match.group("body")
        for match in _DOCSTRING_GOOGLESTYLE_RE.finditer(body):
            if match.group("name") == name:
                desc = match.group("desc").strip()
                desc = re.sub(r"\s+", " ", desc)
                return desc or None
    return None


def _function_description(func: Callable[..., Any]) -> str:
    doc = inspect.getdoc(func)
    if not doc:
        return f"Function: {getattr(func, '__name__', 'tool')}"
    summary = doc.strip().split("\n\n", 1)[0]
    summary = re.sub(r"\s+", " ", summary).strip()
    return summary or f"Function: {getattr(func, '__name__', 'tool')}"


@dataclass
class _FunctionToolSpec:
    name: str
    description: str
    func: Callable[..., Any]
    needs_approval: bool = False
    is_async: bool = False
    mutating: bool = True
    tool_icon: str = "→"
    input_guardrails: list[ToolGuardrailSpec] = field(default_factory=list)
    output_guardrails: list[ToolGuardrailSpec] = field(default_factory=list)


class FunctionTool(BaseTool):
    """A :class:`BaseTool` built from a Python function via ``@function_tool``."""

    _spec: _FunctionToolSpec
    wrapped_function: Callable[..., Any]
    """The original Python function the user decorated. Useful for
    introspection or calling the function directly outside the agent
    loop."""

    def __init__(self, spec: _FunctionToolSpec) -> None:
        self._spec = spec
        self.wrapped_function = spec.func
        self.name = spec.name
        self.description = spec.description
        self.params = _function_to_params_model(spec.func)[0]
        self.mutating = spec.mutating
        self.tool_icon = spec.tool_icon
        self.prompt_guidelines = ()
        self.needs_approval = spec.needs_approval
        self._input_guardrails = spec.input_guardrails
        self._output_guardrails = spec.output_guardrails

    def format_call(self, params: BaseModel) -> str:
        return _format_call_from_dict(params.model_dump(exclude_none=True))

    async def execute(
        self, params: BaseModel, cancel_event: asyncio.Event | None = None
    ) -> ToolResult:
        return await _execute_function_tool(self._spec, params, cancel_event)


def _format_call_from_dict(data: dict[str, Any]) -> str:
    if not data:
        return ""
    parts = []
    for k, v in data.items():
        s = str(v)
        if len(s) > 80:
            s = s[:77] + "..."
        parts.append(f"{k}={s}")
    return " / ".join(parts)


async def _execute_function_tool(
    spec: _FunctionToolSpec, params: BaseModel, cancel_event: asyncio.Event | None
) -> ToolResult:
    kwargs = _coerce_params_for_function(spec.func, params)
    try:
        result = spec.func(**kwargs)
        if inspect.isawaitable(result):
            coro = result
            if cancel_event is not None:
                task = asyncio.ensure_future(coro)
                cancel_task = asyncio.ensure_future(cancel_event.wait())
                try:
                    done, _ = await asyncio.wait(
                        {task, cancel_task}, return_when=asyncio.FIRST_COMPLETED
                    )
                    if task in done:
                        result = task.result()
                    else:
                        task.cancel()
                        return ToolResult(success=False, result="Tool execution was interrupted.")
                finally:
                    if not cancel_task.done():
                        cancel_task.cancel()
            else:
                result = await coro
    except Exception as exc:
        return ToolResult(success=False, result=f"Error: {exc}")

    if isinstance(result, ToolResult):
        return result
    if result is None:
        return ToolResult(success=True, result="(no output)")
    return ToolResult(success=True, result=str(result))


def _coerce_params_for_function(func: Callable[..., Any], params: BaseModel) -> dict[str, Any]:
    """Dump the params model and re-validate any nested Pydantic fields.

    ``params.model_dump()`` converts nested Pydantic models into
    ``dict``s. Functions that declare nested Pydantic models in their
    signature (e.g. ``def consume(n: Nested)``) need to receive the
    nested Pydantic instance, not a dict. This helper re-validates any
    such field.
    """
    try:
        sig = inspect.signature(func)
        globalns = getattr(func, "__globals__", {})
        localns: dict[str, Any] = {}
        # Pydantic model fields with ``$ref`` to ``$defs``/``definitions``
        # usually have the model class as a parameter annotation. Pull
        # forward references from the params model's class.
        # We rely on the params model's own model_fields annotation.
        annotations: dict[str, Any] = {}
        for name, field_info in params.__class__.model_fields.items():
            annotations[name] = field_info.annotation
        for name, param in sig.parameters.items():
            if name in annotations and annotations[name] is not None:
                continue
            if param.annotation is inspect.Parameter.empty:
                continue
            annotations[name] = param.annotation
        try:
            hints = get_type_hints(func, globalns=globalns, localns=localns, include_extras=False)
        except Exception:
            hints = annotations
    except Exception:
        hints = {}

    raw = params.model_dump(exclude_none=True)
    out: dict[str, Any] = {}
    for name, value in raw.items():
        annotation = hints.get(name)
        if (
            annotation is not None
            and isinstance(annotation, type)
            and issubclass(annotation, BaseModel)
            and isinstance(value, dict)
        ):
            out[name] = annotation.model_validate(value)
        else:
            out[name] = value
    return out


def function_tool[F: Callable[..., Any]](
    func: F | None = None,
    *,
    name: str | None = None,
    description: str | None = None,
    needs_approval: bool = False,
    mutating: bool = True,
    tool_icon: str = "→",
    input_guardrails: list[Any] | None = None,
    output_guardrails: list[Any] | None = None,
) -> Any:
    """Decorator that turns a Python function into a Vtx tool.

    Usage::

        @function_tool
        def get_weather(city: str) -> str:
            \"\"\"Return the current weather for a city.\"\"\"
            return f\"Sunny in {city}\"

    Optional arguments:
        * ``name`` — override the tool name (defaults to the function name)
        * ``description`` — override the docstring-derived description
        * ``needs_approval=True`` — pause for human approval before running
        * ``mutating=False`` — mark the tool read-only (no permission prompt)
        * ``input_guardrails=[...]`` / ``output_guardrails=[...]`` — attach
          tool-level guardrails (see :mod:`vtx.sdk.guardrails`)
    """

    def _wrap(target: F) -> FunctionTool:
        target_name = getattr(target, "__name__", "tool")
        tool_name = name or target_name
        tool_description = description or _function_description(target)
        is_async = inspect.iscoroutinefunction(target)
        spec = _FunctionToolSpec(
            name=tool_name,
            description=tool_description,
            func=target,
            needs_approval=needs_approval,
            is_async=is_async,
            mutating=mutating,
            tool_icon=tool_icon,
            input_guardrails=normalize_tool_guardrail_specs(input_guardrails or []),
            output_guardrails=normalize_tool_guardrail_specs(output_guardrails or []),
        )
        tool = FunctionTool(spec)
        # Copy useful attributes from the wrapped function for introspection.
        # We don't use functools.update_wrapper because ``tool`` is a
        # ``BaseTool`` subclass, not a callable.
        target_qualname = getattr(target, "__qualname__", None)
        if target_qualname:
            with contextlib.suppress(AttributeError, TypeError):
                tool.__class__.__qualname__ = target_qualname  # type: ignore[attr-defined]
        target_doc = getattr(target, "__doc__", None)
        if target_doc is not None:
            tool.__class__.__doc__ = target_doc  # type: ignore[attr-defined]
        return tool

    if func is not None:
        return _wrap(func)
    return _wrap


# ---------------------------------------------------------------------------
# Agent-as-tool: runs an Agent synchronously and returns its final output.
# ---------------------------------------------------------------------------


class _AgentAsTool(BaseTool):
    """A :class:`BaseTool` that runs a sub-agent and returns its final output.

    Created via :meth:`Agent.as_tool`. The parent agent stays in control;
    the sub-agent's run is isolated.
    """

    def __init__(
        self,
        agent: Any,
        tool_name: str,
        tool_description: str,
        max_turns: int | None,
        custom_output_extractor: Callable[[Any], str] | None,
    ) -> None:
        self._agent = agent
        self.name = tool_name
        self.description = tool_description
        self._max_turns = max_turns
        self._custom_output_extractor = custom_output_extractor
        self.mutating = False
        self.tool_icon = "↪"
        self.prompt_guidelines = ()

        from pydantic import create_model

        # The model calls this tool with a single ``input`` string.
        self.params = create_model(  # type: ignore[call-overload]
            f"Ask{agent.name.title().replace(' ', '')}_Params",
            input=(str, Field(..., description="The user's request to delegate.")),
        )

    def format_call(self, params: BaseModel) -> str:
        return f"→ {self._agent.name}"

    async def execute(
        self, params: BaseModel, cancel_event: asyncio.Event | None = None
    ) -> ToolResult:
        # Lazy import to avoid a circular import at module load.
        from .runner import Runner

        text = getattr(params, "input", "") or ""
        # Avoid running nested agent-as-tool loops (would create infinite recursion).
        from .run_config import RunConfig

        run_config = RunConfig(max_turns=self._max_turns) if self._max_turns else None
        try:
            result = await Runner.run(self._agent, text, run_config=run_config)
            output = result.final_output
            if self._custom_output_extractor is not None:
                output = self._custom_output_extractor(result)
            return ToolResult(success=True, result=str(output))
        except Exception as exc:
            return ToolResult(success=False, result=f"Agent-as-tool failed: {exc}")


__all__ = [
    "FunctionTool",
    "ToolApprovalItem",
    "_AgentAsTool",
    "_format_call_from_dict",
    "function_tool",
]
