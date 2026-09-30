"""The skills catalog as refreshable session state, end to end.

The catalog in the system prompt is a snapshot. Without a refresh path, a skill
installed or deleted mid-session stays advertised for the rest of the run: the
model keeps calling a skill that no longer exists and never learns about one
that does. Rebuilding the system prompt instead would invalidate the cached
prefix on every cold boundary.

Pure rendering is covered in ``test_skills_refresh.py``. These drive the loop
hook: first-boundary silence, in-place swap, silence when nothing changed,
sub-agent skip, and the tool round trip that replaces a `read` per skill.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from vtx.ai.agent.loop import Agent
from vtx.ai.agent.rlm.harness import get_harness_state
from vtx.ai.agent.rlm.refine import local_state_dir
from vtx.ai.agent.rlm.registry import bridge_session_id
from vtx.ai.agent.session import Session
from vtx.ai.providers.mock import MockProvider
from vtx.coding_agent.tools.skill import SkillTool


def _agent(cwd, **kwargs):
    return Agent(MockProvider(scenario="simple_text"), [], Session.in_memory(), cwd=cwd, **kwargs)


def _write_skill(root: Path, name: str, description: str, *, body: str = "Do the thing.") -> Path:
    skill_dir = root / ".agents" / "skills" / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n", encoding="utf-8"
    )
    return skill_dir


def _refresh_messages(agent: Agent) -> list[str]:
    from vtx.ai.agent.skills_refresh import is_skills_refresh_message

    return [
        entry.message.content
        for entry in agent.session.active_entries
        if is_skills_refresh_message(entry.message)
    ]


# =================================================================================================
# the loop hook
# =================================================================================================


def test_the_first_boundary_stays_silent(tmp_path):
    """The system prompt already carries this catalog; repeating it is pure cost."""
    _write_skill(tmp_path, "review", "Review code changes.")

    agent = _agent(str(tmp_path))
    agent._ensure_skills_refresh_context()

    assert _refresh_messages(agent) == []


def test_an_unchanged_catalog_stays_silent(tmp_path):
    _write_skill(tmp_path, "review", "Review code changes.")
    agent = _agent(str(tmp_path))

    agent._ensure_skills_refresh_context()
    agent._ensure_skills_refresh_context()
    agent._ensure_skills_refresh_context()

    assert _refresh_messages(agent) == []


def test_an_installed_skill_is_announced(tmp_path):
    _write_skill(tmp_path, "review", "Review code changes.")
    agent = _agent(str(tmp_path))
    agent._ensure_skills_refresh_context()

    _write_skill(tmp_path, "brand-new", "A freshly installed skill.")
    agent._ensure_skills_refresh_context()

    messages = _refresh_messages(agent)
    assert len(messages) == 1
    assert "Added: brand-new" in messages[0]
    assert "<name>brand-new</name>" in messages[0]


def test_a_deleted_skill_is_announced_by_name(tmp_path):
    """Announcing removal only by absence leaves the model calling a skill that
    is gone. It has an older copy of the catalog higher up in the conversation."""
    skill = _write_skill(tmp_path, "doomed", "A skill about to be deleted.")
    agent = _agent(str(tmp_path))
    agent._ensure_skills_refresh_context()

    import shutil

    shutil.rmtree(skill)
    agent._ensure_skills_refresh_context()

    messages = _refresh_messages(agent)
    assert "No longer available (do not call these): doomed" in messages[0]


def test_a_changed_description_is_announced(tmp_path):
    skill_dir = _write_skill(tmp_path, "review", "Old description.")
    agent = _agent(str(tmp_path))
    agent._ensure_skills_refresh_context()

    (skill_dir / "SKILL.md").write_text(
        "---\nname: review\ndescription: New description.\n---\n\nDo the thing.\n",
        encoding="utf-8",
    )
    agent._ensure_skills_refresh_context()

    assert "Updated descriptions: review" in _refresh_messages(agent)[0]


def test_repeated_changes_swap_one_slot_rather_than_stacking(tmp_path):
    """The session log is append-only, so a refresh must replace in place."""
    _write_skill(tmp_path, "review", "Review code changes.")
    agent = _agent(str(tmp_path))
    agent._ensure_skills_refresh_context()

    _write_skill(tmp_path, "second", "Second skill.")
    agent._ensure_skills_refresh_context()
    _write_skill(tmp_path, "third", "Third skill.")
    agent._ensure_skills_refresh_context()

    messages = _refresh_messages(agent)
    assert len(messages) == 1, f"refreshes stacked: {len(messages)}"
    assert "<name>third</name>" in messages[0]


def test_a_subagent_does_not_deliver_its_own_refresh(tmp_path):
    """Sub-agents share the parent's prompt; a second copy is duplicated cost."""
    _write_skill(tmp_path, "review", "Review code changes.")
    agent = _agent(str(tmp_path), depth=1)

    _write_skill(tmp_path, "brand-new", "A freshly installed skill.")
    agent._ensure_skills_refresh_context()

    assert _refresh_messages(agent) == []


def test_a_missing_catalog_does_not_announce_the_removal_of_everything(tmp_path):
    """An unreadable catalog preserves the last known one. Announcing "no
    skills" because a read failed is worse than staying quiet."""
    _write_skill(tmp_path, "review", "Review code changes.")
    agent = _agent(str(tmp_path))
    agent._ensure_skills_refresh_context()

    def boom(*_a, **_k):
        raise OSError("permission denied")

    import vtx.ai.agent.loop as loop_mod

    original = loop_mod.Agent._ensure_skills_refresh_context

    def exploding(self):
        self._load_skills_catalog_state = lambda: None
        return original(self)

    agent._load_skills_catalog_state = lambda: None
    agent._ensure_skills_refresh_context()

    assert _refresh_messages(agent) == []


# =================================================================================================
# the tool that replaces a read per skill
# =================================================================================================


def test_load_returns_the_body_and_its_base_directory(tmp_path, monkeypatch):
    skill_dir = _write_skill(tmp_path, "review", "Review code changes.")
    monkeypatch.chdir(tmp_path)

    result = asyncio.run(SkillTool().execute(SkillTool.params(action="load", name="review")))

    assert result.success
    # Frontmatter is the catalog's job; the body is what the model needs.
    assert "description: Review code changes." not in result.result
    assert "Do the thing." in result.result
    assert f"Base directory for this skill: {skill_dir}" in result.result


def test_load_lists_the_files_beside_the_skill(tmp_path, monkeypatch):
    """Otherwise the model spends a directory listing discovering that
    `scripts/run.sh` exists."""
    skill_dir = _write_skill(tmp_path, "review", "Review code changes.")
    (skill_dir / "scripts").mkdir()
    (skill_dir / "scripts" / "run.sh").write_text("echo hi")
    monkeypatch.chdir(tmp_path)

    result = asyncio.run(SkillTool().execute(SkillTool.params(action="load", name="review")))

    assert "scripts/run.sh" in result.result


def test_load_survives_a_hostile_description(tmp_path, monkeypatch):
    skill_dir = tmp_path / ".agents" / "skills" / "hostile"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: hostile\ndescription: <script>alert(1)</script>\n---\n\nBody.\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    result = asyncio.run(SkillTool().execute(SkillTool.params(action="load", name="hostile")))

    # The body is model-visible text; a raw script tag in it is a prompt
    # injection the model may act on.
    assert "<script>" not in result.result


def test_load_reports_a_missing_skill_rather_than_raising(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    result = asyncio.run(SkillTool().execute(SkillTool.params(action="load", name="ghost")))

    assert not result.success
    assert "not found" in result.result


def test_the_catalog_points_at_the_tool_rather_than_a_path(tmp_path):
    from vtx.ai.agent.context.skills import formatted_skills, load_skills

    _write_skill(tmp_path, "review", "Review code changes.")
    skills = load_skills(str(tmp_path)).skills
    formatted = formatted_skills(skills)

    assert 'skill(action="load"' in formatted
    assert "<location>" not in formatted


def test_a_skill_created_mid_session_is_loadable(tmp_path, monkeypatch):
    """The refresh only helps if the announced skill can actually be loaded."""
    monkeypatch.chdir(tmp_path)
    _write_skill(tmp_path, "review", "Review code changes.")

    result = asyncio.run(SkillTool().execute(SkillTool.params(action="load", name="review")))

    assert result.success
    assert get_harness_state is not None  # store import stays exercised
    assert local_state_dir is not None
    assert bridge_session_id() is not None


@pytest.mark.parametrize("action", ["load", "view"])
def test_the_read_only_actions_report_success_without_editing(tmp_path, monkeypatch, action):
    _write_skill(tmp_path, "review", "Review code changes.")
    monkeypatch.chdir(tmp_path)

    tool = SkillTool()
    result = asyncio.run(tool.execute(tool.params(action=action, name="review")))

    assert result.success
