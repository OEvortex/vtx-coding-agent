"""Tests for the SDK skills integration."""

from __future__ import annotations

from vtx.sdk.skills import format_skills_for_prompt, load_vtx_skills


def test_load_vtx_skills_returns_list() -> None:
    skills = load_vtx_skills()
    assert isinstance(skills, list)


def test_format_skills_for_prompt_empty() -> None:
    assert format_skills_for_prompt([]) == ""


def test_format_skills_for_prompt_basic() -> None:
    from vtx.context.skills import Skill

    skills = [
        Skill(path="/tmp/a", name="alpha", description="The alpha skill."),
        Skill(path="/tmp/b", name="beta", description="The beta skill."),
    ]
    output = format_skills_for_prompt(skills)
    assert "Available skills" in output
    assert "alpha" in output
    assert "beta" in output
    assert "The alpha skill." in output
