# vtx.tui

The base terminal-UI toolkit: reusable Textual primitives with no knowledge of the agent, the model catalog, sessions, goals, or slash commands.

This is the **base** half of a two-layer split. The product interface lives in `vtx.coding_agent.tui`, and the dependency runs one way only. `vtx.tui` never imports `vtx.coding_agent`, which is load-bearing rather than cosmetic: the harness (`vtx.agent`) already depends on this package for `blocks.TaskToolBlock` and for `tool_output` truncation, so a base-to-product edge would mean the harness imports the product.

```
vtx.tui          base: primitives, no product knowledge
   ↑
vtx.coding_agent.tui    product: app shell, panels, slash commands
```

## Public surface

`vtx.tui.__all__` re-exports the names most callers need, lazily, so importing the package does not pull in every widget:

`AskUserDialog`, `AutocompleteProvider`, `ContentBlock`, `DEFAULT_COMMANDS`, `FilePathProvider`, `FloatingList`, `HandoffLinkBlock`, `InputBox`, `LaunchWarning`, `LaunchWarningsBlock`, `ListItem`, `PullRequestProvider`, `SelectionMode`, `SlashCommand`, `ThinkingBlock`, `ToolBlock`, `UpdateAvailableBlock`, `UserBlock`, `format_tokens`, `get_styles`, `preprocess_latex`, `stylize_badge_markers`.

`blocks.TaskToolBlock` and `blocks.CompactionBlock` are imported from `vtx.tui.blocks` directly; they are not in `__all__`.

## Modules

### Text entry and completion

| Module | Contents |
| --- | --- |
| `input.py` | `InputBox` (the multi-line editor widget), `Vtx` (the `TextArea` it wraps), `AskUserInput`. Paste compaction, image attachment, shell-command styling, tab completion, and slash/file/PR completion dispatch. |
| `autocomplete.py` | The completion machinery: `AutocompleteProvider` (abstract), `CompletionResult`, `SlashCommand`, and the three concrete providers `SlashCommandProvider` (`/`), `FilePathProvider` (`@`), `PullRequestProvider` (`#`). `DEFAULT_COMMANDS` is the list of slash commands the product registers. |
| `floating_list.py` | `FloatingList` / `ListItem`, the overlay the providers render into: paginated, arrow-indicator, position-counter. The parent owns `show`/`hide` and reads `selected_item`. |
| `fuzzy.py` | `fuzzy_match(query, text)` and `fuzzy_filter(items, query, get_text)`. Subsequence matching; higher score is better here, inverted from pi-mono's convention. Tokens split on whitespace and `/`; ties keep input order. |
| `path_complete.py` | `PathComplete`, the tab-completion engine: tilde expansion, directory-listing cache, longest-common-prefix for multiple matches. |
| `prompt_history.py` | `PromptHistory`, persisted to `~/.vtx/prompt-history.jsonl`, capped at `MAX_HISTORY_ENTRIES` (50). |
| `selection_mode.py` | `SelectionMode`, the `StrEnum` naming every picker the app can be in (session, model, thinking, permissions, goal focus, ...). |

`autocomplete.py` is the completion engine and `ask_user.py` is the questionnaire state machine; `input.py` and `blocks.py` are their consumers, not the other way round.

### Rendering

| Module | Contents |
| --- | --- |
| `blocks.py` | The scrollback widgets: `ToolBlock`, `TaskToolBlock`, `ThinkingBlock`, `ContentBlock`, `UserBlock`, `HandoffLinkBlock`, `UpdateAvailableBlock`, `LaunchWarningsBlock`, `CompactionBlock`, plus `LaunchWarning` and `stylize_badge_markers`. `_StreamingMarkdownMixin` splits completed text at stable block boundaries and caches the closed prefix, so a streaming delta only re-renders the open tail. |
| `task_ui.py` | **Pure rich-text formatting despite the name** — no widget. Glyphs, spinners, and renderers for sub-agent activity: `render_finished`, `render_receipt`, `render_background`, `format_turns`, `format_tool_breakdown`, `extract_summary_line`, `describe_activity`. Every function takes plain data and returns `Text`. |
| `formatting.py` | Markdown rendering for the chat: `MARKDOWN_THEME` / `CustomMarkdown`, `format_markdown`, `format_markdown_block`, `find_stable_block_boundary`, `strip_markdown_for_collapsed_text`, `markdown_render_width`, `format_bash_command`, `format_tokens`. |
| `latex.py` | `preprocess_latex`, converting inline and display math to Unicode. Adapted from innomd by Innomatica GmbH (MIT). |
| `tool_output.py` | `format_expand_hint`, `escape_tool_output_text`, `truncate_tool_output_text` -- the collapsed-output view and its `ctrl+o` hint. Used by `vtx.agent.tools.web` as well as the product. |
| `diff_display.py` | `blend_hex` and `DIFF_BG_PAD_MARKER`, for tinting diff backgrounds in a palette. |

### Styling and interaction

`styles.py` provides `get_styles()` (and the module-level `STYLES`), the app CSS built from the configured palette.

`clipboard.py` provides `copy_to_clipboard(text)` and `read_clipboard_image()`, with per-platform paths for X11/Wayland, macOS and Windows.

`ask_user.py` holds `AskUserDialog`, the questionnaire state machine behind the `ask_user` dialog. Pure Python with no Textual imports: the TUI layer feeds it keypresses and renders it. One dialog owns 1-4 questions, an optional Submit review tab, and a collapsed mode. The full keyboard contract is in the module docstring.

## Notes on use

- `fuzzy_match` returns a score where **higher is better** and `NO_MATCH` (0.0) means no match, so the descending sorts already in the callers keep working.
- `InputBox` manages completion state but not the overlay widget. The app owns the `FloatingList` and calls `show(items)` / `hide()`.
- Providers that shell out (`FilePathProvider`, `PullRequestProvider`) set `slow = True`, which is the input box's signal to debounce keystrokes and run suggestions off the event loop.

## Tests

```bash
uv run --no-sync python -m pytest -p no:cacheprovider tests/ui -q
```

`tests/ui` covers `autocomplete`, `floating_list`, `latex`, `prompt_history`, `styles` and `task_ui` directly, and covers the widgets (`input.py`, `blocks.py`) through the app, since that is the only way they are reachable. `tests/test_tui_fuzzy.py` covers `fuzzy`.
