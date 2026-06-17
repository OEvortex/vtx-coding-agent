import shutil
from pathlib import Path

import pytest

from vtx.tools.grep import GrepParams, GrepTool


@pytest.mark.asyncio
async def test_grep_tool_python_fallback(tmp_path: Path, monkeypatch):
    # Mock ensuring rg returns None to force fallback
    async def mock_ensure_tool(*args, **kwargs):
        return None

    monkeypatch.setattr("vtx.tools.grep.ensure_tool", mock_ensure_tool)
    monkeypatch.setattr(shutil, "which", lambda cmd: None)

    # Create dummy files
    dir_path = tmp_path / "src"
    dir_path.mkdir()
    file1 = dir_path / "hello.txt"
    file1.write_text("Hello World!\nThis is Antigravity.")
    file2 = dir_path / "skip.txt"
    file2.write_text("Nothing to see here.")

    tool = GrepTool()
    params = GrepParams(pattern="Antigravity", path=str(dir_path))
    res = await tool.execute(params)

    assert res.success is True
    assert res.result is not None
    assert "Antigravity" in res.result
    assert "hello.txt" in res.result
    assert "skip.txt" not in res.result
    assert res.ui_summary is not None
    assert "(python fallback)" in res.ui_summary


@pytest.mark.asyncio
async def test_grep_tool_python_fallback_glob(tmp_path: Path, monkeypatch):
    async def mock_ensure_tool(*args, **kwargs):
        return None

    monkeypatch.setattr("vtx.tools.grep.ensure_tool", mock_ensure_tool)
    monkeypatch.setattr(shutil, "which", lambda cmd: None)

    dir_path = tmp_path / "src"
    dir_path.mkdir()
    file1 = dir_path / "match.py"
    file1.write_text("pattern here")
    file2 = dir_path / "match.txt"
    file2.write_text("pattern here")

    tool = GrepTool()
    params = GrepParams(pattern="pattern", path=str(dir_path), glob="*.py")
    res = await tool.execute(params)

    assert res.success is True
    assert res.result is not None
    assert "match.py" in res.result
    assert "match.txt" not in res.result
