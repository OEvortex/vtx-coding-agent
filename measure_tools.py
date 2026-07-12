"""Measure the token cost of the built-in tool schemas for agenite-claw."""

from __future__ import annotations

import asyncio
import json

import tiktoken

from agenite_claw.agent.tools.registry import ToolRegistry
from agenite_claw.agent.tools.loader import ToolLoader
from agenite_claw.config.schema import Config


async def main() -> None:
    enc = tiktoken.get_encoding("cl100k_base")
    loader = ToolLoader()

    class _Ctx:
        # tool create() needs the agent-loop context; provide minimal stubs.
        config = Config().tools
        cron_service = None
        workspace = "."
        file_state_store = None
        bus = None
        sessions = None
        runtime_events = None
        subagent_manager = None
        timezone = "UTC"
        image_generation_provider_configs = {}

    ctx = _Ctx()

    classes = loader.discover()
    total = 0
    per = []
    for cls in classes:
        try:
            if not cls.enabled(ctx):
                continue
            tool = cls.create(ctx)
        except Exception as e:
            print(f"SKIP {cls.__name__}: {e!r}")
            continue
        schema = {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            },
        }
        s = json.dumps(schema, ensure_ascii=False)
        t = len(enc.encode(s))
        total += t
        per.append((t, tool.name, len(s)))
    per.sort(reverse=True)
    for t, name, c in per:
        print(f"{t:5d} tok  {c:6d} chars  {name}")
    print(f"\nTOTAL: {total} tokens, {sum(c for _, _, c in per)} chars, {len(per)} tools")


asyncio.run(main())
