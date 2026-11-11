# Theming

Vtx ships 27 built-in color themes that drive every color in the TUI, and a small set of related display settings. This page lists the shipped themes, shows how to list and switch them, explains what a theme actually controls, and covers the thinking-display settings that sit next to it.

## Shipped themes

`THEME_ORDER` in `src/vtx/core/themes.py:1105` is the authoritative list. There are 27 entries and no more; the theme id is the key in the `_THEMES` dict (`src/vtx/core/themes.py:73`), and the label is the human-readable name shown in the picker (`src/vtx/core/themes.py:1140`).

| Id | Name | Id | Name |
|---|---|---|---|
| `titanium` | Titanium (Pi-Style) | `kanagawa` | Kanagawa |
| `titanium-light` | Titanium Light | `kanagawa-dragon` | Kanagawa Dragon |
| `ayu` | Ayu | `monokai` | Monokai |
| `catppuccin-frappe` | Catppuccin Frappe | `nightowl` | Night Owl |
| `catppuccin-latte` | Catppuccin Latte | `nord` | Nord |
| `catppuccin-macchiato` | Catppuccin Macchiato | `one-dark` | One Dark |
| `catppuccin-mocha` | Catppuccin Mocha | `one-light` | One Light |
| `dracula` | Dracula | `palenight` | Palenight |
| `everforest` | Everforest | `rosepine` | Rosé Pine |
| `flexoki` | Flexoki | `solarized-dark` | Solarized Dark |
| `github-dark` | GitHub Dark | `solarized-light` | Solarized Light |
| `github-light` | GitHub Light | `tokyo-day` | Tokyo Day |
| `gruvbox-dark` | Gruvbox Dark | `tokyo-night` | Tokyo Night |
| `gruvbox-light` | Gruvbox Light | | |

The default is `gruvbox-dark` (`src/vtx/core/config.py:83`).

## List and set a theme

### With `/themes`

```bash
/themes
```

With no argument, `/themes` builds a picker over every `(id, label)` pair from `get_theme_options()` and marks the current one with a check (`src/vtx/tui/commands/settings.py:62`). Pick one and it is written to the config and applied to the live CSS immediately (`src/vtx/tui/commands/settings.py:70`).

```bash
/themes catppuccin-mocha
/themes gruvbox-dark
```

Passing an id skips the picker. An unknown id prints the `ValueError` message from `get_theme()` in the chat log as an error rather than failing the command (`src/vtx/tui/commands/settings.py:58`, `src/vtx/core/themes.py:1147`).

### With `/settings`

```bash
/settings
```

The settings picker has nine rows: `colored-tool-badge`, `git-context`, `notifications`, `ponytail`, `show-shortcuts`, `permissions`, `themes`, `thinking`, `thinking-lines` (`src/vtx/tui/commands/settings.py:246`). The `themes` row shows the current theme id and opens the same picker as `/themes` (`src/vtx/tui/commands/settings.py:261`, `src/vtx/tui/commands/settings.py:337`).

### In the config file

```yaml
ui:
  theme: "gruvbox-dark"
```

Write the id, not the label. The value is checked by a `field_validator` against `get_theme_ids()` and an unknown name fails validation for the whole config, which drops you back to the built-in defaults with a warning (`src/vtx/core/config.py:108-114`, `src/vtx/core/config.py:949-953`).

`set_theme()` is the setter every path funnels through. It validates the id, writes `ui.theme`, and removes any stale `ui.colors` block left by a pre-migration 2 config file (`src/vtx/core/config.py:1011`, `src/vtx/core/config.py:1023`).

## What a theme controls

A theme is one thing: a `ThemeConfig` (`id`, `label`, `colors`) wrapping a `ColorsConfig` whose `syntax` block is filled in from `_SYNTAX_COLORS` on every read (`src/vtx/core/themes.py:67`, `src/vtx/core/themes.py:1144-1149`). `UIConfig.colors` is a property, not a stored field, that resolves the active theme on every read (`src/vtx/core/config.py:116-118`). That is why changing the theme needs no restart.

### Color roles

`ColorsConfig` (`src/vtx/core/themes.py:20`) declares these roles. Every one of them must have a value, so a partial theme inherits from the defaults rather than from a sibling.

| Field | Used for |
|---|---|
| `bg` | Screen background |
| `fg` | Default text |
| `dim` | Thinking block text, dim text |
| `muted` | Secondary text |
| `title` | Titles |
| `spinner` | The busy spinner |
| `accent` | Highlights, blends, approval and thinking tints |
| `info` | Info-bar and markdown heading text |
| `markdown_heading` | Markdown `#` headings |
| `markdown_code` | Inline markdown code |
| `selected` | Selected completion item, diff additions |
| `error` | Errors, diff removals |
| `notice` | Warnings |
| `diff_added` | Added diff lines |
| `diff_removed` | Removed diff lines |
| `badge.bg` | Tool badge background |
| `badge.label` | Tool badge label color |
| `running` | A tool call in flight |
| `success` | A successful tool call, approval text |
| `failed` | A failed tool call |
| `panel` | Panels |
| `panel_alt` | Alternating panel rows |
| `panel_user` | The user's own messages |
| `editor` | Editor background, thinking tint base |
| `border` | Borders |
| `syntax` | The `SyntaxColorConfig` block below |

For reference, `gruvbox-dark` resolves to:

```text
bg=#282828  fg=#ebdbb2  dim=#857e6a  muted=#a89984  title=#fabd2f
accent=#83a598  info=#fabd2f  markdown_heading=#fabd2f  markdown_code=#689d6a
selected=#689d6a  error=#fb4934  notice=#fe8019  diff_added=#b8bb26  diff_removed=#fb4934
badge=(bg=#3c3836, label=#d3869b)  running=#458588  success=#98971a  failed=#cc241d
panel=#3c3836  panel_alt=#32302f  panel_user=#504945  editor=#414141  border=#504945
```

### Syntax highlighting

`SyntaxColorConfig` (`src/vtx/core/themes.py:11`) holds six roles used when the TUI renders a command or a patch:

| Field | Used for |
|---|---|
| `command` | The command name |
| `arg` | Positional arguments |
| `option` | Flags such as `--force` |
| `operator` | Pipes, redirects, and other operators |
| `string` | Quoted strings |
| `variable` | Variable expansions |

Each theme declares its own values in `_SYNTAX_COLORS` (`src/vtx/core/themes.py:886`), and `get_theme()` overwrites the theme's `syntax` with that entry so there is one source of truth (`src/vtx/core/themes.py:1149`). A `ColorsConfig` with `syntax` omitted gets one derived from `title`, `fg`, `accent`, `notice`, `selected`, and `markdown_code` by the `syntax_colors` property and `_default_syntax_colors` validator (`src/vtx/core/themes.py:47-64`).

### Widgets

`get_styles()` turns the active `ColorsConfig` into the Textual CSS for the whole app (`src/vtx/tui/styles.py:17`). Three colors are not read from the theme but blended from it with `_blend_hex()` (`src/vtx/tui/styles.py:6`):

| Blended color | Formula | Applied to |
|---|---|---|
| `approval_bg` | `bg` + `accent` at 0.05 | `.tool-block.-approval` and `.tool-block.-ask-user` (`src/vtx/tui/styles.py:19`, `src/vtx/tui/styles.py:175-196`) |
| `shell_bg` | `editor` + `success` at 0.15 | `#input-box.-shell-command` (`src/vtx/tui/styles.py:20`, `src/vtx/tui/styles.py:434-442`) |
| `thinking_medium_bg` | `editor` + `accent` at 0.08 | `#input-box.-thinking-medium` (`src/vtx/tui/styles.py:21`, `src/vtx/tui/styles.py:416-420`) |
| `thinking_high_bg` | `editor` + `accent` at 0.18 | `#input-box.-thinking-high` (`src/vtx/tui/styles.py:22`, `src/vtx/tui/styles.py:422-426`) |
| `thinking_xhigh_bg` | `editor` + `badge.label` at 0.18 | `#input-box.-thinking-xhigh` (`src/vtx/tui/styles.py:23`, `src/vtx/tui/styles.py:428-432`) |

The selectors that carry a theme color include `Screen`, `#chat-log`, `.thinking-block` and `#thinking-content`, `.run-summary-block`, `.content-block`, `.user-block`, `.update-available-block`, `.launch-warnings-block`, `.tool-block` and its approval/ask-user variants, `#tool-output`, `.compaction-block`, `.agent-details`, `.handoff-link-block`, `.recap-message`, `#queue-display` and `#queue-content`, `#status-text` and `#exit-hint`, `#agents-panel`, `#input-box`, `#input-prefix`, and `.info-bar` (`src/vtx/tui/styles.py:17`).

### Applying a theme

`_apply_theme()` regenerates `type(self).CSS`, calls `refresh_css(animate=False)`, refreshes the input box, and re-applies the thinking-level class so a theme change does not reset the tint (`src/vtx/tui/app.py:467-471`). `ui.colors` being a property is what makes this safe: the next read after a theme change returns the new colors.

## Thinking display settings

Five UI settings sit next to the theme and behave the same way: they are written to the config file by a dedicated setter, then the config is reloaded.

| Setting | Values | Default | Setter |
|---|---|---|---|
| `ui.collapse_thinking` | `true` / `false` | `true` | by hand only |
| `ui.thinking_lines` | `"1"`, `"2"`, `"3"`, `"4"`, `"5"`, `"none"` | `"1"` | `set_thinking_lines()` (`src/vtx/core/config.py:1062`) |
| `ui.colored_tool_badge` | `true` / `false` | `true` | `set_colored_tool_badge()` (`src/vtx/core/config.py:1120`) |
| `ui.show_welcome_shortcuts` | `true` / `false` | `true` | `set_show_welcome_shortcuts()` (`src/vtx/core/config.py:1030`) |
| `ui.theme` | any of the 27 ids | `"gruvbox-dark"` | `set_theme()` (`src/vtx/core/config.py:1011`) |

`collapse_thinking` decides whether a finalized thinking block is reduced to a summary line at all; `thinking_lines` decides how many lines that summary keeps, where `"none"` disables truncation entirely (`src/vtx/core/config.py:86`, `src/vtx/core/config.py:88`). `THINKING_LINES_OPTIONS` is derived from the `ThinkingLinesOption` literal, so the picker and the validator cannot drift (`src/vtx/core/config.py:79`).

```yaml
ui:
  collapse_thinking: true
  thinking_lines: "3"
  colored_tool_badge: true
  show_welcome_shortcuts: true
```

In the TUI, `/settings` > `thinking-lines` opens a picker whose rows read "show 1 line" ... "no truncation" and confirms with a chat message (`src/vtx/tui/commands/settings.py:187`).

### Related key bindings

| Key | Action |
|---|---|
| `ctrl+t` | Cycle the thinking level (`src/vtx/tui/app.py:92`) |
| `ctrl+shift+t` | Toggle thinking display (`src/vtx/tui/app.py:94`) |
| `ctrl+o` | Toggle tool output (`src/vtx/tui/app.py:93`) |

The thinking level also tints the input box. `_apply_thinking_level_style()` removes all level classes then adds `-thinking-<level>`, and the level list is `none`, `default`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max` (`src/vtx/tui/app.py:461-465`). `max` has no CSS rule, so it renders like `none` and `default` do; only `medium`, `high`, and `xhigh` produce a distinct blended tint (`src/vtx/tui/styles.py:416-432`).

## Known gaps

- There is no way to add your own theme. The only source is the `_THEMES` dict in `src/vtx/core/themes.py:73`; there is no `~/.vtx/themes/` directory, no schema file, and no loader for a user-supplied theme, so a custom palette means editing the source.
- There is no light/dark switching. `ui.theme` is a single string with no `auto` option and no code that reads the terminal background, so you switch by hand and the light and dark variants are separate themes (`titanium` / `titanium-light`, `github-dark` / `github-light`, and so on).
- A theme id is validated at config load, so a typo is not a warning: it fails the whole `Config` parse and falls back to the built-in defaults, losing every other setting in the file (`src/vtx/core/config.py:108-114`, `src/vtx/core/config.py:949-953`). `set_theme()` fails the same way but leaves the file untouched (`src/vtx/core/config.py:1011-1012`).
- `ui.colors` is gone from the schema. Migration 2 drops the legacy `ui.colors` block and a `field_validator` rejects an unknown theme name instead, so there is no partial override of a shipped theme (`src/vtx/core/config.py:490`, `src/vtx/core/config.py:108-114`).
- `thinking_level: "max"` has no CSS rule and no blend, so a `max` input box looks identical to `none` (`src/vtx/tui/styles.py:408-432`).
- `set_theme()` and the others write the config file but do not reload extensions or agents. After editing the file by hand, run `/reload` to pick up the change (`src/vtx/tui/commands/reload.py:243`).
- Themes carry no syntax palette for languages the TUI does not render itself. The six syntax roles are for command and patch display only (`src/vtx/core/themes.py:11`).

Related pages: [configuration.md](configuration.md), [providers.md](providers.md), [cli.md](cli.md), [permissions.md](permissions.md), [skills.md](skills.md), [plugins.md](plugins.md).
