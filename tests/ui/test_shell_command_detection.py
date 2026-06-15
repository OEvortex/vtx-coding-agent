from unittest.mock import AsyncMock, Mock, patch

import pytest

from vtx.tools.bash import BashTool
from vtx.ui.app import Vtx


def test_handle_shell_command_execution():
    """Test that _handle_shell_command properly handles shell command execution"""
    # Create a mock app instance
    app = Mock()
    app._is_running = False
    app.query_one = Mock()

    # Mock the chat log
    mock_chat = Mock()
    app.query_one.return_value = mock_chat
    app.run_worker = Mock()

    # Test single ! command
    Vtx._handle_shell_command(app, "!ls -la", "!ls -la")

    # Verify chat message was added
    mock_chat.add_user_message.assert_called_once_with("!ls -la")

    # Verify app state was set
    assert app._is_running is True

    # Verify run_worker was called with correct arguments
    app.run_worker.assert_called_once()
    call_args = app.run_worker.call_args
    # Check that the first argument is a coroutine function (the method)
    assert callable(call_args[0][0])
    # Check that exclusive=True was passed
    assert call_args[1]["exclusive"] is True


def test_handle_shell_command_history_mode():
    """Test that _handle_shell_command properly handles history mode (!!)"""
    # Create a mock app instance
    app = Mock()
    app._is_running = False
    app.query_one = Mock()

    # Mock the chat log
    mock_chat = Mock()
    app.query_one.return_value = mock_chat
    app.run_worker = Mock()

    # Test double !! command
    Vtx._handle_shell_command(app, "!!git status", "!!git status")

    # Verify chat message was added
    mock_chat.add_user_message.assert_called_once_with("!!git status")

    # Verify app state was set
    assert app._is_running is True

    # Verify run_worker was called with correct arguments for history mode
    app.run_worker.assert_called_once()


def test_handle_shell_command_when_running():
    """Test that _handle_shell_command returns early when app is already running"""
    # Create a mock app instance that's already running
    app = Mock()
    app._is_running = True
    app.query_one = Mock()

    # Mock the chat log
    mock_chat = Mock()
    app.query_one.return_value = mock_chat
    app.run_worker = Mock()

    # Call the handler
    Vtx._handle_shell_command(app, "!ls -la", "!ls -la")

    # Verify no chat message was added (should return early)
    mock_chat.add_user_message.assert_not_called()

    # Verify app state wasn't changed
    assert app._is_running is True

    # Verify run_worker was not called
    app.run_worker.assert_not_called()


@pytest.mark.asyncio
async def test_execute_shell_command_basic():
    """Test that _execute_shell_command properly executes basic shell commands"""
    # Create a mock app instance
    app = Mock()
    app._is_running = False
    app._shell_tool_counter = 0

    # Mock the chat log and status line
    mock_chat = Mock()
    mock_status = Mock()
    app.query_one.side_effect = lambda id, _: mock_chat if id == "#chat-log" else mock_status

    # Mock bash tool execution
    mock_result = Mock()
    mock_result.success = True
    mock_result.result = "test output"
    mock_result.ui_details = "collapsed output"
    mock_result.ui_details_full = "expanded output"
    mock_result.ui_summary = "Command completed"

    with patch.object(
        BashTool, "execute", new_callable=AsyncMock, return_value=mock_result
    ) as execute:
        # Call the method
        await Vtx._execute_shell_command(app, "ls -la", False)

    # Verify status was set to running and then idle
    mock_status.set_status.assert_any_call("running")
    mock_status.set_status.assert_called_with("idle")

    # Verify chat methods were called
    mock_chat.start_tool.assert_called_once_with("bash", "shell-1", "$ ls -la", icon="$")

    # Verify tool result was routed through ChatLog
    mock_chat.set_tool_result.assert_called_once_with(
        "shell-1",
        "Command completed",
        "collapsed output",
        True,
        markup=True,
        ui_details_full="expanded output",
    )
    assert execute.call_args.kwargs["inline_output"] is False

    # Verify app state was reset
    assert app._is_running is False


@pytest.mark.asyncio
async def test_execute_shell_command_with_llm():
    """Test that _execute_shell_command sends output to LLM when using !!"""
    # Create a mock app instance
    app = Mock()
    app._is_running = False
    app._shell_tool_counter = 0
    app._interrupt_requested = False

    # Mock the chat log and status line
    mock_chat = Mock()
    mock_status = Mock()
    app.query_one.side_effect = lambda id, _: mock_chat if id == "#chat-log" else mock_status

    # Mock bash tool execution
    mock_result = Mock()
    mock_result.success = True
    mock_result.result = "git status output"
    mock_result.ui_details = "collapsed output"
    mock_result.ui_details_full = "expanded output"
    mock_result.ui_summary = "Command completed"

    # Mock the _run_agent method
    app._run_agent = AsyncMock()

    with patch.object(
        BashTool, "execute", new_callable=AsyncMock, return_value=mock_result
    ) as execute:
        # Call the method with send_to_llm=True
        await Vtx._execute_shell_command(app, "git status", True)

    # Verify _run_agent was called with the correct prompt
    app._run_agent.assert_called_once()
    call_args = app._run_agent.call_args[0][0]
    assert "Shell command output:" in call_args
    assert "git status output" in call_args
    assert "What would you like me to do with this?" in call_args
    assert app._interrupt_requested is False
    assert execute.call_args.kwargs["inline_output"] is False
    mock_chat.set_tool_result.assert_called_once_with(
        "shell-1",
        "Command completed",
        "collapsed output",
        True,
        markup=True,
        ui_details_full="expanded output",
    )


@pytest.mark.asyncio
async def test_execute_shell_command_logs_to_session():
    """Shell commands and their output are persisted so resume and /export include them"""
    app = Mock()
    app._is_running = False
    app._shell_tool_counter = 0

    mock_chat = Mock()
    mock_status = Mock()
    app.query_one.side_effect = lambda id, _: mock_chat if id == "#chat-log" else mock_status

    mock_session = Mock()
    app._runtime.session = mock_session

    mock_result = Mock()
    mock_result.success = False
    mock_result.result = "[stderr]\ngrep: bad option"
    mock_result.ui_details = None
    mock_result.ui_details_full = None
    mock_result.ui_summary = "[red]Exit code 2[/red]"

    with patch.object(BashTool, "execute", new_callable=AsyncMock, return_value=mock_result):
        await Vtx._execute_shell_command(app, "grep -r foo .", False)

    mock_session.append_custom_message.assert_called_once_with(
        "shell_command",
        "!grep -r foo .",
        details={
            "command": "grep -r foo .",
            "output": "[stderr]\ngrep: bad option",
            "success": False,
        },
    )


@pytest.mark.asyncio
async def test_execute_shell_command_interruption_resets_state_and_skips_llm():
    app = Mock()
    app._is_running = False
    app._interrupt_requested = True

    mock_chat = Mock()
    mock_status = Mock()
    app.query_one.side_effect = lambda id, _: mock_chat if id == "#chat-log" else mock_status
    app._run_agent = AsyncMock()

    mock_result = Mock()
    mock_result.success = False
    mock_result.result = "Command aborted"
    mock_result.ui_details = None
    mock_result.ui_details_full = None
    mock_result.ui_summary = "[yellow]Command aborted by user[/yellow]"

    async def mock_execute(*args, cancel_event=None, **kwargs):
        assert cancel_event is not None
        cancel_event.set()
        return mock_result

    with patch.object(BashTool, "execute", new_callable=AsyncMock, side_effect=mock_execute):
        await Vtx._execute_shell_command(app, "sleep 10", True)

    app._run_agent.assert_not_called()
    assert app._is_running is False
    assert app._interrupt_requested is False
    assert app._cancel_event is None
