"""Agent context loaders.

Re-exports resolve lazily through :func:`__getattr__` so importing a light
submodule (e.g. ``vtx.agent.context.skills`` for skill registration) does not
pay for ``agent_mds``/``git`` and their httpx graph.
"""

from __future__ import annotations

from typing import Any

_EXPORTS = {
    "Context": ".loader",
    "ContextFile": ".agent_mds",
    "Skill": ".skills",
    "formatted_agent_mds": ".agent_mds",
    "formatted_git_context": ".git",
    "formatted_skills": ".skills",
    "formatted_skills_index": ".skills",
    "load_agent_mds": ".agent_mds",
    "load_skills": ".skills",
}


def __getattr__(name: str) -> Any:
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    module = importlib.import_module(module_name, __name__)
    return getattr(module, name)


__all__ = [
    "Context",
    "ContextFile",
    "Skill",
    "formatted_agent_mds",
    "formatted_git_context",
    "formatted_skills",
    "formatted_skills_index",
    "load_agent_mds",
    "load_skills",
]
