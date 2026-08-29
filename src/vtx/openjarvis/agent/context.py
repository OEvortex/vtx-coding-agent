"""Context loader for OpenJarvis — wraps VTX context + Jarvis additions.

VTX builtin_skills isolation:
  OpenJarvis MUST NOT see ``src/vtx/coding_agent/builtin_skills/`` (modal,
  google-colab, review, github, skill-builder, goal, init). Those are VTX-
  specific. OpenJarvis loads only project/user skills + its own
  ``src/vtx/openjarvis/agent/skills/`` bundle, via ``load_openjarvis_skills``.
"""

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
        # --- isolate from VTX builtin_skills ---------------------------------
        # VtxContext.load merges VTX builtins (bundled=True) via:
        #   load_skills(cwd) [includes ~/.vtx/skills] + load_builtin_cmd_skills()
        # OpenJarvis must not expose those. Replace vtx.skills with an
        # isolated set that excludes bundled VTX skills.
        try:
            from vtx.openjarvis.agent.skills import load_openjarvis_skills

            isolated = load_openjarvis_skills(cwd)
            # Keep VTX context's agents_files, but swap skills to isolated view.
            # Preserve warnings from isolated loader; drop bundled warnings.
            vtx.skills = isolated.skills
            vtx.skill_warnings = [(w.path, w.message) for w in isolated.warnings]
        except Exception:
            # Fallback: at least strip bundled flag if isolated loader fails.
            try:
                from vtx.openjarvis.agent.skills import filter_bundled_skills

                vtx.skills = filter_bundled_skills(vtx.skills)
                vtx.skill_warnings = [
                    w for w in vtx.skill_warnings if "bundled" not in w[0].lower()
                ]
            except Exception:
                vtx.skills = [s for s in vtx.skills if not getattr(s, "bundled", False)]
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
