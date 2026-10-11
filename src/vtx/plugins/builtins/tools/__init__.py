"""The built-in tool rows.

Each file registers one tool on ``ctx.tools`` and declares nothing else. The
default tool set is a row list, not code, so removing a tool is deleting a row
(``- remove: [tool-grep]``) rather than editing a registry.
"""

from __future__ import annotations

from vtx.plugins.builtins.tools import (
    ask_user,
    bash,
    codemode,
    edit,
    find,
    grep,
    read,
    skill,
    tool_search,
    web,
    write,
)
from vtx.plugins.builtins.tools import subagent as subagent_mod

__all__ = [
    "ask_user",
    "bash",
    "codemode",
    "edit",
    "find",
    "grep",
    "read",
    "skill",
    "subagent_mod",
    "tool_search",
    "web",
    "write",
]
