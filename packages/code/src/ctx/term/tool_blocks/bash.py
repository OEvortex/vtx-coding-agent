"""The in-chat ``bash`` tool block.

Shell commands have their own display syntax (``$`` prompt, token
highlighting, ANSI-styled output). The header/body styling hooks are
overridden here; ANSI output painting stays in the generic block, which
detects SGR codes without knowing which tool produced them.
"""

from __future__ import annotations

from ctx.tui.formatting import format_bash_command  # ty: ignore[import-not-found]
from rich.text import Text

from .base import CodingToolBlock


class BashToolBlock(CodingToolBlock):
    """Shell-command header and output rendering for ``bash``."""

    def _format_call_detail(self, first_line: str) -> Text:
        return format_bash_command(first_line)

    def _format_call_msg(self, truncate: bool = True) -> Text:
        if not self._call_msg:
            return Text()
        content = self._call_msg
        if truncate:
            lines = content.split("\n")
            if len(lines) > self.MAX_HEADER_LINES:
                content = "\n".join(lines[: self.MAX_HEADER_LINES])
                content += f"\n... ({len(lines) - self.MAX_HEADER_LINES} more lines)"
        return format_bash_command(content)
