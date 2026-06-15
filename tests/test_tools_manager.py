import zipfile
from pathlib import Path

import pytest

from vtx.tools_manager import _extract_binary, get_tool_path


def test_extract_binary_rejects_zip_path_traversal(tmp_path: Path):
    archive = tmp_path / "malicious.zip"
    dest = tmp_path / "dest"
    dest.mkdir()

    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("../../evil.txt", "pwned")

    with pytest.raises(ValueError, match="escapes target directory"):
        _extract_binary(archive, "evil.txt", dest)


def test_get_tool_path_returns_config_bin_fd(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fd_path = bin_dir / "fd"
    fd_path.write_text("", encoding="utf-8")

    monkeypatch.setattr("vtx.tools_manager._BIN_DIR", bin_dir)
    monkeypatch.setattr("vtx.tools_manager._get_platform", lambda: "linux")

    assert get_tool_path("fd") == str(fd_path)


def test_get_tool_path_supports_system_fdfind(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr("vtx.tools_manager._BIN_DIR", tmp_path / "missing")

    def fake_which(command: str) -> str | None:
        if command == "fdfind":
            return "/usr/bin/fdfind"
        return None

    monkeypatch.setattr("vtx.tools_manager.shutil.which", fake_which)

    assert get_tool_path("fd") == "/usr/bin/fdfind"
