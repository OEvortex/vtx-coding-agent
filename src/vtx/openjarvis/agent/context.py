"""Context loader for OpenJarvis — wraps VTX context + Jarvis additions.

VTX builtin_skills isolation:
  OpenJarvis MUST NOT see ``src/vtx/coding_agent/builtin_skills/`` (modal,
  google-colab, review, github, skill-builder, goal, init). Those are VTX-
  specific. OpenJarvis loads only project/user skills + its own
  ``src/vtx/openjarvis/agent/skills/`` bundle, via ``load_openjarvis_skills``.
  This module does NOT monkey-patch VTX's ``VtxContext`` — it keeps VTX's
  context intact and stores OpenJarvis-isolated skills in its own fields.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from vtx.coding_agent.context.loader import Context as VtxContext


@dataclass
class OpenJarvisContext:
    cwd: str
    vtx: VtxContext = field(default_factory=lambda: VtxContext.load(str(Path.cwd())))
    # OpenJarvis-isolated skills (never includes VTX builtin_skills).
    # ``vtx`` is kept unmodified for agents_files / git context; prompt
    # building uses ``self.skills`` explicitly via ``skills=`` override.
    skills: list = field(default_factory=list)
    skill_warnings: list[tuple[str, str]] = field(default_factory=list)
    gateway_port: int = 18789
    channels_enabled: list[str] = field(default_factory=list)
    cron_jobs: list[str] = field(default_factory=list)
    pairing_pending: int = 0

    @classmethod
    def load(cls, cwd: str, gateway_port: int = 18789) -> OpenJarvisContext:
        vtx = VtxContext.load(cwd)
        # Load OpenJarvis-isolated skills WITHOUT mutating ``vtx``.
        # VTX's ``vtx.skills`` keeps its builtins; we store the isolated
        # view in ``self.skills`` and pass it explicitly to the prompt
        # builder via ``skills=`` (see prompts.py).
        isolated_skills: list = []
        isolated_warnings: list[tuple[str, str]] = []
        try:
            from vtx.openjarvis.agent.skills import load_openjarvis_skills

            isolated = load_openjarvis_skills(cwd)
            isolated_skills = isolated.skills
            isolated_warnings = [(w.path, w.message) for w in isolated.warnings]
        except Exception:
            try:
                from vtx.openjarvis.agent.skills import filter_bundled_skills

                isolated_skills = filter_bundled_skills(list(vtx.skills))
                isolated_warnings = [
                    w for w in list(vtx.skill_warnings) if "bundled" not in w[0].lower()
                ]
            except Exception:
                isolated_skills = [s for s in list(vtx.skills) if not getattr(s, "bundled", False)]
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
        return cls(
            cwd=cwd,
            vtx=vtx,
            skills=isolated_skills,
            skill_warnings=isolated_warnings,
            gateway_port=gateway_port,
            channels_enabled=enabled,
        )

    def reload(self) -> None:
        self.vtx.reload()
        try:
            from vtx.openjarvis.agent.skills import load_openjarvis_skills

            isolated = load_openjarvis_skills(self.cwd)
            self.skills = isolated.skills
            self.skill_warnings = [(w.path, w.message) for w in isolated.warnings]
        except Exception:
            pass
