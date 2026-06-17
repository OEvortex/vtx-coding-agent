"""Custom TUI block example: a ``my_table`` tool that renders results
as a Rich Table inside the chat log.

This extension registers a tool (``my_table``) that takes a list of
rows + columns and returns the data. The TUI side of the experience is
a subclass of :class:`vtx.ui.blocks.ToolBlock` that renders the result
as a Rich :class:`rich.table.Table` instead of plain text. The custom
block inherits the default tool-block chrome (header, approval prompt,
error state) and only overrides ``set_result`` to format the data.

Drop this file into ``~/.vtx/agent/extensions/custom_block.py`` (or any
``.vtx/extensions/custom_block.py``) to load it.

The LLM can then call::

    my_table(
        columns=[{"name": "city", "style": "bold"}, {"name": "pop"}],
        rows=[{"city": "Paris", "pop": 2.1}, {"city": "Tokyo", "pop": 13.9}],
        title="Largest cities",
    )

The TUI block will render a Rich Table with the columns above.
"""

from __future__ import annotations

import json

from rich.console import Console
from rich.table import Table
from rich.text import Text
from textual.widgets import Label

from vtx.ui.blocks import ToolBlock

# ---------------------------------------------------------------------------
# Custom block
# ---------------------------------------------------------------------------


class TableToolBlock(ToolBlock):
    """Render a ``my_table`` tool call as a Rich table.

    Subclasses :class:`ToolBlock` to inherit:
      * the standard header (icon + tool name + ``format_call`` text),
      * the approval prompt (``show_approval``),
      * the result / error styling (``set_result`` / ``-success``/``-error``).

    Overrides ``set_result`` to read the table payload out of the
    tool's :class:`vtx.core.types.ToolResult` and render it as a Rich
    Table inside the block's existing ``#tool-output`` label.
    """

    def set_result(  # type: ignore[override]
        self,
        ui_summary: str | None,
        ui_details: str | None,
        success: bool,
        markup: bool = True,
        ui_details_full: str | None = None,
        images: list | None = None,
    ) -> None:
        # First, delegate to the parent so the block's success/failure
        # state and the standard ``ui_details``/``ui_summary`` are
        # rendered. The parent's set_result is responsible for
        # toggling the -with-details class and populating the output
        # label with ``ui_details`` (or images / empty placeholder).
        super().set_result(
            ui_summary,
            ui_details,
            success,
            markup=markup,
            ui_details_full=ui_details_full,
            images=images,
        )

        # Now render the actual table inside #tool-output. We only do
        # this on success; failures keep the default text rendering.
        if not success:
            return

        # ``self.tool`` is set by the chat log after construction. It
        # gives us access to the bound BaseTool so we can pull the
        # raw table data the LLM provided.
        table_data = self._extract_table_data()
        if table_data is None:
            return
        columns, rows, title = table_data

        rich_table = Table(title=title or None, show_header=True, header_style="bold")
        for col in columns:
            rich_table.add_column(
                col.get("name", ""), style=col.get("style"), justify=col.get("justify", "left")
            )
        for row in rows:
            rich_table.add_row(*[str(row.get(col.get("name", ""), "")) for col in columns])

        try:
            label = self.query_one("#tool-output", Label)
        except Exception:
            return
        label.update(Text.from_ansi(_render_table_ansi(rich_table)))

    def _extract_table_data(self) -> tuple[list[dict], list[dict], str | None] | None:
        """Pull the table spec out of the last tool result.

        The tool's execute() function packs the table data into
        ``ToolResult.ui_details`` as a small JSON string. Real
        extensions would do something more structured (e.g. set
        ``ui_details_full`` to a JSON document and parse it here);
        we keep it simple for the example.
        """
        if not self._ui_details:
            return None
        try:
            data = json.loads(self._ui_details)
        except (ValueError, TypeError):
            return None
        if not isinstance(data, dict):
            return None
        return (data.get("columns") or [], data.get("rows") or [], data.get("title"))


def _render_table_ansi(table: Table) -> str:
    """Render a Rich Table to ANSI text suitable for the chat log label."""
    import io

    buf = io.StringIO()
    Console(file=buf, force_terminal=True, color_system="truecolor", width=120).print(table)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Tool definition
# ---------------------------------------------------------------------------


def register(api):
    """Register the ``my_table`` tool with a custom TUI block."""

    def _execute(args: dict, ctx: dict | None) -> dict:
        columns = args.get("columns") or []
        rows = args.get("rows") or []
        title = args.get("title")
        if not isinstance(columns, list) or not isinstance(rows, list):
            return {"success": False, "result": "columns and rows must be lists"}
        # Pack the table spec into ui_details as JSON so the custom
        # block can render it as a Rich Table. The LLM-facing result
        # is a short summary; the structured data lives in ui_details.
        return {
            "success": True,
            "result": f"Rendered {len(rows)} row(s) across {len(columns)} column(s).",
            "ui_summary": f"{len(rows)} x {len(columns)}",
            "ui_details": json.dumps({"columns": columns, "rows": rows, "title": title}),
        }

    api.register_tool(
        name="my_table",
        description=(
            "Render a small table in the chat log. Pass ``columns`` (a list of "
            "objects with ``name`` and optional ``style``/``justify``) and "
            "``rows`` (a list of objects keyed by column name). Optionally "
            "pass a ``title`` to render a heading above the table."
        ),
        parameters={
            "type": "object",
            "properties": {
                "columns": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "style": {"type": "string"},
                            "justify": {"type": "string", "enum": ["left", "center", "right"]},
                        },
                        "required": ["name"],
                    },
                },
                "rows": {"type": "array", "items": {"type": "object"}},
                "title": {"type": "string"},
            },
            "required": ["columns", "rows"],
        },
        execute=_execute,
        mutating=False,
        ui_block=TableToolBlock,
    )
