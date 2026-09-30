import asyncio
import base64
import json

import pytest

from vtx.core.types import ImageContent
from vtx.mcp.tool import (
    MCP_OUTPUT_MAX_BYTES,
    McpTool,
    McpToolCaller,
    convert_mcp_result,
    create_mcp_tool_name,
    enrich_result,
    truncate_middle,
)


def _tool(definition: dict, call=None, server: str = "fs", timeout_ms: int = 60_000) -> McpTool:
    async def default_call(_name, _args, _options):
        return {"content": [{"type": "text", "text": "ok"}]}

    return McpTool(
        server=server,
        definition=definition,
        name=create_mcp_tool_name(server, definition.get("name", "t")),
        caller=McpToolCaller(server_name=server, call=call or default_call),
        timeout_ms=timeout_ms,
    )


# ---- naming ---------------------------------------------------------------


def test_name_is_sanitized_and_namespaced():
    assert create_mcp_tool_name("my server", "read file") == "mcp__my_server__read_file"


def test_long_name_gets_a_hash_suffix():
    name = create_mcp_tool_name("s" * 40, "t" * 40)
    assert len(name) == 64
    assert name.endswith(name[-9:]) and "_" in name


def test_a_taken_name_gets_a_distinct_one():
    first = create_mcp_tool_name("fs", "read", lambda n: n == "mcp__fs__read")
    # Short but taken: the hash suffix disambiguates without padding to 64.
    assert first != "mcp__fs__read"
    assert first.startswith("mcp__fs__read_")
    assert len(first) <= 64


def test_sanitization_collision_inside_one_server_is_resolved():
    # Both sanitize to mcp__s__a_b; the second must not shadow the first.
    taken: set[str] = set()
    names = []
    for _ in range(2):
        name = create_mcp_tool_name("s", "a.b" if not names else "a_b", taken.__contains__)
        taken.add(name)
        names.append(name)
    assert names[0] == "mcp__s__a_b"
    assert names[1] != names[0]


def test_name_never_exceeds_the_provider_limit():
    name = create_mcp_tool_name("x" * 100, "y" * 100)
    assert len(name) <= 64
    assert all(c.isalnum() or c in "_-" for c in name)


# ---- annotations ----------------------------------------------------------


def test_read_only_hint_makes_the_tool_non_mutating():
    tool = _tool({"name": "t", "inputSchema": {}, "annotations": {"readOnlyHint": True}})
    assert tool.mutating is False


def test_a_tool_without_annotations_is_mutating():
    assert _tool({"name": "t", "inputSchema": {}}).mutating is True


def test_destructive_hint_overrides_read_only():
    tool = _tool(
        {
            "name": "t",
            "inputSchema": {},
            "annotations": {"readOnlyHint": True, "destructiveHint": True},
        }
    )
    assert tool.mutating is True


# ---- params ---------------------------------------------------------------


def test_params_model_is_built_from_the_input_schema():
    tool = _tool(
        {
            "name": "t",
            "inputSchema": {
                "type": "object",
                "properties": {"path": {"type": "string"}, "n": {"type": "integer"}},
                "required": ["path"],
            },
        }
    )
    params = tool.params(path="/tmp", n=3)
    assert params.path == "/tmp"
    assert params.n == 3


def test_an_omitted_type_still_produces_a_usable_model():
    tool = _tool({"name": "t", "inputSchema": {"properties": {"q": {"type": "string"}}}})
    assert tool.params(q="hello").q == "hello"


def test_an_empty_schema_gets_the_optional_input_field():
    tool = _tool({"name": "t", "inputSchema": {"type": "object"}})
    assert tool.params().input is None


def test_a_non_object_schema_is_skipped_not_fatal():
    with pytest.raises(ValueError, match="must be 'object'"):
        _tool({"name": "t", "inputSchema": {"type": "string"}})


# ---- execution ------------------------------------------------------------


@pytest.mark.asyncio
async def test_calls_the_server_with_the_dumped_params():
    seen: dict = {}

    async def call(name, args, options):
        seen.update(name=name, args=args, options=options)
        return {"content": [{"type": "text", "text": "done"}]}

    tool = _tool(
        {
            "name": "read",
            "inputSchema": {
                "type": "object",
                "properties": {"path": {"type": "string"}, "opt": {"type": "string"}},
                "required": ["path"],
            },
        },
        call=call,
    )
    result = await tool.execute(tool.params(path="/a", opt=None))

    assert seen["name"] == "read"
    # exclude_none drops the unset optional rather than sending an explicit null.
    assert seen["args"] == {"path": "/a"}
    assert result.success is True
    assert result.result == "done"


@pytest.mark.asyncio
async def test_passes_the_cancel_event_and_timeout_through():
    captured: list = []

    async def call(_name, _args, options):
        captured.append(options)
        return {"content": []}

    tool = _tool({"name": "t", "inputSchema": {}}, call=call, timeout_ms=1234)
    cancel = asyncio.Event()
    await tool.execute(tool.params(), cancel_event=cancel)

    assert captured[0].cancel_event is cancel
    assert captured[0].timeout_ms == 1234


@pytest.mark.asyncio
async def test_a_transport_failure_becomes_an_error_result_not_an_exception():
    async def call(_name, _args, _options):
        raise RuntimeError("server went away")

    tool = _tool({"name": "t", "inputSchema": {}}, call=call)
    result = await tool.execute(tool.params())

    assert result.success is False
    assert "fs/t failed" in result.result
    assert "server went away" in result.result


@pytest.mark.asyncio
async def test_progress_is_forwarded_to_on_output():
    async def call(_name, _args, options):
        options.on_progress({"progress": 2, "total": 4, "message": "halfway"})
        return {"content": [{"type": "text", "text": "done"}]}

    tool = _tool({"name": "t", "inputSchema": {}}, call=call)
    updates: list[str] = []
    await tool.execute(tool.params(), on_output=updates.append)
    assert updates == ["halfway"]


@pytest.mark.asyncio
async def test_no_progress_callback_means_no_on_progress_listener():
    captured: list = []

    async def call(_name, _args, options):
        captured.append(options.on_progress)
        return {"content": []}

    tool = _tool({"name": "t", "inputSchema": {}}, call=call)
    await tool.execute(tool.params())
    assert captured == [None]


@pytest.mark.asyncio
async def test_cancellation_propagates_rather_than_becoming_an_error_result():
    async def call(_name, _args, _options):
        raise asyncio.CancelledError

    tool = _tool({"name": "t", "inputSchema": {}}, call=call)
    with pytest.raises(asyncio.CancelledError):
        await tool.execute(tool.params())


@pytest.mark.asyncio
async def test_is_error_result_marks_the_tool_result_failed():
    async def call(_name, _args, _options):
        return {"content": [{"type": "text", "text": "nope"}], "isError": True}

    tool = _tool({"name": "t", "inputSchema": {}}, call=call)
    result = await tool.execute(tool.params())

    assert result.success is False
    assert result.ui_summary == "[red]error[/red]"


def test_format_call_shows_the_server_and_tool():
    tool = _tool(
        {
            "name": "read",
            "inputSchema": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        }
    )
    assert tool.format_call(tool.params(path="/a")) == "fs/read path=/a"
    assert tool.format_call(tool.params(path="x" * 200)).endswith("...")


# ---- result conversion ----------------------------------------------------


def test_converts_text_and_images():
    result = convert_mcp_result(
        {
            "content": [
                {"type": "text", "text": "a"},
                {"type": "image", "data": "aW1n", "mimeType": "image/png"},
                {"type": "text", "text": "b"},
            ]
        }
    )
    assert result.result == "a\nb"
    assert result.images == [ImageContent(data="aW1n", mime_type="image/png")]


def test_structured_content_is_used_when_there_are_no_blocks():
    result = convert_mcp_result({"content": [], "structuredContent": {"ok": True}})
    assert json.loads(result.result) == {"ok": True}


def test_large_output_is_cut_and_the_full_text_is_saved():
    big = "x" * (MCP_OUTPUT_MAX_BYTES * 2)
    result = convert_mcp_result({"content": [{"type": "text", "text": big}]})

    assert result.success is True
    assert "truncated output" in result.result
    assert "elided" in result.result
    # The model is pointed at a file rather than left guessing what it lost.
    assert "Full output:" in result.result
    path = result.result.split("Full output: ")[1].split(" ")[0]
    from pathlib import Path

    saved = Path(path)
    assert saved.exists()
    assert saved.stat().st_size == len(big)
    # Mode 0600: the payload can be source code or credentials.
    assert oct(saved.stat().st_mode)[-3:] == "600"
    saved.unlink()


def test_truncate_middle_keeps_both_ends():
    text = "START" + ("." * 100) + "END"
    out, truncated = truncate_middle(text, 40)
    assert truncated is True
    assert out.startswith("START")
    assert out.endswith("END")
    assert "elided" in out


def test_truncate_middle_leaves_short_text_alone():
    assert truncate_middle("short", 100) == ("short", False)


def test_audio_becomes_a_placeholder():
    result = convert_mcp_result(
        {"content": [{"type": "audio", "data": "x", "mimeType": "audio/wav"}]}
    )
    assert result.result == "[audio audio/wav omitted]"
    assert result.images is None


# ---- resource enrichment --------------------------------------------------


def test_text_typed_blob_resource_is_inlined():
    blob = base64.b64encode(b'{"a": 1}').decode()
    result = convert_mcp_result(
        enrich_result(
            {
                "content": [
                    {
                        "type": "resource",
                        "resource": {
                            "uri": "file:///a.json",
                            "mimeType": "application/json",
                            "blob": blob,
                        },
                    }
                ]
            }
        )
    )
    assert result.result == '{"a": 1}'


def test_binary_resource_is_written_to_a_file():
    blob = base64.b64encode(b"\x00\x01\x02binary").decode()
    result = convert_mcp_result(
        enrich_result(
            {
                "content": [
                    {
                        "type": "resource",
                        "resource": {
                            "uri": "file:///data.bin",
                            "mimeType": "application/octet-stream",
                            "blob": blob,
                        },
                    }
                ]
            }
        )
    )
    assert "saved to" in result.result
    from pathlib import Path

    path = Path(result.result.split("saved to ")[1].rstrip("]"))
    assert path.read_bytes() == b"\x00\x01\x02binary"
    path.unlink()


def test_binary_resource_keeps_a_useful_extension():
    blob = base64.b64encode(b"x").decode()
    result = convert_mcp_result(
        enrich_result(
            {
                "content": [
                    {
                        "type": "resource",
                        "resource": {
                            "uri": "file:///report.pdf",
                            "mimeType": "application/pdf",
                            "blob": blob,
                        },
                    }
                ]
            }
        )
    )
    from pathlib import Path

    path = Path(result.result.split("saved to ")[1].rstrip("]"))
    assert path.suffix == ".pdf"
    path.unlink()


def test_resource_link_names_the_thing():
    result = convert_mcp_result(
        enrich_result(
            {
                "content": [
                    {
                        "type": "resource_link",
                        "uri": "file:///a.txt",
                        "name": "a.txt",
                        "mimeType": "text/plain",
                        "size": 12,
                    }
                ]
            }
        )
    )
    assert result.result == '[Resource file:///a.txt "a.txt" (text/plain, 12 B)]'


def test_image_resource_passes_through_as_an_image():
    blob = base64.b64encode(b"\x89PNG").decode()
    result = convert_mcp_result(
        enrich_result(
            {
                "content": [
                    {
                        "type": "resource",
                        "resource": {
                            "uri": "file:///a.png",
                            "mimeType": "image/png",
                            "blob": blob,
                        },
                    }
                ]
            }
        )
    )
    assert result.images == [ImageContent(data=blob, mime_type="image/png")]
    assert result.result is None


def test_enrich_is_a_no_op_when_nothing_changed():
    original = {"content": [{"type": "text", "text": "plain"}]}
    assert enrich_result(original) is original
