"""Demo: ``code-review`` handoff agent.

A read-only code-review profile. Drop this file at ``.vtx/agent/code-review.py``
(project) or ``~/.vtx/agent/code-review.py`` (global) to make it discoverable.

What this demo shows
====================

* Module-level ``AGENT = AgentDef(...)`` for the static profile
* Optional ``register(api)`` for imperative side effects
* ``api.local_tool(...)`` (decorator form) for an agent-scoped tool
* ``api.local_command(...)`` (decorator form) for an agent-scoped slash command
* ``api.permission_gate(...)`` for imperative permission rules layered on top
  of the declarative ``permission_gates`` in the AgentDef
* ``api.on(AGENT_ACTIVATED)`` for lifecycle hooks

How to use it
=============

* Launch:   ``vtx --agent code-review``
* Switch:   press ``Shift+Tab`` in the TUI, or run ``/agent code-review``
* Inspect:  ``/agent list`` shows it (with the active marker)
* Disable:  ``vtx --no-agents`` skips auto-discovery
"""

from __future__ import annotations

import shutil
import subprocess

from vtx.agents import AgentDef

# -----------------------------------------------------------------------------
# Static profile
# -----------------------------------------------------------------------------

AGENT = AgentDef(
    name="code-review",
    description="Read-only code review profile with PR summary + checklist",
    icon="🔍",
    color="blue",
    # Model / provider overrides — high thinking, generous turn cap.
    thinking_level="high",
    max_turns=200,
    # Append a terse review style guide after the base identity.
    # The base identity (Vtx, tool usage, etc.) is still prepended.
    instructions=(
        "You are reviewing code, not writing it. Be terse and concrete. "
        "Output [P0]..[P3] findings only — no preamble, no apologies, "
        "no 'I'd suggest' hedging. Do not modify any files. If the user "
        "asks for an implementation, refuse and remind them to switch "
        "out of review mode (/agent off)."
    ),
    instructions_mode="append",
    # Tool surface: keep the read-only tools, drop the mutating ones.
    tools_allow=["read", "find", "grep", "skill", "ask_user"],
    tools_deny=["bash", "write", "edit"],
    # Permission mode + declarative gate rules.
    permission_mode="auto",
    permission_gates=[
        {
            "tool": "bash",
            "when": "command matches 'rm -rf'",
            "action": "deny",
            "reason": "destructive commands are blocked in review mode",
        }
    ],  # ty:ignore[invalid-argument-type]
    # This agent can route back to the default session profile.
    handoff_back=True,
    metadata={"cost_tier": "low", "owner": "@me"},
)


# -----------------------------------------------------------------------------
# Imperative registration: tools, commands, gates, lifecycle hooks
# -----------------------------------------------------------------------------


# A tiny helper used by the local tool below. Kept module-level so the
# decorator-form ``register(api)`` can close over it cleanly.
def _summarize_diff(base: str, cwd: str) -> str:
    """Return a one-paragraph summary of the working diff against ``base``.

    Falls back gracefully when ``git`` is unavailable or the cwd is not
    a git repository.
    """
    if not shutil.which("git"):
        return "(git not available; install git to enable diff summaries)"

    try:
        result = subprocess.run(
            ["git", "diff", "--stat", f"{base}...HEAD"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return f"(git diff failed: {exc})"

    if result.returncode != 0:
        # Not a git repo, or the base ref is missing.
        return f"(git diff unavailable: {result.stderr.strip() or 'non-zero exit'})"

    stat = result.stdout.strip()
    if not stat:
        return f"No changes between {base} and HEAD."

    # Parse the summary line (last "X files changed, ..." line).
    summary_line = ""
    files_changed = 0
    for line in stat.splitlines():
        if "files changed" in line or "file changed" in line:
            summary_line = line
        files_changed += 1
    files_changed = max(0, files_changed - 1)  # subtract the trailing summary line

    return f"{files_changed} file(s) changed against {base}.\n{summary_line}"


def register(api):
    # --- agent-scoped local tool -----------------------------------------
    # Available only when ``code-review`` is the active agent. Note that
    # this tool bypasses the agent's own tools_allow/tools_deny filters
    # (see ``compose_active_tools`` in ``vtx.agents.activate``).
    @api.local_tool(
        name="pr_summary",
        description="Summarize the current PR's diff (uses git diff --stat).",
        parameters={
            "type": "object",
            "properties": {
                "base": {
                    "type": "string",
                    "description": "Base ref to compare against (e.g. 'main', 'origin/main')",
                }
            },
            "required": ["base"],
        },
        mutating=False,
    )
    def pr_summary(args, ctx):
        base = args["base"]
        cwd = ctx.get("cwd") if ctx else None
        if not cwd:
            return {"success": False, "result": "no cwd available"}
        return {"success": True, "result": _summarize_diff(base, cwd)}

    # --- agent-scoped slash command --------------------------------------
    @api.local_command(name="checklist", description="Print the code-review checklist")
    def checklist(args):
        return (
            "code-review checklist:\n"
            "  [ ] tests cover new code paths\n"
            "  [ ] docs updated (README, docstrings, CHANGELOG)\n"
            "  [ ] error paths handled and tested\n"
            "  [ ] no secrets, tokens, or PII in the diff\n"
            "  [ ] public API changes reflected in type stubs"
        )

    # --- imperative permission gate --------------------------------------
    # Layered on top of the declarative ``permission_gates`` in the
    # AgentDef. Use a Python predicate for anything the small ``when``
    # expression language can't express.
    def _blocks_sudo(args: dict) -> bool:
        return "sudo" in (args.get("command") or "")

    api.permission_gate(
        tool="bash",
        when=_blocks_sudo,
        action="deny",
        reason="sudo is never allowed in review mode",
    )

    # --- lifecycle hook ---------------------------------------------------
    # Fires when the agent is activated (session start, /agent <name>,
    # or Shift+Tab). Use it to set up per-agent state, log telemetry,
    # or post a notification.
    @api.on("agent_activated")
    def on_activated(event, payload):
        api.notify("code-review active — read-only mode, [P0]..[P3] findings only")
