"""Auth hook for HTTP transports.

Kept in its own module so a client that never authenticates does not import
anything OAuth-related.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class UnauthorizedContext:
    """What the transport hands an auth provider after a rejected request.

    ``response`` is the 401, or the 403 whose challenge reports
    ``insufficient_scope``. ``token`` is the access token the rejected request
    carried, if any -- a *different* current token means another request
    already refreshed it, so retrying rather than refreshing again is correct.
    """

    response: Any
    server_url: str
    token: str | None = None
    fetch: Any = None


class AuthProvider(Protocol):
    """Supplies bearer tokens, and may refresh them after a 401."""

    async def token(self) -> str | None: ...

    async def on_unauthorized(self, context: UnauthorizedContext) -> None: ...


__all__ = ["AuthProvider", "UnauthorizedContext"]
