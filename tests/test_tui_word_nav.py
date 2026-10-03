import asyncio

from textual.app import App, ComposeResult

from vtx.tui.input import InputBox


class Harness(App[None]):
    def compose(self) -> ComposeResult:
        yield InputBox(id="input-box")


async def main() -> None:
    app = Harness()
    async with app.run_test() as pilot:
        box = app.query_one("#input-box", InputBox)
        box.focus()
        await pilot.pause()
        box.insert("hello brave world")
        await pilot.pause()

        start = box.query_one("#input-textarea").selection.end[1]
        await pilot.press("alt+left")
        await pilot.pause()
        after_left = box.query_one("#input-textarea").selection.end[1]
        assert after_left < start, f"alt+left did not move: {start} -> {after_left}"

        await pilot.press("alt+backspace")
        await pilot.pause()
        assert box.text == "hello world", repr(box.text)


if __name__ == "__main__":
    asyncio.run(main())
    print("PASS")
