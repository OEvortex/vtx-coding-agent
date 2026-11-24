"""Session title generation: gating, seeding, cleaning, provider call."""

import asyncio
from types import SimpleNamespace
from typing import cast

import pytest
from ctx.protocol.abc import BaseProvider
from ctx.protocol.types import TextPart
from vx.titles import clean_title, needs_title, seed, title_session


def make_session(*texts: str, name: str | None = None, roles=None):
    messages = []
    for i, text in enumerate(texts):
        role = roles[i] if roles else ("user" if i % 2 == 0 else "assistant")
        messages.append(SimpleNamespace(role=role, content=text))
    return SimpleNamespace(id="s1", name=name, messages=messages)


class TestNeedsTitle:
    def test_none_session(self):
        assert not needs_title(None, set())

    def test_already_attempted(self):
        s = make_session("hi", "there")
        assert not needs_title(s, {"s1"})

    def test_already_named(self):
        s = make_session("hi", "there", name="My title")
        assert not needs_title(s, set())

    def test_needs_two_messages(self):
        s = make_session("hi")
        assert not needs_title(s, set())

    def test_ready(self):
        s = make_session("hi", "hello")
        assert needs_title(s, set())


class TestSeed:
    def test_flattens_roles(self):
        s = make_session("fix the bug", "I will fix it", roles=["user", "assistant"])
        out = seed(s)
        assert "user: fix the bug" in out
        assert "assistant: I will fix it" in out

    def test_empty_session(self):
        s = make_session()
        assert seed(s) == ""

    def test_respects_limit(self):
        s = make_session("x" * 5000, "y" * 5000)
        assert len(seed(s)) <= 1200

    def test_content_parts_joined(self):
        part = SimpleNamespace(text="chunk")
        msg = SimpleNamespace(role="user", content=[part, SimpleNamespace(text=None)])
        s = SimpleNamespace(id="s", name=None, messages=[msg])
        assert "chunk" in seed(s)


class TestCleanTitle:
    def test_plain(self):
        assert clean_title("Fix login bug") == "Fix login bug"

    def test_trims_wrapping(self):
        assert clean_title('**"Fix bug."**') == "Fix bug"

    def test_empty(self):
        assert clean_title("") is None
        assert clean_title("   \n  ") is None

    def test_long_truncated(self):
        out = clean_title("word " * 30)
        assert out is not None and len(out) <= 61  # 60 + ellipsis
        assert out.endswith("…")

    def test_first_line_wins(self):
        assert clean_title("Title here\nsecond line") == "Title here"


class _FakeStream:
    def __init__(self, parts):
        self._parts = parts

    def __aiter__(self):
        async def gen():
            for p in self._parts:
                yield p

        return gen()


class _FakeProvider:
    name = "fake"

    def __init__(self, parts):
        self.parts = parts
        self.calls = 0

    async def stream(self, messages, system_prompt=None, tools=None):
        self.calls += 1
        return _FakeStream(self.parts)


class TestGenerate:
    @pytest.mark.asyncio
    async def test_title_from_text_parts(self):
        provider = _FakeProvider([TextPart(text="Fix the login bug")])
        s = make_session("login is broken", "on it")
        out = await title_session(cast(BaseProvider, provider), s)
        assert out == "Fix the login bug"
        assert provider.calls == 1

    @pytest.mark.asyncio
    async def test_empty_seed_no_call(self):
        provider = _FakeProvider([TextPart(text="x")])
        s = make_session()
        out = await title_session(cast(BaseProvider, provider), s)
        assert out is None
        assert provider.calls == 0

    @pytest.mark.asyncio
    async def test_timeout_returns_none(self):
        class SlowProvider:
            name = "slow"

            async def stream(self, *a, **k):
                await asyncio.sleep(10)
                yield None

        s = make_session("hi", "hello")
        out = await title_session(cast(BaseProvider, SlowProvider()), s, timeout=0.05)
        assert out is None
