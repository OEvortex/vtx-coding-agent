"""The product-neutral harness: loop, turns, sessions, tools, extensions, SDK.

``ctx.agent`` owns the engine. The product layer (``ctx.coding_agent``) injects
everything engine-side: the system-prompt builder, the context loader, the tool
registry, and the user's configuration.

The names re-exported here are the ones call sites have always imported from
``ctx.agent``. They now live in :mod:`ctx.plugins`, because the event contract,
the extension API, and extension discovery are composition concerns; this module
keeps the historical import path working rather than breaking every caller.

The re-exports are lazy: importing this package used to mount the whole plugin
kernel, so reaching any leaf like ``ctx.agent.context.skills`` through it cost
~550ms the leaf does not use. Submodule imports (``from ctx.agent import
revert``) are unaffected — they fall through to the real submodule.
"""

import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ctx.plugins.api import (
        CommandOutcome,
        ExtensionAPI,
        ExtensionCommand,
        ExtensionRecord,
        ExtensionTool,
    )
    from ctx.plugins.event_contract import AGENT_CHANGED, AGENT_START
    from ctx.plugins.extensions import LoadedExtensions, load_for_runtime

# Historical import order, so a name exported by more than one source still
# resolves to the object the eager re-exports produced.
_SOURCES = ("ctx.plugins.api", "ctx.plugins.event_contract", "ctx.plugins.extensions")


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
