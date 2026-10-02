import asyncio
import os
import shutil
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from vtx.ai.agent.tools.base import BaseTool, ToolResult
from vtx.coding_agent.context.skills import (
    get_user_skills_dir,
    load_builtin_cmd_skills,
    load_skills,
    merge_registered_skills,
    skills_for_mode,
    strip_frontmatter,
)


class SkillParams(BaseModel):
    @model_validator(mode="before")
    @classmethod
    def _normalize_none_strings(cls, data: dict) -> dict:
        for key in ("name", "content", "old_string", "new_string", "file_path"):
            if isinstance(data.get(key), str) and data[key].strip().lower() == "none":
                data[key] = None
        return data

    action: Literal["load", "list", "view", "create", "patch", "edit", "delete", "run"] = Field(
        description=(
            "Action: 'load' (inject a skill's instructions into context, the normal way "
            "to use one), 'list' (discover), 'view' (raw file read), 'create' (new skill), "
            "'patch' (find-replace), 'edit' (overwrite), 'delete' (remove), "
            "or 'run' (execute skill instructions in REPL)"
        ),
        default="load",
    )
    name: str | None = Field(
        description="Skill name (lowercase/hyphens, e.g. 'review'). Required except for 'list'.",
        default=None,
    )
    content: str | None = Field(
        description="Full SKILL.md content (required for 'create' and 'edit')", default=None
    )
    old_string: str | None = Field(
        description="Unique exact text to replace (required for 'patch')", default=None
    )
    new_string: str | None = Field(
        description="Replacement text (required for 'patch')", default=None
    )
    file_path: str | None = Field(
        description="Supporting file to target (defaults to 'SKILL.md')", default=None
    )
    scope: Literal["project", "global"] = Field(
        description="Target scope for create: 'project' (.agents/skills) or 'global'",
        default="project",
    )


MAX_SKILL_FILES = 10


def _sibling_files(skill_dir: Path, *, exclude: str) -> list[str]:
    """Files beside the SKILL.md, sampled and capped.

    The model can `read` anything in the directory; this only tells it what is
    there, so a skill that points at `reference/api.md` does not also cost a
    directory listing to discover. Capped because the point is orientation, not
    an inventory, and an uncapped list is unbounded context on a big skill.
    """
    try:
        found = sorted(
            f.relative_to(skill_dir).as_posix()
            for f in skill_dir.rglob("*")
            if f.is_file() and f.name != exclude and "__pycache__" not in f.parts
        )
    except OSError:
        return []
    return found[:MAX_SKILL_FILES]


class SkillTool(BaseTool):
    name = "skill"
    tool_icon = "⚙"
    params = SkillParams
    mutating = True  # Can modify skills, though load/list/view are read-only
    prompt_guidelines = ()
    description = (
        "Load a skill's instructions into the conversation. Call action='load' with the skill "
        "name from <available_skills> when a task matches its description; the output is the "
        "full skill body plus the skill's directory and the files beside it, so relative paths "
        "inside the skill resolve without a separate read. Also manages skills: 'list' to "
        "discover, 'view' for a raw file read, 'create'/'edit' to author SKILL.md files, "
        "'patch' for targeted replacements, 'delete' to remove a skill, 'run' to execute a "
        "skill's instructions in the REPL."
    )

    def format_call(self, params: SkillParams) -> str:
        name_str = f" name={params.name}" if params.name else ""
        file_str = f" file={params.file_path}" if params.file_path else ""
        return f"{params.action}{name_str}{file_str}"

    async def execute(
        self, params: SkillParams, cancel_event: asyncio.Event | None = None
    ) -> ToolResult:
        cwd = os.getcwd()

        # Handle 'list' action
        if params.action == "list":
            result = load_skills(cwd)
            builtin = load_builtin_cmd_skills()
            all_skills = merge_registered_skills(result.skills, builtin.skills)
            # A python skill is a kernel module, not a set of instructions. With
            # no kernel it can only dead-end, so don't offer it for discovery.
            all_skills = skills_for_mode(all_skills)
            lines = ["Available skills:"]
            for skill in sorted(all_skills, key=lambda s: s.name):
                path_str = str(skill.path)
                if skill.bundled:
                    scope = "bundled"
                elif "skills" in path_str and ("~" in path_str or "/.agents/" not in path_str):
                    scope = "global"
                else:
                    scope = "project"
                lines.append(f"- {skill.name} [{scope}]: {skill.description}")
            result_text = "\n".join(lines)
            return ToolResult(
                success=True,
                result=result_text,
                ui_summary=f"[dim]({len(all_skills)} skills)[/dim]",
            )

        if not params.name:
            msg = "Parameter 'name' is required for this action."
            return ToolResult(success=False, result=msg, ui_summary=f"[red]{msg}[/red]")

        # Helper to find skill directory
        def find_skill_dir(name: str) -> tuple[Path | None, bool]:
            # 1. Project skills
            from vtx.coding_agent.context.skills import _project_skill_dirs

            project_dirs = _project_skill_dirs(Path(cwd))
            for skills_dir in project_dirs:
                if skills_dir.exists():
                    skill_dir = skills_dir / name
                    if (skill_dir / "SKILL.md").is_file():
                        return skill_dir, False

            # 2. User global skills
            user_skills_dir = (get_user_skills_dir() / "skills").resolve(strict=False)
            skill_dir = user_skills_dir / name
            if (skill_dir / "SKILL.md").is_file():
                return skill_dir, False

            # 3. Vtx config skills (~/.vtx/skills/ - synced builtins)
            from vtx.coding_agent import get_config_dir as get_vtx_config_dir

            vtx_skills_dir = (get_vtx_config_dir() / "skills").resolve(strict=False)
            skill_dir = vtx_skills_dir / name
            if (skill_dir / "SKILL.md").is_file():
                return skill_dir, True

            # 4. Builtin skills (search recursively through category folders)
            from importlib import resources

            try:
                builtin_resource = resources.files("vtx.coding_agent").joinpath("builtin_skills")
                with resources.as_file(builtin_resource) as builtin_root:
                    for candidate in builtin_root.rglob(name):
                        if candidate.is_dir() and (candidate / "SKILL.md").is_file():
                            return candidate, True
            except Exception:
                pass

            return None, False

        skill_dir, is_builtin = find_skill_dir(params.name)

        # Handle 'load' action: the model's normal way to use a skill.
        if params.action == "load":
            if not skill_dir:
                msg = f"Skill '{params.name}' not found."
                return ToolResult(success=False, result=msg, ui_summary=f"[red]{msg}[/red]")

            target_file = params.file_path or "SKILL.md"
            target_path = skill_dir / target_file
            try:
                body = strip_frontmatter(target_path.read_text(encoding="utf-8"))
            except Exception as e:
                msg = f"Failed to read skill file: {e}"
                return ToolResult(success=False, result=msg, ui_summary=f"[red]{msg}[/red]")

            files = _sibling_files(skill_dir, exclude=target_path.name)
            lines = [
                f'<skill_content name="{params.name}">',
                "",
                body.strip(),
                "",
                f"Base directory for this skill: {skill_dir}",
                "Relative paths in this skill (scripts/, reference/) are relative to this base",
                "directory, not the current working directory.",
            ]
            if files:
                lines += ["", "Files in this skill directory:"]
                lines += [f"- {name}" for name in files]
            lines.append("</skill_content>")
            return ToolResult(success=True, result="\n".join(lines))

        # Handle 'view' action
        if params.action == "view":
            if not skill_dir:
                msg = f"Skill '{params.name}' not found."
                return ToolResult(success=False, result=msg, ui_summary=f"[red]{msg}[/red]")

            target_file = params.file_path or "SKILL.md"
            target_path = skill_dir / target_file
            try:
                content = target_path.read_text(encoding="utf-8")
                return ToolResult(
                    success=True,
                    result=content,
                    ui_summary=f"[dim]({len(content.splitlines())} lines)[/dim]",
                )
            except Exception as e:
                msg = f"Failed to read skill file: {e}"
                return ToolResult(success=False, result=msg, ui_summary=f"[red]{msg}[/red]")

        # Handle 'run' action - hand the skill to the model
        if params.action == "run":
            # `run` used to hand the skill to the persistent Python kernel.
            # That kernel went with the RLM mode, so there is nothing to run it
            # in. Say so and return the file rather than failing outright: a
            # markdown skill's body is instructions, and the model can follow
            # them itself.
            msg = (
                "action='run' needs the persistent Python kernel, which no longer "
                "exists. Returning the skill file instead -- use action='view' if "
                "you only want the path."
            )

            if not skill_dir:
                msg = f"Skill '{params.name}' not found."
                return ToolResult(success=False, result=msg, ui_summary=f"[red]{msg}[/red]")

            target_file = params.file_path or "SKILL.md"
            target_path = skill_dir / target_file
            try:
                skill_content = target_path.read_text(encoding="utf-8")
            except Exception as e:
                msg = f"Failed to read skill file: {e}"
                return ToolResult(success=False, result=msg, ui_summary=f"[red]{msg}[/red]")

            # Executing a skill meant running its body in the persistent kernel, which
            # went with the RLM mode. A python skill has no runtime now, and a
            # markdown skill's body is instructions for a model rather than
            # code, so the file is handed back and the model reads it.
            return ToolResult(
                success=True,
                result=f"{msg}\n\n---\n{skill_content}",
                ui_summary=f"Read skill '{params.name}'",
            )

        # Mutating actions: 'create', 'edit', 'patch', 'delete'
        if is_builtin:
            msg = f"Cannot modify built-in skill '{params.name}'."
            return ToolResult(success=False, result=msg, ui_summary=f"[red]{msg}[/red]")

        # Handle 'create' action
        if params.action == "create":
            if not params.content:
                msg = "Parameter 'content' is required to create a skill."
                return ToolResult(success=False, result=msg, ui_summary=f"[red]{msg}[/red]")

            # Validate name format
            if not params.name.islower() or not params.name.replace("-", "").isalnum():
                msg = "Skill name must be lowercase, alphanumeric, and hyphens only."
                return ToolResult(success=False, result=msg, ui_summary=f"[red]{msg}[/red]")

            # Resolve target path based on scope
            if params.scope == "global":
                target_skills_dir = (get_user_skills_dir() / "skills").resolve(strict=False)
            else:
                target_skills_dir = Path(cwd) / ".agents" / "skills"

            new_skill_dir = target_skills_dir / params.name
            new_skill_dir.mkdir(parents=True, exist_ok=True)
            skill_md_path = new_skill_dir / "SKILL.md"

            try:
                skill_md_path.write_text(params.content, encoding="utf-8")
                return ToolResult(
                    success=True,
                    result=f"Skill '{params.name}' created at {skill_md_path}.",
                    ui_summary=f"Created '{params.name}'",
                )
            except Exception as e:
                msg = f"Failed to create skill: {e}"
                return ToolResult(success=False, result=msg, ui_summary=f"[red]{msg}[/red]")

        # For edit, patch, delete, we need the skill to exist
        if not skill_dir:
            msg = f"Skill '{params.name}' not found."
            return ToolResult(success=False, result=msg, ui_summary=f"[red]{msg}[/red]")

        # Handle 'delete' action
        if params.action == "delete":
            try:
                shutil.rmtree(skill_dir)
                return ToolResult(
                    success=True,
                    result=f"Skill '{params.name}' deleted.",
                    ui_summary=f"Deleted '{params.name}'",
                )
            except Exception as e:
                msg = f"Failed to delete skill: {e}"
                return ToolResult(success=False, result=msg, ui_summary=f"[red]{msg}[/red]")

        target_file = params.file_path or "SKILL.md"
        target_path = skill_dir / target_file

        # Handle 'edit' action
        if params.action == "edit":
            if not params.content:
                msg = "Parameter 'content' is required for edit."
                return ToolResult(success=False, result=msg, ui_summary=f"[red]{msg}[/red]")

            try:
                target_path.parent.mkdir(parents=True, exist_ok=True)
                target_path.write_text(params.content, encoding="utf-8")
                return ToolResult(
                    success=True,
                    result=f"Skill '{params.name}' updated at {target_file}.",
                    ui_summary=f"Edited {target_file}",
                )
            except Exception as e:
                msg = f"Failed to edit skill: {e}"
                return ToolResult(success=False, result=msg, ui_summary=f"[red]{msg}[/red]")

        # Handle 'patch' action
        if params.action == "patch":
            if params.old_string is None or params.new_string is None:
                msg = "Parameters 'old_string' and 'new_string' are required for patch."
                return ToolResult(success=False, result=msg, ui_summary=f"[red]{msg}[/red]")

            if not target_path.is_file():
                msg = f"File {target_file} not found in skill '{params.name}'."
                return ToolResult(success=False, result=msg, ui_summary=f"[red]{msg}[/red]")

            try:
                content = target_path.read_text(encoding="utf-8")
                count = content.count(params.old_string)
                if count == 0:
                    msg = f"Target string 'old_string' not found in {target_file}."
                    return ToolResult(success=False, result=msg, ui_summary=f"[red]{msg}[/red]")
                if count > 1:
                    msg = (
                        f"Target string 'old_string' is not unique in {target_file} "
                        f"(found {count} occurrences)."
                    )
                    return ToolResult(success=False, result=msg, ui_summary=f"[red]{msg}[/red]")

                new_content = content.replace(params.old_string, params.new_string)
                target_path.write_text(new_content, encoding="utf-8")
                return ToolResult(
                    success=True,
                    result=f"Successfully patched {target_file} in skill '{params.name}'.",
                    ui_summary=f"Patched {target_file}",
                )
            except Exception as e:
                msg = f"Failed to patch skill: {e}"
                return ToolResult(success=False, result=msg, ui_summary=f"[red]{msg}[/red]")

        msg = f"Unsupported action '{params.action}'."
        return ToolResult(success=False, result=msg, ui_summary=f"[red]{msg}[/red]")
