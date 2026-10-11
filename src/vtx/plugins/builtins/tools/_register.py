"""Register a tool on ``ctx.tools`` as a reversible effect.

Every tool row calls this rather than ``ctx.tools.register`` directly, so the
registration unwinds with the row: disabling ``tool-grep`` while vtx runs, then
re-enabling it, leaves exactly one ``grep`` tool instead of two.
"""

from __future__ import annotations

from typing import Any


def mount_tool(ctx: Any, tool: Any, **kwargs: Any) -> Any:
    """Register ``tool`` and arrange for the row's teardown to remove it."""
    instance = ctx.tools.register(tool, **kwargs)
    name = instance.name
    ctx.effect(lambda: ctx.tools.forget(name))
    return instance
