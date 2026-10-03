import pytest

from vtx.mcp.content import split_content, to_tool_content
from vtx.protocol.types import ImageContent, TextContent


def test_passes_text_and_images_through_and_replaces_other_blocks():
    blocks = [
        {"type": "text", "text": "hello", "annotations": {"priority": 1}},
        {"type": "image", "data": "aW1n", "mimeType": "image/png", "_meta": {"x": 1}},
        {"type": "audio", "data": "YXVk", "mimeType": "audio/wav"},
        {"type": "resource_link", "uri": "file:///a.txt", "name": "a.txt"},
        {"type": "resource", "resource": {"uri": "file:///b.txt", "text": "inline"}},
        {
            "type": "resource",
            "resource": {"uri": "file:///c.png", "mimeType": "image/png", "blob": "Yw=="},
        },
        {"type": "resource", "resource": {"uri": "file:///d.bin", "blob": "ZA=="}},
    ]

    assert to_tool_content({"content": blocks}) == [
        TextContent(text="hello"),
        ImageContent(data="aW1n", mime_type="image/png"),
        TextContent(text="[audio audio/wav omitted]"),
        TextContent(text="a.txt: file:///a.txt"),
        TextContent(text="inline"),
        ImageContent(data="Yw==", mime_type="image/png"),
        TextContent(text="[binary resource file:///d.bin (unknown type) omitted]"),
    ]


def test_falls_back_to_structured_content_as_json():
    assert to_tool_content({"content": [], "structuredContent": {"n": 1}}) == [
        TextContent(text='{\n  "n": 1\n}')
    ]
    # Real text always wins; structured content is a fallback, not an addition.
    assert to_tool_content(
        {"content": [{"type": "text", "text": "n=1"}], "structuredContent": {"n": 1}}
    ) == [TextContent(text="n=1")]


def test_handles_a_result_with_neither():
    assert to_tool_content({"content": []}) == []


def test_ignores_non_dict_blocks():
    assert to_tool_content({"content": ["junk", None, {"type": "text", "text": "ok"}]}) == [
        TextContent(text="ok")
    ]


def test_split_content_flattens_to_the_toolresult_shape():
    text, images = split_content(
        [
            TextContent(text="first"),
            ImageContent(data="aW1n", mime_type="image/png"),
            TextContent(text="second"),
        ]
    )
    assert text == "first\nsecond"
    assert images == [ImageContent(data="aW1n", mime_type="image/png")]


def test_split_content_drops_empty_text_blocks():
    text, images = split_content([TextContent(text=""), TextContent(text="only")])
    assert text == "only"
    assert images == []


@pytest.mark.parametrize(
    "block,expected",
    [
        ({"type": "nonsense"}, "[unsupported MCP content nonsense]"),
        ({"type": "resource", "resource": "not-a-dict"}, "[unsupported MCP resource]"),
        ({"type": "audio"}, "[audio unknown type omitted]"),
    ],
)
def test_degrades_gracefully_on_malformed_blocks(block, expected):
    assert to_tool_content({"content": [block]}) == [TextContent(text=expected)]
