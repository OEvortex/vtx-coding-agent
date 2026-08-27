"""Runtime environment for the Vtx coding agent.

Re-exports :class:`ConversationRuntime` and runtime helpers from
:mod:`vtx.ai.agent.runtime`.
"""

from __future__ import annotations

import sys

import vtx.ai.agent.runtime as _runtime

_current_module = sys.modules[__name__]
for _attr in dir(_runtime):
    setattr(_current_module, _attr, getattr(_runtime, _attr))

__all__ = [name for name in dir(_runtime) if not name.startswith("__")]
