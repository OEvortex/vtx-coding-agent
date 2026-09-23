"""Minimal IPython REPL runtime speaking newline-delimited JSON over stdio.

Entry point: ``python -m vtx.ai.agent.ipython_runtime``. The implementation
lives in :mod:`vtx.ai.agent.rlm.repl`; this module keeps the historical entry
point and import surface (``RLMContext``, ``transform_cell_code``, ...).

Ported from Prime Agent (MIT) — https://github.com/PrimeIntellect-ai/prime-agent
"""

from __future__ import annotations

from vtx.ai.agent.rlm.repl import (
    PROTOCOL_VERSION,
    RLMContext,
    _init_builtin_helpers,
    _init_python_skills,
    _record_history,
    _record_result,
    _update_context_in_namespace,
    call_tool,
    emit,
    host_request,
    is_active,
    main,
    transform_cell_code,
)

__all__ = [
    "PROTOCOL_VERSION",
    "RLMContext",
    "_init_builtin_helpers",
    "_init_python_skills",
    "_record_history",
    "_record_result",
    "_update_context_in_namespace",
    "call_tool",
    "emit",
    "host_request",
    "is_active",
    "main",
    "transform_cell_code",
]


if __name__ == "__main__":
    main()
