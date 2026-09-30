"""Tests for the budgeted RLM skills index.

The full catalog is a fixed ~6k-token tax on every RLM turn, so the index is
capped. The risk a naive cap introduces is that a large skill category crowds
every other category out of the prompt, leaving the model unable to tell those
skills exist. These tests pin the round-robin behaviour that prevents it.
"""

from __future__ import annotations

import re

from vtx.ai.agent.context.skills import (
    CHARS_PER_TOKEN,
    DEFAULT_SKILLS_INDEX_BUDGET_TOKENS,
    Skill,
    formatted_skills_index,
)


def _skills(count: int, category=lambda i: "general") -> list[Skill]:
    return [
        Skill(
            path=f"/skills/skill_{i:02d}",
            name=f"skill_{i:02d}",
            description="A skill that does a thing. " * 4,
            category=category(i),
        )
        for i in range(count)
    ]


def _listed(text: str) -> list[str]:
    return re.findall(r"^- (skill_\d+)", text, re.M)


def test_no_budget_lists_everything():
    skills = _skills(40, lambda i: f"cat{i % 5}")
    text = formatted_skills_index(skills, budget_tokens=None)
    assert len(_listed(text)) == 40
    assert "further skill" not in text


def test_a_generous_budget_lists_everything():
    skills = _skills(40, lambda i: f"cat{i % 5}")
    text = formatted_skills_index(skills, budget_tokens=DEFAULT_SKILLS_INDEX_BUDGET_TOKENS)
    assert len(_listed(text)) == 40
    assert "further skill" not in text


def test_a_tight_budget_truncates_and_says_so():
    skills = _skills(40)
    text = formatted_skills_index(skills, budget_tokens=200)
    assert 0 < len(_listed(text)) < 40
    assert "further skill" in text


def test_every_category_is_represented_before_any_completes():
    """The whole point of round-robin: no category may vanish from the prompt.

    A cheapest-first pass would fill the budget with one category and drop the
    rest, which reads to the model as "these skills do not exist".
    """
    # 5 categories, 20 skills each. Sized so the budget covers several lines per
    # category: the assertion is that no category is missing, not that the
    # section is small.
    skills = _skills(100, lambda i: f"cat{i % 5}")
    text = formatted_skills_index(skills, budget_tokens=400)
    listed = _listed(text)
    assert listed, "the budget must still list something"
    assert len(listed) < 100, "the budget is not actually binding"

    by_category = {name: skills[int(name.split("_")[1])].category for name in listed}
    assert len(set(by_category.values())) == 5, f"a category was dropped: {by_category}"


def test_an_oversized_category_cannot_starve_the_others():
    """One huge category must not consume the whole budget."""
    skills = _skills(1, lambda i: "tiny") + _skills(200, lambda i: "huge")
    text = formatted_skills_index(skills, budget_tokens=150)
    listed = _listed(text)
    categories = {skills[int(name.split("_")[1])].category for name in listed}
    assert "tiny" in categories, "the small category was crowded out"
    assert "huge" in categories


def test_the_budget_is_actually_respected():
    """Header included, the section must stay near its token ceiling."""
    skills = _skills(60, lambda i: f"cat{i % 4}")
    budget = 300
    text = formatted_skills_index(skills, budget_tokens=budget)
    estimated = len(text) / CHARS_PER_TOKEN
    # Some slack for the header and the omission note, which are not charged
    # against the budget; a large overshoot would mean the cap is not binding.
    assert estimated < budget * 1.5, f"{estimated:.0f} tokens for a {budget} budget"


def test_a_budget_too_small_for_any_line_still_lists_everything():
    """An empty section would read as "no skills are available".

    Better to overshoot a 1-token budget than to tell the model the install has
    no skills at all.
    """
    skills = _skills(5)
    text = formatted_skills_index(skills, budget_tokens=1)
    assert len(_listed(text)) == 5
    assert text.strip()


def test_a_zero_budget_still_lists_everything():
    text = formatted_skills_index(_skills(3), budget_tokens=0)
    assert len(_listed(text)) == 3


def test_the_omission_note_names_the_count():
    skills = _skills(40)
    text = formatted_skills_index(skills, budget_tokens=200)
    listed = len(_listed(text))
    match = re.search(r"\((\d+) further skill", text)
    assert match, text
    assert int(match.group(1)) == 40 - listed


def test_skills_excluded_from_the_prompt_are_never_listed():
    skills = _skills(3)
    skills[1].include_in_prompt = False
    text = formatted_skills_index(skills)
    assert _listed(text) == ["skill_00", "skill_02"]


def test_no_skills_yields_no_section():
    assert formatted_skills_index([]) == ""


def test_python_skills_show_their_import_name():
    from vtx.ai.agent.context.skills import SkillPythonMetadata

    skills = _skills(1)
    skills[0].kind = "python"
    skills[0].python = SkillPythonMetadata(
        import_name="my_skill", package_path="/p", pyproject_path="/p/pyproject.toml"
    )
    assert "python `my_skill`" in formatted_skills_index(skills)


def test_long_descriptions_are_truncated():
    skills = _skills(1)
    skills[0].description = "y" * 500
    text = formatted_skills_index(skills, max_desc_chars=40)
    line = next(row for row in text.splitlines() if row.startswith("- skill_"))
    assert "..." in line
    assert len(line) < 100
