"""Agent loader: import a ``.vtx/agent/<name>.py`` file and run its ``register``.

Mirrors :func:`vtx.extensions.load_extension`. The module must export a
top-level ``AGENT = AgentDef(...)`` constant; ``register(api)`` is optional.
"""

from __future__ import annotations

import importlib.util
import inspect
import logging
import sys
from pathlib import Path
from typing import Any

from ..extensions import AGENT_CHANGED
from .api import AgentAPI, LoadedAgent
from .schema import AgentDef

log = logging.getLogger("vtx.agents")


def _expected_stem(path: Path) -> str:
    """The agent name the file/package is expected to declare.

    For a single-file ``foo.py`` the stem is ``foo``; for a package
    ``foo/__init__.py`` the directory name is ``foo``.
    """
    if path.is_dir() or path.name == "__init__.py":
        return path.parent.name if path.name == "__init__.py" else path.name
    return path.stem


def load_agent(path: Path, *, cwd: str, config_dir: Path, on_event: Any = None) -> LoadedAgent:
    """Import a single agent file or package and read its ``AGENT`` constant.

    ``on_event`` is an optional callback invoked as
    ``on_event(event_name, handler)`` for every event handler the agent
    registers. The runtime passes a function that wires the handler into
    the active event bus. If ``on_event`` is None, handlers are kept on
    the :class:`LoadedAgent` and must be wired later by the caller.
    """
    expected = _expected_stem(path)

    module_name = f"vtx_agent_{abs(hash(path.as_posix()))}"
    spec = importlib.util.spec_from_file_location(module_name, str(path))
    if spec is None or spec.loader is None:
        raise AgentLoadError(f"Could not import agent at {path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise AgentLoadError(f"Agent {path} failed to import: {exc}") from exc

    agent_obj = getattr(module, "AGENT", None)
    if agent_obj is None:
        raise AgentLoadError(
            f"Agent {path} does not export a top-level `AGENT = AgentDef(...)` constant"
        )
    if not isinstance(agent_obj, AgentDef):
        # Allow a dict-like, in case the user constructs it without the
        # AgentDef type hint; convert and validate.
        try:
            agent_obj = AgentDef.model_validate(agent_obj)
        except Exception as exc:
            raise AgentLoadError(
                f"Agent {path}: `AGENT` must be an AgentDef "
                f"(got {type(agent_obj).__name__}): {exc}"
            ) from exc

    if agent_obj.name != expected:
        raise AgentLoadError(
            f"Agent {path}: AGENT.name={agent_obj.name!r} does not match "
            f"file/package name {expected!r}"
        )

    loaded = LoadedAgent(definition=agent_obj, path=path)
    api = AgentAPI(loaded, cwd=cwd, config_dir=config_dir, on_event=on_event)

    register = getattr(module, "register", None)
    if register is not None:
        if not callable(register):
            raise AgentLoadError(
                f"Agent {path}: `register` must be callable, got {type(register).__name__}"
            )
        try:
            result = register(api)
        except Exception as exc:
            raise AgentLoadError(f"Agent {path}: register(api) raised: {exc}") from exc
        if inspect.isawaitable(result):
            raise AgentLoadError(
                f"Agent {path}: async register() is not supported; use a sync function"
            )

    return loaded


def load_all_agents(
    *,
    cwd: str,
    configured: list[str] | None = None,
    agent_dir: Path | None = None,
    config_dir: Path | None = None,
    on_event: Any = None,
) -> tuple[list[LoadedAgent], list[str]]:
    """Discover and load every agent. Returns ``(loaded, errors)``.

    Errors are collected, not raised: one bad agent should not block the
    rest. Mirrors :func:`vtx.extensions.load_all_extensions`.
    """
    from ..config import get_config_dir
    from .discovery import find_agent_paths

    paths = find_agent_paths(cwd=cwd, configured=configured, agent_dir=agent_dir)
    loaded: list[LoadedAgent] = []
    errors: list[str] = []
    cfg_dir = config_dir or get_config_dir()
    for path in paths:
        try:
            loaded.append(load_agent(path, cwd=cwd, config_dir=cfg_dir, on_event=on_event))
        except AgentLoadError as exc:
            errors.append(str(exc))
    return loaded, errors


class AgentLoadError(RuntimeError):
    """Raised when an agent file cannot be loaded."""


__all__ = ["AgentLoadError", "load_agent", "load_all_agents"]


# Re-export the AGENT_CHANGED symbol here so callers that import from
# ``vtx.agents`` see a consistent surface.
_ = AGENT_CHANGED
