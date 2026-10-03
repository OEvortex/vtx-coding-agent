"""Errors raised by the OAuth flow.

Split from :mod:`vtx.mcp.oauth.errors` by concern: the caller usually needs to
branch on *which* of these happened (re-authenticate, retry, give up) rather
than on the message, so each one gets its own type.
"""

from __future__ import annotations


class OAuthError(Exception):
    """An OAuth error response, or a failed request to one.

    ``code`` is the RFC 6749 error code (``invalid_grant``, ``server_error``, ...)
    when the server sent one, so a caller can decide whether re-authenticating
    could possibly help.
    """

    def __init__(self, code: str, message: str, error_uri: str | None = None) -> None:
        super().__init__(message or code)
        self.code = code
        self.error_uri = error_uri


class OAuthIssuerMismatchError(Exception):
    """The authorization server's issuer is not the one we asked.

    Treated as fatal rather than ignored: a metadata document served by a
    different issuer than the one requested is either a misconfiguration or
    something answering in its place, and sending a client secret to it would
    be the wrong outcome either way.
    """

    def __init__(self, expected: str, received: str) -> None:
        super().__init__(f"OAuth issuer mismatch: expected {expected!r}, received {received!r}")
        self.expected = expected
        self.received = received


class OAuthInsecureEndpointError(Exception):
    """Refusing to send credentials to a non-HTTPS endpoint."""

    def __init__(self, endpoint: str) -> None:
        super().__init__(f"Refusing to send OAuth credentials to non-HTTPS endpoint {endpoint}")
        self.endpoint = endpoint


class OAuthRegistrationError(Exception):
    """Dynamic client registration failed."""

    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"OAuth dynamic client registration failed with status {status}: {body}")
        self.status = status
        self.body = body


class McpOAuthAuthorizationRequiredError(Exception):
    """Sign-in needs a human: the flow reached a browser redirect.

    Distinct from a failed refresh, because retrying cannot fix it -- the only
    way forward is for the user to authorize in a browser.
    """


__all__ = [
    "McpOAuthAuthorizationRequiredError",
    "OAuthError",
    "OAuthInsecureEndpointError",
    "OAuthIssuerMismatchError",
    "OAuthRegistrationError",
]
