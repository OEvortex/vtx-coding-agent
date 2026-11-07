from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

JsonValue = Any
JsonPath = tuple[str | int, ...]

# Sentinel for "no value yet" / "not a valid scalar" — distinct from JSON null.
_UNSET: Any = object()
_INVALID: Any = object()

MAX_REPORTED_DEPTH = 32  # kept for parity; unused without change reporting
MAX_PENDING_SCALAR_LENGTH = 64

NUMBER_RE = re.compile(r"-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?")
NUMBER_WITHOUT_EXPONENT_DIGITS_RE = re.compile(r"(-?(?:0|[1-9]\d*)(?:\.\d+)?)[eE][+-]?$")
LITERALS: tuple[tuple[str, JsonValue], ...] = (("true", True), ("false", False), ("null", None))

# Copy credit per append and per appended character; snapshot() spends it and
# returns the previous snapshot while the open path costs more than the credit,
# so total work stays linear in the input length.
_CREDIT_PER_APPEND = 256
_CREDIT_PER_CHARACTER = 8

_QUOTE = '"'
_BACKSLASH = "\\"

# Slot: where a value sits in its parent. "root" | ("member", key) | ("element", index)
_Slot = tuple[str, Any]
_ROOT_SLOT: _Slot = ("root", None)


@dataclass
class _Frame:
    container: JsonValue  # dict or list, never handed out while open
    slot: _Slot | None
    size: int = 0
    key: str | None = None
    phase: str = "key"  # "key" | "colon" | "value" | "comma"


@dataclass
class _OpenString:
    target: str  # "key" | "value"
    slot: _Slot | None
    text: str = ""
    escape: str = ""


class StreamingJsonParser:
    """Incremental, tolerant JSON parser; see the module docstring."""

    def __init__(self) -> None:
        self._root: JsonValue = _UNSET
        self._done = False
        self._stack: list[_Frame] = []
        self._string: _OpenString | None = None
        self._scalar = ""
        self._scalar_slot: _Slot | None = None
        self._path_cost = 0
        self._credit = 0
        self._dirty = False
        self._snapshot: JsonValue = _UNSET
        # High surrogate from a \uXXXX escape awaiting a possible \uDC00-\uDFFF
        # partner (UTF-16 pair semantics, as in the TypeScript original).
        self._pending_surrogate: int | None = None

    def append(self, text: str) -> None:
        """Read the next piece of text."""
        self._credit += _CREDIT_PER_APPEND + _CREDIT_PER_CHARACTER * len(text)
        index = 0
        while index < len(text) and not self._done:
            open_str = self._string
            if open_str is not None:
                index = self._read_string(open_str, text, index)
            elif self._scalar != "":
                index = self._read_scalar(text, index)
            else:
                self._structural(text[index])
                index += 1

    def snapshot(self) -> JsonValue:
        """The value of the text read so far.

        Returns ``_UNSET`` before a value starts. Otherwise a plain JSON value
        that never changes afterwards; the previous snapshot is returned while
        the text read since did not change the value or a fresh copy is not yet
        paid for by the credit.
        """
        if not self._dirty:
            return self._snapshot
        pending = self._pending_scalar()
        cost = self._path_cost + (1 if pending is not None else 0)
        if cost > self._credit:
            return self._snapshot
        self._credit -= cost
        self._dirty = False
        self._snapshot = self._copy_open_path(pending)
        return self._snapshot

    # -- string reading -------------------------------------------------

    def _read_string(self, open_str: _OpenString, text: str, start: int) -> int:
        run_start = start
        index = start
        while index < len(text):
            char = text[index]
            if open_str.escape != "":
                if self._continue_escape(open_str, char):
                    index += 1
                    run_start = index
                    continue
                # Not part of the escape: read it as a normal string character.
                run_start = index
            if char == _QUOTE:
                if index > run_start:
                    self._append_string(open_str, text[run_start:index])
                self._end_string(open_str)
                return index + 1
            if char == _BACKSLASH:
                if index > run_start:
                    self._append_string(open_str, text[run_start:index])
                open_str.escape = _BACKSLASH
                index += 1
                run_start = index
                continue
            index += 1
        if len(text) > run_start:
            self._append_string(open_str, text[run_start:])
        return len(text)

    def _continue_escape(self, open_str: _OpenString, char: str) -> bool:
        if open_str.escape == _BACKSLASH:
            decoded = _SIMPLE_ESCAPES.get(char)
            if decoded is not None:
                open_str.escape = ""
                self._append_string(open_str, decoded)
                return True
            if char == "u":
                open_str.escape = "\\u"
                return True
            # Invalid escape: keep the backslash as text and read the char normally.
            open_str.escape = ""
            self._flush_surrogate(open_str)
            self._append_string(open_str, _BACKSLASH)
            return False
        if _is_hex_digit(char):
            open_str.escape += char
            if len(open_str.escape) < 6:
                return True
            code = int(open_str.escape[2:], 16)
            open_str.escape = ""
            self._append_code_point(open_str, code)
            return True
        # `\u` without four hex digits: keep it as text and read the char normally.
        literal = open_str.escape
        open_str.escape = ""
        self._flush_surrogate(open_str)
        self._append_string(open_str, literal)
        return False

    def _append_code_point(self, open_str: _OpenString, code: int) -> None:
        if 0xD800 <= code <= 0xDBFF:
            self._pending_surrogate = code
            return
        if 0xDC00 <= code <= 0xDFFF and self._pending_surrogate is not None:
            high = self._pending_surrogate
            self._pending_surrogate = None
            code = 0x10000 + ((high - 0xD800) << 10) + (code - 0xDC00)
        else:
            self._flush_surrogate(open_str)
        self._append_string(open_str, chr(code))

    def _flush_surrogate(self, open_str: _OpenString) -> None:
        pending = self._pending_surrogate
        self._pending_surrogate = None
        if pending is not None:
            self._append_string(open_str, chr(pending))

    def _append_string(self, open_str: _OpenString, text: str) -> None:
        self._flush_surrogate(open_str)
        open_str.text += text
        if open_str.target == "value" and open_str.slot is not None:
            self._place(open_str.slot, open_str.text)
        self._dirty = True

    def _end_string(self, open_str: _OpenString) -> None:
        self._flush_surrogate(open_str)
        self._string = None
        frame = self._stack[-1] if self._stack else None
        if open_str.target == "key":
            if frame is None:
                return
            frame.key = open_str.text
            frame.phase = "colon"
            return
        self._value_ended(frame)

    # -- scalar reading -----------------------------------------------

    def _read_scalar(self, text: str, start: int) -> int:
        end = start
        while end < len(text) and not _is_delimiter(text[end]):
            end += 1
        if end > start:
            self._scalar += text[start:end]
            if self._scalar_slot is not None:
                self._dirty = True
        if end < len(text):
            self._end_scalar()
        return end

    def _structural(self, char: str) -> None:
        if char in " \t\n\r":
            return
        if char == _QUOTE:
            self._start_string()
            return
        if char in "{[":
            self._open_container({} if char == "{" else [])
            return
        if char in "}]":
            self._close_container()
            return
        if char == ":":
            frame = self._stack[-1] if self._stack else None
            if (
                frame is not None
                and not isinstance(frame.container, list)
                and frame.phase == "colon"
            ):
                frame.phase = "value"
            return
        if char == ",":
            frame = self._stack[-1] if self._stack else None
            if frame is not None:
                frame.phase = "value" if isinstance(frame.container, list) else "key"
            return
        self._scalar = char
        self._scalar_slot = self._value_slot()
        if self._scalar_slot is not None:
            self._dirty = True

    def _value_slot(self) -> _Slot | None:
        """Where a value starting now goes, or None when this position takes no value."""
        if not self._stack:
            return _ROOT_SLOT if self._root is _UNSET else None
        frame = self._stack[-1]
        if isinstance(frame.container, list):
            return ("element", len(frame.container))
        # A missing colon is tolerated: {"a" 1} reads as {"a": 1}.
        if frame.phase in ("value", "colon") and frame.key is not None:
            return ("member", frame.key)
        return None

    def _start_string(self) -> None:
        frame = self._stack[-1] if self._stack else None
        # A missing comma is tolerated: {"a": 1 "b": 2} reads the second member.
        if (
            frame is not None
            and not isinstance(frame.container, list)
            and frame.phase in ("key", "comma")
        ):
            self._string = _OpenString(target="key", slot=None)
            return
        slot = self._value_slot()
        self._string = _OpenString(target="value", slot=slot)
        self._place(slot, "")

    def _end_scalar(self) -> None:
        token = self._scalar
        slot = self._scalar_slot
        self._scalar = ""
        self._scalar_slot = None
        value = _scalar_value(token)
        if value is not _INVALID:
            self._place(slot, value)
            self._dirty = True
        self._value_ended(self._stack[-1] if self._stack else None)

    def _pending_scalar(self) -> tuple[_Slot, JsonValue] | None:
        """The value of the unterminated scalar at the end of the text, if it shows."""
        slot = self._scalar_slot
        token = self._scalar
        if slot is None or token == "" or len(token) > MAX_PENDING_SCALAR_LENGTH:
            return None
        value = _pending_scalar_value(token)
        return None if value is _INVALID else (slot, value)

    def _open_container(self, container: JsonValue) -> None:
        slot = self._value_slot()
        self._place(slot, container)
        is_list = isinstance(container, list)
        self._stack.append(
            _Frame(container=container, slot=slot, phase="value" if is_list else "key")
        )
        self._path_cost += 1
        self._dirty = True

    def _close_container(self) -> None:
        if not self._stack:
            return  # stray closer outside any container is ignored
        frame = self._stack.pop()
        self._path_cost -= frame.size + 1
        self._dirty = True
        self._value_ended(self._stack[-1] if self._stack else None)

    def _value_ended(self, frame: _Frame | None) -> None:
        if frame is None:
            # Text after the root value is ignored, but only once a root value exists.
            if self._root is not _UNSET:
                self._done = True
            return
        frame.phase = "comma"

    def _place(self, slot: _Slot | None, value: JsonValue) -> None:
        """Set the value at slot in the open path; nothing for invalid positions."""
        if slot is None:
            return
        self._dirty = True
        kind, key = slot
        if kind == "root":
            self._root = value
            return
        frame = self._stack[-1]
        if kind == "element":
            elements = frame.container
            if key == len(elements):
                frame.size += 1
                self._path_cost += 1
                elements.append(value)
            elif key < len(elements):
                elements[key] = value
            else:
                elements.append(value)
            return
        members = frame.container
        if key not in members:
            frame.size += 1
            self._path_cost += 1
        members[key] = value

    def _copy_open_path(self, pending: tuple[_Slot, JsonValue] | None) -> JsonValue:
        """A fresh snapshot: copies of the open containers, innermost first."""
        if not self._stack:
            if pending is not None and pending[0][0] == "root":
                return pending[1]
            return _UNSET if self._root is _UNSET else self._root
        child: JsonValue = _UNSET
        child_slot: _Slot | None = None
        for depth in range(len(self._stack) - 1, -1, -1):
            frame = self._stack[depth]
            if isinstance(frame.container, list):
                copy: JsonValue = list(frame.container)
            else:
                copy = dict(frame.container)
            if depth == len(self._stack) - 1 and pending is not None:
                _set_slot(copy, pending[0], pending[1])
            if child is not _UNSET and child_slot is not None:
                _set_slot(copy, child_slot, child)
            child = copy
            child_slot = frame.slot
        # The outermost open container is the root unless it sits in an invalid position.
        return child if child_slot is not None and child_slot[0] == "root" else self._root


_SIMPLE_ESCAPES = {
    '"': '"',
    "\\": "\\",
    "/": "/",
    "b": "\b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
}


def _is_delimiter(char: str) -> bool:
    return char in ' \t\n\r,:{ }[]"'


def _is_hex_digit(char: str) -> bool:
    return ("0" <= char <= "9") or ("a" <= char <= "f") or ("A" <= char <= "F")


def _scalar_value(token: str) -> JsonValue:
    """The value of a complete number or literal token; _INVALID for anything else."""
    for literal, value in LITERALS:
        if token == literal:
            return value
    if NUMBER_RE.fullmatch(token):
        try:
            return int(token)
        except ValueError:
            return float(token)
    return _INVALID


def _pending_scalar_value(token: str) -> JsonValue:
    """The value an unterminated number or literal token shows."""
    for literal, value in LITERALS:
        if literal.startswith(token):
            return value
    if NUMBER_RE.fullmatch(token):
        try:
            return int(token)
        except ValueError:
            return float(token)
    m = NUMBER_WITHOUT_EXPONENT_DIGITS_RE.search(token)
    if m is None:
        return _INVALID
    try:
        return int(m.group(1))
    except ValueError:
        return float(m.group(1))


def _set_slot(container: JsonValue, slot: _Slot, value: JsonValue) -> None:
    kind, key = slot
    if kind == "element":
        while len(container) < key:
            container.append(None)
        if key == len(container):
            container.append(value)
        else:
            container[key] = value
    elif kind == "member":
        container[key] = value


# =================================================================================================
# Final / repair parsing (json-parse.ts)
# =================================================================================================

_VALID_JSON_ESCAPES = {'"', "\\", "/", "b", "f", "n", "r", "t"}


def _is_control_character(char: str) -> bool:
    return "\x00" <= char <= "\x1f"


def _escape_control_character(char: str) -> str:
    return {"\b": "\\b", "\f": "\\f", "\n": "\\n", "\r": "\\r", "\t": "\\t"}.get(
        char, f"\\u{ord(char):04x}"
    )


def repair_json(text: str) -> str:
    """Repair malformed JSON string literals.

    Escapes raw control characters inside strings and doubles backslashes
    before invalid escape characters.
    """
    repaired = []
    in_string = False
    index = 0
    while index < len(text):
        char = text[index]
        if not in_string:
            repaired.append(char)
            if char == '"':
                in_string = True
            index += 1
            continue
        if char == '"':
            repaired.append(char)
            in_string = False
            index += 1
            continue
        if char == "\\":
            next_char = text[index + 1] if index + 1 < len(text) else None
            if next_char is None:
                repaired.append("\\\\")
                index += 1
                continue
            if next_char == "u":
                unicode_digits = text[index + 2 : index + 6]
                if re.fullmatch(r"[0-9a-fA-F]{4}", unicode_digits):
                    repaired.append(f"\\u{unicode_digits}")
                    index += 6
                    continue
            if next_char in _VALID_JSON_ESCAPES:
                repaired.append(f"\\{next_char}")
                index += 2
                continue
            repaired.append("\\\\")
            index += 1
            continue
        repaired.append(_escape_control_character(char) if _is_control_character(char) else char)
        index += 1
    return "".join(repaired)


def parse_json_with_repair(text: str) -> Any:
    try:
        return json.loads(text)
    except Exception:
        repaired = repair_json(text)
        if repaired != text:
            return json.loads(repaired)
        raise


@dataclass(slots=True)
class ParsedToolCallArguments:
    """Result of parsing a finished tool call's complete arguments text.

    ``arguments_error`` describes the problem (including the received text) when
    the arguments were not a valid JSON object, so tool validation can report it
    back to the model. Mirrors pi's ``{arguments, argumentsError}``.
    """

    arguments: dict[str, Any] = field(default_factory=dict)
    arguments_error: str | None = None


def parse_tool_call_arguments(text: str | None) -> ParsedToolCallArguments:
    """Parse the complete arguments text of a finished tool call.

    Never keeps a partially parsed prefix: text that is not a valid JSON object
    means the model sent malformed arguments.
    """
    if not text or not text.strip():
        return ParsedToolCallArguments()
    try:
        parsed = parse_json_with_repair(text)
        if not isinstance(parsed, dict):
            raise ValueError("Tool arguments must be a JSON object")
        return ParsedToolCallArguments(arguments=parsed)
    except Exception as error:
        return ParsedToolCallArguments(arguments_error=(f"{error}\n\nReceived arguments:\n{text}"))


def parse_streaming_json(text: str | None) -> Any:
    """Parse potentially incomplete JSON during streaming.

    Returns the best-effort value (``{}`` when nothing parses yet). Unlike the
    TypeScript original's ``partial-json`` fallback, this reuses
    ``StreamingJsonParser``, whose tolerant rules already cover malformed text.
    """
    if not text or text.strip() == "":
        return {}
    try:
        return parse_json_with_repair(text)
    except Exception:
        parser = StreamingJsonParser()
        parser.append(text)
        snapshot = parser.snapshot()
        return {} if snapshot is _UNSET else snapshot


class ToolCallArgumentsStream:
    """The arguments of one streaming tool call.

    Keeps the raw text (for the one full parse when the call ends) and a
    ``StreamingJsonParser`` for the partial arguments in between.
    """

    def __init__(self) -> None:
        self._text = ""
        self._parser = StreamingJsonParser()
        self._snapshot: JsonValue = _UNSET
        self._arguments: dict[str, Any] = {}

    @property
    def text(self) -> str:
        """The raw argument text received so far."""
        return self._text

    def append(self, delta: str) -> dict[str, Any]:
        """Append a piece of argument text; returns the partial arguments.

        A new dict whenever they changed, the same object otherwise.
        """
        self._text += delta
        self._parser.append(delta)
        return self._partial()

    def replace(self, text: str) -> tuple[str | None, dict[str, Any]]:
        """Take the provider's complete argument text.

        When it continues the text so far, returns the missing piece as
        ``delta``; otherwise starts over with ``text``. Either way returns the
        partial arguments of ``text``.
        """
        if text.startswith(self._text):
            delta = text[len(self._text) :]
            return (delta, self._partial() if delta == "" else self.append(delta))
        self._text = ""
        self._parser = StreamingJsonParser()
        return (None, self.append(text))

    def finish(self) -> ParsedToolCallArguments:
        """The final arguments: one full parse of the complete text."""
        return parse_tool_call_arguments(self._text)

    def _partial(self) -> dict[str, Any]:
        snapshot = self._parser.snapshot()
        if snapshot is self._snapshot:
            return self._arguments
        self._snapshot = snapshot
        # Tool arguments are an object; any other partial value shows as no arguments yet.
        self._arguments = snapshot if isinstance(snapshot, dict) else {}
        return self._arguments
