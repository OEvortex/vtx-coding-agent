"""Tests for the vtx CLI (plugin subcommand, --<name> flags)."""

from __future__ import annotations

import sys

import pytest

from vtx.coding_agent.cli import build_parser


def test_the_deleted_extension_subcommands_are_gone():
    """``install``/``uninstall``/``list-extensions`` now belong to ``vtx plugin``."""
    for command in ("install", "uninstall", "list-extensions"):
        with pytest.raises(SystemExit):
            build_parser().parse_args([command])


def test_list_extensions_flag_parsing():
    """``vtx --list-extensions`` parses correctly."""
    args = build_parser().parse_args(["--list-extensions"])
    assert args.list_extensions is True


def test_extension_flags_are_repeatable():
    args = build_parser().parse_args(["-e", "a.py", "--extension", "b.py"])
    assert args.extension_paths == ["a.py", "b.py"]
    assert build_parser().parse_args(["--no-extensions"]).no_extensions is True


def test_prompt_flag_takes_value():
    assert build_parser().parse_args(["-p", "hi"]).prompt == "hi"


def test_bare_prompt_flag_means_stdin():
    assert build_parser().parse_args(["-p"]).prompt == "-"


def test_provider_uses_long_form():
    assert build_parser().parse_args(["--provider", "openai"]).provider == "openai"


def test_prompt_no_longer_feeds_provider():
    assert build_parser().parse_args(["-p", "x"]).provider is None


def test_option_after_bare_prompt_falls_back_to_stdin():
    args = build_parser().parse_args(["-p", "-m", "x"])
    assert args.prompt == "-"
    assert args.model == "x"


@pytest.mark.parametrize("flag", [["-c"], ["-r", "abc123"]])
def test_resume_flags_rejected_with_prompt(monkeypatch, flag):
    monkeypatch.setattr(sys, "argv", ["vtx", "-p", "x", *flag])
    with pytest.raises(SystemExit) as exc:
        from vtx.coding_agent.cli import main

        main()
    assert exc.value.code == 2


EXTENSION_SOURCE = """\
def register(api):
    api.register_tool(
        "greet", "Say hi.", {"type": "object", "properties": {}},
        execute=lambda args, ctx: {"success": True, "result": "hi"},
    )
"""


def _boot_cli(monkeypatch, tmp_path, argv):
    """Run ``cli.main()`` far enough to mount the composition, then return its host."""
    import vtx.tui.launch as launch
    from vtx.coding_agent import cli
    from vtx.plugins import active_host

    monkeypatch.setattr("vtx.core.paths.get_config_dir", lambda: tmp_path / "home")
    monkeypatch.setattr(launch, "run_tui", lambda args: None)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", argv)
    cli.main()
    return active_host()


def test_extension_flag_reaches_the_composition(monkeypatch, tmp_path):
    """``-e PATH`` must reach discovery, which now runs inside ``boot``."""
    extension = tmp_path / "greet.py"
    extension.write_text(EXTENSION_SOURCE, encoding="utf-8")

    host = _boot_cli(monkeypatch, tmp_path, ["vtx", "-e", str(extension)])
    try:
        assert host.base.tools.get("greet") is not None
    finally:
        host.dispose()


def test_no_extensions_reaches_the_composition(monkeypatch, tmp_path):
    """``--no-extensions`` must keep auto-discovered files out of the composition."""
    directory = tmp_path / ".vtx" / "extensions"
    directory.mkdir(parents=True)
    (directory / "greet.py").write_text(EXTENSION_SOURCE, encoding="utf-8")

    host = _boot_cli(monkeypatch, tmp_path, ["vtx", "--no-extensions"])
    try:
        assert host.base.tools.get("greet") is None
    finally:
        host.dispose()

    host = _boot_cli(monkeypatch, tmp_path, ["vtx"])
    try:
        assert host.base.tools.get("greet") is not None
    finally:
        host.dispose()


def test_list_extensions_reports_the_running_composition(monkeypatch, tmp_path, capsys):
    extension = tmp_path / "greet.py"
    extension.write_text(EXTENSION_SOURCE, encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        _boot_cli(monkeypatch, tmp_path, ["vtx", "--list-extensions", "-e", str(extension)])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "greet" in out
    assert "tools: greet" in out
