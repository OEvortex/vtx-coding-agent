# ctx.term — terminal UI building blocks

`term` ships the product-neutral half of the `ctx.term` namespace: Textual
widgets, scrollback blocks, formatting, and input primitives. The coding
agent app, its slash commands, its panels, and every tool-specific block
live in `ctx-code` and join the same namespace through
`pkgutil.extend_path`.

The split is a one-way dependency: `ctx-code` imports these building blocks;
this package never imports `ctx-code`. In particular, `ToolBlock` knows
nothing about individual tools — it renders the generic `ui_summary`,
`ui_details`, and `ui_data` fields and exposes hooks a product block can
override (`_source_path`, `_render_custom_output`, `_streaming_body_text`,
`_format_call_detail`, `apply_task_progress`, and the `show_ask_user`
family).

## Usage

Reuse the primitives to build your own app. An `InputBox` decides *what*
should be suggested and posts messages; the app owns the `FloatingList`
overlay and the provider dispatch, because only the app knows how a picked
item should behave.

```python
import asyncio

from textual.app import App, ComposeResult
from textual.containers import VerticalScroll

from ctx.term import FloatingList, InputBox
from ctx.term.autocomplete import FilePathProvider, PullRequestProvider, SlashCommandProvider


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

Typing `/mod` puts `/model` in the overlay and `enter` submits it. `InputBox`
is a `Vertical` wrapping a `TextArea` subclass (`Ctx`), and owns paste
compaction, image attachment via `ctrl+v`, shell-command styling, tab path
completion, and history navigation. It does not own the overlay widget.

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
- `PromptHistory` is append-only, persisted to `~/.ctx/prompt-history.jsonl`, capped at `MAX_HISTORY_ENTRIES` (50). `append`, `navigate`, `is_browsing` are the API.
- `SelectionMode` is a `StrEnum` naming every picker the app can be in (session, model, theme, login, permissions, thinking, settings, tree, provider). It is the shared vocabulary between the app and its overlays.

## Rendering

- `blocks` holds the generic scrollback widgets: `ToolBlock`, `ThinkingBlock`, `ContentBlock`, `UserBlock`, `RunSummaryBlock`, `HandoffLinkBlock`, `UpdateAvailableBlock`, `LaunchWarningsBlock`, `CompactionBlock`, and `UsageBlock`, plus `LaunchWarning` and `stylize_badge_markers`. Tool-specific blocks (`read`, `apply_patch`, `bash`, `ask_user`, `delegate_subagent`, `todo`, `codemode`, …) live in `ctx.term.tool_blocks` in `ctx-code`. The private `_StreamingMarkdownMixin` splits completed text at stable block boundaries and caches the closed prefix, so a streaming delta re-renders only the open tail.
- `chat.ChatLog` is the generic scrollback pane. It mounts whatever block class `tool.ui_block` selects and forwards progress/events through block hooks; it does not recognise tool names.
- `formatting` renders markdown for the chat: `format_markdown(text, width=None)` and `format_markdown_block(text, width)` return `rich.text.Text`, `find_stable_block_boundary` is what the streaming mixin uses to pick a safe split point, and `strip_markdown_for_collapsed_text` and `markdown_render_width` produce the collapsed form. `format_bash_command` styles a shell command, `format_python_source` highlights a script as indented `Text` for a notebook cell, and `format_tokens` formats a token count. `MARKDOWN_THEME` and `CustomMarkdown` are the theme.
- `tool_output` (collapsed-output truncation: `escape_tool_output_text`, `truncate_tool_output_text`, `format_expand_hint`) and `task_ui` (the `delegate_subagent` glyphs and renderers) moved to `ctx-code`: they belong with the tools that produce them.
- `latex.preprocess_latex(text)` converts inline and display math to Unicode. Adapted from innomd by Innomatica GmbH (MIT).
- `diff_display` provides `blend_hex` and `DIFF_BG_PAD_MARKER`, for tinting diff backgrounds into the configured palette.

## Styling, clipboard, and dialogs

- `get_styles()` returns the app CSS built from the configured palette; `styles.STYLES` is the module-level cache of it. Call `get_styles()` again after a theme change.
- `copy_to_clipboard(text)` and `read_clipboard_image()` handle X11, Wayland, macOS, and Windows.
- `status_lines` owns the witty status lines and the generic live-activity probe (`set_live_activity_probe`). The delegate_subagent registry in `ctx-code` registers itself here, so this module stays product-neutral.

## Notes on use

- `fuzzy_match(query, text)` returns `(score, positions)` where **higher is better** and `NO_MATCH` (0.0) means no match - inverted from the pi-mono convention, so the descending sorts already in the callers keep working. `fuzzy_filter(items, query, get_text)` applies it. Tokens split on whitespace and `/`; ties keep input order.

## Where the app lives

The interactive app, its mixins (`agent_runner`, `session_ui`, `startup`, `recap`, `completion_ui`, `queue_ui`), the slash-command registry, the panels (`agents_panel`, `todo_panel`), the session browser, and every tool block are in `ctx-code` under `ctx/term/`. See that package's README.
