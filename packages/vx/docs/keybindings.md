# Key bindings

The composer's full key map, as listed under the commands in `/help` (`KEY_BINDINGS` in `packages/vx/src/vx/commands/__init__.py`). Rows marked *Keys* in `/help` are reference only — selecting one does not fill the composer.

## Swarm board

| Keys | Action |
|---|---|
| `left` | Open the swarm board when the composer is empty; `esc` leaves |
| `alt+s` | Open the swarm board from anywhere |

## Transcript and composer navigation

| Keys | Action |
|---|---|
| `ctrl+g` | Fold the sidebar's sections |
| `ctrl+f` | Search the transcript |
| `n` / `N` | Next / previous search match |
| `ctrl+end` | Jump to the latest message |
| `pgup` / `pgdn` | Scroll the transcript — or the composer once it fills the screen |
| wheel | Scroll the transcript |
| `shift+drag` | Select text |

## Editing

| Keys | Action |
|---|---|
| `ctrl+r` | Search prompt history |
| `ctrl+v` | Attach a clipboard image, or paste text |
| `ctrl+x` | Drop the attached images |
| `ctrl+z` / `ctrl+y` | Undo / redo the composer |
| `alt+b` / `alt+f` | Move by word |
| `ctrl+left` / `ctrl+right` | Move by word |
| `ctrl+y` | Yank the last kill |
| `alt+y` | Cycle the kill ring |
| `alt+enter` | New line without sending |
| `ctrl+o` | Expand a `!cmd` shell block's output |

Note the newline key is `alt+enter`: terminals send a distinct sequence for it, while `shift+enter` usually arrives as plain `enter` and would submit.

## Modes and exit

| Keys | Action |
|---|---|
| `tab` / `shift+tab` | Cycle reasoning effort / permission mode |
| `esc` | Interrupt the turn, or clear the composer |
| `ctrl+d` | Exit when the composer is empty |
| `ctrl+c` twice | Exit |
