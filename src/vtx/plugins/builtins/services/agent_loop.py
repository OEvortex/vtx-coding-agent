"""``ctx.agentLoop`` — the driver behind the :class:`Agent` interface.

This is the row that makes "everything is a plugin" true of the loop itself: a
composition that provides its own ``agentLoop`` runs its own driver, and nothing
else in the harness changes. The default is ``vtx.agent.loop.Agent``.

The service is deliberately narrow: it builds an engine from the seams it is
handed and runs one query against it. Everything an engine needs — provider,
tools, session, prompt — arrives as an argument, so a plugin that swaps the loop
can also swap what it is given.
"""

from __future__ import annotations

from typing import Any

from vtx.plugins.plugin import plugin


class AgentLoopService:
    """``ctx.agentLoop`` — construct and run one agent engine."""

    def __init__(self) -> None:
        self._engines: list[Any] = []

    def create(self, provider: Any, tools: list[Any], session: Any, **kwargs: Any) -> Any:
        """Build an engine from the seams this composition resolved."""
        from vtx.agent.loop import Agent, AgentConfig

        config = kwargs.pop("config", None) or AgentConfig()
        engine = Agent(provider, tools, session, config=config, **kwargs)
        self._engines.append(engine)
        return engine

    async def run(self, engine: Any, query: str, **kwargs: Any) -> Any:
        """Drive one query through ``engine``, yielding its events."""
        return engine.run(query, **kwargs)

    def active(self) -> list[Any]:
        return list(self._engines)


@plugin(name="agentLoop", inject=("llm", "sessions"))
def apply(ctx, config):
    """Depends on ``llm`` and ``sessions`` so a provider or session swap lands
    before the loop is built: the loop reads both from the composition."""
    ctx.provide("agentLoop", AgentLoopService())


PLUGIN = apply
