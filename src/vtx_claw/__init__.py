"""Vtx-Claw: vtx-based project orchestration for LLMs."""

from __future__ import annotations

import tomllib
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from pathlib import Path


# ====================================================================================
# __version__, __logo__ — resolved lazily so circular-import clients (cli/command.py)
# can still ``from vtx_claw import __version__`` at module load time.
# ====================================================================================


def _read_pyproject_version() -> str | None:
    """Read the source-tree version when package metadata is unavailable."""
    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    if not pyproject.exists():
        return None
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    return data.get("project", {}).get("version")


def _resolve_version() -> str:
    try:
        return _pkg_version("vtx-claw")
    except PackageNotFoundError:
        return _read_pyproject_version() or "0.2.2"


__version__ = _resolve_version()
__logo__ = "🐈"


# ====================================================================================
# Lazy exports — avoid circular imports for modules that import us during their
# own module-level setup (e.g. ``cli/commands.py`` imports ``__version__``).
# ====================================================================================

_LAZY_EXPORTS: dict[str, str] = {}


def __getattr__(name: str) -> object:
    module_path = _LAZY_EXPORTS.get(name)
    if module_path is not None:
        from importlib import import_module

        mod = import_module(module_path, __name__)
        val = getattr(mod, name)
        globals()[name] = val
        return val
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["__version__", "__logo__", "VtxClaw"]
