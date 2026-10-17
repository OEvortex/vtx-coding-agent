from __future__ import annotations

from rich.style import Style
from rich.text import Text
from textual.widgets import Label

from vtx.core.config import config
from vtx.tui.blocks import ToolBlock


class TodoToolBlock(ToolBlock):
    DEFAULT_CSS = """
    TodoToolBlock #tool-output {
        padding: 0;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.add_class("todo-block")

    def _format_header(self, truncate: bool = True) -> Text:
        if self._awaiting_approval or self._ask_dialog is not None:
            return super()._format_header(truncate=truncate)

        try:
            accent = config.ui.colors.accent
        except Exception:
            accent = "#00b4ff"

        accent_style = Style(color=accent, bold=True)

        result = Text()
        result.append(f"{self._icon} ", style=accent_style)
        result.append("Todos", style=accent_style)
        if self._ui_summary:
            result.append(f" {self._ui_summary}", style=accent_style)
        elif self._success is None:
            result.append(" ...", style=Style(color=accent))
        return result

    def _render_result_output(self) -> None:
        try:
            output = self.query_one("#tool-output", Label)
        except Exception:
            return
        ui_details = (
            self._ui_details_full if self._expanded and self._ui_details_full else self._ui_details
        )
        if ui_details:
            rendered = (
                self._render_markup_safe(ui_details) if self._result_markup else Text(ui_details)
            )
            self.remove_class("-compact")
            self.add_class("-with-details")
            output.remove_class("-hidden")
            output.remove_class("-details")
            output.remove_class("-diff-output")
            output.update(rendered)
        else:
            output.update(Text(""))
            self.remove_class("-with-details")
            output.remove_class("-details")
            output.remove_class("-diff-output")
            output.add_class("-hidden")
