from __future__ import annotations

import os
import subprocess
import sys
from typing import Literal


def _find_executable(name: str) -> bool:
    try:
        subprocess.run(
            [name, "--version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True
        )
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


def _in_venv() -> bool:
    if os.environ.get("VIRTUAL_ENV"):
        return True
    return sys.prefix != sys.base_prefix


def _is_uv_tool(package: str) -> bool:
    try:
        proc = subprocess.run(["uv", "tool", "list"], capture_output=True, text=True, check=False)
        if proc.returncode == 0:
            for line in proc.stdout.splitlines():
                parts = line.strip().split()
                if parts and parts[0].lower() == package.lower():
                    return True
        return False
    except Exception:
        return False


def _is_pipx_tool(package: str) -> bool:
    try:
        proc = subprocess.run(["pipx", "list"], capture_output=True, text=True, check=False)
        if proc.returncode == 0:
            return package.lower() in proc.stdout.lower()
        return False
    except Exception:
        return False


def _installer_choice(
    package: str = "vtx-coding-agent",
) -> tuple[Literal["uv tool", "pipx", "uv", "pip"], list[str]]:
    if os.environ.get("VTX_UPDATE_USE_PIP"):
        return "pip", [sys.executable, "-m", "pip", "install", "--upgrade", package]
    if _find_executable("uv") and _is_uv_tool(package):
        return "uv tool", ["uv", "tool", "upgrade", package]
    if _find_executable("pipx") and _is_pipx_tool(package):
        return "pipx", ["pipx", "upgrade", package]
    if _find_executable("uv"):
        return "uv", ["uv", "pip", "install", "--upgrade", package]
    return "pip", [sys.executable, "-m", "pip", "install", "--upgrade", package]


def self_update(package: str = "vtx-coding-agent") -> tuple[bool, str]:
    installer, cmd = _installer_choice(package)

    try:
        result = subprocess.run(cmd, capture_output=True, check=False, text=True)
        if result.returncode == 0:
            stdout_lower = (result.stdout or "").lower()
            if (
                "nothing to upgrade" in stdout_lower
                or "requirement already satisfied" in stdout_lower
            ):
                return True, f"Already up to date ({installer})."
            return True, f"Updated successfully via {installer}."
        err = (result.stderr or result.stdout or "").strip()
        if err:
            return False, f"{installer} failed (exit code {result.returncode}): {err}"
        return False, f"{installer} exited with code {result.returncode}."
    except FileNotFoundError as exc:
        return False, f"Installer not found: {exc}"
    except Exception as exc:
        return False, str(exc)
