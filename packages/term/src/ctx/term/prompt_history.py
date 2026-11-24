from __future__ import annotations

import contextlib
import time

from ctx.core import store

MAX_HISTORY_ENTRIES = 50


class PromptHistory:
    """Prompt recall, kept in the shared database.

    The TUI and vx read the same rows, so an up arrow recalls what you asked in
    either front end, in the order you asked it.
    """

    def __init__(self) -> None:
        self._entries: list[str] = []
        self._index: int = 0
        self._draft: str = ""
        self._load()

    def _load(self) -> None:
        try:
            entries = store.recent_prompts(MAX_HISTORY_ENTRIES)
        except Exception:
            entries = []
        self._entries = list(reversed(entries))

    def append(self, text: str) -> None:
        if not text:
            return
        if self._entries and self._entries[-1] == text:
            self._reset_index()
            return
        self._entries.append(text)
        if len(self._entries) > MAX_HISTORY_ENTRIES:
            self._entries = self._entries[-MAX_HISTORY_ENTRIES:]
            with contextlib.suppress(Exception):
                store.replace_prompts(self._entries, MAX_HISTORY_ENTRIES)
        else:
            with contextlib.suppress(Exception):
                store.append_prompt(text, time.time(), MAX_HISTORY_ENTRIES)
        self._reset_index()

    @property
    def is_browsing(self) -> bool:
        return self._index != 0

    def _reset_index(self) -> None:
        self._index = 0
        self._draft = ""

    def navigate(self, direction: int, current_text: str) -> str | None:
        if not self._entries:
            return None

        if self._index == 0:
            self._draft = current_text

        new_index = self._index + direction

        if new_index > 0 or abs(new_index) > len(self._entries):
            return None

        self._index = new_index

        if self._index == 0:
            return self._draft

        return self._entries[self._index]
