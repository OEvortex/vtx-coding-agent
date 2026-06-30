"""
vtx_claw - A lightweight AI agent framework
"""

import tomllib
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from pathlib import Path


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
        # Source checkouts often import vtx_claw without installed dist-info.
        return _read_pyproject_version() or "0.2.2"


__version__ = _resolve_version()
__logo__ = "🐈"

_LAZY_EXPORTS = {
    "VtxClaw": ".vtx_claw",
    "RunStream": ".vtx_claw",
    "RunResult": ".vtx_claw",
    "SessionInfo": ".vtx_claw",
    "SessionSnapshot": ".vtx_claw",
    "STREAM_EVENT_REASONING_COMPLETED": ".vtx_claw",
    "STREAM_EVENT_REASONING_DELTA": ".vtx_claw",
    "STREAM_EVENT_RUN_COMPLETED": ".vtx_claw",
    "STREAM_EVENT_RUN_FAILED": ".vtx_claw",
    "STREAM_EVENT_RUN_STARTED": ".vtx_claw",
    "STREAM_EVENT_TEXT_COMPLETED": ".vtx_claw",
    "STREAM_EVENT_TEXT_DELTA": ".vtx_claw",
    "STREAM_EVENT_TOOL_COMPLETED": ".vtx_claw",
    "STREAM_EVENT_TOOL_FAILED": ".vtx_claw",
    "STREAM_EVENT_TOOL_STARTED": ".vtx_claw",
    "STREAM_EVENT_TYPES": ".vtx_claw",
    "StreamEvent": ".vtx_claw",
    "StreamEventType": ".vtx_claw",
}


def __getattr__(name: str):
    module_path = _LAZY_EXPORTS.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    mod = import_module(module_path, __name__)
    val = getattr(mod, name)
    globals()[name] = val
    return val


__all__ = [
    "STREAM_EVENT_REASONING_COMPLETED",
    "STREAM_EVENT_REASONING_DELTA",
    "STREAM_EVENT_RUN_COMPLETED",
    "STREAM_EVENT_RUN_FAILED",
    "STREAM_EVENT_RUN_STARTED",
    "STREAM_EVENT_TEXT_COMPLETED",
    "STREAM_EVENT_TEXT_DELTA",
    "STREAM_EVENT_TOOL_COMPLETED",
    "STREAM_EVENT_TOOL_FAILED",
    "STREAM_EVENT_TOOL_STARTED",
    "STREAM_EVENT_TYPES",
    "RunResult",
    "RunStream",
    "SessionInfo",
    "SessionSnapshot",
    "StreamEvent",
    "StreamEventType",
    "VtxClaw",
]
