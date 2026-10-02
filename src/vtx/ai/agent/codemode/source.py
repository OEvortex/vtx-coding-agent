"""The ``# @options:`` line, and a grammar for providers that support one.

A model can constrain its own budget with a first-line comment::

    # @options: {"timeout_ms": 30000}
    ...

The sandbox does not act on those fields. ``timeout_ms`` is host policy and
the host owns the deadline; a script that asks for a shorter one than the host
allows is fine, and one that asks for longer is clamped. Putting the knob in the
source is a convenience for the model, not a control.

The options line is replaced by an empty line rather than removed, so line
numbers in a traceback still match what the model wrote. That matters more than
it sounds: the model is going to fix the line the traceback names.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Final

_OPTIONS_LINE: Final = re.compile(r"\A\s*#\s*@options:\s*(?P<body>.*?)\s*\Z")

#: Fields a model may set. Unknown fields are an error rather than ignored:
#: silently dropping one would let a model believe it had set a limit.
KNOWN_FIELDS: Final = frozenset({"timeout_ms"})

#: Bounds. A model cannot widen the host's deadline or set it absurdly short.
MIN_TIMEOUT_MS: Final = 1_000
MAX_TIMEOUT_MS: Final = 600_000


class CodemodeSourceError(ValueError):
    """The script's options line is invalid."""


@dataclass(frozen=True)
class SourceOptions:
    """Parsed options plus the source with the options line blanked."""

    code: str
    timeout_ms: int | None = None


def parse_source(source: str) -> SourceOptions:
    """Split an optional ``# @options:`` line off ``source``.

    Raises :class:`CodemodeSourceError` for an empty script, malformed JSON, an
    unknown field, or an options line with no code after it -- all cases where
    the model believes it configured something it did not.
    """
    if not source.strip():
        raise CodemodeSourceError("The script is empty.")

    lines = source.split("\n")
    match = _OPTIONS_LINE.match(lines[0])
    if match is None:
        return SourceOptions(code=source)

    try:
        parsed = json.loads(match.group("body") or "{}")
    except json.JSONDecodeError as exc:
        raise CodemodeSourceError(f"The options line is not valid JSON: {exc.msg}.") from exc
    if not isinstance(parsed, dict):
        raise CodemodeSourceError("The options line must be a JSON object.")

    unknown = sorted(set(parsed) - KNOWN_FIELDS)
    if unknown:
        raise CodemodeSourceError(
            f"Unknown options field(s): {', '.join(unknown)}. Known fields: {', '.join(sorted(KNOWN_FIELDS))}."
        )

    timeout_ms = parsed.get("timeout_ms")
    if timeout_ms is not None:
        if not isinstance(timeout_ms, int) or isinstance(timeout_ms, bool):
            raise CodemodeSourceError("timeout_ms must be an integer number of milliseconds.")
        if not MIN_TIMEOUT_MS <= timeout_ms <= MAX_TIMEOUT_MS:
            raise CodemodeSourceError(
                f"timeout_ms must be between {MIN_TIMEOUT_MS} and {MAX_TIMEOUT_MS}."
            )

    # Replace, do not remove: the traceback line numbers stay correct.
    lines[0] = ""
    remainder = "\n".join(lines)
    if not remainder.strip():
        raise CodemodeSourceError("The options line must be followed by code.")

    return SourceOptions(code=remainder, timeout_ms=timeout_ms)


def clamp_timeout(requested: int | None, host_limit: int | None) -> int | None:
    """Resolve the effective deadline.

    The model may shorten the host's deadline but never extend it. A script that
    asked for an hour does not get an hour.
    """
    if requested is None:
        return host_limit
    if host_limit is None:
        return requested
    return min(requested, host_limit)


#: A Lark grammar for providers that support grammar-constrained tool input.
#: Exported as source because the provider SDKs take it as a string, and as a
#: module because tests need to check it without a provider.
CODEMODE_SOURCE_GRAMMAR: Final = r"""
start: options_line? line*
options_line: /#[ \t]*@options:[ \t]*/ json_object
json_object: "{" (pair ("," pair)*)? "}"
pair: string ":" json_value
json_value: string | number | object | array | "true" | "false" | "null"
object: "{" (pair ("," pair)*)? "}"
array: "[" (json_value ("," json_value)*)? "]"
string: /"[^"\\]*(\\.[^"\\]*)*"/
number: /-?\d+(\.\d+)?([eE][+-]?\d+)?/
line: /[^\n]*/
"""
