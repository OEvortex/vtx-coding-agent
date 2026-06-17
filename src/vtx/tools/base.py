import asyncio
from abc import ABC, abstractmethod

from pydantic import BaseModel

from ..core.types import ToolResult


class BaseTool[T: BaseModel](ABC):
    # UI model for tool blocks:
    # - format_call(params): short call text shown on the tool header
    # - ToolResult.ui_summary: one-line result summary appended to that header
    # - ToolResult.ui_details: multiline result body shown below the header
    # - format_preview(params): approval-time preview shown before execution
    name: str
    params: type[T]
    description: str
    mutating: bool = True
    tool_icon: str = "→"
    prompt_guidelines: tuple[str, ...] = ()
    needs_approval: bool = False
    """Set by SDK users via ``@tool(needs_approval=True)``.
    The SDK runner pauses the run for human approval when this is True."""
    ui_block: type | None = None
    """Optional Textual widget class the TUI instantiates instead of the
    default :class:`vtx.ui.blocks.ToolBlock` for this tool. Subclasses
    must accept the same ``__init__`` kwargs as ``ToolBlock`` plus the
    optional ``tool`` kwarg (the bound :class:`BaseTool` instance, for
    introspection). The chat log sets ``block.tool`` after construction
    so custom blocks can call back into the tool (``self.tool.format_call``,
    ``self.tool.format_preview``, etc.)."""

    @abstractmethod
    async def execute(
        self, params: T, cancel_event: asyncio.Event | None = None
    ) -> ToolResult: ...

    def format_call(self, params: T) -> str:
        data = params.model_dump(exclude_none=True)
        if not data:
            return ""
        parts = [f"{k}={v}" for k, v in data.items()]
        return " / ".join(parts)

    def format_preview(self, params: T) -> str | None:
        """Extended preview shown only during approval prompts. Returns None by default."""
        return None
