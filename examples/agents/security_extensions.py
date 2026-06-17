"""Agent-scoped extension for the ``security-audit`` agent.

Demonstrates the cross-agent local_tool API: any extension can register
tools that only exist when a specific agent is active. Move this file
to ``~/.vtx/agent_extensions/`` (or the project-local equivalent) and
the ``secrets_scan`` tool will appear only when ``security-audit`` is
the active agent.
"""


def register(api):
    @api.register_local_tool(
        agent="security-audit",
        name="secrets_scan",
        description="Scan a path for hardcoded secrets (placeholder).",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Path to scan"}},
            "required": ["path"],
        },
        mutating=False,
    )
    def secrets_scan(args, ctx):
        # Real implementation would shell out to trufflehog / gitleaks.
        return {"success": True, "result": f"(stub) no secrets found in {args['path']!r}"}
