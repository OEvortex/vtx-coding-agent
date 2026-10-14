"""The product-neutral harness: loop, turns, sessions, tools, extensions, SDK.

``vtx.agent`` owns the engine. The product layer (``vtx.coding_agent``) injects
everything engine-side: the system-prompt builder, the context loader, the tool
registry, and the user's configuration.

The names re-exported here are the ones call sites have always imported from
``vtx.agent``. They now live in :mod:`vtx.plugins`, because the event contract,
the extension API, and extension discovery are composition concerns; this module
keeps the historical import path working rather than breaking every caller.

The re-exports are lazy: importing this package used to mount the whole plugin
kernel, so reaching any leaf like ``vtx.agent.context.skills`` through it cost
~550ms the leaf does not use. Submodule imports (``from vtx.agent import
revert``) are unaffected — they fall through to the real submodule.
"""

import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from vtx.plugins.api import (
        CommandOutcome,
        ExtensionAPI,
        ExtensionCommand,
        ExtensionRecord,
        ExtensionTool,
    )
    from vtx.plugins.event_contract import AGENT_CHANGED, AGENT_START
    from vtx.plugins.extensions import LoadedExtensions, load_for_runtime

# Historical import order, so a name exported by more than one source still
# resolves to the object the eager re-exports produced.
_SOURCES = ("vtx.plugins.api", "vtx.plugins.event_contract", "vtx.plugins.extensions")


def __getattr__(name: str) -> Any:
    for module_name in _SOURCES:
        module = importlib.import_module(module_name)
        if (value := getattr(module, name, None)) is not None:
            globals()[name] = value
            return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(__all__)


__all__ = [
    "AGENT_CHANGED",
    "AGENT_START",
    "CommandOutcome",
    "ExtensionAPI",
    "ExtensionCommand",
    "ExtensionRecord",
    "ExtensionTool",
    "LoadedExtensions",
    "load_for_runtime",
]
