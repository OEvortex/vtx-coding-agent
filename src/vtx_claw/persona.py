from __future__ import annotations

import logging
from pathlib import Path

from vtx_claw.config.schema import PersonaConfig

logger = logging.getLogger(__name__)


class PersonaManager:
    def __init__(self, cfg: PersonaConfig | None = None) -> None:
        self._cfg = cfg or PersonaConfig()
        self._soul: str = ""
        self._personas: dict[str, str] = {}
        self._active = self._cfg.active
        self._load()

    def _load(self) -> None:
        soul_path = Path(self._cfg.soul_file)
        if soul_path.exists():
            self._soul = soul_path.read_text()

        persona_dir = Path(self._cfg.persona_file).parent
        persona_dir.mkdir(parents=True, exist_ok=True)

        default_path = Path(self._cfg.persona_file)
        default_key = default_path.stem
        if default_path.exists():
            self._personas[default_key] = default_path.read_text()

        for p in persona_dir.glob("*.md"):
            key = p.stem
            if key not in self._personas:
                self._personas[key] = p.read_text()

    def get_system_prompt(self) -> str:
        parts: list[str] = []
        if self._soul:
            parts.append(self._soul)
        active_text = self._personas.get(self._active, self._personas.get("default", ""))
        if active_text:
            parts.append(active_text)
        return "\n\n".join(parts)

    def set_active(self, name: str) -> None:
        if name in self._personas or name == "default":
            self._active = name
        else:
            raise ValueError(f"Unknown persona: {name}")

    def active_name(self) -> str:
        return self._active
