"""Provider request interception — pi ``before_provider_headers`` /
``before_provider_request`` parity, available to every transport.

SDK wrappers call :func:`prepare_request` right before sending an LLM
request. Listeners registered here can:

- **headers**: mutate the ``headers`` dict in place. Set a key to a string
  to add or override it, or to ``None`` to delete it.
- **payload**: mutate the wire payload in place, or return
  ``{"payload": {...}}`` to replace it for subsequent listeners and the
  actual request.

Retries reuse the prepared values: listeners are *not* re-fired on
transient-error retries.

The extension bridge (:func:`vtx.ai.agent.extensions.install_provider_bridge`)
registers itself here when extensions load, so these hook points are also
reachable via ``api.on("before_provider_headers", ...)`` /
``api.on("before_provider_request", ...)`` with full ``ctx`` support.
"""

from __future__ import annotations

import contextlib
import inspect
import logging
from collections.abc import Callable
from typing import Any

log = logging.getLogger("ai.provider_hooks")

HeadersListener = Callable[..., Any]
RequestListener = Callable[..., Any]

_headers_listeners: list[HeadersListener] = []
_request_listeners: list[RequestListener] = []


def register_headers_listener(fn: HeadersListener) -> HeadersListener:
    """Register a listener receiving ``(headers, context)``; mutating in place."""
    _headers_listeners.append(fn)
    return fn


def register_request_listener(fn: RequestListener) -> RequestListener:
    """Register a listener receiving ``(payload, context) -> {"payload": ...} | None``."""
    _request_listeners.append(fn)
    return fn


def unregister_headers_listener(fn: HeadersListener) -> None:
    with contextlib.suppress(ValueError):
        _headers_listeners.remove(fn)


def unregister_request_listener(fn: RequestListener) -> None:
    with contextlib.suppress(ValueError):
        _request_listeners.remove(fn)


def reset_listeners() -> None:
    """Remove all listeners (test isolation helper)."""
    _headers_listeners.clear()
    _request_listeners.clear()


async def _run(fn: Callable[..., Any], *args: Any) -> Any:
    result = fn(*args)
    if inspect.isawaitable(result):
        result = await result
    return result


async def prepare_request(
    *, provider: str, payload: dict[str, Any]
) -> tuple[dict[str, str], dict[str, Any]]:
    """Run header and payload listeners for an outgoing LLM request.

    Returns ``(extra_headers, final_payload)``. ``extra_headers`` merges over
    the transport's default headers at send time; keys whose listener set
    them to ``None`` are dropped (deletion markers). Listener exceptions are
    logged and never fail the request.
    """
    context = {"provider": provider, "model": str(payload.get("model", "") or "")}

    headers: dict[str, str | None] = {}
    for fn in list(_headers_listeners):
        try:
            await _run(fn, headers, dict(context))
        except Exception:
            log.exception("before_provider_headers listener %r failed", fn)
    extra_headers = {k: v for k, v in headers.items() if v is not None}

    working = payload
    for fn in list(_request_listeners):
        try:
            result = await _run(fn, working, dict(context))
        except Exception:
            log.exception("before_provider_request listener %r failed", fn)
            continue
        if isinstance(result, dict) and isinstance(result.get("payload"), dict):
            working = result["payload"]

    return extra_headers, working
