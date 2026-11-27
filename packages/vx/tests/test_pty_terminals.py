"""Persistent terminals: one shell, state that survives between calls."""

import asyncio

import pytest
from vx.terminals import SENTINEL_RE, TERMINALS, Terminal, TerminalRegistry


class TestSentinel:
    def test_a_sentinel_splits_output_and_code(self):
        terminal = Terminal(id="t1")
        terminal._buf = "hello\n\x1e VX-EXIT:3 \x1e"
        parsed = terminal._take()
        assert parsed is not None
        assert parsed.text == "hello"
        assert parsed.exit_code == 3
        assert terminal._buf == ""

    def test_no_sentinel_means_still_running(self):
        terminal = Terminal(id="t1")
        terminal._buf = "partial line"
        assert terminal._take() is None
        assert terminal._take_buffer() == "partial line"

    def test_the_sentinel_pattern_matches_what_the_shell_prints(self):
        assert SENTINEL_RE.search("out\x1e VX-EXIT:0 \x1e") is not None
        assert SENTINEL_RE.search("out\x1e VX-EXIT:137 \x1e").group(1) == "137"


@pytest.mark.asyncio
class TestReuse:
    async def test_a_cd_survives_into_the_next_command(self):
        registry = TerminalRegistry()
        terminal = await registry.get_or_create("t1")
        if terminal is None:
            pytest.skip("no pty on this platform")
        try:
            first = await terminal.run("cd /tmp && pwd", timeout=20)
            assert first.exit_code == 0
            assert "/tmp" in first.text
            second = await terminal.run("pwd", timeout=20)
            assert second.exit_code == 0
            assert second.text.strip().endswith("/tmp")
        finally:
            await registry.close_all()

    async def test_an_export_survives_into_the_next_command(self):
        registry = TerminalRegistry()
        terminal = await registry.get_or_create("t1")
        if terminal is None:
            pytest.skip("no pty on this platform")
        try:
            await terminal.run("export VX_MARK=42", timeout=20)
            echo = await terminal.run('printf %s "$VX_MARK"', timeout=20)
            assert "42" in echo.text
        finally:
            await registry.close_all()

    async def test_the_exit_code_of_a_failure_comes_back(self):
        registry = TerminalRegistry()
        terminal = await registry.get_or_create("t1")
        if terminal is None:
            pytest.skip("no pty on this platform")
        try:
            out = await terminal.run("exit 7", timeout=20)
            # The shell itself exits: the terminal is gone, and the reader sees EOF.
            assert out.exit_code == 7 or terminal.status_word() == "CLOSED"
        finally:
            await registry.close_all()

    async def test_input_reaches_a_command_that_is_waiting(self):
        registry = TerminalRegistry()
        terminal = await registry.get_or_create("t1")
        if terminal is None:
            pytest.skip("no pty on this platform")
        try:
            task = asyncio.create_task(terminal.run("read answer; echo got:$answer", timeout=20))
            await asyncio.sleep(0.3)
            assert terminal.write("yes\n")
            out = await asyncio.wait_for(task, 10)
            assert "got:yes" in out.text
        finally:
            await registry.close_all()

    async def test_the_global_registry_names_terminals_the_model_chose(self):
        TERMINALS.terminals.clear()
        terminal = await TERMINALS.get_or_create("t9")
        if terminal is None:
            pytest.skip("no pty on this platform")
        try:
            assert TERMINALS.get("t9") is terminal
            assert await TERMINALS.close("t9") is True
            assert TERMINALS.get("t9") is None
        finally:
            await TERMINALS.close_all()
