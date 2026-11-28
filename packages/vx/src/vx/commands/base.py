"""Shared plumbing for the slash-command mixins.

The command set is a port of ``packages/code/src/ctx/term/commands`` onto vx's
footer UI: same verbs, same sub-commands, same wording where it is user-facing.
Only the widgets differ, so everything here is about turning a command's
results into vx primitives — a transcript block, a picker, or a text prompt.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine, Mapping, Sequence
from typing import TYPE_CHECKING, Any

from vx.tui import blocks, footer
from vx.tui.terminal import Terminal
from vx.tui.viewport import Viewport

if TYPE_CHECKING:
    # Annotation only: importing the runtime here dragged ctx's whole agent
    # graph (and aiohttp) in before vx could paint or read a key.
    from ctx.agent.runtime import ConversationRuntime


class CommandSupport:
    """Attributes and helpers every command mixin can rely on."""

    cwd: str
    runtime: ConversationRuntime | None
    term: Terminal
    viewport: Viewport
    _menu: dict[str, Any] | None
    _prompt: dict[str, Any] | None
    _input: str
    _cursor: int
    _is_running: bool
    _activity: str
    _exiting: bool
    _history: list[str]
    _searching: bool

    # -- provided by VxApp (declared here for type checking) --------------

    def _set_search(self, text: str) -> None: ...

    if TYPE_CHECKING:

        def _size(self) -> tuple[int, int]: ...

    def _notice(
        self, body: str, *, topic: str = "", tone: str = "information", detail: str = ""
    ) -> None: ...

    def _draw(self, *, force: bool = False) -> None: ...

    def _refresh_stats(self, *, force: bool = False) -> None: ...

    def _open_swarm(self) -> None:
        """Show the swarm board page (implemented by VxApp)."""
        ...

    def _reset_sidebar(self) -> None:
        """Forget the plan with the conversation."""
        ...

    def _welcome(self) -> None:
        """Re-add the pinned welcome banner after the transcript is cleared."""
        ...

    async def _draft_recap(self, rt: Any = None, *, note: str = "you asked") -> None:
        """Draft a recap into the box vx draws for it."""
        ...

    def _enqueue_or_run(self, prompt: str) -> None:
        """Run a prompt as a normal turn, or queue it behind the live one."""
        ...

    def _render_session_history(self) -> None:
        """Redraw the current session's messages into a fresh transcript."""
        ...

    # -- transcript ------------------------------------------------------

    def _report(
        self, title: str, sections: list[tuple[str, list[tuple[str, str]]]], *, detail: str = ""
    ) -> None:
        self.viewport.add(blocks.Sections(title=title, sections=sections, detail=detail))

    def _warn(self, body: str, *, topic: str = "") -> None:
        self._notice(body, topic=topic, tone="warning")

    def _fail(self, body: str, *, topic: str = "") -> None:
        self._notice(body, topic=topic, tone="error")

    def _ok(self, body: str, *, topic: str = "") -> None:
        self._notice(body, topic=topic, tone="success")

    # -- pickers ---------------------------------------------------------

    def _open_menu(
        self,
        items: list[footer.MenuItem],
        *,
        noun: str = "Commands",
        on_select: Callable[[footer.MenuItem], None] | None = None,
        variants: tuple[str, ...] = footer.MENU_HINT_VARIANTS,
        selected: int = 0,
        filterable: bool = False,
    ) -> None:
        if not items:
            self._warn("nothing to show here")
            return
        self._menu = {
            "kind": "choice",
            "items": items,
            "selected": max(0, min(selected, len(items) - 1)),
            "noun": noun,
            "type_hint": filterable,
            "variants": variants,
            "filterable": filterable,
            "query": "",
            "on_select": on_select,
        }

    def _close_menu(self) -> None:
        self._menu = None

    def _choice_items(
        self,
        choices: Sequence[str],
        current: str | None,
        descriptions: Mapping[str, str] | None = None,
    ) -> list[footer.MenuItem]:
        descriptions = descriptions or {}
        return [
            footer.MenuItem(
                label=f"{choice} ✓" if choice == current else choice,
                detail=descriptions.get(choice, ""),
                value=choice,
            )
            for choice in choices
        ]

    def _pick_choice(
        self,
        noun: str,
        choices: Sequence[str],
        current: str | None,
        select: Callable[[str], None],
        descriptions: Mapping[str, str] | None = None,
    ) -> None:
        items = self._choice_items(choices, current, descriptions)
        index = next((i for i, c in enumerate(choices) if c == current), 0)
        self._open_menu(
            items,
            noun=noun,
            on_select=lambda item: select(item.value or item.label),
            selected=index,
        )

    # -- text prompts ----------------------------------------------------

    def _prompt_input(
        self, label: str, callback: Callable[[str], None], *, secret: bool = False
    ) -> None:
        """Ask for one line of text through the composer, then call back."""
        self._prompt = {"label": label, "callback": callback, "secret": secret}
        self._input = ""
        self._cursor = 0

    # -- background work -------------------------------------------------

    def _spawn(self, coro: Coroutine[Any, Any, Any]) -> None:
        asyncio.get_running_loop().create_task(coro)

    def _require_runtime(self) -> ConversationRuntime | None:
        if self.runtime is None:
            self._warn("the runtime is still starting — try again in a moment")
            return None
        return self.runtime

    def _require_idle(self, verb: str) -> bool:
        if self._is_running:
            self._warn(f"cannot {verb} while the agent is running — press esc to interrupt")
            return False
        return True

    def _get_permission_runtime(self):
        """Return the vx PermissionRuntime, if set on the app."""
        return getattr(self, "_permission_runtime", None)
