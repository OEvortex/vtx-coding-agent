"""Re-export env prompt helpers from vtx.ai.agent.prompts.env for backward compatibility."""

from __future__ import annotations

import sys

from vtx.ai.agent.prompts import env as _impl

sys.modules[__name__] = _impl
