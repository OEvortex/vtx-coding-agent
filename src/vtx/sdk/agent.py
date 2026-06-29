"""Agent — the SDK's unit of work.

An ``Agent`` bundles a model, instructions, tools, optional handoffs, and
optional guardrails. The :class:`Runner` orchestrates one or more
``Agent`` instances into a run.

Most users only need::

    agent = Agent(
        name="Tutor",
        instructions="You answer history questions concisely.",
        model="gpt-4o-mini",
        tools=[...],
    )

For multi-agent setups::

    triage = Agent(
        name="Triage",
        instructions="Route to the right specialist.",
        model="gpt-4o-mini",
        handoffs=[booking, refund],
    )
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar

from pydantic import BaseModel

from ..tools.base import BaseTool
from .guardrails import InputGuardrail, OutputGuardrail
from .handoffs import Handoff
from .tools import FunctionTool

# Keys recognized in the ``provider`` dict. Kept as a module constant so
# tests and the docstring can stay in sync with the resolution code in
# :meth:`Agent.resolve_provider`.
_PROVIDER_DICT_KEYS = frozenset(
    {
        "name",
        "sdk",
        "api_key",
        "base_url",
        "model",
        "max_tokens",
        "temperature",
        "thinking_level",
        "default_headers",
    }
)

# Avoid an import cycle with .runner: type-hint the runner.
if False:  # pragma: no cover
    pass

TContext = TypeVar("TContext")


@dataclass
class AgentOutputSchema:
    """Wraps a Pydantic model so the runner can validate the model's final output."""

    output_type: type[BaseModel]
    validate_strict: bool = False
    """When True, raise on any validation failure. Default is permissive."""

    def json_schema(self) -> dict[str, Any]:
        return self.output_type.model_json_schema()

    def validate(self, text: str) -> BaseModel:
        # Best-effort: extract a JSON object from the model's text reply.
        import json
        import re

        # Try direct parse first.
        try:
            data = json.loads(text)
            return self.output_type.model_validate(data)
        except Exception:
            pass
        # Look for a JSON object inside the text.
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return self.output_type.model_validate(json.loads(match.group(0)))
            except Exception:
                pass
        if self.validate_strict:
            raise ValueError(f"Model output did not match {self.output_type.__name__}: {text!r}")
        # Fallback: empty instance.
        return self.output_type.model_construct()


@dataclass
class Agent[TContext]:
    """A unit of work: model + instructions + tools + handoffs.

    Parameters
    ----------
    name:
        Human-readable name. Used in traces, tool descriptions, and the
        log when this agent becomes the active one.
    instructions:
        The system prompt. Either a static string or a function that
        receives the run context and returns a string.
    model:
        Model identifier (e.g. ``"gpt-4o-mini"``). The SDK resolves a
        provider from environment variables (or the ``provider`` arg).
    provider:
        The LLM provider. Accepts one of:

        * A :class:`vtx.llm.BaseProvider` instance (full control).
        * A dict. The dict's shape depends on whether the provider is
          built-in (declared in Vtx's ``provider.yaml``) or custom:

          - **Built-in**: just ``name`` (the provider slug from
            ``provider.yaml``) and ``api_key``. Plus any
            ``ProviderConfig`` field as override (e.g.
            ``thinking_level``, ``max_tokens``).

            ```python
            provider={
                "name": "openai",      # or "anthropic", "kilo", ...
                "api_key": "sk-...",
                # "thinking_level": "low",
                # "max_tokens": 4096,
            }
            ```

          - **Custom (non-builtin)**: ``name`` is your identifier,
            ``sdk`` is the SDK transport mode (``"openai"``,
            ``"anthropic"``, …), and ``base_url`` is the endpoint.

            ```python
            provider={
                "name": "my-local",
                "sdk": "openai",        # the SDK transport
                "api_key": "...",
                "base_url": "http://localhost:11434/v1",
            }
            ```

          Built-ins are looked up by ``name`` in Vtx's provider
          catalog. Custom providers are constructed from the
          explicit ``sdk`` + ``base_url`` and never touch the
          catalog.
        * ``None`` — the SDK resolves a provider from environment
          variables (using ``self.model`` for the model name).
    tools:
        LLM-callable tools. Each may be a :class:`BaseTool`, a
        :class:`FunctionTool` (from ``@tool``), a callable
        (auto-wrapped), or an :class:`Agent` (exposed as a tool).
    handoffs:
        Agents this agent can delegate to. Each may be an :class:`Agent`
        or a :class:`Handoff` (from :func:`handoff`).
    output_type:
        Optional Pydantic model. When set, the runner validates the
        final assistant text against this model and returns an instance.
    input_guardrails / output_guardrails:
        Lists of input/output guardrails (use the ``@input_guardrail`` /
        ``@output_guardrail`` decorators).
    tool_use_behavior:
        ``"run_llm_again"`` (default) or ``"stop_on_first_tool"``.
    metadata:
        Free-form dict for app-side bookkeeping.
    """

    name: str
    instructions: str | Callable[[Any], str] | None = None
    model: str | None = None
    provider: Any | dict[str, Any] | None = None
    tools: list[Any] = field(default_factory=list)
    handoffs: list[Any] = field(default_factory=list)
    output_type: type[BaseModel] | None = None
    input_guardrails: list[InputGuardrail] = field(default_factory=list)
    output_guardrails: list[OutputGuardrail] = field(default_factory=list)
    tool_use_behavior: str = "run_llm_again"
    metadata: dict[str, Any] = field(default_factory=dict)
    needs_approval_tools: set[str] = field(default_factory=set)
    """Tool names that should pause for human approval before running."""

    def __post_init__(self) -> None:
        if not self.name or not isinstance(self.name, str):
            raise ValueError("Agent.name must be a non-empty string")
        if self.tool_use_behavior not in ("run_llm_again", "stop_on_first_tool"):
            raise ValueError(
                f"Invalid tool_use_behavior: {self.tool_use_behavior!r}. "
                "Expected 'run_llm_again' or 'stop_on_first_tool'."
            )

    # ------------------------------------------------------------------
    # Tool / handoff compilation
    # ------------------------------------------------------------------

    def compiled_tools(self) -> list[BaseTool]:
        """Return a flat list of Vtx ``BaseTool`` instances for this agent.

        Resolves every item in :attr:`tools` to a ``BaseTool``: ``Agent``
        items are exposed as tools (manager pattern); raw callables are
        wrapped via :func:`tool`; ``BaseTool`` and
        ``FunctionTool`` pass through.
        """
        out: list[BaseTool] = []
        for item in self.tools:
            out.extend(self._coerce_to_tools(item))
        return out

    def _coerce_to_tools(self, item: Any) -> list[BaseTool]:
        # Direct tool instances.
        if isinstance(item, BaseTool):
            return [item]
        # Agents exposed as tools (manager pattern).
        if isinstance(item, Agent):
            return [item.as_tool()]
        # FunctionTool instances (skip — they're BaseTool subclasses).
        if isinstance(item, FunctionTool):
            return [item]
        # Raw callable - wrap.
        if callable(item):
            from .tools import tool

            return [tool(item)]
        raise TypeError(
            f"Agent tool items must be BaseTool, FunctionTool, Agent, or callable, "
            f"got {type(item).__name__}"
        )

    def compiled_handoff_tools(self) -> tuple[list[BaseTool], dict[str, Agent]]:
        """Return ``(tools, by_name)``: the handoff tools and the agent map.

        Each handoff becomes a tool the LLM can call; the tool's
        ``execute()`` records the target agent, and the runner switches
        the active agent when such a tool call is detected.
        """
        tools: list[BaseTool] = []
        by_name: dict[str, Agent] = {}
        for item in self.handoffs:
            handoff_obj: Handoff
            target: Agent
            if isinstance(item, Handoff):
                handoff_obj = item
                target = item.target_agent
            elif isinstance(item, Agent):
                target = item
                handoff_obj = Handoff(agent=item)
            else:
                raise TypeError(
                    f"Handoff items must be Agent or Handoff, got {type(item).__name__}"
                )
            tools.append(handoff_obj)
            by_name[handoff_obj.name] = target
        return tools, by_name

    def all_tools(self) -> tuple[list[BaseTool], dict[str, Agent], dict[str, BaseTool]]:
        """Combine tools and handoffs into one list plus lookup tables."""
        all_tools = self.compiled_tools()
        handoff_tools, handoff_targets = self.compiled_handoff_tools()
        all_tools.extend(handoff_tools)
        handoff_by_name = {t.name: t for t in handoff_tools}
        return all_tools, handoff_targets, handoff_by_name

    # ------------------------------------------------------------------
    # agents-as-tools
    # ------------------------------------------------------------------

    def as_tool(
        self,
        tool_name: str | None = None,
        tool_description: str | None = None,
        max_turns: int | None = None,
        custom_output_extractor: Callable[[Any], str] | None = None,
    ) -> BaseTool:
        """Expose this agent as a callable tool (manager pattern).

        When invoked, runs the agent synchronously on the supplied
        input and returns its ``final_output`` as the tool result. The
        parent agent stays in control.
        """
        from .tools import _AgentAsTool

        return _AgentAsTool(
            agent=self,
            tool_name=tool_name or self.name.lower().replace(" ", "_"),
            tool_description=tool_description or f"Ask the {self.name!r} agent.",
            max_turns=max_turns,
            custom_output_extractor=custom_output_extractor,
        )

    # ------------------------------------------------------------------
    # Cloning
    # ------------------------------------------------------------------

    def clone(self, **overrides: Any) -> Agent:
        """Return a shallow copy with the given field overrides."""
        from dataclasses import replace

        return replace(self, **overrides)

    # ------------------------------------------------------------------
    # System prompt
    # ------------------------------------------------------------------

    def resolve_instructions(self, context: Any = None) -> str:
        """Return the instructions for this agent, evaluated if callable."""
        if self.instructions is None:
            return ""
        if isinstance(self.instructions, str):
            return self.instructions
        if callable(self.instructions):
            return self.instructions(context)
        return str(self.instructions)

    def build_system_prompt(
        self, *, context: Any = None, tools: list[BaseTool] | None = None
    ) -> str:
        """Compose the system prompt for this agent.

        Layout (in order):

        1. The agent's ``instructions``.
        2. Tool-usage guidelines aggregated from each tool's
           ``prompt_guidelines`` tuple.
        3. A list of available tools, formatted as a compact reference.
        4. Output type instructions when ``output_type`` is set.
        """
        parts: list[str] = []
        instr = self.resolve_instructions(context)
        if instr:
            parts.append(instr)

        guidelines: list[str] = []
        for tool in tools or []:
            for line in tool.prompt_guidelines or ():
                guidelines.append(f"- {tool.name}: {line}")
        if guidelines:
            parts.append("# Tool usage\n" + "\n".join(guidelines))

        if self.output_type is not None:
            parts.append(
                "# Output format\n"
                f"Reply with a JSON object matching the {self.output_type.__name__} schema:\n"
                f"```json\n{self.output_type.model_json_schema()}\n```"
            )

        return "\n\n".join(parts)

    # ------------------------------------------------------------------
    # Provider resolution
    # ------------------------------------------------------------------

    def _provider_dict(self) -> dict[str, Any] | None:
        """Return ``self.provider`` as a dict, or ``None`` if it's a
        :class:`BaseProvider` (or ``None``)."""
        if self.provider is None:
            return None
        if isinstance(self.provider, dict):
            return dict(self.provider)
        return None

    def _is_builtin_provider(self, name: str) -> bool:
        """Return True if ``name`` is a Vtx built-in provider slug.

        Built-ins are listed in Vtx's ``provider.yaml`` (static catalog)
        and the dynamic catalog of gateways. Custom / non-builtin
        providers are user-supplied endpoints that don't appear in
        either.
        """
        from ..llm.dynamic_models import DYNAMIC_PROVIDERS
        from ..llm.provider_catalog import list_providers

        static_slugs = {p.slug for p in list_providers()}
        return name in static_slugs or name in DYNAMIC_PROVIDERS

    def resolve_provider(self) -> Any:
        """Build a Vtx :class:`BaseProvider` for this agent's model.

        Resolution order:

        1. ``self.provider`` if it's already a :class:`BaseProvider`
           instance — return it as-is.
        2. ``self.provider`` if it's a dict with ``name``. The
           resolution branches on whether the ``name`` is a built-in
           provider or a custom / non-builtin one:

           * **Built-in** (``name`` resolves in Vtx's catalog): the
             ``sdk`` field is ignored (the catalog knows the SDK
             mode). Only ``api_key``, ``base_url`` (optional override),
             and any other ``ProviderConfig`` field are honored.

           * **Custom** (``name`` is not in the catalog): ``sdk`` is
             **required** (it tells the SDK which transport to use),
             and ``base_url`` is required too.

        3. Otherwise (``self.provider`` is ``None``), fall back to env
           vars and ``self.model``.
        """
        from ..llm import BaseProvider as _BaseProvider

        if isinstance(self.provider, _BaseProvider):
            return self.provider

        from ..llm import ApiType, ProviderConfig, get_model, get_provider_class
        from ..llm.base import resolve_api_key

        cfg = self._provider_dict() or {}
        name = cfg.get("name")
        sdk = cfg.get("sdk")
        api_key = cfg.get("api_key")
        base_url = cfg.get("base_url")
        thinking_level = cfg.get("thinking_level", "low")
        max_tokens_override = cfg.get("max_tokens")
        temperature = cfg.get("temperature")
        default_headers = cfg.get("default_headers")
        model = self.model or cfg.get("model") or "gpt-4o-mini"

        # ------------------------------------------------------------------
        # Case 1: name is a Vtx built-in provider.
        # ------------------------------------------------------------------
        if name is not None and self._is_builtin_provider(name):
            # Look up the model info using the built-in name.
            info = get_model(model, name)
            if info is not None:
                api_type = info.api
                provider_slug = info.provider or name
            else:
                # Built-in name but model not in the static catalog.
                # Resolve via the provider slug.
                from ..llm.providers import resolve_provider_api_type

                api_type = resolve_provider_api_type(name)
                provider_slug = name

            api_key_resolved = resolve_api_key(
                api_key, env_vars=(), base_url=base_url, auth_mode="auto"
            )
            config = ProviderConfig(
                api_key=api_key_resolved,
                base_url=base_url or (info.base_url if info else None),
                model=model,
                max_tokens=(
                    max_tokens_override
                    if max_tokens_override is not None
                    else (info.max_tokens if info else None)
                ),
                temperature=temperature,
                thinking_level=thinking_level,
                provider=provider_slug,
                default_headers=default_headers or {},
            )
            return get_provider_class(api_type)(config)  # type: ignore[arg-type]  # type: ignore[arg-type]

        # ------------------------------------------------------------------
        # Case 2: name is NOT a built-in. It's a custom / non-builtin
        # provider — we need both ``sdk`` and ``base_url``.
        # ------------------------------------------------------------------
        if name is not None and sdk is None:
            raise ValueError(
                f"provider={{'name': {name!r}}} is not a Vtx built-in provider. "
                "For custom providers you must also pass an 'sdk' field "
                "(e.g. 'openai', 'anthropic') and a 'base_url'."
            )
        if name is not None and base_url is None:
            raise ValueError(
                f"provider={{'name': {name!r}, 'sdk': {sdk!r}}} is a custom provider. "
                "You must also pass a 'base_url'."
            )

        if sdk is not None and base_url is not None:
            # Build a custom provider from the SDK + base_url.
            from ..llm.providers import resolve_provider_api_type

            api_type = resolve_provider_api_type(sdk)
            api_key_resolved = resolve_api_key(
                api_key, env_vars=(), base_url=base_url, auth_mode="auto"
            )
            config = ProviderConfig(
                api_key=api_key_resolved,
                base_url=base_url,
                model=model,
                max_tokens=max_tokens_override,
                temperature=temperature,
                thinking_level=thinking_level,
                provider=name or sdk,
                default_headers=default_headers or {},
            )
            return get_provider_class(api_type)(config)  # type: ignore[arg-type]

        # ------------------------------------------------------------------
        # Case 3: No provider info at all. Fall back to env vars.
        # ------------------------------------------------------------------
        info = get_model(model, None)
        if info is not None:
            api_type: Any = info.api
            provider_slug = info.provider or "openai"
        else:
            from ..llm import ApiType

            api_type: Any = ApiType.OPENAI_COMPLETIONS
            provider_slug = "openai"

        api_key_resolved = resolve_api_key(
            api_key, env_vars=(), base_url=base_url, auth_mode="auto"
        )
        config = ProviderConfig(
            api_key=api_key_resolved,
            base_url=base_url or (info.base_url if info else None),
            model=model,
            max_tokens=(
                max_tokens_override
                if max_tokens_override is not None
                else (info.max_tokens if info else None)
            ),
            temperature=temperature,
            thinking_level=thinking_level,
            provider=provider_slug,
            default_headers=default_headers or {},
        )
        return get_provider_class(api_type)(config)  # type: ignore[arg-type]


__all__ = ["Agent", "AgentOutputSchema"]
