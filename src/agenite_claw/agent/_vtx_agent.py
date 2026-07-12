"""Minimal bridge from claw's world to a ``vtx.sdk.Agent``.

This module is the only place where claw constructs the backend agent
loop primitives (``vtx.sdk.Agent`` / ``vtx.sdk.RunConfig``). It delegates
all loop orchestration to the sibling ``vtx`` package and contains no
loop logic of its own.
"""

from __future__ import annotations

from typing import Any

from vtx.sdk import Agent, RunConfig
from agenite_claw.providers.factory import ProviderSnapshot


def build_agent(
    *,
    provider_snapshot: ProviderSnapshot,
    system_prompt: str,
    tools: list[Any],
    name: str = "agenite-claw",
    instructions: str | None = None,
    model: str | None = None,
    **kwargs: Any,
) -> Agent:
    """Construct a ``vtx.sdk.Agent`` from claw's resolved world.

    Parameters
    ----------
    provider_snapshot:
        The resolved provider chain (provider + model) from
        :func:`agenite_claw.providers.factory.build_provider_snapshot`. Its
        ``.provider`` is a :class:`vtx.llm.BaseProvider` instance and
        ``.model`` is the model identifier.
    system_prompt:
        The agent's system prompt. Used as ``instructions`` unless
        ``instructions`` is explicitly provided.
    tools:
        LLM-callable tools, passed straight through to vtx (supports
        ``BaseTool``, ``@tool`` FunctionTools, callables, and Agents).
    name:
        Human-readable agent name.
    instructions:
        Optional override for the system prompt. Takes precedence over
        ``system_prompt`` when given.
    model:
        Optional model override; defaults to ``provider_snapshot.model``.
    **kwargs:
        Any remaining ``vtx.sdk.Agent`` fields (handoffs, output_type,
        input_guardrails, output_guardrails, tool_use_behavior, ...).
    """
    return Agent(
        name=name,
        instructions=instructions if instructions is not None else system_prompt,
        model=provider_snapshot.model or model,
        provider=provider_snapshot.provider,
        tools=tools,
        **kwargs,
    )


def build_run_config(*, max_turns: int | None = None, **kwargs: Any) -> RunConfig:
    """Construct a ``vtx.sdk.RunConfig`` for a single run.

    ``max_turns`` (maximum model turns before the run stops) is forwarded
    when set; any other ``vtx.sdk.RunConfig`` field may be passed via
    ``**kwargs``.
    """
    return RunConfig(max_turns=max_turns, **kwargs)
