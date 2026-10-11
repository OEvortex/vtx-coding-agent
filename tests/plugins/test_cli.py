"""Tests for the ``vtx plugin`` command surface and the CLI flags."""

from __future__ import annotations

import pytest

from vtx.coding_agent.plugin_cmd import run_plugin_command


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setattr("vtx.core.paths.get_config_dir", lambda: tmp_path)


def test_profiles_lists_the_shipped_templates(capsys):
    assert run_plugin_command(["profiles"]) == 0
    out = capsys.readouterr().out
    assert "default" in out
    assert "base" in out
    assert "minimal" in out


def test_init_creates_a_profile_directory(capsys):
    assert run_plugin_command(["init", "mine"]) == 0
    out = capsys.readouterr().out
    assert "mine" in out
    assert run_plugin_command(["path", "mine"]) == 0
    assert "profiles/mine" in capsys.readouterr().out


def test_disable_then_enable_round_trips_a_row(capsys):
    assert run_plugin_command(["--profile", "p", "disable", "tool-grep"]) == 0
    assert "disabled" in capsys.readouterr().out
    from vtx.plugins.profile import load_profile

    rows = {row.id: row for row in load_profile("p")}
    assert rows["tool-grep"].disabled is True

    assert run_plugin_command(["--profile", "p", "enable", "tool-grep"]) == 0
    rows = {row.id: row for row in load_profile("p")}
    assert rows["tool-grep"].disabled is False


def test_profile_flag_may_come_after_the_command(capsys):
    assert run_plugin_command(["disable", "tool-bash", "--profile", "after"]) == 0
    assert "after" in capsys.readouterr().out


def test_disable_rejects_an_unknown_row(capsys):
    assert run_plugin_command(["--profile", "p", "disable", "tool-nope"]) == 1
    assert "unknown row" in capsys.readouterr().err


def test_disable_keeps_exactly_one_home_for_the_row(tmp_path):
    run_plugin_command(["--profile", "p", "disable", "tool-grep"])
    run_plugin_command(["--profile", "p", "enable", "tool-grep"])
    run_plugin_command(["--profile", "p", "disable", "tool-grep"])
    text = (tmp_path / "profiles" / "p" / "cordis.yml").read_text()
    assert text.count("id: tool-grep") == 1


def test_disable_the_same_row_twice_is_idempotent(capsys):
    assert run_plugin_command(["--profile", "p", "disable", "tool-grep"]) == 0
    assert run_plugin_command(["--profile", "p", "disable", "tool-grep"]) == 0
    from vtx.plugins.profile import load_profile

    rows = [row for row in load_profile("p") if row.id == "tool-grep"]
    assert len(rows) == 1
    assert rows[0].disabled is True


def test_unknown_plugin_command_fails(capsys):
    assert run_plugin_command(["nonsense"]) == 1
    assert "unknown plugin command" in capsys.readouterr().err


def test_help_is_not_an_error(capsys):
    assert run_plugin_command(["--help"]) == 0


def test_a_refusal_prints_the_command_that_grants_the_exemption(tmp_path, monkeypatch, capsys):
    """The install gate must tell the operator exactly what to run, not just no."""
    import vtx.plugins.install as install_module
    from vtx.coding_agent.plugin_cmd import _install

    monkeypatch.setattr(install_module, "VERSION", "1.2.2")
    package = tmp_path / "demo-plugin"
    package.mkdir()
    (package / "pyproject.toml").write_text(
        "[project]\nname = 'demo-plugin'\nversion = '1.0.0'\n"
        "[project.optional-dependencies]\nvtx = ['>=99.0']\n",
        encoding="utf-8",
    )
    assert _install("default", [str(package)]) == 1
    err = capsys.readouterr().err
    assert ">=99.0" in err
    assert "allow-version demo-plugin@1.0.0" in err
    assert "--accept-risk" in err


def test_granting_an_exemption_then_installing_succeeds(tmp_path, monkeypatch, capsys):
    import vtx.plugins.install as install_module
    from vtx.coding_agent.plugin_cmd import _install

    monkeypatch.setattr(install_module, "VERSION", "1.2.2")
    package = tmp_path / "demo-plugin"
    package.mkdir()
    (package / "pyproject.toml").write_text(
        "[project]\nname = 'demo-plugin'\nversion = '1.0.0'\n"
        "[project.optional-dependencies]\nvtx = ['>=99.0']\n",
        encoding="utf-8",
    )
    assert _install("default", [str(package)]) == 1
    assert (
        run_plugin_command(
            [
                "--profile",
                "default",
                "allow-version",
                "demo-plugin@1.0.0",
                "--vtx-version",
                "1.2.2",
                "--accept-risk",
            ]
        )
        == 0
    )
    capsys.readouterr()
    # Only the gate is asserted here: the real `uv pip install` would run next.
    from vtx.plugins.install import is_exempt, plan_install

    assert is_exempt(plan_install(str(package))) is True


def test_version_exemptions_lists_grants_as_json(tmp_path, capsys):
    import json

    run_plugin_command(
        [
            "--profile",
            "default",
            "allow-version",
            "demo@1.0.0",
            "--vtx-version",
            "1.2.2",
            "--accept-risk",
        ]
    )
    capsys.readouterr()  # drain the confirmation line
    assert run_plugin_command(["--profile", "default", "version-exemptions"]) == 0
    grants = json.loads(capsys.readouterr().out)
    assert grants == {"demo@1.0.0": ["1.2.2"]}


def test_allow_version_without_a_target_fails(capsys):
    assert run_plugin_command(["--profile", "default", "allow-version"]) == 1
    assert "usage" in capsys.readouterr().err


def test_allow_version_rejects_a_package_without_a_version(capsys):
    assert (
        run_plugin_command(
            ["--profile", "default", "allow-version", "demo-plugin", "--vtx-version", "1.2.2"]
        )
        == 1
    )
    assert "package@version" in capsys.readouterr().err


def test_revoking_without_accept_risk_is_allowed(capsys):
    import json

    run_plugin_command(
        [
            "--profile",
            "default",
            "allow-version",
            "demo@1.0.0",
            "--vtx-version",
            "1.2.2",
            "--accept-risk",
        ]
    )
    capsys.readouterr()
    run_plugin_command(
        ["--profile", "default", "revoke-version", "demo@1.0.0", "--vtx-version", "1.2.2"]
    )
    capsys.readouterr()
    assert run_plugin_command(["--profile", "default", "version-exemptions"]) == 0
    assert json.loads(capsys.readouterr().out) == {}


def test_dump_config_flag_prints_the_composed_rows(capsys):
    import sys

    from vtx.coding_agent.cli import main

    sys.argv = ["vtx", "--dump-config"]
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "tool-bash" in out
    assert "[enabled]" in out
    assert "vtx.plugins.builtins" in out


def test_the_cli_boots_the_active_composition(tmp_path, monkeypatch):
    import sys

    import vtx.tui.launch as launch
    from vtx.coding_agent import cli
    from vtx.plugins import active_host

    monkeypatch.setattr("vtx.core.paths.get_config_dir", lambda: tmp_path)
    # Stop before the TUI launch, which needs a terminal.
    monkeypatch.setattr(launch, "run_tui", lambda args: None)
    sys.argv = ["vtx", "--profile", "headless"]
    cli.main()
    assert active_host() is not None
    assert active_host().base.label == "headless"
    active_host().dispose()


def test_a_broken_composition_fails_loud(tmp_path, monkeypatch, capsys):
    import sys

    import vtx.tui.launch as launch
    from vtx.coding_agent import cli

    monkeypatch.setattr("vtx.core.paths.get_config_dir", lambda: tmp_path)
    directory = tmp_path / "profiles" / "broken"
    directory.mkdir(parents=True)
    (directory / "bundle.txt").write_text("base\n", encoding="utf-8")
    (directory / "cordis.yml").write_text("- nope:\n    - whatever\n", encoding="utf-8")
    monkeypatch.setattr(launch, "run_tui", lambda args: None)
    sys.argv = ["vtx", "--profile", "broken"]
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1
    assert "plugin composition failed" in capsys.readouterr().err
