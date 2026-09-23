"""End-to-end tests for the RLM kernel's protocol v3 and strict contracts.

These spawn a real ``python -m vtx.ai.agent.ipython_runtime`` subprocess
through :class:`vtx.ai.agent.ipython_manager.IpythonKernel`, so they cover the
ready handshake, frame ordering, the host bridge round trip, and the two
semantics VTX shares with Prime Agent (``rlm`` is not callable, ``bash()``
always returns a handle).
"""

from __future__ import annotations

import pytest

from vtx.ai.agent.ipython_manager import PROTOCOL_VERSION, IpythonKernel


@pytest.mark.asyncio
async def test_ready_handshake_reports_protocol_version(tmp_path):
    kernel = IpythonKernel("test-protocol", cwd=str(tmp_path))
    await kernel.start()
    try:
        assert kernel._protocol == PROTOCOL_VERSION
    finally:
        await kernel.close()


@pytest.mark.asyncio
async def test_rlm_is_not_callable(tmp_path):
    """``rlm(...)`` must fail with Prime's spawn-directed message."""
    kernel = IpythonKernel("test-rlm-not-callable", cwd=str(tmp_path))
    await kernel.start()
    try:
        output, errored = await kernel.execute("rlm()", timeout=30.0)
    finally:
        await kernel.close()
    assert errored is True
    assert "is not callable" in output
    assert "rlm.spawn" in output


@pytest.mark.asyncio
async def test_rlm_spawn_available_but_host_gated(tmp_path):
    """`rlm` binds the spawn/collect surface; without a host bridge handler the
    request must fail with a typed error rather than AttributeError."""
    kernel = IpythonKernel("test-rlm-spawn", cwd=str(tmp_path))
    await kernel.start()
    try:
        output, errored = await kernel.execute(
            "import rlm\n"
            "print(sorted(a for a in ('spawn', 'collect', 'harness') if hasattr(rlm, a)))",
            timeout=30.0,
        )
    finally:
        await kernel.close()
    assert errored is False
    assert "collect" in output and "harness" in output and "spawn" in output


@pytest.mark.asyncio
async def test_bash_returns_handle_not_string(tmp_path):
    """Strict contract: ``bash()`` hands back a live handle immediately — never
    a blocking string — so a cell can start work and end its turn."""
    kernel = IpythonKernel("test-bash-handle", cwd=str(tmp_path))
    await kernel.start()
    try:
        output, errored = await kernel.execute(
            'h = bash("echo hi")\nprint(type(h).__name__, isinstance(h, str))', timeout=30.0
        )
    finally:
        await kernel.close()
    assert errored is False
    assert "BashHandle False" in output


@pytest.mark.asyncio
async def test_shell_sugar_runs_through_run_bash(tmp_path):
    """``!cmd`` line sugar must resolve to a blocking ``run_bash`` call whose
    output lands in the cell's stdout."""
    kernel = IpythonKernel("test-bang-sugar", cwd=str(tmp_path))
    await kernel.start()
    try:
        output, errored = await kernel.execute("!echo bang-sugar", timeout=30.0)
    finally:
        await kernel.close()
    assert errored is False
    assert "bang-sugar" in output


@pytest.mark.asyncio
async def test_python_skills_bind_when_host_supplies_inventory(tmp_path):
    from vtx.ai.agent.tools.ipython import _python_skill_inventory

    inventory = _python_skill_inventory(str(tmp_path))
    assert {entry["import_name"] for entry in inventory} >= {
        "agent_message",
        "agent_observe",
        "edit",
        "compact",
        "refine",
    }

    kernel = IpythonKernel("test-python-skills", cwd=str(tmp_path))
    await kernel.start()
    try:
        output, errored = await kernel.execute(
            "print(sorted(n for n in ('edit', 'agent_message', 'refine') if n in globals()))",
            timeout=30.0,
            context={"python_skills": inventory, "cwd": str(tmp_path)},
        )
    finally:
        await kernel.close()
    assert errored is False
    assert "'agent_message'" in output and "'edit'" in output and "'refine'" in output


@pytest.mark.asyncio
async def test_host_request_round_trips_through_manager(tmp_path):
    """kernel -> ``host_request`` frame -> manager bridge -> ``host_reply``.
    The ``type`` key must be injected last so it cannot be rerouted by a
    payload that already carries one."""
    seen: list[dict] = []

    async def dispatcher(
        payload: dict, *, tool_executor=None, session_id: str | None = None
    ) -> dict:
        seen.append(payload)
        if payload.get("type") == "demo.fail":
            return {"status": "error", "error": "demo refused"}
        return {"status": "ok", "result": {"echo": payload.get("n")}}

    kernel = IpythonKernel("test-host-bridge", cwd=str(tmp_path), host_dispatcher=dispatcher)
    kernel.set_session("sess-v3")
    await kernel.start()
    try:
        output, errored = await kernel.execute(
            "from vtx.ai.agent.rlm import host_request\n"
            "reply = await host_request('demo.ask', {'n': 7, 'type': 'spoofed'})\n"
            "print('GOT:', reply)",
            timeout=30.0,
        )
        failed, failed_errored = await kernel.execute(
            "from vtx.ai.agent.rlm import host_request\nawait host_request('demo.fail')",
            timeout=30.0,
        )
    finally:
        await kernel.close()

    assert errored is False, output
    assert "GOT: {'echo': 7}" in output, output
    # 'type' went last: the request type wins over the payload's spoofed key.
    assert seen[0]["type"] == "demo.ask"
    assert seen[0]["n"] == 7

    assert failed_errored is True
    assert "demo refused" in failed


@pytest.mark.asyncio
async def test_cell_error_reports_traceback(tmp_path):
    kernel = IpythonKernel("test-cell-error", cwd=str(tmp_path))
    await kernel.start()
    try:
        output, errored = await kernel.execute("raise ValueError('boom')", timeout=30.0)
    finally:
        await kernel.close()
    assert errored is True
    assert "ValueError" in output
    assert "boom" in output


@pytest.mark.asyncio
async def test_ipython_history_shorthands_rotate(tmp_path):
    """``_`` / ``__`` / ``___`` follow IPython's rules: ``_`` after the first
    result, ``__`` after the second, ``___`` after the third, each holding the
    result one slot further back."""
    kernel = IpythonKernel("test-history-shorthand", cwd=str(tmp_path))
    await kernel.start()
    try:
        for cell in ("10", "20", "30"):
            _out, err = await kernel.execute(cell, timeout=30.0)
            assert err is False
        output, errored = await kernel.execute(
            "print(_, __, ___)\nprint(sorted(Out))", timeout=30.0
        )
    finally:
        await kernel.close()
    assert errored is False, output
    lines = output.splitlines()
    assert lines[0] == "30 20 10"
    # ``Out`` is keyed by cell number, not by value.
    assert lines[1] == "[1, 2, 3]"


@pytest.mark.asyncio
async def test_state_survives_between_cells(tmp_path):
    """The persistent namespace is the whole point of the kernel: variables
    written in one cell must be readable in the next."""
    kernel = IpythonKernel("test-persistence", cwd=str(tmp_path))
    await kernel.start()
    try:
        _first, err1 = await kernel.execute("counter = 41", timeout=30.0)
        second, err2 = await kernel.execute("counter += 1\ncounter", timeout=30.0)
    finally:
        await kernel.close()
    assert err1 is False and err2 is False
    assert "42" in second
