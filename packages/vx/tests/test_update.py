"""Update logic: install detection and self_update, subprocess mocked."""

import subprocess

import pytest
from vx import update


@pytest.fixture
def no_tools(monkeypatch):
    """Pretend no installer or source checkout exists."""
    monkeypatch.setattr(update, "_find_executable", lambda name: False)
    monkeypatch.setattr(update, "_source_checkout", lambda: None)
    monkeypatch.delenv(update.FORCE_PIP_ENV, raising=False)


class TestInstallChoice:
    def test_force_pip_env(self, monkeypatch, no_tools):
        monkeypatch.setenv(update.FORCE_PIP_ENV, "1")
        installer, cmd = update.install_choice()
        assert installer == "pip"
        assert cmd is not None
        assert "pip" in cmd and "--upgrade" in cmd and "vx" in cmd

    def test_uv_tool_detected(self, monkeypatch, no_tools):
        monkeypatch.setattr(update, "_find_executable", lambda name: name == "uv")
        monkeypatch.setattr(update, "_is_uv_tool", lambda pkg: True)
        installer, cmd = update.install_choice()
        assert installer == "uv tool"
        assert cmd is not None and cmd[:3] == ["uv", "tool", "upgrade"]

    def test_pipx_detected(self, monkeypatch, no_tools):
        monkeypatch.setattr(update, "_find_executable", lambda name: name == "pipx")
        monkeypatch.setattr(update, "_is_pipx_tool", lambda pkg: True)
        installer, cmd = update.install_choice()
        assert installer == "pipx"
        assert cmd is not None and cmd[:2] == ["pipx", "upgrade"]

    def test_source_checkout_wins(self, monkeypatch, no_tools, tmp_path):
        root = tmp_path / "checkout"
        (root / "packages" / "vx").mkdir(parents=True)
        (root / ".git").mkdir()
        monkeypatch.setattr(update, "_source_checkout", lambda: root)
        installer, cmd = update.install_choice()
        assert installer == "source checkout"
        assert cmd is None

    def test_uv_fallback(self, monkeypatch, no_tools):
        monkeypatch.setattr(update, "_find_executable", lambda name: name == "uv")
        installer, cmd = update.install_choice()
        assert installer == "uv"
        assert cmd is not None and cmd[:3] == ["uv", "pip", "install"]

    def test_pip_fallback(self, no_tools):
        installer, cmd = update.install_choice()
        assert installer == "pip"
        assert cmd is not None and "pip" in cmd


class TestSelfUpdate:
    def test_success_message(self, monkeypatch):
        monkeypatch.setattr(
            update, "install_choice", lambda pkg="vx": ("uv tool", ["uv", "tool", "upgrade", "vx"])
        )
        monkeypatch.setattr(
            update,
            "_run",
            lambda cmd: subprocess.CompletedProcess(cmd, 0, stdout="done", stderr=""),
        )
        ok, msg = update.self_update()
        assert ok
        assert "uv tool" in msg

    def test_already_up_to_date(self, monkeypatch):
        monkeypatch.setattr(update, "install_choice", lambda pkg="vx": ("pip", ["pip", "install"]))
        monkeypatch.setattr(
            update,
            "_run",
            lambda cmd: subprocess.CompletedProcess(
                cmd, 0, stdout="Requirement already satisfied", stderr=""
            ),
        )
        ok, msg = update.self_update()
        assert ok and "up to date" in msg

    def test_failure_message(self, monkeypatch):
        monkeypatch.setattr(update, "install_choice", lambda pkg="vx": ("pip", ["pip", "install"]))
        monkeypatch.setattr(
            update,
            "_run",
            lambda cmd: subprocess.CompletedProcess(cmd, 1, stdout="", stderr="nope"),
        )
        ok, msg = update.self_update()
        assert not ok and "nope" in msg

    def test_git_pull_path(self, monkeypatch, tmp_path):
        root = tmp_path / "checkout"
        monkeypatch.setattr(update, "_source_checkout", lambda: root)
        monkeypatch.setattr(update, "_find_executable", lambda name: False)
        monkeypatch.delenv(update.FORCE_PIP_ENV, raising=False)
        monkeypatch.setattr(
            update,
            "_run",
            lambda cmd: subprocess.CompletedProcess(
                cmd, 0, stdout="Already up to date.", stderr=""
            ),
        )
        ok, msg = update.self_update()
        assert ok and "up to date" in msg
