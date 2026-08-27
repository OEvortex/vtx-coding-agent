import os
from pathlib import Path

CONFIG_DIR_NAME = "vtx"


def get_config_dir() -> Path:
    # Be robust when HOME is empty/unset (e.g. `env -i`, some `uv tool`
    # shims, or sudo without -H). Path.home() then returns `/` and
    # `/.vtx` is not writable -> `Failed to write model cache for ...:
    # Permission denied: '/.vtx'` and the user sees "not writing to
    # ~/.vtx". Prefer explicit env vars, then HOME, then fallback.
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        # XDG specifies $XDG_CONFIG_HOME/vtx, but we keep ~/.vtx for
        # backwards compat - only use XDG when explicitly set.
        p = Path(xdg)
        if p.is_absolute():
            return p / CONFIG_DIR_NAME
    home = os.environ.get("HOME")
    if home:
        p = Path(home)
        if str(p) not in ("", "/"):
            return p / f".{CONFIG_DIR_NAME}"
    # Fallback: Path.home() may still be `/` when HOME is empty, so
    # try to expand `~` via os.path.expanduser and guard against `/`.
    try:
        h = Path.home()
        if str(h) not in ("", "/"):
            return h / f".{CONFIG_DIR_NAME}"
    except Exception:
        pass
    # Try pwd database (works even when HOME is unset).
    try:
        import pwd

        pw_dir = pwd.getpwuid(os.getuid()).pw_dir
        if pw_dir and pw_dir != "/":
            return Path(pw_dir) / f".{CONFIG_DIR_NAME}"
    except Exception:
        pass
    # Last resort: use current directory's `.vtx` so we don't silently
    # write to `/.vtx` and lose data.
    return Path.cwd() / f".{CONFIG_DIR_NAME}"


def get_agents_dir() -> Path:
    # Keep consistent with get_config_dir for HOME handling, but agents
    # have historically lived at `~/.agents`.
    home = os.environ.get("HOME")
    if home and str(Path(home)) not in ("", "/"):
        return Path(home) / ".agents"
    try:
        h = Path.home()
        if str(h) not in ("", "/"):
            return h / ".agents"
    except Exception:
        pass
    try:
        import pwd

        pw_dir = pwd.getpwuid(os.getuid()).pw_dir
        if pw_dir and pw_dir != "/":
            return Path(pw_dir) / ".agents"
    except Exception:
        pass
    return Path.cwd() / ".agents"


def shorten_path(path: str) -> str:
    """Shorten path for display (replace $HOME with ~)."""
    try:
        home = str(Path.home())
        if path.startswith(home):
            return "~" + path[len(home) :]
    except Exception:
        pass
    return path
