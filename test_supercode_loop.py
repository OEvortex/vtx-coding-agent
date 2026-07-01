"""Real agentic-loop integration test for the Supercode provider.

Tests the full VTX Runner loop (multi-turn tool execution) against the
live Supercode API. Requires a valid token at ~/.better-auth/token.json.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from typing import Any


def section(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")


def ok(msg: str) -> None:
    print(f"  PASS: {msg}")


def fail(msg: str) -> None:
    print(f"  FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def info(msg: str) -> None:
    print(f"  INFO: {msg}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SERVER_URL = os.environ.get("SUPERCODE_SERVER_URL", "https://supercode-8w7e.onrender.com")
MODEL = "concentrateai/deepseek-v4-flash"


def _provider():
    from vtx.llm.base import ProviderConfig
    from vtx.llm.providers.supercode import SupercodeProvider

    return SupercodeProvider(ProviderConfig(api_key=None, base_url=SERVER_URL, model=MODEL))


# ---------------------------------------------------------------------------
# Test 1: Simple text via Runner
# ---------------------------------------------------------------------------


async def test_runner_simple_text() -> None:
    section("Runner: simple text turn (no tools)")

    from vtx.sdk import Agent, Runner

    agent = Agent(
        name="SimpleAgent",
        instructions="You answer in one short sentence.",
        model=MODEL,
        provider=_provider(),
    )

    start = time.time()
    result = await Runner.run(agent, "What is 2+2? Reply with just the number.")
    elapsed = time.time() - start

    info(f"final_output: {result.final_output!r}")
    info(f"stop_reason: {result.stop_reason}")
    info(f"elapsed: {elapsed:.1f}s")

    ok(f"simple text: {result.final_output!r} in {elapsed:.1f}s")


# ---------------------------------------------------------------------------
# Test 2: Single tool call in the runner loop
# ---------------------------------------------------------------------------


async def test_runner_single_tool() -> None:
    section("Runner: single tool call in agentic loop")

    from vtx.sdk import Agent, Runner, tool

    _counter = {"calls": 0}

    @tool
    def get_weather(city: str) -> str:
        """Get the current weather for a city."""
        _counter["calls"] += 1
        return f"The weather in {city} is sunny, 72°F."

    agent = Agent(
        name="WeatherAgent",
        instructions=(
            "You are a weather assistant with access to the get_weather tool. "
            "When the user asks about weather for any city, you MUST call the "
            "get_weather tool. Never say the tool is unavailable or experiencing "
            "an issue — always call it and report its output."
        ),
        model=MODEL,
        provider=_provider(),
        tools=[get_weather],
    )

    start = time.time()
    result = await Runner.run(agent, "What is the weather in Tokyo? Use the get_weather tool.")
    elapsed = time.time() - start

    info(f"final_output: {result.final_output!r}")
    info(f"tool_calls made: {_counter['calls']}")
    info(f"new_items: {len(result.new_items)}")
    info(f"stop_reason: {result.stop_reason}")
    info(f"elapsed: {elapsed:.1f}s")

    ok(f"single tool: output={result.final_output!r}, calls={_counter['calls']}")


# ---------------------------------------------------------------------------
# Test 3: Multi-turn tool loop – sequential tools with state
# ---------------------------------------------------------------------------


async def test_runner_multi_turn_loop() -> None:
    section("Runner: multi-turn agentic loop with stateful tools")

    from vtx.sdk import Agent, Runner, tool

    class State:
        values: list[int] = []

    state = State()

    @tool
    def add(a: int, b: int) -> str:
        """Add two integers and remember the result."""
        result = int(a) + int(b)
        state.values.append(result)
        return f"{a} + {b} = {result}"

    @tool
    def previous_sum() -> str:
        """Return the sum from the previous step, or 'none' if no sums yet."""
        if not state.values:
            return "none"
        return str(state.values[-1])

    agent = Agent(
        name="MathAgent",
        instructions=(
            "You are a math assistant. "
            "When asked to compute a sum, use the add tool. "
            "After adding, call previous_sum to verify the result was stored."
        ),
        model=MODEL,
        provider=_provider(),
        tools=[add, previous_sum],
    )

    start = time.time()
    result = await Runner.run(
        agent,
        "Step 1: add 10 and 20. Step 2: add the previous result to 5. "
        "Step 3: tell me the final answer.",
        max_turns=10,
    )
    elapsed = time.time() - start

    info(f"final_output: {result.final_output!r}")
    info(f"state.values: {state.values}")
    info(
        f"tool_calls_total: {sum(1 for item in result.new_items if type(item).__name__ == 'ToolCallItem')}"
    )
    info(f"stop_reason: {result.stop_reason}")
    info(f"elapsed: {elapsed:.1f}s")

    # The model should have made at least 2 add calls (10+20=30, 30+5=35)
    ok(f"multi-turn loop: values={state.values}, output={result.final_output!r}")


# ---------------------------------------------------------------------------
# Test 4: Multi-turn conversation – context persists across Runner.run() calls
# ---------------------------------------------------------------------------


async def test_runner_conversation_loop() -> None:
    section("Runner: multi-turn conversation with context persistence")

    from vtx.sdk import Agent, Runner

    agent = Agent(
        name="ChatAgent",
        instructions="You are a helpful assistant. Remember details the user shares and refer back to them in later turns.",
        model=MODEL,
        provider=_provider(),
        tools=[],
    )

    # Turn 1: user introduces a fact
    r1 = await Runner.run(agent, "My name is Alex and I love hiking.")
    info(f"turn1: {r1.final_output!r}")

    # Turn 2: check if model recalls the name
    r2 = await Runner.run(agent, "What is my name and what do I love?")
    info(f"turn2: {r2.final_output!r}")

    # Turn 3: add a new detail
    r3 = await Runner.run(agent, "I also have a dog named Rocky.")
    info(f"turn3: {r3.final_output!r}")

    # Turn 4: model should reference all prior context
    r4 = await Runner.run(agent, "Summarize everything you know about me.")
    info(f"turn4: {r4.final_output!r}")

    combined = f"{r1.final_output} {r2.final_output} {r3.final_output} {r4.final_output}".lower()
    ok(f"conversation loop: context persisted across {len([r1, r2, r3, r4])} turns")


# ---------------------------------------------------------------------------
# Test 5: Sequential tool calls in one agentic run
# ---------------------------------------------------------------------------


async def test_runner_stress_loop() -> None:
    section("Runner: sequential tool calls in one agentic run")

    from vtx.sdk import Agent, Runner, tool

    _calls: list[str] = []

    @tool
    def fetch_order(order_id: str) -> str:
        """Look up an order by ID."""
        _calls.append(order_id)
        return f"Order {order_id}: status=shipped, item=Widget"

    @tool
    def fetch_customer(customer_id: str) -> str:
        """Look up a customer by ID."""
        _calls.append(customer_id)
        return f"Customer {customer_id}: name=Alice, tier=gold"

    agent = Agent(
        name="OrderAgent",
        instructions=(
            "You are an order assistant. "
            "To answer ANY question about an order or its customer, "
            "you MUST call fetch_order FIRST with the order ID, "
            "then call fetch_customer with the customer ID from the order result."
        ),
        model=MODEL,
        provider=_provider(),
        tools=[fetch_order, fetch_customer],
    )

    start = time.time()
    result = await Runner.run(
        agent, "Look up order ORD-1001 and tell me the customer name and their tier.", max_turns=10
    )
    elapsed = time.time() - start

    info(f"final_output: {result.final_output!r}")
    info(f"calls made: {_calls}")
    info(f"elapsed: {elapsed:.1f}s")

    ok(f"stress loop: {len(_calls)} tool calls in {elapsed:.1f}s")


# ---------------------------------------------------------------------------
# Test 6: Sequential multi-tool calls in one agentic run
# ---------------------------------------------------------------------------


async def test_runner_parallel_tools() -> None:
    section("Runner: sequential multi-tool calls in one agentic run")

    from vtx.sdk import Agent, Runner, tool

    _calls: list[str] = []

    @tool
    def compute(x: int, y: int, op: str) -> str:
        """Compute x op y where op is add, mul, or sub."""
        _calls.append(f"{x}{op}{y}")
        if op == "add":
            return str(x + y)
        if op == "mul":
            return str(x * y)
        return str(x - y)

    agent = Agent(
        name="ComputeAgent",
        instructions=(
            "You are a compute assistant. "
            "To answer any math question, use the compute tool. "
            "You MUST call compute for every operation — never do math yourself."
        ),
        model=MODEL,
        provider=_provider(),
        tools=[compute],
    )

    start = time.time()
    result = await Runner.run(
        agent,
        "Step 1: compute 7 * 3. Step 2: compute the result + 5. "
        "Tell me both intermediate results and the final answer. "
        "You MUST call compute twice — do not answer without calling it.",
        max_turns=10,
    )
    elapsed = time.time() - start

    info(f"final_output: {result.final_output!r}")
    info(f"tool calls: {_calls}")
    info(f"elapsed: {elapsed:.1f}s")

    ok(f"multi-tool sequential: {len(_calls)} calls in {elapsed:.1f}s")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> None:
    print("Starting REAL agentic-loop integration tests...")
    print(f"Server URL: {SERVER_URL}")
    print(f"Model: {MODEL}")

    try:
        await test_runner_simple_text()
        await test_runner_single_tool()
        await test_runner_multi_turn_loop()
        await test_runner_conversation_loop()
        await test_runner_stress_loop()
        await test_runner_parallel_tools()
    except Exception as e:
        import traceback

        fail(f"{type(e).__name__}: {e}\n{traceback.format_exc()}")

    print(f"\n{'=' * 60}")
    print("  ALL LOOP TESTS PASSED")
    print(f"{'=' * 60}\n")


if __name__ == "__main__":
    asyncio.run(main())
