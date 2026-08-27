"""Context loader for OpenJarvis — wraps VTX context + Jarvis additions."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from vtx.coding_agent.context.loader import Context as VtxContext


@dataclass
class OpenJarvisContext:
    cwd: str
    vtx: VtxContext = field(default_factory=lambda: VtxContext.load(str(Path.cwd())))
    gateway_port: int = 18789
    channels_enabled: list[str] = field(default_factory=list)
    cron_jobs: list[str] = field(default_factory=list)
    pairing_pending: int = 0

    @classmethod
    def load(cls, cwd: str, gateway_port: int = 18789) -> OpenJarvisContext:
        vtx = VtxContext.load(cwd)
        # Probe enabled channels via discovery without importing heavy SDKs
        try:
            from vtx.openjarvis.channels.registry import discover_channel_names

            enabled = discover_channel_names()
        except Exception:
            enabled = []
        # Cron jobs count (probe openjarvis store)
        try:
            from pathlib import Path as PathLib

            from vtx.core.paths import get_config_dir

            _ = PathLib(get_config_dir() / "openjarvis.json")
        except Exception:
            pass
        return cls(cwd=cwd, vtx=vtx, gateway_port=gateway_port, channels_enabled=enabled)

    def reload(self) -> None:
        self.vtx.reload()
