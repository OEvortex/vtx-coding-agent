import asyncio
import difflib
from pathlib import Path

import aiofiles
from pydantic import BaseModel, Field
from rich.markup import escape

from vtx.ai.agent.tools.base import BaseTool, ToolResult
from vtx.coding_agent.config import config
from vtx.coding_agent.diff_display import DIFF_BG_PAD_MARKER, blend_hex
from vtx.coding_agent.tools._tool_utils import shorten_path
from vtx.core.types import FileChanges

CONTEXT_LINES = 4


class EditParams(BaseModel):
    path: str = Field(description="Absolute path of file to edit")
    old_string: str = Field(description="Text to replace (exact match)")
    new_string: str = Field(description="Replacement text (must differ)")
    replace_all: bool = Field(description="Replace all occurrences", default=False)


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
            sign, line_num, content_part = parsed
            content = f"[{colors.diff_removed}]{escape(line_num)}  {escape(content_part)}[/{colors.diff_removed}]"
            formatted.append(f"[on {bg_removed}]{content}{DIFF_BG_PAD_MARKER}[/]")
        elif parsed and parsed[0] == "+":
            sign, line_num, content_part = parsed
            content = f"[{colors.diff_added}]{escape(line_num)}  {escape(content_part)}[/{colors.diff_added}]"
            formatted.append(f"[on {bg_added}]{content}{DIFF_BG_PAD_MARKER}[/]")
        elif "\u22ef" in line:
            escaped = escape(line)
            formatted.append(f"[{colors.dim}]{escaped}[/{colors.dim}]")
        else:
            escaped = escape(line)
            formatted.append(f"[{colors.dim}]{escaped}[/{colors.dim}]")

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
        result = f"Diff · +{added} -{removed}"
        ui_summary = (
            f"[{colors.dim}]Diff ·[/{colors.dim}] "
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
