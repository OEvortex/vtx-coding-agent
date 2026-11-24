"""Permission runtime: modes, grants, and the check() gate."""

from vx.permissions import (
    BUILTIN_MODES,
    PermissionMode,
    PermissionRuntime,
    RuleDecision,
    mode_description,
    mode_label,
    mode_spec,
)


class TestModes:
    def test_default_is_auto(self):
        rt = PermissionRuntime()
        assert rt.mode == "auto"
        assert rt.permission_mode == PermissionMode.auto

    def test_set_mode_valid(self):
        rt = PermissionRuntime()
        assert rt.set_mode("ask")
        assert rt.mode == "ask"

    def test_set_mode_invalid(self):
        rt = PermissionRuntime()
        assert not rt.set_mode("bogus")
        assert rt.mode == "auto"

    def test_toggle_cycles(self):
        rt = PermissionRuntime()
        rt.set_mode("ask")
        assert rt.toggle_mode() == "auto"
        assert rt.toggle_mode() == "ask"

    def test_reset(self):
        rt = PermissionRuntime()
        rt.set_mode("auto")
        rt.add_grant("bash", "/tmp")
        rt.reset()
        assert rt.mode == "ask"
        assert rt.grants == []

    def test_mode_spec_lookups(self):
        spec = mode_spec("ask")
        assert spec is not None and spec.name == "Ask"
        assert mode_spec("nope") is None
        assert mode_label("auto") == "Auto"
        assert mode_label("nope") == "nope"
        assert "Bypass" in mode_description("auto")


class TestCheck:
    def test_auto_allows_everything(self):
        rt = PermissionRuntime()
        assert rt.check("bash", "/etc").decision == RuleDecision.allow

    def test_ask_prompts_mutating(self):
        rt = PermissionRuntime()
        rt.set_mode("ask")
        result = rt.check("bash", "/tmp/x")
        assert result.decision == RuleDecision.permission_required
        assert "bash" in result.reason

    def test_ask_allows_read_only(self):
        rt = PermissionRuntime()
        rt.set_mode("ask")
        for tool in ("read", "grep", "web", "web_search", "web_fetch"):
            assert rt.check(tool, "/etc/passwd").decision == RuleDecision.allow

    def test_grant_allows_matching_path(self):
        rt = PermissionRuntime()
        rt.set_mode("ask")
        rt.add_grant("bash", "/tmp/work")
        assert rt.check("bash", "/tmp/work").decision == RuleDecision.allow
        # Prefix match: children of the granted path are covered.
        assert rt.check("bash", "/tmp/work/sub/f").decision == RuleDecision.allow

    def test_grant_does_not_leak(self):
        rt = PermissionRuntime()
        rt.set_mode("ask")
        rt.add_grant("bash", "/tmp/work")
        assert rt.check("bash", "/tmp/other").decision == RuleDecision.permission_required
        # Different tool on the granted path still prompts.
        assert rt.check("apply_patch", "/tmp/work/f").decision == RuleDecision.permission_required

    def test_duplicate_grant_deduped(self):
        rt = PermissionRuntime()
        rt.add_grant("bash", "/a")
        rt.add_grant("bash", "/a")
        assert len(rt.grants) == 1


class TestBuiltinModes:
    def test_two_modes(self):
        assert {m.id for m in BUILTIN_MODES} == {"ask", "auto"}
