"""Environment section for the system prompt.

Mirrors JARVIS's ``enhance_prompt_with_env_details`` helper: a small
``# Env`` block with the live execution context the model should know
about, including the working directory, project root, OS, Python
version, and the running vtx build.
"""

from __future__ import annotations

import contextlib
import platform
import sys
from datetime import datetime
from pathlib import Path

from ..version import VERSION as VTX_VERSION

ENV_HEADER = "# Env"


def _find_project_root(start: Path) -> Path:
    """Walk up from ``start`` until a ``.git`` directory is found.

    Falls back to the starting directory if no git root exists, which
    matches the behavior of JARVIS's ``get_project_root``.
    """
    current = start
    while current.parent != current and not (current / ".git").exists():
        current = current.parent
    return current


def _format_env_details(cwd: str) -> str:
    cwd_path = Path(cwd)
    project_root = _find_project_root(cwd_path)
    os_release = platform.system() or "unknown"
    with contextlib.suppress(Exception):
        os_release = f"{platform.system()} {platform.release()}".strip()

    return "\n".join(
        [
            f"- Date and time: {datetime.now().strftime('%A, %B %d, %Y at %I:%M %p %Z').strip()}",
            f"- Working directory: {cwd_path}",
            f"- Project root: {project_root}",
            f"- OS: {os_release}",
            f"- Python: {sys.version.split()[0]}",
            f"- Vtx version: {VTX_VERSION}",
        ]
    )


def build_env_section(cwd: str) -> str:
    """Return the ``# Env`` section for ``cwd``."""
    return f"{ENV_HEADER}\n\n{_format_env_details(cwd)}"


__all__ = ["ENV_HEADER", "build_env_section"]
