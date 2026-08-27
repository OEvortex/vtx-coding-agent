"""Configuration for the Vtx coding agent.

Re-exports configuration primitives and schema from :mod:`vtx.ai.config`.
"""

from __future__ import annotations

import sys
import vtx.ai.config as _cfg

# Populate this module's namespace with everything from vtx.ai.config (including private helpers)
_current_module = sys.modules[__name__]
for _attr in dir(_cfg):
    setattr(_current_module, _attr, getattr(_cfg, _attr))

__all__ = [name for name in dir(_cfg) if not name.startswith("__")]

