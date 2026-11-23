"""Minimal @path completion helpers for vx, mirroring fx's file_picker_path."""

from __future__ import annotations

import os
from pathlib import Path


def is_boundary(ch: str) -> bool:
    return ch.isspace() or ch in "([{<\"'`"


def parse_at(text: str, cursor: int) -> tuple[int, int, str] | None:
    """Return (at_offset, replace_end, query) for an @ token around cursor, or None."""
    # find nearest '@' at or before cursor that is at a boundary
    at_offset = -1
    i = cursor
    while i >= 0:
        if i < len(text) and text[i] == "@" and (i == 0 or is_boundary(text[i - 1])):
            at_offset = i
            break
        i -= 1
    if at_offset < 0:
        return None
    quoted = at_offset + 1 < len(text) and text[at_offset + 1] == '"'
    path_start = at_offset + 1 + (1 if quoted else 0)
    if cursor < path_start:
        return None
    # end of token
    if quoted:
        j = path_start
        while j < len(text) and text[j] != '"':
            j += 1
        end = j if j < len(text) else len(text)
    else:
        end = path_start
        while end < len(text) and not text[end].isspace():
            end += 1
    if cursor > end:
        return None
    query = text[path_start:cursor]
    # Replace the closing quote too when the token already has one.
    replace_end = end + 1 if quoted and end < len(text) and text[end] == '"' else end
    return at_offset, replace_end, query


def list_candidates(cwd: str, query: str, limit: int = 40) -> list[str]:
    """Workspace paths matching ``query``, best first.

    fx ranks by basename prefix, then substring, then path order; the same
    ordering here keeps typing an @ token stable instead of reshuffling rows
    as the walk finds more files.
    """
    needle = query.strip().lstrip("./").lower()
    base = Path(cwd)
    skip = {".git", "__pycache__", "node_modules", ".venv", ".mypy_cache", ".ruff_cache"}
    matches: list[tuple[int, int, str]] = []
    for root, dirs, files in os.walk(base):
        dirs[:] = sorted(d for d in dirs if d not in skip and not d.startswith("."))
        depth = 0 if root == str(base) else root[len(str(base)) + 1 :].count(os.sep) + 1
        if depth >= 6:
            dirs[:] = []
        for name in files + dirs:
            rel = os.path.relpath(os.path.join(root, name), base)
            low = rel.lower()
            if needle:
                if needle not in low:
                    continue
                rank = 0 if name.lower().startswith(needle) else 1
            else:
                rank = 0 if depth == 0 else 1
            matches.append((rank, depth, rel))
        if len(matches) > 4000:
            break
    matches.sort()
    return [rel for _, _, rel in matches[:limit]]


def expand_paths(text: str, cwd: str) -> str:
    """Replace @relative paths with @absolute paths for the model."""
    import os

    out = []
    i = 0
    n = len(text)
    while i < n:
        if text[i] == "@" and (i == 0 or (text[i - 1].isspace() or text[i - 1] in "([{<\"'`")):
            # parse token
            quoted = i + 1 < n and text[i + 1] == '"'
            start = i + 1 + (1 if quoted else 0)
            if quoted:
                j = start
                while j < n and text[j] != '"':
                    j += 1
                end = j
            else:
                end = start
                while end < n and not text[end].isspace():
                    end += 1
            rel = text[start:end]
            # strip leading ./
            clean = rel.strip().lstrip("./") if not quoted else rel
            full = os.path.join(cwd, clean) if clean and not os.path.isabs(clean) else rel
            # rebuild token
            if quoted:
                out.append("@" + '"' + full + '"')
                i = end + (1 if end < n and text[end] == '"' else 0)
            else:
                out.append("@" + full)
                i = end
            continue
        out.append(text[i])
        i += 1
    return "".join(out)
