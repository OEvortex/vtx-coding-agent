"""/skills and /tools - the catalog behind the launch summary line.

Launch prints counts, not lists: ~50 skills and every MCP tool is a wall of
text that pushes the prompt off a short terminal, and nothing in it is needed
before the first message. The lists live here instead, grouped so they read as
a directory: skills by category, tools by the server that contributes them.
"""

from __future__ import annotations

from pathlib import Path

from vtx.agent.context.skills import Skill
from vtx.core.paths import get_agents_dir
from vtx.tui.chat import ChatLog
from vtx.tui.commands.base import CommandSupport

#: Category title when a skill declares none.
UNCATEGORIZED = "general"


def _is_local_skill(skill: Skill) -> bool:
    """Project/`.agents` skill rather than the global or bundled catalog.

    Only the ones the user or the project actually added get marked: a catalog
    where half the entries read `(local)` marks nothing.
    """
    if skill.bundled:
        return False
    global_skills_dir = (get_agents_dir() / "skills").resolve(strict=False)
    return not Path(skill.path).resolve(strict=False).is_relative_to(global_skills_dir)


def skill_sections(skills: list[Skill]) -> list[tuple[str, list[tuple[str, str]]]]:
    """Skills grouped by category, local ones marked, counts in the titles."""
    groups: dict[str, list[Skill]] = {}
    for skill in skills:
        groups.setdefault(skill.category or UNCATEGORIZED, []).append(skill)

    sections: list[tuple[str, list[tuple[str, str]]]] = []
    for category in sorted(groups):
        members = sorted(groups[category], key=lambda skill: skill.name)
        rows = [(skill.name, "(local)" if _is_local_skill(skill) else "") for skill in members]
        sections.append((f"{category} ({len(members)})", rows))
    return sections


def tool_label(tool) -> str:
    """`server > tool` for MCP tools, the bare name otherwise.

    The wire name is `mcp__ddg-search__web_search` because providers only
    accept `[A-Za-z0-9_-]`; the separator belongs to the provider, not to the
    human reading the list.
    """
    server = getattr(tool, "server", None)
    tool_name = getattr(tool, "tool_name", None)
    if server and tool_name:
        return f"{server} > {tool_name}"
    return tool.name


def tool_sections(tools: list) -> list[tuple[str, list[tuple[str, str]]]]:
    """Built-in tools first, then one section per MCP server."""
    builtin: list[str] = []
    servers: dict[str, list[str]] = {}
    for tool in tools:
        server = getattr(tool, "server", None)
        if server:
            servers.setdefault(server, []).append(tool_label(tool))
        else:
            builtin.append(tool.name)

    sections: list[tuple[str, list[tuple[str, str]]]] = []
    if builtin:
        sections.append((f"built-in ({len(builtin)})", [(name, "") for name in sorted(builtin)]))
    for server in sorted(servers):
        names = sorted(servers[server])
        sections.append((f"{server} ({len(names)})", [(name, "") for name in names]))
    return sections


class CatalogCommands(CommandSupport):
    def _handle_skills_command(self) -> None:
        chat = self.query_one("#chat-log", ChatLog)
        context = getattr(self._runtime, "context", None)
        skills = list(getattr(context, "skills", None) or [])
        if not skills:
            chat.add_info_message("No skills are loaded in this session.", warning=True)
            return
        sections = skill_sections(skills)
        for path, message in getattr(context, "skill_warnings", []) or []:
            chat.add_info_message(f"Warning in {path}: {message}", warning=True)
        chat.add_catalog_details(sections)

    def _handle_tools_command(self) -> None:
        chat = self.query_one("#chat-log", ChatLog)
        tools = list(getattr(self._runtime, "tools", None) or [])
        if not tools:
            chat.add_info_message("No tools are loaded in this session.", warning=True)
            return
        chat.add_catalog_details(tool_sections(tools))
