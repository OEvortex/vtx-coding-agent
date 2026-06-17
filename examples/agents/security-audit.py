"""Read-only security review agent.

Pulls in an extension that contributes a ``secrets_scan`` tool, scoped
to this agent only. Run ``/agent security-audit`` (or Shift+Tab) to
switch into it.
"""

from vtx.agents import AgentDef

AGENT = AgentDef(
    name="security-audit",
    description="Read-only security review (secrets + dependency audit)",
    icon="🛡",
    thinking_level="high",
    tools_allow=["read", "find", "grep", "skill", "bash"],
    tools_deny=["write", "edit"],
    permission_mode="auto",
    # Load an agent-scoped extension that contributes the secrets_scan tool.
    extensions=["./security_extensions.py"],
)
