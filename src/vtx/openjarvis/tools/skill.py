"""Isolated skill tool for OpenJarvis — hides VTX builtin_skills.

VTX ships ~7 builtins in ``src/vtx/coding_agent/builtin_skills/`` (modal,
google-colab, review, github, skill-builder, goal, init) and syncs them to
``~/.vtx/skills`` plus loads directly via ``load_builtin_cmd_skills()``.
OpenJarvis must NOT see those — it has its own isolated discovery via
``vtx.openjarvis.agent.skills.load_openjarvis_skills`` which only sees:

  1. <cwd>/.agents/skills  (project)
  2. ~/.agents/skills       (user global)
  3. src/vtx/openjarvis/agent/skills/ (openjarvis-own bundle, if present)

This tool shadows VTX's ``SkillTool`` (same name ``skill``) but filters
bundled VTX skills for every action.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from vtx.core.paths import get_agents_dir as get_user_skills_dir
from vtx.openjarvis.agent.skills import BUILTIN_SKILLS_DIR, load_openjarvis_skills
from vtx.openjarvis.tools.base import Tool, tool_parameters
from vtx.openjarvis.tools.schema import StringSchema, tool_parameters_schema


def _find_openjarvis_skill_dir(name: str, workspace: str) -> tuple[Path | None, bool]:
    """Locate skill dir for OpenJarvis-isolated lookup.

    Returns (dir, is_bundled). Excludes VTX synced dir and VTX package resource.
    """
    cwd = Path(workspace) if workspace else Path.cwd()
    # 1. Project skills (walk ancestors like VTX does)
    from vtx.coding_agent.context.skills import _project_skill_dirs

    for skills_dir in _project_skill_dirs(cwd):
        candidate = skills_dir / name
        if (candidate / "SKILL.md").is_file():
            return candidate, False

    # 2. User global
    user_dir = (get_user_skills_dir() / "skills" / name).resolve(strict=False)
    if (user_dir / "SKILL.md").is_file():
        return user_dir, False

    # 3. OpenJarvis own bundle
    oj_bundled = (BUILTIN_SKILLS_DIR / name).resolve(strict=False)
    if (oj_bundled / "SKILL.md").is_file():
        return oj_bundled, True

    return None, False


@tool_parameters(
    tool_parameters_schema(
        action=StringSchema(
            "Action: 'list' (discover), 'view' (read), 'create' (new), 'patch', 'edit', 'delete'",
            enum=["list", "view", "create", "patch", "edit", "delete"],
        ),
        name=StringSchema("Skill name (lowercase/hyphens)"),
        content=StringSchema("Full SKILL.md content for create/edit"),
        old_string=StringSchema("Exact text to replace for patch"),
        new_string=StringSchema("Replacement text for patch"),
        file_path=StringSchema("Supporting file (defaults to SKILL.md)"),
        scope=StringSchema("Target scope for create: project|global", enum=["project", "global"]),
        required=[],
    )
)
class SkillTool(Tool):
    """Isolated skill workflows for OpenJarvis (VTX builtins hidden)."""

    @property
    def name(self) -> str:
        return "skill"

    @property
    def description(self) -> str:
        return (
            "Inspect and manage OpenJarvis skills (VTX builtin_skills hidden). "
            "Use 'list' to discover project/user/openjarvis skills, "
            "'view' to read, 'create'/'edit'/'patch'/'delete' to manage. "
            "VTX builtins are not visible."
        )

    @property
    def read_only(self) -> bool:
        return False

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        inst = cls()
        inst._ctx = ctx
        return inst

    def __init__(self) -> None:
        self._ctx: Any = None

    async def execute(
        self,
        action: str = "view",
        name: str | None = None,
        content: str | None = None,
        old_string: str | None = None,
        new_string: str | None = None,
        file_path: str | None = None,
        scope: str = "project",
        **kwargs: Any,
    ) -> str:
        # Normalize nullable string "none"
        for k in ("name", "content", "old_string", "new_string", "file_path"):
            v = locals().get(k)
            if isinstance(v, str) and v.strip().lower() == "none":
                if k == "name":
                    name = None
                elif k == "content":
                    content = None
                elif k == "old_string":
                    old_string = None
                elif k == "new_string":
                    new_string = None
                elif k == "file_path":
                    file_path = None

        workspace = getattr(getattr(self, "_ctx", None), "workspace", None) or os.getcwd()
        cwd = str(workspace)

        # --- list -------------------------------------------------------------
        if action == "list":
            result = load_openjarvis_skills(cwd)
            skills = sorted(result.skills, key=lambda s: s.name)
            if not skills:
                return (
                    "Available skills: (none) — VTX builtins hidden for OpenJarvis. "
                    "Create project skills in .agents/skills/."
                )
            lines = ["Available skills (VTX builtins hidden):"]
            for skill in skills:
                scope_label = (
                    "bundled" if skill.bundled else ("global" if "~" in skill.path else "project")
                )
                lines.append(f"- {skill.name} [{scope_label}]: {skill.description}")
            return "\n".join(lines)

        if not name:
            return "Error: Parameter 'name' is required for this action."

        skill_dir, is_bundled = _find_openjarvis_skill_dir(name, cwd)

        # --- view -------------------------------------------------------------
        if action == "view":
            if not skill_dir:
                return (
                    f"Error: Skill '{name}' not found in OpenJarvis scope (VTX builtins hidden)."
                )
            target = file_path or "SKILL.md"
            target_path = skill_dir / target
            try:
                return target_path.read_text(encoding="utf-8")
            except Exception as e:
                return f"Error reading skill file: {e}"

        # Mutating actions blocked on bundled
        if is_bundled:
            return f"Error: Cannot modify bundled skill '{name}'."

        # --- create -----------------------------------------------------------
        if action == "create":
            if not content:
                return "Error: Parameter 'content' is required to create a skill."
            if not name.islower() or not name.replace("-", "").isalnum():
                return "Error: Skill name must be lowercase, alphanumeric and hyphens only."
            if scope == "global":
                target_dir = (get_user_skills_dir() / "skills" / name).resolve(strict=False)
            else:
                target_dir = Path(cwd) / ".agents" / "skills" / name
            target_dir.mkdir(parents=True, exist_ok=True)
            try:
                (target_dir / "SKILL.md").write_text(content, encoding="utf-8")
                return f"Skill '{name}' created at {target_dir / 'SKILL.md'}."
            except Exception as e:
                return f"Error creating skill: {e}"

        if not skill_dir:
            return f"Error: Skill '{name}' not found."

        # --- delete -----------------------------------------------------------
        if action == "delete":
            try:
                shutil.rmtree(skill_dir)
                return f"Skill '{name}' deleted."
            except Exception as e:
                return f"Error deleting skill: {e}"

        target = file_path or "SKILL.md"
        target_path = skill_dir / target

        # --- edit -------------------------------------------------------------
        if action == "edit":
            if not content:
                return "Error: Parameter 'content' is required for edit."
            try:
                target_path.parent.mkdir(parents=True, exist_ok=True)
                target_path.write_text(content, encoding="utf-8")
                return f"Skill '{name}' updated at {target}."
            except Exception as e:
                return f"Error editing skill: {e}"

        # --- patch ------------------------------------------------------------
        if action == "patch":
            if old_string is None or new_string is None:
                return "Error: Parameters 'old_string' and 'new_string' are required for patch."
            if not target_path.is_file():
                return f"Error: File {target} not found in skill '{name}'."
            try:
                text = target_path.read_text(encoding="utf-8")
                count = text.count(old_string)
                if count == 0:
                    return f"Error: old_string not found in {target}."
                if count > 1:
                    return f"Error: old_string is not unique in {target} (found {count})."
                target_path.write_text(text.replace(old_string, new_string), encoding="utf-8")
                return f"Successfully patched {target} in skill '{name}'."
            except Exception as e:
                return f"Error patching skill: {e}"

        return f"Error: Unsupported action '{action}'."
