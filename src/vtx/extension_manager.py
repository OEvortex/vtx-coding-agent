"""
Extension manager: install, list, and uninstall vtx extensions from PyPI
or local paths.

An extension package can expose its extensions/agents via:

1. Entry points:
   - ``vtx.extensions`` -> module path (``my_pkg.ext:register``)
   - ``vtx.agents`` -> module path (``my_pkg.agent:AGENT``)

2. Package layout discovery (fallback):
   - ``<package>/vtx_extensions/*.py`` or ``<package>/vtx_extensions/__init__.py``
   - ``<package>/vtx_agent/*.py`` or ``<package>/vtx_agent/__init__.py``
"""

from __future__ import annotations

import logging
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .config import get_config_dir

log = logging.getLogger("vtx.extension_manager")

INSTALLED_EXTENSIONS_FILE = "installed_extensions.yml"


@dataclass
class InstalledExtension:
    name: str
    source: str  # pip package name or local path
    version: str = ""
    extensions: list[str] = field(default_factory=list)
    agents: list[str] = field(default_factory=list)


def _get_installed_path() -> Path:
    return get_config_dir() / INSTALLED_EXTENSIONS_FILE


def _load_installed() -> dict[str, InstalledExtension]:
    import yaml

    path = _get_installed_path()
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        out = {}
        for name, info in data.items():
            if isinstance(info, dict):
                out[name] = InstalledExtension(
                    name=name,
                    source=info.get("source", ""),
                    version=info.get("version", ""),
                    extensions=list(info.get("extensions", [])),
                    agents=list(info.get("agents", [])),
                )
        return out
    except Exception:
        return {}


def _save_installed(installed: dict[str, InstalledExtension]) -> None:
    import yaml

    path = _get_installed_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        name: {
            "source": ext.source,
            "version": ext.version,
            "extensions": ext.extensions,
            "agents": ext.agents,
        }
        for name, ext in installed.items()
    }
    path.write_text(yaml.safe_dump(data, default_flow_style=False), encoding="utf-8")


def _run_uv_pip_install(package: str) -> tuple[bool, str]:
    """Run ``uv pip install <package>`` and return (success, output)."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "uv", "pip", "install", package],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode == 0:
            return True, result.stdout
        return False, result.stderr or result.stdout
    except FileNotFoundError:
        return False, "uv not found; install uv first"
    except subprocess.TimeoutExpired:
        return False, f"timeout installing {package}"
    except Exception as exc:
        return False, str(exc)


def _find_package_location(package: str) -> Path | None:
    """Find the installed location of a pip package."""
    mod_name = package.replace("-", "_")
    try:
        py_code = f"import {mod_name}; print({mod_name}.__file__)"
        result = subprocess.run(
            [sys.executable, "-c", py_code], capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            return Path(result.stdout.strip().split("__init__.py")[0])
    except Exception:
        pass
    # Fallback: try importlib.metadata
    try:
        from importlib.metadata import distribution

        dist = distribution(package)
        if dist.locate_file("").exists():
            return Path(str(dist.locate_file("")))
    except Exception:
        pass
    return None


def _discover_entry_points(package_location: Path) -> tuple[list[str], list[str]]:
    """Discover extension and agent entry points from a package."""
    extensions: list[str] = []
    agents: list[str] = []
    try:
        import tomllib

        pyproject = package_location / "pyproject.toml"
        if pyproject.exists():
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            eps = data.get("project", {}).get("entry-points", {})
            for mod_path in eps.get("vtx.extensions", {}).values():
                extensions.append(mod_path)
            for mod_path in eps.get("vtx.agents", {}).values():
                agents.append(mod_path)
    except Exception:
        pass
    return extensions, agents


def _discover_package_layout(package_location: Path) -> tuple[list[str], list[str]]:
    """Discover extensions/agents from package subdirectories."""
    extensions: list[str] = []
    agents: list[str] = []
    pkg_name = package_location.name

    ext_dir = package_location / "vtx_extensions"
    if ext_dir.is_dir():
        for py_file in sorted(ext_dir.rglob("*.py")):
            if py_file.name == "__init__.py":
                rel = py_file.relative_to(package_location)
                mod_path = f"{pkg_name}.{'.'.join(rel.parts[:-1])}:register"
                extensions.append(mod_path)

    agent_dir = package_location / "vtx_agent"
    if agent_dir.is_dir():
        for py_file in sorted(agent_dir.rglob("*.py")):
            if py_file.name == "__init__.py":
                rel = py_file.relative_to(package_location)
                mod_path = f"{pkg_name}.{'.'.join(rel.parts[:-1])}:AGENT"
                agents.append(mod_path)

    return extensions, agents


def _get_package_version(package: str) -> str:
    try:
        from importlib.metadata import version

        return version(package)
    except Exception:
        return ""


def install_extension(
    name: str, *, upgrade: bool = False
) -> tuple[bool, str, InstalledExtension | None]:
    """Install an extension package.

    Tries ``vtx-<name>`` first, then ``<name>`` if the first fails.
    Returns (success, message, extension_info).
    """
    candidates = [f"vtx-{name}", name] if not name.startswith("vtx-") else [name]

    installed_pkg = None
    for candidate in candidates:
        log.info("trying to install %s", candidate)
        ok, msg = _run_uv_pip_install(candidate)
        if ok:
            installed_pkg = candidate
            break
        log.debug("install %s failed: %s", candidate, msg)

    if installed_pkg is None:
        return False, f"failed to install {name}: tried {', '.join(candidates)}", None

    pkg_location = _find_package_location(installed_pkg)
    if pkg_location is None:
        return (False, f"installed {installed_pkg} but could not locate package files", None)

    extensions, agents = _discover_entry_points(pkg_location)
    if not extensions and not agents:
        extensions, agents = _discover_package_layout(pkg_location)

    version = _get_package_version(installed_pkg)
    info = InstalledExtension(
        name=name, source=installed_pkg, version=version, extensions=extensions, agents=agents
    )

    installed = _load_installed()
    installed[name] = info
    _save_installed(installed)

    parts = [f"installed {installed_pkg} ({version})"]
    if extensions:
        parts.append(f"{len(extensions)} extension(s)")
    if agents:
        parts.append(f"{len(agents)} agent(s)")
    return True, ", ".join(parts), info


def uninstall_extension(name: str) -> tuple[bool, str]:
    """Uninstall an extension package."""
    installed = _load_installed()
    info = installed.get(name)
    if info is None:
        return False, f"{name!r} is not installed"

    try:
        result = subprocess.run(
            [sys.executable, "-m", "uv", "pip", "uninstall", info.source],
            capture_output=True,
            text=True,
            timeout=120,
        )
        ok = result.returncode == 0
        msg = result.stdout or result.stderr
    except Exception as exc:
        return False, str(exc)

    if ok:
        del installed[name]
        _save_installed(installed)

    return ok, msg


def list_installed() -> list[InstalledExtension]:
    """Return all installed extensions."""
    return list(_load_installed().values())


def get_installed_extension(name: str) -> InstalledExtension | None:
    """Return a specific installed extension by name."""
    return _load_installed().get(name)
