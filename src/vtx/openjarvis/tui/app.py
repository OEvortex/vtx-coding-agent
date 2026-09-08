"""OpenJarvis TUI — reuses vtx.tui exactly (same UI as `vtx`)."""

from __future__ import annotations

import contextlib

try:
    from vtx.tui.app import Vtx as _Vtx  # legacy
except (ModuleNotFoundError, ImportError):
    from vtx.ui.app import Vtx as _Vtx  # type: ignore

from vtx.openjarvis.tools import register_with_vtx
from vtx.openjarvis.version import VERSION

# Apply branding after _Vtx is imported (like vtx_crs does)
with contextlib.suppress(Exception):
    from vtx.openjarvis.tui import branding  # noqa: F401


class OpenJarvisApp(_Vtx):
    TITLE = "openjarvis"
    VERSION = VERSION

    def __init__(self, *args, **kwargs):
        from vtx.ai.config import config

        # Default OpenJarvis to the titanium pi-style theme if on default
        if config.ui.theme == "gruvbox-dark":
            config.ui.theme = "titanium"

        register_with_vtx()
        super().__init__(*args, **kwargs)
