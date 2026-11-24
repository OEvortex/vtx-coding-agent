"""Prompt history: load/save/append against the ctx store."""

import pytest
from vx import history


class FakeStore:
    def __init__(self):
        self.prompts: list[str] = []
        self.replaced: list[str] | None = None
        self.appended: list[tuple[str, float, int]] = []
        self.fail = False

    def recent_prompts(self, limit):
        if self.fail:
            raise RuntimeError("db gone")
        return list(reversed(self.prompts[-limit:]))

    def replace_prompts(self, entries, limit=0):
        if self.fail:
            raise RuntimeError("db gone")
        self.replaced = list(entries)
        self.prompts = list(entries)

    def append_prompt(self, text, ts, limit=0):
        if self.fail:
            raise RuntimeError("db gone")
        self.appended.append((text, ts, limit))
        self.prompts.append(text)
        if limit:
            self.prompts = self.prompts[-limit:]


@pytest.fixture
def store(monkeypatch):
    fake = FakeStore()
    monkeypatch.setattr(history, "_store", lambda: fake)
    return fake


class TestLoad:
    def test_empty(self, store):
        assert history.load() == []

    def test_oldest_first(self, store):
        store.prompts = ["one", "two", "three"]
        assert history.load() == ["one", "two", "three"]

    def test_store_failure_returns_empty(self, store):
        store.fail = True
        assert history.load() == []


class TestSave:
    def test_writes_entries(self, store):
        history.save(["a", "b"])
        assert store.replaced == ["a", "b"]

    def test_trims_to_limit(self, store):
        history.save(["a", "b", "c"], limit=2)
        assert store.replaced == ["b", "c"]

    def test_failure_swallowed(self, store):
        store.fail = True
        history.save(["a"])  # must not raise


class TestAppend:
    def test_adds_and_saves(self, store):
        entries = history.append("hello", ["old"])
        assert entries == ["old", "hello"]
        assert store.appended and store.appended[0][0] == "hello"

    def test_strips_and_skips_empty(self, store):
        assert history.append("   ", ["a"]) == ["a"]
        assert not store.appended

    def test_consecutive_duplicate_skipped(self, store):
        entries = history.append("same", ["same"])
        assert entries == ["same"]
        assert not store.appended

    def test_nonconsecutive_duplicate_kept(self, store):
        entries = history.append("a", ["a", "b"])
        assert entries == ["a", "b", "a"]

    def test_trims_to_limit(self, store):
        entries = history.append("new", ["a", "b"], limit=2)
        assert entries == ["b", "new"]

    def test_store_failure_returns_existing(self, store):
        store.fail = True
        assert history.append("x", ["a"]) == ["a"]
