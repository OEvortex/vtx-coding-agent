"""Focused tests for the bus face an extension handler is called through.

``vtx.plugins.bus`` is a deprecation boundary: it adapts the composition's
:class:`EventHub` to the four-method shape the older call sites already use.
The point of these tests is that the adapter is *transparent* — a handler
written against the old contract keeps its exact semantics, in particular the
one that is easy to regress quietly: the first handler that blocks stops the
rest from running at all.

One source bug is worked around rather than fixed here, and it is the reason for
the autouse fixture below: ``_accepts_third`` memoises by ``id(handler)``, and
CPython hands a freed function's address straight back to the next one, so a
cache entry written by one handler answers for an unrelated handler that lands
on the same address.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from vtx.plugins.api import ExtensionUIContext
from vtx.plugins.bus import EventBus, ExtensionBus, HandlerContext
from vtx.plugins.event_contract import ALL_EVENTS, TOOL_CALL, TOOL_RESULT

_BUS_LOG = "vtx.plugins.bus"


@pytest.fixture(autouse=True)
def recycle_handler_addresses():
    """Force the address reuse that a memo keyed on ``id()`` falls for.

    ``_accepts_third`` used to be memoised in a dict keyed by ``id(handler)``.
    CPython hands a freed function's address straight back to the next function
    it allocates, so a stale entry made an unrelated handler answer with the
    wrong arity, and dispatch swallowed the resulting ``TypeError`` — a handler
    that silently never runs. Rebuilding the function in place is what makes a
    regression visible rather than dependent on allocation luck.
    """

    def rebuild(handler):
        clone = type(handler)(handler.__code__, handler.__globals__, handler.__name__)
        return clone

    yield rebuild


def test_a_rebuilt_handler_is_not_mistaken_for_the_one_it_replaced(recycle_handler_addresses):
    """Arity is read from the handler itself, never remembered by its address.

    ``old`` takes three arguments, so an ``id()``-keyed memo records ``True``
    for its address. Dropping it and allocating a two-argument handler in its
    place reproduces the collision CPython hands out for free; the new handler
    must still be judged on its own signature.
    """
    from vtx.plugins.bus import _accepts_third

    def old(event, payload, ctx):
        return None

    assert _accepts_third(old) is True
    old_address = id(old)
    del old

    def fresh(event, payload):
        return None

    assert id(fresh) == old_address, "the address was not recycled; this test proves nothing"
    assert _accepts_third(fresh) is False


class _RecordingUI(ExtensionUIContext):
    """A host UI installed over the no-op one."""

    def __init__(self) -> None:
        self.notices: list[tuple[str, str]] = []

    def notify(self, message: str, level: str = "info") -> None:
        self.notices.append((message, level))


# --------------------------------------------------------------------------
# Registration
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_handler_receives_the_event_name_and_the_payload():
    """The calling convention is the extension contract: the handler is told
    which event fired and what it carried, in that order."""
    bus = ExtensionBus()
    seen: list[tuple[str, dict]] = []

    bus.on(TOOL_CALL, lambda event, payload: seen.append((event, dict(payload))))

    assert await bus.emit(TOOL_CALL, tool_name="bash", args={"cmd": "ls"}) == {}
    assert seen == [(TOOL_CALL, {"tool_name": "bash", "args": {"cmd": "ls"}})]


@pytest.mark.asyncio
async def test_the_decorator_form_subscribes_and_returns_the_handler():
    """``@bus.on(EVENT)`` has to hand the function back unchanged, or an
    extension that decorates its own function ends up calling a decorator."""
    bus = ExtensionBus()

    @bus.on(TOOL_CALL)
    def handler(event, payload):
        return {"args": {"seen": payload["tool_name"]}}

    assert callable(handler)
    assert bus.handler_count(TOOL_CALL) == 1
    assert bus.has_handlers(TOOL_CALL) is True
    assert await bus.emit(TOOL_CALL, tool_name="bash") == {"args": {"seen": "bash"}}


def test_a_bus_with_no_handlers_reports_none():
    bus = EventBus()
    assert bus.handler_count(TOOL_RESULT) == 0
    assert bus.has_handlers(TOOL_RESULT) is False


def test_an_unknown_event_name_is_rejected_and_names_the_valid_set():
    """A typo in an extension's subscription must fail at load, and the error
    has to say what it could have been — there are forty valid names."""
    bus = ExtensionBus()

    with pytest.raises(ValueError) as caught:
        bus.on("tool-call", lambda event, payload: None)

    message = str(caught.value)
    assert "Unknown event 'tool-call'" in message
    assert TOOL_CALL in message
    for name in ALL_EVENTS:
        assert name in message


# --------------------------------------------------------------------------
# The payload
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_payload_indexes_as_a_dict_and_exposes_typed_fields():
    """Both spellings are in the wild and both have to keep working from the
    same object: ``payload["tool_name"]`` for the mapping callers, and
    ``payload.tool_name`` for the ones that read the typed event."""
    bus = ExtensionBus()
    seen: list[tuple] = []

    def handler(event, payload):
        seen.append(
            (
                payload["tool_name"],
                payload.tool_name,
                payload.args,
                # A field the emitter did not send still reads as the typed
                # event's default rather than raising.
                payload.tool_call_id,
            )
        )

    bus.on(TOOL_CALL, handler)
    await bus.emit(TOOL_CALL, tool_name="bash", args={"cmd": "ls"})

    assert seen == [("bash", "bash", {"cmd": "ls"}, "")]


@pytest.mark.asyncio
async def test_a_payload_field_that_does_not_exist_anywhere_is_an_error(caplog):
    """The typed fallback must not turn a typo into a silent ``None``: the
    handler fails, and the failure is the same logged one as any other."""
    bus = ExtensionBus()

    def handler(event, payload):
        return {"args": {"seen": payload.not_a_field}}

    bus.on(TOOL_CALL, handler)
    with caplog.at_level(logging.ERROR, logger=_BUS_LOG):
        assert await bus.emit(TOOL_CALL, tool_name="bash") == {}

    assert "not_a_field" in caplog.text


# --------------------------------------------------------------------------
# HandlerContext
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_three_argument_handler_gets_a_usable_context():
    """``ctx.ui`` has to exist with no host installed. An extension that
    prompts the user has to run in headless mode too, where the primitives
    resolve to their safe defaults rather than raising ``AttributeError``."""
    bus = ExtensionBus()
    contexts: list[HandlerContext] = []

    def handler(event, payload, ctx):
        contexts.append(ctx)

    bus.on(TOOL_CALL, handler)
    await bus.emit(TOOL_CALL, tool_name="bash")

    assert len(contexts) == 1
    context = contexts[0]
    assert isinstance(context, HandlerContext)
    assert isinstance(context.ui, ExtensionUIContext)
    assert context.mode == "print"
    assert await context.ui.confirm("proceed?", "really") is False
    assert await context.ui.select("pick", ["a"]) is None
    context.ui.notify("no-op", "warning")


@pytest.mark.asyncio
async def test_set_ui_context_replaces_what_a_three_argument_handler_receives():
    """Installing the host's UI is what turns the no-op context into an
    interactive one, together with the cwd and mode the handler asked for."""
    bus = ExtensionBus()
    seen: list[HandlerContext] = []

    bus.on(TOOL_CALL, lambda event, payload, ctx: seen.append(ctx))
    await bus.emit(TOOL_CALL, tool_name="bash")
    assert seen[-1].cwd == ""
    assert seen[-1].mode == "print"

    ui = _RecordingUI()
    bus.set_ui_context(ui, cwd="/tmp/project", mode="tui")
    await bus.emit(TOOL_CALL, tool_name="bash")

    assert seen[-1].ui is ui
    assert seen[-1].cwd == "/tmp/project"
    assert seen[-1].mode == "tui"
    seen[-1].ui.notify("from the handler")
    assert ui.notices == [("from the handler", "info")]


# --------------------------------------------------------------------------
# Blocking and chaining
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_first_blocking_handler_stops_the_rest_from_running():
    """A policy handler that denies a tool call has to prevent any later
    handler from seeing it — including the ones that rewrite arguments or log
    the call for audit."""
    bus = ExtensionBus()
    reached: list[str] = []

    @bus.on(TOOL_CALL)
    def policy(event, payload):
        return {"block": True, "reason": "denied by policy"}

    @bus.on(TOOL_CALL)
    def audit(event, payload):
        reached.append("audit")
        return {"args": {"cmd": "rm -rf /"}}

    merged = await bus.emit(TOOL_CALL, tool_name="bash", args={"cmd": "rm -rf /"})

    assert reached == []
    assert merged["block"] is True
    assert merged["reason"] == "denied by policy"
    assert "args" not in merged


@pytest.mark.asyncio
async def test_an_args_rewrite_chains_through_the_handlers_that_follow():
    """A rewriter is only useful if the next handler sees its output; the
    merged result is the last writer's, because it saw the chain."""
    bus = ExtensionBus()
    seen: list[dict] = []

    def sandbox(event, payload):
        return {"args": {"path": "/tmp/safe"}}

    def observe(event, payload):
        seen.append(dict(payload["args"]))
        return {"args": {"path": "/tmp/even-safer"}}

    bus.on(TOOL_CALL, sandbox)
    bus.on(TOOL_CALL, observe)

    merged = await bus.emit(TOOL_CALL, args={"path": "/etc/passwd"})

    assert seen == [{"path": "/tmp/safe"}]
    assert merged["args"] == {"path": "/tmp/even-safer"}


@pytest.mark.asyncio
async def test_an_output_rewrite_chains_through_the_handlers_that_follow():
    """Same rule for the other chained key: a redactor hands its result to
    whatever runs next rather than the original text."""
    bus = ExtensionBus()
    seen: list[str] = []

    def redact(event, payload):
        return {"output": "REDACTED"}

    def audit(event, payload):
        seen.append(payload["output"])
        return {"output": "REDACTED (logged)"}

    bus.on(TOOL_RESULT, redact)
    bus.on(TOOL_RESULT, audit)

    merged = await bus.emit(TOOL_RESULT, tool_name="read", output="secret token")

    assert seen == ["REDACTED"]
    assert merged["output"] == "REDACTED (logged)"


# --------------------------------------------------------------------------
# Dispatch robustness
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_emit_awaits_an_async_handler_and_merges_its_result():
    """The blocking path is not sync-only: an async policy handler is awaited
    and its decision merged like any other."""
    bus = ExtensionBus()

    async def policy(event, payload):
        await asyncio.sleep(0)
        return {"block": True, "reason": "checked"}

    bus.on(TOOL_CALL, policy)
    assert await bus.emit(TOOL_CALL, tool_name="bash") == {"block": True, "reason": "checked"}


@pytest.mark.asyncio
async def test_a_raising_handler_does_not_break_the_others(caplog):
    """One broken extension must not take the tool call down with it; the
    failure is logged and dispatch carries on to the next handler."""
    bus = ExtensionBus()
    ran: list[str] = []

    def broken(event, payload):
        raise RuntimeError("handler is broken")

    def healthy(event, payload):
        ran.append("healthy")
        return {"args": {"rewritten": True}}

    bus.on(TOOL_CALL, broken)
    bus.on(TOOL_CALL, healthy)

    with caplog.at_level(logging.ERROR, logger=_BUS_LOG):
        merged = await bus.emit(TOOL_CALL, tool_name="bash")

    assert ran == ["healthy"]
    assert merged == {"args": {"rewritten": True}}
    assert "handler for tool_call raised" in caplog.text
    assert "handler is broken" in caplog.text


def test_emit_sync_runs_sync_handlers_and_drops_coroutines(caplog):
    """``emit_sync`` is the pre-loop path (``session_start`` on mount). An
    async handler cannot be awaited there, so it is logged and skipped rather
    than silently dropped — and it must not stop the sync handlers after it."""
    bus = ExtensionBus()
    ran: list[str] = []

    async def async_handler(event, payload):
        ran.append("async")
        return {"args": {"from": "async"}}

    def sync_handler(event, payload):
        ran.append("sync")
        return {"output": "from sync"}

    bus.on(TOOL_CALL, async_handler)
    bus.on(TOOL_CALL, sync_handler)

    with caplog.at_level(logging.WARNING, logger=_BUS_LOG):
        merged = bus.emit_sync(TOOL_CALL, tool_name="bash")

    assert ran == ["sync"]
    assert merged == {"output": "from sync"}
    assert "returned a coroutine" in caplog.text


def test_emit_sync_still_honours_a_block():
    """The sync path shares the merge rules, so a startup policy handler still
    short-circuits the ones behind it."""
    bus = ExtensionBus()
    reached: list[str] = []

    bus.on(TOOL_CALL, lambda event, payload: {"block": True, "reason": "nope"})
    bus.on(TOOL_CALL, lambda event, payload: reached.append("late"))

    assert bus.emit_sync(TOOL_CALL) == {"block": True, "reason": "nope"}
    assert reached == []


# --------------------------------------------------------------------------
# Unsubscription
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_off_removes_exactly_the_handler_it_was_given():
    """A reload unsubscribes what a bridge registered; anything else has to stay."""
    bus = ExtensionBus()
    kept: list[str] = []

    def dropped(event, payload):
        kept.append("dropped")

    def survivor(event, payload):
        kept.append("survivor")

    bus.on(TOOL_CALL, dropped)
    bus.on(TOOL_CALL, survivor)
    bus.off(TOOL_CALL, dropped)

    assert bus.handler_count(TOOL_CALL) == 1
    await bus.emit(TOOL_CALL, tool_name="bash")
    assert kept == ["survivor"]


@pytest.mark.asyncio
async def test_off_is_a_no_op_for_a_handler_that_was_never_subscribed():
    """Teardown calls it defensively; an unknown handler must not raise."""
    bus = ExtensionBus()
    bus.on(TOOL_CALL, lambda event, payload: None)
    bus.off(TOOL_CALL, lambda event, payload: None)
    assert bus.handler_count(TOOL_CALL) == 1


@pytest.mark.asyncio
async def test_a_decorated_handler_can_be_removed_by_the_name_it_was_bound_to():
    """``on`` returns the handler, not the wrapper the hub holds, so ``off`` must
    map back to the wrapper or the subscription is impossible to remove."""
    bus = ExtensionBus()

    @bus.on(TOOL_CALL)
    def handler(event, payload):
        return {"args": {"x": 1}}

    bus.off(TOOL_CALL, handler)
    assert bus.handler_count(TOOL_CALL) == 0
    assert await bus.emit(TOOL_CALL, tool_name="bash") == {}


@pytest.mark.asyncio
async def test_a_second_subscription_of_the_same_handler_is_removed_one_at_a_time():
    bus = ExtensionBus()

    def handler(event, payload):
        return None

    bus.on(TOOL_CALL, handler)
    bus.on(TOOL_CALL, handler)
    assert bus.handler_count(TOOL_CALL) == 2
    bus.off(TOOL_CALL, handler)
    assert bus.handler_count(TOOL_CALL) == 1
    bus.off(TOOL_CALL, handler)
    assert bus.handler_count(TOOL_CALL) == 0
