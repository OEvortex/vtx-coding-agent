"""Code review handoff agent.

A read-only profile that surfaces a PR-summary tool scoped to this agent
and gates destructive ``bash`` commands. Use with ``/agent code-review``
or Shift+Tab to switch into it.
"""

from vtx.agents import AgentDef

AGENT = AgentDef(
    name="code-review",
    description="Read-only code review profile",
    icon="🔍",
    color="blue",
    thinking_level="high",
    max_turns=200,
    instructions=(
        "You are reviewing code, not writing it. Be terse and concrete. "
        "Output [P0]..[P3] findings only — no preamble, no apologies, "
        "no 'I'd suggest' hedging. Do not modify any files."
    ),
    instructions_mode="append",
    tools_allow=["read", "find", "grep", "skill"],
    tools_deny=["bash", "write", "edit"],
    permission_mode="auto",
    permission_gates=[
        {
            "tool": "bash",
            "when": "command matches 'rm -rf'",
            "action": "deny",
            "reason": "destructive commands are blocked in review mode",
        }
    ],  # ty:ignore[invalid-argument-type]
    handoff_back=True,
    metadata={"cost_tier": "low", "owner": "@me"},
)


def register(api):
    @api.local_tool(
        name="pr_summary",
        description="Summarize the current PR's diff (stub: lists changed files).",
        parameters={
            "type": "object",
            "properties": {"base": {"type": "string", "description": "Base ref"}},
            "required": ["base"],
        },
        mutating=False,
    )
    def pr_summary(args, ctx):
        # Real implementation would shell out to `git diff` and return
        # a structured summary. Stub returns a placeholder.
        return {"success": True, "result": f"(stub) PR summary against {args['base']!r}"}

    @api.local_command(name="checklist", description="Run the code review checklist")
    def checklist(args):
        return "review checklist:\n  - [ ] tests\n  - [ ] docs\n  - [ ] error paths"

    api.permission_gate(
        tool="bash",
        when="command matches 'sudo'",
        action="deny",
        reason="sudo is never allowed in review mode",
    )
