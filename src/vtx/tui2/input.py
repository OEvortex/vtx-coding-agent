"""The prompt editor.

Ported from :mod:`vtx.tui.input`. The Textual build composed a ``Horizontal``
row of ``Label("›")`` + ``Vtx(TextArea)`` and posted ``Message`` objects upward;
here the editor is a single xnano ``Component`` wrapping an
``Input(multiline=True)``, and everything that used to be a message is a method
call or a callback the app installs.

That removes the whole message round trip: ``Submitted``/``CompletionUpdate``/
``CompletionHide``/``CompletionMove`` become ``on_submit``, ``on_completion_*``
callbacks that the app sets, so the app reads as ordinary function calls instead
of ``@on_input_box_submitted`` dispatch.

Keyboard handling uses the ``passthrough`` list on ``Input``: keys the editor
would otherwise swallow (``enter``, ``escape``, ``tab``, ``up``, ``down``,
``ctrl+j``) are declared so they reach the grid's hooks.
"""

from __future__ import annotations

import base64
import dataclasses
import os
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from xnano.area import Size
from xnano.components.component import Component, ComponentRenderContext
from xnano.components.input import Input
from xnano.core.content import Panel, Run, Stack, TextBlock

from vtx.tui2.autocomplete import (
    DEFAULT_COMMANDS,
    AutocompleteProvider,
    CompletionResult,
    FilePathProvider,
    ListItem,
    PullRequestProvider,
    SlashCommand,
    SlashCommandProvider,
)
from vtx.tui2.clipboard import read_clipboard_image
from vtx.tui2.path_complete import PathComplete
from vtx.tui2.prompt_history import PromptHistory
from vtx.tui2.theme import settings

_PASTE_LINE_THRESHOLD = 5
_PASTE_CHAR_THRESHOLD = 500
# Matches the attachment autocomplete debounce.
_SLOW_AUTOCOMPLETE_DEBOUNCE_S = 0.02
_PASTE_MARKER_RE = re.compile(r"\[paste #(\d+)(?: (\+\d+ lines|\d+ chars))?\]")
_IMAGE_MARKER_RE = re.compile(r"\[image #(\d+)\]")
_MAX_ATTACHED_IMAGES = 5

MAX_ATTACHED_IMAGES = _MAX_ATTACHED_IMAGES
"""How many clipboard images one message may carry."""
_SKILL_TRIGGER_MARKER = "⁣"

PROMPT_GLYPH = "›"
"""The prompt marker drawn to the left of the editor."""

# Keys the editor must not swallow: the app binds all of them.
PASSTHROUGH_KEYS: tuple[str, ...] = (
    "enter",
    "escape",
    "esc",
    "tab",
    "up",
    "down",
    "ctrl+j",
    "ctrl+o",
    "ctrl+]",
    "ctrl+u",
    "ctrl+v",
    "alt+enter",
    "shift+enter",
)

__all__ = [
    "MAX_ATTACHED_IMAGES",
    "PASSTHROUGH_KEYS",
    "PROMPT_GLYPH",
    "AttachedImage",
    "InputBox",
    "Submission",
]


@dataclass(frozen=True, slots=True)
class AttachedImage:
    """An image pasted from the clipboard, referenced inline by ``[image #N]``."""

    id: int
    data: bytes
    mime_type: str

    def to_content_part(self) -> dict[str, str]:
        """The base64 content part handed to a model."""
        return {
            "type": "image",
            "media_type": self.mime_type,
            "data": base64.b64encode(self.data).decode("utf-8"),
        }


@dataclass(frozen=True, slots=True)
class Submission:
    """What :meth:`InputBox.submit` produced."""

    text: str
    """What the user block shows, with ``[image]`` placeholders intact."""

    query_text: str
    """What the model receives: markers expanded, images removed."""

    images: tuple[AttachedImage, ...] = ()
    selected_skill_name: str | None = None
    selected_skill_query: str | None = None
    steer: bool = False


@dataclasses.dataclass
class InputBox(Component):
    """Multi-line prompt with inline completion.

    - ``enter`` submits
    - ``ctrl+j`` / ``shift+enter`` inserts a newline
    - ``alt+enter`` submits as a steer
    - ``up``/``down`` browse history, or navigate the overlay while completing
    - ``@`` searches file paths, ``/`` slash commands, ``#`` pull requests
    - ``tab`` completes a path
    - ``escape`` cancels completion, else clears the prompt
    - ``ctrl+v`` attaches a clipboard image

    The overlay itself is owned by the app. This component only reports what it
    wants shown through the ``on_completion_*`` callbacks.
    """

    cwd: str = ""
    placeholder: str = "Ask anything…  / for commands, @ for files, # for PRs"
    background: str | None = None
    thinking_level: str = "none"

    editor: Input = field(default_factory=Input, init=False, repr=False)
    _history: PromptHistory = field(default_factory=PromptHistory, init=False, repr=False)
    _slash_provider: SlashCommandProvider = field(
        default_factory=lambda: SlashCommandProvider(DEFAULT_COMMANDS.copy()),
        init=False,
        repr=False,
    )
    _file_provider: FilePathProvider = field(
        default_factory=FilePathProvider, init=False, repr=False
    )
    _pr_provider: PullRequestProvider = field(
        default_factory=PullRequestProvider, init=False, repr=False
    )
    _providers: list[AutocompleteProvider] = field(default_factory=list, init=False, repr=False)
    _active_provider: AutocompleteProvider | None = field(default=None, init=False, repr=False)
    _completion_prefix: str = field(default="", init=False, repr=False)
    _is_completing: bool = field(default=False, init=False, repr=False)
    _autocomplete_enabled: bool = field(default=True, init=False, repr=False)
    _suppress_autocomplete: int = field(default=0, init=False, repr=False)
    _autocomplete_token: int = field(default=0, init=False, repr=False)
    _path_complete: PathComplete = field(default_factory=PathComplete, init=False, repr=False)
    _tab_completing: bool = field(default=False, init=False, repr=False)
    _tab_start_col: int = field(default=0, init=False, repr=False)
    _tab_base_fragment: str = field(default="", init=False, repr=False)
    _pastes: dict[int, str] = field(default_factory=dict, init=False, repr=False)
    _paste_counter: int = field(default=0, init=False, repr=False)
    _images: dict[int, AttachedImage] = field(default_factory=dict, init=False, repr=False)
    _image_counter: int = field(default=0, init=False, repr=False)
    _loading_image: bool = field(default=False, init=False, repr=False)
    _selected_skill_commands: list[str] = field(default_factory=list, init=False, repr=False)
    _last_text: str = field(default="", init=False, repr=False)
    _scroll_hint: str = field(default="", init=False, repr=False)

    # Callbacks the app installs.
    on_submit: Callable[[Submission], None] | None = None
    on_completion_update: Callable[[list[ListItem]], None] | None = None
    on_completion_hide: Callable[[], None] | None = None
    on_completion_select: Callable[[], None] | None = None
    on_completion_move: Callable[[int], None] | None = None
    on_search_update: Callable[[str], None] | None = None
    on_text_change: Callable[[str], None] | None = None
    on_bell: Callable[[], None] | None = None
    on_notify: Callable[[str, str], None] | None = None
    on_pending_paste: Callable[[Callable[[], None]], None] | None = None

    def component_post_init(self) -> None:
        if not self.cwd:
            self.cwd = os.getcwd()
        self.editor.multiline = True
        self.editor.input = True
        self.editor.auto_height = True
        self.editor.min_rows = 1
        self.editor.max_rows = 12
        self.editor.passthrough = PASSTHROUGH_KEYS
        self.editor.placeholder = self.placeholder
        self.editor.foreground = settings.colors.fg
        self.editor.tab_size = 4
        self._file_provider.set_cwd(self.cwd)
        self._pr_provider.set_cwd(self.cwd)
        self._providers = [self._slash_provider, self._file_provider, self._pr_provider]

    # -- Introspection ---------------------------------------------------

    @property
    def text(self) -> str:
        """The raw editor content, markers included."""
        return self.editor.value

    @property
    def is_completing(self) -> bool:
        """Whether the completion overlay is open."""
        return self._is_completing

    @property
    def is_tab_completing(self) -> bool:
        """Whether the overlay came from tab path completion."""
        return self._tab_completing

    @property
    def active_provider(self) -> AutocompleteProvider | None:
        """The provider whose suggestions are currently shown."""
        return self._active_provider

    @property
    def is_shell_command(self) -> bool:
        """Whether the prompt holds a ``!`` shell command."""
        return self.text.strip().startswith("!")

    @property
    def attached_images(self) -> tuple[AttachedImage, ...]:
        """Images attached to the pending message."""
        return tuple(self._images.values())

    @property
    def editor_area(self) -> Any:
        """The last painted area of the editor, for cursor placement."""
        return getattr(self, "_editor_area", None)

    # -- Setters ---------------------------------------------------------

    def set_commands(self, commands: list[SlashCommand]) -> None:
        """Replace the slash-command list."""
        self._slash_provider.commands = commands

    def set_fd_path(self, fd_path: str | None) -> None:
        """Point the file provider at an ``fd`` binary (or ``None``)."""
        self._file_provider.set_fd_path(fd_path)

    def set_file_paths(self, paths: list[str]) -> None:
        """Seed the file provider with a pre-scanned path list."""
        self._file_provider.set_paths(paths)

    def set_cwd(self, cwd: str) -> None:
        """Move the editor's working directory."""
        self.cwd = cwd
        self._file_provider.set_cwd(cwd)
        self._pr_provider.set_cwd(cwd)
        self._path_complete.clear_cache()

    def set_autocomplete_enabled(self, enabled: bool) -> None:
        """Turn inline completion on or off."""
        self._autocomplete_enabled = enabled

    def set_placeholder(self, value: str) -> None:
        """Change the empty-prompt hint."""
        self.placeholder = value
        self.editor.placeholder = value

    def set_completing(self, is_completing: bool) -> None:
        """Open or close completion from the app side (a selection mode)."""
        self._is_completing = is_completing
        if is_completing:
            return
        # Invalidate any in-flight slow request so it can't reopen a list the
        # user just dismissed.
        self._autocomplete_token += 1
        self._active_provider = None
        self._completion_prefix = ""
        self._tab_completing = False
        self._tab_start_col = 0
        self._tab_base_fragment = ""

    def set_thinking_level(self, level: str) -> None:
        """Tint the editor background by reasoning level."""
        from vtx.tui2.styles import get_chrome

        self.thinking_level = level
        chrome = get_chrome()
        self.editor.background = chrome.thinking_backgrounds.get(level, chrome.colors.editor)

    # -- Editing ---------------------------------------------------------

    def clear(self, *, reset_pastes: bool = True) -> None:
        """Empty the editor and drop pending attachments."""
        self.editor.content = ""
        self.editor.cursor = 0
        self._selected_skill_commands.clear()
        self._scroll_hint = ""
        self._last_text = ""
        if reset_pastes:
            self._reset_pastes()
            self._reset_images()

    def insert(self, text: str) -> None:
        """Insert text at the caret.

        The editor's native buffer is the source of truth for the caret, so the
        insert has to go through it — assigning ``content`` would move the text
        without moving the cursor with it.
        """
        editor = self.editor._editor
        if editor is None:
            self.editor.content = (self.editor.value or "") + text
            self.editor.cursor = len(self.editor.content)
            return
        editor.insert_text(text)
        self.editor.content = editor.text()

    def refresh_theme(self) -> None:
        """Re-apply palette colors after a theme change."""
        self.editor.foreground = settings.colors.fg

    def replace_text(self, text: str, cursor_col: int | None = None) -> None:
        """Replace the whole editor content and place the caret.

        Used by the app to restore a prompt or load history, not by the user's
        own typing. The change handler is not re-entered: ``_last_text`` is
        moved to the new value first, so the next real keystroke — not this
        programmatic write — decides whether completion opens.
        """
        self.editor.content = text
        self.editor.cursor = (
            len(text) if cursor_col is None else max(0, min(cursor_col, len(text)))
        )
        self._last_text = text

    def notify(self, message: str, severity: str = "warning") -> None:
        """Report a transient message through the app."""
        if self.on_notify is not None:
            self.on_notify(message, severity)

    def bell(self) -> None:
        """Signal a rejected keystroke."""
        if self.on_bell is not None:
            self.on_bell()

    # -- Paste compaction ------------------------------------------------

    def _transform_paste(self, pasted_text: str) -> str:
        """Compact a large paste into a ``[paste #N]`` marker."""
        normalized = pasted_text.replace("\r\n", "\n").replace("\r", "\n")
        filtered = "".join(char for char in normalized if char == "\n" or ord(char) >= 32)
        line_count = len(filtered.split("\n"))
        char_count = len(filtered)

        if line_count > _PASTE_LINE_THRESHOLD or char_count > _PASTE_CHAR_THRESHOLD:
            self._paste_counter += 1
            paste_id = self._paste_counter
            self._pastes[paste_id] = filtered
            if line_count > _PASTE_LINE_THRESHOLD:
                return f"[paste #{paste_id} +{line_count} lines]"
            return f"[paste #{paste_id} {char_count} chars]"

        return filtered

    def _expand_paste_markers(self, text: str) -> str:
        def replace_match(match: re.Match[str]) -> str:
            paste_id = int(match.group(1))
            return self._pastes.get(paste_id, match.group(0))

        return _PASTE_MARKER_RE.sub(replace_match, text)

    def _reset_pastes(self) -> None:
        self._pastes.clear()
        self._paste_counter = 0

    def _reset_images(self) -> None:
        self._images.clear()
        self._image_counter = 0

    # -- Image attachment ------------------------------------------------

    def attach_clipboard_image(self) -> None:
        """Attach the clipboard image at the caret as an ``[image #N]`` marker.

        Runs the clipboard read on a worker thread when the app supplies
        :attr:`on_pending_paste`; otherwise it reads inline, which is only safe
        in tests.
        """
        if self._loading_image or len(self._images) >= _MAX_ATTACHED_IMAGES:
            if len(self._images) >= _MAX_ATTACHED_IMAGES:
                self.notify(f"Image limit reached ({_MAX_ATTACHED_IMAGES} per message)")
            return

        def work() -> None:
            grabbed = read_clipboard_image()
            if grabbed is None:
                self.notify("No image found on clipboard")
                return
            data, mime_type = grabbed
            self._image_counter += 1
            image_id = self._image_counter
            self._images[image_id] = AttachedImage(id=image_id, data=data, mime_type=mime_type)
            self._suppress_autocomplete = 1
            self.insert(f"[image #{image_id}] ")

        self._loading_image = True
        if self.on_pending_paste is None:
            try:
                work()
            finally:
                self._loading_image = False
            return
        self.on_pending_paste(work)

    def _extract_attached_images(self, text: str) -> tuple[str, str, list[AttachedImage]]:
        """Split text around ``[image #N]`` markers.

        Returns ``(display, plain, images)``: display keeps an ``[image]``
        placeholder where each attached image sat (so the sent user block still
        shows the attachment), plain drops the markers entirely (the model
        receives the actual image content parts). Unresolved ids are untouched.
        """
        images: list[AttachedImage] = []
        display_parts: list[str] = []
        plain_parts: list[str] = []
        cursor = 0
        for match in _IMAGE_MARKER_RE.finditer(text):
            image = self._images.get(int(match.group(1)))
            if image is None:
                continue
            segment = text[cursor : match.start()]
            display_parts.append(segment)
            plain_parts.append(segment)
            display_parts.append("[image]")
            images.append(image)
            cursor = match.end()
        tail = text[cursor:]
        display_parts.append(tail)
        plain_parts.append(tail)
        return "".join(display_parts), "".join(plain_parts), images

    # -- Skill triggers --------------------------------------------------

    def _strip_skill_markers(self, text: str) -> str:
        return text.replace(_SKILL_TRIGGER_MARKER, "")

    def _extract_selected_skill_submission(self, text: str) -> tuple[str | None, str | None]:
        pattern = re.compile(rf"{_SKILL_TRIGGER_MARKER}/skill:([a-z0-9-]+){_SKILL_TRIGGER_MARKER}")
        match = pattern.search(text)
        if not match:
            return None, None

        skill_name = match.group(1)
        if skill_name not in self._selected_skill_commands:
            return None, None

        query = (text[: match.start()] + text[match.end() :]).strip()
        return skill_name, self._strip_skill_markers(query)

    # -- Text change -----------------------------------------------------

    def _cursor_offset(self, text: str) -> int:
        """Caret position as an offset into ``text``."""
        row, col = self.editor._editor.cursor() if self.editor._editor else (0, len(text))
        lines = text.split("\n")
        if not lines:
            return 0
        if row <= 0:
            return max(0, min(col, len(lines[0])))
        clamped_row = min(row, len(lines) - 1)
        prefix_len = sum(len(line) + 1 for line in lines[:clamped_row])
        return prefix_len + max(0, min(col, len(lines[clamped_row])))

    def notify_text_changed(self) -> None:
        """Report an edit: drive completion, notify the app, update state.

        Called from :meth:`compose` every frame and safe to call directly, so a
        host that mutates the editor without a paint (tests, history restore)
        still gets the same behavior. Repeat calls with unchanged text are
        ignored, which keeps the streaming update coalesced to one per frame.
        """
        text = self.text
        if text == self._last_text:
            return
        self._last_text = text

        if self.on_text_change is not None:
            self.on_text_change(text)

        # Skip autocomplete if we just applied a completion.
        if self._suppress_autocomplete > 0:
            self._suppress_autocomplete -= 1
            return

        if not self._autocomplete_enabled:
            # When completing with autocomplete disabled (a selection mode),
            # route text to the overlay's search layer instead.
            if self._is_completing and self.on_search_update is not None:
                self.on_search_update(text)
            return

        self._try_autocomplete()

    # Backwards-compatible alias for the internal name used by the port.
    _handle_text_change = notify_text_changed

    def _try_autocomplete(self) -> None:
        text = self.text
        cursor_col = self._cursor_offset(text)

        for provider in self._providers:
            if not provider.should_trigger(text, cursor_col):
                continue
            if provider.slow:
                # ``get_suggestions`` shells out (fd ~20ms, gh ~800ms on a
                # cold cache). Off-loop it, and drop the result if the text
                # moved on while it ran.
                self._autocomplete_token += 1
                self._run_slow_suggestions(provider, text, cursor_col, self._autocomplete_token)
                return
            self._apply_suggestions(provider, provider.get_suggestions(text, cursor_col))
            return

        self._hide_completion()

    def _run_slow_suggestions(
        self, provider: AutocompleteProvider, text: str, cursor_col: int, token: int
    ) -> None:
        """Debounce then compute a slow provider's suggestions off the loop.

        ``fd`` costs ~20ms and ``gh`` ~800ms on a cold cache. Running either
        inline froze the UI on every keystroke, so the work is handed to the
        app's scheduler and the token check drops a result the user has already
        typed past.
        """
        scheduler = self.on_pending_paste
        if scheduler is None:
            # No scheduler (tests): compute inline rather than silently
            # dropping the suggestions.
            self._accept_slow_result(provider, token, provider.get_suggestions(text, cursor_col))
            return

        def work() -> None:
            # 20ms: coalesces a burst of keystrokes into one subprocess while
            # staying under the threshold of feeling laggy.
            result = provider.get_suggestions(text, cursor_col)
            self._accept_slow_result(provider, token, result)

        def after_delay() -> None:
            scheduler(work)

        timer = threading.Timer(_SLOW_AUTOCOMPLETE_DEBOUNCE_S, after_delay)
        timer.daemon = True
        timer.start()

    def _accept_slow_result(
        self, provider: AutocompleteProvider, token: int, result: CompletionResult | None
    ) -> None:
        if token != self._autocomplete_token or not self._autocomplete_enabled:
            return
        self._apply_suggestions(provider, result)

    def _apply_suggestions(
        self, provider: AutocompleteProvider, result: CompletionResult | None
    ) -> None:
        if not result or not result.items:
            self._hide_completion()
            return
        self._active_provider = provider
        self._completion_prefix = result.prefix
        self._is_completing = True
        if self.on_completion_update is not None:
            self.on_completion_update(result.items)

    def _hide_completion(self) -> None:
        if not self._is_completing:
            return
        self._is_completing = False
        self._active_provider = None
        self._completion_prefix = ""
        if self.on_completion_hide is not None:
            self.on_completion_hide()

    # -- Key actions -----------------------------------------------------

    def move_completion(self, delta: int) -> None:
        """Route an arrow key to the overlay while completing."""
        if self._is_completing and self.on_completion_move is not None:
            self.on_completion_move(delta)

    def request_completion_select(self) -> None:
        """Accept the highlighted completion."""
        if self._is_completing and self.on_completion_select is not None:
            self.on_completion_select()

    def action_submit(self) -> Submission | None:
        """Build the submission, clear the prompt, and hand it to the app."""
        if self._is_completing:
            self.request_completion_select()
            return None

        raw_text = self.text.strip()
        display_base, plain_text, images = self._extract_attached_images(raw_text)
        if not display_base.strip() and not images:
            return None

        display_text = display_base.strip() or "[image]"
        query_text = self._expand_paste_markers(plain_text.strip())
        skill_name, skill_query = self._extract_selected_skill_submission(query_text)
        display_text = self._strip_skill_markers(display_text)
        query_text = self._strip_skill_markers(query_text)

        submission = Submission(
            text=display_text,
            query_text=query_text,
            images=tuple(images),
            selected_skill_name=skill_name,
            selected_skill_query=skill_query,
        )
        if query_text:
            self._history.append(query_text)
        self.clear(reset_pastes=True)
        if self.on_submit is not None:
            self.on_submit(submission)
        return submission

    def action_steer_submit(self) -> Submission | None:
        """Submit without cancelling the running turn."""
        if self._is_completing:
            self._is_completing = False
            self._active_provider = None
            self._completion_prefix = ""
            if self.on_completion_hide is not None:
                self.on_completion_hide()

        raw_text = self.text.strip()
        if not raw_text:
            return None
        display_base, plain_text, images = self._extract_attached_images(raw_text)
        display_text = display_base.strip() or "[image]"
        query_text = self._strip_skill_markers(self._expand_paste_markers(plain_text.strip()))
        submission = Submission(
            text=display_text, query_text=query_text, images=tuple(images), steer=True
        )
        if self.on_submit is not None:
            self.on_submit(submission)
        return submission

    def action_newline(self) -> None:
        """Insert a literal newline."""
        self.insert("\n")

    def action_cancel(self) -> bool:
        """Cancel completion or clear the prompt.

        Returns ``True`` when the key was fully consumed, so the app can fall
        through to its own interrupt handling.
        """
        if self._is_completing:
            self._is_completing = False
            self._active_provider = None
            self._completion_prefix = ""
            if self.on_completion_hide is not None:
                self.on_completion_hide()
            return True
        if self.text:
            self.clear()
            return True
        return False

    def action_cursor_up(self) -> bool:
        """Move up a line, or browse history at the top."""
        row = self.editor._editor.cursor()[0] if self.editor._editor else 0
        if row > 0:
            return False
        self._history_navigate(-1)
        return True

    def action_cursor_down(self) -> bool:
        """Move down a line, or browse history at the bottom."""
        editor = self.editor._editor
        row, _ = editor.cursor() if editor else (0, 0)
        total_lines = editor.lines() if editor else 1
        if row < total_lines - 1:
            return False
        self._history_navigate(1)
        return True

    def action_tab_complete(self) -> None:
        """Accept the highlighted completion, or complete a filesystem path.

        Tab used to only move the highlight, so the slash/at dropdowns could be
        accepted with Enter but not Tab — the key every other shell uses to
        accept a completion. Enter keeps submitting the message.
        """
        if self._is_completing:
            self.request_completion_select()
            return
        self._do_tab_complete()

    def _do_tab_complete(self) -> None:
        row, col = self.editor._editor.cursor() if self.editor._editor else (0, 0)
        text = self.text
        lines = text.split("\n")
        if row >= len(lines):
            return
        line = lines[row]
        text_before_cursor = line[:col]

        path_fragment, start_col = PathComplete.extract_path_fragment(text_before_cursor)
        if not path_fragment or start_col < 0:
            # No path to complete - insert a literal tab (spaces).
            self._suppress_autocomplete = 1
            self.insert("    ")
            return

        completion, alternatives = self._path_complete(self.cwd, path_fragment)

        if not completion and not alternatives:
            self.bell()
            return

        if completion and not alternatives:
            # Unique completion - insert directly.
            self._suppress_autocomplete = 1
            self.insert(completion)
            if not completion.endswith(os.sep):
                self.insert(" ")
            return

        # Multiple alternatives - show the overlay, first inserting any common
        # prefix so the shown list matches what is on screen.
        if completion:
            self._suppress_autocomplete = 1
            self.insert(completion)

        base_fragment = PathComplete.get_base_path(path_fragment + completion)
        items = [
            ListItem(value=alt, label=alt, description=base_fragment if base_fragment else ".")
            for alt in alternatives[:20]
        ]

        self._tab_completing = True
        self._tab_start_col = start_col
        self._tab_base_fragment = base_fragment
        self._is_completing = True
        if self.on_completion_update is not None:
            self.on_completion_update(items)

    # -- Applying a selection --------------------------------------------

    def apply_slash_command(self, item: ListItem) -> None:
        """Accept a slash command: submit it, or splice it into the prompt."""
        cmd: SlashCommand = item.value
        self._is_completing = False
        self._active_provider = None

        if cmd.submit_on_select and not cmd.is_skill:
            self._completion_prefix = ""
            self._suppress_autocomplete = 1
            self.clear(reset_pastes=True)
            if self.on_submit is not None:
                self.on_submit(Submission(text=f"/{cmd.name}", query_text=f"/{cmd.name}"))
            return

        prefix = self._completion_prefix
        self._completion_prefix = ""
        new_text, _ = self._slash_provider.apply_completion(
            self.text, self._cursor_offset(self.text), item, prefix
        )

        if cmd.is_skill:
            if cmd.name not in self._selected_skill_commands:
                self._selected_skill_commands.append(cmd.name)
            marker_wrapped = f"{_SKILL_TRIGGER_MARKER}/skill:{cmd.name}{_SKILL_TRIGGER_MARKER} "
            plain = f"/skill:{cmd.name} "
            if plain in new_text:
                new_text = new_text.replace(plain, marker_wrapped, 1)

        self._suppress_autocomplete = 1
        self.editor.content = new_text
        self.editor.cursor = len(new_text)

    def apply_provider_completion(self, item: ListItem) -> None:
        """Accept a suggestion from the active provider."""
        provider = self._active_provider
        if provider is None:
            return
        new_text, _ = provider.apply_completion(
            self.text, self._cursor_offset(self.text), item, self._completion_prefix
        )
        self._is_completing = False
        self._active_provider = None
        self._completion_prefix = ""
        self._suppress_autocomplete = 1
        self.editor.content = new_text
        self.editor.cursor = len(new_text)

    def apply_file_completion(self, item: ListItem) -> None:
        """Accept a file-path suggestion."""
        self.apply_provider_completion(item)

    def apply_tab_path_completion(self, item: ListItem) -> None:
        """Accept a tab path-completion suggestion."""
        selected_path: str = item.value
        cursor_col = self._cursor_offset(self.text)

        new_path = self._tab_base_fragment + selected_path
        if " " in new_path and not new_path.startswith('"'):
            new_path = f'"{new_path}"'

        is_dir = selected_path.endswith(("/", os.sep))
        suffix = "" if is_dir else " "

        new_text = self.text[: self._tab_start_col] + new_path + suffix + self.text[cursor_col:]

        self._is_completing = False
        self._tab_completing = False
        self._tab_start_col = 0
        self._tab_base_fragment = ""
        self._suppress_autocomplete = 1
        self.editor.content = new_text
        self.editor.cursor = len(new_text[: self._tab_start_col]) + len(new_path) + len(suffix)

    # -- History ---------------------------------------------------------

    def _history_navigate(self, direction: int) -> None:
        result = self._history.navigate(direction, self.text)
        if result is None:
            return
        self._suppress_autocomplete = 1
        self.editor.content = result
        self.editor.cursor = len(result)

    # -- Painting --------------------------------------------------------

    def _shell_command_style(self) -> str | None:
        return settings.colors.success if self.is_shell_command else None

    def get_size(self, ctx: ComponentRenderContext[Any]) -> Size:
        """Report the editor's grown height, plus a hint row when one shows.

        The editor itself reports its wrapped height through
        ``auto_height``; the surrounding component has to add the panel padding
        and the optional hint line, or the prompt would be measured as zero
        rows tall and collapse in the layout.
        """
        rows = self.editor.get_size(ctx).height
        if _hint_text(self) is not None:
            rows += 1
        return Size(width=ctx.area.width, height=rows + 2)

    def compose(self, ctx: ComponentRenderContext[Any]) -> Panel:
        self._editor_area = ctx.area
        self.notify_text_changed()

        c = settings.colors
        shell = self.is_shell_command
        marker = Run(
            text=PROMPT_GLYPH,
            foreground=c.success if shell else c.fg,
            modifiers=() if shell else ("bold",),
        )
        editor_lines = tuple(
            (Run(text=line, foreground=c.fg),) for line in self.editor.value.split("\n")
        ) or ((Run(text=""),),)

        hint = _hint_text(self)
        children: list[Any] = [
            TextBlock(lines=editor_lines, wrap=True)
            if marker is None
            else _hanging_indent(marker, editor_lines)
        ]
        if hint is not None:
            children.append(hint)

        return Panel(
            child=Stack(children=tuple(children), direction="vertical", gap=0),
            background=self.editor.background,
            padding=(0, 1, 0, 1),
        )


def _hanging_indent(marker: Run, lines: tuple[tuple[Run, ...], ...]) -> TextBlock:
    """Put ``marker`` on the first line and indent every later line under it.

    The prompt glyph hangs in the left margin rather than occupying a column, so
    the editor keeps the full width — the same look the Textual build got from
    a ``#input-prefix`` label beside the text area.
    """
    gutter = marker.text + " "
    width = len(gutter)
    rows: list[tuple[Run, ...]] = []
    for index, line in enumerate(lines):
        if index == 0:
            rows.append((marker, *line))
        else:
            rows.append((Run(text=" " * width), *line))
    return TextBlock(lines=tuple(rows), wrap=False)


def _hint_text(box: InputBox) -> TextBlock | None:
    """The status hint under the editor, or ``None`` when nothing applies."""
    c = settings.colors
    if box.is_shell_command:
        return TextBlock(
            lines=((Run(text="  running as a shell command", foreground=c.success),),)
        )
    if box.is_completing:
        return TextBlock(
            lines=((Run(text="  tab or enter to accept · esc to dismiss", foreground=c.dim),),)
        )
    if box._scroll_hint:
        return TextBlock(lines=((Run(text=f"  {box._scroll_hint}", foreground=c.dim),),))
    return None
