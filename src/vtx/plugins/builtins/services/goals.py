"""``ctx.goals`` — the same-session objective."""

from __future__ import annotations

from vtx.plugins.plugin import plugin
from vtx.plugins.services import GoalService


@plugin(name="goals")
def apply(ctx, config):
    """The goal system stays in :mod:`vtx.agent.goal`; this row is the seam that
    lets a composition substitute a different objective model."""
    ctx.provide("goals", GoalService())


PLUGIN = apply
