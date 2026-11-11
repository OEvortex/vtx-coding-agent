import asyncio
import difflib
from pathlib import Path
from typing import Literal

import aiofiles
from pydantic import BaseModel, Field
from rich.markup import escape

from vtx.agent.tools.base import BaseTool, ToolResult
from vtx.coding_agent.tools._tool_utils import shorten_path
from vtx.core.config import config
from vtx.plugins import mount_tool, plugin
from vtx.protocol.types import FileChanges
from vtx.tui.diff_display import DIFF_BG_PAD_MARKER, blend_hex

CONTEXT_LINES = 4


class PatchEdit(BaseModel):
    old: str = Field(description="Exact text to replace (must match including whitespace)")
    new: str = Field(description="Replacement text (must differ from old)")
    replace_all: bool = Field(
        default=False, description="Replace all occurrences (default: first only)"
    )


class PatchOp(BaseModel):
    op: Literal["update", "create", "delete", "move"] = Field(
        description="Operation: update edits a file, create writes a new file, "
        "delete removes a file, move renames a file"
    )
    path: str = Field(description="Path of the file to operate on")
    old: str | None = Field(
        default=None,
        description="Exact text to replace — for a single 'update' edit. "
        "Must match file content including whitespace",
    )
    new: str | None = Field(default=None, description="Replacement text for 'old'")
    replace_all: bool = Field(
        default=False, description="Replace all occurrences of 'old' (default: first only)"
    )
    edits: list[PatchEdit] | None = Field(
        default=None, description="Multiple edits for one 'update' op — alternative to old/new"
    )
    content: str | None = Field(
        default=None, description="Full file content — required for 'create' ops"
    )
    new_path: str | None = Field(
        default=None, description="Destination path — required for 'move' ops"
    )


class ApplyPatchParams(BaseModel):
    ops: list[PatchOp] = Field(
        description="File operations applied atomically and in order. "
        "If any op fails, no changes are written."
    )


def _ellipsis(line_num_width: int, skipped: int) -> str:
    return f" {''.rjust(line_num_width)} \u22ef {skipped} unchanged lines hidden"


def generate_diff(
    old_content: str, new_content: str, context_lines: int = CONTEXT_LINES
) -> tuple[str, int, int]:
    """
    Generate a diff with line numbers and context.

    Returns:
        tuple: (diff_string, added_count, removed_count)

    Format:
        "- 42   removed line"    (minus, num, two spaces = removed)
        "+ 42   added line"      (plus, num, two spaces = added)
        " 42   context line"     (space, num, two spaces = context)
        "⋯ N unchanged lines hidden"  (ellipsis = skipped lines with count)
    """
    old_lines = old_content.splitlines()
    new_lines = new_content.splitlines()

    matcher = difflib.SequenceMatcher(None, old_lines, new_lines)
    opcodes = matcher.get_opcodes()

    max_line_num = max(len(old_lines), len(new_lines))
    line_num_width = len(str(max_line_num))

    def _num(n: int) -> str:
        return str(n).rjust(line_num_width)

    output: list[str] = []
    added, removed = 0, 0
    last_was_change = False

    for i, (tag, i1, i2, j1, j2) in enumerate(opcodes):
        if tag == "equal":
            equal_lines = old_lines[i1:i2]
            next_is_change = i < len(opcodes) - 1 and opcodes[i + 1][0] != "equal"

            if last_was_change or next_is_change:
                if last_was_change and next_is_change:
                    if len(equal_lines) > context_lines * 2:
                        for idx, line in enumerate(equal_lines[:context_lines]):
                            line_num = i1 + idx + 1
                            output.append(f" {_num(line_num)}   {line}")
                        skipped = len(equal_lines) - context_lines * 2
                        output.append(_ellipsis(line_num_width, skipped))
                        for idx, line in enumerate(equal_lines[-context_lines:]):
                            line_num = i1 + len(equal_lines) - context_lines + idx + 1
                            output.append(f" {_num(line_num)}   {line}")
                    else:
                        for idx, line in enumerate(equal_lines):
                            line_num = i1 + idx + 1
                            output.append(f" {_num(line_num)}   {line}")
                elif last_was_change:
                    if len(equal_lines) > context_lines:
                        for idx, line in enumerate(equal_lines[:context_lines]):
                            line_num = i1 + idx + 1
                            output.append(f" {_num(line_num)}   {line}")
                        skipped = len(equal_lines) - context_lines
                        output.append(_ellipsis(line_num_width, skipped))
                    else:
                        for idx, line in enumerate(equal_lines):
                            line_num = i1 + idx + 1
                            output.append(f" {_num(line_num)}   {line}")
                else:
                    if len(equal_lines) > context_lines:
                        skipped = len(equal_lines) - context_lines
                        output.append(_ellipsis(line_num_width, skipped))
                        for idx, line in enumerate(equal_lines[-context_lines:]):
                            line_num = i1 + len(equal_lines) - context_lines + idx + 1
                            output.append(f" {_num(line_num)}   {line}")
                    else:
                        for idx, line in enumerate(equal_lines):
                            line_num = i1 + idx + 1
                            output.append(f" {_num(line_num)}   {line}")

            last_was_change = False

        elif tag == "replace":
            for idx, line in enumerate(old_lines[i1:i2]):
                line_num = i1 + idx + 1
                output.append(f"- {_num(line_num)}   {line}")
                removed += 1
            for idx, line in enumerate(new_lines[j1:j2]):
                line_num = j1 + idx + 1
                output.append(f"+ {_num(line_num)}   {line}")
                added += 1
            last_was_change = True

        elif tag == "delete":
            for idx, line in enumerate(old_lines[i1:i2]):
                line_num = i1 + idx + 1
                output.append(f"- {_num(line_num)}   {line}")
                removed += 1
            last_was_change = True

        elif tag == "insert":
            for idx, line in enumerate(new_lines[j1:j2]):
                line_num = j1 + idx + 1
                output.append(f"+ {_num(line_num)}   {line}")
                added += 1
            last_was_change = True

    return "\n".join(output), added, removed


def _parse_diff_line(line: str) -> tuple[str, str, str] | None:
    """Parse a formatted diff line into (sign, line_number_part, content_part)."""
    if not line:
        return None

    sign = line[0]
    if sign not in ("-", "+", " "):
        return None

    rest = line[1:]
    num_start = next((i for i, char in enumerate(rest) if char.isdigit()), -1)
    if num_start == -1:
        return None

    num_end = num_start
    while num_end < len(rest) and rest[num_end].isdigit():
        num_end += 1

    line_number_part = rest[:num_end]
    content_part = (
        rest[num_end + 2 :] if num_end + 1 < len(rest) and rest[num_end] == " " else rest[num_end:]
    )
    return sign, line_number_part, content_part


def format_diff_display(diff: str) -> str:
    colors = config.ui.colors
    lines = diff.split("\n")
    formatted = []

    bg_added = blend_hex(colors.diff_added, colors.bg, alpha=0.24)
    bg_removed = blend_hex(colors.diff_removed, colors.bg, alpha=0.12)

    for line in lines:
        if not line:
            continue

        parsed = _parse_diff_line(line)

        if parsed and parsed[0] == "-":
            _sign, line_num, content_part = parsed
            content = (
                f"[{colors.diff_removed}]{escape(line_num)}  "
                f"{escape(content_part)}[/{colors.diff_removed}]"
            )
            formatted.append(f"[on {bg_removed}]{content}{DIFF_BG_PAD_MARKER}[/]")
        elif parsed and parsed[0] == "+":
            _sign, line_num, content_part = parsed
            content = (
                f"[{colors.diff_added}]{escape(line_num)}  "
                f"{escape(content_part)}[/{colors.diff_added}]"
            )
            formatted.append(f"[on {bg_added}]{content}{DIFF_BG_PAD_MARKER}[/]")
        elif "\u22ef" in line:
            escaped = escape(line)
            formatted.append(f"[{colors.dim}]{escaped}[/{colors.dim}]")
        else:
            escaped = escape(line)
            formatted.append(f"[{colors.dim}]{escaped}[/{colors.dim}]")

    return "\n".join(formatted)


_DELETED = object()  # state sentinel: path removed by an earlier op


class ApplyPatchTool(BaseTool):
    name = "apply_patch"
    tool_icon = "←"
    params = ApplyPatchParams
    prompt_guidelines = ("apply_patch for ALL file edits: update/create/delete/move",)
    description = (
        "Apply file edits atomically. ops: 'update' (path + old/new, or edits[] for "
        "several), 'create' (path + content), 'delete' (path), 'move' (path + new_path). "
        "old must match exactly incl. whitespace; any failure aborts all ops. "
        'E.g. {"ops":[{"op":"update","path":"a.py","old":"x","new":"y"}]}'
    )

    def format_call(self, params: ApplyPatchParams) -> str:
        paths = [shorten_path(op.path) for op in params.ops]
        shown = ", ".join(paths[:3])
        if len(paths) > 3:
            shown += f" +{len(paths) - 3} more"
        return shown

    def format_preview(self, params: ApplyPatchParams) -> str | None:
        parts = []
        for op in params.ops:
            if op.op == "update":
                if op.edits:
                    n = len(op.edits)
                    parts.append(f"update {op.path} ({n} edit{'s' if n != 1 else ''})")
                elif op.old is not None:
                    parts.append(f"update {op.path}")
            elif op.op == "create":
                parts.append(f"create {op.path}")
            elif op.op == "delete":
                parts.append(f"delete {op.path}")
            else:
                parts.append(f"move {op.path} -> {op.new_path or '?'}")
        return "\n".join(parts)

    async def execute(
        self, params: ApplyPatchParams, cancel_event: asyncio.Event | None = None
    ) -> ToolResult:
        # Validate and simulate every op against an in-memory view first, so a
        # failure anywhere aborts the whole patch before any byte is written.
        state: dict[Path, str | object] = {}
        moves_src_to_dst: dict[Path, Path] = {}
        moves_dst_to_src: dict[Path, Path] = {}

        def current(path: Path) -> str | None:
            """File content as the patch currently sees it; None if deleted/missing."""
            if path in state:
                v = state[path]
                return None if v is _DELETED else v  # type: ignore
            if not path.exists():
                return None
            try:
                return path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as e:
                raise ValueError(f"cannot read {path}: {e}") from e

        def exists(path: Path) -> bool:
            if path in state:
                return state[path] is not _DELETED
            return path.exists()

        def mark(path: Path, content: str | None) -> None:
            state[path] = _DELETED if content is None else content

        try:
            for idx, op in enumerate(params.ops):
                path = Path(op.path)
                label = f"op {idx + 1} ({op.op} {op.path})"

                if op.op == "update":
                    edits = list(op.edits or [])
                    if op.old is not None or op.new is not None:
                        if op.old is None or op.new is None:
                            raise ValueError(f"{label}: 'old' and 'new' must be provided together")
                        edits.append(PatchEdit(old=op.old, new=op.new, replace_all=op.replace_all))
                    if not edits:
                        raise ValueError(
                            f"{label}: provide 'old'+'new' or a non-empty 'edits' list"
                        )
                    content = current(path)
                    if content is None:
                        raise ValueError(f"{label}: file not found")
                    new_content = content
                    for edit in edits:
                        if edit.old == edit.new:
                            raise ValueError(f"{label}: old == new in an edit")
                        if edit.old not in new_content:
                            raise ValueError(f"{label}: old_string not found in file")
                        count = -1 if edit.replace_all else 1
                        new_content = new_content.replace(edit.old, edit.new, count)
                    mark(path, new_content)

                elif op.op == "create":
                    if op.content is None:
                        raise ValueError(f"{label}: 'content' is required")
                    if exists(path):
                        raise ValueError(f"{label}: file already exists")
                    mark(path, op.content)

                elif op.op == "delete":
                    if not exists(path):
                        raise ValueError(f"{label}: file not found")
                    mark(path, None)

                else:  # move
                    if not op.new_path:
                        raise ValueError(f"{label}: 'new_path' is required")
                    content = current(path)
                    if content is None:
                        raise ValueError(f"{label}: file not found")
                    dest = Path(op.new_path)
                    if exists(dest):
                        raise ValueError(f"{label}: destination already exists")
                    moves_src_to_dst[path] = dest
                    moves_dst_to_src[dest] = path
                    mark(dest, content)
                    mark(path, None)
        except ValueError as e:
            msg = str(e)
            return ToolResult(success=False, result=msg, ui_summary=f"[red]{msg}[/red]")

        # All ops validated — write the changes.
        file_changes: list[FileChanges] = []
        diff_sections: list[str] = []
        diff_sections_full: list[str] = []
        result_parts: list[str] = []
        total_added = total_removed = 0

        def _line_count(text: str) -> int:
            return text.count("\n") + (0 if text.endswith("\n") or not text else 1)

        try:
            for path, value in state.items():
                old_content: str | None = None
                if path.exists():
                    try:
                        async with aiofiles.open(path, encoding="utf-8") as f:
                            old_content = await f.read()
                    except (OSError, UnicodeDecodeError):
                        old_content = None

                if value is _DELETED:
                    if path.exists():
                        path.unlink()
                    if path in moves_src_to_dst:
                        continue  # reported as a move at the destination
                    removed = _line_count(old_content) if old_content else 0
                    file_changes.append(FileChanges(path=str(path), added=0, removed=removed))
                    total_removed += removed
                    result_parts.append(f"deleted {shorten_path(str(path))}")
                    continue

                new_content = str(value)
                header = f"[bold]{escape(shorten_path(str(path)))}[/bold]"
                if path in moves_dst_to_src:
                    src = moves_dst_to_src[path]
                    path.parent.mkdir(parents=True, exist_ok=True)
                    async with aiofiles.open(path, "w", encoding="utf-8") as f:
                        await f.write(new_content)
                    result_parts.append(
                        f"moved {shorten_path(str(src))} -> {shorten_path(str(path))}"
                    )
                    continue
                if old_content is not None:
                    diff, added, removed = generate_diff(old_content, new_content)
                    total_lines = max(old_content.count("\n"), new_content.count("\n")) + 1
                    diff_full, _, _ = generate_diff(
                        old_content, new_content, context_lines=total_lines
                    )
                    diff_sections.append(header + "\n" + format_diff_display(diff))
                    diff_sections_full.append(format_diff_display(diff_full))
                    file_changes.append(FileChanges(path=str(path), added=added, removed=removed))
                    total_added += added
                    total_removed += removed
                    result_parts.append(f"updated {shorten_path(str(path))} (+{added} -{removed})")
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    async with aiofiles.open(path, "w", encoding="utf-8") as f:
                        await f.write(new_content)
                    added = _line_count(new_content)
                    create_diff, _, _ = generate_diff("", new_content)
                    diff_sections.append(header + "\n" + format_diff_display(create_diff))
                    diff_sections_full.append(
                        format_diff_display(generate_diff("", new_content, context_lines=10**6)[0])
                    )
                    file_changes.append(FileChanges(path=str(path), added=added, removed=0))
                    total_added += added
                    result_parts.append(f"created {shorten_path(str(path))} (+{added})")

                if old_content is not None:
                    async with aiofiles.open(path, "w", encoding="utf-8") as f:
                        await f.write(new_content)
        except OSError as e:
            msg = f"Failed to apply patch: {e}"
            return ToolResult(success=False, result=msg, ui_summary=f"[red]{msg}[/red]")

        colors = config.ui.colors
        n_files = len(state)
        result = f"Patched {n_files} file{'s' if n_files != 1 else ''}: " + ", ".join(result_parts)
        ui_summary = (
            f"[{colors.dim}]Patch ·[/{colors.dim}] "
            f"[bold]{n_files}[/bold] file{'s' if n_files != 1 else ''} "
            f"[{colors.diff_added}]+{total_added}[/{colors.diff_added}] "
            f"[{colors.diff_removed}]-{total_removed}[/{colors.diff_removed}]"
        )
        ui_details = "\n\n".join(diff_sections) if diff_sections else None
        ui_details_full = "\n\n".join(diff_sections_full) if diff_sections_full else None

        return ToolResult(
            success=True,
            result=result,
            ui_summary=ui_summary,
            ui_details=ui_details,
            ui_details_full=ui_details_full,
            file_changes=file_changes,
        )


@plugin(name="tool-apply-patch", inject=("tools",))
def apply(ctx, config):
    mount_tool(ctx, ApplyPatchTool(), is_default=True, parent_only=False)


PLUGIN = apply
