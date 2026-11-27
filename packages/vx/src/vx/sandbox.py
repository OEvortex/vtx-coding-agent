"""bwrap sandbox for the bash tool: a read-only root, a writable workspace.

The rule is the one agy's terminal sandbox uses: everything on the machine is
visible, nothing outside the workspace is writable, and the process cannot reach
the network, the session pid space or the user's other processes. A command that
needs to write somewhere else passes ``extra_rw``; a command that needs the
network asks for it explicitly.

Probing is cached per process: bwrap can be installed and still be useless
(unprivileged user namespaces disabled, or AppArmor refusing them), and paying a
subprocess for that answer on every call is worse than paying it once.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

BWARP = "bwrap"
_PROBE_TIMEOUT_SECONDS = 5.0

#: Paths a sandboxed command may write. The workspace is added per call; these
#: are the ones every toolchain assumes (a shell needs a tmp, a compiler needs a
#: cache inside the workspace, not in $HOME).
DEFAULT_RW_PATHS: tuple[str, ...] = ("/tmp", "/dev/shm")

_APPARMOR_HINT = (
    "AppArmor restricts unprivileged user namespaces "
    "(kernel.apparmor_restrict_unprivileged_userns = 1; fix: "
    "sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0, or grant an "
    "AppArmor profile that allows userns)"
)


@dataclass(frozen=True)
class SandboxSupport:
    """Whether bwrap can run a command here, and why not when it cannot."""

    available: bool
    reason: str = ""


_cache: SandboxSupport | None = None


def reset_cache() -> None:
    """Forget the probe result. Tests and ``/doctor`` style checks call this."""
    global _cache
    _cache = None


def _probe_argv() -> list[str]:
    return [
        BWARP,
        "--dev",
        "/dev",
        "--proc",
        "/proc",
        "--unshare-pid",
        "--ro-bind",
        "/",
        "/",
        "/bin/true",
    ]


def _classify(stderr: str) -> str:
    text = stderr.lower()
    if "operation not permitted" in text or "permission denied" in text:
        if "namespace" in text or "unshare" in text:
            return f"bwrap cannot create namespaces: {_APPARMOR_HINT}"
        return "bwrap is not permitted to create namespaces on this host"
    if "no such file" in text:
        return "bwrap could not bind a required path (is /proc mounted?)"
    first = stderr.strip().splitlines()[0] if stderr.strip() else "unknown error"
    return f"bwrap probe failed: {first}"


def support(refresh: bool = False) -> SandboxSupport:
    """Cached answer to "can this host sandbox a command"."""
    global _cache
    if _cache is not None and not refresh:
        return _cache
    if shutil.which(BWARP) is None:
        _cache = SandboxSupport(False, "bubblewrap (bwrap) is not installed")
        return _cache
    try:
        proc = subprocess.run(
            _probe_argv(), capture_output=True, text=True, timeout=_PROBE_TIMEOUT_SECONDS
        )
    except FileNotFoundError:
        _cache = SandboxSupport(False, "bubblewrap (bwrap) is not installed")
        return _cache
    except subprocess.TimeoutExpired:
        _cache = SandboxSupport(False, "bwrap probe timed out")
        return _cache
    if proc.returncode == 0:
        _cache = SandboxSupport(True)
    else:
        _cache = SandboxSupport(False, _classify(proc.stderr or proc.stdout))
    return _cache


def build_argv(
    command: str, cwd: Path, *, shell: str, network: bool = False, extra_rw: tuple[str, ...] = ()
) -> list[str]:
    """The full ``bwrap ... /bin/sh -c <command>`` argv for one command."""
    cwd = Path(cwd)
    argv = [
        BWARP,
        # Everything visible, nothing writable except what is bound below.
        "--ro-bind",
        "/",
        "/",
        "--dev",
        "/dev",
        "--proc",
        "/proc",
        "--tmpfs",
        "/tmp",
    ]
    if Path("/run/user").exists():
        # A private runtime dir; some tools refuse to start without one.
        argv += ["--tmpfs", "/run/user"]
    argv += [
        # The workspace, writable, at its real path so absolute paths keep working.
        "--bind",
        str(cwd),
        str(cwd),
    ]
    for path in DEFAULT_RW_PATHS + tuple(extra_rw):
        resolved = Path(path).expanduser()
        if str(resolved) == "/tmp":
            continue  # already a tmpfs
        if resolved.exists() and not _is_inside(resolved, cwd):
            argv += ["--bind", str(resolved), str(resolved)]
    argv += [
        "--unshare-ipc",
        "--unshare-pid",
        "--unshare-uts",
        "--unshare-cgroup-try",
        "--die-with-parent",
        "--new-session",
        "--chdir",
        str(cwd),
    ]
    if not network:
        argv += ["--unshare-net"]
    argv += ["--", shell, "-c", command]
    return argv


def _is_inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except (ValueError, OSError):
        return False


def describe(*, network: bool = False) -> str:
    """One line a result can carry so the model knows what it just ran under."""
    scope = "read-only root, workspace writable, private /tmp, no network"
    if network:
        scope = "read-only root, workspace writable, private /tmp, network allowed"
    return f"sandbox: bwrap ({scope})"


def wrap_error(command: str, stderr: str) -> str | None:
    """Turn a sandbox-shaped failure into guidance, or None if it is not one."""
    text = (stderr or "").lower()
    if not text:
        return None
    if (
        "unshare" in text
        or "create new namespace" in text
        or "creating new namespace" in text
        or "operation not permitted" in text
    ):
        return (
            f"The sandbox could not start this command ({_APPARMOR_HINT}). "
            "Either fix the host or re-run with sandbox=false."
        )
    if "no such file or directory" in text and os.sep in command:
        return None
    return None
