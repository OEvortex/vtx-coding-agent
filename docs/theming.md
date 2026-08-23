# Theming

Themes live in `src/coding_agent/themes.py`. Switch with `/settings` → themes or `ui.theme` in config.

## Built-in themes (25)

`ayu`, `catppuccin-frappe`, `catppuccin-latte`, `catppuccin-macchiato`, `catppuccin-mocha`, `dracula`, `everforest`, `flexoki`, `github-dark`, `github-light`, `gruvbox-dark`, `gruvbox-light`, `kanagawa`, `kanagawa-dragon`, `monokai`, `nightowl`, `nord`, `one-dark`, `one-light`, `palenight`, `rosepine`, `solarized-dark`, `solarized-light`, `tokyo-day`, `tokyo-night`

Preview them all with `uv run python scripts/show_themes.py`.

## Palette tokens

Each theme defines a `ColorsConfig`:

| Token group | Tokens |
| --- | --- |
| Text | `fg`, `bg`, `dim`, `muted`, `title` |
| Status | `accent`, `info`, `notice`, `error`, `selected`, `running`, `success`, `failed`, `spinner` |
| Markdown | `markdown_heading`, `markdown_code` |
| Diffs | `diff_added`, `diff_removed` |
| Panels | `panel`, `panel_alt`, `panel_user`, `editor`, `border` |
| Tool badge | `badge.bg`, `badge.label`, `tool_bg.pending/success/error` |
| Syntax (optional) | `syntax.command/arg/option/operator/string/variable` |

Missing tokens are derived by blending, so a minimal theme only needs the core colors. The TUI stylesheet (`tui/styles.py`) is generated from the active theme at startup; input-area colors use a matching Textual theme.

There is no custom-theme file format yet — add themes in Python or override single UI options under `ui:` in config.
