"""
Context loader - loads and caches AGENTS.md files and skills.

This is loaded once at startup and passed to the agent for system prompt building.
The UI can also access it to display loaded resources.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .agent_mds import ContextFile, load_agent_mds
from .skills import (
    Skill,
    load_builtin_cmd_skills,
    load_skills,
    merge_registered_skills,
    sync_builtin_skills,
)


@dataclass
class Context:
    cwd: str
    agents_files: list[ContextFile] = field(default_factory=list)
    skills: list[Skill] = field(default_factory=list)
    skill_warnings: list[tuple[str, str]] = field(default_factory=list)

    @classmethod
    def load(cls, cwd: str) -> Context:
        sync_builtin_skills()
        agents_files = load_agent_mds(cwd)
        skills_result = load_skills(cwd)
        builtin_result = load_builtin_cmd_skills()

        skills = merge_registered_skills(skills_result.skills, builtin_result.skills)
        warnings = [(w.path, w.message) for w in skills_result.warnings]
        warnings.extend((w.path, w.message) for w in builtin_result.warnings)

        return cls(cwd=cwd, agents_files=agents_files, skills=skills, skill_warnings=warnings)

    def reload(self) -> None:
        sync_builtin_skills()
        agents_files = load_agent_mds(self.cwd)
        skills_result = load_skills(self.cwd)
        builtin_result = load_builtin_cmd_skills()

        self.agents_files = agents_files
        self.skills = merge_registered_skills(skills_result.skills, builtin_result.skills)
        self.skill_warnings = [(w.path, w.message) for w in skills_result.warnings]
        self.skill_warnings.extend((w.path, w.message) for w in builtin_result.warnings)
