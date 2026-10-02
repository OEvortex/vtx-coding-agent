"""The one definition of "JSON-safe data" shared by the host and the worker.

Both sides must agree exactly. If the host accepted a value the worker would
reject, a tool could succeed and then fail the script for no reason the model
could see. So there is one implementation and both import it.

Arguments and results make a JSON round trip. That means ``NaN``/``Infinity``
go (non-standard JSON), non-string dict keys go, and anything with no JSON
representation is an error rather than a silent ``str()``.
"""

from __future__ import annotations

import json
import math
from typing import Any, Final

#: Nesting depth cap. Deeper values are rejected rather than allowed to blow
#: the stack in either process; a clear error beats a native RecursionError.
MAX_DEPTH: Final = 32

_PRIMITIVES: Final = (str, bool, int)


def coerce_json(value: Any, *, what: str) -> Any:
    """Return ``value`` as JSON-safe data or raise :class:`~.errors.InvalidOutput`.

    ``what`` names the side of the boundary for the error message, so the
    model can tell "this tool's return value is wrong" from "these arguments
    are wrong".
    """

    return _coerce(value, what=what, depth=0)


def _coerce(value: Any, *, what: str, depth: int) -> Any:
    from vtx.ai.agent.codemode.errors import InvalidOutput

    if depth > MAX_DEPTH:
        raise InvalidOutput(what, detail=f"nesting deeper than {MAX_DEPTH} levels")

    if value is None or isinstance(value, _PRIMITIVES):
        # bool is a subclass of int, so it is handled by the same branch, and
        # both serialize as themselves.
        return value
    if isinstance(value, float):
        # JSON has no NaN or Infinity. json.dumps emits them by default, which
        # would produce output the parent cannot parse.
        if math.isnan(value) or math.isinf(value):
            raise InvalidOutput(what, detail="NaN and Infinity are not JSON")
        return value
    if isinstance(value, (list, tuple)):
        return [_coerce(item, what=what, depth=depth + 1) for item in value]
    if isinstance(value, dict):
        coerced: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise InvalidOutput(
                    what, detail=f"dict keys must be strings, got {type(key).__name__}"
                )
            coerced[key] = _coerce(item, what=what, depth=depth + 1)
        return coerced
    raise InvalidOutput(what, detail=f"{type(value).__name__} has no JSON representation")


def dumps(value: Any) -> str:
    """Serialize with the strict settings the protocol depends on.

    ``allow_nan=False`` is the belt to :func:`coerce_json`'s braces: if
    anything NaN-shaped ever reaches the wire, this raises instead of writing
    a frame the parent cannot parse.
    """
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def store_fits(
    value: Any, *, max_value_chars: int, max_total_chars: int, existing_total: int
) -> str | None:
    """Return a rejection reason for a store write, or ``None`` when it fits.

    A store the script can grow without bound is a memory leak with a
    one-shot-per-execution shape: nothing would ever evict the old values.
    """
    try:
        encoded = dumps(value)
    except (TypeError, ValueError) as exc:
        return f"value is not JSON: {exc}"
    if len(encoded) > max_value_chars:
        return f"value is {len(encoded)} characters, limit is {max_value_chars}"
    total = existing_total + len(encoded)
    if total > max_total_chars:
        return f"store would total {total} characters, limit is {max_total_chars}"
    return None
