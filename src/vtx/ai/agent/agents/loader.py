"""Agent loader: import a ``.vtx/agent/<name>.py`` file and run its ``register``.

Mirrors :func:`vtx.extensions.load_extension`. The module must export a
top-level ``AGENT = AgentDef(...)`` constant; ``register(api)`` is optional.
"""

from __future__ import annotations

import importlib.util
import inspect
import logging
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from vtx.ai.agent.agents.api import AgentAPI, LoadedAgent
from vtx.ai.agent.agents.discovery import find_agent_paths
from vtx.ai.agent.agents.schema import AgentDef
from vtx.ai.agent.tools.base import BaseTool
from vtx.core.paths import get_config_dir

log = logging.getLogger("agent.agents")


def _expected_stem(path: Path) -> str:
    """The agent name the file/package is expected to declare.

    For a single-file ``foo.py`` the stem is ``foo``; for a package
    ``foo/__init__.py`` the directory name is ``foo``.
    """
    if path.is_dir() or path.name == "__init__.py":
        return path.parent.name if path.name == "__init__.py" else path.name
    return path.stem


def _wrap_callable_as_tool(fn: Callable[..., Any], fallback_name: str) -> BaseTool:
    """Use the SDK ``@tool`` machinery to wrap a plain callable."""
    from vtx.ai.agent.sdk.tools import tool as sdk_tool
    from vtx.ai.agent.tools.base import BaseTool

    raw = sdk_tool(fn, name=getattr(fn, "__name__", None) or fallback_name)
    assert isinstance(raw, BaseTool)
    return raw


def _coerce_raw_tools(raw: list[Any] | None) -> dict[str, BaseTool]:
    """Convert ``AgentDef.tools`` entries into ``BaseTool`` instances.

    Accepted entry types:

    * ``BaseTool`` instance — passed through.
    * Callable — wrapped via ``vtx.sdk.tools.tool``.
    * SDK ``Agent`` instance (duck-typed via ``as_tool()``) — converted to
      a manager-pattern tool.
    * Anything else raises :class:`AgentLoadError`.
    """
    from vtx.ai.agent.tools.base import BaseTool

    if not raw:
        return {}
    out: dict[str, BaseTool] = {}
    for idx, item in enumerate(raw):
        if item is None:
            continue
        if isinstance(item, BaseTool):
            tool = item
        elif callable(item):
            tool = _wrap_callable_as_tool(item, fallback_name=f"profile-tool-{idx}")
        elif hasattr(item, "as_tool") and callable(item.as_tool):
            tool = item.as_tool()
        else:
            raise AgentLoadError(
                f"tools[{idx}]: unsupported type {type(item).__name__} "
                f"(expected BaseTool, callable, or Agent)"
            )
        assert isinstance(tool, BaseTool)
        key = tool.name or f"tool-{idx}"
        out[key] = tool
    return out


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
        raise AgentLoadError(f"Could not import vtx.ai.agent at {path}")

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

    # Convert ``AgentDef.tools`` (raw callables / BaseTool / Agent) into
    # ``LoadedAgent.local_tools``. These participate in the same allow/deny
    # pipeline as ``register(api)`` tools.
    try:
        raw_tools = _coerce_raw_tools(agent_obj.tools)
    except AgentLoadError:
        raise
    except Exception as exc:
        raise AgentLoadError(f"Agent {path}: tools[] coercion failed: {exc}") from exc
    loaded.local_tools.update(raw_tools)

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
    # No built-in profiles. They used to be hardcoded here, which meant every
    # session carried a `plan` profile the user never asked for, and editing the
    # shipped list was the only way to change what the agent could be. Agents now
    # come solely from `.vtx/agent/<name>.py`; with none present the registry is
    # empty and the runtime falls back to its session-default behaviour, which is
    # the same path it already took when no agent was active.
    loaded_by_name: dict[str, LoadedAgent] = {}

    paths = find_agent_paths(cwd=cwd, configured=configured, agent_dir=agent_dir)
    errors: list[str] = []
    cfg_dir = config_dir or get_config_dir()
    for path in paths:
        try:
            agent = load_agent(path, cwd=cwd, config_dir=cfg_dir, on_event=on_event)
            loaded_by_name[agent.definition.name] = agent
        except AgentLoadError as exc:
            errors.append(str(exc))
    return list(loaded_by_name.values()), errors


class AgentLoadError(RuntimeError):
    """Raised when an agent file cannot be loaded."""


__all__ = ["AgentLoadError", "load_agent", "load_all_agents"]
