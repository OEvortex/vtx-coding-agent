from __future__ import annotations

import subprocess
import sys
import textwrap
import tomllib
from pathlib import Path


def test_source_checkout_import_uses_pyproject_version_without_metadata() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    pyproject_path = repo_root / "pyproject.toml"
    if pyproject_path.exists():
        expected = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))["project"]["version"]
    else:
        expected = "0.2.2"
    script = textwrap.dedent(
        f"""
        import sys
        import types

        sys.path.insert(0, {str(repo_root)!r})
        fake = types.ModuleType("vtx_claw.vtx_claw")
        fake.VtxClaw = object
        fake.RunResult = object
        sys.modules["vtx_claw.vtx_claw"] = fake

        import vtx_claw

        print(vtx_claw.__version__)
        """
    )

    proc = subprocess.run(
        [sys.executable, "-S", "-c", script], capture_output=True, text=True, check=False
    )

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == expected
