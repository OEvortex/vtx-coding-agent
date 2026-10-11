"""``ctx.sessionProjections`` — folds over the committed session log."""

from __future__ import annotations

from typing import Any

from vtx.plugins.plugin import plugin
from vtx.plugins.services import Projection, ProjectionService


def _make_uses_projection() -> Projection:
    """Token usage per model role, summed from the log.

    Registered here rather than by the runtime so a host that does not agree
    can replace the row rather than patch the loop.
    """

    def init() -> dict[str, dict[str, int]]:
        return {}

    def apply(state: dict[str, dict[str, int]], event: Any) -> dict[str, dict[str, int]]:
        if getattr(event, "type", None) != "message":
            return state
        message = getattr(event, "message", None)
        usage = getattr(message, "usage", None)
        if usage is None:
            return state
        role = str(getattr(message, "role", "unknown"))
        bucket = state.setdefault(role, {})
        for field in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"):
            value = getattr(usage, field, None)
            if value:
                bucket[field] = bucket.get(field, 0) + int(value)
        return state

    return Projection(key="uses", init=init, apply=apply, state_version=1)


@plugin(name="sessionProjections", inject=("sessions",))
def apply(ctx, config):
    projections = ProjectionService()
    projections.register(_make_uses_projection())
    ctx.provide("sessionProjections", projections)


PLUGIN = apply
