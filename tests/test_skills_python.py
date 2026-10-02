from pathlib import Path

from vtx.agent.context.skills import formatted_skills, load_skills
from vtx.skill import CallableModule, wrap_skill_module


def test_skill_detection_and_formatting(tmp_path: Path):
    skill_dir = tmp_path / ".agents" / "skills" / "word-count"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: word-count\ndescription: Count words\n---\n# Word Count\n"
    )
    (skill_dir / "pyproject.toml").write_text(
        '[project]\nname = "word-count"\nversion = "0.1.0"\n'
    )
    src_dir = skill_dir / "src" / "word_count"
    src_dir.mkdir(parents=True)
    (src_dir / "__init__.py").write_text(
        'def run(text: str) -> int:\n    """Count words."""\n    return len(text.split())\n'
    )

    res = load_skills(cwd=str(tmp_path))
    assert len(res.skills) == 1
    skill = res.skills[0]
    assert skill.name == "word-count"
    assert skill.kind == "python"
    assert skill.python is not None
    assert skill.python.import_name == "word_count"

    formatted = formatted_skills(res.skills)
    # <type> was dropped: the catalog routes by name and description, and the
    # python import is the only type-specific fact the model acts on.
    assert "<python_import>word_count</python_import>" in formatted


def test_callable_module_wrapper():
    import types

    mod = types.ModuleType("sample_skill")

    def run_fn(x: int) -> int:
        """Double number."""
        return x * 2

    mod.run = run_fn
    mod.__doc__ = "Sample skill module doc"

    wrapped = wrap_skill_module(mod)
    assert isinstance(wrapped, CallableModule)
    assert wrapped(5) == 10
    assert wrapped.run(5) == 10
    assert wrapped.__name__ == "sample_skill"
    assert wrapped.__doc__ == run_fn.__doc__


def test_python_skill_module_is_still_wrappable(tmp_path: Path):
    """A python skill stays loadable, but there is nothing to run it in.

    The persistent kernel went with the RLM mode, so a python skill is
    discovered and importable but not executable by the agent -- which is why
    ``skills_for_mode`` filters them out of the prompt catalog. This test
    covers the surviving half so the removal does not silently break
    discovery.
    """
    skill_dir = tmp_path / ".agents" / "skills" / "calculator"
    src_dir = skill_dir / "src" / "calculator"
    src_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: calculator\ndescription: Calc\n---\n# Calc\n")
    (skill_dir / "pyproject.toml").write_text(
        '[project]\nname = "calculator"\nversion = "0.1.0"\n'
    )
    (src_dir / "__init__.py").write_text(
        'async def run(a: int, b: int) -> int:\n    """Add two numbers."""\n    return a + b\n'
    )

    res = load_skills(cwd=str(tmp_path))
    assert len(res.skills) == 1
    assert res.skills[0].kind == "python"
    # And it is hidden from the prompt, because offering a skill the agent
    # cannot run spends context on a dead end.
    assert formatted_skills(res.skills) != ""  # formatting still works
    from vtx.agent.context.skills import skills_for_mode

    assert skills_for_mode(res.skills) == []
