from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class CategoryMetadata:
    __slots__ = ("description", "emoji", "name")

    def __init__(self, name: str, description: str, emoji: str = "") -> None:
        self.name = name
        self.description = description
        self.emoji = emoji

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name, "description": self.description, "emoji": self.emoji}


class SkillLoader:
    def __init__(self, skills_dir: Path | None = None) -> None:
        self._dir = skills_dir or Path.home() / ".vtx" / "claw" / "skills"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._skills: dict[str, SkillMetadata] = {}
        self._load()

    def _load(self) -> None:
        self._skills.clear()
        for skill_dir in self._dir.iterdir():
            if not skill_dir.is_dir():
                continue
            md = skill_dir / "SKILL.md"
            if not md.exists():
                continue
            try:
                meta = _parse_skill_md(md)
                self._skills[meta.name] = meta
            except Exception:
                logger.exception("Failed to load skill %s", skill_dir.name)

    def list_metadata(self) -> list[dict[str, Any]]:
        return [s.to_dict() for s in self._skills.values()]


class SkillMetadata:
    __slots__ = ("category", "dependencies", "description", "emoji", "name", "path")

    def __init__(
        self,
        name: str = "",
        description: str = "",
        path: Path | None = None,
        category: str = "",
        emoji: str = "",
        dependencies: list[str] | None = None,
    ) -> None:
        self.name = name
        self.description = description
        self.path = path or Path()
        self.category = category
        self.emoji = emoji
        self.dependencies = dependencies or []

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "path": str(self.path),
            "category": self.category,
            "emoji": self.emoji,
            "dependencies": list(self.dependencies),
        }


def _parse_skill_md(path: Path) -> SkillMetadata:
    text = path.read_text()
    name = "unknown"
    description = ""
    if text.startswith("---"):
        end = text.find("---", 3)
        if end != -1:
            front = text[3:end]
            for line in front.splitlines():
                stripped = line.strip()
                if stripped.startswith("name:"):
                    name = stripped.split(":", 1)[1].strip()
                elif stripped.startswith("description:"):
                    description = stripped.split(":", 1)[1].strip().strip("\"'")
    return SkillMetadata(name=name, description=description, path=path)
