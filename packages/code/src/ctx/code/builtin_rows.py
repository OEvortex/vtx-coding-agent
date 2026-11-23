"""Contributes the coding agent's built-in tool rows to the harness bundles.

The harness's ``BASE``/``MINIMAL`` bundles stay product-neutral; this module
appends the coding toolset rows at ``ctx.code`` import time, which is
the same eager side-effect slot ``register_skills_package`` already uses.
"""

from __future__ import annotations

from ctx.plugins.builtins import BASE, MINIMAL
from ctx.plugins.loader import Row

#: The coding agent's tool rows, addressed at their owning product modules.
CODING_TOOL_ROWS: list[Row] = [
    Row(id="tool-read", name="ctx.code.tools.read:PLUGIN"),
    Row(id="tool-apply-patch", name="ctx.code.tools.apply_patch:PLUGIN"),
    Row(id="tool-bash", name="ctx.code.tools.bash:PLUGIN"),
    # ``find`` removed: bash covers file discovery better for models.
    Row(id="tool-grep", name="ctx.code.tools.grep:PLUGIN"),
    Row(id="tool-skill", name="ctx.code.tools.skill:PLUGIN"),
    Row(id="tool-todo", name="ctx.code.tools.todo:PLUGIN"),
    # Generic agent tools the coding product also ships: web access, the
    # user-questionnaire tool, and sub-agent dispatch (built on the harness's
    # ``ctx.agent.subagent`` machinery).
    Row(id="tool-web", name="ctx.code.tools.web:PLUGIN"),
    Row(id="tool-ask-user", name="ctx.code.tools.ask_user:PLUGIN"),
    Row(id="tool-subagent", name="ctx.code.tools.subagent:PLUGIN"),
    # Registered but not default-on: the harness declares ``tool_search`` only
    # when the session has tools it has not been offered (see
    # ``ConversationRuntime.declared_tools``), and ``codemode`` is what makes the
    # script-reachable ones reachable.
    Row(id="tool-search", name="ctx.code.tools.tool_search:PLUGIN"),
    Row(id="tool-codemode", name="ctx.code.tools.codemode:PLUGIN"),
    Row(id="coding-prompts", name="ctx.code.prompts_extras:PLUGIN"),
    # External-server and confined-execution seams: the implementations ship
    # beside the coding toolset that consumes them.
    Row(id="mcp", name="ctx.mcp.plugin:PLUGIN"),
    Row(id="sandbox", name="ctx.codemode.plugin:PLUGIN"),
]

_MINIMAL_TOOL_ROWS: list[Row] = [
    Row(id="tool-read", name="ctx.code.tools.read:PLUGIN"),
    Row(id="tool-bash", name="ctx.code.tools.bash:PLUGIN"),
]

_registered = False


def register_builtin_tool_rows() -> None:
    """Append the coding tool rows to ``BASE`` and ``MINIMAL`` (idempotent)."""
    global _registered
    if _registered:
        return
    _registered = True
    # Presentation flair for the coding toolset (status quips, activity
    # verbs); registers itself into ``ctx.agent.tools`` on import.
    import ctx.code.tools.flair  # noqa: F401

    BASE.extend(CODING_TOOL_ROWS)
    MINIMAL.extend(_MINIMAL_TOOL_ROWS)
