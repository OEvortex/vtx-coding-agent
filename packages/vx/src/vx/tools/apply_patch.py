"""Apply patch tool for vx — apply edits to files."""

import asyncio
import difflib
import json
from pathlib import Path

import aiofiles
from ctx.agent.tools.base import BaseTool, ToolResult
from ctx.protocol.types import FileChanges
from pydantic import BaseModel, Field, field_validator, model_validator

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


def generate_diff_preview(old: str, new: str, max_lines: int = 12) -> list[tuple[str, str, str]]:
    """Generate fx-style diff preview lines: (op, line_number, text).

    Returns a list of tuples where op is '+', '-', or ' ' (context),
    line_number is the string representation of the new file line number,
    and text is the line content.
    """
    old_lines = old.splitlines()
    new_lines = new.splitlines()

    # Use SequenceMatcher to find matching blocks
    matcher = difflib.SequenceMatcher(None, old_lines, new_lines)

    result: list[tuple[str, str, str]] = []
    old_line_num = 0  # 1-based line number in old file
    new_line_num = 0  # 1-based line number in new file

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for _k, (oi, ni) in enumerate(zip(range(i1, i2), range(j1, j2), strict=True)):
                old_line_num = oi + 1
                new_line_num = ni + 1
                if len(result) < max_lines:
                    result.append((" ", str(new_line_num), new_lines[ni]))
        elif tag == "replace":
            # Remove old lines
            for oi in range(i1, i2):
                old_line_num = oi + 1
                if len(result) < max_lines:
                    result.append(("-", str(old_line_num), old_lines[oi]))
            # Add new lines
            for ni in range(j1, j2):
                new_line_num = ni + 1
                if len(result) < max_lines:
                    result.append(("+", str(new_line_num), new_lines[ni]))
        elif tag == "delete":
            for oi in range(i1, i2):
                old_line_num = oi + 1
                if len(result) < max_lines:
                    result.append(("-", str(old_line_num), old_lines[oi]))
        elif tag == "insert":
            for ni in range(j1, j2):
                new_line_num = ni + 1
                if len(result) < max_lines:
                    result.append(("+", str(new_line_num), new_lines[ni]))

    # If we truncated, add an elision marker
    if len(result) >= max_lines:
        result.append((" ", "", "..."))

    return result


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


#: Names a model reaches for that mean one of this tool's fields.
_OP_ALIASES = {
    "file_path": "path",
    "filename": "path",
    "old": "old_str",
    "new": "new_str",
    "replace": "new_str",
    "content": "new_file_content",
}


class PatchOp(BaseModel):
    path: str = Field(description="File path")
    old_str: str | None = Field(description="Text to find", default=None)
    new_str: str | None = Field(description="Replacement text", default=None)
    insert_line: int | None = Field(description="Line number to insert after", default=None)
    new_file_content: str | None = Field(description="Full content for new file", default=None)

    @model_validator(mode="before")
    @staticmethod
    def _accept_aliases(value: object) -> object:
        if not isinstance(value, dict):
            return value
        op = dict(value)
        for alias, field in _OP_ALIASES.items():
            if alias in op and field not in op:
                op[field] = op.pop(alias)
        return op


class ApplyPatchParams(BaseModel):
    ops: list[PatchOp] = Field(description="List of patch operations to apply")

    @field_validator("ops", mode="before")
    @staticmethod
    def _accept_shapes(value: object) -> object:
        """Take the shapes models send: one op, JSON text, or flat key/value pairs."""
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                return value
        if isinstance(value, dict):
            value = [value]
        if not isinstance(value, list):
            return value
        if value and all(isinstance(item, str) for item in value) and len(value) % 2 == 0:
            # The whole list flattened: ["path", "a.py", "old_str", "x", ...]
            return [{value[i]: value[i + 1] for i in range(0, len(value), 2)}]
        ops: list[object] = []
        for item in value:
            if isinstance(item, str):
                try:
                    item = json.loads(item)
                except json.JSONDecodeError:
                    ops.append(item)
                    continue
            if isinstance(item, (list, tuple)) and len(item) % 2 == 0:
                # A flattened pair list: ["path", "a.py", "old_str", "x", ...]
                item = {str(item[i]): item[i + 1] for i in range(0, len(item), 2)}
            ops.append(item)
        return ops


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
        paths = [op.path for op in params.ops]
        return ", ".join(shorten_path(p) for p in paths)

    async def _apply_op(
        self, op: PatchOp
    ) -> tuple[str, FileChanges | None, list[tuple[str, str, str]]]:
        path = Path(op.path)

        # Create new file
        if op.new_file_content is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            async with aiofiles.open(path, "w") as f:
                await f.write(op.new_file_content)
            diff_lines = generate_diff_preview("", op.new_file_content)
            return (
                f"Created {path}",
                FileChanges(
                    path=str(path), added=len(op.new_file_content.splitlines()), removed=0
                ),
                diff_lines,
            )

        # Insert at line
        if op.insert_line is not None and op.new_str is not None:
            if not path.exists():
                return f"File not found: {path}", None, []
            old_content = path.read_text()
            lines = old_content.splitlines(True)
            insert_idx = min(op.insert_line, len(lines))
            lines.insert(
                insert_idx, op.new_str if op.new_str.endswith("\n") else op.new_str + "\n"
            )
            new_content = "".join(lines)
            path.write_text(new_content)
            added = len(op.new_str.splitlines())
            diff_lines = generate_diff_preview(old_content, new_content)
            return (
                f"Inserted at line {op.insert_line} in {path}",
                FileChanges(path=str(path), added=added, removed=0),
                diff_lines,
            )

        # Search and replace
        if op.old_str is not None:
            if not path.exists():
                return f"File not found: {path}", None, []
            old_content = path.read_text()
            if op.old_str not in old_content:
                # Try to find close match for better error message
                return (
                    f"Search text not found in {path}. First 100 chars: {old_content[:100]!r}",
                    None,
                    [],
                )
            new_content = old_content.replace(op.old_str or "", op.new_str or "", 1)
            path.write_text(new_content)
            old_line_count = op.old_str.count("\n") + 1 if op.old_str else 0
            new_line_count = (op.new_str or "").count("\n") + 1 if op.new_str else 0
            diff_lines = generate_diff_preview(old_content, new_content)
            return (
                f"Edited {path}",
                FileChanges(path=str(path), added=new_line_count, removed=old_line_count),
                diff_lines,
            )

        return f"No operation specified for {path}", None, []

    async def execute(
        self, params: ApplyPatchParams, cancel_event: asyncio.Event | None = None
    ) -> ToolResult:
        if not params.ops:
            return ToolResult(success=False, result="No operations provided")

        results: list[str] = []
        file_changes: list[FileChanges] = []
        errors: list[str] = []
        all_diff_lines: list[tuple[str, str, str]] = []
        #: The diff per file: a later op on the same file supersedes the
        #: earlier preview, so each file is shown once, as it ended up.
        diff_by_path: dict[str, list[tuple[str, str, str]]] = {}

        for op in params.ops:
            try:
                result, changes, diff_lines = await self._apply_op(op)
                if changes:
                    file_changes.append(changes)
                if diff_lines:
                    all_diff_lines.extend(diff_lines)
                    diff_by_path[op.path] = diff_lines
                if result.startswith("File not found") or result.startswith(
                    "Search text not found"
                ):
                    errors.append(result)
                else:
                    results.append(result)
            except Exception as e:
                errors.append(f"Error editing {op.path}: {e}")

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

        # Convert diff lines to JSON-serializable format for ui_data
        diff_data = [[op, ln, text] for op, ln, text in all_diff_lines]

        return ToolResult(
            success=True,
            result="\n".join(results),
            ui_summary=", ".join(summary_parts),
            file_changes=file_changes if file_changes else None,
            ui_data=(
                {
                    "diff_lines": diff_data,
                    "diff_groups": [
                        {"path": path, "lines": [[op, ln, text] for op, ln, text in lines]}
                        for path, lines in diff_by_path.items()
                    ],
                }
                if diff_data
                else None
            ),
        )
