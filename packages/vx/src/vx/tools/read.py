"""Read tool for vx — read files and list directories."""

import asyncio
import os
from datetime import datetime
from pathlib import Path

import aiofiles
from ctx.agent.tools.base import BaseTool, ToolResult
from ctx.agent.tools_manager import ensure_tool
from ctx.core.image import is_image_file, read_and_process_image
from ctx.protocol.types import ImageContent
from pydantic import BaseModel, Field

from vx.tools._tool_utils import ToolCancelledError, communicate_or_cancel, shorten_path

MAX_CHARS_PER_LINE = 2000
MAX_LINES_PER_FILE = 2000
PREVIEW_LINES = 12
DIRECTORY_DEPTH_ROW_LIMIT = 200
MAX_DIRECTORY_ROWS = 1000


def _strip_line_number(line: str) -> str:
    """Drop the ``%6d\\t`` gutter ``read_file`` adds for the model."""
    _, separator, text = line.partition("\t")
    return text if separator else line


class ReadParams(BaseModel):
    path: str = Field(
        description=(
            "File or directory path — absolute, ~/, $VAR, or relative to the working directory"
        )
    )
    start_line: int | None = Field(
        default=None, description="First line to read (1-based). Omit to start at line 1."
    )
    end_line: int | None = Field(
        default=None,
        description=(
            "Last line to read, inclusive. Omit to read to the end of the file "
            "(capped at 2000 lines per call — page with start_line/end_line)."
        ),
    )


class ReadTool(BaseTool):
    name = "read"
    tool_icon = "→"
    params = ReadParams
    mutating = False
    parallel_safe = True
    description = (
        "Read a file (numbered lines; 2000-line/2000-char caps — page large files "
        "with start_line/end_line: search first, then read a window, don't dump "
        "whole files) or list a directory (recursive, depth<=3). Any path works: "
        "absolute, ~/, or relative to the working directory. Supports jpg/png/gif/webp."
    )

    def format_call(self, params: ReadParams) -> str:
        path = shorten_path(params.path)
        if params.start_line is not None or params.end_line is not None:
            start = params.start_line or 1
            end = params.end_line if params.end_line is not None else "?"
            return f"{path}:{start}-{end}"
        return path

    async def read_file(
        self, file_path: Path, start_line: int | None, end_line: int | None
    ) -> str:
        lines = []
        start = max(0, (start_line - 1)) if start_line else 0
        if end_line is not None:
            # end_line is inclusive: lines wanted = end_line - start (0-based).
            effective_limit = max(0, min(end_line - start, MAX_LINES_PER_FILE))
        else:
            effective_limit = MAX_LINES_PER_FILE
        line_number = 0

        async with aiofiles.open(file_path, encoding="utf-8") as f:
            async for line in f:
                line_number += 1
                if line_number <= start:
                    continue
                if len(lines) == effective_limit:
                    if effective_limit == MAX_LINES_PER_FILE:
                        lines.append(
                            f"[output truncated after {MAX_LINES_PER_FILE} lines — "
                            "continue with start_line/end_line]"
                        )
                    break

                if len(line) > MAX_CHARS_PER_LINE:
                    line = (
                        line[:MAX_CHARS_PER_LINE]
                        + f" [output truncated after {MAX_CHARS_PER_LINE} chars]\n"
                    )
                lines.append(f"{line_number:6d}\t{line}")

        return "".join(lines)

    def _format_directory_entry(self, entry_path: Path, relative: Path) -> str:
        modified = datetime.fromtimestamp(entry_path.stat().st_mtime)
        timestamp = f"{modified.day:2d} {modified.strftime('%b %H:%M')}"
        display = relative.as_posix()
        if entry_path.is_dir():
            display += "/"
        return f"{timestamp}  {display}"

    async def _list_directory_entries(
        self,
        fd_path: str,
        dir_path: Path,
        max_depth: int,
        max_results: int,
        cancel_event: asyncio.Event | None,
    ) -> list[dict]:
        proc = await asyncio.create_subprocess_exec(
            fd_path,
            "--hidden",
            "--color=never",
            "--max-depth",
            str(max_depth),
            "--max-results",
            str(max_results),
            ".",
            str(dir_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await communicate_or_cancel(proc, cancel_event)
        output = stdout.decode("utf-8", errors="replace").strip()
        error_output = stderr.decode("utf-8", errors="replace").strip()

        if proc.returncode not in (0, 1):
            raise RuntimeError(f"fd failed: {error_output or 'unknown error'}")

        if not output:
            return []

        entries: list[dict] = []
        for line in output.split("\n"):
            if not line.strip():
                continue

            entry_path = Path(line)
            if entry_path.is_absolute():
                try:
                    relative = entry_path.relative_to(dir_path)
                except ValueError:
                    relative = entry_path
            else:
                relative = entry_path
                entry_path = dir_path / entry_path

            entries.append(
                {
                    "line": self._format_directory_entry(entry_path, relative),
                    "path": relative.as_posix(),
                    "is_dir": entry_path.is_dir(),
                }
            )

        return entries

    async def read_directory(
        self, dir_path: Path, cancel_event: asyncio.Event | None = None
    ) -> ToolResult:
        fd_path = await ensure_tool("fd", silent=True)
        if not fd_path:
            msg = "fd is not available and could not be downloaded"
            return ToolResult(success=False, result=msg, ui_summary=f"[red]{msg}[/red]")

        async def _entries(**kw) -> list[dict]:
            raw = await self._list_directory_entries(fd_path, dir_path, **kw)
            return [
                e if isinstance(e, dict) else {"line": e, "path": "", "is_dir": e.endswith("/")}
                for e in raw
            ]

        try:
            for max_depth in (3, 2):
                entries = await _entries(
                    max_depth=max_depth,
                    max_results=DIRECTORY_DEPTH_ROW_LIMIT + 1,
                    cancel_event=cancel_event,
                )
                if len(entries) <= DIRECTORY_DEPTH_ROW_LIMIT:
                    lines = [e["line"] for e in entries]
                    result = "\n".join(lines) if lines else "(empty directory)"
                    return ToolResult(
                        success=True,
                        result=result,
                        ui_summary=f"[dim]({len(entries)} entries)[/dim]",
                        ui_data={
                            "path": str(dir_path),
                            "is_dir": True,
                            "entries": entries,
                            "truncated": False,
                        },
                    )

            entries = await _entries(
                max_depth=1, max_results=MAX_DIRECTORY_ROWS + 1, cancel_event=cancel_event
            )
        except ToolCancelledError:
            return ToolResult(
                success=False, result="Read aborted", ui_summary="[yellow]Read aborted[/yellow]"
            )
        except RuntimeError as e:
            msg = str(e)
            return ToolResult(success=False, result=msg, ui_summary=f"[red]{msg}[/red]")

        truncated = len(entries) > MAX_DIRECTORY_ROWS
        visible_entries = entries[:MAX_DIRECTORY_ROWS]
        result = (
            "\n".join(e["line"] for e in visible_entries)
            if visible_entries
            else "(empty directory)"
        )
        if truncated:
            result += f"\n[output truncated after {MAX_DIRECTORY_ROWS} lines]"

        shown = min(len(entries), MAX_DIRECTORY_ROWS)
        return ToolResult(
            success=True,
            result=result,
            ui_summary=f"[dim]({shown} entries shown)[/dim]",
            ui_data={
                "path": str(dir_path),
                "is_dir": True,
                "entries": visible_entries,
                "truncated": truncated,
            },
        )

    async def execute(
        self, params: ReadParams, cancel_event: asyncio.Event | None = None
    ) -> ToolResult:
        # ~ and $VARS expand so home-dir and env paths work like they do in a shell.
        file_path = Path(os.path.expandvars(os.path.expanduser(params.path)))

        if not file_path.exists():
            msg = "Path not found"
            return ToolResult(success=False, result=msg, ui_summary=f"[red]{msg}[/red]")

        if file_path.is_dir():
            return await self.read_directory(file_path, cancel_event)

        if not file_path.is_file():
            msg = "Path is not a file or directory"
            return ToolResult(success=False, result=msg, ui_summary=f"[red]{msg}[/red]")

        if is_image_file(str(file_path)):
            try:
                base64_data, mime_type, resize_note = read_and_process_image(str(file_path))

                text_note = f"Read image file [{mime_type}]"
                if resize_note:
                    text_note += f" {resize_note}"

                display_note = "[dim]Read image[/dim]"
                if resize_note:
                    display_note = f"{display_note} {resize_note}"

                return ToolResult(
                    success=True,
                    result=text_note,
                    images=[ImageContent(data=base64_data, mime_type=mime_type)],
                    ui_summary=display_note,
                )
            except Exception as e:
                msg = f"Failed to read image: {e}"
                return ToolResult(success=False, result=msg, ui_summary=f"[red]{msg}[/red]")

        try:
            content = await self.read_file(file_path, params.start_line, params.end_line)
        except OSError as e:
            msg = f"Failed to read: {e}"
            return ToolResult(success=False, result=msg, ui_summary=f"[red]{msg}[/red]")

        lines_read = len(content.splitlines()) if content else 0
        paged = bool(params.start_line is not None or params.end_line is not None)
        preview_limit = lines_read if paged else PREVIEW_LINES
        preview = "\n".join(
            _strip_line_number(line) for line in content.splitlines()[:preview_limit]
        )
        return ToolResult(
            success=True,
            result=content,
            ui_summary=f"[dim]({lines_read} lines)[/dim]",
            ui_data={
                "path": str(file_path),
                "preview": preview,
                "start_line": params.start_line or 1,
                "paged": paged,
            },
        )
