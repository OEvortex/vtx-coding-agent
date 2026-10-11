"""The built-in tool rows for harness-native tools.

Each file registers one tool on ``ctx.tools`` and declares nothing else. The
default tool set is a row list, not code, so removing a tool is deleting a row
(``- remove: [tool-grep]``) rather than editing a registry.

Product-specific tools are not here: a row that mounts a tool owned by a
product (the coding agent's file tools) lives with that product, so the
dependency points from the product into the plugin system rather than the
other way round. The ``tool-*`` row ids in the base bundle address whichever
package owns the tool.
"""

from __future__ import annotations

from vtx.plugins.builtins.tools import ask_user, goal, subagent, web

__all__ = [
    "ask_user",
    "goal",
    "subagent",
    "web",
]
