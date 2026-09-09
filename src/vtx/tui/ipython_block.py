"""Prime-Agent-style ipython cell widget for VTX RLM mode.

Renders the ``ipython`` tool as a single-line cell with a pulsing working
marker, a syntax-highlighted code body, live-streaming stdout/stderr with
small ``out``/``err``/``res`` labels, and structured error tracebacks.
Expands on Ctrl+O, matching Prime Agent's ``IPythonCellComponent`` UX.
"""

from __future__ import annotations

import contextlib
import re
import time
from dataclasses import dataclass, field

from rich.style import Style
from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.content import Content
from textual.highlight import highlight
from textual.widgets import Label

from vtx.ai.agent.tools.base import BaseTool
from vtx.ai.config import config
from vtx.core.types import ImageContent
from vtx.tui.blocks import ToolBlock

# Stream-event prefixes emitted by :class:`vtx.ai.agent.ipython_manager.IpythonKernel`.
TAG_STDOUT = "__STDOUT__"
TAG_STDERR = "__STDERR__"
TAG_RESULT = "__RESULT__"
TAG_ERROR = "__ERROR__"
TAG_DONE = "__DONE__"

# Prime-Agent working-icon pulse frames.
_WORKING_FRAMES = ("◇", "◈", "◆", "◈")
_WORKING_TICK_MS = 250

DESCRIPTOR_MAX_WIDTH = 64

MAGIC_LINE_PATTERN = re.compile(r"^\s*!")
COMMENT_LINE_PATTERN = re.compile(r"^\s*#")
CD_PREFIX_PATTERN = re.compile(r"^\s*cd\s+([^&;|]+)(?:&&|;)\s*")
BASH_SET_PATTERN = re.compile(r"^\s*set\s+[-+][A-Za-z]*(?:\s+[-+]?\w+)*(?:\s+pipefail)?\s*$")
BASH_SETUP_PATTERN = re.compile(r"^(?:export\s+\w+=|source\s+\S+|\.\s+\S+)")
PYTHON_IMPORT_PATTERN = re.compile(r"^\s*(?:import\s+\S|from\s+\S+\s+import\s+)")
PYTHON_DECORATOR_PATTERN = re.compile(r"^\s*@")
PYTHON_DEFINITION_PATTERN = re.compile(r"^\s*(?:async\s+def|def|class)\s+")
PYTHON_MAIN_PATTERN = re.compile(r"^\s*if\s+__name__\s*==\s*['\"]__main__['\"]\s*:")
PYTHON_CONTROL_PATTERN = re.compile(
    r"^\s*(?:if|elif|else|for|while|with|try|except|finally)\b.*:\s*$"
)
PYTHON_CALL_PATTERN = re.compile(r"^\s*(?:await\s+)?[A-Za-z_][A-Za-z0-9_.]*\s*\(")
BASH_SKILL_CALL_PATTERN = re.compile(
    r"^\s*(?:[A-Za-z_][A-Za-z0-9_]*\s*=\s*)?(?:await\s+)?(?:run_bash|bash)\s*\(\s*[rR]?(\"\"\"|'''|\"|')"
)
PYTHON_LOW_SIGNAL_CALL_PATTERN = re.compile(
    r"^\s*(?:await\s+)?(?:print|len|str|repr|int|float|list|dict|set|tuple)\s*\("
)
PYTHON_ASSIGNMENT_CALL_PATTERN = re.compile(
    r"^\s*[A-Za-z_][A-Za-z0-9_]*(?:\s*:\s*[^=]+)?\s*=\s*(?:await\s+)?[A-Za-z_][A-Za-z0-9_.]*\s*\("
)
PYTHON_LOW_SIGNAL_ASSIGNMENT_CALL_PATTERN = re.compile(
    r"^\s*[A-Za-z_][A-Za-z0-9_]*(?:\s*:\s*[^=]+)?\s*=\s*(?:await\s+)?(?:Path|pathlib\.Path|json\.loads|json\.dumps|str|int|float|list|dict|set|tuple)\s*\("
)
PYTHON_EFFECT_CALL_PATTERN = re.compile(
    r"^\s*(?:await\s+)?[A-Za-z_][A-Za-z0-9_.]*\.(?:write_text|write_bytes|mkdir|unlink|rename|replace|touch|append|extend|update|add|remove|discard|close|commit|execute|run)\s*\("
)
HEREDOC_PATTERN = re.compile(r"<<-?\s*['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?")
PATH_ASSIGN_PATTERN = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?:Path|pathlib\.Path)\([\"']([^\"']+)[\"']\)"
)
STRING_ASSIGN_PATTERN = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*[\"']([^\"']+)[\"']")
BASH_CELL_MAGIC_PATTERN = re.compile(r"^(?:[ \t]*\r?\n)*[ \t]*%%bash\b[^\r\n]*(?:\r?\n|$)")


@dataclass
class IpythonCellContentBlock:
    kind: str  # "stdout" | "stderr" | "result" | "error"
    text: str = ""


@dataclass
class IpythonCellState:
    code: str = ""
    content: list[IpythonCellContentBlock] = field(default_factory=list)
    is_partial: bool = True
    is_error: bool = False
    expanded: bool = False
    show_expand_hint: bool = True
    started_at: float | None = None
    finished_at: float | None = None
    ename: str | None = None
    error_summary: str = ""
    result_repr: str = ""

    def line_counts(self) -> tuple[int, int] | None:
        """Return ``(code_lines, output_lines)`` for the header."""
        code = self.code.strip()
        bash_match = BASH_CELL_MAGIC_PATTERN.search(code)
        body = code[bash_match.end() :] if bash_match else code
        in_lines = sum(1 for line in body.splitlines() if line.strip())

        out_lines = sum(c.text.count("\n") + 1 if c.text else 0 for c in self.content)
        if in_lines == 0 and out_lines == 0:
            return None
        return in_lines, out_lines

    def rendered_output_text(self) -> str:
        """Concatenate rendered output text for the live-output path."""
        parts: list[str] = []
        for block in self.content:
            if block.text:
                parts.append(block.text)
        return "".join(parts)


# --- Code Preview & Error Heuristics (mirrored from Prime Agent) ---


def _collapse_whitespace(text: str) -> str:
    return " ".join(text.split()).strip()


def _truncate_descriptor(text: str, limit: int = DESCRIPTOR_MAX_WIDTH) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _redact_noise(text: str) -> str:
    text = re.sub(r"[A-Za-z0-9+/]{80,}={0,2}", "<blob>", text)
    text = re.sub(
        r"\b((?=\w*(?:token|key|secret|password))[A-Za-z_]\w*)\s*=\s*([\"'])[^\"']*\2",
        r"\1=<redacted>",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\b((?=\w*(?:token|key|secret|password))[A-Za-z_]\w*)\s*=\s*(?!<redacted>)(?![\"'])\S+",
        r"\1=<redacted>",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\b(authorization:\s*(?:bearer\s+)?)[^\s\"']+", r"\1<redacted>", text, flags=re.IGNORECASE
    )
    text = re.sub(r"([\"'])sk-[^\"']+\1", r"\1<redacted>\1", text)
    text = re.sub(r"([\"']).{160,}\1", r"\1…\1", text)
    return text


def _descriptor(text: str) -> str:
    return _truncate_descriptor(_collapse_whitespace(_redact_noise(text)))


def _strip_bash_prefix(line: str) -> str:
    line = MAGIC_LINE_PATTERN.sub("", line).strip()
    return CD_PREFIX_PATTERN.sub("", line).strip()


def _is_skippable_bash_line(line: str) -> bool:
    trimmed = line.strip()
    return (
        not trimmed
        or bool(COMMENT_LINE_PATTERN.match(trimmed))
        or bool(BASH_SET_PATTERN.match(trimmed))
        or bool(BASH_SETUP_PATTERN.match(trimmed))
    )


def _shell_words(line: str) -> list[str]:
    words: list[str] = []
    pattern = re.compile(r'"([^"]*)"|\'([^\']*)\'|(\S+)')
    for match in pattern.finditer(line):
        words.append(match.group(1) or match.group(2) or match.group(3) or "")
    return words


def _path_tail(path: str) -> str:
    return re.sub(r"^\./", "", path)


def _simplify_runner_command(line: str) -> str | None:
    words = _shell_words(line)
    if not words:
        return None
    joined = " ".join(words)
    vitest_idx = next(
        (i for i, w in enumerate(words) if re.search(r"(?:^|/)vitest/dist/cli\.js$", w)), -1
    )
    if len(words) >= 2 and words[0] == "npx" and words[1] == "tsx" and vitest_idx >= 2:
        return f"vitest {' '.join(words[vitest_idx + 1 :])}".strip()

    if words[0] == "npm":
        prefix_idx = words.index("--prefix") if "--prefix" in words else -1
        cwd = words[prefix_idx + 1] if prefix_idx >= 0 and prefix_idx + 1 < len(words) else None
        run_idx = words.index("run") if "run" in words else -1
        if run_idx >= 0 and run_idx + 1 < len(words):
            cmd = f"npm {words[run_idx + 1]} {' '.join(words[run_idx + 2 :])}".strip()
            return f"{cmd} ({_path_tail(cwd)})" if cwd else cmd

    if words[0] == "pnpm":
        cwd_idx = next((i for i, w in enumerate(words) if w in ("-C", "--dir")), -1)
        cwd = words[cwd_idx + 1] if cwd_idx >= 0 and cwd_idx + 1 < len(words) else None
        rest = [w for i, w in enumerate(words) if i not in (cwd_idx, cwd_idx + 1)]
        return f"{' '.join(rest)} ({_path_tail(cwd)})" if cwd else None

    pytest_idx = next(
        (
            i
            for i, w in enumerate(words)
            if w == "pytest" or (w == "pytest" and words[i - 1] == "-m")
        ),
        -1,
    )
    if len(words) >= 2 and words[0] == "uv" and words[1] == "run" and pytest_idx >= 0:
        return f"pytest {' '.join(words[pytest_idx + 1 :])}".strip()
    if (
        len(words) >= 3
        and words[0] in ("python", "python3")
        and words[1] == "-m"
        and words[2] == "pytest"
    ):
        return f"pytest {' '.join(words[3:])}".strip()

    if "node_modules/.bin/" in joined:
        return re.sub(r"\S*node_modules/\.bin/", "", joined)
    return None


def _simplify_mutation_command(line: str) -> str | None:
    words = _shell_words(line)
    if not words:
        return None
    if len(words) >= 3 and words[0] == "cat" and words[1] == ">":
        return f"write {_path_tail(words[2])}"
    if words[0] == "tee" and words:
        action = "append" if "-a" in words else "write"
        return f"{action} {_path_tail(words[-1])}"
    if words[0] == "apply_patch":
        return "apply patch"
    if words[0] in ("rm", "mv", "cp", "git", "npm"):
        return line
    if (words[0] == "sed" and any(w.startswith("-i") for w in words)) or (
        words[0] == "perl" and "-pi" in words
    ):
        return line
    return None


def _simplify_bash_command_line(line: str) -> str:
    return _simplify_runner_command(line) or _simplify_mutation_command(line) or line


def _split_command_chain(line: str) -> list[str]:
    return [p.strip() for p in re.split(r"\s*(?:&&|;)\s*", line) if p.strip()]


def _heredoc_body(lines: list[str], start_idx: int, delimiter: str) -> str | None:
    body: list[str] = []
    for i in range(start_idx + 1, len(lines)):
        line = lines[i]
        if line.strip() == delimiter:
            return "\n".join(body)
        body.append(line)
    return "\n".join(body) if body else None


def _preview_heredoc(lines: list[str]) -> tuple[str, str] | None:
    fallback: tuple[str, str] | None = None
    for i, raw_line in enumerate(lines):
        line = _strip_bash_prefix(raw_line)
        if _is_skippable_bash_line(line):
            continue
        m = HEREDOC_PATTERN.search(line)
        if not m:
            continue
        delimiter = m.group(1)
        body = _heredoc_body(lines, i, delimiter)
        if not body:
            continue
        if re.search(r"\b(?:uv\s+run\s+)?python3?\b", line):
            lang, text = preview_python_code(body)
            if text:
                return lang, text
            continue
        if re.search(r"(?<![\w.])(?:bash|sh)\b", line):
            lang, text = preview_bash_command(body)
            return (lang, text) if text else ("bash", _descriptor(body))
        if re.search(r"\bnode\b", line):
            return "bash", f"node: {_descriptor(body)}"
        cat_match = re.search(r"\b(?:cat|tee)\b.*(?:>|\s)(\S+)\s*<<-?", line)
        if cat_match:
            action = "append" if "tee -a" in line else "write"
            return "bash", f"{action} {_path_tail(cat_match.group(1))}"
        if "apply_patch" in line:
            return "bash", "apply patch"
        if fallback is None:
            fallback = ("bash", _descriptor(body))
    return fallback


def _bash_line_score(line: str, index: int) -> int:
    simplified = _simplify_bash_command_line(line)
    words = _shell_words(line)
    score = 30
    if simplified != line:
        score += 40
    if words and words[0] in ("rm", "mv", "cp", "git", "npm", "pnpm", "pytest", "vitest"):
        score += 20
    if re.search(
        r"\b(?:rm|mv|cp|git\s+(?:add|commit)|npm\s+install|sed\s+-i|perl\s+-pi|tee|cat\s*>|apply_patch)\b",
        line,
    ):
        score += 40
    return score + index


def preview_bash_command(command: str) -> tuple[str, str]:
    lines = command.splitlines()
    heredoc = _preview_heredoc(lines)
    if heredoc and heredoc[1]:
        return heredoc[0], _descriptor(heredoc[1])

    best_text = ""
    best_score = -1
    idx = 0
    for raw_line in lines:
        for raw_part in _split_command_chain(raw_line):
            cmd_line = _strip_bash_prefix(raw_part.strip())
            if not cmd_line or _is_skippable_bash_line(cmd_line):
                continue
            sc = _bash_line_score(cmd_line, idx)
            if sc > best_score:
                best_score = sc
                best_text = _simplify_bash_command_line(cmd_line)
            idx += 1
    return "bash", _descriptor(best_text) if best_text else ""


def _is_skippable_python_line(line: str) -> bool:
    trimmed = line.strip()
    return (
        not trimmed
        or bool(COMMENT_LINE_PATTERN.match(trimmed))
        or bool(PYTHON_IMPORT_PATTERN.match(trimmed))
    )


def _python_indent(line: str) -> int:
    m = re.match(r"^\s*", line)
    return len(m.group(0)) if m else 0


def _python_print_inner_call(line: str) -> str | None:
    m = re.match(r"^print\((.*)\)$", line.strip())
    if not m:
        return None
    inner = m.group(1).strip()
    return inner if PYTHON_CALL_PATTERN.match(inner) else None


def _python_path_vars(lines: list[str]) -> dict[str, str]:
    vars_map: dict[str, str] = {}
    for line in lines:
        m = PATH_ASSIGN_PATTERN.match(line) or STRING_ASSIGN_PATTERN.match(line)
        if m and m.group(2):
            vars_map[m.group(1)] = m.group(2)
    return vars_map


def _python_file_operation(line: str, paths: dict[str, str]) -> str | None:
    m = re.match(
        r"^(?:await\s+)?([A-Za-z_][A-Za-z0-9_]*)\.(write_text|write_bytes|read_text|read_bytes|mkdir|unlink|rename|replace|touch)\s*\(",
        line.strip(),
    )
    if not m:
        return None
    var_name, op = m.group(1), m.group(2)
    path = paths.get(var_name)
    if not path:
        return None
    action_map = {
        "write_text": "write",
        "write_bytes": "write",
        "read_text": "read",
        "read_bytes": "read",
        "mkdir": "mkdir",
        "unlink": "delete",
        "rename": "rename",
        "replace": "replace",
        "touch": "touch",
    }
    return f"{action_map.get(op, op)} {_path_tail(path)}"


def _python_subprocess_command(line: str) -> str | None:
    trimmed = line.strip()
    shell_match = re.search(
        r"subprocess\.(?:run|check_call|check_output|Popen)\(\s*[\"`]([^\"`]+)[\"`]", trimmed
    )
    if shell_match:
        return _simplify_bash_command_line(shell_match.group(1))
    list_match = re.search(
        r"subprocess\.(?:run|check_call|check_output|Popen)\(\s*\[([^\]]+)\]", trimmed
    )
    if list_match:
        items = [m.group(1) for m in re.finditer(r"[\"']([^\"']+)[\"']", list_match.group(1))]
        return _simplify_bash_command_line(" ".join(items))
    return None


def _first_python_child_line(lines: list[str], parent_idx: int) -> int | None:
    parent_indent = _python_indent(lines[parent_idx])
    for i in range(parent_idx + 1, len(lines)):
        line = lines[i]
        if _is_skippable_python_line(line) or PYTHON_DECORATOR_PATTERN.match(line.strip()):
            continue
        if _python_indent(line) <= parent_indent:
            return None
        return i
    return None


def _simplify_python_preview_line(line: str, paths: dict[str, str]) -> str:
    return (
        _python_file_operation(line, paths)
        or _python_subprocess_command(line)
        or _python_print_inner_call(line)
        or line.strip()
    )


def _python_preview_line(lines: list[str], index: int, paths: dict[str, str]) -> str:
    line = lines[index]
    if index > 0 and PYTHON_DEFINITION_PATTERN.match(line):
        prev = lines[index - 1]
        if PYTHON_DECORATOR_PATTERN.match(prev.strip()):
            return f"{prev.strip()} {line.strip()}"
    if PYTHON_CONTROL_PATTERN.match(line):
        child_idx = _first_python_child_line(lines, index)
        if child_idx is not None:
            ctrl = re.sub(r":\s*$", ":", line.strip())
            return f"{ctrl} {_simplify_python_preview_line(lines[child_idx], paths)}"
    return _simplify_python_preview_line(line, paths)


def _python_line_score(lines: list[str], index: int, paths: dict[str, str]) -> int:
    line = lines[index]
    trimmed = line.strip()
    if _is_skippable_python_line(line) or PYTHON_DECORATOR_PATTERN.match(trimmed):
        return -1
    if _python_file_operation(line, paths):
        return 95
    if _python_subprocess_command(line):
        return 90
    if PYTHON_MAIN_PATTERN.match(line):
        return 70
    if PYTHON_EFFECT_CALL_PATTERN.match(line):
        return 80
    if PYTHON_CONTROL_PATTERN.match(line):
        child_idx = _first_python_child_line(lines, index)
        return (
            20 if child_idx is None else max(20, _python_line_score(lines, child_idx, paths) - 5)
        )
    if PYTHON_DEFINITION_PATTERN.match(line):
        return 50
    if PYTHON_LOW_SIGNAL_ASSIGNMENT_CALL_PATTERN.match(line):
        return 25
    inner_call = _python_print_inner_call(line)
    if inner_call and not PYTHON_LOW_SIGNAL_CALL_PATTERN.match(inner_call):
        return 55
    if PYTHON_ASSIGNMENT_CALL_PATTERN.match(line):
        return 60
    if PYTHON_CALL_PATTERN.match(line) and not PYTHON_LOW_SIGNAL_CALL_PATTERN.match(line):
        return 65
    if PYTHON_CALL_PATTERN.match(line):
        return 15
    return 30


def _python_preview_index(lines: list[str], index: int) -> int:
    line = lines[index]
    if not PYTHON_CONTROL_PATTERN.match(line):
        return index
    child_idx = _first_python_child_line(lines, index)
    return index if child_idx is None else _python_preview_index(lines, child_idx)


def preview_python_code(code: str) -> tuple[str, str]:
    lines = code.splitlines()
    paths = _python_path_vars(lines)
    best_idx: int | None = None
    best_score = -1

    for i in range(len(lines)):
        score = _python_line_score(lines, i, paths)
        if score > best_score:
            best_idx = i
            best_score = score

    if best_idx is not None and best_score >= 0:
        preview_idx = _python_preview_index(lines, best_idx)
        tail = "\n".join(lines[preview_idx:])
        m = BASH_SKILL_CALL_PATTERN.search(tail)
        if m:
            quote = m.group(1)
            raw = tail[m.end() - len(quote) :]
            if raw.startswith(quote):
                end_quote = raw.find(quote, len(quote))
                if end_quote > 0:
                    bash_str = raw[len(quote) : end_quote]
                    return preview_bash_command(bash_str)
        return "python", _descriptor(_python_preview_line(lines, preview_idx, paths))
    return "python", ""


def preview_ipython_code(code: str) -> tuple[str, str]:
    trimmed = code.rstrip()
    bash_match = BASH_CELL_MAGIC_PATTERN.search(trimmed)
    if bash_match:
        body = trimmed[bash_match.end() :]
        return preview_bash_command(body)
    # If the first non-empty line starts with '!', preview as bash command
    for line in trimmed.splitlines():
        line_str = line.strip()
        if not line_str:
            continue
        if line_str.startswith("!"):
            cmd = line_str.lstrip("!").strip()
            return preview_bash_command(cmd)
        break
    return preview_python_code(trimmed)


def normalize_error_details(text: str) -> str:
    cleaned = re.sub(r"\x1b\[[0-9;]*m", "", text)
    return cleaned.replace("\r\n", "\n").replace("\r", "\n").rstrip()


def summarize_error_details(text: str) -> str:
    normalized = normalize_error_details(text)
    lines = [line.strip() for line in normalized.splitlines() if line.strip()]
    if not lines:
        return "Error"
    if len(lines) > 1 and (
        lines[0].startswith("Traceback ")
        or lines[0].startswith("Cell In[")
        or lines[0].startswith("---->")
    ):
        for line in reversed(lines):
            if not (line.startswith("Traceback") or line.startswith("File ") or "line " in line):
                return line
        return "Error"
    return lines[0]


def _preview_line(code: str) -> str:
    if not code:
        return ""
    _, text = preview_ipython_code(code)
    return text


def _truncate(text: str, limit: int = 64) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _format_duration_ms(ms: float) -> str:
    if ms < 1000:
        return f"{int(ms)}ms"
    return f"{ms / 1000:.1f}s"


def _content_to_text(content: Any) -> Text:
    """Convert a ``textual.highlight`` Content to a Rich Text."""
    text = Text()
    char_styles: dict[int, str] = {}
    for span in content.spans:
        for i in range(span.start, span.end):
            char_styles[i] = span.style
    prev_style: str | None = None
    segment_start = 0
    for i, char in enumerate(content.plain):
        style = char_styles.get(i)
        if style != prev_style:
            if i > segment_start:
                text.append(content.plain[segment_start:i], style=prev_style)
            segment_start = i
            prev_style = style
    if segment_start < len(content.plain):
        text.append(content.plain[segment_start:], style=prev_style)
    return text


def _label_for_kind(kind: str) -> str:
    return {"stdout": "out", "stderr": "err", "result": "res", "error": "err"}.get(kind, "out")


def _style_for_kind(kind: str, colors) -> Style:
    if kind == "error":
        return Style(color=colors.failed, bold=True)
    if kind == "stderr":
        return Style(color=colors.muted, bold=True)
    return Style(color=colors.muted, bold=True)


def _text_style_for_kind(kind: str, colors) -> str:
    if kind == "error":
        return colors.failed
    if kind == "stderr":
        return colors.muted
    return colors.fg


class IpythonBlock(ToolBlock):
    """Prime-Agent-style cell rendering for the ``ipython`` tool.

    Reuses :class:`ToolBlock`'s ``#tool-header`` and ``#tool-output`` slots
    plus the chat-log dispatch hooks. Overrides header formatting, body
    rendering, and live-output parsing to produce the cell UX.
    """

    def __init__(
        self,
        name: str = "",
        call_msg: str | None = None,
        icon: str = ">>>",
        expanded: bool = False,
        tool: BaseTool | None = None,
        **kwargs,
    ) -> None:
        super().__init__(
            name=name or "ipython",
            call_msg=call_msg,
            icon=icon or ">>>",
            expanded=expanded,
            tool=tool,
            **kwargs,
        )
        self._cell_state = IpythonCellState(expanded=expanded)
        self._pulse_frame_index = 0
        self._pulse_timer = None
        self.add_class("ipython-cell-block")
        if call_msg:
            self._ingest_call_msg(call_msg)
        self._cell_state.started_at = time.monotonic()
        self._safe_update(self._refresh_header)

    # -- Compose ---------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Label(self._build_header_text(), id="tool-header")
        yield Label("", id="tool-output", classes="tool-output -hidden")

    # -- Call msg parsing -------------------------------------------------

    def _ingest_call_msg(self, call_msg: str) -> None:
        if not call_msg:
            return
        text = call_msg
        if text.startswith("[") and "]: " in text:
            _, _, text = text.partition("]: ")
        self._cell_state.code = text
        self._safe_update(self._refresh_header)

    def update_call_msg(self, call_msg: str | None) -> None:
        self._call_msg = call_msg
        self._ingest_call_msg(call_msg or "")
        self._safe_update(
            lambda: self.query_one("#tool-header", Label).update(self._build_header_text())
        )

    # -- Header ----------------------------------------------------------

    def _marker(self) -> tuple[str, str]:
        colors = config.ui.colors
        if self._success is False or self._cell_state.is_error:
            return ("✗", colors.failed)
        if self._success is True:
            return ("✓", colors.success)
        if self._awaiting_approval:
            return ("△", colors.notice)
        if self._cell_state.is_partial:
            return (
                _WORKING_FRAMES[self._pulse_frame_index % len(_WORKING_FRAMES)],
                colors.spinner,
            )
        return ("◇", colors.muted)

    def _build_header_text(self) -> Text:
        text = Text()
        colors = config.ui.colors
        _marker_char, marker_style = self._marker()

        code = self._cell_state.code.rstrip()
        is_bash_cell = bool(BASH_CELL_MAGIC_PATTERN.search(code))
        lang, preview = preview_ipython_code(code)

        lang_label = f"bash · {lang}" if is_bash_cell and lang != "bash" else lang
        if not lang_label:
            lang_label = "ipython"

        text.append(f"{_marker_char} ", style=marker_style)
        text.append(lang_label, style=colors.muted)

        if preview:
            text.append(" · ", style=colors.dim)
            if lang == "bash" or is_bash_cell:
                text.append(preview, style=colors.spinner)
            else:
                text.append(preview, style=colors.fg)
        elif self._cell_state.is_partial:
            text.append(" · ", style=colors.dim)
            text.append("waiting for code", style=colors.muted)

        counts = self._cell_state.line_counts()
        if counts:
            code_lines, out_lines = counts
            text.append(" · ", style=colors.dim)
            segments: list[str] = []
            if code_lines > 0:
                segments.append(f"↑{code_lines}")
            if out_lines > 0:
                segments.append(f"↓{out_lines}")
            text.append(f"{' '.join(segments)} lines", style=colors.muted)

        duration = self._duration_label()
        if duration:
            text.append(" · ", style=colors.dim)
            text.append(duration, style=colors.muted)

        if self._cell_state.is_error and self._cell_state.ename:
            text.append(" · ", style=colors.dim)
            text.append(self._cell_state.ename, style=colors.failed)

        if self._cell_state.show_expand_hint:
            text.append(" · ", style=colors.dim)
            hint_action = "collapse" if self._cell_state.expanded else "expand"
            text.append(f"(ctrl+o to {hint_action})", style=colors.muted)

        return text

    def _format_header(self, truncate: bool = True) -> Text:
        del truncate
        return self._build_header_text()

    def _duration_label(self) -> str:
        state = self._cell_state
        if state.started_at is None:
            return ""
        end = state.finished_at if state.finished_at is not None else time.monotonic()
        ms = max(0.0, (end - state.started_at) * 1000)
        return _format_duration_ms(ms)

    def _refresh_header(self) -> None:
        try:
            self.query_one("#tool-header", Label).update(self._build_header_text())
        except Exception:
            return

    # -- Pulse animation --------------------------------------------------

    def on_mount(self, event: events.Mount) -> None:
        del event
        self._start_pulse()

    def on_unmount(self) -> None:
        self._stop_pulse()

    def _start_pulse(self) -> None:
        if self._pulse_timer is not None:
            return
        try:
            timer = self.set_interval(_WORKING_TICK_MS / 1000.0, self._tick_pulse)
        except Exception:
            self._pulse_timer = None
            return
        self._pulse_timer = timer
        if not self._cell_state.is_partial:
            timer.pause()

    def _stop_pulse(self) -> None:
        timer = self._pulse_timer
        self._pulse_timer = None
        if timer is None:
            return
        with contextlib.suppress(Exception):
            timer.stop()

    def _tick_pulse(self) -> None:
        if not self._cell_state.is_partial:
            self._stop_pulse()
            return
        self._pulse_frame_index += 1
        self._refresh_header()

    # -- Live output (streaming) -----------------------------------------

    def append_live_output(self, delta: str) -> None:
        if not delta:
            return
        state = self._cell_state
        if delta.startswith(TAG_STDOUT):
            text = delta[len(TAG_STDOUT) :]
            if text:
                state.content.append(IpythonCellContentBlock(kind="stdout", text=text))
        elif delta.startswith(TAG_STDERR):
            text = delta[len(TAG_STDERR) :]
            if text:
                state.content.append(IpythonCellContentBlock(kind="stderr", text=text))
        elif delta.startswith(TAG_RESULT):
            text = delta[len(TAG_RESULT) :]
            state.result_repr = text
            if text:
                state.content.append(IpythonCellContentBlock(kind="result", text=text))
        elif delta.startswith(TAG_ERROR):
            text = delta[len(TAG_ERROR) :]
            state.is_error = True
            first_line = text.splitlines()[0] if text else ""
            state.ename = first_line.split(":", 1)[0] if first_line else "Error"
            state.error_summary = text
            state.content.append(IpythonCellContentBlock(kind="error", text=text))
        elif delta.startswith(TAG_DONE):
            state.is_partial = False
            self._stop_pulse()
        else:
            state.content.append(IpythonCellContentBlock(kind="stdout", text=delta))
        self._render_cell()

    # -- Result ----------------------------------------------------------

    def set_result(  # type: ignore[override]
        self,
        ui_summary: str | None,
        ui_details: str | None,
        success: bool,
        markup: bool = True,
        ui_details_full: str | None = None,
        images: list[ImageContent] | None = None,
    ) -> None:
        self._live_output = ""
        self._ui_summary = ui_summary
        self._ui_details = ui_details
        self._ui_details_full = ui_details_full
        self._images = images
        self._result_markup = markup
        self._success = success
        self._awaiting_approval = False
        self._cell_state.is_partial = False
        if not success and not self._cell_state.is_error:
            self._cell_state.is_error = True
            self._cell_state.ename = "Error"
        self._cell_state.finished_at = time.monotonic()
        self._stop_pulse()
        self._set_state(success)
        self._render_cell()
        self._refresh_header()

    # -- Expansion -------------------------------------------------------

    def set_expanded(self, expanded: bool) -> None:
        if self._expanded == expanded and self._cell_state.expanded == expanded:
            return
        self._expanded = expanded
        self._cell_state.expanded = expanded
        self._render_cell()

    # -- Render ----------------------------------------------------------

    def _render_cell(self) -> None:
        try:
            output = self.query_one("#tool-output", Label)
        except Exception:
            return

        if not self._cell_state.expanded:
            output.update(Text(""))
            self.remove_class("-with-details")
            output.add_class("-hidden")
            output.remove_class("-details")
            output.remove_class("-diff-output")
            self._refresh_header()
            return

        # Build body as Content so syntax-highlighted code stays in Textual's
        # native renderable and theme tokens resolve correctly.
        body = Content("")
        if self._cell_state.code:
            code_render = self._render_code()
            if isinstance(code_render, Content):
                body = code_render
            else:
                body = Content.from_rich_text(code_render)
        output_render = self._render_output_blocks()
        if isinstance(output_render, Text):
            output_content = Content.from_rich_text(output_render)
        else:
            output_content = output_render
        if output_content.plain:
            if body.plain:
                body = Content(body.plain + "\n" + output_content.plain)
            else:
                body = output_content

        if body.plain:
            self.remove_class("-compact")
            self.add_class("-with-details")
            output.remove_class("-hidden")
            output.remove_class("-details")
            output.remove_class("-diff-output")
            output.update(body)
        else:
            output.update(Text(""))
            self.remove_class("-with-details")
            output.add_class("-hidden")
        self._refresh_header()

    def _render_code(self) -> "Content":
        code = self._cell_state.code
        if not code:
            return Content("")
        try:
            highlighted = highlight(code, language="python")
            prefix = "› "
            offset_spans = [
                type("Span", (), {"start": s.start + len(prefix), "end": s.end + len(prefix), "style": s.style})  # noqa: E501
                for s in highlighted.spans
            ]
            return Content(prefix + highlighted.plain, spans=offset_spans)
        except Exception:
            text = Text()
            lines = code.splitlines()
            for index, line in enumerate(lines):
                prefix = "› " if index == 0 else "  "  # noqa: RUF001
                text.append(prefix, style=config.ui.colors.dim)
                text.append(line, style=config.ui.colors.fg)
                if index < len(lines) - 1:
                    text.append("\n")
            return text

    def _render_output_blocks(self) -> Text:
        state = self._cell_state
        text = Text()
        colors = config.ui.colors

        if state.is_partial and not state.content:
            text.append("  ")
            text.append("waiting for output...", style=colors.muted)
            return text

        if not state.is_partial and not state.content:
            text.append("  ")
            text.append("no output", style=colors.muted)
            return text

        for index, block in enumerate(state.content):
            if index > 0:
                text.append("\n")
            label = _label_for_kind(block.kind)
            label_style = _style_for_kind(block.kind, colors)
            text.append("  ")
            text.append(f"{label} ", style=label_style)
            if not block.text:
                continue
            style = _text_style_for_kind(block.kind, colors)
            raw = block.text
            if block.kind == "error" and "\n" in raw:
                text.append_text(self._render_traceback(raw, style))
            elif block.kind in {"stdout", "stderr", "result"} and self._looks_like_code(raw):
                text.append_text(self._render_code_output(raw, style))
            else:
                lines = raw.splitlines() or [""]
                for line_index, line in enumerate(lines):
                    if line_index == 0:
                        text.append(line, style=style)
                    else:
                        text.append("\n")
                        text.append("    ", style=colors.dim)
                        text.append(line, style=style)
        return text

    def _looks_like_code(self, text: str) -> bool:
        stripped = text.strip()
        if not stripped:
            return False
        if stripped.startswith("Traceback") or "Error:" in stripped.splitlines()[0]:
            return False
        if stripped.startswith(">>> ") or stripped.startswith("..."):
            return True
        if "\n" in stripped:
            lines = stripped.splitlines()
            if any(line.startswith(">>> ") or line.startswith("...") for line in lines):
                return True
        return False

    def _render_code_output(self, text: str, style: str) -> Text:
        rendered = Text()
        lines = text.splitlines() or [""]
        for index, line in enumerate(lines):
            if index == 0:
                rendered.append(line, style=style)
            else:
                rendered.append("\n")
                rendered.append("    ", style=config.ui.colors.dim)
                rendered.append(line, style=style)
        return rendered

    def _render_traceback(self, text: str, style: str) -> Text:
        rendered = Text()
        lines = text.splitlines() or [""]
        for index, line in enumerate(lines):
            if index == 0:
                rendered.append(line, style=style)
            else:
                rendered.append("\n")
                rendered.append("    ", style=config.ui.colors.dim)
                if line.startswith("Traceback") or line.startswith("  File"):
                    rendered.append(line, style=style)
                elif "Error:" in line or "Exception:" in line:
                    rendered.append(line, style=Style(color=config.ui.colors.failed, bold=True))
                else:
                    rendered.append(line, style=style)
        return rendered

    # -- Resize ----------------------------------------------------------

    def on_resize(self, event: events.Resize) -> None:
        del event
        if self._cell_state.expanded:
            self._render_cell()
        else:
            self._refresh_header()


__all__ = [
    "IpythonBlock",
    "IpythonCellContentBlock",
    "IpythonCellState",
    "normalize_error_details",
    "preview_bash_command",
    "preview_ipython_code",
    "preview_python_code",
    "summarize_error_details",
]
