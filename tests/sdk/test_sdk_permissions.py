"""Tests for the permissions module."""

from __future__ import annotations

from vtx.sdk.permissions import AllowlistApprove, AutoApprove, PermissionDecision, PromptApprove
from vtx.sdk.tools import function_tool


def test_auto_approve_allows_everything() -> None:
    p = AutoApprove()
    assert p.decide(None, {}) == PermissionDecision.ALLOW  # type: ignore


def test_allowlist_approve_allows_listed() -> None:
    p = AllowlistApprove(["web_search"])

    @function_tool(mutating=True)
    def web_search(query: str) -> str:
        return ""

    @function_tool(mutating=True)
    def rm(path: str) -> str:
        return ""

    assert p.decide(web_search, {"query": "x"}) == PermissionDecision.ALLOW
    assert p.decide(rm, {"path": "/"}) == PermissionDecision.PROMPT


def test_prompt_approve_allows_readonly() -> None:
    p = PromptApprove()

    @function_tool(mutating=False)
    def read(path: str) -> str:
        return ""

    @function_tool
    def write(path: str, content: str) -> str:
        return ""

    assert p.decide(read, {"path": "/x"}) == PermissionDecision.ALLOW
    assert p.decide(write, {"path": "/x", "content": "y"}) == PermissionDecision.PROMPT


def test_prompt_approve_allows_safe_bash() -> None:
    p = PromptApprove()

    @function_tool
    def bash(command: str) -> str:
        return ""

    @function_tool
    def dangerous_bash(command: str) -> str:
        return ""

    assert p.decide(bash, {"command": "ls -la"}) == PermissionDecision.ALLOW
    assert p.decide(dangerous_bash, {"command": "rm -rf /"}) == PermissionDecision.PROMPT
