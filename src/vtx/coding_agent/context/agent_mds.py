"""Back-compat alias for :mod:`vtx.ai.agent.context.agent_mds`."""

from __future__ import annotations

from vtx.ai.agent.context._xml import escape_xml
from vtx.ai.agent.context.agent_mds import (
    CONTEXT_FILE_CANDIDATES,
    ContextFile,
    _find_git_root,
    _get_stop_directory,
    _load_context_from_dir,
    formatted_agent_mds,
    load_agent_mds,
)

__all__ = [
    "CONTEXT_FILE_CANDIDATES",
    "ContextFile",
    "_find_git_root",
    "_get_stop_directory",
    "_load_context_from_dir",
    "escape_xml",
    "formatted_agent_mds",
    "load_agent_mds",
]
