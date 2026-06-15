# Theming

Vtx ships with a curated set of built-in themes. Themes control every color in the TUI: backgrounds, foreground, accent, status colors, tool badges, diff highlights, markdown rendering, and syntax highlighting for shell commands.

## Built-in themes

| ID | Label | Family |
| --- | --- | --- |
| `gruvbox-dark` (default) | Gruvbox Dark | Gruvbox |
| `gruvbox-light` | Gruvbox Light | Gruvbox |
| `catppuccin-frappe` | Catppuccin Frappe | Catppuccin |
| `catppuccin-latte` | Catppuccin Latte | Catppuccin |
| `catppuccin-macchiato` | Catppuccin Macchiato | Catppuccin |
| `catppuccin-mocha` | Catppuccin Mocha | Catppuccin |
| `dracula` | Dracula | Dracula |
| `everforest` | Everforest | Everforest |
| `flexoki` | Flexoki | Flexoki |
| `github-dark` | GitHub Dark | GitHub |
| `github-light` | GitHub Light | GitHub |
| `kanagawa` | Kanagawa | Kanagawa |
| `kanagawa-dragon` | Kanagawa Dragon | Kanagawa |
| `monokai` | Monokai | Monokai |
| `nightowl` | Night Owl | Night Owl |
| `nord` | Nord | Nord |
| `one-dark` | One Dark | Atom |
| `one-light` | One Light | Atom |
| `palenight` | Palenight | Material |
| `rosepine` | Rosé Pine | Rosé Pine |
| `solarized-dark` | Solarized Dark | Solarized |
| `solarized-light` | Solarized Light | Solarized |
| `tokyo-day` | Tokyo Day | Tokyo |
| `tokyo-night` | Tokyo Night | Tokyo |
| `ayu` | Ayu | Ayu |

The full source is in [`src/vtx/themes.py`](../src/vtx/themes.py). Each theme is a `ThemeConfig` with a `ColorsConfig` body.

## Switching themes

| Where | How |
| --- | --- |
| `/themes` (in-app) | Open the picker. Arrow keys to navigate, Enter to apply. The theme is persisted to `ui.theme` in `config.yml`. |
| `~/.vtx/config.yml` | `ui.theme: "tokyo-night"` |
| `vtx` CLI | No CLI flag — use the in-app picker or edit config. |

The current theme is also reflected in the `ui.theme` field of `/session` and in the launch logs.

## Palette tokens

Every theme defines the same set of tokens. They're the public palette — Vtx's UI binds to these names, not to specific colors, so themes can swap colors freely without code changes.

| Token | Used for |
| --- | --- |
| `bg` | Main background |
| `fg` | Default foreground |
| `dim` | Subdued text (hints, secondary info) |
| `muted` | Even dimmer — chrome, separators |
| `title` | Section titles, headers |
| `spinner` | Loading spinners |
| `accent` | Highlights, links, active state |
| `info` | Informational notices |
| `markdown_heading` | Markdown `#`/`##`/etc. headings |
| `markdown_code` | Inline and fenced code in Markdown |
| `selected` | Selected item in pickers |
| `error` | Error messages, failed tool status |
| `notice` | Warnings, soft alerts |
| `diff_added` | Diff lines starting with `+` |
| `diff_removed` | Diff lines starting with `-` |
| `running` | In-progress tool call |
| `success` | Successful tool call |
| `failed` | Failed tool call |
| `panel` | Modal background |
| `panel_alt` | Alternating row in lists / pickers |
| `panel_user` | User message background |
| `editor` | Input box background |
| `border` | Box borders |
| `tool_bg.pending` | Tool block background while running |
| `tool_bg.success` | Tool block background on success |
| `tool_bg.error` | Tool block background on failure |
| `badge.bg` | Tool name / icon badge background |
| `badge.label` | Tool name / icon badge label color |
| `syntax.command` | Shell command name in syntax highlighting |
| `syntax.arg` | Shell argument |
| `syntax.option` | Shell flag (`-x`, `--foo`) |
| `syntax.operator` | Shell operator (`&&`, `|`, `>`, …) |
| `syntax.string` | Quoted string |
| `syntax.variable` | `$VAR` |

A theme must define every token. There's no fallback chain — an incomplete theme is rejected at load time.

## Choosing a theme

Light themes (`gruvbox-light`, `catppuccin-latte`, `github-light`, `one-light`, `solarized-light`, `tokyo-day`) are easier to read in well-lit environments and when sharing screenshots. Dark themes are the default for a reason: lower contrast means less visual fatigue in long sessions.

The Gruvbox family is the safest default — it has explicit muted/dim distinctions that other palettes blur.

## Adding a custom theme

User themes are not yet supported via a config file (the palette is hard-coded in `src/vtx/themes.py`). To add a new built-in theme:

1. Open `src/vtx/themes.py`.
2. Add a new `ThemeConfig` entry to `_THEMES`. The ID must be unique and use the same token names listed above.
3. Bump the `config_version` in `src/vtx/defaults/config.yml` if you want the theme to be available without code changes (otherwise users just need to set `ui.theme` to the new id).
4. Run the test suite: `uv run python -m pytest tests/ui/test_styles.py`.
5. Add the theme to the table at the top of this doc.

The validation in [`src/vtx/config.py`](../src/vtx/config.py) ensures the theme id is in the registry before `ui.theme` is accepted. Unknown values produce an `Invalid theme: <name>` error at load time and fall back to the previous theme.

## Theme switching caveats

- Theme changes are **immediate** — the TUI re-renders on the next frame. No reload needed.
- Themes are **session-scoped in memory** but **persisted to config** when changed through `/themes`.
- The exported HTML transcript uses the theme that was active when the export ran. If you want a specific palette in the export, switch to it first.
- Some Terminal emulators render the colors slightly differently. If a theme looks "off", try a different terminal app or check the `TERM` env var (`xterm-256color` is well-supported).

## The theme picker UI

`/themes` shows the list grouped by family. Each row uses the theme's actual `bg` and `fg` tokens as a swatch — so the picker is also a live preview. The current theme is marked with a checkmark.
