"""Tool call parser for handling text-embedded tool calls.

This module parses tool calls embedded in text content, supporting formats like:
- <function=name>...</function>
- <function name="name">...</function>
- With nested <parameter name="x">value</parameter> tags
"""

from __future__ import annotations

import json
import re
from typing import Any


def extract_tool_calls_from_text(content: str) -> list[dict[str, Any]]:
    """Extract tool calls embedded in text content.

    Supports formats:
    - <function=name>...</function>
    - <function name="name">...</function>
    - With nested <parameter name="x">value</parameter> tags
    - Self-closing: <function=name/> or <function name="name"/>

    Args:
        content: Text content that may contain embedded tool calls

    Returns:
        List of dicts: [{"name": "tool_name", "arguments": {...}}]
    """
    tool_calls = []

    # Match both self-closing and open/close tag pairs
    # Pattern for: <function=name ...> or <function name="name" ...>
    # Then either /> for self-closing or >...</function> for open/close

    # First, find all function tags (both self-closing and with content)
    # Match: <function=name ...> or <function name="name" ...>
    function_start_pattern = (
        r"<function(?:\s+name=[\"\']?([^\"\'\s/>]+)[\"\']?|=[\"\']?([^\"\'\s/>]+)[\"\']?)([^>]*)"
    )

    for match in re.finditer(function_start_pattern, content):
        name1 = match.group(1)  # function name="xxx" format
        name2 = match.group(2)  # function=xxx format
        attrs = match.group(3)  # Additional attributes

        tool_name = name1 or name2
        if not tool_name:
            continue

        # Find the full function tag (self-closing or with content)
        start_pos = match.start()

        # Check if self-closing
        if "/>" in content[start_pos : start_pos + 200]:  # Look ahead for />
            # Self-closing tag
            arguments = _parse_function_attributes(attrs)
            tool_calls.append({"name": tool_name, "arguments": arguments})
        else:
            # Find matching </function>
            end_tag = "</function>"
            end_pos = content.find(end_tag, start_pos)
            if end_pos == -1:
                continue

            # Extract body (content between > and </function>)
            tag_end = content.find(">", start_pos)
            if tag_end == -1:
                continue

            body = content[tag_end + 1 : end_pos]

            arguments = _parse_function_attributes(attrs)

            # Parse parameters from body
            if body:
                param_pattern = (
                    r"<parameter\s+name=[\"\']?([^\"\'\s/>]+)[\"\']?[^>]*>(.*?)</parameter>"
                )
                for param_match in re.finditer(param_pattern, body, re.DOTALL):
                    param_name = param_match.group(1)
                    param_value = param_match.group(2)
                    arguments[param_name] = param_value

            tool_calls.append({"name": tool_name, "arguments": arguments})

    return tool_calls


def _parse_function_attributes(attrs: str) -> dict[str, Any]:
    """Parse attributes from function tag."""
    arguments = {}
    if not attrs:
        return arguments

    attr_pattern = r'(\w+)=(?:"([^"]*)"|\'([^\']*)\'|([^\s/>]+))'
    for attr_match in re.finditer(attr_pattern, attrs):
        key = attr_match.group(1)
        value = attr_match.group(2) or attr_match.group(3) or attr_match.group(4) or ""
        if key not in ("name", "function"):
            arguments[key] = value

    return arguments


def has_text_tool_calls(content: str) -> bool:
    """Check if content contains text-embedded tool calls.

    Args:
        content: Text content to check

    Returns:
        True if content contains <function...> tags
    """
    if not content:
        return False
    return bool(re.search(r"<function[\s=]", content))


def normalize_tool_calls(tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize tool calls to format compatible with ToolRegistry.

    Converts arguments dict to JSON string for compatibility with
    the existing tool execution pipeline.

    Args:
        tool_calls: List of tool call dicts with "name" and "arguments" keys

    Returns:
        Normalized list with arguments as JSON strings
    """
    normalized = []
    for tc in tool_calls:
        normalized.append(
            {"name": tc.get("name", ""), "arguments": json.dumps(tc.get("arguments", {}))}
        )
    return normalized


def extract_text_and_tool_calls(content: str) -> tuple[str, list[dict[str, Any]]]:
    """Extract both text content and tool calls from a mixed response.

    Args:
        content: Text that may contain embedded tool calls

    Returns:
        Tuple of (cleaned_text, tool_calls)
        - cleaned_text: Text with tool call tags removed
        - tool_calls: List of extracted tool calls
    """
    if not has_text_tool_calls(content):
        return content, []

    tool_calls = extract_tool_calls_from_text(content)

    # Remove function tags from text (both self-closing and with content)
    cleaned = re.sub(r"<function[^>]*(?:/>|>.*?</function>)", "", content, flags=re.DOTALL)
    cleaned = re.sub(r"\n\s*\n", "\n\n", cleaned).strip()  # Clean up extra newlines

    return cleaned, tool_calls
