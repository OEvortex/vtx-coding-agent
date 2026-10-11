"""The point of the whole package: VTX's own subsystems, as replaceable rows."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from vtx.plugins import ConfigError, PluginHost, boot, load_profile, plugin
from vtx.plugins.builtins import BUNDLES
from vtx.plugins.builtins.commands import CommandService
from vtx.plugins.builtins.ui import UIService
from vtx.plugins.loader import Row
from vtx.plugins.profile import init_profile
from vtx.plugins.services import PermissionService, PromptService, ToolService

EVERY_SERVICE = {
    "agents",
    "agentLoop",
    "compaction",
    "commands",
    "llm",
    "mcp",
    "permissions",
    "prompts",
    "sandbox",
    "sessions",
    "skills",
    "telemetry",
    "tools",
    "ui",
}


@pytest.fixture()
def mounted():
    host = boot()
    yield host
    host.dispose()


def _profile(tmp_path, monkeypatch, bundles: str, patch: str = "") -> PluginHost:
    monkeypatch.setattr("vtx.core.paths.get_config_dir", lambda: tmp_path)
    directory = tmp_path / "profiles" / "custom"
    directory.mkdir(parents=True)
    (directory / "bundle.txt").write_text(bundles + "\n", encoding="utf-8")
    if patch:
        (directory / "cordis.yml").write_text(patch, encoding="utf-8")
    return load_profile("custom")


def test_the_default_profile_mounts_every_service(tmp_path, monkeypatch):
    rows = _profile(tmp_path, monkeypatch, "base\ntui")
    host = PluginHost(rows)
    host.mount()
    assert {k for k in EVERY_SERVICE if k in host.base} >= EVERY_SERVICE
    host.dispose()


def test_vtxs_own_tools_come_from_rows(mounted):
    names = mounted.base.tools.names()
    for expected in (
        "read",
        "edit",
        "write",
        "bash",
        "find",
        "skill",
        "web",
        "ask_user",
        "delegate_subagent",
        "goal",
    ):
        assert expected in names, expected


def test_the_default_tool_set_matches_the_documented_one(mounted):
    assert set(mounted.base.tools.defaults()) >= {
        "read",
        "edit",
        "write",
        "bash",
        "find",
        "skill",
        "web",
        "ask_user",
        "delegate_subagent",
        "goal",
        "codemode",
        "tool_search",
    }


def test_parent_only_tools_stay_parent_only(mounted):
    assert {"delegate_subagent", "goal"} <= mounted.base.tools.parent_only()


def test_a_third_party_row_mounts_as_a_peer_of_a_builtin(tmp_path, monkeypatch):
    from vtx.agent.tools import unregister_tool

    class StubTool:
        name = "stub"
        description = "A tool contributed by a plugin from outside the tree."
        mutating = False

    # ``mount_tool`` rather than ``ctx.tools.register`` so the row's teardown
    # removes the tool again — the difference is exactly what the helper exists
    # to hide from a plugin author.
    from vtx.plugins.builtins.tools._register import mount_tool

    third_party = plugin(name="tool-stub", inject=("tools",))(
        lambda ctx, config: mount_tool(ctx, StubTool())
    )
    rows = _profile(tmp_path, monkeypatch, "base")
    host = PluginHost(rows)
    try:
        host.add(third_party)
        host.mount()
        assert host.base.tools.get("stub") is not None
        assert host.unmount("tool-stub") is True
        assert host.base.tools.get("stub") is None
    finally:
        host.dispose()
        # The row's teardown calls ``forget``, which is composition-scoped by
        # design: registration also writes the process-wide registry that
        # legacy consumers read, and a row unloading must not erase state it
        # did not create. That means a test which mounts a tool has to take it
        # back out itself. Left behind, this duck-typed stub — which has no
        # ``params`` — lands in the catalog and breaks whichever test reads the
        # catalog next in the same xdist worker.
        unregister_tool("stub")


def test_removing_a_tool_row_removes_the_tool(tmp_path, monkeypatch):
    rows = _profile(tmp_path, monkeypatch, "base", "- remove:\n    - tool-grep\n")
    host = PluginHost(rows)
    host.mount()
    assert host.base.tools.get("grep") is None
    host.dispose()


def test_disabling_a_tool_row_keeps_it_installed_but_not_registered(tmp_path, monkeypatch):
    rows = _profile(
        tmp_path, monkeypatch, "base", "- override:\n    - id: tool-grep\n      disabled: true\n"
    )
    host = PluginHost(rows)
    host.mount()
    assert host.base.tools.get("grep") is None
    assert "tool-grep" in host.row_ids()
    host.dispose()


def test_swapping_the_tools_service_replaces_the_registry(tmp_path, monkeypatch):
    """The seam: disable the built-in ``tools`` row and provide your own, and
    every tool row still resolves against it because activation is
    service-availability driven."""

    class RecordingTools(ToolService):
        def __init__(self) -> None:
            super().__init__()
            self.seen: list[str] = []

        def register(self, tool, **kwargs):
            self.seen.append(tool.name)
            return super().register(tool, **kwargs)

    replacement = RecordingTools()
    rows = _profile(
        tmp_path, monkeypatch, "base", "- override:\n    - id: tools\n      disabled: true\n"
    )
    host = PluginHost(rows)
    host.add(plugin(name="tools")(lambda ctx, config: ctx.provide("tools", replacement)))
    host.mount()
    assert replacement.seen, "the replacement tool service was never used"
    assert "read" in replacement.seen
    host.dispose()


def test_the_minimal_profile_mounts_only_what_it_declares(tmp_path, monkeypatch):
    rows = _profile(tmp_path, monkeypatch, "minimal")
    host = PluginHost(rows)
    host.mount()
    assert set(host.base.tools.names()) == {"bash", "read"}
    assert "sessions" not in host.base
    host.dispose()


def test_the_agent_loop_is_a_row_like_any_other(mounted):
    loop = mounted.base.agentLoop
    assert hasattr(loop, "create")
    assert hasattr(loop, "run")


def test_permission_rules_can_only_tighten():
    service = PermissionService()
    service.add_rule(lambda name, args: "allow" if name == "bash" else None)
    service.add_rule(lambda name, args: "prompt" if "rm" in str(args) else None)

    class Tool:
        name = "bash"
        mutating = True

    # The base decision for a mutating tool is prompt; an allow rule cannot
    # downgrade it, and the rm rule escalates it to prompt as well.
    assert service.decide(Tool(), {"command": "rm -rf /"}) in {"prompt", "deny"}


def test_prompt_sections_are_ordered():
    service = PromptService()
    service.add_section("first", lambda cwd, tools: "one")
    service.add_section("second", lambda cwd, tools: "two")
    assert service.section_keys() == ["first", "second"]


def test_prompt_build_joins_only_the_sections_that_render():
    service = PromptService()
    service.add_section("kept", lambda cwd, tools: "KEPT")
    service.add_section("skipped", lambda cwd, tools: None)
    built = service.build("/tmp", [], base_content="BASE")
    assert "KEPT" in built
    assert "BASE" in built


def test_commands_dispatch_without_a_model_turn():
    service = CommandService()
    service.register("ping", lambda: "pong")
    assert "ping" in service
    assert service.dispatch("ping") == "pong"
    assert service.names() == ["ping"]
    assert service.describe("ping") == ""
    assert service.unregister("ping") is True
    assert service.unregister("ping") is False


def test_dispatching_an_unknown_command_raises_a_key_error():
    service = CommandService()
    with pytest.raises(KeyError, match="nope"):
        service.dispatch("nope")


def test_ui_service_is_a_safe_noop_without_a_host():
    service = UIService()
    assert service.installed is False
    service.notify("hello")  # must not raise
    assert service.ask("pick") is None


def test_ui_service_routes_to_an_installed_host():
    class FakeUI:
        def __init__(self):
            self.seen: list[tuple[str, str]] = []

        def notify(self, message: str, level: str = "info") -> None:
            self.seen.append((message, level))

    ui = FakeUI()
    service = UIService()
    service.set_context(ui)
    service.notify("hi", "info")
    assert ui.seen == [("hi", "info")]


def test_ui_service_routes_a_question_to_the_host():
    class FakeUI:
        def ask(self, question, options=None):
            return "picked"

    service = UIService()
    service.set_context(FakeUI())
    assert service.ask("pick") == "picked"


def test_ui_fallback_collects_when_no_host_is_installed():
    collected: list[tuple[str, str]] = []
    service = UIService()
    service.set_fallback(lambda message, level: collected.append((message, level)))
    service.notify("bare", "warning")
    assert collected == [("bare", "warning")]


def test_tui_bundle_declares_the_ui_and_command_rows():
    assert {row.id for row in BUNDLES["tui"]} == {"ui", "commands"}


def test_headless_bundle_is_empty_by_design():
    assert BUNDLES["headless"] == []


def test_init_profile_is_idempotent_about_the_patch_file(tmp_path, monkeypatch):
    monkeypatch.setattr("vtx.core.paths.get_config_dir", lambda: tmp_path)
    first = init_profile("p")
    (first / "cordis.yml").write_text(
        "- override:\n    - id: tool-grep\n      disabled: true\n", encoding="utf-8"
    )
    second = init_profile("p")
    assert first == second
    assert "tool-grep" in (second / "cordis.yml").read_text()


class _Config(BaseModel):
    answer: int = 0


def test_a_row_config_is_validated_against_the_plugin_schema():
    spec = plugin(name="configured", config=_Config)(
        lambda ctx, config: ctx.provide("cfg", config)
    )
    host = PluginHost([Row(id="configured", name="configured", config={"answer": 7})])
    host.add(spec)
    host.mount()
    assert host.base.cfg.answer == 7
    host.dispose()


def test_invalid_row_config_fails_loud():
    spec = plugin(name="configured", config=_Config)(lambda ctx, config: None)
    host = PluginHost([Row(id="configured", name="configured", config={"answer": "no"})])
    host.add(spec)
    with pytest.raises(ConfigError, match="answer"):
        host.mount()


def test_a_sandbox_without_a_runner_refuses_rather_than_guessing():
    from vtx.plugins.services import SandboxService, ServiceMissingError

    with pytest.raises(ServiceMissingError, match="no sandbox runner"):
        SandboxService().run("print(1)")


def test_a_sandbox_provider_installs_its_runner():
    from vtx.plugins.services import SandboxService

    service = SandboxService()
    service.set_runner(lambda script: f"ran {script}")
    assert service.run("x") == "ran x"


def test_forget_drops_a_tool_from_the_view_but_not_the_registry():
    from vtx.agent.tools import get_all_tools
    from vtx.coding_agent.tools.bash import BashTool

    service = ToolService()
    service.register(BashTool())
    assert "bash" in service.all()
    assert "bash" in get_all_tools()
    service.forget("bash")
    assert service.get("bash") is None
    assert "bash" not in service.all()
    # The global registry keeps it: a row unloading must not erase state it did
    # not create.
    assert "bash" in get_all_tools()


def test_unregister_removes_from_both_the_view_and_the_registry():
    from vtx.agent.tools import get_all_tools
    from vtx.coding_agent.tools.bash import BashTool

    service = ToolService()
    service.register(BashTool())
    service.unregister("bash")
    assert service.get("bash") is None
    assert "bash" not in get_all_tools()


def test_unmounting_the_tools_row_leaves_the_global_registry_intact(tmp_path, monkeypatch):
    """The regression this guards: booting and disposing a composition must not
    empty the module-level registry other consumers read."""
    from vtx.agent.tools import get_all_tools

    monkeypatch.setattr("vtx.core.paths.get_config_dir", lambda: tmp_path)
    before = set(get_all_tools())
    host = boot()
    host.dispose()
    # Disposing must not remove anything. It may add: mounting imports the tool
    # modules, whose import side effects populate the global registry.
    assert before <= set(get_all_tools())


def test_boot_makes_the_host_reachable(tmp_path, monkeypatch):
    from vtx.plugins import active_host, reset

    monkeypatch.setattr("vtx.core.paths.get_config_dir", lambda: tmp_path)
    host = boot()
    try:
        assert active_host() is host
    finally:
        reset()
    assert active_host() is None
