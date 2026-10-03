"""Convert an MCP tool result into the content blocks VTX sends to a model.

Text and images pass through. Embedded text resources become text and embedded
image resources become images. Everything else -- audio, resource links,
binary resources -- becomes a short text placeholder, because no provider in
:mod:`vtx.ai.providers` accepts those shapes and a raw base64 blob would blow
the context window.

A result with no content blocks but a ``structuredContent`` becomes its JSON:
servers should mirror structured results as text, but not all of them do.
"""

from __future__ import annotations

import json
from typing import Any

from vtx.protocol.types import ImageContent, TextContent

from .types import CallToolResult, ContentBlock

LlmContent = TextContent | ImageContent


def _block_to_content(block: ContentBlock) -> LlmContent:
    block_type = block.get("type")

    if block_type == "text":
        return TextContent(text=block.get("text") or "")

    if block_type == "image":
        return ImageContent(data=block.get("data") or "", mime_type=block.get("mimeType") or "")

    if block_type == "audio":
        return TextContent(text=f"[audio {block.get('mimeType') or 'unknown type'} omitted]")

    if block_type == "resource_link":
        return TextContent(text=f"{block.get('name') or block.get('uri')}: {block.get('uri')}")

    if block_type == "resource":
        return _resource_to_content(block.get("resource"))

    return TextContent(text=f"[unsupported MCP content {block_type}]")


def _resource_to_content(resource: Any) -> LlmContent:
    if not isinstance(resource, dict):
        return TextContent(text="[unsupported MCP resource]")
    if isinstance(resource.get("text"), str):
        return TextContent(text=resource["text"])
    mime_type = resource.get("mimeType")
    if isinstance(mime_type, str) and mime_type.startswith("image/"):
        return ImageContent(data=resource.get("blob") or "", mime_type=mime_type)
    return TextContent(
        text=f"[binary resource {resource.get('uri')} ({mime_type or 'unknown type'}) omitted]"
    )


def to_tool_content(result: CallToolResult) -> list[LlmContent]:
    """Model-facing content for one ``tools/call`` result."""
    blocks = result.get("content") or []
    content = [_block_to_content(block) for block in blocks if isinstance(block, dict)]
    if not content and result.get("structuredContent") is not None:
        content.append(TextContent(text=json.dumps(result["structuredContent"], indent=2)))
    return content


def split_content(content: list[LlmContent]) -> tuple[str, list[ImageContent]]:
    """Flatten content into the ``(text, images)`` shape :class:`ToolResult` takes.

    ``ToolResult`` has one text field and a list of images, so multiple text
    blocks join with newlines. The caller decides the final ordering of the
    flattened text.
    """
    texts: list[str] = []
    images: list[ImageContent] = []
    for block in content:
        if isinstance(block, TextContent):
            if block.text:
                texts.append(block.text)
        else:
            images.append(block)
    return "\n".join(texts), images


__all__ = ["LlmContent", "split_content", "to_tool_content"]
