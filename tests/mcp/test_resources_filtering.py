"""What the resource listings leave out, and how entries are shaped.

Pure functions with nothing to await, kept out of ``test_resources.py`` so that
module can mark every test asyncio without marking these two wrongly.
"""

from __future__ import annotations

import pytest

from vtx.mcp.resources import is_mcp_app_resource, listed


@pytest.mark.parametrize(
    "item,expected",
    [
        ({"uri": "ui://panel"}, True),
        ({"uriTemplate": "ui://panel/{id}"}, True),
        ({"uri": "file://a", "mimeType": "text/html; profile=mcp-app"}, True),
        ({"uri": "file://a", "mimeType": 'text/html;profile="mcp-app"'}, True),
        ({"uri": "file://a", "mimeType": "text/html"}, False),
        ({"uri": "file://a"}, False),
        ({}, False),
    ],
)
def test_mcp_app_resources_are_recognized(item: dict, expected: bool) -> None:
    assert is_mcp_app_resource(item) is expected


def test_listing_drops_meta_and_icons() -> None:
    """Host decoration is not something a model can act on."""
    assert listed("srv", {"uri": "u", "_meta": {"a": 1}, "icons": [1, 2], "name": "n"}) == {
        "server": "srv",
        "uri": "u",
        "name": "n",
    }
