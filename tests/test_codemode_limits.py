"""The new codemode mechanisms: budgets, deadlock detection, images, governance.

Each of these exists because something was unbounded, unobservable, or
ungoverned. The tests are written to pin the *reason* as much as the behavior,
because the tempting regression is not "the code broke" but "the code works and
nobody can tell it is no longer bounded".
"""

from __future__ import annotations

import asyncio
import re

import pytest

from vtx.codemode import CodemodeSandbox, Limits, ToolError, truncate_middle
from vtx.codemode import CodemodeTool as SandboxTool
from vtx.codemode.governance import ToolGovernance

pytestmark = pytest.mark.asyncio


def _tool(
    name: str = "echo",
    result=None,
    *,
    schema: dict | None = None,
    delay: float = 0.0,
    raises: Exception | None = None,
) -> SandboxTool:
    async def run(args, _signal):
        if delay:
            await asyncio.sleep(delay)
        if raises is not None:
            raise raises
        return result if result is not None else args

    return SandboxTool(
        name=name,
        description="Echo",
        execute=run,
        input_schema=schema
        or {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]},
    )


# ---- deadline clamping ---------------------------------------------------


async def test_a_script_cannot_widen_the_host_deadline():
    # The tool path used to pass a script's `timeout_ms` through as a
    # *replacement*, so a script could raise the host's deadline up to the
    # parser's ceiling. A budget a model can inflate is not a budget.
    from vtx.codemode import clamp_int, clamp_timeout

    assert clamp_timeout(600_000, 1_000) == 1_000
    assert clamp_timeout(500, 1_000) == 500
    assert clamp_timeout(None, 1_000) == 1_000
    assert clamp_int(5_000, 200) == 200
    assert clamp_int(5, 200) == 5
    # A script cannot ask for a budget so small it cannot call anything.
    assert clamp_int(0, 200, floor=1) == 1

    sandbox = CodemodeSandbox(tools=[_tool()], limits=Limits(timeout_ms=1_000))
    result = await sandbox.execute('# @options: {"timeout_ms": 600000}\nreturn 1')
    assert result.ok


async def test_an_unknown_option_field_is_an_error():
    # Silently dropping one would let a model believe it had configured
    # something. The options line is the host's to read, so this is where it is
    # rejected -- the sandbox never sees the field.
    from vtx.codemode import CodemodeSourceError, parse_source

    with pytest.raises(CodemodeSourceError, match="Unknown options field"):
        parse_source('# @options: {"max_calls": 3}\nreturn 1')


async def test_an_out_of_range_option_is_an_error():
    from vtx.codemode import CodemodeSourceError, parse_source

    for bad in ('{"timeout_ms": 10}', '{"max_tool_calls": 0}', '{"max_output_tokens": -5}'):
        with pytest.raises(CodemodeSourceError, match="must be between"):
            parse_source(f"# @options: {bad}\nreturn 1")


async def test_the_new_option_fields_parse():
    from vtx.codemode import parse_source

    options = parse_source(
        '# @options: {"timeout_ms": 5000, "max_tool_calls": 4, "max_output_tokens": 900}\nreturn 1'
    )
    assert (options.timeout_ms, options.max_tool_calls, options.max_output_tokens) == (
        5000,
        4,
        900,
    )
    # The line is blanked, not removed, so traceback line numbers still line up
    # with what the model wrote.
    assert options.code.splitlines()[0] == ""
    assert options.code.splitlines()[1] == "return 1"


# ---- deadlock detection --------------------------------------------------


async def test_a_sleep_is_not_a_stall():
    # `asyncio.sleep` exists in this sandbox even though the JavaScript
    # reference's VM has no timers, so a naive "blocked and no tool calls" check
    # would call a legitimate wait a deadlock.
    sandbox = CodemodeSandbox(tools=[_tool()])
    result = await sandbox.execute("await asyncio.sleep(0.15)\nreturn 'slept'")
    assert result.ok
    assert result.value == "slept"


async def test_a_wait_on_a_tool_call_is_not_a_stall():
    sandbox = CodemodeSandbox(tools=[_tool(delay=0.1)])
    result = await sandbox.execute("await tools.echo(n=1)\nreturn 'waited'")
    assert result.ok
    assert result.value == "waited"


async def test_a_gather_over_slow_calls_is_not_a_stall():
    # Concurrent calls are the common case, and the detector has to hold off
    # while *any* of them is outstanding or every fan-out would be a deadlock.
    sandbox = CodemodeSandbox(tools=[_tool(delay=0.1)])
    result = await sandbox.execute(
        "r = await asyncio.gather(*[tools.echo(n=i) for i in range(4)])\nreturn len(r)"
    )
    assert result.ok
    assert result.value == 4


async def test_stall_detection_can_be_turned_off():
    # An escape hatch for chasing a suspected false positive, rather than
    # shipping a heuristic with no way to opt out of it.
    sandbox = CodemodeSandbox(
        tools=[_tool(delay=0.1)], limits=Limits(detect_stalls=False, timeout_ms=2_000)
    )
    result = await sandbox.execute("await tools.echo(n=1)\nreturn 1")
    assert result.ok


# ---- image() -------------------------------------------------------------


async def test_image_accepts_a_base64_data_uri():
    sandbox = CodemodeSandbox()
    payload = "aGVsbG8="  # "hello"
    result = await sandbox.execute(f"image('data:image/png;base64,{payload}')\nreturn 1")
    assert result.ok
    assert result.output[0] == {"type": "image", "data": payload, "mimeType": "image/png"}


async def test_image_passes_through_a_raw_mcp_image_block():
    # The case that motivates it: a tool returns a picture, and a script that
    # cannot forward it has no way to pass on what the tool found.
    sandbox = CodemodeSandbox()
    result = await sandbox.execute(
        "image({'type': 'image', 'data': 'QUJD', 'mimeType': 'image/jpeg'})\nreturn 1"
    )
    assert result.ok
    assert result.output[0] == {"type": "image", "data": "QUJD", "mimeType": "image/jpeg"}


async def test_image_refuses_a_remote_url():
    # A script that could point the transcript at any host the model chose is an
    # exfiltration primitive, not a feature.
    sandbox = CodemodeSandbox()
    result = await sandbox.execute("image('https://evil.example/x.png')\nreturn 1")
    assert not result.ok
    assert "remote image URLs" in result.diagnostic.message


async def test_image_rejects_a_non_image_block_with_a_useful_message():
    sandbox = CodemodeSandbox()
    result = await sandbox.execute("image({'type': 'text', 'text': 'hi'})\nreturn 1")
    assert not result.ok
    assert "only accepts MCP image blocks" in result.diagnostic.message


async def test_image_rejects_a_non_data_uri():
    sandbox = CodemodeSandbox()
    result = await sandbox.execute("image('not-a-uri')\nreturn 1")
    assert not result.ok
    assert "base64 data URI" in result.diagnostic.message


# ---- governance ----------------------------------------------------------


class _Recorder:
    """An event bus that records what a nested call emitted."""

    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    async def emit(self, event: str, **_payload):
        self.events.append((event, _payload.get("tool_name", "")))
        return {}


def _host_tool(name: str = "writer", *, mutating: bool = True):
    from pydantic import BaseModel

    from vtx.agent.tools.base import BaseTool

    class Params(BaseModel):
        text: str = ""

    class Writer(BaseTool):
        async def execute(self, params, cancel_event=None):
            from vtx.protocol.types import ToolResult

            return ToolResult(success=True, result="written")

    tool = Writer()
    tool.name = name
    tool.description = "Write something"
    tool.params = Params
    tool.mutating = mutating
    return tool


async def test_a_nested_call_emits_the_same_hooks_a_direct_one_does():
    from vtx.protocol.types import ToolResult

    bus = _Recorder()
    calls: list[str] = []

    async def run(tool, args):
        calls.append(tool.name)
        return ToolResult(success=True, result="ok")

    tool = _host_tool()
    governed = ToolGovernance(tool, run=run, extensions=bus)
    await governed({"text": "hi"}, None)

    assert calls == ["writer"]
    names = [event for event, _ in bus.events]
    assert "tool_execution_start" in names
    assert "tool_call" in names
    assert "tool_execution_end" in names


async def test_an_extension_can_block_a_nested_call():
    class Blocking:
        async def emit(self, event: str, **_payload):
            if event == "tool_call":
                return {"block": True, "reason": "not on a tuesday"}
            return {}

    async def run(tool, args):  # pragma: no cover - must not be reached
        raise AssertionError("a blocked call must not execute")

    governed = ToolGovernance(_host_tool(), run=run, extensions=Blocking())
    with pytest.raises(ToolError, match="not on a tuesday"):
        await governed({"text": "hi"}, None)


async def test_an_extension_can_rewrite_a_nested_calls_arguments():
    # The other half of the `tool_call` contract. An extension that rewrites
    # arguments for the model must have rewritten them for the script too, or
    # the two paths disagree about what a tool may be handed.
    from vtx.protocol.types import ToolResult

    class Rewriting:
        async def emit(self, event: str, **_payload):
            if event == "tool_call":
                return {"args": {"text": "sanitized"}}
            return {}

    seen: list[dict] = []

    async def run(tool, args):
        seen.append(args)
        return ToolResult(success=True, result="ok")

    governed = ToolGovernance(_host_tool(), run=run, extensions=Rewriting())
    await governed({"text": "<script>alert(1)</script>"}, None)
    assert seen == [{"text": "sanitized"}]


async def test_a_prompt_verdict_becomes_a_refusal_not_a_silent_allow():
    # The gate answers ALLOW or PROMPT, and PROMPT means "ask the user" -- which
    # a script has nobody to ask. Auto-approving here would make a script a way
    # to run a gated call the user never saw.
    from vtx.core.permissions import PermissionDecision

    async def run(tool, args):  # pragma: no cover - must not be reached
        raise AssertionError("a gated call must not run")

    governed = ToolGovernance(
        _host_tool(mutating=True), run=run, permission=lambda tool, args: PermissionDecision.PROMPT
    )
    with pytest.raises(ToolError, match="needs approval"):
        await governed({"text": "hi"}, None)


async def test_a_read_only_tool_is_allowed_through_the_gate():
    from vtx.core.permissions import PermissionDecision
    from vtx.protocol.types import ToolResult

    async def run(tool, args):
        return ToolResult(success=True, result="ok")

    governed = ToolGovernance(
        _host_tool(mutating=False), run=run, permission=lambda tool, args: PermissionDecision.ALLOW
    )
    assert (await governed({"text": "hi"}, None)).result == "ok"


async def test_a_gate_that_throws_refuses_rather_than_allows():

    def broken(tool, args):
        raise RuntimeError("gate exploded")

    async def run(tool, args):  # pragma: no cover - must not be reached
        raise AssertionError("an unchecked call must not run")

    governed = ToolGovernance(_host_tool(), run=run, permission=broken)
    with pytest.raises(ToolError, match="permission-checked"):
        await governed({"text": "hi"}, None)


async def test_a_gated_tool_is_refused_from_a_real_script():
    # The back door this closes, exercised through the whole path rather than
    # the wrapper alone: before, `adapt_tool` called `tool.execute` directly, so
    # one approved `codemode` call could do anything any exposed tool could, with
    # no second look and nothing shown to the user.
    from pydantic import BaseModel

    from vtx.agent.tools.base import BaseTool
    from vtx.codemode import adapt_tool
    from vtx.codemode.governance import ToolGovernance
    from vtx.core.permissions import PermissionDecision
    from vtx.protocol.types import ToolResult

    class P(BaseModel):
        text: str = ""

    class Writer(BaseTool):
        async def execute(self, params, cancel_event=None) -> ToolResult:
            return ToolResult(success=True, result="written")

    writer = Writer()
    writer.name = "write_file"
    writer.description = "Write a file"
    writer.params = P
    writer.mutating = True

    async def run(tool, args):
        return await tool.execute(tool.params.model_validate(args))

    governed = adapt_tool(
        writer,
        invoke=lambda tool, args: ToolGovernance(
            tool, run=run, permission=lambda t, a: PermissionDecision.PROMPT
        )(args, None),
    )
    sandbox = CodemodeSandbox(tools=[governed])
    result = await sandbox.execute(
        "try:\n"
        "    await tools.write_file(text='x')\n"
        "except ToolError as e:\n"
        "    return str(e)\n"
        "return 'it ran'"
    )
    assert result.ok
    # The refusal is a value the script can read and the model can act on, not
    # a mystery failure.
    assert "needs approval" in str(result.value)
    assert "it ran" not in str(result.value)


async def test_an_unknown_tool_still_raises_unknown_tool():
    sandbox = CodemodeSandbox(tools=[_tool()])
    result = await sandbox.execute(
        "try:\n    await tools.nope()\nexcept UnknownTool:\n    return 1"
    )
    assert result.ok
    assert result.value == 1


# ---- call budget ---------------------------------------------------------


async def test_the_call_budget_refuses_the_call_that_exceeds_it():
    sandbox = CodemodeSandbox(tools=[_tool()], limits=Limits(max_tool_calls=3))
    result = await sandbox.execute("for i in range(10):\n    await tools.echo(n=i)\nreturn 'done'")
    # A refusal the script can catch, not a truncated success. Uncaught here, it
    # surfaces as `tool_failure` -- the same kind any declined tool call reports.
    assert not result.ok
    assert result.diagnostic.kind == "tool_failure"
    # Three calls ran; the fourth was refused rather than executed.
    assert len([c for c in result.calls if c.ok]) == 3


async def test_a_refused_call_is_catchable_inside_the_script():
    sandbox = CodemodeSandbox(tools=[_tool()], limits=Limits(max_tool_calls=1))
    result = await sandbox.execute(
        "await tools.echo(n=1)\n"
        "try:\n"
        "    await tools.echo(n=2)\n"
        "except ToolError as e:\n"
        "    return 'refused: ' + str(e)\n"
        "return 'no refusal'"
    )
    assert result.ok
    assert str(result.value).startswith("refused:")
    assert "at most 1 tool call" in str(result.value)


async def test_a_script_may_lower_the_budget_but_not_raise_it():
    sandbox = CodemodeSandbox(tools=[_tool()], limits=Limits(max_tool_calls=5))
    result = await sandbox.execute(
        '# @options: {"max_tool_calls": 5000}\n'
        "for i in range(20):\n"
        "    await tools.echo(n=i)\n"
        "return 'ran'"
    )
    # 5000 asked for, 5 allowed. Offering a limit that silently does nothing is
    # the failure this exists to prevent.
    assert not result.ok
    assert "at most 5 tool calls" in result.diagnostic.message


async def test_each_call_reports_its_own_duration():
    # Joined on the protocol id, not on position: with `asyncio.gather` the host
    # and the worker finish in different orders, so pairing by index would
    # attribute one call's timing and error to its neighbour.
    sandbox = CodemodeSandbox(tools=[_tool(), _tool("slow", delay=0.2)])
    result = await sandbox.execute(
        "await asyncio.gather(tools.echo(n=1), tools.slow(n=2))\nreturn 'done'"
    )
    assert result.ok
    by_name = {c.name: c for c in result.calls}
    assert by_name["echo"].duration_ms is not None
    assert by_name["echo"].duration_ms < by_name["slow"].duration_ms


async def test_a_failure_is_attributed_to_the_call_that_failed():
    sandbox = CodemodeSandbox(tools=[_tool("bad", raises=ToolError("declined"))])
    result = await sandbox.execute("await tools.bad(n=1)\nreturn 'unreachable'")
    assert not result.ok
    assert [c.name for c in result.calls] == ["bad"]
    assert result.calls[0].kind == "tool_failure"
    assert result.calls[0].duration_ms is not None


async def test_the_budget_counts_concurrent_calls_not_completed_ones():
    # Four calls in flight have already cost four. Checking at completion would
    # let a script exceed the ceiling by exactly its own concurrency.
    sandbox = CodemodeSandbox(tools=[_tool(delay=0.05)], limits=Limits(max_tool_calls=2))
    result = await sandbox.execute(
        "await asyncio.gather(*[tools.echo(n=i) for i in range(6)])\nreturn 'done'"
    )
    assert len([c for c in result.calls if c.ok]) == 2


# ---- output budget -------------------------------------------------------


async def test_a_long_output_is_cut_in_the_middle_and_says_so():
    sandbox = CodemodeSandbox(limits=Limits(max_output_tokens=64))
    result = await sandbox.execute("text('A' * 200_000)\nreturn 1")
    assert result.ok
    assert result.output_truncated
    text = result.output[0]["text"]
    assert "characters truncated" in text
    # Both ends survive: the head is what the script did, the tail is what it
    # concluded.
    assert text.startswith("A")
    assert text.endswith("A")
    assert len(text) < 1000


async def test_a_short_output_is_left_alone():
    sandbox = CodemodeSandbox(limits=Limits(max_output_tokens=1000))
    result = await sandbox.execute("text('hello')\nreturn 1")
    assert result.ok
    assert not result.output_truncated
    assert result.output[0]["text"] == "hello"


async def test_truncate_middle_reports_whether_it_cut():
    text, cut = truncate_middle("x" * 10_000, 100)
    assert cut
    assert "truncated" in text
    untouched, cut = truncate_middle("short", 1000)
    assert not cut
    assert untouched == "short"


@pytest.mark.parametrize("budget", [10, 40, 256, 1000, 10_000])
async def test_a_truncated_result_never_exceeds_its_budget(budget):
    # Including the marker that explains the truncation. Measuring only the
    # retained text would overshoot by the length of the notice, which nobody
    # notices until the budget is what keeps a session inside its context window.
    allowance = budget * 4
    for size in (allowance - 1, allowance, allowance + 1, allowance * 3, 1_000_000):
        text, cut = truncate_middle("x" * size, budget)
        assert len(text) <= allowance, (budget, size, len(text))
        assert cut is (size > allowance)


async def test_the_marker_stays_accurate_at_every_budget():
    # The number it reports is what the model uses to decide whether to ask for
    # a narrower slice, so it has to be the number actually dropped. Splitting
    # the result on the marker and comparing the pieces against the source is
    # the only check that does not trust the same arithmetic twice.
    for budget in (10, 40, 256, 1000):
        allowance = budget * 4
        source = "".join(chr(97 + (i % 26)) for i in range(allowance * 4))
        text, _cut = truncate_middle(source, budget)

        # Split around the marker with a regex rather than a fixed separator: the
        # number's digit count varies with the budget, so any literal that
        # includes it would only hold for one size.
        parts = re.split(r"\n\.\.\. ([\d,]+) characters truncated \.\.\.\n", text, maxsplit=1)
        assert len(parts) == 3, text
        head, digits, tail = parts
        reported = int(digits.replace(",", ""))

        assert head == source[: len(head)]
        assert tail == source[len(source) - len(tail) :] if tail else True
        # Everything is accounted for: head + dropped + tail is the whole source.
        assert len(head) + reported + len(tail) == len(source)
