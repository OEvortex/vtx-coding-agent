from pathlib import Path

import pytest

from vtx.ai.agent.context.skills import formatted_skills, load_skills
from vtx.ai.agent.ipython_manager import IpythonKernel
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
    assert "<type>python</type>" in formatted
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


@pytest.mark.asyncio
async def test_runtime_python_skills_execution(tmp_path: Path):
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

    kernel = IpythonKernel("test_kernel", cwd=str(tmp_path))
    try:
        await kernel.start()
        stdout, errored = await kernel.execute(
            'ans = await calculator(10, 20)\nprint("RESULT:", ans)'
        )
        assert not errored, f"Execution failed: {stdout}"
        assert "RESULT: 30" in stdout
    finally:
        await kernel.close()
