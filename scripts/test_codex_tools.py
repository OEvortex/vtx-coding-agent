#!/usr/bin/env python3
"""Test Codex provider with tools to verify the 404 fix."""

from __future__ import annotations

import asyncio
import sys

from vtx.ai import ProviderConfig, get_valid_codex_token_sync
from vtx.ai.oauth.codex import is_codex_logged_in
from vtx.ai.providers.openai_responses_sdk import OpenAIResponsesSDKProvider
from vtx.core.types import ToolDefinition, UserMessage

MODEL = "gpt-5.6-luna"
TEST_MESSAGE = "What is the capital of France? Use the lookup tool."


async def main() -> None:
    print("[auth] Checking Codex credentials...")
    if not is_codex_logged_in():
        print("[auth] No Codex credentials found. Run scripts/test_codex_luna.py first.")
        sys.exit(1)
    token = get_valid_codex_token_sync()
    if not token:
        print("[auth] No Codex token available. Run scripts/test_codex_luna.py first.")
        sys.exit(1)
    print(f"[auth] Token: {token[:10]}...")

    config = ProviderConfig(
        model=MODEL,
        provider="codex",
        base_url="https://chatgpt.com/backend-api/codex",
        max_tokens=None,
    )
    provider = OpenAIResponsesSDKProvider(config)

    tools = [
        ToolDefinition(
            name="lookup",
            description="Look up a fact",
            parameters={
                "type": "object",
                "properties": {"query": {"type": "string", "description": "The query to look up"}},
                "required": ["query"],
            },
        )
    ]

    print(f"\n[api] Testing Codex provider with tools (model={MODEL})...")
    stream = await provider.stream(messages=[UserMessage(content=TEST_MESSAGE)], tools=tools)

    content_parts = []
    tool_calls = []
    finish_reason = "stop"

    async for part in stream:
        from vtx.core.types import StreamDone, StreamError, TextPart, ToolCallDelta, ToolCallStart

        if isinstance(part, StreamError):
            print(f"\n[api] STREAM ERROR: {part.error}")
            sys.exit(1)
        if isinstance(part, TextPart):
            content_parts.append(part.text)
            print(f"  [text] {part.text!r}", end="", flush=True)
        elif isinstance(part, ToolCallStart):
            tool_calls.append({"id": part.id, "name": part.name, "arguments": ""})
            print(f"\n  [tool] {part.name}(id={part.id})")
        elif isinstance(part, ToolCallDelta):
            if tool_calls:
                tool_calls[-1]["arguments"] += part.arguments_delta or ""
                print(f"    delta: {(part.arguments_delta or '')!r}", end="", flush=True)
        elif isinstance(part, StreamDone):
            finish_reason = part.stop_reason

    content = "".join(content_parts) if content_parts else None
    print("\n\n[api] Response received:")
    print(f"       content: {content!r}")
    print(f"       tool_calls: {tool_calls}")
    print(f"       finish_reason: {finish_reason}")

    if content or tool_calls:
        print("\n[result] SUCCESS: Codex provider handled tools correctly.")
    else:
        print("\n[result] WARNING: Empty response.")


if __name__ == "__main__":
    asyncio.run(main())
