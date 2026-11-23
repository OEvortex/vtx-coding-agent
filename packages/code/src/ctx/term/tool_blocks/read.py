"""The in-chat ``read`` tool block.

A read has no ``ui_details``: its result is a structured ``ui_data`` payload.
Whole-file reads carry a capped numbered preview, paged reads carry the full
range the model saw, and directory reads carry a flat entry list that this
block regroups into a tree. All of that layout is tool-specific, so it lives
here rather than in the generic ``ToolBlock``.
"""

from __future__ import annotations

import re

from ctx.core.config import config
from ctx.term import boxed  # ty: ignore[missing-module-member]
from rich.text import Text

from .base import CodingToolBlock


class ReadToolBlock(CodingToolBlock):
    """Renders numbered file previews and directory trees for ``read``."""

    def _source_path(self) -> str | None:
        """Path from the call header, used for the language chip/highlighting."""
        first = boxed.strip_markup(self._call_msg or "").split("\n", 1)[0].strip()
        if not first or first.startswith("$"):
            return None
        # ``read`` renders ``path:12-30``; strip only a trailing line range so
        # a Windows drive letter is left intact.
        path = re.sub(r":\d+(?:-\d+)?$", "", first).strip()
        return path or None

    def _render_custom_output(self) -> Text | None:
        data = self.ui_data or {}
        if data.get("is_dir"):
            return self._render_dir_listing(data)
        if data.get("preview"):
            preview = str(data.get("preview") or "")
            # A paged read (offset/limit) shows the full range the model read;
            # a whole-file read stays capped so a huge file doesn't flood the
            # scrollback (the model sees the full content either way).
            max_lines = len(preview.splitlines()) if data.get("paged") else 12
            return boxed.highlighted_numbered_lines(
                preview,
                str(data.get("path") or self._source_path() or ""),
                max_lines=max_lines,
                start_line=int(data.get("start_line") or 1),
            )
        return None

    def _render_dir_listing(self, data: dict) -> Text:
        """Directory read result: a panel-style tree.

        Entries arrive as ``{line, path, is_dir}`` dicts. Flat paths are
        regrouped into a tree drawn with ``├─``/``└─``/``│`` glyphs —
        directories in accent with a trailing ``/``, files dim. Capped so a
        huge listing doesn't flood the scrollback.
        """
        colors = config.ui.colors
        entries = [e for e in (data.get("entries") or []) if e.get("path")]

        # Regroup flat paths into a nested tree: {name: {"is_dir": bool, "kids": {}}}
        root: dict = {}
        for e in entries:
            children = root
            parts = str(e["path"]).split("/")
            for i, part in enumerate(parts):
                last = i == len(parts) - 1
                node = children.setdefault(part, {"is_dir": True, "kids": {}})
                if last:
                    node["is_dir"] = bool(e["is_dir"])
                children = node["kids"]

        lines: list[Text] = []
        cap = 20

        def _emit(children: dict, prefix: str) -> None:
            items = sorted(children.items(), key=lambda kv: (not kv[1]["is_dir"], kv[0]))
            for idx, (name, node) in enumerate(items):
                if len(lines) >= cap:
                    return
                last = idx == len(items) - 1
                arm = "└─ " if last else "├─ "
                if node["is_dir"]:
                    lines.append(Text(prefix + arm + name + "/", style=colors.accent))
                else:
                    lines.append(Text(prefix + arm + name, style=colors.dim))
                if node["kids"]:
                    _emit(node["kids"], prefix + ("    " if last else "│   "))

        if root:
            _emit(root, "")
        else:
            lines.append(Text("(empty directory)", style=colors.dim))

        total = sum(1 for _ in self._iter_tree(root)) if root else 0
        if total > len(lines):
            lines.append(Text(f"  ⋯ +{total - len(lines)} more", style=colors.dim))
        elif data.get("truncated"):
            lines.append(Text("  ⋯", style=colors.dim))

        out = Text()
        for i, line in enumerate(lines):
            if i:
                out.append("\n")
            out.append_text(line)
        return out

    @staticmethod
    def _iter_tree(node: dict):
        for child in node.values():
            yield child
            yield from ReadToolBlock._iter_tree(child["kids"])
