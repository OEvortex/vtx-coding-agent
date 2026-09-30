"""OAuth discovery: finding out who to authenticate against.

Two documents matter. The *protected resource* metadata (RFC 9728) says which
authorization server guards a given MCP server and which scopes exist. The
*authorization server* metadata (RFC 8414) gives the endpoints.

Both are fetched from well-known URLs, and both are cached on the provider so a
reconnect does not pay for them again.

Adapted from modelcontextprotocol/typescript-sdk v1.29.0 (MIT, see LICENSES/).
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse

from ..types import LATEST_PROTOCOL_VERSION
from .errors import OAuthIssuerMismatchError
from .types import (
    AuthorizationServerMetadata,
    OAuthChallenge,
    OAuthProtectedResourceMetadata,
    OAuthServerInfo,
    parse_authorization_server_metadata,
    parse_protected_resource_metadata,
)

# Duck-typed to httpx.AsyncClient so callers can inject one, and so a test can
# pass a client pointed at a loopback server.
HttpClient = Any


def _client(fetch: HttpClient | None) -> HttpClient:
    if fetch is not None:
        return fetch
    import httpx

    return httpx.AsyncClient(timeout=30.0, follow_redirects=True)


def _is_discovery_miss(status: int) -> bool:
    """4xx and 502 mean "not here", so try the next candidate URL.

    502 is included deliberately: a gateway in front of a not-yet-provisioned
    authorization server answers 502 rather than 404, and treating that as fatal
    would break sign-in against a perfectly good server behind one.
    """
    return 400 <= status < 500 or status == 502


def _path_suffix(pathname: str) -> str:
    """Path for ``/.well-known/<kind><path>``; empty for the root path."""
    return pathname[:-1] if pathname.endswith("/") else pathname


def _field(header: str, name: str) -> str | None:
    match = re.search(
        rf'(?:^|[,\s]){re.escape(name)}=(?:"([^"]*)"|([^\s,]+))', header, re.IGNORECASE
    )
    if not match:
        return None
    return match.group(1) if match.group(1) is not None else match.group(2)


def parse_www_authenticate(header: str | None) -> OAuthChallenge:
    """Pull the useful fields out of a ``WWW-Authenticate`` header.

    Only ``bearer`` and ``dpop`` challenges carry OAuth information; anything
    else (basic, negotiate) is not ours to interpret.
    """
    if not header:
        return OAuthChallenge()
    scheme = header.strip().split(None, 1)[0].lower() if header.strip() else ""
    if scheme not in ("bearer", "dpop"):
        return OAuthChallenge()
    resource_metadata = _field(header, "resource_metadata")
    return OAuthChallenge(
        resource_metadata_url=resource_metadata,
        scope=_field(header, "scope"),
        error=_field(header, "error"),
        error_description=_field(header, "error_description"),
    )


async def _fetch_metadata(client: HttpClient, url: str, protocol_version: str) -> Any:
    return await client.get(
        url, headers={"Accept": "application/json", "MCP-Protocol-Version": protocol_version}
    )


async def discover_protected_resource_metadata(
    server_url: str,
    *,
    resource_metadata_url: str | None = None,
    protocol_version: str = LATEST_PROTOCOL_VERSION,
    fetch: HttpClient | None = None,
) -> OAuthProtectedResourceMetadata:
    """Read ``/.well-known/oauth-protected-resource`` for ``server_url``.

    A challenge may name the document directly; when it does not, the path form
    is tried first and then the origin form, because servers disagree on which
    one they publish.
    """
    client = _client(fetch)
    server = urlparse(server_url)
    if not server.scheme or not server.netloc:
        raise ValueError(f"Invalid MCP server URL: {server_url!r}")
    origin = urlunparse((server.scheme, server.netloc, "", "", "", ""))

    if resource_metadata_url:
        candidates = [resource_metadata_url]
    else:
        candidates = [
            urljoin(origin, f"/.well-known/oauth-protected-resource{_path_suffix(server.path)}")
        ]
        if server.path and server.path != "/":
            candidates.append(urljoin(origin, "/.well-known/oauth-protected-resource"))

    last_status = 0
    for candidate in candidates:
        response = await _fetch_metadata(client, candidate, protocol_version)
        last_status = response.status_code
        if not response.is_success:
            continue
        return parse_protected_resource_metadata(response.json())

    raise ValueError(f"HTTP {last_status} loading OAuth protected resource metadata")


def build_authorization_server_discovery_urls(authorization_server_url: str) -> list[str]:
    """Well-known URLs to try for an issuer, most specific first."""
    issuer = urlparse(authorization_server_url)
    if not issuer.scheme or not issuer.netloc:
        raise ValueError(f"Invalid authorization server URL: {authorization_server_url!r}")
    path = _path_suffix(issuer.path)
    origin = urlunparse((issuer.scheme, issuer.netloc, "", "", "", ""))
    urls = [
        urljoin(origin, f"/.well-known/oauth-authorization-server{path}"),
        urljoin(origin, f"/.well-known/openid-configuration{path}"),
    ]
    if path:
        # The OpenID variant some servers publish for a path-based issuer.
        urls.append(urljoin(origin, f"{path}/.well-known/openid-configuration"))
    return urls


def _trim_trailing_slash(value: str) -> str:
    return value[:-1] if value.endswith("/") else value


async def discover_authorization_server_metadata(
    authorization_server_url: str,
    *,
    protocol_version: str = LATEST_PROTOCOL_VERSION,
    fetch: HttpClient | None = None,
    skip_issuer_validation: bool = False,
) -> AuthorizationServerMetadata | None:
    """Read the authorization server's metadata, or ``None`` if it publishes none.

    A server with no metadata document is workable: the RFC 8414 default paths
    (``/authorize``, ``/token``, ``/register``) are tried instead. So this
    returning ``None`` is not a failure.
    """
    client = _client(fetch)
    for url in build_authorization_server_discovery_urls(authorization_server_url):
        response = await _fetch_metadata(client, url, protocol_version)
        if not response.is_success:
            if _is_discovery_miss(response.status_code):
                continue
            raise ValueError(
                f"HTTP {response.status_code} loading authorization server metadata from {url}"
            )
        metadata = parse_authorization_server_metadata(response.json())
        if not skip_issuer_validation:
            expected = _trim_trailing_slash(authorization_server_url)
            received = _trim_trailing_slash(metadata.issuer)
            if expected != received:
                raise OAuthIssuerMismatchError(expected, received)
        return metadata
    return None


async def discover_oauth_server_info(
    server_url: str,
    *,
    resource_metadata_url: str | None = None,
    fetch: HttpClient | None = None,
    skip_issuer_validation: bool = False,
) -> OAuthServerInfo:
    """Resolve a server URL to the authorization server that guards it."""
    client = _client(fetch)
    resource_metadata: OAuthProtectedResourceMetadata | None = None
    try:
        resource_metadata = await discover_protected_resource_metadata(
            server_url, resource_metadata_url=resource_metadata_url, fetch=client
        )
    except httpx_transport_errors():
        # No metadata and no server to ask: the request never left the machine,
        # so there is nothing to authenticate against.
        raise
    except ValueError:
        # The server simply publishes no protected-resource metadata, which is
        # allowed. Fall back to the server's own origin.
        resource_metadata = None

    authorization_server_url = (
        resource_metadata.authorization_servers[0]
        if resource_metadata and resource_metadata.authorization_servers
        else origin_of(server_url)
    )
    return OAuthServerInfo(
        authorization_server_url=authorization_server_url,
        authorization_server_metadata=await discover_authorization_server_metadata(
            authorization_server_url, fetch=client, skip_issuer_validation=skip_issuer_validation
        ),
        resource_metadata=resource_metadata,
    )


def httpx_transport_errors() -> tuple[type[BaseException], ...]:
    """Connection-level failures, which are fatal rather than "no metadata"."""
    import httpx

    return (httpx.TransportError, httpx.InvalidURL, OSError)


def origin_of(value: str) -> str:
    parsed = urlparse(value)
    return urlunparse((parsed.scheme, parsed.netloc, "/", "", "", ""))


def resource_url_from_server_url(value: str) -> str:
    parsed = urlparse(value)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, parsed.query, ""))


def select_resource(
    server_url: str, metadata: OAuthProtectedResourceMetadata | None
) -> str | None:
    """Check that a protected resource's identifier really covers our server.

    A resource identifier that does not cover the server we are about to send a
    token to would get a token minted for somewhere else, so a mismatch is an
    error rather than something to send anyway.
    """
    if metadata is None:
        return None
    requested = urlparse(resource_url_from_server_url(server_url))
    configured = urlparse(metadata.resource)
    if requested.scheme != configured.scheme or requested.netloc != configured.netloc:
        raise ValueError(
            f"Protected resource {metadata.resource} does not match MCP server {server_url}"
        )
    requested_path = requested.path if requested.path.endswith("/") else requested.path + "/"
    configured_path = configured.path if configured.path.endswith("/") else configured.path + "/"
    if not requested_path.startswith(configured_path):
        raise ValueError(
            f"Protected resource {metadata.resource} does not match MCP server {server_url}"
        )
    return metadata.resource


__all__ = [
    "HttpClient",
    "OAuthChallenge",
    "OAuthServerInfo",
    "build_authorization_server_discovery_urls",
    "discover_authorization_server_metadata",
    "discover_oauth_server_info",
    "discover_protected_resource_metadata",
    "origin_of",
    "parse_www_authenticate",
    "resource_url_from_server_url",
    "select_resource",
]
