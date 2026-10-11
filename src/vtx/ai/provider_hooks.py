"""Provider request interception — ``before_provider_headers`` /
``before_provider_request`` hooks, available to every transport.

SDK wrappers call :func:`prepare_request` right before sending an LLM
request. Listeners registered here can:

- **headers**: mutate the ``headers`` dict in place. Set a key to a string
  to add or override it, or to ``None`` to delete it.
- **payload**: mutate the wire payload in place, or return
  ``{"payload": {...}}`` to replace it for subsequent listeners and the
  actual request.

Retries reuse the prepared values: listeners are *not* re-fired on
transient-error retries.

The composition bridge (:func:`vtx.plugins.extensions.install_provider_bridge`)
registers itself here when the extensions row mounts, so these hook points are
reachable via ``api.on("before_provider_headers", ...)`` /
``api.on("before_provider_request", ...)`` with full ``ctx`` support.
"""

from __future__ import annotations

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


def reset_listeners() -> None:
    """Remove all listeners (test isolation helper)."""
    _headers_listeners.clear()
    _request_listeners.clear()


_bridge_bus: Any = None


def install_provider_bridge(bus: Any) -> None:
    """Expose these hook points to the composition's event bus.

    Wires this registry to ``bus`` so ``before_provider_headers`` and
    ``before_provider_request`` handlers run with full context support right
    before an LLM request is sent. Idempotent: only the first bus in a process is
    bridged, matching the one active runtime.

    Called by the extensions row, so a plugin that registers
    ``api.on("before_provider_request", ...)`` reaches the transport.
    """
    global _bridge_bus
    if _bridge_bus is not None:
        return
    _bridge_bus = bus

    from vtx.plugins.event_contract import BEFORE_PROVIDER_HEADERS, BEFORE_PROVIDER_REQUEST

    async def on_headers(headers: dict[str, str | None], context: dict[str, Any]) -> None:
        await bus.emit(
            BEFORE_PROVIDER_HEADERS,
            provider=context.get("provider", ""),
            model=context.get("model", ""),
            headers=headers,
        )

    async def on_request(payload: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        merged = await bus.emit(
            BEFORE_PROVIDER_REQUEST,
            provider=context.get("provider", ""),
            model=context.get("model", ""),
            payload=payload,
        )
        # Handlers either mutate ``payload["payload"]`` in place — already
        # visible, since the same object is passed — or return
        # ``{"payload": {...}}``, which the bus writes back for chaining.
        result = merged.get("payload")
        return {"payload": result if isinstance(result, dict) else payload}

    register_headers_listener(on_headers)
    register_request_listener(on_request)


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
