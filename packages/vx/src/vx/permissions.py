"""Permission modes and runtime for vx, inspired by fx's permission system.

Two modes control how tool calls are handled:
- **ask** — every mutating tool call prompts the user for approval
- **auto** — bypass all checks, everything runs freely

Session grants let the user allow a tool+path combination for the entire session.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ctx.agent.hooks.agent_hook import AgentHook


class PermissionMode(Enum):
    ask = "ask"
    auto = "auto"


@dataclass(frozen=True)
class ModeSpec:
    """One permission mode: id, display name, description, and tool policy."""

    id: str
    name: str
    description: str = ""
    permission_mode: PermissionMode = PermissionMode.ask
    #: Which tools are exposed to the model.
    tool_policy: str = "full"  # "full" | "read_only"
    tool_policy_denial_message: str = ""


#: The two built-in modes.
BUILTIN_MODES: list[ModeSpec] = [
    ModeSpec(
        id="ask",
        name="Ask",
        description="Confirm each action before it runs",
        permission_mode=PermissionMode.ask,
    ),
    ModeSpec(
        id="auto",
        name="Auto",
        description="Bypass all checks — everything runs freely",
        permission_mode=PermissionMode.auto,
        tool_policy="full",
    ),
]

DEFAULT_MODE = "auto"


@dataclass(frozen=True)
class PermissionGrant:
    """A session-scoped grant: allow a tool on a target path without asking again."""

    tool_name: str
    target_path: str


# -- read-only tool names ----------------------------------------------------

#: Tools that never mutate state; always allowed without a prompt.
READ_ONLY_TOOLS = frozenset({"read", "grep", "web", "web_search", "web_fetch"})


# -- decision ----------------------------------------------------------------


class RuleDecision(Enum):
    allow = "allow"
    deny = "deny"
    permission_required = "permission_required"


@dataclass
class PermissionResult:
    """Outcome of checking a tool call against the current mode + grants."""

    decision: RuleDecision
    reason: str = ""


# -- runtime -----------------------------------------------------------------


class PermissionRuntime:
    """Manages the current permission mode, session grants, and tool admission.

    Mirrors fx's ``app_permission_runtime.zig``: mode switching, persistence,
    grant tracking, and the core ``check()`` gate.
    """

    def __init__(self) -> None:
        self._mode: str = DEFAULT_MODE
        self._grants: list[PermissionGrant] = []

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def permission_mode(self) -> PermissionMode:
        spec = mode_spec(self._mode)
        return spec.permission_mode if spec else PermissionMode.ask

    @property
    def grants(self) -> list[PermissionGrant]:
        return list(self._grants)

    # -- mode switching ----------------------------------------------------

    def set_mode(self, mode_id: str) -> bool:
        """Change mode for the current session. Returns ``False`` if invalid."""
        if mode_spec(mode_id) is None:
            return False
        self._mode = mode_id
        return True

    def toggle_mode(self) -> str:
        """Cycle: ask → auto → ask …"""
        order = [m.id for m in BUILTIN_MODES]
        idx = order.index(self._mode) if self._mode in order else 0
        self._mode = order[(idx + 1) % len(order)]
        return self._mode

    def reset(self) -> None:
        """Reset to ``ask`` mode and clear all session grants."""
        self._mode = "ask"
        self._grants.clear()

    # -- grants ------------------------------------------------------------

    def add_grant(self, tool_name: str, target_path: str) -> None:
        """Record a session grant so the same tool+path won't ask again."""
        grant = PermissionGrant(tool_name=tool_name, target_path=target_path)
        if grant not in self._grants:
            self._grants.append(grant)

    def _is_granted(self, tool_name: str, target_path: str) -> bool:
        for g in self._grants:
            if g.tool_name == tool_name and (
                g.target_path == target_path or target_path.startswith(g.target_path)
            ):
                return True
        return False

    # -- permission gate ---------------------------------------------------

    def check(self, tool_name: str, target_path: str = "") -> PermissionResult:
        """Decide whether a tool call should proceed, be reviewed, or prompt.

        Mirrors fx's ``decideOrdinary()`` in permission_gate.zig.
        """
        pm = self.permission_mode

        # auto → always allow (bypass all checks)
        if pm == PermissionMode.auto:
            return PermissionResult(RuleDecision.allow)

        # read-only tools → always allow
        if tool_name in READ_ONLY_TOOLS:
            return PermissionResult(RuleDecision.allow)

        # check session grants
        if target_path and self._is_granted(tool_name, target_path):
            return PermissionResult(RuleDecision.allow)

        # ask mode → always prompt for mutating tools
        return PermissionResult(
            RuleDecision.permission_required, reason=f"Please confirm: {tool_name}"
        )


# -- lookups -----------------------------------------------------------------


def mode_spec(mode_id: str) -> ModeSpec | None:
    """Look up a mode by id."""
    for spec in BUILTIN_MODES:
        if spec.id == mode_id:
            return spec
    return None


def mode_label(mode_id: str) -> str:
    """Short display label for the hint row."""
    spec = mode_spec(mode_id)
    return spec.name if spec else mode_id


def mode_description(mode_id: str) -> str:
    """One-line description for picker metadata."""
    spec = mode_spec(mode_id)
    return spec.description if spec else ""


# -- ctx agent hook integration --------------------------------------------


class VxPermissionHook(AgentHook):
    """Bridges vx's PermissionRuntime into ctx's AgentHook system.

    Implements ``before_execute_tools`` to check each tool call against the
    current permission mode. In ``ask`` mode, mutating tools trigger the
    existing ToolApprovalEvent flow. In ``auto`` mode, everything passes
    silently without any checks.

    The hook stores a reference to the PermissionRuntime and the app's
    _request_approval method so it can trigger the UI approval flow.
    """

    def __init__(self, perm_runtime: PermissionRuntime) -> None:
        super().__init__()
        self.perm_runtime = perm_runtime

    async def before_execute_tools(self, context) -> None:
        """Check each pending tool call against the permission mode.

        This fires before the engine executes any tools in the iteration.
        Tool calls that need approval are handled by the existing
        ToolApprovalEvent mechanism in the turn loop, so this hook mainly
        tracks which tools were reviewed.
        """
        for tc in context.tool_calls:
            tool_name = getattr(tc, "name", "")
            # Extract target path from arguments
            target = ""
            args = getattr(tc, "arguments", None)
            if isinstance(args, dict):
                for key in ("path", "file_path", "filename", "command", "cmd"):
                    val = args.get(key)
                    if isinstance(val, str) and val.strip():
                        target = val.strip()
                        break
            self.perm_runtime.check(tool_name, target)
            # The actual approval gating happens in the turn loop via
            # ToolApprovalEvent; this hook records the decision for
            # telemetry and future auto-review enhancements.
