"""The OAuth 2.1 authorization-code flow with PKCE.

Three shapes of call:

* :func:`authorize_mcp` -- the whole flow, from "we have nothing" to either
  stored tokens or a browser redirect the caller has to send the user to.
* :func:`adapt_oauth_provider` -- plugs the flow into an HTTP transport so a 401
  refreshes and retries without the caller noticing.
* the individual steps, for a caller driving the browser itself.

The provider protocol is a Protocol rather than an ABC so a caller can satisfy it
with a plain object, and so the durable store stays the caller's choice.

Adapted from modelcontextprotocol/typescript-sdk v1.29.0 (MIT, see LICENSES/).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import secrets
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlencode, urljoin, urlparse

from ..auth import AuthProvider, UnauthorizedContext
from .discovery import (
    HttpClient,
    discover_authorization_server_metadata,
    discover_oauth_server_info,
    parse_www_authenticate,
    select_resource,
)
from .errors import (
    McpOAuthAuthorizationRequiredError,
    OAuthError,
    OAuthInsecureEndpointError,
    OAuthRegistrationError,
)
from .types import (
    AuthorizationServerMetadata,
    OAuthClientInformation,
    OAuthClientMetadata,
    OAuthDiscoveryState,
    OAuthTokens,
    parse_oauth_error,
    parse_oauth_tokens,
)

log = logging.getLogger("mcp.oauth")

LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

ClientAuthMethod = str  # "client_secret_basic" | "client_secret_post" | "none"


def _client(fetch: HttpClient | None) -> HttpClient:
    if fetch is not None:
        return fetch
    import httpx

    return httpx.AsyncClient(timeout=30.0, follow_redirects=True)


def is_loopback(hostname: str) -> bool:
    return hostname in LOOPBACK_HOSTS or hostname == "[::1]"


def secure_endpoint(value: str) -> str:
    """Refuse to send credentials anywhere but HTTPS (or a loopback address).

    RFC 8252 section 8.3 allows plain HTTP for loopback redirect URIs, which is
    the one case where the traffic never leaves the machine.
    """
    url = urlparse(value)
    if url.scheme != "https" and not is_loopback(url.hostname or ""):
        raise OAuthInsecureEndpointError(value)
    return value


def generate_pkce() -> tuple[str, str]:
    """A PKCE verifier and its S256 challenge.

    32 random bytes, base64url-encoded per RFC 7636. 256 bits of entropy is more
    than the spec's minimum 256-bit *verifier* length requires of the verifier's
    own randomness, and the challenge is derived, not drawn.
    """
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def select_client_auth_method(
    information: OAuthClientInformation, supported: list[str]
) -> ClientAuthMethod:
    """Pick how to authenticate at the token endpoint."""
    hinted = information.extra.get("token_endpoint_auth_method")
    if hinted in ("client_secret_basic", "client_secret_post", "none") and (
        not supported or hinted in supported
    ):
        return hinted
    if not supported:
        return "client_secret_basic" if information.client_secret else "none"
    if information.client_secret and "client_secret_basic" in supported:
        return "client_secret_basic"
    if information.client_secret and "client_secret_post" in supported:
        return "client_secret_post"
    if "none" in supported:
        return "none"
    return "client_secret_post" if information.client_secret else "none"


def apply_client_authentication(
    method: ClientAuthMethod,
    information: OAuthClientInformation,
    headers: dict[str, str],
    params: dict[str, str],
) -> None:
    if method == "client_secret_basic":
        if not information.client_secret:
            raise ValueError("client_secret_basic requires a client secret")
        raw = f"{information.client_id}:{information.client_secret}".encode()
        encoded = base64.b64encode(raw).decode("ascii")
        headers["Authorization"] = f"Basic {encoded}"
        return
    params["client_id"] = information.client_id
    if method == "client_secret_post" and information.client_secret:
        params["client_secret"] = information.client_secret


@dataclass
class TokenRequestOptions:
    client_information: OAuthClientInformation
    metadata: AuthorizationServerMetadata | None = None
    resource: str | None = None
    fetch: HttpClient | None = None


async def _token_request(
    authorization_server_url: str, options: TokenRequestOptions, params: dict[str, str]
) -> OAuthTokens:
    metadata = options.metadata
    endpoint = secure_endpoint(
        metadata.token_endpoint if metadata else urljoin(authorization_server_url, "/token")
    )
    headers = {"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"}
    if options.resource:
        params["resource"] = options.resource
    apply_client_authentication(
        select_client_auth_method(
            options.client_information,
            metadata.token_endpoint_auth_methods_supported if metadata else [],
        ),
        options.client_information,
        headers,
        params,
    )

    client = _client(options.fetch)
    response = await client.post(endpoint, headers=headers, content=urlencode(params).encode())
    text = response.text
    try:
        body = response.json()
    except ValueError:
        body = None
    # Servers report OAuth errors with any status, so the body decides.
    if isinstance(body, dict) and isinstance(body.get("error"), str):
        raise parse_oauth_error(response.status_code, body)
    if not response.is_success:
        raise OAuthError("server_error", f"HTTP {response.status_code}: {text}")
    return parse_oauth_tokens(body)


async def start_authorization(
    authorization_server_url: str,
    *,
    client_information: OAuthClientInformation,
    redirect_url: str,
    metadata: AuthorizationServerMetadata | None = None,
    scope: str | None = None,
    state: str | None = None,
    resource: str | None = None,
) -> tuple[str, str]:
    """Build the authorize URL and its PKCE verifier. Returns ``(url, verifier)``."""
    if metadata and "code" not in metadata.response_types_supported:
        raise ValueError("Authorization server does not support authorization codes")
    if (
        metadata
        and metadata.code_challenge_methods_supported
        and "S256" not in (metadata.code_challenge_methods_supported)
    ):
        raise ValueError("Authorization server does not support PKCE S256")

    endpoint = (
        metadata.authorization_endpoint
        if metadata
        else urljoin(authorization_server_url, "/authorize")
    )
    verifier, challenge = generate_pkce()

    query = {
        "response_type": "code",
        "client_id": client_information.client_id,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "redirect_uri": redirect_url,
    }
    if state:
        query["state"] = state
    if scope:
        query["scope"] = scope
    if scope and "offline_access" in scope.split():
        # Without an explicit prompt, some providers return a refresh token
        # silently only when consent was actually shown.
        query["prompt"] = "consent"
    if resource:
        query["resource"] = resource

    separator = "&" if urlparse(endpoint).query else "?"
    return f"{endpoint}{separator}{urlencode(query)}", verifier


async def register_client(
    authorization_server_url: str,
    *,
    client_metadata: OAuthClientMetadata,
    metadata: AuthorizationServerMetadata | None = None,
    scope: str | None = None,
    fetch: HttpClient | None = None,
) -> OAuthClientInformation:
    """Dynamic client registration (RFC 7591)."""
    endpoint = (
        metadata.registration_endpoint
        if metadata and metadata.registration_endpoint
        else urljoin(authorization_server_url, "/register")
    )
    body = dict(client_metadata.to_dict())
    if scope:
        body["scope"] = scope
    client = _client(fetch)
    response = await client.post(
        endpoint,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        json=body,
    )
    if not response.is_success:
        raise OAuthRegistrationError(response.status_code, response.text)
    return OAuthClientInformation.from_dict(response.json())


async def exchange_authorization_code(
    authorization_server_url: str,
    *,
    code: str,
    code_verifier: str,
    redirect_url: str,
    options: TokenRequestOptions,
) -> OAuthTokens:
    return await _token_request(
        authorization_server_url,
        options,
        {
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": code_verifier,
            "redirect_uri": redirect_url,
        },
    )


async def refresh_authorization(
    authorization_server_url: str, *, refresh_token: str, options: TokenRequestOptions
) -> OAuthTokens:
    tokens = await _token_request(
        authorization_server_url,
        options,
        {"grant_type": "refresh_token", "refresh_token": refresh_token},
    )
    # A refresh response often omits the refresh token, meaning "keep using the
    # one you have". Carrying it forward is what makes a rotating provider work.
    return OAuthTokens(
        access_token=tokens.access_token,
        token_type=tokens.token_type,
        expires_in=tokens.expires_in,
        scope=tokens.scope,
        refresh_token=tokens.refresh_token or refresh_token,
        id_token=tokens.id_token,
    )


# ---- the provider protocol -------------------------------------------------


class OAuthClientProvider(Protocol):
    """What the flow needs from whoever is storing the credentials."""

    redirect_url: str
    client_metadata: OAuthClientMetadata

    async def state(self) -> str: ...
    async def client_information(self) -> OAuthClientInformation | None: ...
    async def save_client_information(self, information: OAuthClientInformation) -> None: ...
    async def tokens(self) -> OAuthTokens | None: ...
    async def save_tokens(self, tokens: OAuthTokens) -> None: ...
    async def redirect_to_authorization(self, url: str) -> None: ...
    async def save_code_verifier(self, verifier: str) -> None: ...
    async def code_verifier(self) -> str: ...
    async def invalidate_credentials(self, kind: str) -> None: ...
    async def save_discovery_state(self, state: OAuthDiscoveryState) -> None: ...
    async def discovery_state(self) -> OAuthDiscoveryState | None: ...


@dataclass
class OAuthFlowOptions:
    server_url: str
    authorization_code: str | None = None
    scope: str | None = None
    resource_metadata_url: str | None = None
    fetch: HttpClient | None = None
    skip_issuer_validation: bool = False
    skip_refresh: bool = False
    """Go straight to the authorize redirect instead of refreshing.

    Needed for step-up authorization: the server wants a scope the current grant
    does not have, and a refresh would hand back the same grant.
    """


async def _run_flow(provider: OAuthClientProvider, options: OAuthFlowOptions) -> str:
    client = _client(options.fetch)

    cached = await provider.discovery_state()
    if cached and cached.authorization_server_url:
        discovered = OAuthDiscoveryState(
            authorization_server_url=cached.authorization_server_url,
            authorization_server_metadata=cached.authorization_server_metadata
            or await discover_authorization_server_metadata(
                cached.authorization_server_url,
                fetch=client,
                skip_issuer_validation=options.skip_issuer_validation,
            ),
            resource_metadata=cached.resource_metadata,
            resource_metadata_url=cached.resource_metadata_url,
        )
    else:
        info = await discover_oauth_server_info(
            options.server_url,
            resource_metadata_url=options.resource_metadata_url,
            fetch=client,
            skip_issuer_validation=options.skip_issuer_validation,
        )
        discovered = OAuthDiscoveryState(
            authorization_server_url=info.authorization_server_url,
            authorization_server_metadata=info.authorization_server_metadata,
            resource_metadata=info.resource_metadata,
            resource_metadata_url=options.resource_metadata_url,
        )
    await provider.save_discovery_state(discovered)

    metadata = discovered.authorization_server_metadata
    resource = select_resource(options.server_url, discovered.resource_metadata)
    advertised = (
        discovered.resource_metadata.scopes_supported if discovered.resource_metadata else []
    )
    scope = (
        options.scope
        or (" ".join(advertised) if advertised else None)
        or (provider.client_metadata.scope)
    )

    client_info = await provider.client_information()
    if client_info is None:
        if options.authorization_code:
            raise ValueError("OAuth client information is missing during code exchange")
        client_info = await register_client(
            discovered.authorization_server_url,
            metadata=metadata,
            client_metadata=provider.client_metadata,
            scope=scope,
            fetch=client,
        )
        await provider.save_client_information(client_info)

    token_options = TokenRequestOptions(
        metadata=metadata, client_information=client_info, resource=resource, fetch=client
    )

    if options.authorization_code:
        tokens = await exchange_authorization_code(
            discovered.authorization_server_url,
            code=options.authorization_code,
            code_verifier=await provider.code_verifier(),
            redirect_url=provider.redirect_url,
            options=token_options,
        )
        await provider.save_tokens(tokens)
        return "AUTHORIZED"

    if not options.skip_refresh:
        existing = await provider.tokens()
        if existing and existing.refresh_token:
            try:
                tokens = await refresh_authorization(
                    discovered.authorization_server_url,
                    refresh_token=existing.refresh_token,
                    options=token_options,
                )
                await provider.save_tokens(tokens)
                return "AUTHORIZED"
            except OAuthInsecureEndpointError:
                raise
            except OAuthError as exc:
                # ``server_error`` is the one refresh failure worth retrying
                # with a fresh authorization; the rest (invalid_grant,
                # invalid_client) will fail again identically.
                if exc.code != "server_error":
                    raise
                log.debug("OAuth refresh failed (%s); falling back to authorize", exc.code)

    state = await provider.state()
    url, verifier = await start_authorization(
        discovered.authorization_server_url,
        metadata=metadata,
        client_information=client_info,
        redirect_url=provider.redirect_url,
        scope=scope,
        state=state,
        resource=resource,
    )
    await provider.save_code_verifier(verifier)
    await provider.redirect_to_authorization(url)
    return "REDIRECT"


async def authorize_mcp(provider: OAuthClientProvider, options: OAuthFlowOptions) -> str:
    """Run the flow, retrying once with credentials dropped.

    The retry is what makes a stale registration recoverable: a provider that
    has forgotten our client, or a refresh token it has already rotated away,
    both surface as a token-endpoint error that only a fresh grant can clear.
    """
    try:
        return await _run_flow(provider, options)
    except OAuthError as exc:
        if exc.code in ("invalid_client", "unauthorized_client"):
            await provider.invalidate_credentials("all")
            return await _run_flow(provider, options)
        if exc.code == "invalid_grant":
            await provider.invalidate_credentials("tokens")
            return await _run_flow(provider, options)
        raise


def adapt_oauth_provider(provider: OAuthClientProvider) -> AuthProvider:
    """An :class:`AuthProvider` that keeps an HTTP transport authenticated.

    Two details are load-bearing:

    * Concurrent 401s share one refresh. Several in-flight requests failing at
      once would otherwise each start a flow.
    * A request whose token was already replaced is retried rather than
      refreshed again. With rotating refresh tokens, a second refresh using the
      old one fails *and* discards the new grant.
    """

    class _Adapter:
        """Concrete because :class:`AuthProvider` is a Protocol."""

        def __init__(self) -> None:
            self._in_flight: asyncio.Task | None = None

        async def token(self) -> str | None:
            tokens = await provider.tokens()
            return tokens.access_token if tokens else None

        async def on_unauthorized(self, context: UnauthorizedContext) -> None:
            challenge = parse_www_authenticate(
                context.response.headers.get("www-authenticate") if context.response else None
            )
            insufficient_scope = challenge.error == "insufficient_scope"
            if not insufficient_scope and self._in_flight is None and context.token is not None:
                current = await provider.tokens()
                if current and current.access_token != context.token:
                    # Another request already refreshed; retry with the new one.
                    return
            if self._in_flight is None:
                self._in_flight = asyncio.ensure_future(
                    authorize_mcp(
                        provider,
                        OAuthFlowOptions(
                            server_url=context.server_url,
                            resource_metadata_url=challenge.resource_metadata_url,
                            scope=challenge.scope,
                            fetch=context.fetch,
                            skip_refresh=insufficient_scope,
                        ),
                    )
                )
            task = self._in_flight
            try:
                if await task == "REDIRECT":
                    # Only a human in a browser can fix this, so say so rather
                    # than retrying a flow that cannot succeed unattended.
                    raise McpOAuthAuthorizationRequiredError()
            finally:
                if self._in_flight is task:
                    self._in_flight = None

    return _Adapter()


__all__ = [
    "LOOPBACK_HOSTS",
    "ClientAuthMethod",
    "OAuthClientProvider",
    "OAuthFlowOptions",
    "TokenRequestOptions",
    "adapt_oauth_provider",
    "apply_client_authentication",
    "authorize_mcp",
    "exchange_authorization_code",
    "generate_pkce",
    "is_loopback",
    "refresh_authorization",
    "register_client",
    "secure_endpoint",
    "select_client_auth_method",
    "start_authorization",
]
