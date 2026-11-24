# Changelog

All notable changes to ctx-term are documented in this file. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-11-24

### Added

- **Initial release** — ctx-term provides the product-neutral half of the `ctx.term` namespace: Textual widgets, scrollback blocks, formatting, and input primitives. It depends on [ctx-sdk](https://pypi.org/project/ctx-sdk/).
- **`InputBox`** — multi-line editor with paste compaction, image attachment, path completion, and history navigation.
- **`FloatingList`** — completion overlay with pagination and search.
- **Autocomplete** — providers for slash commands, file paths, and pull requests.
- **Blocks** — generic scrollback widgets: `ToolBlock`, `ThinkingBlock`, `ContentBlock`, `UserBlock`, etc.
- **Formatting** — markdown rendering for chat, LaTeX preprocessing, diff display.
- **Styling** — theme-based CSS generation, clipboard support.
- **`TreeSelector`** — session tree widget with node selection, movement, and filtering.
- **`InfoBar`** — centralized status line with file changes, token usage, and agent status readouts.

### Changed

- **Monorepo split** — the terminal UI was split from the monolithic `vtx-coding-agent` into a standalone base toolkit. The base package never imports the product, so the harness can depend on `ctx.term` without pulling in the coding agent app.
- **Namespace sharing** — ctx-term and ctx-agent join the same `ctx.term` namespace through `pkgutil.extend_path`, with ctx-term providing the base layer and ctx-agent providing the product-specific widgets.
