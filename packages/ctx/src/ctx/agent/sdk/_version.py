"""Mirror the installed ctx package version."""

from __future__ import annotations

from ctx.core.version import VERSION

__version__ = VERSION

__all__ = ["__version__"]
