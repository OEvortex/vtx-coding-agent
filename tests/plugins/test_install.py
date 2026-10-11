"""Tests for the install gate: compatibility refusal and exact-pair exemptions."""

from __future__ import annotations

import json
import textwrap

import pytest

import vtx.plugins.install as install_module
from vtx.plugins.install import (
    InstallPlan,
    _package_of,
    _satisfies,
    comparable,
    is_exempt,
    plan_install,
    read_exemptions,
    verify_installed,
    write_exemption,
)
from vtx.plugins.profile import init_profile

#: An editable checkout reports VERSION as "editable", which cannot be ordered
#: against a requirement; pin a real one so the gate is under test.
RUNNING = "1.2.2"


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setattr("vtx.core.paths.get_config_dir", lambda: tmp_path)
    monkeypatch.setattr(install_module, "VERSION", RUNNING)
    init_profile("default")


def _write_pyproject(directory, *, version: str, requirement: str | None):
    (directory / "pyproject.toml").write_text(
        textwrap.dedent(f"""\
            [project]
            name = "demo-plugin"
            version = "{version}"
            [project.optional-dependencies]
            vtx = ["{requirement}"]
            """)
        if requirement
        else textwrap.dedent(f"""\
            [project]
            name = "demo-plugin"
            version = "{version}"
            """),
        encoding="utf-8",
    )


def test_a_compatible_local_path_is_allowed(tmp_path):
    _write_pyproject(tmp_path, version="1.0.0", requirement=f">={RUNNING}")
    plan = plan_install(str(tmp_path))
    assert plan.compatible is True
    assert plan.version == "1.0.0"
    assert plan.required_range == f">={RUNNING}"


def test_an_incompatible_local_path_is_refused_with_the_reason(tmp_path):
    _write_pyproject(tmp_path, version="1.0.0", requirement=">=99.0")
    plan = plan_install(str(tmp_path))
    assert plan.compatible is False
    assert f"requires vtx >=99.0, running {RUNNING}" == plan.reason


def test_a_package_declaring_no_vtx_requirement_is_allowed(tmp_path):
    _write_pyproject(tmp_path, version="1.0.0", requirement=None)
    plan = plan_install(str(tmp_path))
    assert plan.compatible is True
    assert plan.required_range is None


def test_a_path_without_a_pyproject_is_refused(tmp_path):
    plan = plan_install(str(tmp_path / "nowhere"))
    assert plan.compatible is False
    assert "no pyproject.toml" in plan.reason


def test_a_registry_spec_is_unjudged_until_installed():
    plan = plan_install("some-plugin")
    assert plan.compatible is True
    assert plan.reason == "unjudged until installed"


def test_the_exempt_key_pins_the_exact_pair():
    plan = InstallPlan("demo-plugin", "1.2.3", "demo-plugin", ">=99.0", False)
    assert plan.exempt_key == "demo-plugin@1.2.3"


def test_exemptions_round_trip(tmp_path):
    assert read_exemptions("default") == ({}, [])
    write_exemption("default", "demo@1.0.0", RUNNING, enabled=True, accept_risk=True)
    grants, warnings = read_exemptions("default")
    assert grants == {"demo@1.0.0": [RUNNING]}
    assert warnings == []


def test_granting_without_accept_risk_is_refused():
    with pytest.raises(Exception, match="--accept-risk"):
        write_exemption("default", "demo@1.0.0", RUNNING, enabled=True, accept_risk=False)


def test_revoking_the_last_version_removes_the_entry(tmp_path):
    write_exemption("default", "demo@1.0.0", RUNNING, enabled=True, accept_risk=True)
    write_exemption("default", "demo@1.0.0", RUNNING, enabled=False)
    grants, _warnings = read_exemptions("default")
    assert grants == {}


def test_revoking_one_version_keeps_the_others(tmp_path):
    write_exemption("default", "demo@1.0.0", "1.0.0", enabled=True, accept_risk=True)
    write_exemption("default", "demo@1.0.0", "2.0.0", enabled=True, accept_risk=True)
    write_exemption("default", "demo@1.0.0", "1.0.0", enabled=False)
    grants, _warnings = read_exemptions("default")
    assert grants == {"demo@1.0.0": ["2.0.0"]}


def test_the_exemption_file_is_readable_json(tmp_path):
    write_exemption("default", "demo@1.0.0", RUNNING, enabled=True, accept_risk=True)
    path = tmp_path / "profiles" / "default" / "compatibility.json"
    assert json.loads(path.read_text()) == {"exemptions": {"demo@1.0.0": [RUNNING]}}


def test_an_unreadable_exemption_file_does_not_break_the_read(tmp_path):
    path = tmp_path / "profiles" / "default" / "compatibility.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    grants, warnings = read_exemptions("default")
    assert grants == {}
    assert len(warnings) == 1


def test_is_exempt_requires_the_exact_runtime_version():
    plan = InstallPlan("demo", "1.0.0", "demo", ">=99.0", False)
    assert is_exempt(plan) is False
    write_exemption("default", "demo@1.0.0", "9.9.9", enabled=True, accept_risk=True)
    assert is_exempt(plan) is False
    write_exemption("default", "demo@1.0.0", RUNNING, enabled=True, accept_risk=True)
    assert is_exempt(plan) is True


def test_a_compatible_plan_is_exempt_without_any_grant():
    plan = InstallPlan("demo", "1.0.0", "demo", None, True)
    assert is_exempt(plan) is True


def test_verify_installed_reports_a_missing_distribution():
    plan = verify_installed("vtx-this-package-does-not-exist-xyz")
    assert plan.compatible is False
    assert "not installed" in plan.reason


def test_package_of_normalises_every_spec_spelling():
    assert _package_of("demo-plugin") == "demo-plugin"
    assert _package_of("demo-plugin==1.0.0") == "demo-plugin"
    assert _package_of("https://github.com/me/demo-plugin") == "demo-plugin"
    assert _package_of("git@github.com:me/demo-plugin.git") == "demo-plugin"
    assert _package_of("/tmp/some/dir/demo-plugin") == "demo-plugin"
    assert _package_of("./demo-plugin") == "demo-plugin"


def test_a_local_refusal_names_the_package_not_the_path(tmp_path):
    _write_pyproject(tmp_path, version="1.0.0", requirement=">=99.0")
    plan = plan_install(str(tmp_path))
    assert plan.package == "demo-plugin"
    assert plan.exempt_key == "demo-plugin@1.0.0"


def test_a_non_pep440_runtime_version_is_treated_as_satisfied():
    """ "editable" is what a source checkout reports; it must not crash the gate."""
    assert _satisfies("editable", ">=1.0,<2.0") is True
    assert comparable("editable") is False
    assert comparable("1.2.2") is True


def test_a_satisfied_range_is_true_and_an_unsatisfied_one_is_false():
    assert _satisfies("1.5.0", ">=1.2,<2.0") is True
    assert _satisfies("2.0.0", ">=1.2,<2.0") is False
    assert _satisfies("1.2.1", ">=1.2,<2.0") is True


def test_an_unparseable_requirement_is_treated_as_satisfied():
    """ "not a requirement" is a malformed declaration, not a refusal."""
    assert _satisfies("1.2.2", "not a requirement") is True


def test_the_vtx_group_may_name_the_package_or_be_a_bare_specifier(tmp_path):
    for requirement in (f"vtx-coding-agent>={RUNNING}", f">={RUNNING}"):
        directory = tmp_path / requirement.replace(">", "_gt_")
        directory.mkdir()
        _write_pyproject(directory, version="1.0.0", requirement=requirement)
        plan = plan_install(str(directory))
        assert plan.compatible is True, requirement
        assert plan.required_range == f">={RUNNING}", requirement
