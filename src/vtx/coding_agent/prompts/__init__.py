"""System prompt package for Vtx.

Re-exports system prompt builder and sections from :mod:`vtx.ai.agent.prompts`.
"""

from __future__ import annotations

import sys

import vtx.ai.agent.prompts as _prompts

_current_module = sys.modules[__name__]
for _attr in dir(_prompts):
    setattr(_current_module, _attr, getattr(_prompts, _attr))

__all__ = [name for name in dir(_prompts) if not name.startswith("__")]
