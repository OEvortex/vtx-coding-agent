"""
05_sessions.py — pluggable session backends.

Demonstrates:

* InMemorySession — fastest, no persistence
* JSONLSession — append-only JSONL on disk, interop with the TUI

Both implement the same ``Session`` Protocol.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from vtx.llm.providers.mock import MockProvider
from vtx.sdk import Agent, InMemorySession, JSONLSession, Runner


async def run_with_session(session, agent, label: str) -> None:
    print(f"--- {label} ---")
    r1 = await Runner.run(agent, "Hello", session=session)
    r2 = await Runner.run(agent, "World", session=session)
    print(f"  Final outputs: {r1.final_output!r}, {r2.final_output!r}")
    items = await session.get_items()
    print(f"  Stored {len(items)} items in session {session.session_id!r}")


async def main() -> None:
    agent = Agent(name="Bot", provider=MockProvider(scenario="simple_text"))

    print("== In-memory session ==")
    await run_with_session(InMemorySession(), agent, "in-memory")

    print()
    print("== JSONL session ==")
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "demo.jsonl"
        await run_with_session(JSONLSession(path), agent, "jsonl")
        print(f"  File persisted: {path.exists()}, size: {path.stat().st_size} bytes")

        # Reload from disk.
        print("  Reloading...")
        s2 = JSONLSession(path)
        items = await s2.get_items()
        print(f"  Reloaded {len(items)} items from disk.")


if __name__ == "__main__":
    asyncio.run(main())
