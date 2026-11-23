"""The in-chat ``apply_patch`` tool block.

While arguments stream this shows a live diff-shaped preview in the header
and a numbered/diff body; the finished result is rendered by the generic
``ToolBlock`` from ``ui_details`` (the tool already supplies diff markup).
Keeping that streaming logic here means the generic block never needs to
know what an ``ops`` payload looks like.
"""

from __future__ import annotations

from ctx.core.config import config
from ctx.term import boxed
from ctx.term.diff_display import DIFF_BG_PAD_MARKER, blend_hex
from rich.text import Text

from .base import CodingToolBlock


class ApplyPatchToolBlock(CodingToolBlock):
    """Live and finished diff rendering for ``apply_patch``."""

    def _source_path(self) -> str | None:
        first = boxed.strip_markup(self._call_msg or "").split("\n", 1)[0].strip()
        if not first or first.startswith("$"):
            return None
        path = first.strip()
        return path or None

    def _rerender_body_on_args(self) -> bool:
        return True

    def _streaming_file_preview(self, args: dict) -> str:
        """Live diff-style preview of an apply_patch payload while args stream.

        Deliberately not a real diff: computing one per frame is O(file) and
        the partial args change every delta. Instead show the incoming
        content's tail as ``+``/``-`` lines with diff colors — reads like the
        file filling in, costs a few µs per frame. The real diff still
        renders from the tool result when the call finishes.
        """
        ops = args.get("ops")
        if not isinstance(ops, list):
            return ""
        added_src_parts: list[str] = []
        removed_src_parts: list[str] = []
        first_path = ""
        for op in ops:
            if not isinstance(op, dict):
                continue
            if not first_path and isinstance(op.get("path"), str):
                first_path = op["path"]
            if isinstance(op.get("content"), str):
                added_src_parts.append(op["content"])
            if isinstance(op.get("old"), str):
                removed_src_parts.append(op["old"])
            if isinstance(op.get("new"), str):
                added_src_parts.append(op["new"])
            for edit in op.get("edits") or []:
                if not isinstance(edit, dict):
                    continue
                if isinstance(edit.get("new"), str):
                    added_src_parts.append(edit["new"])
                if isinstance(edit.get("old"), str):
                    removed_src_parts.append(edit["old"])
        added_src = "\n".join(added_src_parts)
        removed_src = "\n".join(removed_src_parts)
        if not added_src:
            return ""

        colors = config.ui.colors
        bg_added = blend_hex(colors.diff_added, colors.bg)
        bg_removed = blend_hex(colors.diff_removed, colors.bg)

        max_side = 15

        def _tail(src: str) -> list[str]:
            return src[-6000:].splitlines()[-max_side:]

        def _count(src: str) -> int:
            return src.count("\n") + (0 if src.endswith("\n") else 1)

        removed_tail = _tail(removed_src) if removed_src else []
        added_tail = _tail(added_src)

        total_added = _count(added_src)
        total_removed = _count(removed_src) if removed_src else 0
        hidden_added = total_added - len(added_tail)
        hidden_removed = total_removed - len(removed_tail)

        meta_parts = []
        if first_path:
            meta_parts.append(first_path)
        if len(ops) > 1:
            meta_parts.append(f"{len(ops)} ops")
        meta_parts.append(f"[{colors.diff_added}]+{total_added}[/{colors.diff_added}]")
        meta_parts.append(f"[{colors.diff_removed}]-{total_removed}[/{colors.diff_removed}]")
        meta = "  ".join(meta_parts)
        if hidden_added > 0 or hidden_removed > 0:
            meta += f"  [dim]…{hidden_added + hidden_removed} earlier[/dim]"

        # The header truncates call messages to MAX_HEADER_LINES, so emit a
        # compact two-line preview: meta + the freshest content line. Older
        # lines are covered by the "…N earlier" count.
        lines = [f"[dim]{meta}[/dim]"]
        last = ""
        sign = color = bg = ""
        if added_tail:
            last, sign, color, bg = added_tail[-1], "+", colors.diff_added, bg_added
        elif removed_tail:
            last, sign, color, bg = (removed_tail[-1], "-", colors.diff_removed, bg_removed)
        if last:
            esc = last.replace("[", "\\[")
            lines.append(f"[{color} on {bg}]{sign} {esc}{DIFF_BG_PAD_MARKER}[/]")
        return "\n".join(lines)

    def _streaming_body_text(self) -> Text | None:
        """apply_patch body while args stream: numbered preview or diff."""
        args = self._partial_args or {}
        ops = args.get("ops")
        if not isinstance(ops, list) or not ops:
            return None
        first = ops[0] if isinstance(ops[0], dict) else {}
        content = first.get("content")
        if isinstance(content, str) and content:
            path = str(first.get("path") or "")
            return boxed.highlighted_numbered_lines(content, path, max_lines=15)
        edits = first.get("edits")
        if not (isinstance(edits, list) and edits) and isinstance(first.get("old"), str):
            edits = [{"old": first.get("old"), "new": first.get("new")}]
        if isinstance(edits, list) and edits:
            olds = [e.get("old", "") for e in edits if isinstance(e, dict)]
            news = [e.get("new", "") for e in edits if isinstance(e, dict)]
            old = "\n".join(str(o) for o in olds)
            new = "\n".join(str(n) for n in news)
            if len(old) + len(new) > 40000:
                return None
            try:
                from ctx.code.tools.apply_patch import format_diff_display, generate_diff

                diff, _, _ = generate_diff(old, new)
                text = Text.from_markup(format_diff_display(diff))
                # The streaming path bypasses _render_result_output's padding
                # step, so consume DIFF_BG_PAD_MARKER here or it leaks literally.
                text = boxed.highlight_diff(text, self._source_path())
                return self._pad_diff_backgrounds(text, max(20, self._box_w() - 4))
            except Exception:
                return None
        return None
