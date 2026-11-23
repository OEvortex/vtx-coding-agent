"""Product-level prompt extras contributed through ``ctx.prompts``.

The harness ``prompts`` plugin provides an empty ``PromptService``; this row
adds the coding agent's dynamic sections (the live todo list) on top, via the
same ``add_section`` seam any product or extension can use.
"""

from __future__ import annotations

from ctx.plugins.plugin import plugin


def _render_todo_context(cwd: str, tools) -> str | None:
    """The live todo list, when ``llm.system_prompt.todo_context`` is on."""
    from ctx.core.config import config as ctx_config

    if not getattr(ctx_config.llm.system_prompt, "todo_context", False):
        return None
    from ctx.coding_agent.prompts.todo import build_todo_section

    return build_todo_section() or None


@plugin(name="coding-prompts", inject=("prompts",))
def apply(ctx, config) -> None:
    ctx.prompts.add_section("todo", _render_todo_context)


PLUGIN = apply
