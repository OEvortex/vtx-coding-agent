from __future__ import annotations

import os
import subprocess
import sys
from typing import Literal


def _installer_choice() -> Literal["uv", "pip", "none"]:
    if os.environ.get("VTX_UPDATE_USE_PIP"):
        return "pip"
    return "uv" if _find_executable("uv") else "pip"


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


def self_update(package: str = "vtx-coding-agent") -> tuple[bool, str]:
    installer = _installer_choice()

    if installer == "uv":
        cmd = ["uv", "pip", "install", "--upgrade", package]
    else:
        cmd = [sys.executable, "-m", "pip", "install", "--upgrade", package]

    try:
        result = subprocess.run(cmd, check=False, text=True)
        if result.returncode == 0:
            return True, f"Updated successfully via {installer}."
        return False, f"{installer} exited with code {result.returncode}."
    except FileNotFoundError as exc:
        return False, f"Installer not found: {exc}"
    except Exception as exc:
        return False, str(exc)
