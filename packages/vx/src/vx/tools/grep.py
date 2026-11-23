"""Grep tool for vx — search file contents."""

import asyncio
from pathlib import Path

from ctx.agent.tools.base import BaseTool
from ctx.agent.tools_manager import ensure_tool
from ctx.protocol.types import ToolResult
from pydantic import BaseModel, Field

from vx.tools._tool_utils import (
    ToolCancelledError,
    communicate_or_cancel,
    shorten_path,
    truncate_lines_by_bytes,
)

MAX_OUTPUT_BYTES = 50 * 1024


class GrepParams(BaseModel):
    pattern: str = Field(description="Regex pattern to search for")
    path: str = Field(description="File or directory to search in")
    file_pattern: str | None = Field(
        description="File glob pattern to filter (e.g. '*.py')", default=None
    )
    context: int = Field(description="Number of context lines before/after match", default=0)
    max_results: int = Field(description="Max number of matches to return", default=50)


class GrepTool(BaseTool):
    name = "grep"
    tool_icon = "🔍"
    params = GrepParams
    mutating = False
    parallel_safe = True
    prompt_guidelines = ("grep for search (not bash grep)",)
    description = (
        "Search file contents using ripgrep (regex). Supports context lines, "
        "file filters, and result limits. Much faster than bash grep."
    )

    def format_call(self, params: GrepParams) -> str:
        return f"{params.pattern} in {shorten_path(params.path)}"

    async def execute(
        self, params: GrepParams, cancel_event: asyncio.Event | None = None
    ) -> ToolResult:

        target = Path(params.path)
        if not target.exists():
            return ToolResult(
                success=False,
                result=f"Path not found: {params.path}",
                ui_summary="[red]Path not found[/red]",
            )

        rg_path = await ensure_tool("rg", silent=True)
        if not rg_path:
            return ToolResult(
                success=False,
                result="ripgrep (rg) is not available",
                ui_summary="[red]ripgrep not found[/red]",
            )

        cmd: list[str] = [
            rg_path,
            "--color=never",
            "--heading",
            "--line-number",
            "--column",
            "-m",
            str(params.max_results),
        ]
        if params.context > 0:
            cmd.extend(["-C", str(params.context)])
        if params.file_pattern:
            cmd.extend(["-g", params.file_pattern])
        cmd.extend([params.pattern, str(target)])

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await communicate_or_cancel(proc, cancel_event)
        except ToolCancelledError:
            return ToolResult(
                success=False,
                result="Search aborted",
                ui_summary="[yellow]Search aborted[/yellow]",
            )

        output = stdout.decode("utf-8", errors="replace").strip()
        error = stderr.decode("utf-8", errors="replace").strip()

        if proc.returncode not in (0, 1):
            return ToolResult(
                success=False,
                result=f"grep error: {error or 'unknown'}",
                ui_summary="[red]grep error[/red]",
            )

        if not output:
            return ToolResult(
                success=True, result="No matches found", ui_summary="[dim]No matches[/dim]"
            )

        truncated_output, was_truncated = truncate_lines_by_bytes(
            output.split("\n"), MAX_OUTPUT_BYTES
        )
        if was_truncated:
            truncated_output += f"\n[output truncated after {MAX_OUTPUT_BYTES // 1024}KB]"

        match_count = sum(
            1 for line in output.split("\n") if ":" in line and not line.endswith(":")
        )
        return ToolResult(
            success=True,
            result=truncated_output,
            ui_summary=f"[dim]({match_count} matches)[/dim]",
            ui_data={"pattern": params.pattern, "path": str(target)},
        )
