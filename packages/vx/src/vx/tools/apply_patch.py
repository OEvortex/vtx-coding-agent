"""Apply patch tool for vx — apply edits to files."""

import ast
import asyncio
import difflib
import json
import warnings
from contextlib import suppress
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


#: Context lines kept around each change; longer runs collapse to a marker.
DIFF_CONTEXT = 3


def generate_diff_preview(
    old: str, new: str, context: int = DIFF_CONTEXT
) -> list[tuple[str, str, str]]:
    """The change as rows: ``(op, line_number, text)``, one per line.

    ``op`` is ``+``, ``-`` or a space for context, and ``~`` marks a run of
    unchanged lines that was dropped: its line number is the count. Keeping
    the hunk small this way means a patch in a two-thousand-line file still
    shows where it landed without printing the file.
    """
    old_lines = old.splitlines()
    new_lines = new.splitlines()
    matcher = difflib.SequenceMatcher(None, old_lines, new_lines)

    rows: list[tuple[str, str, str]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "replace":
            rows.extend(("-", str(oi + 1), old_lines[oi]) for oi in range(i1, i2))
            rows.extend(("+", str(ni + 1), new_lines[ni]) for ni in range(j1, j2))
        elif tag == "delete":
            rows.extend(("-", str(oi + 1), old_lines[oi]) for oi in range(i1, i2))
        elif tag == "insert":
            rows.extend(("+", str(ni + 1), new_lines[ni]) for ni in range(j1, j2))
        else:
            rows.extend((" ", str(ni + 1), new_lines[ni]) for ni in range(j1, j2))
    return _collapse_context(rows, context)


def _collapse_context(
    rows: list[tuple[str, str, str]], context: int
) -> list[tuple[str, str, str]]:
    """Keep ``context`` lines around each change and count the rest."""
    changed = [index for index, (op, _ln, _text) in enumerate(rows) if op in "+-"]
    if not changed:
        return rows
    keep: set[int] = set()
    for index in changed:
        keep.update(range(max(0, index - context), min(len(rows), index + context + 1)))
    out: list[tuple[str, str, str]] = []
    skipped = 0
    for index, row in enumerate(rows):
        if index in keep:
            if skipped:
                out.append(("~", str(skipped), ""))
                skipped = 0
            out.append(row)
        else:
            skipped += 1
    if skipped:
        out.append(("~", str(skipped), ""))
    return out


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


#: What a model is told when its ops arrive as text nothing can parse.
_BAD_OPS = (
    "`ops` arrived as a string, not a list of objects, and it does not parse as "
    "JSON or a Python literal (a quote inside it is unbalanced). Send `ops` as a "
    'real JSON array: [{"path": ..., "old_str": ..., "new_str": ...}]. '
    "To replace a whole file, use one op with `new_file_content` instead of "
    "passing the whole file as `old_str`; for small changes keep `old_str` short."
)


def _parse_ops_text(value: str) -> object:
    """A string of ops, as best it can be read: JSON first, then Python.

    Models hand `ops` over as text far more often than as an array, and a
    triple-quoted block is Python, not JSON. Neither parser is allowed to
    guess: half a patch applied is worse than a refusal that says why.
    """
    text = value.strip()
    if text.startswith("```"):
        # A fenced block: the model's habit of wrapping the payload in markdown.
        text = text.split("\n", 1)[-1]
        text = text.rsplit("```", 1)[0].strip()
    with suppress(ValueError, SyntaxError):
        return json.loads(text)
    with suppress(ValueError, SyntaxError), warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        return ast.literal_eval(text)
    return value


class ApplyPatchParams(BaseModel):
    ops: list[PatchOp] | str = Field(description="List of patch operations to apply")

    @field_validator("ops", mode="before")
    @staticmethod
    def _accept_shapes(value: object) -> object:
        """Take the shapes models send: one op, JSON text, or flat key/value pairs."""
        if isinstance(value, str):
            value = _parse_ops_text(value)
            if isinstance(value, str):
                # Unparseable: kept as text so `execute` can say what to do
                # instead of dying in validation, where the reason is lost.
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
    description = (
        "Apply precise edits to files. Each op: search+replace (old_str→new_str), "
        "insert at line, or create new file (new_file_content). "
        "old_str must match exactly (whitespace-sensitive)."
    )

    def format_call(self, params: ApplyPatchParams) -> str:
        if isinstance(params.ops, str):
            return "unreadable ops"
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
        if isinstance(params.ops, str):
            return ToolResult(success=False, result=_BAD_OPS, ui_summary="unreadable ops")
        if not params.ops:
            return ToolResult(success=False, result="No operations provided")

        results: list[str] = []
        file_changes: list[FileChanges] = []
        errors: list[str] = []
        #: The diff per file: a later op on the same file supersedes the
        #: earlier preview, so each file is shown once, as it ended up.
        diff_by_path: dict[str, list[tuple[str, str, str]]] = {}
        #: Each file's content before the first op touched it, so several ops
        #: on one file still produce one diff of the whole change.
        before: dict[str, str] = {}

        for op in params.ops:
            if op.new_file_content is None and op.path not in before:
                with suppress(OSError):
                    before[op.path] = Path(op.path).read_text()
            try:
                result, changes, diff_lines = await self._apply_op(op)
                if changes:
                    file_changes.append(changes)
                if diff_lines:
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

        for path, start in before.items():
            try:
                end = Path(path).read_text()
            except OSError:
                continue
            if path in diff_by_path and end != start:
                diff_by_path[path] = generate_diff_preview(start, end)

        # Convert diff lines to JSON-serializable format for ui_data
        diff_data = [[op, ln, text] for lines in diff_by_path.values() for op, ln, text in lines]

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
