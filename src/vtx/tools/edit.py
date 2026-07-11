import asyncio
import difflib
from pathlib import Path

import aiofiles
from pydantic import BaseModel, Field
from rich.markup import escape

from vtx import config
from vtx.diff_display import DIFF_BG_PAD_MARKER, blend_hex

from ..core.types import FileChanges
from ._tool_utils import shorten_path
from .base import BaseTool, ToolResult

CONTEXT_LINES = 4


class EditParams(BaseModel):
    path: str = Field(description="Absolute path of file to edit")
    old_string: str = Field(description="Text to replace (exact match)")
    new_string: str = Field(description="Replacement text (must differ)")
    replace_all: bool = Field(description="Replace all occurrences", default=False)


def _ellipsis(line_num_width: int, skipped: int) -> str:
    return f" {''.rjust(line_num_width)} \u22ef {skipped} lines \u22ef"  # ⋯ N lines ⋯


def generate_diff(
    old_content: str, new_content: str, context_lines: int = CONTEXT_LINES
) -> tuple[str, int, int]:
    """
    Generate a diff with line numbers and context.

    Returns:
        tuple: (diff_string, added_count, removed_count)

    Format:
        " 42   context line"    (space, num, three spaces = empty change marker)
        " 42 - removed line"    (space, num, space-minus-space = removed)
        " 42 + added line"      (space, num, space-plus-space = added)
        "    ⋯ N lines ⋯"       (ellipsis = skipped lines with count)
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
                output.append(f" {_num(line_num)} - {line}")
                removed += 1
            for idx, line in enumerate(new_lines[j1:j2]):
                line_num = j1 + idx + 1
                output.append(f" {_num(line_num)} + {line}")
                added += 1
            last_was_change = True

        elif tag == "delete":
            for idx, line in enumerate(old_lines[i1:i2]):
                line_num = i1 + idx + 1
                output.append(f" {_num(line_num)} - {line}")
                removed += 1
            last_was_change = True

        elif tag == "insert":
            for idx, line in enumerate(new_lines[j1:j2]):
                line_num = j1 + idx + 1
                output.append(f" {_num(line_num)} + {line}")
                added += 1
            last_was_change = True

    return "\n".join(output), added, removed


def _parse_diff_line(line: str) -> tuple[str, str, str] | None:
    """Parse a formatted diff line into (line_number_part, sign, content_part)."""
    num_start = next((i for i, char in enumerate(line) if char.isdigit()), -1)
    if num_start == -1:
        return None

    num_end = num_start
    while num_end < len(line) and line[num_end].isdigit():
        num_end += 1

    sign_index = num_end + 1
    if sign_index >= len(line):
        return None

    # Includes leading padding and the separator space after the line number.
    line_number_part = line[:sign_index]
    sign = line[sign_index]
    content_part = line[sign_index + 1 :]
    return line_number_part, sign, content_part


def format_diff_display(diff: str) -> str:
    colors = config.ui.colors
    lines = diff.split("\n")
    formatted = []

    bg_added = blend_hex(colors.diff_added, colors.bg)
    bg_removed = blend_hex(colors.diff_removed, colors.bg)

    for line in lines:
        if not line:
            continue

        truncated = line[:200] + "\u2026" if len(line) > 203 else line  # … ellipsis
        parsed = _parse_diff_line(truncated)

        if parsed and parsed[1] == "-":
            line_num, sign, content_part = parsed
            content = (
                f"  [{colors.dim}]{escape(line_num)}[/{colors.dim}]"
                f"[{colors.diff_removed}]{sign}{escape(content_part)}[/{colors.diff_removed}]"
            )
            formatted.append(f"[on {bg_removed}]{content}{DIFF_BG_PAD_MARKER}[/]")
        elif parsed and parsed[1] == "+":
            line_num, sign, content_part = parsed
            content = (
                f"  [{colors.dim}]{escape(line_num)}[/{colors.dim}]"
                f"[{colors.diff_added}]{sign}{escape(content_part)}[/{colors.diff_added}]"
            )
            formatted.append(f"[on {bg_added}]{content}{DIFF_BG_PAD_MARKER}[/]")
        elif "\u22ef" in line:
            escaped = escape(truncated)
            formatted.append(f"[{colors.dim}]{escaped}[/{colors.dim}]")
        else:
            escaped = escape(truncated)
            formatted.append(f"[{colors.dim}]  {escaped}[/{colors.dim}]")

    return "\n".join(formatted)


class EditTool(BaseTool):
    name = "edit"
    tool_icon = "←"
    params = EditParams
    prompt_guidelines = ("edit (not sed/awk)",)
    description = "Replace exact text in a file. old_string must match exactly (incl. whitespace)."

    def format_call(self, params: EditParams) -> str:
        return shorten_path(params.path)

    def format_preview(self, params: EditParams) -> str | None:
        diff, _, _ = generate_diff(params.old_string, params.new_string)
        return format_diff_display(diff)

    async def execute(
        self, params: EditParams, cancel_event: asyncio.Event | None = None
    ) -> ToolResult:
        file_path = Path(params.path)

        if not file_path.exists():
            msg = f"File not found: {file_path}"
            return ToolResult(success=False, result=msg, ui_summary=f"[red]{msg}[/red]")

        async with aiofiles.open(file_path, encoding="utf-8") as f:
            content = await f.read()

        if params.old_string not in content:
            msg = "old_string not found in file"
            return ToolResult(success=False, result=msg, ui_summary=f"[red]{msg}[/red]")

        if params.replace_all:
            new_content = content.replace(params.old_string, params.new_string)
        else:
            new_content = content.replace(params.old_string, params.new_string, 1)

        async with aiofiles.open(file_path, "w", encoding="utf-8") as f:
            await f.write(new_content)

        diff, added, removed = generate_diff(content, new_content)
        diff_display = format_diff_display(diff)

        # Full diff for expanded view (no context truncation)
        total_lines = max(content.count("\n"), new_content.count("\n")) + 1
        diff_full, _, _ = generate_diff(content, new_content, context_lines=total_lines)
        diff_full_display = format_diff_display(diff_full)

        colors = config.ui.colors
        result = f"Updated {file_path} +{added} -{removed}"
        ui_summary = (
            f"[{colors.diff_added}]+{added}[/{colors.diff_added}] "
            f"[{colors.diff_removed}]-{removed}[/{colors.diff_removed}]"
        )

        return ToolResult(
            success=True,
            result=result,
            ui_summary=ui_summary,
            ui_details=diff_display,
            ui_details_full=diff_full_display,
            file_changes=FileChanges(path=str(file_path), added=added, removed=removed),
        )
