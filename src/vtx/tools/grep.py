import asyncio
import os

from pydantic import BaseModel, Field

from ..core.types import ToolResult
from ..tools_manager import ensure_tool
from ._tool_utils import (
    ToolCancelledError,
    communicate_or_cancel,
    shorten_path,
    truncate_lines_by_bytes,
)
from .base import BaseTool

MAX_RESULTS = 100
MAX_OUTPUT_BYTES = 30 * 1024


class GrepParams(BaseModel):
    pattern: str = Field(description="Text or regex to search for")
    path: str | None = Field(description="Dir or file to search (default: cwd)", default=None)
    glob: str | None = Field(description="File filter glob, e.g. '*.py'", default=None)


class GrepTool(BaseTool[GrepParams]):
    name = "grep"
    tool_icon = "🔎"
    params = GrepParams
    mutating = False
    prompt_guidelines = ("grep for text in files (not bash grep/rg)",)
    description = (
        "Search file contents by regex (ripgrep). Returns matching lines with path:line, "
        f"respects .gitignore, truncated to {MAX_RESULTS}."
    )

    def format_call(self, params: GrepParams) -> str:
        parts = [f'"{params.pattern}"']
        if params.glob:
            parts.append(f'in "{params.glob}"')
        if params.path:
            parts.append(f"under {shorten_path(params.path)}")
        return " ".join(parts)

    async def execute(
        self, params: GrepParams, cancel_event: asyncio.Event | None = None
    ) -> ToolResult:
        rg_path = await ensure_tool("rg", silent=True)
        search_path = params.path or os.getcwd()
        if not os.path.isabs(search_path):
            search_path = os.path.join(os.getcwd(), search_path)

        stdout = ""
        used_fallback = None

        if rg_path:
            cmd = [
                rg_path,
                "--line-number",
                "--no-heading",
                "--color=never",
                params.pattern,
                search_path,
            ]
            if params.glob:
                cmd.extend(["-g", params.glob])

            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                )
                raw_stdout, _ = await communicate_or_cancel(proc, cancel_event)
                stdout = raw_stdout.decode("utf-8", errors="replace")
                if proc.returncode not in (0, 1):
                    rg_path = None
            except (OSError, ToolCancelledError) as e:
                if isinstance(e, ToolCancelledError):
                    return ToolResult(
                        success=False, result="Cancelled", ui_summary="[yellow]Cancelled[/yellow]"
                    )
                rg_path = None

        if not rg_path:
            # Fallback 1: system grep
            import shutil

            grep_path = shutil.which("grep")
            if grep_path:
                used_fallback = "grep"
                cmd = [
                    grep_path,
                    "-rnI",
                    "--exclude-dir=.git",
                    "--exclude-dir=node_modules",
                    "--exclude-dir=.venv",
                    "--exclude-dir=.ruff_cache",
                    "--exclude-dir=.vtx",
                ]
                if params.glob:
                    cmd.append(f"--include={params.glob}")
                cmd.extend([params.pattern, search_path])

                try:
                    proc = await asyncio.create_subprocess_exec(
                        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                    )
                    raw_stdout, _ = await communicate_or_cancel(proc, cancel_event)
                    stdout = raw_stdout.decode("utf-8", errors="replace")
                except ToolCancelledError:
                    return ToolResult(
                        success=False, result="Cancelled", ui_summary="[yellow]Cancelled[/yellow]"
                    )
                except OSError:
                    grep_path = None

            if not grep_path:
                # Fallback 2: python builtin search
                used_fallback = "python"
                import fnmatch
                import re

                try:
                    rx = re.compile(params.pattern)
                except re.error:
                    rx = None

                matches = []
                ignored_dirs = {
                    ".git",
                    "node_modules",
                    ".venv",
                    "__pycache__",
                    ".vtx",
                    ".ruff_cache",
                    "build",
                    "dist",
                }

                try:
                    for root, dirs, files in os.walk(search_path):
                        if cancel_event and cancel_event.is_set():
                            return ToolResult(
                                success=False,
                                result="Cancelled",
                                ui_summary="[yellow]Cancelled[/yellow]",
                            )
                        # yield control to loop periodically
                        await asyncio.sleep(0)

                        dirs[:] = [d for d in dirs if d not in ignored_dirs]
                        for file in files:
                            full_path = os.path.join(root, file)
                            rel_path = os.path.relpath(full_path, search_path)

                            if params.glob:
                                if params.glob.startswith("!"):
                                    neg_pattern = params.glob[1:]
                                    if fnmatch.fnmatch(rel_path, neg_pattern) or fnmatch.fnmatch(
                                        file, neg_pattern
                                    ):
                                        continue
                                else:
                                    if not (
                                        fnmatch.fnmatch(rel_path, params.glob)
                                        or fnmatch.fnmatch(file, params.glob)
                                    ):
                                        continue

                            try:
                                with open(full_path, encoding="utf-8", errors="ignore") as f:
                                    for line_num, line in enumerate(f, 1):
                                        matched = False
                                        if rx:
                                            if rx.search(line):
                                                matched = True
                                        else:
                                            if params.pattern in line:
                                                matched = True
                                        if matched:
                                            matches.append(
                                                f"{full_path}:{line_num}:{line.rstrip('\r\n')}"
                                            )
                                            if len(matches) >= MAX_RESULTS + 10:
                                                break
                            except Exception:
                                continue
                            if len(matches) >= MAX_RESULTS + 10:
                                break
                        if len(matches) >= MAX_RESULTS + 10:
                            break
                except Exception as e:
                    return ToolResult(
                        success=False,
                        result=f"Python search failed: {e}",
                        ui_summary="[red]Failed[/red]",
                    )

                stdout = "\n".join(matches)

        if not stdout and not used_fallback:
            return ToolResult(success=True, result="No matches found.", ui_summary="No matches")

        lines = stdout.splitlines()
        n_matches = len(lines)
        if n_matches > MAX_RESULTS:
            lines = lines[:MAX_RESULTS]
            lines.append(f"... ({n_matches - MAX_RESULTS} more matches)")

        truncated_str, _ = truncate_lines_by_bytes(lines, MAX_OUTPUT_BYTES)

        ui_summary = f"{n_matches} match{'es' if n_matches != 1 else ''}"
        if used_fallback:
            ui_summary += f" ({used_fallback} fallback)"
        return ToolResult(success=True, result=truncated_str, ui_summary=ui_summary)
