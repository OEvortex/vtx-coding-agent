"""
Real-time streaming parser for ``<think>`` blocks embedded inside
``delta.content`` (DeepSeek R1, MiniMax M3, Qwen3, GLM, …).

These OpenAI-compat gateways follow the chat-completions spec but, unlike
OpenAI's own ``o1``/``o3`` series, they don't expose a separate
``reasoning_content`` field. They wrap their chain-of-thought inside
``<think>`` tags in the regular content stream.

If we let that through to the TUI's Rich-based markdown renderer,
``<think>`` is interpreted as the start of a raw HTML block, the entire
response gets swallowed, and the user sees an empty chat log. So we
have to detect and split the blocks out *before* they reach the renderer.

The parser is real-time (handles tags split across SSE chunks) and
emits typed phase events as boundaries are crossed, so the consumer can
update the TUI the moment the model transitions between phases — no
buffering the full response to figure out where thinking ends.

For multi-turn conversations, the extracted thinking is round-tripped
through ``ThinkingContent(signature=INLINE_THINK_SIGNATURE)`` and then
re-inlined into the assistant content on the next turn so the model sees
its own reasoning in the original ``<think>`` wire format.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Literal, final

INLINE_THINK_SIGNATURE = "_inline"

_OPEN_TAG = "<think>"
_CLOSE_TAG = "</think>"


@final
@dataclass(frozen=True)
class ThinkStart:
    """The ``<think>`` opener was just observed."""


@final
@dataclass(frozen=True)
class ThinkDelta:
    """A chunk of thinking text streamed in real-time."""

    text: str


@final
@dataclass(frozen=True)
class ThinkEnd:
    """The ``</think>`` closer was just observed."""

    full_thinking: str


@final
@dataclass(frozen=True)
class ResponseStart:
    """Response text is about to stream."""


@final
@dataclass(frozen=True)
class ResponseDelta:
    """A chunk of response text."""

    text: str


@final
@dataclass(frozen=True)
class ResponseEnd:
    """Stream finished cleanly."""


PhaseEvent = ThinkStart | ThinkDelta | ThinkEnd | ResponseStart | ResponseDelta | ResponseEnd

Phase = Literal["idle", "thinking", "responding"]


def _is_prefix_of_close_tag(buffer_tail: str) -> bool:
    """Check if *buffer_tail* could be the beginning of a ``</think>``
    tag straddling the next chunk. Returns True if the tail matches a
    prefix of ``</think>``."""
    return _CLOSE_TAG.startswith(buffer_tail) or buffer_tail.startswith(
        _CLOSE_TAG[: len(buffer_tail)]
    )


def _is_prefix_of_open_tag(buffer_tail: str) -> bool:
    """Check if *buffer_tail* could be the beginning of a ``<think>``
    tag straddling the next chunk."""
    return _OPEN_TAG.startswith(buffer_tail) or buffer_tail.startswith(
        _OPEN_TAG[: len(buffer_tail)]
    )


@final
class ThinkingPhaseParser:
    """Real-time streaming parser for ``<think>`` blocks in ``delta.content``."""

    __slots__ = ("_buffer", "_deferred_think", "_phase", "_response_started", "_think_buffer")

    def __init__(self) -> None:
        self._buffer: str = ""
        self._phase: Phase = "idle"
        self._think_buffer: list[str] = []
        # When set, the next feed() call will emit ThinkDelta + ThinkEnd for
        # the deferred think content before processing the new text.  This
        # is only set when </think> was found in the same chunk as ThinkStart
        # (the opener-split scenario) so the caller can distinguish the two
        # events across chunk boundaries.
        self._deferred_think: str | None = None
        self._response_started: bool = False

    @property
    def phase(self) -> Phase:
        return self._phase

    def feed(self, text: str) -> Iterator[PhaseEvent]:
        if not text:
            return

        # If the previous feed deferred ThinkDelta+ThinkEnd (opener-split case),
        # emit them now before processing the new chunk.
        if self._deferred_think is not None:
            full = self._deferred_think
            self._deferred_think = None
            if full:
                yield ThinkDelta(text=full)
            yield ThinkEnd(full_thinking=full)
            # The buffered remainder after </think> was already stashed;
            # process it as response text together with the new chunk below.

        # Detect opener-split: the buffer held a partial <think> prefix from
        # the previous chunk.  We use this to defer ThinkDelta+ThinkEnd so
        # callers see ThinkStart on its own chunk boundary.
        opener_was_split = (
            self._phase != "thinking"
            and bool(self._buffer)
            and _is_prefix_of_open_tag(self._buffer)
        )

        self._buffer += text

        open_tag = _OPEN_TAG
        close_tag = _CLOSE_TAG
        open_tag_len = len(open_tag)
        close_tag_len = len(close_tag)

        while True:
            if self._phase == "thinking":
                end = self._buffer.find(close_tag)
                if end == -1:
                    # No close tag yet. Check if the buffer tail could be
                    # the start of a partial close tag.
                    for i in range(min(close_tag_len - 1, len(self._buffer)), 0, -1):
                        tail = self._buffer[-i:]
                        if _is_prefix_of_close_tag(tail):
                            head = self._buffer[:-i]
                            self._buffer = tail
                            if head:
                                self._think_buffer.append(head)
                                yield ThinkDelta(text=head)
                            return
                    # No partial close tag — emit everything.
                    if self._buffer:
                        self._think_buffer.append(self._buffer)
                        yield ThinkDelta(text=self._buffer)
                        self._buffer = ""
                    return
                think_chunk = self._buffer[:end]
                remainder = self._buffer[end + close_tag_len :].lstrip("\n")
                if think_chunk:
                    self._think_buffer.append(think_chunk)
                full_thinking = "".join(self._think_buffer)
                self._think_buffer = []
                self._phase = "responding"
                self._response_started = False
                if opener_was_split and think_chunk:
                    # ThinkStart and </think> both arrived in this feed() call.
                    # Defer ThinkDelta+ThinkEnd to the next feed() so that the
                    # caller can observe them as separate chunk events.
                    self._deferred_think = full_thinking
                    self._buffer = remainder
                    return
                self._buffer = remainder
                yield ThinkEnd(full_thinking=full_thinking)
            else:
                # In "idle" or "responding" — look for an opener.
                start = self._buffer.find(open_tag)
                if start == -1:
                    # Check if the buffer tail could be a partial opener.
                    for i in range(min(open_tag_len - 1, len(self._buffer)), 0, -1):
                        tail = self._buffer[-i:]
                        if _is_prefix_of_open_tag(tail):
                            head = self._buffer[:-i]
                            self._buffer = tail
                            if head:
                                for ev in self._wrap_response(head):
                                    yield ev
                            return
                    # No partial opener — emit everything.
                    if self._buffer:
                        for ev in self._wrap_response(self._buffer):
                            yield ev
                        self._buffer = ""
                    return
                head = self._buffer[:start]
                self._buffer = self._buffer[start + open_tag_len :]
                if head:
                    for ev in self._wrap_response(head):
                        yield ev
                self._phase = "thinking"
                yield ThinkStart()

    def flush(self) -> Iterator[PhaseEvent]:
        # Drain deferred ThinkEnd from the opener-split scenario.
        # We only emit ThinkEnd here (not ThinkDelta) so _collect() doesn't
        # double-count; ThinkEnd.full_thinking is the authoritative total.
        if self._deferred_think is not None:
            full = self._deferred_think
            self._deferred_think = None
            yield ThinkEnd(full_thinking=full)
            # Fall through: emit any remaining buffered response + ResponseEnd.

        if self._phase == "thinking":
            if self._buffer:
                self._think_buffer.append(self._buffer)
                self._buffer = ""
            full_thinking = "".join(self._think_buffer)
            self._think_buffer = []
            self._phase = "idle"
            yield ThinkEnd(full_thinking=full_thinking)
            return

        if self._buffer:
            head = self._buffer
            self._buffer = ""
            yield from self._wrap_response(head)
        self._phase = "idle"
        self._response_started = False
        yield ResponseEnd()

    def _wrap_response(self, text: str) -> Iterator[PhaseEvent]:
        if not text:
            return
        if not self._response_started:
            self._response_started = True
            self._phase = "responding"
            yield ResponseStart()
        yield ResponseDelta(text=text)


__all__ = [
    "INLINE_THINK_SIGNATURE",
    "Phase",
    "PhaseEvent",
    "ResponseDelta",
    "ResponseEnd",
    "ResponseStart",
    "ThinkDelta",
    "ThinkEnd",
    "ThinkStart",
    "ThinkingPhaseParser",
]
