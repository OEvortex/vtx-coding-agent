# Theming

Vtx ships 25 built-in themes. Switch them interactively with `/themes` in the TUI, or set `ui.theme` in `config.yml` (see [configuration.md](configuration.md)).

## Available themes

`ayu`, `catppuccin-frappe`, `catppuccin-latte`, `catppuccin-macchiato`, `catppuccin-mocha`, `dracula`, `everforest`, `flexoki`, `github-dark`, `github-light`, `gruvbox-dark`, `gruvbox-light`, `kanagawa`, `kanagawa-dragon`, `monokai`, `nightowl`, `nord`, `one-dark`, `one-light`, `palenight`, `rosepine`, `solarized-dark`, `solarized-light`, `tokyo-day`, `tokyo-night`.

## Setting a theme

```yaml
ui:
  theme: "tokyo-night"
```

An invalid theme name raises a config validation error. The default is `gruvbox-dark`.

## Color tokens

Each theme defines a `ColorsConfig` with semantic tokens (accent, dim, muted, border, error, warning, success, etc.) consumed across the TUI. Extensions and custom tool blocks receive these via `config.ui.colors`. Access at runtime:

```python
from vtx import config
colors = config.ui.colors
```
