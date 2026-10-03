# vtx.tui

One flat package with the whole Textual interface: the reusable primitives (text entry, inline completion, scrollback blocks, rich-text formatting) and the app that composes them. The harness `vtx.agent` depends on this package for `blocks.TaskToolBlock` and for `tool_output` truncation.

Two ways in. Reuse the primitives to build your own app — everything above "The app" is app-agnostic. Or run the shipped one: `from vtx.tui import Vtx, run_tui`.

## Usage

The pieces are meant to be composed. An `InputBox` decides *what* should be suggested and posts messages; the app owns the `FloatingList` overlay and the provider dispatch, because only the app knows how a picked item should behave.

```python
import asyncio

from textual.app import App, ComposeResult
from textual.containers import VerticalScroll

from vtx.tui import FloatingList, InputBox
from vtx.tui.autocomplete import FilePathProvider, PullRequestProvider, SlashCommandProvider


class Demo(App):
    CSS = "Screen { layout: vertical; }"

    def compose(self) -> ComposeResult:
        yield VerticalScroll(id="chat")
        yield FloatingList(id="overlay")
        yield InputBox()

    def on_mount(self) -> None:
        self.query_one(InputBox).focus()

    def on_input_box_submitted(self, event: InputBox.Submitted) -> None:
        print("submitted:", event.query_text)

    def on_input_box_completion_update(self, event: InputBox.CompletionUpdate) -> None:
        self.query_one(FloatingList).show(event.items)

    def on_input_box_completion_hide(self) -> None:
        self.query_one(FloatingList).hide()

    def on_input_box_completion_move(self, event: InputBox.CompletionMove) -> None:
        overlay = self.query_one(FloatingList)
        overlay.move_up() if event.delta < 0 else overlay.move_down()

    def on_input_box_completion_select(self) -> None:
        box = self.query_one(InputBox)
        overlay = self.query_one(FloatingList)
        item = overlay.selected_item
        overlay.hide()
        if item is None:
            return
        provider = box.active_provider
        if isinstance(provider, SlashCommandProvider):
            box.apply_slash_command(item)
        elif isinstance(provider, FilePathProvider | PullRequestProvider):
            box.apply_provider_completion(item)
        box.set_completing(False)


asyncio.run(Demo().run())
```

Typing `/mod` puts `/model` in the overlay and `enter` submits it. `InputBox` is a `Vertical` wrapping a `TextArea` subclass (`Vtx`), and owns paste compaction, image attachment via `ctrl+v`, shell-command styling, tab path completion, and history navigation. It does not own the overlay widget.

## Text entry and completion

- `InputBox` is the multi-line editor. Bindings: `enter` submits, `ctrl+j` / `shift+enter` newline, `alt+enter` steer, `ctrl+v` paste image, `escape` cancel completion or clear, `up`/`down` history when not completing and list navigation when completing, `tab` path completion. `set_cwd`, `set_commands`, `set_fd_path`, `set_file_paths`, `set_autocomplete_enabled`, and `active_provider` are the setters the app uses. Messages: `Submitted`, `CompletionUpdate`, `CompletionHide`, `CompletionSelect`, `CompletionMove`, `SearchUpdate`, and `ScrollInfo`.
- `AutocompleteProvider` is the abstract base: `trigger_chars`, `should_trigger(text, cursor_col)`, `get_suggestions(...) -> CompletionResult | None`, `apply_completion(...) -> (text, cursor_col)`, and the `slow` flag. Set `slow = True` when `get_suggestions` shells out; the input box then debounces keystrokes and runs it off the event loop, and drops a late result that no longer matches the text.
- `CompletionResult` is `items`, `prefix`, and `replace_start` (the column the replacement starts at).
- `SlashCommandProvider` triggers on `/` at the start of input and is configured with a list of `SlashCommand(name, description, shortcut, is_skill)`.
- `FilePathProvider` triggers on `@`, uses `fd` when available and falls back to a directory walk, and takes an explicit path list via `set_paths`.
- `PullRequestProvider` triggers on `#` and lists open pull requests through `gh`.
- `DEFAULT_COMMANDS` is the command list the input box starts with. Replace it with `InputBox.set_commands` in a product that has its own router.
- `FloatingList` and `ListItem(value, label, description, prefix, prefix_style)` are the overlay. It is hidden until `show(items)` and paginates with an arrow indicator and a position counter; `show(items, searchable=True)` adds a second filter layer over the full item set. The parent owns `show`/`hide`, calls `move_up()`/`move_down()`, and reads `selected_item`. `hide()` clears the items, so read the selection before hiding it.
- `PathComplete` is the tab-completion engine: tilde expansion, a directory-listing cache (`clear_cache`, `invalidate`), and a longest-common-prefix replacement when several paths match. `extract_path_fragment(text)` and `get_base_path(fragment)` are static helpers.
- `PromptHistory` is append-only, persisted to `~/.vtx/prompt-history.jsonl`, capped at `MAX_HISTORY_ENTRIES` (50). `append`, `navigate`, `is_browsing` are the API.
- `SelectionMode` is a `StrEnum` naming every picker the app can be in (session, model, theme, login, permissions, thinking, settings, tree, provider, and the four goal modes). It is the shared vocabulary between the app and its overlays.

## Rendering

- `blocks` holds the scrollback widgets: `UserBlock`, `ContentBlock`, `ToolBlock`, `TaskToolBlock`, `ThinkingBlock`, `HandoffLinkBlock`, `UpdateAvailableBlock`, `LaunchWarningsBlock`, `CompactionBlock`, plus `LaunchWarning` and `stylize_badge_markers`. `TaskToolBlock` and `CompactionBlock` are not in `vtx.tui.__all__`; import them from `vtx.tui.blocks`. The private `_StreamingMarkdownMixin` splits completed text at stable block boundaries and caches the closed prefix, so a streaming delta re-renders only the open tail.
- `formatting` renders markdown for the chat: `format_markdown(text, width=None)` and `format_markdown_block(text, width)` return `rich.text.Text`, `find_stable_block_boundary` is what the streaming mixin uses to pick a safe split point, and `strip_markdown_for_collapsed_text` and `markdown_render_width` produce the collapsed form. `format_bash_command` styles a shell command and `format_tokens` formats a token count. `MARKDOWN_THEME` and `CustomMarkdown` are the theme.
- `task_ui` is pure rich-text formatting despite the name - there is no widget. `GLYPHS`, `SPINNER`, `TOOL_DISPLAY`, and `TOOL_NOUNS` are the tables; `render_finished`, `render_receipt`, and `render_background` return `Text` for a sub-agent's output; `format_turns`, `format_tool_breakdown`, `format_ms`, `format_tokens`, and `stats_parts` build status lines; `extract_summary_line`, `describe_activity`, `detect_files_referenced`, and `short_model` derive one-line descriptions from plain data.
- `latex.preprocess_latex(text)` converts inline and display math to Unicode. Adapted from innomd by Innomatica GmbH (MIT).
- `tool_output` holds the collapsed-output view: `escape_tool_output_text`, `truncate_tool_output_text`, and `format_expand_hint` (the `ctrl+o` hint). `vtx.agent.tools.web` uses this too.
- `diff_display` provides `blend_hex` and `DIFF_BG_PAD_MARKER`, for tinting diff backgrounds into the configured palette.

## Styling, clipboard, and dialogs

- `get_styles()` returns the app CSS built from the configured palette; `styles.STYLES` is the module-level cache of it. Call `get_styles()` again after a theme change.
- `copy_to_clipboard(text)` and `read_clipboard_image()` handle X11, Wayland, macOS, and Windows.
- `AskUserDialog` is the questionnaire state machine behind the `ask_user` dialog. It is pure Python with no Textual imports: the UI layer feeds it keypresses and renders `rows()`. One dialog owns 1-4 questions plus an optional Submit review tab; `handle_key(key, custom_value=...)` returns whether it consumed the key, and `build_answers()` / `build_response()` produce the result. The full keyboard contract is in the module docstring.

## The app

Everything above is primitives. The rest composes them into the coding agent's interactive interface.

- `app.Vtx` is the `App`: bindings, CSS, key routing, and the mixin stack. `run_tui(args)` is the console entry point.
- The app is split into mixins so each concern owns its own state: `agent_runner.AgentRunnerMixin` (streaming a turn, tool dispatch, approval, background wakeups), `session_ui.SessionUIMixin` (new/clear/resume/compact), `startup.StartupMixin` (tool install, update check, launch warnings), `recap.RecapMixin` (idle session recap), `completion_ui.CompletionUIMixin` (wiring `InputBox` providers to the overlay).
- `chat.ChatLog` is the scrollback pane that appends blocks and tracks streaming state.
- `widgets` holds `InfoBar` (model, tokens/context meter, branch, permission mode, file changes), `StatusLine` (spinner, witty line, exit hints), `QueueDisplay`, and `FileChangesModal`.
- `tree.TreeSelector` is the `/tree` session browser; `agents_panel.AgentsPanel` is the pinned per-sub-agent panel; `goal_ui` is the goal dashboard and beacon.
- `commands/` is the slash-command registry: `base.CommandSupport` is the duck-typed mixin base, and `CommandsMixin` combines `settings`, `models`, `sessions`, `auth`, `providers`, `agents`, `switch`, `update`, `reload`, `mcp`, and `goals`. Each module is one `CommandSupport` subclass.
- `app_protocol.Vtx` is the Protocol the mixins are typed against; depend on it, not the concrete App, to write your own.
- `export.export_session_html(path)` renders a session JSONL to a standalone HTML transcript.
- `extension_ui.TextualExtensionUI` is the modal screens extensions get to declare their own UI.

## Notes on use

- `fuzzy_match(query, text)` returns `(score, positions)` where **higher is better** and `NO_MATCH` (0.0) means no match - inverted from the pi-mono convention, so the descending sorts already in the callers keep working. `fuzzy_filter(items, query, get_text)` applies it. Tokens split on whitespace and `/`; ties keep input order.

## Tests

```bash
uv run --no-sync python -m pytest -p no:cacheprovider tests/ui -q
```

`tests/ui` covers `autocomplete`, `floating_list`, `latex`, `prompt_history`, `styles`, and `task_ui` directly, and reaches the widgets (`input.py`, `blocks.py`) through the app, which is the only way they are runnable. `tests/test_tui_fuzzy.py` covers `fuzzy`.