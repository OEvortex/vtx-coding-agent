"""The product-neutral harness: loop, turns, sessions, tools, extensions, SDK.

``vtx.agent`` owns the engine. The product layer (``vtx.coding_agent``) injects
everything engine-side: the system-prompt builder, the context loader, the tool
registry, and the user's configuration.

The names re-exported here are the ones call sites have always imported from
``vtx.agent``. They now live in :mod:`vtx.plugins`, because the event contract,
the extension API, and extension discovery are composition concerns; this module
keeps the historical import path working rather than breaking every caller.
"""

from __future__ import annotations

from vtx.plugins.api import (
    CommandOutcome,
    ExtensionAPI,
    ExtensionCommand,
    ExtensionRecord,
    ExtensionTool,
)
from vtx.plugins.event_contract import AGENT_CHANGED, AGENT_START
from vtx.plugins.extensions import LoadedExtensions, load_for_runtime

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
