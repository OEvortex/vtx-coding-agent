"""Skills discovery, registration, and loading for vtx.

Re-exports all skills APIs and data structures from :mod:`vtx.ai.agent.context.skills`.
"""

from __future__ import annotations

import sys

import vtx.ai.agent.context.skills as _skills

_current_module = sys.modules[__name__]
for _attr in dir(_skills):
    setattr(_current_module, _attr, getattr(_skills, _attr))

__all__ = [name for name in dir(_skills) if not name.startswith("__")]
