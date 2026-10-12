"""VTX's own subsystems, re-expressed as plugins.

Every row here is a peer of anything a third party writes. ``BASE`` is the layer
every profile starts from; the mode layers add a run surface on top. A row whose
service set differs by mode belongs to the mode layer, not to ``BASE``.

Providers live in ``services/`` and each claim one ``ctx.<key>``; tool rows live
in ``tools/`` and each register one tool on ``ctx.tools``. That split is the
capability-seam shape: the definition, the provider, and the consumer are
separate rows, so one provider swap changes the whole product.
"""

from __future__ import annotations

from vtx.plugins.loader import Row

#: The shared first layer: services, then the tools that consume them. Row order
#: carries no load semantics — activation is service-availability driven, so a
#: provider may sit below its consumers or above them.
BASE: list[Row] = [
    # ---- service providers ---------------------------------------------
    Row(id="tools", name="vtx.plugins.builtins.services.tools:PLUGIN"),
    Row(
        id="llm",
        name="vtx.plugins.builtins.services.llm:PLUGIN",
        config={"default_provider": None},
    ),
    Row(id="sessions", name="vtx.plugins.builtins.services.sessions:PLUGIN"),
    Row(id="permissions", name="vtx.plugins.builtins.services.permissions:PLUGIN"),
    Row(id="prompts", name="vtx.plugins.builtins.services.prompts:PLUGIN"),
    Row(id="compaction", name="vtx.plugins.builtins.services.compaction:PLUGIN"),
    Row(id="skills", name="vtx.plugins.builtins.services.skills:PLUGIN"),
    Row(id="telemetry", name="vtx.plugins.builtins.services.telemetry:PLUGIN"),
    Row(id="sandbox", name="vtx.plugins.builtins.services.sandbox:PLUGIN"),
    Row(id="mcp", name="vtx.plugins.builtins.services.mcp:PLUGIN"),
    Row(id="agents", name="vtx.plugins.builtins.services.agents:PLUGIN"),
    Row(id="agent-loop", name="vtx.plugins.builtins.services.agent_loop:PLUGIN"),
    Row(id="session-projections", name="vtx.plugins.builtins.services.projections:PLUGIN"),
    Row(id="goals", name="vtx.plugins.builtins.services.goals:PLUGIN"),
    Row(id="extensions", name="vtx.plugins.builtins.services.extensions:PLUGIN"),
    Row(id="hooks", name="vtx.plugins.builtins.hooks:PLUGIN"),
    # ---- tools ----------------------------------------------------------
    # Coding-agent tools: the row addresses the owning product package, so the
    # plugin system never imports a product to mount one.
    Row(id="tool-read", name="vtx.coding_agent.tools.read:PLUGIN"),
    Row(id="tool-edit", name="vtx.coding_agent.tools.edit:PLUGIN"),
    Row(id="tool-write", name="vtx.coding_agent.tools.write:PLUGIN"),
    Row(id="tool-bash", name="vtx.coding_agent.tools.bash:PLUGIN"),
    Row(id="tool-find", name="vtx.coding_agent.tools.find:PLUGIN"),
    Row(id="tool-grep", name="vtx.coding_agent.tools.grep:PLUGIN"),
    Row(id="tool-skill", name="vtx.coding_agent.tools.skill:PLUGIN"),
    Row(id="tool-web", name="vtx.plugins.builtins.tools.web:PLUGIN"),
    Row(id="tool-ask-user", name="vtx.plugins.builtins.tools.ask_user:PLUGIN"),
    # Registered but not default-on: the harness declares ``tool_search`` only
    # when the session has tools it has not been offered (see
    # ``ConversationRuntime.declared_tools``), and ``codemode`` is what makes the
    # script-reachable ones reachable.
    Row(id="tool-search", name="vtx.coding_agent.tools.tool_search:PLUGIN"),
    Row(id="tool-codemode", name="vtx.coding_agent.tools.codemode:PLUGIN"),
    Row(id="tool-goal", name="vtx.plugins.builtins.tools.goal:PLUGIN"),
    Row(id="tool-subagent", name="vtx.plugins.builtins.tools.subagent:PLUGIN"),
]

#: The interactive terminal surface: the UI service the TUI installs, plus the
#: slash commands it dispatches without a model turn.
TUI: list[Row] = [
    Row(id="ui", name="vtx.plugins.builtins.ui:PLUGIN"),
    Row(id="commands", name="vtx.plugins.builtins.commands:PLUGIN"),
]

#: The one-shot runner adds nothing: a serverless run has no reload channel, so
#: it boots the base layer alone.
HEADLESS: list[Row] = []

#: The smallest composition that still has a tool registry, a provider seam, and
#: a shell. Useful for embedding and for tests that only need the kernel.
MINIMAL: list[Row] = [
    Row(id="tools", name="vtx.plugins.builtins.services.tools:PLUGIN"),
    Row(
        id="llm",
        name="vtx.plugins.builtins.services.llm:PLUGIN",
        config={"default_provider": None},
    ),
    Row(id="tool-read", name="vtx.coding_agent.tools.read:PLUGIN"),
    Row(id="tool-bash", name="vtx.coding_agent.tools.bash:PLUGIN"),
]

#: The shipped bundles, addressable by name from a profile.
BUNDLES: dict[str, list[Row]] = {
    "base": BASE,
    "tui": TUI,
    "headless": HEADLESS,
    "minimal": MINIMAL,
}

#: The shipped profile templates: an ordered bundle list per profile name.
PROFILE_TEMPLATES: dict[str, tuple[str, ...]] = {
    "default": ("base", "tui"),
    "headless": ("base",),
    "minimal": ("minimal",),
}

#: The bundle list a profile with no shipped template starts from.
DEFAULT_PROFILE_BUNDLES: tuple[str, ...] = ("base", "tui")


def row_dicts(rows: list[Row]) -> list[dict]:
    return [row.to_dict() for row in rows]


__all__ = [
    "BASE",
    "BUNDLES",
    "DEFAULT_PROFILE_BUNDLES",
    "HEADLESS",
    "MINIMAL",
    "PROFILE_TEMPLATES",
    "TUI",
    "row_dicts",
]
