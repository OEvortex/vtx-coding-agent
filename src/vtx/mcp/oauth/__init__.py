"""OAuth 2.1 sign-in for remote MCP servers.

Kept separate from :mod:`vtx.mcp` so a client that never authenticates does not
import any of it.

A caller drives the whole thing::

    from vtx.mcp.oauth import (
        McpOAuthProvider, OAuthCallbackServer, authorize_mcp,
    )

    callback = await OAuthCallbackServer.listen()
    provider = McpOAuthProvider(
        server_url="https://mcp.example.com/mcp",
        redirect_url=callback.redirect_url,
        client_metadata=OAuthClientMetadata(
            redirect_uris=[callback.redirect_url], client_name="vtx"
        ),
        on_redirect=open_browser,
    )

    state = await provider.state()
    waiting = asyncio.create_task(callback.wait_for_callback(state))
    if await authorize_mcp(provider, OAuthFlowOptions(server_url=...)) == "REDIRECT":
        result = await waiting

Or let a transport do it automatically with :func:`adapt_oauth_provider`.

Adapted from modelcontextprotocol/typescript-sdk v1.29.0 (MIT, see LICENSES/).
"""

from .callback import CallbackPage, OAuthCallback, OAuthCallbackServer
from .discovery import (
    HttpClient,
    build_authorization_server_discovery_urls,
    discover_authorization_server_metadata,
    discover_oauth_server_info,
    discover_protected_resource_metadata,
    origin_of,
    parse_www_authenticate,
    resource_url_from_server_url,
    select_resource,
)
from .errors import (
    McpOAuthAuthorizationRequiredError,
    OAuthError,
    OAuthInsecureEndpointError,
    OAuthIssuerMismatchError,
    OAuthRegistrationError,
)
from .flow import (
    OAuthClientProvider,
    OAuthFlowOptions,
    TokenRequestOptions,
    adapt_oauth_provider,
    authorize_mcp,
    exchange_authorization_code,
    generate_pkce,
    refresh_authorization,
    register_client,
    secure_endpoint,
    select_client_auth_method,
    start_authorization,
)
from .provider import (
    CREDENTIALS_FILENAME,
    FileOAuthStateStore,
    McpOAuthProvider,
    McpOAuthState,
    MemoryOAuthStateStore,
    OAuthStateStore,
)
from .types import (
    AuthorizationServerMetadata,
    OAuthChallenge,
    OAuthClientInformation,
    OAuthClientMetadata,
    OAuthDiscoveryState,
    OAuthProtectedResourceMetadata,
    OAuthServerInfo,
    OAuthTokens,
)

__all__ = [
    "CREDENTIALS_FILENAME",
    "AuthorizationServerMetadata",
    "CallbackPage",
    "FileOAuthStateStore",
    "HttpClient",
    "McpOAuthAuthorizationRequiredError",
    "McpOAuthProvider",
    "McpOAuthState",
    "MemoryOAuthStateStore",
    "OAuthCallback",
    "OAuthCallbackServer",
    "OAuthChallenge",
    "OAuthClientInformation",
    "OAuthClientMetadata",
    "OAuthClientProvider",
    "OAuthDiscoveryState",
    "OAuthError",
    "OAuthFlowOptions",
    "OAuthInsecureEndpointError",
    "OAuthIssuerMismatchError",
    "OAuthProtectedResourceMetadata",
    "OAuthRegistrationError",
    "OAuthServerInfo",
    "OAuthStateStore",
    "OAuthTokens",
    "TokenRequestOptions",
    "adapt_oauth_provider",
    "authorize_mcp",
    "build_authorization_server_discovery_urls",
    "discover_authorization_server_metadata",
    "discover_oauth_server_info",
    "discover_protected_resource_metadata",
    "exchange_authorization_code",
    "generate_pkce",
    "origin_of",
    "parse_www_authenticate",
    "refresh_authorization",
    "register_client",
    "resource_url_from_server_url",
    "secure_endpoint",
    "select_client_auth_method",
    "select_resource",
    "start_authorization",
]
