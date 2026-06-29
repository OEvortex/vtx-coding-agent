from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class SkillMetadata:
    __slots__ = ("name", "description", "path", "category", "emoji", "dependencies")

    def __init__(
        self,
        name: str,
        description: str,
        path: Path,
        category: str = "",
        emoji: str = "",
        dependencies: list[str] | None = None,
    ) -> None:
        self.name = name
        self.description = description
        self.path = path
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


class SkillRegistry:
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

    def list_names(self) -> list[str]:
        return list(self._skills.keys())

    def get(self, name: str) -> SkillMetadata | None:
        return self._skills.get(name)

    def search(self, query: str) -> list[SkillMetadata]:
        q = query.lower()
        return [s for s in self._skills.values() if q in s.description.lower()]

    def install(self, skill_id: str) -> bool:
        target = self._dir / skill_id / "SKILL.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_text(
                f"---\nname: {skill_id}\ndescription: installed skill\n---\n# {skill_id}\n"
            )
            self._skills[skill_id] = SkillMetadata(
                name=skill_id, description="installed skill", path=target
            )
            return True
        return False


def _parse_skill_md(path: Path) -> SkillMetadata:
    text = path.read_text()
    name = "unknown"
    description = ""
    if text.startswith("---"):
        end = text.find("---", 3)
        if end != -1:
            front = text[3:end]
            for line in front.splitlines():
                line = line.strip()
                if line.startswith("name:"):
                    name = line.split(":", 1)[1].strip()
                elif line.startswith("description:"):
                    description = line.split(":", 1)[1].strip().strip("\"'")
    return SkillMetadata(
        name=name,
        description=description,
        path=path,
    )
