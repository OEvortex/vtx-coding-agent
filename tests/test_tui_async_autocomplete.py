import asyncio
import time
import warnings

from textual.app import App, ComposeResult
from textual.widgets import Label

from vtx.tui.autocomplete import AutocompleteProvider, CompletionResult
from vtx.tui.floating_list import ListItem
from vtx.tui.input import InputBox


class SlowProvider(AutocompleteProvider):
    """Stands in for the fd/gh providers: get_suggestions blocks."""

    slow = True

    def __init__(self, delay: float = 0.3) -> None:
        self.delay = delay

    @property
    def trigger_chars(self) -> set[str]:
        return {"!"}

    def should_trigger(self, text: str, cursor_col: int) -> bool:
        return text.startswith("!")

    def get_suggestions(self, text: str, cursor_col: int) -> CompletionResult | None:
        time.sleep(self.delay)
        return CompletionResult(
            items=[ListItem(value="x", label="hit")], prefix=text, replace_start=0
        )

    def apply_completion(self, text, cursor_col, item, prefix):
        return text, cursor_col


class Harness(App[None]):
    def compose(self) -> ComposeResult:
        yield InputBox(id="input-box")
        yield Label(id="out")


async def main() -> None:
    app = Harness()
    async with app.run_test() as pilot:
        box = app.query_one("#input-box", InputBox)
        box._providers = [SlowProvider(delay=0.3)]
        box.focus()
        await pilot.pause()

        box.insert("!hello")
        started = time.perf_counter()
        box._try_autocomplete()
        # _try_autocomplete must return immediately; the work is off-loop.
        elapsed = time.perf_counter() - started
        assert elapsed < 0.1, f"_try_autocomplete blocked for {elapsed * 1000:.0f} ms"

        # The UI stays responsive while the slow provider runs.
        t0 = time.perf_counter()
        await pilot.pause()
        assert time.perf_counter() - t0 < 0.1, "event loop stalled during suggestions"

        await asyncio.sleep(0.6)
        await pilot.pause()
        assert box.is_completing, "slow suggestions never arrived"

        # Dismissing the list while a slow request is in flight (Esc mid-search)
        # invalidates it, so the late reply cannot reopen what was just closed.
        box.set_completing(False)
        assert not box.is_completing
        box._try_autocomplete()  # starts a fresh slow request
        assert box._is_completing is False
        box.set_completing(False)  # user dismisses before it lands
        await asyncio.sleep(0.6)
        await pilot.pause()
        assert not box.is_completing, "dismissed request still applied its result"


async def rapid_input_keeps_latest_and_never_leaks() -> None:
    """Overlapping requests: newest wins, and no coroutine is left un-awaited."""
    app = Harness()
    async with app.run_test() as pilot:
        box = app.query_one("#input-box", InputBox)
        box._providers = [SlowProvider(delay=0.05)]
        box.focus()
        await pilot.pause()
        for _ in range(5):
            box.insert("!")
            box._try_autocomplete()
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.5)
        await pilot.pause()
        assert box.is_completing, "latest request should win"


if __name__ == "__main__":
    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        asyncio.run(main())
        asyncio.run(rapid_input_keeps_latest_and_never_leaks())
    print("PASS")
