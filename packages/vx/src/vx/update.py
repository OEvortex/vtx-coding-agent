"""``/update`` for vx itself.

Detects how this copy of vx was installed and upgrades that one -- a source
checkout is fast-forwarded with git, a tool install goes through ``uv tool`` or
``pipx``, and anything else falls back to ``pip install --upgrade vx``. The
package name and the escape hatch env var are vx's, so the command never
upgrades another product by accident.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Literal

PACKAGE = "vx"

#: Set to 1 to force the pip path (mirrors the ctx/ctx-code convention).
FORCE_PIP_ENV = "VX_UPDATE_USE_PIP"

Installer = Literal["source checkout", "uv tool", "pipx", "uv", "pip"]


def _find_executable(name: str) -> bool:
    try:
        subprocess.run(
            [name, "--version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True
        )
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, errors="replace", check=False)


def _is_uv_tool(package: str) -> bool:
    try:
        proc = _run(["uv", "tool", "list"])
        if proc.returncode != 0:
            return False
        for line in proc.stdout.splitlines():
            parts = line.strip().split()
            if parts and parts[0].lower() == package.lower():
                return True
    except Exception:
        pass
    return False


def _is_pipx_tool(package: str) -> bool:
    try:
        proc = _run(["pipx", "list"])
        if proc.returncode != 0:
            return False
        return package.lower() in proc.stdout.lower()
    except Exception:
        return False


def _source_checkout() -> Path | None:
    """The git root of a vx running from a working tree, if it is one."""
    # .../packages/vx/src/vx/update.py -> the checkout that owns it.
    for candidate in Path(__file__).resolve().parents:
        if (candidate / ".git").exists() and (candidate / "packages" / "vx").is_dir():
            return candidate
    return None


def install_choice(package: str = PACKAGE) -> tuple[Installer, list[str] | None]:
    """How this install should be upgraded, and the command (None = git pull)."""
    if os.environ.get(FORCE_PIP_ENV):
        return "pip", [sys.executable, "-m", "pip", "install", "--upgrade", package]
    if _find_executable("uv") and _is_uv_tool(package):
        return "uv tool", ["uv", "tool", "upgrade", package]
    if _find_executable("pipx") and _is_pipx_tool(package):
        return "pipx", ["pipx", "upgrade", package]
    root = _source_checkout()
    if root is not None:
        return "source checkout", None
    if _find_executable("uv"):
        return "uv", ["uv", "pip", "install", "--upgrade", package]
    return "pip", [sys.executable, "-m", "pip", "install", "--upgrade", package]


def self_update(package: str = PACKAGE) -> tuple[bool, str]:
    """Upgrade vx. Returns ``(ok, message)``; never raises."""
    installer, cmd = install_choice(package)
    if cmd is None:
        root = _source_checkout()
        if root is None:
            return False, "vx is running from source, but no git checkout was found."
        try:
            pull = _run(["git", "-C", str(root), "pull", "--ff-only"])
        except FileNotFoundError:
            return False, "git is not installed, so the checkout cannot be updated."
        except Exception as exc:
            return False, str(exc)
        if pull.returncode != 0:
            detail = (pull.stderr or pull.stdout or "").strip()
            return False, f"git pull failed (exit {pull.returncode}): {detail}"
        if "already up to date" in (pull.stdout or "").lower():
            return True, f"Already up to date ({installer})."
        return True, f"Updated via {installer}. Restart vx to load the new code."

    try:
        result = _run(cmd)
    except FileNotFoundError as exc:
        return False, f"Installer not found: {exc}"
    except Exception as exc:
        return False, str(exc)

    if result.returncode == 0:
        out = (result.stdout or "").lower()
        if "nothing to upgrade" in out or "requirement already satisfied" in out:
            return True, f"Already up to date ({installer})."
        return True, f"Updated successfully via {installer}."
    err = (result.stderr or result.stdout or "").strip()
    if err:
        return False, f"{installer} failed (exit code {result.returncode}): {err}"
    return False, f"{installer} exited with code {result.returncode}."


__all__ = ["PACKAGE", "install_choice", "self_update"]
