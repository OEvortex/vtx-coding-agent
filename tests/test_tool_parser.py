"""Tests for the text-embedded tool call parser."""

from __future__ import annotations

import json

from vtx.llm.tool_parser import (
    extract_text_and_tool_calls,
    extract_tool_calls_from_text,
    has_text_tool_calls,
    normalize_tool_calls,
)


def test_has_text_tool_calls() -> None:
    assert has_text_tool_calls("Hello <function=run_bash>cmd</function>") is True
    assert has_text_tool_calls('<function name="run_bash">cmd</function>') is True
    assert has_text_tool_calls("Just plain text") is False
    assert has_text_tool_calls("") is False


def test_extract_tool_calls_from_text_attributes() -> None:
    content = '<function name="run_bash" command="echo hello" />'
    calls = extract_tool_calls_from_text(content)
    assert len(calls) == 1
    assert calls[0]["name"] == "run_bash"
    assert calls[0]["arguments"] == {"command": "echo hello"}


def test_extract_tool_calls_from_text_parameters() -> None:
    content = """
    <function name="write_file">
        <parameter name="path">/test/file.txt</parameter>
        <parameter name="content">hello world</parameter>
    </function>
    """
    calls = extract_tool_calls_from_text(content)
    assert len(calls) == 1
    assert calls[0]["name"] == "write_file"
    assert calls[0]["arguments"] == {"path": "/test/file.txt", "content": "hello world"}


def test_extract_text_and_tool_calls() -> None:
    content = """
    I will write a file for you.
    <function name="write_file">
        <parameter name="path">/test/file.txt</parameter>
        <parameter name="content">hello world</parameter>
    </function>
    Let me know if that worked.
    """
    cleaned, calls = extract_text_and_tool_calls(content)
    assert "I will write a file for you." in cleaned
    assert "Let me know if that worked." in cleaned
    assert "<function" not in cleaned
    assert "</function>" not in cleaned

    assert len(calls) == 1
    assert calls[0]["name"] == "write_file"
    assert calls[0]["arguments"] == {"path": "/test/file.txt", "content": "hello world"}


def test_normalize_tool_calls() -> None:
    calls = [{"name": "foo", "arguments": {"x": 1}}]
    normalized = normalize_tool_calls(calls)
    assert len(normalized) == 1
    assert normalized[0]["name"] == "foo"
    assert json.loads(normalized[0]["arguments"]) == {"x": 1}
