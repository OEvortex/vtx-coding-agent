# ctx-term

**Terminal UI building blocks for ctx.**

[![PyPI version](https://img.shields.io/pypi/v/ctx-term.svg)](https://pypi.org/project/ctx-term/)
[![Python](https://img.shields.io/pypi/pyversions/ctx-term.svg)](https://pypi.org/project/ctx-term/)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)

`ctx-term` ships the product-neutral half of the `ctx.term` namespace: Textual widgets, scrollback blocks, formatting, and input primitives. The coding agent app, its slash commands, its panels, and every tool-specific block live in [ctx-code](https://pypi.org/project/ctx-code/) and join the same namespace through `pkgutil.extend_path`. It depends on [ctx-sdk](https://pypi.org/project/ctx-sdk/).

## Quick start

```bash
pip install ctx-term
```

Python 3.12 or newer.

## Usage

Reuse the primitives to build your own app:

```python
import asyncio

from textual.app import App, ComposeResult
from textual.containers import VerticalScroll

from ctx.term import FloatingList, InputBox


class Demo(App):
    CSS = "Screen { layout: vertical; }"

    def compose(self) -> ComposeResult:
        yield VerticalScroll(id="chat")
        yield FloatingList(id="overlay")
        yield InputBox()

    def on_mount(self) -> None:
        self.query_one(InputBox).focus()


asyncio.run(Demo().run())
```

## Features

- **InputBox** — multi-line editor with paste compaction, image attachment, path completion, and history navigation
- **FloatingList** — completion overlay with pagination and search
- **Autocomplete** — providers for slash commands, file paths, and pull requests
- **Blocks** — generic scrollback widgets: `ToolBlock`, `ThinkingBlock`, `ContentBlock`, `UserBlock`, etc.
- **Formatting** — markdown rendering for chat, LaTeX preprocessing, diff display
- **Styling** — theme-based CSS generation, clipboard support

## Documentation

Full reference at [github.com/OEvortex/ctx-coding-agent](https://github.com/OEvortex/ctx-coding-agent).

## License

[Apache License 2.0](LICENSE)
