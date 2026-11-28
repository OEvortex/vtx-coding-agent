"""vx ships its own skills: the package, the registration, and the sync."""

from importlib import resources
from pathlib import Path

import pytest
from ctx.agent.context import skills as skills_mod
from ctx.agent.context.skills import (
    get_registered_skills_packages,
    load_builtin_cmd_skills,
    load_skills,
    sync_builtin_skills,
)


def builtin_root() -> Path:
    """The skill tree inside the installed package, wherever it is."""
    with resources.as_file(resources.files("vx").joinpath("builtin_skills")) as root:
        return Path(root)


def test_vx_registers_its_skills_package_at_import():
    """The registration is the whole mechanism: no register, no skills."""
    import vx  # noqa: F401  -- importing the package is the registration

    assert "vx" in get_registered_skills_packages()


@pytest.mark.parametrize("relative", ["meta/swarm/SKILL.md"])
def test_the_skill_files_are_in_the_package(relative: str):
    assert (builtin_root() / relative).is_file()


def test_the_swarm_skill_loads_as_a_command():
    skill = next(s for s in load_builtin_cmd_skills("vx").skills if s.name == "swarm")
    assert skill.bundled
    assert skill.register_cmd
    assert skill.category == "meta"
    assert skill.cmd_info
    assert "in parallel" in skill.description
    # The name must be the directory name, or the catalog and /swarm disagree.
    assert Path(skill.path).parent.name == skill.name == "swarm"


def test_the_body_is_a_playbook_not_a_stub():
    body = (builtin_root() / "meta/swarm/SKILL.md").read_text(encoding="utf-8")
    assert body.startswith("---\nname: swarm\n")
    for heading in ("## The task", "## Start it", "## Keep it going", "## Things that will bite"):
        assert heading in body
    # The tools it names have to be the tools that exist.
    for tool in ("swarm{", "post_message{", "read_board{", "swarm_wait{"):
        assert tool in body
    assert "$ARGUMENTS" in body


def test_sync_copies_the_skill_where_the_model_can_read_it(tmp_path, monkeypatch):
    """``sync_builtin_skills`` is what makes ``~/.ctx/skills/swarm`` exist."""
    monkeypatch.setattr(skills_mod, "get_ctx_config_dir", lambda: tmp_path)
    sync_builtin_skills("vx")
    copied = tmp_path / "skills" / "swarm" / "SKILL.md"
    assert copied.is_file()
    assert "swarm coordinator" in copied.read_text(encoding="utf-8")
    # A second sync is a no-op, not a rewrite: this runs on every startup.
    before = copied.stat().st_mtime_ns
    sync_builtin_skills("vx")
    assert copied.stat().st_mtime_ns == before


def test_the_skill_is_discoverable_from_an_unrelated_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(skills_mod, "get_ctx_config_dir", lambda: tmp_path / "config")
    monkeypatch.setattr(skills_mod, "get_user_skills_dir", lambda: tmp_path / "user")
    sync_builtin_skills("vx")
    names = {skill.name for skill in load_skills(str(tmp_path)).skills}
    assert "swarm" in names


def test_the_skill_tool_can_load_it(tmp_path, monkeypatch):
    """The catalog names skills and the ``skill`` tool loads them: both must work."""
    from vx.tools.skill import find_skill

    monkeypatch.setattr(skills_mod, "get_ctx_config_dir", lambda: tmp_path / "config")
    sync_builtin_skills("vx")
    found = find_skill("swarm", str(tmp_path))
    assert found is not None
    assert found.bundled
    assert "coordinator" in Path(found.path).read_text(encoding="utf-8")


def test_a_project_skill_shadows_the_bundled_one(tmp_path, monkeypatch):
    """The rule the harness documents, checked against the skill vx ships."""
    monkeypatch.setattr(skills_mod, "get_ctx_config_dir", lambda: tmp_path / "config")
    monkeypatch.setattr(skills_mod, "get_user_skills_dir", lambda: tmp_path / "user")
    sync_builtin_skills("vx")
    local = tmp_path / ".agents" / "skills" / "swarm"
    local.mkdir(parents=True)
    (local / "SKILL.md").write_text(
        "---\nname: swarm\ndescription: the repo's own swarm rules\n---\n\nlocal\n",
        encoding="utf-8",
    )
    from vx.tools.skill import find_skill

    found = find_skill("swarm", str(tmp_path))
    assert found is not None
    assert "the repo's own swarm rules" in found.description


def test_the_skill_is_reachable_as_a_slash_command(tmp_path, monkeypatch):
    """``/swarm …`` in the real app, with the shipped skill behind it."""
    from types import SimpleNamespace
    from typing import cast

    from vx.app import VxApp

    class Stub:
        _fd = -1

        def size(self):
            return 30, 100

        def write(self, data): ...

        def enter(self): ...

        def exit(self): ...

    app = VxApp(cwd=str(tmp_path), terminal=cast("object", Stub()))  # type: ignore[arg-type]
    app._ready = __import__("asyncio").Event()
    app._ready.set()
    app._draw = lambda *args, **kwargs: None  # type: ignore[method-assign]
    shipped = load_builtin_cmd_skills("vx").skills
    app.runtime = SimpleNamespace(context=SimpleNamespace(skills=shipped))  # type: ignore[assignment]
    sent: list[str] = []
    app._enqueue_or_run = lambda prompt, **kwargs: sent.append(prompt)  # type: ignore[method-assign]

    assert app._handle_slash("/swarm audit the retry logic") is True
    assert sent, "the skill command did not start a turn"
    assert '<skill name="swarm"' in sent[0]
    assert "swarm coordinator" in sent[0]
    # The user's words land in the playbook, not at the bottom of it.
    assert "audit the retry logic" in sent[0].split("Your hands are four tools")[0]
