"""Independent completion audit (re-exported from vtx.ai.agent.goal.auditor)."""

from __future__ import annotations

from vtx.ai.agent.goal.auditor import (
    AUDIT_TOOLS,
    MAX_AUDIT_TURNS,
    AuditResult,
    run_completion_audit,
)

__all__ = ["AUDIT_TOOLS", "MAX_AUDIT_TURNS", "AuditResult", "run_completion_audit"]
