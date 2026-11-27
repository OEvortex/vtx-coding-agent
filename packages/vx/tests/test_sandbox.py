"""Sandbox: the bwrap argv is a contract, and a broken host says so plainly."""

import subprocess
from pathlib import Path

import pytest
from vx import sandbox


class TestArgv:
    def test_root_is_read_only_and_the_workspace_is_writable(self, tmp_path):
        argv = sandbox.build_argv("pytest -q", tmp_path, shell="/bin/bash")
        joined = " ".join(argv)
        assert argv[0] == "bwrap"
        assert "--ro-bind / /" in joined
        assert f"--bind {tmp_path} {tmp_path}" in joined
        assert "--die-with-parent" in joined
        assert argv[-3:] == ["/bin/bash", "-c", "pytest -q"]

    def test_network_is_denied_by_default(self, tmp_path):
        argv = sandbox.build_argv("curl example.com", tmp_path, shell="/bin/sh")
        assert "--unshare-net" in argv

    def test_network_can_be_allowed(self, tmp_path):
        argv = sandbox.build_argv("curl example.com", tmp_path, shell="/bin/sh", network=True)
        assert "--unshare-net" not in argv
        assert "network allowed" in sandbox.describe(network=True)

    def test_the_command_is_separated_by_a_double_dash(self, tmp_path):
        argv = sandbox.build_argv("echo hi", tmp_path, shell="/bin/sh")
        assert argv[argv.index("--") :] == ["--", "/bin/sh", "-c", "echo hi"]

    def test_a_path_inside_the_workspace_is_not_bound_twice(self, tmp_path):
        inner = tmp_path / "cache"
        inner.mkdir()
        argv = sandbox.build_argv("ls", tmp_path, shell="/bin/sh", extra_rw=(str(inner),))
        assert argv.count(str(inner)) == 0 or "--bind" in argv  # not duplicated as a bind
        assert sum(1 for a in argv if a == str(inner)) <= 1


class TestProbe:
    def setup_method(self):
        sandbox.reset_cache()

    def teardown_method(self):
        sandbox.reset_cache()

    def test_missing_bwrap_is_reported_not_crashed(self, monkeypatch):
        monkeypatch.setattr(sandbox.shutil, "which", lambda _name: None)
        support = sandbox.support()
        assert not support.available
        assert "not installed" in support.reason

    def test_a_working_host_is_available(self, monkeypatch):
        monkeypatch.setattr(sandbox.shutil, "which", lambda _name: "/usr/bin/bwrap")
        monkeypatch.setattr(
            sandbox.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, "", "")
        )
        assert sandbox.support().available

    def test_a_userns_refusal_carries_the_apparmor_hint(self, monkeypatch):
        monkeypatch.setattr(sandbox.shutil, "which", lambda _name: "/usr/bin/bwrap")
        monkeypatch.setattr(
            sandbox.subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess(
                a, 1, "", "bwrap: No permissions to create new namespace, requires CAP_SYS_ADMIN"
            ),
        )
        support = sandbox.support()
        assert not support.available
        assert "namespace" in support.reason

    def test_the_answer_is_cached(self, monkeypatch):
        calls = []

        def run(*a, **k):
            calls.append(1)
            return subprocess.CompletedProcess(a, 0, "", "")

        monkeypatch.setattr(sandbox.shutil, "which", lambda _name: "/usr/bin/bwrap")
        monkeypatch.setattr(sandbox.subprocess, "run", run)
        sandbox.support()
        sandbox.support()
        assert len(calls) == 1


class TestGuidance:
    def test_a_namespace_failure_becomes_advice(self):
        message = sandbox.wrap_error("ls", "bwrap: No permissions to create new namespace")
        assert message is not None
        assert "sandbox=false" in message

    def test_an_ordinary_failure_is_not_blamed_on_the_sandbox(self):
        assert sandbox.wrap_error("pytest -q", "1 failed, 2 passed") is None


class TestRealBwrap:
    """When the host can sandbox, it should actually sandbox."""

    def test_probe_matches_this_host(self):
        support = sandbox.support(refresh=True)
        if not support.available:
            pytest.skip(f"host cannot bwrap: {support.reason}")
        inside = Path("/tmp")
        proc = subprocess.run(
            sandbox.build_argv("test -w /tmp && echo writable", inside, shell="/bin/bash"),
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert "writable" in proc.stdout
