"""Skill registration and frontmatter parsing.

Three defects these lock down:

- ``register_cmd`` is documented as opt-in (AGENTS.md, docs/skills.md) but
  defaulted to ``True``, so every skill became a user-facing ``/command`` and
  bundled kernel skills named ``compact`` / ``refine`` shadowed the real
  ``/compact`` and ``/refine`` commands.
- The frontmatter parser was a naive key/value scan that truncated YAML folded
  block scalars (``description: >``) to a bare ``">"``, wiping the description
  of every skill that used them.
- A skill sharing a name with a built-in command must never shadow it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vtx.ai.agent.context.skills import _parse_frontmatter, load_builtin_cmd_skills, load_skills


def _skills_root(tmp_path: Path) -> Path:
    root = tmp_path / ".agents" / "skills"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _write_markdown_skill(tmp_path: Path, name: str, frontmatter: str) -> None:
    skill_dir = _skills_root(tmp_path) / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(f"---\n{frontmatter}\n---\n# {name}\n")


def _write_python_skill(tmp_path: Path, name: str, frontmatter: str) -> None:
    skill_dir = _skills_root(tmp_path) / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(f"---\n{frontmatter}\n---\n# {name}\n")
    (skill_dir / "pyproject.toml").write_text(f'[project]\nname = "{name}"\nversion = "0.1.0"\n')
    src = skill_dir / "src" / name.replace("-", "_")
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("def run() -> None:\n    return None\n")


# ---------------------------------------------------------------------------
# register_cmd is opt-in
# ---------------------------------------------------------------------------


def test_register_cmd_defaults_off(tmp_path: Path):
    """A skill with no register_cmd must NOT appear as a slash command."""
    _write_markdown_skill(tmp_path, "plain", "name: plain\ndescription: A plain skill")
    res = load_skills(cwd=str(tmp_path))
    assert len(res.skills) == 1
    assert res.skills[0].register_cmd is False


def test_register_cmd_opts_in(tmp_path: Path):
    _write_markdown_skill(
        tmp_path, "opted", "name: opted\ndescription: Opted in\nregister_cmd: true"
    )
    res = load_skills(cwd=str(tmp_path))
    assert res.skills[0].register_cmd is True


def test_python_skill_never_registers_by_default(tmp_path: Path):
    """A kernel skill is imported by name; ``/compact`` must not reach it.

    Regression guard: the bundled ``compact`` and ``refine`` python skills
    default-registered and shadowed the real /compact and /refine commands.
    """
    _write_python_skill(tmp_path, "compact", "name: compact\ndescription: Compact from the REPL")
    res = load_skills(cwd=str(tmp_path))
    assert len(res.skills) == 1
    assert res.skills[0].kind == "python"
    assert res.skills[0].register_cmd is False


def test_python_skill_can_still_opt_in(tmp_path: Path):
    _write_python_skill(
        tmp_path, "compact", "name: compact\ndescription: Compact\nregister_cmd: true"
    )
    res = load_skills(cwd=str(tmp_path))
    assert res.skills[0].register_cmd is True


def test_bundled_kernel_skills_do_not_shadow_commands():
    """The real bug: no bundled skill may claim a built-in command name."""
    builtins = {
        "agent",
        "clear",
        "compact",
        "copy",
        "export",
        "handoff",
        "help",
        "login",
        "logout",
        "model",
        "new",
        "notifications",
        "permissions",
        "ponytail",
        "provider",
        "recap",
        "refine",
        "resume",
        "session",
        "settings",
        "switch",
        "themes",
        "thinking",
        "tree",
        "update",
        "quit",
        "exit",
        "q",
    }
    skills = load_builtin_cmd_skills().skills
    collisions = sorted(s.name for s in skills if s.register_cmd and s.name in builtins)
    assert collisions == [], f"skills shadow built-in commands: {collisions}"


def test_bundled_kernel_skills_are_not_slash_commands():
    """The five bundled kernel skills are imported, never typed as /name."""
    skills = {s.name: s for s in load_builtin_cmd_skills().skills}
    for name in ("agent-message", "agent-observe", "compact", "edit", "refine"):
        assert skills[name].kind == "python", name
        assert skills[name].register_cmd is False, name


# ---------------------------------------------------------------------------
# frontmatter: real YAML
# ---------------------------------------------------------------------------


def test_folded_block_description_parses():
    """``description: >`` must fold, not collapse to a bare '>'."""
    content = (
        "---\n"
        "name: folded\n"
        "description: >\n"
        "  First line of the description\n"
        "  and the second line folded in.\n"
        "---\n"
        "# Folded\n"
    )
    parsed = _parse_frontmatter(content)
    assert parsed["name"] == "folded"
    description = parsed["description"]
    assert description != ">"
    assert "First line" in description
    assert "second line" in description


def test_literal_block_description_parses():
    content = "---\nname: literal\ndescription: |\n  line one\n  line two\n---\n"
    parsed = _parse_frontmatter(content)
    assert "line one" in parsed["description"]
    assert "line two" in parsed["description"]


def test_no_skill_keeps_a_bare_gt_description():
    """Regression guard: 15 bundled skills had their description reduced to '>'."""
    skills = load_builtin_cmd_skills().skills
    broken = [s.name for s in skills if s.description.strip() in ("", ">")]
    assert broken == [], f"skills with unusable descriptions: {broken}"


def test_description_stays_a_string_for_block_scalars(tmp_path: Path):
    """A non-str frontmatter value must not crash skill loading."""
    _write_markdown_skill(
        tmp_path, "listy", "name: listy\ndescription: >\n  A folded description\n  spanning lines"
    )
    res = load_skills(cwd=str(tmp_path))
    assert len(res.skills) == 1
    assert isinstance(res.skills[0].description, str)
    assert "folded description" in res.skills[0].description


@pytest.mark.parametrize("body", ["", "no frontmatter here", "---\nname: x\n"])
def test_malformed_frontmatter_is_tolerated(body: str):
    assert isinstance(_parse_frontmatter(body), dict)
