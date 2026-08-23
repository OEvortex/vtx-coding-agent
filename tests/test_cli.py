"""Tests for the vtx CLI (install, uninstall, list-extensions, --<name> flags)."""

from __future__ import annotations

import sys

import pytest

from coding_agent.cli import build_parser


def test_install_subcommand_parsing():
    """``vtx install foo`` parses correctly."""
    args = build_parser().parse_args(["install", "foo"])
    assert args.command == "install"
    assert args.name == "foo"
    assert args.upgrade is False


def test_install_with_upgrade_flag():
    """``vtx install foo --upgrade`` parses correctly."""
    args = build_parser().parse_args(["install", "foo", "--upgrade"])
    assert args.command == "install"
    assert args.name == "foo"
    assert args.upgrade is True


def test_uninstall_subcommand_parsing():
    """``vtx uninstall foo`` parses correctly."""
    args = build_parser().parse_args(["uninstall", "foo"])
    assert args.command == "uninstall"
    assert args.name == "foo"


def test_list_extensions_subcommand_parsing():
    """``vtx list-extensions`` parses correctly."""
    args = build_parser().parse_args(["list-extensions"])
    assert args.command == "list-extensions"


def test_list_extensions_flag_parsing():
    """``vtx --list-extensions`` parses correctly."""
    args = build_parser().parse_args(["--list-extensions"])
    assert args.list_extensions is True


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
        from coding_agent.cli import main

        main()
    assert exc.value.code == 2
