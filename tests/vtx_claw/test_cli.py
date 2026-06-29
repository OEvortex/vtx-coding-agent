from __future__ import annotations

import pytest

from vtx_claw.cli import main
from vtx_claw.daemon import PIDManager


def test_cli_help(capsys):
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 0


def test_cli_stop_no_pid(tmp_path, monkeypatch):
    pid_file = tmp_path / "claw.pid"
    monkeypatch.setattr("vtx_claw.cli.PIDManager", lambda: PIDManager(pid_file=pid_file))
    main(["stop"])


def test_cli_status_no_config(tmp_path, monkeypatch):
    import vtx_claw.cli as cli_mod

    monkeypatch.setattr(
        cli_mod, "load_claw_config", lambda: (_ for _ in ()).throw(FileNotFoundError("no config"))
    )
    main(["status"])


def test_pid_manager_roundtrip(tmp_path):
    pm = PIDManager(tmp_path / "claw.pid")
    pm.write(12345)
    assert pm.read() == 12345
    pm.clear()
    assert pm.read() is None
