"""CTX's own subsystems, re-expressed as plugins.

Every row here is a peer of anything a third party writes. ``BASE`` is the layer
every profile starts from; the mode layers add a run surface on top. A row whose
service set differs by mode belongs to the mode layer, not to ``BASE``.

Providers live in ``services/`` and each claim one ``ctx.<key>``; tool rows live
in ``tools/`` and each register one tool on ``ctx.tools``. That split is the
capability-seam shape: the definition, the provider, and the consumer are
separate rows, so one provider swap changes the whole product.
"""

from __future__ import annotations

from ctx.plugins.loader import Row

#: The shared first layer: services, then the tools that consume them. Row order
#: carries no load semantics — activation is service-availability driven, so a
#: provider may sit below its consumers or above them.
BASE: list[Row] = [
    # ---- service providers ---------------------------------------------
    Row(id="tools", name="ctx.plugins.builtins.services.tools:PLUGIN"),
    Row(
        id="llm",
        name="ctx.plugins.builtins.services.llm:PLUGIN",
        config={"default_provider": None},
    ),
    Row(id="sessions", name="ctx.plugins.builtins.services.sessions:PLUGIN"),
    Row(id="permissions", name="ctx.plugins.builtins.services.permissions:PLUGIN"),
    Row(id="prompts", name="ctx.plugins.builtins.services.prompts:PLUGIN"),
    Row(id="compaction", name="ctx.plugins.builtins.services.compaction:PLUGIN"),
    Row(id="skills", name="ctx.plugins.builtins.services.skills:PLUGIN"),
    Row(id="telemetry", name="ctx.plugins.builtins.services.telemetry:PLUGIN"),
    Row(id="sandbox", name="ctx.plugins.builtins.services.sandbox:PLUGIN"),
    Row(id="mcp", name="ctx.plugins.builtins.services.mcp:PLUGIN"),
    Row(id="agents", name="ctx.plugins.builtins.services.agents:PLUGIN"),
    Row(id="agent-loop", name="ctx.plugins.builtins.services.agent_loop:PLUGIN"),
    Row(id="session-projections", name="ctx.plugins.builtins.services.projections:PLUGIN"),
    Row(id="extensions", name="ctx.plugins.builtins.services.extensions:PLUGIN"),
    Row(id="hooks", name="ctx.plugins.builtins.hooks:PLUGIN"),
    # ---- tools ----------------------------------------------------------
    # Engine-level tools only. Product toolsets (e.g. the coding agent's
    # read/bash/grep rows) are contributed by the product package — it extends
    # ``BASE``/``MINIMAL`` at import time, keeping this bundle product-neutral.
    Row(id="tool-web", name="ctx.plugins.builtins.tools.web:PLUGIN"),
    Row(id="tool-ask-user", name="ctx.plugins.builtins.tools.ask_user:PLUGIN"),
    Row(id="tool-subagent", name="ctx.plugins.builtins.tools.subagent:PLUGIN"),
]

#: The interactive terminal surface: the UI service the TUI installs, plus the
#: slash commands it dispatches without a model turn.
TUI: list[Row] = [
    Row(id="ui", name="ctx.plugins.builtins.ui:PLUGIN"),
    Row(id="commands", name="ctx.plugins.builtins.commands:PLUGIN"),
]

#: The one-shot runner adds nothing: a serverless run has no reload channel, so
#: it boots the base layer alone.
HEADLESS: list[Row] = []

#: The smallest composition that still has a tool registry, a provider seam, and
#: a shell. Useful for embedding and for tests that only need the kernel.
MINIMAL: list[Row] = [
    Row(id="tools", name="ctx.plugins.builtins.services.tools:PLUGIN"),
    Row(
        id="llm",
        name="ctx.plugins.builtins.services.llm:PLUGIN",
        config={"default_provider": None},
    ),
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
