"""
Extension manager: install, list, and uninstall vtx extensions from PyPI
or GitHub repos.

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
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from coding_agent.config import get_config_dir

log = logging.getLogger("agent.extension_manager")

INSTALLED_EXTENSIONS_FILE = "installed_extensions.yml"


@dataclass
class InstalledExtension:
    name: str
    source: str  # pip package name, local path, or git URL
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


def _run_git_clone(url: str, dest: Path) -> tuple[bool, str]:
    """Run ``git clone <url> <dest>`` and return (success, output)."""
    try:
        result = subprocess.run(
            ["git", "clone", "--depth", "1", url, str(dest)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode == 0:
            return True, result.stdout
        return False, result.stderr or result.stdout
    except FileNotFoundError:
        return False, "git not found; install git first"
    except subprocess.TimeoutExpired:
        return False, f"timeout cloning {url}"
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
        from pathlib import Path as _Path

        dist = distribution(package)
        located = _Path(str(dist.locate_file("")))
        if located.exists():
            return located
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
            # Support both old ``agent.*`` and new ``ai.agent.*`` entry points.
            for key in ("agent.extensions", "ai.agent.extensions"):
                for mod_path in eps.get(key, {}).values():
                    extensions.append(mod_path)
            for key in ("agent.agents", "ai.agent.agents"):
                for mod_path in eps.get(key, {}).values():
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


def _is_url(value: str) -> bool:
    """Return True if value looks like a URL (git, https, ssh)."""
    return value.startswith(("http://", "https://", "ssh://", "git@", "git://"))


def _extract_repo_name(url: str) -> str:
    """Extract repo name from a GitHub URL like https://github.com/user/repo."""
    url = url.rstrip("/")
    if url.endswith(".git"):
        url = url[:-4]
    name = url.split("/")[-1]
    # Sanitize to a valid Python package name
    return name.lower().replace("-", "_")


def install_extension(
    name: str, *, upgrade: bool = False
) -> tuple[bool, str, InstalledExtension | None]:
    """Install an extension package from PyPI or a GitHub repo.

    - If ``name`` is a URL, clone it and install from the local path.
    - Otherwise, try ``vtx-<name>`` then ``<name>`` via pip.
    - Returns (success, message, extension_info).
    """
    if _is_url(name):
        return _install_from_git(name)

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

    return _finalize_install(pkg_location, name, installed_pkg)


def _install_from_git(url: str) -> tuple[bool, str, InstalledExtension | None]:
    """Clone a git repo and install it as a local extension."""
    repo_name = _extract_repo_name(url)
    with tempfile.TemporaryDirectory(prefix="vtx-ext-") as tmp:
        dest = Path(tmp) / repo_name
        ok, msg = _run_git_clone(url, dest)
        if not ok:
            return False, f"git clone failed: {msg}", None

        # Try pip install from the cloned path first
        pip_ok, _pip_msg = _run_uv_pip_install(str(dest))
        if pip_ok:
            pkg_location = _find_package_location(repo_name)
            if pkg_location:
                return _finalize_install(pkg_location, repo_name, url)

        # Fall back to direct package layout discovery from the clone
        extensions, agents = _discover_entry_points(dest)
        if not extensions and not agents:
            extensions, agents = _discover_package_layout(dest)

        info = InstalledExtension(
            name=repo_name, source=url, version="", extensions=extensions, agents=agents
        )
        installed = _load_installed()
        installed[repo_name] = info
        _save_installed(installed)

        parts = [f"cloned {url}"]
        if extensions:
            parts.append(f"{len(extensions)} extension(s)")
        if agents:
            parts.append(f"{len(agents)} agent(s)")
        return True, ", ".join(parts), info


def _finalize_install(
    pkg_location: Path, name: str, source: str
) -> tuple[bool, str, InstalledExtension]:
    """Discover entry points and save to installed record."""
    extensions, agents = _discover_entry_points(pkg_location)
    if not extensions and not agents:
        extensions, agents = _discover_package_layout(pkg_location)

    version = _get_package_version(name)
    info = InstalledExtension(
        name=name, source=source, version=version, extensions=extensions, agents=agents
    )

    installed = _load_installed()
    installed[name] = info
    _save_installed(installed)

    parts = [f"installed {source} ({version})"]
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
