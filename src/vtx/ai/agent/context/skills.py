"""
Skills discovery and loading.

Skills are directories containing a SKILL.md file with frontmatter.
They provide specialized instructions that the model can read on-demand.

Discovery locations:
1. User: ~/.agents/skills/
2. Project: <cwd-or-ancestor>/.agents/skills/
"""

import os
import re
import shutil
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from vtx.core.paths import get_agents_dir as get_user_skills_dir
from vtx.core.paths import get_config_dir as get_vtx_config_dir

from ._xml import escape_xml

MAX_NAME_LENGTH = 64
MAX_DESCRIPTION_LENGTH = 1024
MAX_CMD_INFO_LENGTH = 32
MAX_CATEGORY_LENGTH = 32
DEFAULT_SKILL_CATEGORY = "general"


def shorten_path(path: str) -> str:
    home = os.path.expanduser("~")
    if path.startswith(home):
        path = "~" + path[len(home) :]
    return path.replace(os.sep, "/")


def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


@dataclass
class SkillPythonMetadata:
    import_name: str
    package_path: str
    pyproject_path: str


@dataclass
class Skill:
    path: str
    name: str
    description: str
    register_cmd: bool = True
    cmd_info: str = ""
    include_in_prompt: bool = True
    bundled: bool = False
    category: str = DEFAULT_SKILL_CATEGORY
    kind: str = "markdown"  # "markdown" | "python"
    python: SkillPythonMetadata | None = None


@dataclass
class SkillWarning:
    path: str
    message: str


def is_kernel_skill(skill: Any) -> bool:
    """Whether a skill is a Python-backed module with no way to run it.

    A python skill ships a ``pyproject.toml`` plus ``src/<import_name>``, and its
    SKILL.md body is API documentation for that module ("call
    ``await compact.run()`` from the REPL") rather than instructions to follow.
    It was imported into the persistent IPython kernel that the RLM mode
    provided.

    That kernel is gone -- :mod:`vtx.ai.agent.codemode` runs a confined script
    in a subprocess instead -- so every python skill is now unrunnable and is
    hidden from discovery. The predicate stays because it is how a skill is
    recognised as unrunnable, which is a fact about the skill, not about a mode
    that no longer exists.
    """
    return (
        getattr(skill, "kind", "markdown") == "python"
        and getattr(skill, "python", None) is not None
    )


def skills_for_mode(skills: list[Any], mode: str | None = None) -> list[Any]:
    """Filter out skills the agent cannot actually act on.

    Discovery surfaces (the mandatory prompt catalog, ``skill(action="list")``,
    the ``/`` command list) must only offer skills the agent can act on. An
    unrunnable python skill is worse than absent: its description reads as
    generally applicable and its body is API documentation for a module there is
    no interpreter to call, so loading it spends context and then dead-ends.
    """
    return [skill for skill in skills if not is_kernel_skill(skill)]


@dataclass
class LoadSkillsResult:
    skills: list[Skill]
    warnings: list[SkillWarning]


def _strip_inline_comment(value: str) -> str:
    quote_char = ""
    escaped = False
    for i, char in enumerate(value):
        if escaped:
            escaped = False
            continue
        if char == "\\" and quote_char:
            escaped = True
            continue
        if char in ('"', "'"):
            if not quote_char:
                quote_char = char
            elif quote_char == char:
                quote_char = ""
            continue
        if char == "#" and not quote_char and (i == 0 or value[i - 1].isspace()):
            return value[:i].rstrip()
    return value


def _parse_frontmatter(content: str) -> dict[str, Any]:
    if not content.startswith("---"):
        return {}

    end_match = re.search(r"\n---\s*\n", content[3:])
    if not end_match:
        return {}

    frontmatter_text = content[3 : end_match.start() + 3]

    # Real YAML: the naive key/value scan below silently truncated folded block
    # scalars (``description: >``) to a bare ">", which cost ~15 bundled skills
    # their entire description in the skill index and the / command list.
    try:
        import yaml

        parsed = yaml.safe_load(frontmatter_text)
        if isinstance(parsed, dict):
            return {str(key): value for key, value in parsed.items()}
    except Exception:
        pass

    result: dict[str, Any] = {}
    for line in frontmatter_text.split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            key, _, value = line.partition(":")
            key = key.strip()
            value = _strip_inline_comment(value.strip())
            if value and value[0] in ('"', "'") and value[-1] == value[0]:
                value = value[1:-1]
            result[key] = value

    return result


def _validate_skill(
    name: str,
    description: str,
    parent_dir_name: str,
    file_path: str,
    cmd_info: str = "",
    category: str = DEFAULT_SKILL_CATEGORY,
) -> list[SkillWarning]:
    warnings: list[SkillWarning] = []

    if name != parent_dir_name:
        warnings.append(
            SkillWarning(file_path, f'name "{name}" does not match directory "{parent_dir_name}"')
        )

    if len(name) > MAX_NAME_LENGTH:
        warnings.append(SkillWarning(file_path, f"name exceeds {MAX_NAME_LENGTH} characters"))

    if not re.match(r"^[a-z0-9-]+$", name):
        warnings.append(SkillWarning(file_path, "name must be lowercase a-z, 0-9, hyphens only"))

    if name.startswith("-") or name.endswith("-"):
        warnings.append(SkillWarning(file_path, "name must not start or end with hyphen"))

    if "--" in name:
        warnings.append(SkillWarning(file_path, "name must not contain consecutive hyphens"))

    if not description or not description.strip():
        warnings.append(SkillWarning(file_path, "description is required"))

    if len(description) > MAX_DESCRIPTION_LENGTH:
        warnings.append(
            SkillWarning(file_path, f"description exceeds {MAX_DESCRIPTION_LENGTH} characters")
        )

    if len(cmd_info) > MAX_CMD_INFO_LENGTH:
        warnings.append(
            SkillWarning(file_path, f"cmd_info exceeds {MAX_CMD_INFO_LENGTH} characters")
        )

    if len(category) > MAX_CATEGORY_LENGTH:
        warnings.append(
            SkillWarning(file_path, f"category exceeds {MAX_CATEGORY_LENGTH} characters")
        )

    return warnings


def _load_skill_from_dir(skill_dir: Path) -> tuple[Skill | None, list[SkillWarning]]:
    skill_file = skill_dir / "SKILL.md"
    if not skill_file.is_file():
        return None, []

    warnings: list[SkillWarning] = []
    file_path = str(skill_file)

    try:
        content = skill_file.read_text(encoding="utf-8")
        frontmatter = _parse_frontmatter(content)

        parent_dir_name = skill_dir.name
        name = str(frontmatter.get("name") or parent_dir_name)
        description = str(frontmatter.get("description") or "")
        cmd_info = str(frontmatter.get("cmd_info") or "").strip()
        category_raw = str(frontmatter.get("category") or "").strip().lower()
        category = category_raw or DEFAULT_SKILL_CATEGORY

        warnings = _validate_skill(
            name, description, parent_dir_name, file_path, cmd_info=cmd_info, category=category
        )

        if not description or not description.strip():
            return None, warnings

        # Detect Python-backed skill:
        # Detect Python-backed skill: pyproject.toml + src/<import_name>/__init__.py
        kind = "markdown"
        python_meta: SkillPythonMetadata | None = None
        pyproject_path = skill_dir / "pyproject.toml"
        if pyproject_path.is_file():
            import_name = name.replace("-", "_")
            if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", import_name):
                warnings.append(
                    SkillWarning(
                        str(pyproject_path), f'python skill import name "{import_name}" is invalid'
                    )
                )
            else:
                pkg_init_path = skill_dir / "src" / import_name / "__init__.py"
                if pkg_init_path.is_file():
                    kind = "python"
                    python_meta = SkillPythonMetadata(
                        import_name=import_name,
                        package_path=str(skill_dir),
                        pyproject_path=str(pyproject_path),
                    )
                else:
                    warnings.append(
                        SkillWarning(
                            str(pyproject_path),
                            f"python skill package src/{import_name}/__init__.py not found",
                        )
                    )

        # register_cmd is opt-in, as documented (AGENTS.md, docs/skills.md):
        # a skill is context for the agent, and only becomes a user-facing
        # /command when it asks to be. Defaulting this to True put all 56
        # skills in the slash list, and let a skill named `compact` shadow the
        # real /compact command.
        register_cmd_raw = frontmatter.get("register_cmd", False)
        if isinstance(register_cmd_raw, str):
            register_cmd_value = register_cmd_raw.strip().lower()
            cmd_only = register_cmd_value == "only"
            register_cmd = cmd_only or _parse_bool(register_cmd_raw)
        else:
            cmd_only = False
            register_cmd = bool(register_cmd_raw)

        skill = Skill(
            name=name,
            description=description,
            path=file_path,
            register_cmd=register_cmd,
            cmd_info=cmd_info,
            include_in_prompt=not cmd_only,
            category=category,
            kind=kind,
            python=python_meta,
        )
        return skill, warnings

    except Exception as e:
        return None, [SkillWarning(file_path, str(e))]


def _load_skills_from_dir(
    directory: Path, *, legacy_warning: str | None = None
) -> LoadSkillsResult:
    skills: list[Skill] = []
    warnings: list[SkillWarning] = []

    if not directory.exists():
        return LoadSkillsResult(skills=skills, warnings=warnings)

    if legacy_warning:
        warnings.append(SkillWarning(str(directory), legacy_warning))

    try:
        for entry in directory.iterdir():
            if entry.name.startswith("."):
                continue
            if not entry.is_dir():
                continue

            skill, skill_warnings = _load_skill_from_dir(entry)
            warnings.extend(skill_warnings)
            if skill:
                skills.append(skill)

    except Exception:
        pass

    return LoadSkillsResult(skills=skills, warnings=warnings)


def _load_skills_recursive(directory: Path, *, max_depth: int = 2) -> LoadSkillsResult:
    skills: list[Skill] = []
    warnings: list[SkillWarning] = []

    if not directory.exists():
        return LoadSkillsResult(skills=skills, warnings=warnings)

    def _walk(current: Path, depth: int) -> None:
        if depth > max_depth:
            return
        try:
            for entry in current.iterdir():
                if entry.name.startswith("."):
                    continue
                if not entry.is_dir():
                    continue
                skill, skill_warnings = _load_skill_from_dir(entry)
                warnings.extend(skill_warnings)
                if skill:
                    skills.append(skill)
                else:
                    _walk(entry, depth + 1)
        except Exception:
            pass

    _walk(directory, 0)
    return LoadSkillsResult(skills=skills, warnings=warnings)


def _find_git_root(start: Path, boundary: Path | None = None) -> Path | None:
    if boundary is not None:
        boundary = boundary.resolve()
    current = start.resolve()
    while True:
        if (current / ".git").is_dir():
            return current
        parent = current.parent
        if parent == current:
            return None
        if boundary is not None and not parent.is_relative_to(boundary):
            return None
        current = parent


def _project_skill_dirs(cwd: Path) -> list[Path]:
    git_root = _find_git_root(cwd)
    stop_dir = git_root or cwd
    dirs: list[Path] = []
    current = cwd
    while True:
        dirs.append((current / ".agents" / "skills").resolve(strict=False))
        if current == stop_dir:
            break
        current = current.parent
    return dirs


_REGISTERED_SKILL_PACKAGES: list[str] = []


def register_skills_package(package_name: str) -> None:
    """Register a Python package name containing a `builtin_skills` directory."""
    if package_name not in _REGISTERED_SKILL_PACKAGES:
        _REGISTERED_SKILL_PACKAGES.append(package_name)


def unregister_skills_package(package_name: str) -> None:
    """Unregister a skills package."""
    if package_name in _REGISTERED_SKILL_PACKAGES:
        _REGISTERED_SKILL_PACKAGES.remove(package_name)


def get_registered_skills_packages() -> list[str]:
    """Return all registered skills packages."""
    return list(_REGISTERED_SKILL_PACKAGES)


def sync_builtin_skills(package_names: str | list[str] | None = None) -> None:
    """Copy built-in skills from registered packages to ~/.vtx/skills/ for filesystem access."""
    if package_names is None:
        pkgs = get_registered_skills_packages()
    elif isinstance(package_names, str):
        pkgs = [package_names]
    else:
        pkgs = list(package_names)

    dst_root = (get_vtx_config_dir() / "skills").resolve(strict=False)
    for pkg in pkgs:
        try:
            builtin_resource = resources.files(pkg).joinpath("builtin_skills")
            with resources.as_file(builtin_resource) as src_root:
                if not src_root.is_dir():
                    continue
                dst_root.mkdir(parents=True, exist_ok=True)
                for entry in src_root.iterdir():
                    if not entry.is_dir():
                        continue
                    for skill_dir in entry.iterdir():
                        if not skill_dir.is_dir() or skill_dir.name.startswith("."):
                            continue
                        if not (skill_dir / "SKILL.md").is_file():
                            continue
                        dst = dst_root / skill_dir.name
                        if dst.exists():
                            shutil.rmtree(dst)
                        shutil.copytree(skill_dir, dst)
        except Exception:
            pass


def load_skills(cwd: str | None = None) -> LoadSkillsResult:
    """
    Load skills from ~/.vtx, ~/.agents and project .agents locations.

    Discovery:
    1. <cwd-or-ancestor>/.agents/skills/ - each subdirectory with SKILL.md is a skill
    2. ~/.agents/skills/ - each subdirectory with SKILL.md is a skill
    3. ~/.vtx/skills/ - synced built-in skills (lowest priority)

    Local skills take precedence over global skills with the same name.
    """
    resolved_cwd = Path(cwd) if cwd else Path.cwd()
    resolved_cwd = resolved_cwd.resolve()

    skill_map: dict[str, Skill] = {}
    all_warnings: list[SkillWarning] = []

    def add_skills(result: LoadSkillsResult) -> None:
        all_warnings.extend(result.warnings)
        for skill in result.skills:
            if skill.name in skill_map:
                all_warnings.append(
                    SkillWarning(
                        skill.path,
                        f'name collision: "{skill.name}" already loaded '
                        f"from {shorten_path(skill_map[skill.name].path)}",
                    )
                )
            else:
                skill_map[skill.name] = skill

    project_skills_dirs = _project_skill_dirs(resolved_cwd)
    for skills_dir in project_skills_dirs:
        add_skills(_load_skills_from_dir(skills_dir))

    user_skills_dir = (get_user_skills_dir() / "skills").resolve(strict=False)
    if user_skills_dir not in project_skills_dirs:
        add_skills(_load_skills_from_dir(user_skills_dir))

    vtx_skills_dir = (get_vtx_config_dir() / "skills").resolve(strict=False)
    if vtx_skills_dir not in project_skills_dirs and vtx_skills_dir != user_skills_dir:
        result = _load_skills_from_dir(vtx_skills_dir)
        for skill in result.skills:
            skill.bundled = True
        add_skills(result)

    return LoadSkillsResult(skills=list(skill_map.values()), warnings=all_warnings)


def load_builtin_cmd_skills(package_names: str | list[str] | None = None) -> LoadSkillsResult:
    """Load built-in registered slash command skills from registered packages."""
    if package_names is None:
        pkgs = get_registered_skills_packages()
    elif isinstance(package_names, str):
        pkgs = [package_names]
    else:
        pkgs = list(package_names)

    all_skills: list[Skill] = []
    all_warnings: list[SkillWarning] = []
    for pkg in pkgs:
        try:
            builtin_resource = resources.files(pkg).joinpath("builtin_skills")
            with resources.as_file(builtin_resource) as builtin_root:
                result = _load_skills_recursive(builtin_root)
                all_skills.extend(
                    [
                        Skill(
                            path=skill.path,
                            name=skill.name,
                            description=skill.description,
                            register_cmd=skill.register_cmd,
                            cmd_info=skill.cmd_info,
                            include_in_prompt=skill.include_in_prompt,
                            bundled=True,
                            category=skill.category,
                            kind=skill.kind,
                            python=skill.python,
                        )
                        for skill in result.skills
                    ]
                )
                all_warnings.extend(result.warnings)
        except Exception:
            pass
    return LoadSkillsResult(skills=all_skills, warnings=all_warnings)


def strip_frontmatter(content: str) -> str:
    if not content.startswith("---"):
        return content.strip()
    end_match = re.search(r"\n---\s*\n", content[3:])
    if not end_match:
        return content.strip()
    return content[end_match.end() + 3 :].strip()


def render_skill_prompt(skill: Skill, query: str) -> str:
    try:
        content = Path(skill.path).read_text(encoding="utf-8")
    except Exception:
        return _build_fallback_skill_prompt(skill.description, query)
    template = strip_frontmatter(content)
    if "$ARGUMENTS" in template:
        rendered = template.replace("$ARGUMENTS", query).strip()
    else:
        rendered = template.strip()
        if query.strip():
            rendered = f"{rendered}\n\n{query.strip()}"
    skill_dir = str(Path(skill.path).parent)
    return (
        f'<skill name="{escape_xml(skill.name)}" location="{escape_xml(skill.path)}">\n'
        f"References are relative to {skill_dir}.\n"
        f"\n"
        f"{rendered}\n"
        f"</skill>"
    )


def _build_fallback_skill_prompt(description: str, query: str) -> str:
    query = query.strip()
    if not query:
        return description
    return f"{description}\n\n{query}"


def merge_registered_skills(primary: list[Skill], secondary: list[Skill]) -> list[Skill]:
    seen = {skill.name for skill in primary}
    merged = list(primary)
    for skill in secondary:
        if skill.name in seen:
            continue
        merged.append(skill)
        seen.add(skill.name)
    return merged


def formatted_skills(skills: list[Skill]) -> str:
    skills = [skill for skill in skills if skill.include_in_prompt]
    if not skills:
        return ""

    # One listing only. A grouped name index used to precede the XML, which
    # named every skill a second time with no description or path, so the
    # catalog shipped doubled on every turn. The XML is the listing; the python
    # import it carries per entry is what the index used to add.
    #
    # No <location>. The catalog names skills for the `skill` tool to load by,
    # and the tool resolves the path itself; shipping the path as well made the
    # model read the file directly, skipping the tool's base-directory banner
    # and its relative-path resolution rules.
    skill_tags: list[str] = []
    for skill in sorted(skills, key=lambda s: s.name):
        skill_tags.append("  <skill>")
        skill_tags.append(f"    <name>{escape_xml(skill.name)}</name>")
        skill_tags.append(f"    <description>{escape_xml(skill.description)}</description>")
        if skill.kind == "python" and skill.python:
            skill_tags.append(
                f"    <python_import>{escape_xml(skill.python.import_name)}</python_import>"
            )
        skill_tags.append("  </skill>")

    rules = [
        "Skills provide specialized instructions and workflows for specific tasks.",
        "Before replying, scan the skills below. If a skill matches or is even partially relevant",
        'to your task, you MUST load it with `skill(action="load", name="...")` and follow its',
        "instructions. Err on the side of loading — it is always better to have context you don't",
        "need than to miss critical steps, pitfalls, or established workflows.",
        "Each load returns the skill's directory; paths inside a skill are relative to it.",
        "If a skill is manually triggered via slash command, its full content is already included",
        "in the user message, so you don't need to load it again.",
    ]
    lines = [
        "## Skills (mandatory)",
        "",
        *rules,
        "",
        "<available_skills>",
        *skill_tags,
        "</available_skills>",
        "",
        "Only proceed without loading a skill if genuinely none are relevant to the task.",
    ]

    return "\n".join(lines)


#: Characters per token when costing an index line. The same 4-chars-per-token
#: estimate pi's codemode catalog uses; a rough estimate is enough to decide
#: which lines fit, and being wrong only moves the cutoff by a few entries.
CHARS_PER_TOKEN = 4

#: Default ceiling for the compact skills index, in estimated tokens. The index
#: is one line per skill, so it grows with the installed set; the ceiling keeps
#: it from competing with the rest of the prompt.
DEFAULT_SKILLS_INDEX_BUDGET_TOKENS = 1200


def _skill_index_line(skill: Skill, max_desc_chars: int) -> str:
    desc = re.sub(r"\s+", " ", skill.description or "").strip()
    if len(desc) > max_desc_chars:
        desc = desc[: max_desc_chars - 3].rstrip() + "..."
    if skill.kind == "python" and skill.python:
        return f"- {skill.name} (python `{skill.python.import_name}`): {desc}"
    return f"- {skill.name}: {desc}"


def _select_skill_index_lines(
    skills: list[Skill], max_desc_chars: int, budget_tokens: int | None
) -> tuple[list[str], int]:
    """Pick index lines that fit ``budget_tokens``, spread across categories.

    Selection is round-robin over categories, cheapest line first within each.
    A plain cheapest-first pass would fill the budget with one category's short
    lines and drop every other category entirely, which is worse than useless:
    the model then cannot tell that the omitted skills exist. Round-robin
    guarantees each category is represented before any category is complete,
    and a category whose next line does not fit drops out while the others
    continue, so a single oversized category cannot starve the rest.

    Returns ``(selected_lines, omitted_count)``.
    """
    groups: dict[str, list[tuple[str, int]]] = {}
    for skill in skills:
        line = _skill_index_line(skill, max_desc_chars)
        cost = max(1, -(-len(line) // CHARS_PER_TOKEN))  # ceil
        groups.setdefault(skill.category or DEFAULT_SKILL_CATEGORY, []).append((line, cost))

    # Ungrouped first, then categories by name, so the ordering is stable and
    # does not depend on dict insertion order from discovery.
    ordered = sorted(groups.items(), key=lambda item: item[0])
    if budget_tokens is None:
        selected = [line for _, entries in ordered for line, _ in entries]
        return selected, 0

    queues = [sorted(entries, key=lambda entry: entry[1]) for _, entries in ordered]
    remaining = budget_tokens
    chosen: list[tuple[int, str]] = []
    active = [index for index, queue in enumerate(queues) if queue]
    while active:
        placed = 0
        still_active: list[int] = []
        for index in active:
            line, cost = queues[index][0]
            if cost > remaining:
                continue  # drop out; the other categories keep going
            remaining -= cost
            placed += 1
            chosen.append((index, line))
            queues[index].pop(0)
            if queues[index]:
                still_active.append(index)
        if not placed:
            # Nothing fit this round, so nothing will fit a later one either.
            break
        active = still_active

    chosen.sort(key=lambda entry: entry[0])
    return [line for _, line in chosen], sum(len(queue) for queue in queues)


def formatted_skills_index(
    skills: list[Skill],
    *,
    max_desc_chars: int = 120,
    budget_tokens: int | None = DEFAULT_SKILLS_INDEX_BUDGET_TOKENS,
) -> str:
    """Compact one-line-per-skill index, for when the full catalog is too large.

    The full :func:`formatted_skills` catalog (~24k chars for a typical install)
    is a fixed ~6k-token cost on every turn. Where the prompt already tells the
    model to read SKILL.md on demand, a routing index is enough: it reads the
    full file only for skills it will actually use.

    The index is capped at ``budget_tokens`` estimated tokens, spread across
    skill categories so a large category cannot crowd the others out of the
    prompt entirely. Omitted skills stay reachable: they are named as omitted
    and the model can find them on disk or with ``find_tools``-style discovery
    over the skills directory.
    """
    listed = [skill for skill in skills if skill.include_in_prompt]
    if not listed:
        return ""
    listed.sort(key=lambda skill: skill.name)

    lines, omitted = _select_skill_index_lines(listed, max_desc_chars, budget_tokens)
    if not lines:
        # A budget too small for even one line must not produce an empty
        # section, which would read as "no skills are available".
        lines, omitted = _select_skill_index_lines(listed, max_desc_chars, None)

    header = [
        "## Skills index",
        "",
        "One line per available skill. Load a skill's instructions with",
        '`skill(action="load", name="...")` only for skills you will actually use —',
        "do not preload them.",
        "",
    ]
    if omitted:
        header.append(
            f"({omitted} further skill(s) are installed but not listed here to save "
            'context. Find them with `skill(action="list")`.'
        )
        header.append("")
    return "\n".join([*header, *lines])
