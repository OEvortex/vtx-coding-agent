# Keybindings

This page is a complete inventory of every keybinding in the Vtx TUI, the action each one triggers, and the widget that owns it. Bindings are declared as `BINDINGS` class attributes on Textual widgets; where a key is handled by a raw `on_key` handler instead, that is called out separately.

Two declaration forms appear in the codebase and both are listed here:

- `Binding("key", "action", "description")` (object form, optionally with `priority=True`)
- `("key", "action", "description")` (plain-tuple shorthand, no `priority`)

A `priority=True` binding is checked before the focused widget's own bindings. A binding written without it is dispatched in normal Textual order, which is why the app-level `escape` and `ctrl+c` bindings (plain tuples) can lose to a focused widget.

Keys with a comma in the `Binding` key field bind several keys to one action, for example `Binding("ctrl+j,shift+enter", "newline", ...)` (`src/vtx/tui/input.py:204`).

## App level (`Vtx`)

Owner: the `Vtx` application class, `src/vtx/tui/app.py:71`, `BINDINGS` at `src/vtx/tui/app.py:86`.

| Key | Action | Priority | What it does |
|---|---|---|---|
| `ctrl+c` | `handle_ctrl_c` | no | Clear the input if it has text; otherwise arm exit and require a second press within 2.0 s (`src/vtx/tui/app.py:832-840`) |
| `ctrl+d` | `handle_ctrl_d` | yes | Delete the selected queued message; in the session picker, delete the highlighted session after a confirming second press within 2.0 s (`src/vtx/tui/app.py:862-873`) |
| `escape` | `interrupt_agent` | no | Cancel the tree selector if open, otherwise interrupt the running turn (`src/vtx/tui/app.py:894`) |
| `left` | `tree_page_up` | yes | Page up in the session tree, only while `_selection_mode == TREE` (`src/vtx/tui/app.py:886`) |
| `right` | `tree_page_down` | yes | Page down in the session tree, same guard (`src/vtx/tui/app.py:890`) |
| `ctrl+t` | `cycle_thinking_level` | yes | Cycle the thinking effort to the next level the model offers (`src/vtx/tui/app.py:947`) |
| `ctrl+o` | `toggle_tool_output` | yes | Expand or collapse tool output blocks (`src/vtx/tui/app.py:923`) |
| `ctrl+shift+t` | `toggle_thinking` | yes | Show or hide thinking blocks (`src/vtx/tui/app.py:929`) |
| `alt+ctrl+p` | `cycle_permission_mode` | yes | Toggle between `prompt` and `auto` (`src/vtx/tui/app.py:942`) |
| `shift+tab` | `cycle_agent` | yes | Cycle to the next handoff agent (`src/vtx/tui/commands/agents.py:121`) |
| `alt+ctrl+g` | `cycle_tool_group` | yes | Cycle to the next tool group for the active agent (`src/vtx/tui/commands/agents.py:131`) |

`left` and `right` are also gated by `check_action`, which returns `False` unless the app is in tree selection mode (`src/vtx/tui/app.py:881-884`).

## Input box (`InputBox`)

Owner: `src/vtx/tui/input.py:187`, `BINDINGS` at `src/vtx/tui/input.py:202`. All are `Binding` objects with `priority=True` except `escape`.

| Key | Action | Priority | What it does |
|---|---|---|---|
| `enter` | `submit` | yes | Accept the highlighted completion, start/continue a queue edit, or submit the prompt. When an approval or ask-user picker is active, the key is routed to the app's `on_key` first (`src/vtx/tui/input.py:606`) |
| `ctrl+j`, `shift+enter` | `newline` | yes | Insert a newline into the multi-line editor (`src/vtx/tui/input.py:679`) |
| `alt+enter` | `steer_submit` | yes | Submit as a steer message, injected into the running turn instead of queuing (`src/vtx/tui/input.py:631`) |
| `ctrl+v` | `paste_image` | yes | Paste an image from the clipboard as an attachment (`src/vtx/tui/input.py:409`) |
| `escape` | `cancel` | no | Cancel an ask-user picker, cancel a queue edit, deny a pending approval, interrupt a running turn, or clear the input (`src/vtx/tui/input.py:682`) |
| `up` | `cursor_up` | yes | Move within the editor, then select a queued message, then browse prompt history (`src/vtx/tui/input.py:714`) |
| `down` | `cursor_down` | yes | Move within the editor, then select a queued message, then browse prompt history (`src/vtx/tui/input.py:738`) |
| `tab` | `tab_complete` | yes | Accept the highlighted slash or `@path` completion; otherwise run path completion (`src/vtx/tui/input.py:759`) |

`escape` is declared with `priority=False` deliberately, so that `shift+enter` on terminals that report it as `escape` still inserts a newline.

## Editor text area

Owner: the `Vtx(TextArea)` subclass at `src/vtx/tui/input.py:116`, `BINDINGS` at `src/vtx/tui/input.py:119`. These supplement Textual's built-in `ctrl+arrow` word motion.

| Key | Action | Priority | What it does |
|---|---|---|---|
| `alt+left` | `cursor_word_left` | no | Move the cursor one word left |
| `alt+right` | `cursor_word_right` | no | Move the cursor one word right |
| `alt+backspace` | `delete_word_left` | no | Delete the word before the cursor |
| `alt+delete` | `delete_word_right` | no | Delete the word after the cursor |

## Session tree (`TreeSelector`)

Owner: `src/vtx/tui/tree.py:42`. Opens from `/tree`. All are `Binding` objects with `priority=True`.

| Key | Action | What it does |
|---|---|---|
| `up` | `move_up` | Move the selection up one entry (`src/vtx/tui/tree.py:394`) |
| `down` | `move_down` | Move the selection down one entry (`src/vtx/tui/tree.py:404`) |
| `left`, `pageup` | `page_up` | Page up (also bound to the app-level `left` while the tree is open) (`src/vtx/tui/tree.py:414`) |
| `right`, `pagedown` | `page_down` | Page down (`src/vtx/tui/tree.py:417`) |
| `enter` | `select` | Jump to the selected entry (`src/vtx/tui/tree.py:436`) |
| `escape` | `cancel` | Close the tree (`src/vtx/tui/tree.py:442`) |

## Modal and overlay widgets

| Owner | Key | Action | Priority | What it does |
|---|---|---|---|---|
| `FileChangesModal` (`src/vtx/tui/widgets.py:62`) | `escape` | `dismiss_modal` | no | Close the file-changes modal (`src/vtx/tui/widgets.py:150`) |
| `ExtensionConfirmScreen` (`src/vtx/tui/extension_ui.py:38`) | `y` | `yes` | no | Confirm (`src/vtx/tui/extension_ui.py:79`) |
| `ExtensionConfirmScreen` | `enter` | `yes` | no | Confirm |
| `ExtensionConfirmScreen` | `n` | `no` | no | Decline (`src/vtx/tui/extension_ui.py:82`) |
| `ExtensionConfirmScreen` | `escape` | `no` | no | Decline |
| `ExtensionSelectScreen` (`src/vtx/tui/extension_ui.py:86`) | `escape` | `cancel` | no | Cancel the selection (`src/vtx/tui/extension_ui.py:130`) |
| `ExtensionInputScreen` (`src/vtx/tui/extension_ui.py:134`) | `escape` | `cancel` | no | Cancel the input (`src/vtx/tui/extension_ui.py:175`) |
| `ExtensionCustomScreen` (`src/vtx/tui/extension_ui.py:429`) | `escape` | `cancel` | no | Dismiss the extension's widget with `None` (`src/vtx/tui/extension_ui.py:460-461`) |

The extension screens use the plain-tuple form for all of their bindings.

## Widgets with no bindings

`src/vtx/tui/blocks.py`, `src/vtx/tui/floating_list.py`, and `src/vtx/tui/queue_ui.py` declare no `BINDINGS`. Their input arrives from the input box through messages (`CompletionMove`, `CompletionSelect`, `CompletionHide`) rather than from keys bound on the widget itself.

## Keys handled directly, without a `BINDINGS` entry

These keys are intercepted in an event handler before the binding system runs, so they are not declared anywhere.

### Tool approval prompt

Handled in `Vtx._on_key` (`src/vtx/tui/app.py:1085`), and mirrored in the input box so the keys work while the editor has focus (`src/vtx/tui/input.py:136-148`).

| Key | Effect |
|---|---|
| `y`, `Y` | Approve immediately |
| `n`, `N` | Deny immediately |
| `left`, `right` (input empty) | Move the highlight between the two buttons without submitting |
| `enter` (input empty) | Submit the highlighted button |

### `ask_user` questionnaire picker

While an `ask_user` tool call is pending, every key in `_ASK_USER_PICKER_KEYS` plus any single digit is routed to the dialog instead of being typed into the editor (`src/vtx/tui/input.py:79-104`).

| Key | Effect |
|---|---|
| `up`/`k`, `down`/`j` | Move the highlight, wrapping at both ends |
| `left`/`shift+tab`, `right`/`tab` | Switch question tab backward/forward (question tabs plus the Submit review tab) |
| `space` | Toggle the highlighted option's checkbox (multi-select only) |
| `enter` | Confirm the focused option (single-select), toggle a checkbox (multi-select), commit typed text, or activate the focused Submit-picker row |
| `escape` | Cancel the whole questionnaire |
| `ctrl+u` | Clear the current custom-answer draft |
| `ctrl+]`, `ctrl+right_square_bracket` | Collapse/expand the dialog (while collapsed only these and `escape` act) |
| `1`-`9` | Jump to row N and activate it if it is an option (out-of-range digits are ignored) |

The state machine lives in `src/vtx/tui/ask_user.py:184-273`.

### Queue navigation

The pending/steer queue has no widget bindings; it is driven from the input box's `up`/`down` actions and from the app-level `ctrl+d`.

| Key | Effect |
|---|---|
| `up` / `down` when the editor is on its first/last line | Select a queued message (`src/vtx/tui/input.py:731`, `src/vtx/tui/input.py:752`) |
| `enter` on a selected queue item | Put it back in the editor for editing (`src/vtx/tui/input.py:627`, `src/vtx/tui/queue_ui.py:102-118`) |
| `ctrl+d` on a selected queue item | Delete it (`src/vtx/tui/app.py:850`, `src/vtx/tui/queue_ui.py:86-100`) |

The queue holds at most 5 entries (`src/vtx/tui/widgets.py:517`); beyond that, submitting prints `Queue full (max 5)` or `Steer queue full (max 5)` (`src/vtx/tui/app.py:1216`, `src/vtx/tui/app.py:1223`).

## Textual framework bindings

`Vtx` subclasses `App`, so Textual's own bindings are also live. The ones that reach this app include `tab` / `shift+tab` focus traversal, `pageup`/`pagedown` scrolling, and the standard `ctrl+q` app-quit action. Because `shift+tab` is rebound at app level with `priority=True` (`src/vtx/tui/app.py:100`), the default focus-traversal `shift+tab` does not reach the app.

## Known gaps

- There is no user keybinding file. Keys are compile-time constants in the `BINDINGS` lists above; rebinding requires editing `src/vtx/tui/app.py:86` or `src/vtx/tui/input.py:199`.
- `/help` prints a hand-maintained shortcut list (`src/vtx/tui/chat.py:572-586`) that is a subset of this page. It covers the app-level keys but omits the input box's `enter`, `ctrl+j`/`shift+enter`, `alt+enter`, `ctrl+v` and `tab`, the editor word keys, the tree selector's own keys, `left`/`right` tree paging, the tool-approval `y`/`n` keys, and the whole `ask_user` picker key set.
- `alt+enter` for steer submission depends on the terminal reporting it distinctly from `enter`; terminals that do not distinguish modifiers will submit normally.
- The `ask_user` key set includes two spellings of the same physical key (`ctrl+]` and `ctrl+right_square_bracket`) because Textual can report either name. They are not separate keys.
- The `ctrl+c` double-press window (2.0 s) and `ctrl+d` double-press window (2.0 s) are hardcoded constants (`src/vtx/tui/app.py:175`), not configurable.
- `escape` has no `priority=True`, so a focused extension screen or future modal can swallow it. The tree selector opts in with priority for exactly this reason.
- Textual's own `ctrl+q` is not overridden, so it exits the app without the double-press confirmation `ctrl+c` requires.

## See also

- [Slash commands](slash-commands.md) for the in-app command router
- [Command line](cli.md) for the executable's flags
- [Plugins](plugins.md) for the extension screens that bind `escape`
