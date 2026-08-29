import pytest

from vtx.core.events import ToolOutputDeltaEvent
from vtx.openjarvis.tools.shell import ExecTool
from vtx.tui.blocks import ToolBlock


@pytest.mark.asyncio
async def test_exec_tool_realtime_streaming():
    tool = ExecTool()
    streamed_chunks: list[str] = []

    def on_chunk(chunk: str):
        streamed_chunks.append(chunk)

    result = await tool.execute(command="printf 'line1\\nline2\\nline3\\n'", on_output=on_chunk)

    assert "line1" in result
    assert "line2" in result
    assert "line3" in result
    assert len(streamed_chunks) >= 1
    joined = "".join(streamed_chunks)
    assert "line1" in joined
    assert "line2" in joined
    assert "line3" in joined


@pytest.mark.asyncio
async def test_tool_block_live_output_streaming():
    block = ToolBlock(name="exec", call_msg="test command")
    assert block._live_output == ""

    block.append_live_output("progress 1...\n")
    assert block._live_output == "progress 1...\n"

    block.append_live_output("progress 2...\n")
    assert block._live_output == "progress 1...\nprogress 2...\n"

    block.set_result(ui_summary="Done", ui_details="Result line", success=True)
    assert block._live_output == ""
    assert block._success is True


def test_tool_output_delta_event():
    evt = ToolOutputDeltaEvent(tool_call_id="call_123", tool_name="exec", delta="hello world\n")
    assert evt.type == "tool_output_delta"
    assert evt.tool_call_id == "call_123"
    assert evt.tool_name == "exec"
    assert evt.delta == "hello world\n"
