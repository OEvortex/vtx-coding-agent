"""OpenJarvis TUI — reuses vtx.tui exactly (same UI as `vtx`)."""

from __future__ import annotations

try:
    from vtx.tui.app import Vtx as _Vtx  # legacy
except (ModuleNotFoundError, ImportError):
    from vtx.ui.app import Vtx as _Vtx  # type: ignore

from vtx.openjarvis.tools import register_with_vtx
from vtx.openjarvis.version import VERSION

# Apply branding after _Vtx is imported (like vtx_crs does)
try:
    from vtx.openjarvis.tui import branding  # noqa: F401
except Exception:
    pass


class OpenJarvisApp(_Vtx):
    TITLE = "openjarvis"
    VERSION = VERSION

    def __init__(self, *args, **kwargs):
        register_with_vtx()
        super().__init__(*args, **kwargs)
