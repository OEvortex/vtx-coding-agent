"""Apply patch tool for vx — apply edits to files."""

import difflib
from pathlib import Path

import aiofiles
from ctx.agent.tools.base import BaseTool, ToolResult
from ctx.protocol.types import FileChanges
from pydantic import BaseModel, Field

from vx.tools._tool_utils import shorten_path

MAX_PATCH_BYTES = 200 * 1024


def generate_diff(old: str, new: str) -> tuple[str, int, int]:
    """Generate a unified diff between old and new content."""
    old_lines = old.splitlines(keepends=True)
    new_lines = new.splitlines(keepends=True)
    diff = list(difflib.unified_diff(old_lines, new_lines, n=3))
    added = sum(1 for line in diff if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in diff if line.startswith("-") and not line.startswith("---"))
    return "".join(diff), added, removed


def format_diff_display(diff: str) -> str:
    """Format a diff for display in the TUI."""
    lines = []
    for line in diff.split("\n"):
        if line.startswith("@@"):
            lines.append(f"[dim]{line}[/dim]")
        elif line.startswith("+"):
            lines.append(f"[green]{line}[/green]")
        elif line.startswith("-"):
            lines.append(f"[red]{line}[/red]")
        elif line.startswith("==="):
            lines.append(f"[dim]{line}[/dim]")
        else:
            lines.append(f" {line}")
    return "\n".join(lines)


class PatchEdit(BaseModel):
    file_path: str = Field(description="Path to the file to edit")
    old_str: str = Field(description="Text to search for (must match exactly)")
    new_str: str = Field(description="Text to replace with")


class PatchOp(BaseModel):
    path: str = Field(description="File path")
    file_path: str = Field(description="File path (alias for path)", default="")
    old_str: str | None = Field(description="Text to find", default=None)
    new_str: str | None = Field(description="Replacement text", default=None)
    insert_line: int | None = Field(description="Line number to insert after", default=None)
    new_file_content: str | None = Field(description="Full content for new file", default=None)

    @property
    def target_path(self) -> str:
        return self.file_path or self.path


class ApplyPatchParams(BaseModel):
    ops: list[PatchOp] = Field(description="List of patch operations to apply")


class ApplyPatchTool(BaseTool):
    name = "apply_patch"
    tool_icon = "✏"
    params = ApplyPatchParams
    mutating = True
    parallel_safe = False
    prompt_guidelines = ("apply_patch for edits (not bash sed)",)
    description = (
        "Apply precise edits to files. Each op: search+replace (old_str→new_str), "
        "insert at line, or create new file (new_file_content). "
        "old_str must match exactly (whitespace-sensitive)."
    )

    def format_call(self, params: ApplyPatchParams) -> str:
        paths = [op.target_path for op in params.ops]
        return ", ".join(shorten_path(p) for p in paths)

    async def _apply_op(self, op: PatchOp) -> tuple[str, FileChanges | None]:
        path = Path(op.target_path)

        # Create new file
        if op.new_file_content is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            async with aiofiles.open(path, "w") as f:
                await f.write(op.new_file_content)
            return f"Created {path}", FileChanges(
                file_path=str(path), added=len(op.new_file_content.splitlines()), removed=0
            )

        # Insert at line
        if op.insert_line is not None and op.new_str is not None:
            if not path.exists():
                return f"File not found: {path}", None
            content = path.read_text()
            lines = content.splitlines(True)
            insert_idx = min(op.insert_line, len(lines))
            lines.insert(
                insert_idx, op.new_str if op.new_str.endswith("\n") else op.new_str + "\n"
            )
            path.write_text("".join(lines))
            added = len(op.new_str.splitlines())
            return f"Inserted at line {op.insert_line} in {path}", FileChanges(
                file_path=str(path), added=added, removed=0
            )

        # Search and replace
        if op.old_str is not None:
            if not path.exists():
                return f"File not found: {path}", None
            content = path.read_text()
            if op.old_str not in content:
                # Try to find close match for better error message
                return (
                    f"Search text not found in {path}. First 100 chars: {content[:100]!r}",
                    None,
                )
            new_content = content.replace(op.old_str, op.new_str or "", 1)
            path.write_text(new_content)
            old_lines = op.old_str.count("\n") + 1 if op.old_str else 0
            new_lines = (op.new_str or "").count("\n") + 1 if op.new_str else 0
            return f"Edited {path}", FileChanges(
                file_path=str(path), added=new_lines, removed=old_lines
            )

        return f"No operation specified for {path}", None

    async def execute(self, params: ApplyPatchParams) -> ToolResult:
        if not params.ops:
            return ToolResult(success=False, result="No operations provided")

        results: list[str] = []
        file_changes: list[FileChanges] = []
        errors: list[str] = []

        for op in params.ops:
            try:
                result, changes = await self._apply_op(op)
                if changes:
                    file_changes.append(changes)
                if result.startswith("File not found") or result.startswith(
                    "Search text not found"
                ):
                    errors.append(result)
                else:
                    results.append(result)
            except Exception as e:
                errors.append(f"Error editing {op.target_path}: {e}")

        if errors and not results:
            return ToolResult(
                success=False,
                result="\n".join(errors),
                ui_summary=f"[red]{len(errors)} error(s)[/red]",
            )

        summary_parts = [r for r in results[:3]]
        if len(results) > 3:
            summary_parts.append(f"... and {len(results) - 3} more")
        if errors:
            summary_parts.append(f"[yellow]{len(errors)} warning(s)[/yellow]")

        return ToolResult(
            success=True,
            result="\n".join(results),
            ui_summary=", ".join(summary_parts),
            file_changes=file_changes if file_changes else None,
        )
